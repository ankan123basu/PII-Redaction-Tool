#!/bin/bash
# PII Redaction Tool — convenience runner script
#
# Usage:
#   ./scripts/run_redaction.sh [input_file] [output_file]
#
# Defaults to the standard input/output paths if not provided.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

INPUT="${1:-$PROJECT_DIR/data/input/Red_Herring_Prospectus.docx}"
OUTPUT="${2:-$PROJECT_DIR/data/output/redacted_output.docx}"

echo "=== PII Redaction Tool ==="
echo "Input:  $INPUT"
echo "Output: $OUTPUT"
echo ""

cd "$PROJECT_DIR"

# Activate venv if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

python -m pii_redactor redact \
    --input "$INPUT" \
    --output "$OUTPUT" \
    --entity-map-out data/output/entity_map.json \
    --config config/entity_rules.yaml \
    --report data/output/redaction_run_report.json

echo ""
echo "=== Done ==="
