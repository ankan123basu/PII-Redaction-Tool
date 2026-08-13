"""
Pipeline orchestrator — the central controller for PII redaction.

Orchestrates the full flow:
    Read DOCX -> Extract segments -> For each segment:
        -> Run all detectors -> Resolve overlaps -> Pseudonymize (via EntityMap)
    -> Apply replacements to DOCX -> Write output
    -> Save entity_map.json -> Generate run report

Includes the critical overlap resolution algorithm that determines
which detection wins when regex and NER spans overlap.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pii_redactor.detectors.base import DetectedEntity
from pii_redactor.detectors.registry import DetectorRegistry
from pii_redactor.document_io.docx_reader import DocxReader, TextSegment
from pii_redactor.document_io.docx_writer import DocxWriter, Replacement
from pii_redactor.pseudonymizer.entity_map import EntityMap
from pii_redactor.pseudonymizer.faker_provider import FakerProvider

logger = logging.getLogger(__name__)


@dataclass
class RedactionStats:
    """Statistics from a redaction run.

    Tracks counts per entity type for reporting.
    """

    entities_found: dict[str, int] = field(default_factory=dict)
    entities_redacted: dict[str, int] = field(default_factory=dict)
    segments_processed: int = 0
    total_replacements: int = 0
    duration_seconds: float = 0.0
    low_confidence_entities: list[dict[str, Any]] = field(default_factory=list)

    def record_found(self, entity_type: str) -> None:
        self.entities_found[entity_type] = self.entities_found.get(entity_type, 0) + 1

    def record_redacted(self, entity_type: str) -> None:
        self.entities_redacted[entity_type] = self.entities_redacted.get(entity_type, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities_found": self.entities_found,
            "entities_redacted": self.entities_redacted,
            "segments_processed": self.segments_processed,
            "total_replacements": self.total_replacements,
            "duration_seconds": round(self.duration_seconds, 2),
            "unique_entities": sum(self.entities_found.values()),
        }


def resolve_overlaps(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    """Resolve overlapping entity spans with a deterministic priority scheme.

    Priority order (highest wins):
    1. Validated regex detections (e.g., Luhn-checked credit cards) — highest specificity
    2. Higher confidence score
    3. Longer span (more specific)
    4. Earlier start position (deterministic tiebreaker)

    Algorithm:
    - Sort entities by priority (validated > confidence > length > position)
    - Greedily select non-overlapping spans
    - Skip any entity that overlaps with an already-selected one

    Args:
        entities: List of detected entities, potentially with overlaps.

    Returns:
        Non-overlapping list of entities after resolution.
    """
    if not entities:
        return []

    # Sort by priority: validated first, then confidence desc, length desc, start asc
    def sort_key(e: DetectedEntity) -> tuple:
        return (
            -int(e.validated),       # Validated wins (True=-1 sorts first)
            -e.confidence,           # Higher confidence wins
            -(e.end - e.start),      # Longer span wins
            e.start,                 # Earlier position wins (tiebreaker)
        )

    sorted_entities = sorted(entities, key=sort_key)

    resolved: list[DetectedEntity] = []
    for entity in sorted_entities:
        # Check if this entity overlaps with any already-selected entity
        has_overlap = False
        for selected in resolved:
            if entity.overlaps_with(selected):
                has_overlap = True
                logger.debug(
                    "Overlap resolved: keeping '%s' (%s, conf=%.2f, validated=%s) "
                    "over '%s' (%s, conf=%.2f, validated=%s)",
                    selected.text[:20],
                    selected.entity_type,
                    selected.confidence,
                    selected.validated,
                    entity.text[:20],
                    entity.entity_type,
                    entity.confidence,
                    entity.validated,
                )
                break

        if not has_overlap:
            resolved.append(entity)

    # Sort by position for ordered replacement
    resolved.sort(key=lambda e: e.start)

    if len(entities) != len(resolved):
        logger.info(
            "Overlap resolution: %d entities -> %d (removed %d overlaps)",
            len(entities),
            len(resolved),
            len(entities) - len(resolved),
        )

    return resolved


class RedactionPipeline:
    """Orchestrates the full PII redaction workflow.

    Usage:
        pipeline = RedactionPipeline(
            config_path="config/entity_rules.yaml",
            seed=42,
        )
        stats = pipeline.run(
            input_path="data/input/document.docx",
            output_path="data/output/redacted.docx",
            entity_map_path="data/output/entity_map.json",
        )
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        seed: int = 42,
        low_confidence_threshold: float = 0.75,
    ) -> None:
        """Initialize the pipeline components.

        Args:
            config_path: Path to entity_rules.yaml config.
            seed: Random seed for reproducible pseudonymization.
            low_confidence_threshold: Entities below this confidence
                are flagged for human review (stretch goal #1).
        """
        self.registry = DetectorRegistry(config_path)
        self.faker_provider = FakerProvider(seed=seed)
        self.entity_map = EntityMap()
        self.stats = RedactionStats()
        self._low_confidence_threshold = low_confidence_threshold

    def run(
        self,
        input_path: str | Path,
        output_path: str | Path,
        entity_map_path: str | Path | None = None,
        report_path: str | Path | None = None,
    ) -> RedactionStats:
        """Execute the full redaction pipeline.

        Args:
            input_path: Path to the input DOCX file.
            output_path: Path for the redacted output DOCX.
            entity_map_path: Optional path to save the entity map JSON.
            report_path: Optional path to save the run report JSON.

        Returns:
            RedactionStats with counts and timing information.
        """
        start_time = time.time()

        input_path = Path(input_path)
        output_path = Path(output_path)

        logger.info("Starting redaction pipeline: %s -> %s", input_path, output_path)

        # --- Step 1: Initialize writer (loads the document we'll modify) ---
        writer = DocxWriter(input_path)

        # --- Step 2: Extract segments from the SAME document the writer holds ---
        # Critical: We must read segments from the writer's document instance,
        # not from a separate DocxReader, because element_ref must point to
        # the same paragraph objects we'll modify in the writer.
        reader = DocxReader.__new__(DocxReader)
        reader.file_path = input_path
        reader.document = writer.document  # Share the same Document object!
        segments = reader.extract_segments()
        logger.info("Extracted %d text segments", len(segments))

        # --- Step 3: Process each segment ---
        for seg_idx, segment in enumerate(segments):
            self._process_segment(segment, writer, seg_idx, len(segments))

        # --- Step 4: Save output ---
        output_path.parent.mkdir(parents=True, exist_ok=True)
        writer.save(output_path)

        # --- Step 5: Save entity map ---
        if entity_map_path:
            self.entity_map.save(entity_map_path)

        # --- Step 6: Record timing ---
        self.stats.duration_seconds = time.time() - start_time

        # --- Step 7: Save run report ---
        if report_path:
            self._save_report(report_path)

        # --- Step 8: Save low-confidence review file ---
        if self.stats.low_confidence_entities:
            self._save_low_confidence_review(output_path.parent / "low_confidence_review.csv")

        logger.info(
            "Pipeline complete in %.1fs: %d segments, %d replacements",
            self.stats.duration_seconds,
            self.stats.segments_processed,
            self.stats.total_replacements,
        )

        return self.stats

    def _process_segment(
        self,
        segment: TextSegment,
        writer: DocxWriter,
        seg_idx: int,
        total_segments: int,
    ) -> None:
        """Process a single text segment through detection and replacement.

        Args:
            segment: The text segment to process.
            writer: The DOCX writer to apply replacements to.
            seg_idx: Current segment index (for progress logging).
            total_segments: Total number of segments (for progress logging).
        """
        if not segment.text or not segment.text.strip():
            return

        self.stats.segments_processed += 1

        # Progress logging every 100 segments
        if seg_idx % 100 == 0:
            logger.info("Processing segment %d/%d...", seg_idx + 1, total_segments)

        # --- Detect ---
        raw_entities = self.registry.detect_all(segment.text)

        for entity in raw_entities:
            self.stats.record_found(entity.entity_type)

        if not raw_entities:
            return

        # --- Resolve overlaps ---
        resolved_entities = resolve_overlaps(raw_entities)

        # --- Pseudonymize and build replacements ---
        replacements: list[Replacement] = []
        for entity in resolved_entities:
            # Check for low confidence (stretch goal: review flags)
            if entity.confidence < self._low_confidence_threshold:
                self.stats.low_confidence_entities.append({
                    "text": entity.text,
                    "entity_type": entity.entity_type,
                    "confidence": entity.confidence,
                    "detector": entity.detector_name,
                    "segment_location": segment.location_id,
                })

            # Get or create consistent fake value
            fake_value = self.entity_map.get_or_create(
                original=entity.text,
                entity_type=entity.entity_type,
                fake_generator=self.faker_provider.generate,
            )

            replacements.append(
                Replacement(
                    start=entity.start,
                    end=entity.end,
                    original_text=entity.text,
                    replacement_text=fake_value,
                )
            )

            self.stats.record_redacted(entity.entity_type)

        # --- Apply replacements ---
        if replacements:
            applied = writer.apply_replacements(segment, replacements)
            self.stats.total_replacements += applied

    def _save_report(self, report_path: str | Path) -> None:
        """Save a structured JSON run report.

        This report contains counts, entity types, and run duration,
        suitable for feeding into a dashboard (stretch goal #4).
        """
        report_path = Path(report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        report = {
            "pipeline_version": "1.0.0",
            "statistics": self.stats.to_dict(),
            "entity_map_stats": self.entity_map.stats,
            "entity_map_total": self.entity_map.total_entries,
            "detectors_used": [d.name for d in self.registry.detectors],
            "enabled_entity_types": self.registry.enabled_entity_types,
            "low_confidence_count": len(self.stats.low_confidence_entities),
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        logger.info("Saved run report to %s", report_path)

    def _save_low_confidence_review(self, output_path: str | Path) -> None:
        """Save low-confidence detections to CSV for human review.

        Stretch goal #1: Confidence scoring + review flags.
        Shows awareness that no PII tool is 100% automatable in production.
        """
        import csv

        output_path = Path(output_path)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer_csv = csv.DictWriter(
                f,
                fieldnames=["text", "entity_type", "confidence", "detector", "segment_location"],
            )
            writer_csv.writeheader()
            writer_csv.writerows(self.stats.low_confidence_entities)

        logger.info(
            "Saved %d low-confidence entities for review to %s",
            len(self.stats.low_confidence_entities),
            output_path,
        )
