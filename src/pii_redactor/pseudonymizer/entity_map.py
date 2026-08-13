"""
Entity Map — consistent entity-to-fake-value mapping with fuzzy matching.

This is the core consistency mechanism: the same real PII value always maps
to the same fake value across an entire document run. Near-duplicate mentions
(e.g., "Kushal Hegde" vs "Kushal Subbayya Hegde") are fuzzy-matched to the
same canonical entity.

Security note: The entity_map.json file maps fake values back to real PII
and must NEVER be committed or shared. It is added to .gitignore.

Design tradeoff (fuzzy matching):
  Conservative threshold (token_sort_ratio >= 85) reduces false merges
  but may miss some near-duplicates. We only fuzzy-match within the same
  entity type (PERSON→PERSON, not PERSON→ORG) to limit risk.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# Fuzzy match threshold: 0-100. Higher = more conservative (fewer merges).
# 80 is chosen to catch "Kushal Hegde" vs "Kushal Subbayya Hegde" (token subset)
# while still rejecting genuinely different names.
DEFAULT_FUZZY_THRESHOLD = 80

# Entity types where fuzzy matching is applied
FUZZY_MATCH_TYPES = {"FULL_NAME", "COMPANY_NAME"}


class EntityMap:
    """Consistent mapping from real PII values to fake replacements.

    Maintains a dictionary keyed by (normalized_original, entity_type)
    that maps to an assigned fake value. Built incrementally during a
    single pipeline run and optionally persisted to JSON.

    Fuzzy matching: For FULL_NAME and COMPANY_NAME types, new values are
    checked against existing entries using token-sort similarity. If a
    close match is found above the threshold, the existing fake is reused
    rather than generating a new one.

    Attributes:
        _map: Internal mapping of (normalized_value, type) -> entry dict.
        _fuzzy_threshold: Minimum similarity score for fuzzy matching.
    """

    def __init__(self, fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD) -> None:
        # Key: (normalized_value, entity_type)
        # Value: {"fake_value": str, "original_variants": list[str], "occurrences": int}
        self._map: dict[tuple[str, str], dict[str, Any]] = {}
        self._fuzzy_threshold = fuzzy_threshold

    def get_or_create(
        self,
        original: str,
        entity_type: str,
        fake_generator: callable,
    ) -> str:
        """Get an existing fake value or create a new one for a PII string.

        Args:
            original: The real PII text as detected.
            entity_type: The PII type (e.g., "FULL_NAME", "EMAIL").
            fake_generator: A callable that generates a fake replacement.

        Returns:
            The assigned fake value (consistent across calls with the same
            or fuzzy-matching input).
        """
        normalized = self._normalize(original, entity_type)

        # Exact match first
        key = (normalized, entity_type)
        if key in self._map:
            self._map[key]["occurrences"] += 1
            if original not in self._map[key]["original_variants"]:
                self._map[key]["original_variants"].append(original)
            return self._map[key]["fake_value"]

        # Fuzzy match for name-like types
        if entity_type in FUZZY_MATCH_TYPES:
            fuzzy_match = self._find_fuzzy_match(normalized, entity_type)
            if fuzzy_match is not None:
                self._map[fuzzy_match]["occurrences"] += 1
                if original not in self._map[fuzzy_match]["original_variants"]:
                    self._map[fuzzy_match]["original_variants"].append(original)
                logger.debug(
                    "Fuzzy-matched '%s' to existing entry '%s' (type=%s)",
                    original,
                    fuzzy_match[0],
                    entity_type,
                )
                return self._map[fuzzy_match]["fake_value"]

        # Generate new fake value
        fake_value = fake_generator(original, entity_type)

        self._map[key] = {
            "fake_value": fake_value,
            "original_variants": [original],
            "occurrences": 1,
        }

        logger.debug(
            "New entity mapping: '%s' (%s) -> '%s'",
            original[:30],
            entity_type,
            fake_value[:30],
        )
        return fake_value

    def _normalize(self, text: str, entity_type: str) -> str:
        """Normalize a PII value for consistent matching.

        For names: lowercase, strip whitespace, remove honorifics.
        For other types: lowercase and strip whitespace.
        """
        normalized = text.strip().lower()

        if entity_type == "FULL_NAME":
            # Remove common honorifics for matching purposes
            honorifics = [
                "mr.", "mrs.", "ms.", "dr.", "prof.", "shri", "smt.",
                "mr ", "mrs ", "ms ", "dr ", "prof ", "shri ", "smt ",
            ]
            for h in honorifics:
                if normalized.startswith(h):
                    normalized = normalized[len(h):].strip()
                    break

            # Normalize whitespace
            normalized = " ".join(normalized.split())

        elif entity_type in ("EMAIL", "PHONE"):
            # For structured types, exact match (after lowering) is sufficient
            normalized = normalized.replace(" ", "").replace("-", "")

        return normalized

    def _find_fuzzy_match(
        self, normalized: str, entity_type: str
    ) -> tuple[str, str] | None:
        """Find a fuzzy match among existing entries of the same type.

        Uses rapidfuzz token_sort_ratio for order-independent comparison.
        Only matches within the same entity type to prevent cross-type merges.

        Returns:
            The key (normalized_value, entity_type) of the best match,
            or None if no match meets the threshold.
        """
        best_score = 0
        best_key: tuple[str, str] | None = None

        for (existing_norm, existing_type), _entry in self._map.items():
            if existing_type != entity_type:
                continue

            score = fuzz.token_set_ratio(normalized, existing_norm)
            if score > best_score and score >= self._fuzzy_threshold:
                best_score = score
                best_key = (existing_norm, existing_type)

        return best_key

    def save(self, output_path: str | Path) -> None:
        """Persist the entity map to a JSON file.

        SECURITY WARNING: This file maps fake values back to real PII.
        It must never be committed to version control or shared externally.

        Args:
            output_path: Path to write the JSON file.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert tuple keys to serializable format
        serializable = {}
        for (normalized, entity_type), entry in self._map.items():
            key_str = f"{entity_type}::{normalized}"
            serializable[key_str] = entry

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)

        logger.info(
            "Saved entity map (%d entries) to %s",
            len(self._map),
            output_path,
        )

    def load(self, input_path: str | Path) -> None:
        """Load an existing entity map from JSON.

        Useful for deterministic re-runs or incremental processing.

        Args:
            input_path: Path to the JSON file.
        """
        input_path = Path(input_path)
        if not input_path.exists():
            logger.warning("Entity map file not found: %s", input_path)
            return

        with open(input_path, "r", encoding="utf-8") as f:
            serializable = json.load(f)

        for key_str, entry in serializable.items():
            entity_type, normalized = key_str.split("::", 1)
            self._map[(normalized, entity_type)] = entry

        logger.info(
            "Loaded entity map (%d entries) from %s",
            len(self._map),
            input_path,
        )

    @property
    def stats(self) -> dict[str, int]:
        """Return counts per entity type in the map."""
        counts: dict[str, int] = {}
        for (_norm, entity_type), _entry in self._map.items():
            counts[entity_type] = counts.get(entity_type, 0) + 1
        return counts

    @property
    def total_entries(self) -> int:
        """Total number of unique entity entries."""
        return len(self._map)

    def __len__(self) -> int:
        return len(self._map)
