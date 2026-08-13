# Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLI Entry Point                              │
│                    (cli.py / __main__.py)                            │
│  Parses arguments, configures logging, invokes pipeline             │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     RedactionPipeline                                │
│                      (pipeline.py)                                   │
│  Orchestrates:  Read → Detect → Resolve → Pseudonymize → Write      │
│  Owns: EntityMap, FakerProvider, DetectorRegistry, RedactionStats    │
└──┬──────────────┬───────────────┬──────────────────┬────────────────┘
   │              │               │                  │
   ▼              ▼               ▼                  ▼
┌────────┐  ┌───────────┐  ┌────────────┐  ┌──────────────┐
│ DocxIO │  │ Detectors │  │Pseudonymize│  │  Evaluation  │
└────────┘  └───────────┘  └────────────┘  └──────────────┘
```

## Module Dependency Graph

```
cli.py
  └── pipeline.py
        ├── document_io/
        │     ├── docx_reader.py     ← python-docx (reads Document, yields TextSegments)
        │     └── docx_writer.py     ← python-docx (modifies runs in-place, saves)
        ├── detectors/
        │     ├── registry.py        ← config/entity_rules.yaml (YAML-driven wiring)
        │     ├── regex_detectors.py ← validators/checksum.py (Luhn, PAN, CIN, etc.)
        │     ├── ner_detector.py    ← spaCy en_core_web_lg (lazy-loaded singleton)
        │     └── base.py           (abstract Detector interface)
        └── pseudonymizer/
              ├── entity_map.py      ← rapidfuzz (fuzzy matching for consistency)
              └── faker_provider.py  ← Faker (format-preserving fake generation)
```

## Data Flow

### 1. Document Reading (`docx_reader.py`)

```
DOCX File
  → python-docx Document instance (shared with writer!)
    → Iterate sections: header paragraphs, footer paragraphs
    → Iterate body: paragraphs and tables (via iter_inner_content)
      → For each paragraph: extract runs, build character-offset map
      → For each table: iterate rows/cells, deduplicate merged cells
    → Output: List[TextSegment]
              Each segment carries: text, location_type, location_id,
              runs (offset → run index mapping), element_ref (paragraph object)
```

**Critical design constraint**: The reader and writer must share the **same** `python-docx` Document instance. If they open separate instances, `element_ref` pointers from the reader point to different objects than the writer would modify, and replacements silently fail.

### 2. Detection (`detectors/`)

```
TextSegment.text
  → RegexDetector (8 patterns with validators):
      EMAIL, PHONE, SSN, CREDIT_CARD, DOB, IP_ADDRESS, PAN, CIN
  → NERDetector (spaCy NER with precision filters):
      FULL_NAME (PERSON label → name-shape check, stoplist)
      COMPANY_NAME (ORG label → legal-suffix boost, expanded stoplist)
      ADDRESS (GPE/LOC/FAC labels → geo stoplist, pincode detection)
  → Output: List[DetectedEntity] (may have overlapping spans)
```

### 3. Overlap Resolution (`pipeline.py :: resolve_overlaps`)

```
List[DetectedEntity] (with overlaps)
  → Sort by priority: validated > confidence > span_length > position
  → Greedy selection: iterate sorted list, skip if overlaps with any selected
  → Output: List[DetectedEntity] (non-overlapping, deterministic)
```

### 4. Pseudonymization (`pseudonymizer/`)

```
DetectedEntity
  → EntityMap.get_or_create(original_text, entity_type, generator)
      → Exact match? Return cached fake value
      → Fuzzy match (token_set_ratio ≥ 80, same type only)? Return cached
      → No match? Generate new fake via FakerProvider, cache it
  → FakerProvider.generate(original, type)
      → Type-specific generator (preserves format: honorifics, separators,
        country codes, digit grouping, date format, legal suffixes)
  → Output: Replacement(start, end, original_text, replacement_text)
```

### 5. Document Writing (`docx_writer.py`)

```
TextSegment + List[Replacement]
  → For each replacement (in reverse offset order):
      → Find which runs the replacement spans (using RunInfo offset map)
      → Perform run-level text replacement:
          - Single-run: simple string replace in that run
          - Cross-run: merge text into first run, clear subsequent runs
      → Formatting (bold, italic, font, color) is preserved because we
        modify run.text, not the run object itself
  → Document.save(output_path)
```

## Extension Points

| Extension | Where to add | Changes needed |
|-----------|-------------|----------------|
| New regex PII type | `config/entity_rules.yaml` + optional method in `regex_detectors.py` + optional validator in `checksum.py` | Zero pipeline changes |
| New NER PII type | Map new spaCy label in `_map_spacy_label()`, add filter in `_apply_precision_filters()` | Zero pipeline changes |
| New document format | Implement `BaseReader`/`BaseWriter` interface | Pipeline unchanged |
| New fake generator | Add method to `faker_provider.py`, reference in YAML | Zero pipeline changes |
| Different NER model | Change model name in `_load_spacy_model()` | Zero pipeline changes |

## Thread Safety / Concurrency

The current design is **single-threaded by choice**. Reasons:
- python-docx Document objects are not thread-safe
- spaCy's `nlp()` calls are not safe to parallelize without `nlp.pipe()`
- The EntityMap must be built sequentially (later entities may fuzzy-match earlier ones)
- For a 400-page document, 42 seconds is acceptable for a batch pipeline

If parallelism were needed, the approach would be:
1. Split segments into batches
2. Run detection in parallel (each batch gets its own NER pipeline)
3. Merge results and build EntityMap sequentially
4. Apply replacements sequentially (single Document instance)
