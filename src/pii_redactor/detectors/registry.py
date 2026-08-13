"""
Detector registry — loads config/entity_rules.yaml and wires up detectors.

This module is the glue between the YAML config and the actual detector
instances. It reads the entity_rules.yaml file, determines which entity
types are enabled, and creates the appropriate detector instances
(RegexDetector and/or NERDetector) configured for those types.

The registry provides a single detect_all(text) method that runs all
enabled detectors and returns aggregated results.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from pii_redactor.detectors.base import DetectedEntity, Detector
from pii_redactor.detectors.ner_detector import NERDetector
from pii_redactor.detectors.regex_detectors import RegexDetector

logger = logging.getLogger(__name__)

# Default config path (relative to project root)
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "entity_rules.yaml"


class DetectorRegistry:
    """Registry that loads config and manages detector instances.

    Usage:
        registry = DetectorRegistry("config/entity_rules.yaml")
        entities = registry.detect_all("Some text with PII...")
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Initialize the registry from a YAML config file.

        Args:
            config_path: Path to entity_rules.yaml.
                        Uses default path if None.
        """
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config: dict[str, Any] = {}
        self.entity_rules: list[dict[str, Any]] = []
        self._detectors: list[Detector] = []

        self._load_config()
        self._build_detectors()

    def _load_config(self) -> None:
        """Load and parse the YAML config file."""
        if not self.config_path.exists():
            logger.warning(
                "Config file not found at %s, using defaults", self.config_path
            )
            self.entity_rules = []
            return

        with open(self.config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f) or {}

        self.entity_rules = self.config.get("entity_types", [])
        enabled_count = sum(1 for r in self.entity_rules if r.get("enabled", True))
        logger.info(
            "Loaded %d entity rules (%d enabled) from %s",
            len(self.entity_rules),
            enabled_count,
            self.config_path.name,
        )

    def _build_detectors(self) -> None:
        """Instantiate detectors based on enabled entity rules."""
        regex_types: set[str] = set()
        ner_types: set[str] = set()
        self._confidence_thresholds: dict[str, float] = {}

        for rule in self.entity_rules:
            if not rule.get("enabled", True):
                continue

            name = rule["name"]
            method = rule.get("method", "regex")
            threshold = rule.get("confidence_threshold", 0.70)
            self._confidence_thresholds[name] = threshold

            if method == "regex":
                regex_types.add(name)
            elif method == "ner":
                ner_types.add(name)
            else:
                logger.warning("Unknown method '%s' for entity type '%s'", method, name)

        # Create detector instances
        if regex_types:
            self._detectors.append(RegexDetector(enabled_types=regex_types))
            logger.info("Initialized RegexDetector for types: %s", sorted(regex_types))

        if ner_types:
            min_threshold = min(
                self._confidence_thresholds.get(t, 0.70) for t in ner_types
            )
            self._detectors.append(
                NERDetector(enabled_types=ner_types, confidence_threshold=min_threshold)
            )
            logger.info("Initialized NERDetector for types: %s", sorted(ner_types))

    @property
    def detectors(self) -> list[Detector]:
        """List of active detector instances."""
        return self._detectors

    @property
    def enabled_entity_types(self) -> list[str]:
        """List of all enabled entity type names."""
        return [
            rule["name"]
            for rule in self.entity_rules
            if rule.get("enabled", True)
        ]

    def get_confidence_threshold(self, entity_type: str) -> float:
        """Get the configured confidence threshold for an entity type."""
        return self._confidence_thresholds.get(entity_type, 0.70)

    def get_rule(self, entity_type: str) -> dict[str, Any] | None:
        """Get the full config rule for an entity type."""
        for rule in self.entity_rules:
            if rule["name"] == entity_type:
                return rule
        return None

    def detect_all(self, text: str) -> list[DetectedEntity]:
        """Run all enabled detectors on the input text.

        Filters results by confidence threshold per entity type.

        Args:
            text: Input text to scan for PII.

        Returns:
            Aggregated list of detected entities from all detectors.
            May contain overlaps (resolved downstream by the pipeline).
        """
        all_entities: list[DetectedEntity] = []

        for detector in self._detectors:
            try:
                entities = detector.detect(text)
                # Filter by confidence threshold
                for entity in entities:
                    threshold = self._confidence_thresholds.get(
                        entity.entity_type, 0.70
                    )
                    if entity.confidence >= threshold:
                        all_entities.append(entity)
                    else:
                        logger.debug(
                            "Filtered %s '%s' (confidence %.2f < threshold %.2f)",
                            entity.entity_type,
                            entity.text[:20],
                            entity.confidence,
                            threshold,
                        )
            except Exception:
                logger.exception("Error running detector %s", detector.name)

        logger.debug("detect_all found %d entities in text of length %d", len(all_entities), len(text))
        return all_entities
