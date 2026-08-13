"""
Evaluation report generator.

Produces EVALUATION_REPORT.md from predictions and ground truth,
containing:
- Per-type precision/recall/F1 table
- Overall micro/macro averages
- Confusion section (concrete FP/FN examples)
- Methodology description

This module auto-generates honest evaluation results. We explicitly
report sample size and limitations rather than fabricating numbers.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from pii_redactor.evaluation.metrics import (
    EvaluationResult,
    compute_metrics,
    load_spans_from_jsonl,
)

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates EVALUATION_REPORT.md from evaluation results."""

    def generate(
        self,
        predictions_path: str | Path,
        ground_truth_path: str | Path,
        output_path: str | Path = "EVALUATION_REPORT.md",
        match_mode: str = "text",
    ) -> EvaluationResult:
        """Generate the full evaluation report.

        Args:
            predictions_path: Path to predictions JSONL.
            ground_truth_path: Path to ground truth JSONL.
            output_path: Path for the output Markdown report.
            match_mode: Matching strategy ("text", "span", or "exact").

        Returns:
            The computed EvaluationResult for further processing.
        """
        predictions = load_spans_from_jsonl(predictions_path)
        ground_truth = load_spans_from_jsonl(ground_truth_path)

        result = compute_metrics(predictions, ground_truth, match_mode=match_mode)

        report_md = self._render_report(result, predictions, ground_truth, match_mode)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_md)

        logger.info("Evaluation report written to %s", output_path)
        return result

    def generate_from_result(
        self,
        result: EvaluationResult,
        output_path: str | Path,
        num_predictions: int = 0,
        num_ground_truth: int = 0,
        match_mode: str = "text",
    ) -> None:
        """Generate the report from a pre-computed EvaluationResult.

        Args:
            result: Pre-computed evaluation result.
            output_path: Path for the output Markdown report.
            num_predictions: Number of prediction spans.
            num_ground_truth: Number of ground truth spans.
            match_mode: Matching strategy used.
        """
        predictions_placeholder = [None] * num_predictions
        gt_placeholder = [None] * num_ground_truth
        report_md = self._render_report(result, predictions_placeholder, gt_placeholder, match_mode)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_md)

        logger.info("Evaluation report written to %s", output_path)

    def _render_report(
        self,
        result: EvaluationResult,
        predictions: list,
        ground_truth: list,
        match_mode: str,
    ) -> str:
        """Render the full evaluation report as Markdown."""
        lines = []

        # Title
        lines.append("# PII Redaction Tool — Evaluation Report")
        lines.append("")
        lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        lines.append("")

        # Executive Summary
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(
            f"Evaluated the PII redaction pipeline against a hand-labeled ground truth "
            f"of **{len(ground_truth)} spans** across representative document sections. "
            f"The pipeline produced **{len(predictions)} predicted spans**."
        )
        lines.append("")
        lines.append("| Metric | Micro-averaged | Macro-averaged |")
        lines.append("|--------|---------------|----------------|")
        lines.append(
            f"| **Precision** | {result.micro_precision:.1%} | {result.macro_precision:.1%} |"
        )
        lines.append(
            f"| **Recall** | {result.micro_recall:.1%} | {result.macro_recall:.1%} |"
        )
        lines.append(
            f"| **F1 Score** | {result.micro_f1:.1%} | {result.macro_f1:.1%} |"
        )
        lines.append("")

        # Per-type Results Table
        lines.append("## Per-Entity-Type Results")
        lines.append("")
        lines.append("| Entity Type | TP | FP | FN | Precision | Recall | F1 |")
        lines.append("|------------|---:|---:|---:|----------:|-------:|---:|")

        for entity_type in sorted(result.per_type.keys()):
            m = result.per_type[entity_type]
            lines.append(
                f"| {entity_type} | {m.true_positives} | {m.false_positives} | "
                f"{m.false_negatives} | {m.precision:.1%} | {m.recall:.1%} | {m.f1:.1%} |"
            )

        lines.append("")

        # Confusion Analysis
        lines.append("## Confusion Analysis")
        lines.append("")

        # False Positives
        lines.append("### False Positives (Redacted but should not have been)")
        lines.append("")
        has_fp = False
        for entity_type, m in sorted(result.per_type.items()):
            if m.fp_examples:
                has_fp = True
                lines.append(f"**{entity_type}** ({m.false_positives} false positives):")
                for example in m.fp_examples[:3]:
                    lines.append(f"- {example}")
                lines.append("")

        if not has_fp:
            lines.append("No false positives detected in the evaluated sample.")
            lines.append("")

        # False Negatives
        lines.append("### False Negatives (Missed PII)")
        lines.append("")
        has_fn = False
        for entity_type, m in sorted(result.per_type.items()):
            if m.fn_examples:
                has_fn = True
                lines.append(f"**{entity_type}** ({m.false_negatives} missed):")
                for example in m.fn_examples[:3]:
                    lines.append(f"- {example}")
                lines.append("")

        if not has_fn:
            lines.append("No false negatives detected in the evaluated sample.")
            lines.append("")

        # Design Tradeoffs
        lines.append("## Design Tradeoffs Affecting Results")
        lines.append("")
        lines.append(
            "1. **DOB Context-Window Guard**: Dates are only classified as DOB if a "
            "keyword ('DOB', 'date of birth', 'born') appears within 80 characters. "
            "This significantly improves precision (avoids redacting incorporation dates, "
            "filing dates, etc.) but may miss DOBs that appear without nearby context "
            "keywords (recall cost)."
        )
        lines.append("")
        lines.append(
            "2. **Credit Card Luhn Validation**: Regex matches that fail the Luhn "
            "checksum are rejected. This is the single biggest precision win — "
            "16-digit numbers are common in financial documents (account numbers, "
            "reference numbers) and would be false positives without Luhn."
        )
        lines.append("")
        lines.append(
            "3. **ORG Stoplist**: Generic references like 'the Company', 'our Company', "
            "'the Board' are excluded from COMPANY_NAME detections. This prevents "
            "dozens of false positives per page in legal prose."
        )
        lines.append("")
        lines.append(
            "4. **Entity Fuzzy Matching**: Near-duplicate names (e.g., 'Kushal Hegde' "
            "vs 'Kushal Subbayya Hegde') are merged to the same fake value using "
            "token_sort_ratio ≥ 85. Risk: false merges of genuinely different people "
            "with similar names. Mitigated by only matching within the same entity type."
        )
        lines.append("")
        lines.append(
            "5. **CIN as PII**: We include CIN (Corporate Identity Number) detection as "
            "enabled by default, treating it as company-identifying data. CINs are "
            "publicly registered with the MCA and not technically secret, but they "
            "uniquely identify a company. This is a deliberate policy choice — "
            "users can disable it in `config/entity_rules.yaml`."
        )
        lines.append("")

        # Methodology
        lines.append("## Methodology")
        lines.append("")
        lines.append(
            "### Ground Truth Construction"
        )
        lines.append("")
        lines.append(
            "Ground truth spans were hand-labeled from representative sections of the "
            "test document, covering:"
        )
        lines.append("- Running prose paragraphs (legal boilerplate with embedded names/addresses)")
        lines.append("- Table cells (promoter details, director information)")
        lines.append("- Headers and footers (company name, document title)")
        lines.append("- Cover page (company name, registration details, CIN)")
        lines.append("")
        lines.append(
            f"**Sample size**: {len(ground_truth)} labeled spans across the evaluated sections. "
            "This is a representative but not exhaustive sample. Metrics reported here "
            "reflect performance on this subset — actual performance on the full 400+ page "
            "document may vary, particularly for entity types with low representation in "
            "the sample."
        )
        lines.append("")
        lines.append("### Matching Strategy")
        lines.append("")
        lines.append(
            f"Matching mode: **{match_mode}**. Predictions are matched to ground truth "
            f"entries by {'text content and entity type (case-insensitive)' if match_mode == 'text' else 'span overlap (IoU ≥ 0.5)' if match_mode == 'span' else 'exact start/end offsets and entity type'}."
        )
        lines.append("")
        lines.append("### Limitations")
        lines.append("")
        lines.append(
            "- Evaluation is on a subset, not the full document. Edge cases in "
            "unsampled sections may not be reflected."
        )
        lines.append(
            "- NER performance varies by context — names in tables vs. running prose "
            "may have different detection rates."
        )
        lines.append(
            "- The ground truth may itself contain labeling errors, particularly for "
            "ambiguous cases (is a city name PII? Is a company abbreviation an ORG?)."
        )
        lines.append("")

        return "\n".join(lines)
