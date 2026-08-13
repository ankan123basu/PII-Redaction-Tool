# PII Redaction Tool — PowerShell convenience runner
#
# Usage:
#   .\scripts\run_redaction.ps1 [-Input "path\to\input.docx"] [-Output "path\to\output.docx"]

param(
    [string]$Input = "data\input\Red_Herring_Prospectus.docx",
    [string]$Output = "data\output\redacted_output.docx"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "=== PII Redaction Tool ===" -ForegroundColor Cyan
Write-Host "Input:  $Input"
Write-Host "Output: $Output"
Write-Host ""

Set-Location $ProjectDir

# Activate venv if present
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & .venv\Scripts\Activate.ps1
}

& python -m pii_redactor redact `
    --input $Input `
    --output $Output `
    --entity-map-out "data\output\entity_map.json" `
    --config "config\entity_rules.yaml" `
    --report "data\output\redaction_run_report.json"

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
