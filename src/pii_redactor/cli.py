"""
CLI entry point for the PII Redaction Tool.

Usage:
    python -m pii_redactor redact \
        --input data/input/Red_Herring_Prospectus.docx \
        --output data/output/redacted_output.docx \
        --entity-map-out data/output/entity_map.json \
        --config config/entity_rules.yaml \
        --report data/output/redaction_run_report.json

Prints a live summary table (per entity type: count found, count redacted)
to stdout using rich for production-grade formatting.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from pii_redactor.pipeline import RedactionPipeline, RedactionStats

# Force UTF-8 for Windows terminals
if os.name == "nt":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

console = Console(force_terminal=True)


def _setup_logging(verbose: bool) -> None:
    """Configure logging with rich formatting."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def _print_summary(stats: RedactionStats, output_path: str) -> None:
    """Print a rich summary table of the redaction run."""
    # Header panel
    console.print()
    console.print(
        Panel.fit(
            "[bold green]✓ PII Redaction Complete[/bold green]",
            border_style="green",
        )
    )
    console.print()

    # Entity type breakdown table
    table = Table(
        title="[bold]Redaction Summary[/bold]",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Entity Type", style="bold", min_width=15)
    table.add_column("Found", justify="right", style="yellow")
    table.add_column("Redacted", justify="right", style="green")
    table.add_column("Status", justify="center")

    # Combine all entity types from found and redacted
    all_types = sorted(
        set(list(stats.entities_found.keys()) + list(stats.entities_redacted.keys()))
    )

    total_found = 0
    total_redacted = 0

    for entity_type in all_types:
        found = stats.entities_found.get(entity_type, 0)
        redacted = stats.entities_redacted.get(entity_type, 0)
        total_found += found
        total_redacted += redacted

        if redacted > 0:
            status = "[green]✓[/green]"
        elif found > 0:
            status = "[yellow]⚠[/yellow]"
        else:
            status = "[dim]—[/dim]"

        table.add_row(entity_type, str(found), str(redacted), status)

    # Totals row
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total_found}[/bold]",
        f"[bold]{total_redacted}[/bold]",
        "",
    )

    console.print(table)
    console.print()

    # Run metadata
    meta_table = Table(show_header=False, border_style="dim", box=None)
    meta_table.add_column("Key", style="dim")
    meta_table.add_column("Value")
    meta_table.add_row("Output file", str(output_path))
    meta_table.add_row("Segments processed", str(stats.segments_processed))
    meta_table.add_row("Total replacements", str(stats.total_replacements))
    meta_table.add_row("Duration", f"{stats.duration_seconds:.1f}s")

    if stats.low_confidence_entities:
        meta_table.add_row(
            "Low-confidence flags",
            f"[yellow]{len(stats.low_confidence_entities)} (see low_confidence_review.csv)[/yellow]",
        )

    console.print(meta_table)
    console.print()


@click.group()
@click.version_option(version="1.0.0", prog_name="pii-redactor")
def cli():
    """PII Redaction Tool — Detect and pseudonymize PII in DOCX documents."""
    pass


@cli.command()
@click.option(
    "--input", "-i",
    "input_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the input DOCX file.",
)
@click.option(
    "--output", "-o",
    "output_path",
    required=True,
    type=click.Path(path_type=Path),
    help="Path for the redacted output DOCX file.",
)
@click.option(
    "--entity-map-out",
    "entity_map_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to save the entity mapping JSON (maps fake → real PII; do NOT share).",
)
@click.option(
    "--config", "-c",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to entity_rules.yaml config file.",
)
@click.option(
    "--report",
    "report_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to save the structured JSON run report.",
)
@click.option(
    "--seed",
    type=int,
    default=42,
    show_default=True,
    help="Random seed for reproducible pseudonymization.",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Enable verbose (DEBUG) logging.",
)
def redact(
    input_path: Path,
    output_path: Path,
    entity_map_path: Path | None,
    config_path: Path | None,
    report_path: Path | None,
    seed: int,
    verbose: bool,
) -> None:
    """Detect and redact PII in a DOCX document.

    Processes the input DOCX, detects PII using hybrid regex+NER detection,
    generates format-preserving fake replacements, and writes a redacted
    DOCX with formatting preserved.

    \b
    Example:
        python -m pii_redactor redact \\
            --input data/input/Red_Herring_Prospectus.docx \\
            --output data/output/redacted_output.docx \\
            --entity-map-out data/output/entity_map.json \\
            --config config/entity_rules.yaml \\
            --report data/output/redaction_run_report.json
    """
    _setup_logging(verbose)

    console.print(
        Panel.fit(
            "[bold blue]PII Redaction Tool v1.0.0[/bold blue]\n"
            f"Input:  {input_path}\n"
            f"Output: {output_path}",
            title="[bold]Starting Redaction[/bold]",
            border_style="blue",
        )
    )
    console.print()

    try:
        pipeline = RedactionPipeline(
            config_path=config_path,
            seed=seed,
        )

        stats = pipeline.run(
            input_path=input_path,
            output_path=output_path,
            entity_map_path=entity_map_path,
            report_path=report_path,
        )

        _print_summary(stats, output_path)

    except FileNotFoundError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Unexpected error:[/bold red] {e}")
        logging.exception("Pipeline failed")
        sys.exit(2)


@cli.command()
@click.option(
    "--predictions",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to predictions JSONL file.",
)
@click.option(
    "--ground-truth",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to labeled ground truth JSONL file.",
)
@click.option(
    "--output", "-o",
    "output_path",
    type=click.Path(path_type=Path),
    default="EVALUATION_REPORT.md",
    help="Path for the generated evaluation report.",
)
def evaluate(
    predictions: Path,
    ground_truth: Path,
    output_path: Path,
) -> None:
    """Generate an evaluation report comparing predictions to ground truth.

    Computes per-entity-type precision, recall, and F1 scores,
    and generates a detailed EVALUATION_REPORT.md.
    """
    _setup_logging(verbose=False)

    from pii_redactor.evaluation.report_generator import ReportGenerator

    console.print("[bold]Generating evaluation report...[/bold]")

    generator = ReportGenerator()
    generator.generate(
        predictions_path=predictions,
        ground_truth_path=ground_truth,
        output_path=output_path,
    )

    console.print(f"[green]✓ Evaluation report saved to {output_path}[/green]")


if __name__ == "__main__":
    cli()
