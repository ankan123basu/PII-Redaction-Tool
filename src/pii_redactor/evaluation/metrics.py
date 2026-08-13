"""
Precision / Recall / F1 metrics for PII detection evaluation.

Computes per-entity-type and overall (micro/macro average) metrics
by comparing predicted entity spans against a labeled ground truth set.

Matching strategy: span-level matching with configurable overlap threshold.
Default is exact match (start and end must match). Partial overlap matching
can be enabled for more lenient evaluation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EntitySpan:
    """A labeled or predicted entity span.

    Attributes:
        text_span: The text content of the entity.
        entity_type: The PII category (e.g., "EMAIL", "FULL_NAME").
        start: Character offset start.
        end: Character offset end (exclusive).
        segment_id: Optional segment/page identifier for location tracking.
    """

    text_span: str
    entity_type: str
    start: int
    end: int
    segment_id: str = ""

    def overlaps_with(self, other: EntitySpan, threshold: float = 0.5) -> bool:
        """Check if spans overlap above a threshold (intersection over union).

        Args:
            other: Another entity span to compare with.
            threshold: Minimum IoU for a match (0.0 = any overlap, 1.0 = exact).
        """
        if self.entity_type != other.entity_type:
            return False

        intersection_start = max(self.start, other.start)
        intersection_end = min(self.end, other.end)
        intersection = max(0, intersection_end - intersection_start)

        if intersection == 0:
            return False

        union = (self.end - self.start) + (other.end - other.start) - intersection
        if union == 0:
            return False

        return (intersection / union) >= threshold

    def text_matches(self, other: EntitySpan) -> bool:
        """Check if spans match by text content and type (ignoring offsets).

        This is useful when exact character offsets may differ between
        ground truth and predictions due to tokenization differences.
        """
        return (
            self.entity_type == other.entity_type
            and self.text_span.strip().lower() == other.text_span.strip().lower()
        )


@dataclass
class TypeMetrics:
    """Metrics for a single entity type.

    Attributes:
        entity_type: The PII type these metrics are for.
        true_positives: Count of correctly detected entities.
        false_positives: Count of incorrectly flagged non-entities.
        false_negatives: Count of missed true entities.
        fp_examples: Concrete false positive examples for analysis.
        fn_examples: Concrete false negative examples for analysis.
    """

    entity_type: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    fp_examples: list[str] = field(default_factory=list)
    fn_examples: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        """Precision = TP / (TP + FP)."""
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        """Recall = TP / (TP + FN)."""
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def f1(self) -> float:
        """F1 = 2 * (precision * recall) / (precision + recall)."""
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "fp_examples": self.fp_examples[:5],  # Limit examples
            "fn_examples": self.fn_examples[:5],
        }


@dataclass
class EvaluationResult:
    """Complete evaluation results across all entity types."""

    per_type: dict[str, TypeMetrics] = field(default_factory=dict)

    @property
    def micro_precision(self) -> float:
        """Micro-averaged precision (global TP/FP across all types)."""
        tp = sum(m.true_positives for m in self.per_type.values())
        fp = sum(m.false_positives for m in self.per_type.values())
        return tp / (tp + fp) if (tp + fp) > 0 else 0.0

    @property
    def micro_recall(self) -> float:
        """Micro-averaged recall (global TP/FN across all types)."""
        tp = sum(m.true_positives for m in self.per_type.values())
        fn = sum(m.false_negatives for m in self.per_type.values())
        return tp / (tp + fn) if (tp + fn) > 0 else 0.0

    @property
    def micro_f1(self) -> float:
        """Micro-averaged F1."""
        p, r = self.micro_precision, self.micro_recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    @property
    def macro_precision(self) -> float:
        """Macro-averaged precision (average across types)."""
        precisions = [m.precision for m in self.per_type.values() if m.true_positives + m.false_positives + m.false_negatives > 0]
        return sum(precisions) / len(precisions) if precisions else 0.0

    @property
    def macro_recall(self) -> float:
        """Macro-averaged recall (average across types)."""
        recalls = [m.recall for m in self.per_type.values() if m.true_positives + m.false_positives + m.false_negatives > 0]
        return sum(recalls) / len(recalls) if recalls else 0.0

    @property
    def macro_f1(self) -> float:
        """Macro-averaged F1."""
        p, r = self.macro_precision, self.macro_recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "per_type": {k: v.to_dict() for k, v in self.per_type.items()},
            "micro": {
                "precision": round(self.micro_precision, 4),
                "recall": round(self.micro_recall, 4),
                "f1": round(self.micro_f1, 4),
            },
            "macro": {
                "precision": round(self.macro_precision, 4),
                "recall": round(self.macro_recall, 4),
                "f1": round(self.macro_f1, 4),
            },
        }


def load_spans_from_jsonl(file_path: str | Path) -> list[EntitySpan]:
    """Load entity spans from a JSONL file.

    Expected format per line:
        {"text_span": "...", "type": "EMAIL", "start": 0, "end": 20}

    Args:
        file_path: Path to the JSONL file.

    Returns:
        List of EntitySpan objects.
    """
    spans = []
    file_path = Path(file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                spans.append(
                    EntitySpan(
                        text_span=data.get("text_span", ""),
                        entity_type=data.get("type", "UNKNOWN"),
                        start=data.get("start", 0),
                        end=data.get("end", 0),
                        segment_id=data.get("segment_id", ""),
                    )
                )
            except json.JSONDecodeError:
                logger.warning("Invalid JSON on line %d of %s", line_num, file_path)

    logger.info("Loaded %d spans from %s", len(spans), file_path)
    return spans


def compute_metrics(
    predictions: list[EntitySpan],
    ground_truth: list[EntitySpan],
    match_mode: str = "text",
) -> EvaluationResult:
    """Compute per-type and overall metrics.

    Args:
        predictions: List of predicted entity spans.
        ground_truth: List of ground-truth entity spans.
        match_mode: Matching strategy:
            - "text": Match by text content + type (ignoring offsets)
            - "span": Match by span overlap IoU
            - "exact": Match by exact start/end + type

    Returns:
        EvaluationResult with per-type and aggregate metrics.
    """
    result = EvaluationResult()

    # Collect all entity types
    all_types = set()
    for span in ground_truth + predictions:
        all_types.add(span.entity_type)

    for entity_type in all_types:
        result.per_type[entity_type] = TypeMetrics(entity_type=entity_type)

    # Track which predictions and ground truths have been matched
    gt_matched = [False] * len(ground_truth)
    pred_matched = [False] * len(predictions)

    # Match predictions against ground truth
    for pred_idx, pred in enumerate(predictions):
        matched = False
        for gt_idx, gt in enumerate(ground_truth):
            if gt_matched[gt_idx]:
                continue

            is_match = False
            if match_mode == "text":
                is_match = pred.text_matches(gt)
            elif match_mode == "span":
                is_match = pred.overlaps_with(gt, threshold=0.5)
            elif match_mode == "exact":
                is_match = (
                    pred.entity_type == gt.entity_type
                    and pred.start == gt.start
                    and pred.end == gt.end
                )

            if is_match:
                gt_matched[gt_idx] = True
                pred_matched[pred_idx] = True
                result.per_type[pred.entity_type].true_positives += 1
                matched = True
                break

        if not matched:
            # False positive: predicted but not in ground truth
            metrics = result.per_type.get(pred.entity_type)
            if metrics is None:
                metrics = TypeMetrics(entity_type=pred.entity_type)
                result.per_type[pred.entity_type] = metrics
            metrics.false_positives += 1
            metrics.fp_examples.append(
                f'"{pred.text_span}" (predicted as {pred.entity_type})'
            )

    # False negatives: ground truth not matched by any prediction
    for gt_idx, gt in enumerate(ground_truth):
        if not gt_matched[gt_idx]:
            metrics = result.per_type.get(gt.entity_type)
            if metrics is None:
                metrics = TypeMetrics(entity_type=gt.entity_type)
                result.per_type[gt.entity_type] = metrics
            metrics.false_negatives += 1
            metrics.fn_examples.append(
                f'"{gt.text_span}" (missed {gt.entity_type})'
            )

    return result
