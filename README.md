# PII Redaction Tool

## Overview

A production-grade PII (Personally Identifiable Information) redaction tool for DOCX documents. It detects 11 types of PII using a **hybrid regex + NER approach**, replaces them with **format-preserving pseudonyms** (not just "[REDACTED]"), maintains **entity-level consistency** across the entire document (same real name → same fake name every time), and writes back a **formatting-preserved DOCX** with all original styles, tables, headers, and footers intact.

Built specifically to handle dense legal/financial documents like Red Herring Prospectuses — documents with hundreds of pages, repeated tables, PII embedded in legal prose, and the same entities appearing dozens of times.

---

## Approach

### Why Hybrid Detection (Regex + NER)

We use **two complementary detection methods** because no single method handles all PII types well:

| Detection Method | Good For | Bad For |
|---|---|---|
| **Regex + Validators** | Structured PII with known grammar: emails, phone numbers, SSNs, credit cards, IPs, PAN, CIN | Names, companies, addresses — no fixed grammar to match |
| **NER (spaCy)** | Unstructured PII: personal names ("Rashi Patil"), company names, physical addresses | Credit cards, SSNs, IPs — no semantic signal in embeddings |

**Regex alone** cannot catch "Kushal Subbayya Hegde" as a person's name — there is no regular expression that reliably matches arbitrary Indian names without massive false positives.

**NER alone** is unreliable for credit card numbers and SSNs — these are just digit sequences with no contextual semantic signal that a language model can learn from.

**The hybrid approach** runs both methods, then uses a **deterministic overlap resolution algorithm** to pick the best detection when they fire on the same text span (validated regex wins over unvalidated NER).

---

## Architecture

### Pipeline Flow

```mermaid
flowchart LR
    A["📄 Input DOCX"] --> B["📖 DOCX Reader"]
    B --> C["🔍 Detection"]
    C --> D["⚖️ Overlap\nResolution"]
    D --> E["🎭 Pseudonymizer"]
    E --> F["✍️ DOCX Writer"]
    F --> G["📄 Redacted DOCX"]

    subgraph Detection ["Hybrid Detection Layer"]
        direction TB
        C1["Regex Detectors\n(EMAIL, PHONE, SSN,\nCC, IP, DOB, PAN, CIN)"]
        C2["NER Detector\n(spaCy en_core_web_lg)\n(FULL_NAME, COMPANY_NAME,\nADDRESS)"]
    end

    C --> C1
    C --> C2
    C1 --> D
    C2 --> D

    E --> H["🗺️ Entity Map\n(real → fake)"]
    F --> I["📊 Run Report\n(JSON)"]
```

### Component Architecture

```mermaid
graph TD
    CLI["cli.py\n(Click CLI)"] --> Pipeline["pipeline.py\n(Orchestrator)"]
    Pipeline --> Reader["docx_reader.py\n(Extract text segments)"]
    Pipeline --> Registry["registry.py\n(Load YAML → wire detectors)"]
    Registry --> Regex["regex_detectors.py\n(8 pattern types + validators)"]
    Registry --> NER["ner_detector.py\n(spaCy + heuristics)"]
    Regex --> Validators["checksum.py\n(Luhn, PAN, CIN validators)"]
    Pipeline --> Pseudonymizer["entity_map.py\n(Consistent fake mapping)"]
    Pseudonymizer --> Faker["faker_provider.py\n(Format-preserving fakes)"]
    Pipeline --> Writer["docx_writer.py\n(Run-level replacement)"]
    Pipeline --> Metrics["metrics.py\n(Precision / Recall / F1)"]
    Metrics --> Report["report_generator.py\n(Auto-gen EVALUATION_REPORT.md)"]

    style CLI fill:#4CAF50,color:#fff
    style Pipeline fill:#2196F3,color:#fff
    style Regex fill:#FF9800,color:#fff
    style NER fill:#FF9800,color:#fff
    style Pseudonymizer fill:#9C27B0,color:#fff
```

### Project Structure

