"""
Structure-preserving redacted DOCX writer.

This module takes the original DOCX document and a list of replacements
(original text → fake text, per TextSegment), and writes a new DOCX with
the replacements applied at the RUN level to preserve all formatting.

CRITICAL DESIGN: We never recreate the document. We open the original,
modify runs in-place, and save to a new path. This preserves:
- Font styles (bold, italic, underline, color, size)
- Paragraph styles and alignment
- Table structure and cell formatting
- Images, shapes, and other non-text elements
- Headers, footers, and section breaks

The replacement algorithm handles PII spans that cross run boundaries
by merging affected runs and redistributing text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph

from pii_redactor.document_io.docx_reader import RunInfo, TextSegment

logger = logging.getLogger(__name__)


@dataclass
class Replacement:
    """A single text replacement to apply.

    Attributes:
        start: Character offset in the segment's full text where replacement begins.
        end: Character offset where replacement ends (exclusive).
        original_text: The original PII text being replaced.
        replacement_text: The fake text to insert.
    """

    start: int
    end: int
    original_text: str
    replacement_text: str


class DocxWriter:
    """Structure-preserving DOCX writer that applies redactions in place.

    Opens the original DOCX, applies replacements at the run level,
    and saves to a new file path.

    Usage:
        writer = DocxWriter("path/to/original.docx")
        writer.apply_replacements(segment, [replacement1, replacement2])
        writer.save("path/to/redacted.docx")
    """

    def __init__(self, source_path: str | Path) -> None:
        """Initialize by loading the original document.

        Args:
            source_path: Path to the original (unredacted) DOCX file.
        """
        self.source_path = Path(source_path)
        if not self.source_path.exists():
            raise FileNotFoundError(f"Source DOCX not found: {self.source_path}")
        self.document = Document(str(self.source_path))
        self._replacement_count = 0
        logger.info("Loaded source DOCX for writing: %s", self.source_path.name)

    def apply_replacements(
        self,
        segment: TextSegment,
        replacements: list[Replacement],
    ) -> int:
        """Apply a list of replacements to a text segment's runs.

        Replacements are applied in reverse order (right-to-left) to
        maintain correct offsets as text lengths change.

        Args:
            segment: The TextSegment containing the paragraph reference and run info.
            replacements: List of Replacement objects to apply.

        Returns:
            Number of replacements successfully applied.
        """
        if not replacements or not segment.element_ref:
            return 0

        paragraph = segment.element_ref
        runs = paragraph.runs

        if not runs:
            # Paragraph has no runs — try direct text replacement as fallback
            return self._fallback_replace(paragraph, replacements)

        # Sort replacements by start offset, DESCENDING (right-to-left)
        sorted_replacements = sorted(replacements, key=lambda r: r.start, reverse=True)

        applied = 0
        for replacement in sorted_replacements:
            success = self._apply_single_replacement(
                runs, segment.runs, replacement
            )
            if success:
                applied += 1
                self._replacement_count += 1

        return applied

    def _apply_single_replacement(
        self,
        runs: list,
        run_infos: list[RunInfo],
        replacement: Replacement,
    ) -> bool:
        """Apply a single replacement across one or more runs.

        Handles three cases:
        1. Replacement falls entirely within one run → simple in-run replacement
        2. Replacement spans multiple runs → merge, replace, redistribute
        3. No matching run found → log warning and skip
        """
        if not run_infos:
            return False

        # Find which runs are affected by this replacement span
        affected_runs = []
        for ri in run_infos:
            if ri.start_offset < replacement.end and ri.end_offset > replacement.start:
                affected_runs.append(ri)

        if not affected_runs:
            logger.warning(
                "No runs found for replacement at [%d:%d] '%s'",
                replacement.start,
                replacement.end,
                replacement.original_text[:30],
            )
            return False

        if len(affected_runs) == 1:
            return self._replace_within_single_run(
                runs, affected_runs[0], replacement
            )
        else:
            return self._replace_across_runs(
                runs, affected_runs, replacement
            )

    def _replace_within_single_run(
        self,
        runs: list,
        run_info: RunInfo,
        replacement: Replacement,
    ) -> bool:
        """Replace text within a single run (most common case).

        The replacement span is entirely contained within one run,
        so we can do a simple string replacement preserving all formatting.
        """
        try:
            run = runs[run_info.run_index]
        except IndexError:
            logger.warning("Run index %d out of range", run_info.run_index)
            return False

        current_text = run.text or ""

        # Calculate offsets relative to this run
        rel_start = replacement.start - run_info.start_offset
        rel_end = replacement.end - run_info.start_offset

        # Clamp to run boundaries
        rel_start = max(0, rel_start)
        rel_end = min(len(current_text), rel_end)

        # Build new text
        new_text = current_text[:rel_start] + replacement.replacement_text + current_text[rel_end:]
        run.text = new_text

        return True

    def _replace_across_runs(
        self,
        runs: list,
        affected_runs: list[RunInfo],
        replacement: Replacement,
    ) -> bool:
        """Replace text that spans multiple runs.

        Strategy:
        1. Put the full replacement text into the first affected run
           (preserving the first run's formatting).
        2. Clear the text of subsequent affected runs (keep the run
           elements for formatting consistency in the XML).
        """
        first_ri = affected_runs[0]
        last_ri = affected_runs[-1]

        try:
            first_run = runs[first_ri.run_index]
        except IndexError:
            return False

        # Text before the replacement in the first run
        first_run_text = first_run.text or ""
        rel_start = replacement.start - first_ri.start_offset
        rel_start = max(0, rel_start)
        prefix = first_run_text[:rel_start]

        # Text after the replacement in the last run
        try:
            last_run = runs[last_ri.run_index]
            last_run_text = last_run.text or ""
            rel_end = replacement.end - last_ri.start_offset
            rel_end = min(len(last_run_text), rel_end)
            suffix = last_run_text[rel_end:]
        except IndexError:
            suffix = ""

        # Set first run to prefix + replacement + suffix
        first_run.text = prefix + replacement.replacement_text + suffix

        # Clear middle and last runs
        for ri in affected_runs[1:]:
            import contextlib
            with contextlib.suppress(IndexError):
                runs[ri.run_index].text = ""

        return True

    def _fallback_replace(
        self, paragraph: Paragraph, replacements: list[Replacement]
    ) -> int:
        """Fallback: direct string replacement on paragraph.text.

        Used when a paragraph has no runs (rare, but possible).
        WARNING: This loses run-level formatting.
        """
        text = paragraph.text or ""
        applied = 0

        # Sort replacements right-to-left
        for rep in sorted(replacements, key=lambda r: r.start, reverse=True):
            text = text[:rep.start] + rep.replacement_text + text[rep.end:]
            applied += 1

        if applied:
            paragraph.text = text
            self._replacement_count += applied
            logger.warning(
                "Used fallback (formatting-lossy) replacement for paragraph with no runs"
            )

        return applied

    def save(self, output_path: str | Path) -> None:
        """Save the modified document to a new file.

        Args:
            output_path: Path for the redacted output DOCX.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.document.save(str(output_path))
        logger.info(
            "Saved redacted DOCX (%d replacements) to %s",
            self._replacement_count,
            output_path,
        )

    @property
    def replacement_count(self) -> int:
        """Total number of replacements applied so far."""
        return self._replacement_count