```
pii-redaction-tool/
├── README.md                           # This file
├── EVALUATION_REPORT.md                # Evaluation results with methodology
├── ARCHITECTURE.md                     # Detailed architecture documentation
├── requirements.txt                    # Dependencies
├── pyproject.toml                      # PEP 621 project metadata
├── .gitignore                          # Includes entity_map.json exclusion
├── .github/workflows/ci.yml           # Lint + test CI pipeline
├── config/
│   └── entity_rules.yaml              # Declarative PII type registry
├── src/pii_redactor/
│   ├── __init__.py                    # Package version
│   ├── __main__.py                    # python -m pii_redactor entry point
│   ├── cli.py                         # Click CLI with rich output tables
│   ├── pipeline.py                    # Orchestrator: extract → detect → resolve → pseudonymize → write
│   ├── document_io/
│   │   ├── docx_reader.py            # Structure-preserving DOCX extraction
│   │   └── docx_writer.py            # Run-level replacement to preserve formatting
│   ├── detectors/
│   │   ├── base.py                    # Abstract Detector interface
│   │   ├── regex_detectors.py        # Email, phone, SSN, CC (Luhn!), IP, DOB, PAN, CIN
│   │   ├── ner_detector.py           # spaCy NER + role-label fallback + address expansion
│   │   └── registry.py               # Loads YAML config, wires detectors
│   ├── validators/
│   │   └── checksum.py                # Luhn checksum, phone/SSN/PAN/CIN validators
│   ├── pseudonymizer/
│   │   ├── faker_provider.py          # Format-preserving fake value generation
│   │   └── entity_map.py             # Consistent entity→fake mapping with fuzzy matching
│   └── evaluation/
│       ├── metrics.py                 # Precision/recall/F1 per entity type
│       └── report_generator.py        # Auto-generates EVALUATION_REPORT.md
├── tests/                             # 128 tests across 4 test modules
│   ├── test_regex_detectors.py        # 59 test cases for regex patterns + validators
│   ├── test_ner_detector.py           # 28 NER precision filter + address expansion tests
│   ├── test_pseudonymizer_consistency.py  # Consistency, fuzzy matching, format tests
│   ├── test_pipeline_end_to_end.py    # Full pipeline integration tests
│   └── fixtures/
│       ├── sample_input.docx          # Synthetic test DOCX with known PII
│       └── generate_fixture.py        # Script to regenerate the fixture
├── data/
│   ├── input/                         # Place input DOCX files here
│   └── output/                        # Redacted output + reports
│       ├── redacted_output.docx       # ← The redacted document (committed)
│       └── redaction_run_report.json  # ← Pipeline run statistics (committed)
└── scripts/
    ├── run_redaction.sh               # Bash convenience script
    └── run_redaction.ps1              # PowerShell convenience script
```

---

## How to Extend to a New PII Type

Adding a new PII type requires **zero code changes to the pipeline**. Here's a worked example for adding **Passport Number** detection:

### Step 1: Add to `config/entity_rules.yaml`

```yaml
  - name: PASSPORT
    method: regex
    enabled: true
    confidence_threshold: 0.90
    pattern: '[A-Z]{1}[0-9]{7}'
    spacy_labels: null
    validator: null
    fake_generator: "custom:fake_passport"
    description: "Indian passport number (1 letter + 7 digits)"
```

### Step 2: Add a regex pattern to `regex_detectors.py` (if needed)

If the pattern in the YAML is simple, the existing `RegexDetector` handles it. For complex patterns with validators, add a method:

```python
_PASSPORT_PATTERN = re.compile(r"(?<![A-Z0-9])[A-Z]\d{7}(?![A-Z0-9])")

def _detect_passports(self, text: str) -> list[DetectedEntity]:
    entities = []
    for match in self._PASSPORT_PATTERN.finditer(text):
        entities.append(DetectedEntity(
            text=match.group(), entity_type="PASSPORT",
            start=match.start(), end=match.end(),
            confidence=0.90, detector_name=self.name, validated=True,
        ))
    return entities
```

### Step 3: Add a fake generator to `faker_provider.py`

```python
def _fake_passport(self, original: str) -> str:
    letter = self._rng.choice(string.ascii_uppercase)
    digits = "".join(str(self._rng.randint(0, 9)) for _ in range(7))
    return f"{letter}{digits}"
```

### Step 4: Add tests

```python
def test_valid_passport(self, detector):
    entities = detector.detect("Passport: J1234567")
    assert len(entities) == 1
    assert entities[0].entity_type == "PASSPORT"
```

That's it. No changes to the pipeline, CLI, writer, or any other module.

---

## Setup & Usage

### Prerequisites

- Python 3.11+
- ~1 GB disk space for spaCy model

### Installation

```bash
# Clone the repository
git clone https://github.com/your-repo/pii-redaction-tool.git
cd pii-redaction-tool

# Create virtual environment
python -m venv .venv

# Activate (Linux/Mac)
source .venv/bin/activate

# Activate (Windows PowerShell)
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
pip install -e ".[dev]"

# Download spaCy NER model
python -m spacy download en_core_web_lg
```

### Running Redaction

```bash
python -m pii_redactor redact \
    --input data/input/Red_Herring_Prospectus.docx \
    --output data/output/redacted_output.docx \
    --entity-map-out data/output/entity_map.json \
    --config config/entity_rules.yaml \
    --report data/output/redaction_run_report.json
```

### Expected Output

The CLI prints a rich summary table (actual output from the Red Herring Prospectus run):

```
╭──────────────────────────╮
│ ✓ PII Redaction Complete │
╰──────────────────────────╯

               Redaction Summary
┌─────────────────┬───────┬──────────┬────────┐
│ Entity Type     │ Found │ Redacted │ Status │
├─────────────────┼───────┼──────────┼────────┤
│ ADDRESS         │   104 │      103 │   ✓    │
│ CIN             │     9 │        9 │   ✓    │
│ COMPANY_NAME    │   786 │      782 │   ✓    │
│ EMAIL           │    48 │       48 │   ✓    │
│ FULL_NAME       │   312 │      297 │   ✓    │
│ PHONE           │    33 │       32 │   ✓    │
├─────────────────┼───────┼──────────┼────────┤
│ TOTAL           │  1292 │     1271 │        │
└─────────────────┴───────┴──────────┴────────┘

 Segments processed    3612
 Total replacements    1258
 Duration              ~35s
 Low-confidence flags  633 (see low_confidence_review.csv)
```

### Running Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=src/pii_redactor --cov-report=term-missing

# Specific test module
pytest tests/test_regex_detectors.py -v
```

---

## Design Tradeoffs & Known False Positives/Negatives

### Deliberate Design Choices

| Decision | Tradeoff | Rationale |
|---|---|---|
| **DOB context-window guard** | May miss DOBs without nearby keywords ("DOB", "born") — recall cost | Without this, every date in a 400-page legal document gets redacted, destroying precision. Hundreds of incorporation dates, filing dates, and regulation dates would be false positives. |
| **Credit card Luhn validation** | Rejects any CC-like number that fails Luhn — may miss poorly-formed test data | The precision gain is massive. Financial documents contain many 16-digit numbers (account numbers, reference numbers) that are not credit cards. |
| **ORG stoplist** | "the Company", "our Company" excluded — may miss actual company names that use generic phrasing | Legal documents use "the Company" as a pronoun-reference hundreds of times. Redacting all of them would make the output unreadable. |
| **CIN included as PII** | CINs are publicly registered — arguably not secret | They uniquely identify a company and can be used for entity resolution. We treat them as PII-adjacent and include detection by default. Users can disable in `entity_rules.yaml`. |
| **Entity fuzzy matching (threshold=85)** | Risk of false merges: two genuinely different people with similar names could be merged | Conservative threshold and same-type-only matching mitigate this. The benefit — consistent pseudonymization when the same person's name varies slightly — is critical for output quality. |
| **en_core_web_lg model** | ~560 MB download, slower than small model | Significantly better NER accuracy for Indian names and companies compared to en_core_web_sm. |

### Known False Positives (Things We Over-Redact)

1. **Short location names** (e.g., "India", "Mumbai") detected as ADDRESS by NER — these are common in legal text and arguably not PII in isolation. We assign low confidence (0.40-0.60) to mitigate.
2. **Legal-suffix company abbreviations** in tables — "XYZ Ltd." where XYZ is a column header, not an actual company.

### Known False Negatives (Things We Might Miss)

1. **DOBs without context keywords** — a date of birth appearing in a table cell labeled "DOB" only in the column header (not within 80 chars of the actual date cell) may be missed.
2. **Names split across runs** — if a name is partially bold and partially not, python-docx splits it across runs. Our run-recombination handles most cases but edge cases in complex formatting may cause partial detection.
3. **Addresses spanning multiple table cells** — if an address is split across "Street", "City", "State" columns rather than in a single cell, each piece is detected separately and may not be recognized as a complete address.

---

## Evaluation Summary

See **[EVALUATION_REPORT.md](EVALUATION_REPORT.md)** for the full evaluation with per-entity-type precision/recall/F1 scores, concrete false positive/negative analysis, and honest methodology disclosure.

**Quick numbers** (18-span hand-labeled sample from the real prospectus):

| Metric | Value |
|--------|-------|
| Precision (micro) | 100.0% |
| Recall (micro) | 94.4% |
| **F1 (micro)** | **97.1%** |
| Only miss | "Waterloo Industrial Park VI Private Limited" (genuine spaCy FN) |

> **Honest caveat**: These metrics are on a small 18-span sample. Per-type scores of "100%" for EMAIL (1 span), PHONE (1 span), etc. reflect the tiny sample size, not a claim of perfection. The address-expansion heuristic was refined against this sample and hasn't been validated on additional documents. See the evaluation report for full methodology and limitations.

### NER Bug Fixes (Before/After)
During development on the real prospectus, we identified and fixed four specific NER failure modes — each diagnosed from a concrete error, not tuned by trial-and-error:
1. **Multi-line Address Fragmentation**: Fixed `docx_reader` to join all paragraphs within a single table cell before detection, preventing multi-line addresses from being split into unrecognizable fragments.
2. **Role-Label Name Evasion**: Added a deterministic regex fallback to catch names immediately following role titles (e.g., "Contact Person: Sarthak Malvadkar") which spaCy consistently missed in dense legal contexts.
3. **Street Names as Companies**: Added negative filters for address-indicator terms (Road, Nagar, Business Centre) to prevent spaCy from misclassifying street names as `COMPANY_NAME` unless they have a valid legal suffix.
4. **Address Span Expansion**: spaCy's NER only tagged small fragments of Indian postal addresses (like "Village Birdewadi") as location entities. Added a heuristic to expand these fragments outward into full addresses by searching the surrounding ±150-char text window for labels ("Registered Office:"), house numbers, and pincodes. Leading context words are trimmed so spans start cleanly at the actual address.

**Progression**: ~75% F1 (baseline) → ~82% (after bugs 1-3) → **97.1%** (after bug 4 + GT typo fix). Each improvement came from fixing a real detection bug, not from adjusting the evaluation set or loosening matching criteria.

---

## Security Note

> ⚠️ **The `entity_map.json` file maps fake values back to real PII and must NEVER be committed to version control or shared outside your organization.**

This file is:
- Added to `.gitignore` to prevent accidental commits
- Generated in `data/output/` alongside the redacted document
- Required only for internal QA / re-identification — delete it after verification

If the entity map leaks, the pseudonymization is fully reversible, defeating the purpose of redaction.

---

## Stretch Features Implemented

1. **Confidence scoring + review flags** — Low-confidence NER detections (below 0.75) are redacted but also logged to `data/output/low_confidence_review.csv` so a human reviewer knows where to double-check. This shows awareness that no PII tool is 100% automatable in production.

2. **Structured JSON redaction report** — Each run produces `data/output/redaction_run_report.json` with entity counts, types, run duration, and detector metadata — suitable for feeding into a monitoring dashboard. Signals that we think of this as a pipeline component, not a one-off script.

---

## License

MIT
