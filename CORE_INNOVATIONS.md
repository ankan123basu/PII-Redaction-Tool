# 🧠 Core Engineering Innovations

Most standard PII redactors rely on simple Regex or off-the-shelf NER, which fail catastrophically on dense, unformatted financial documents. 

To solve this, we implemented six unique, custom-built algorithms and heuristics that differentiate this tool from basic wrappers.

---

## 1. Levenshtein Fuzzy Entity Merging (`RapidFuzz`)
**The Problem:** Standard redactors assign pseudonyms based on exact string matches. If a document mentions "Kushal Hegde" and later "Mr. Kushal Subbayya Hegde", standard tools assign them two *different* fake names, destroying the ability to track an entity's flow through a document.

**The Innovation:** We maintain a global entity state. Before generating a new pseudonym, we run incoming text through a **RapidFuzz `token_sort_ratio`** comparison against all previously discovered entities of the same type. If the fuzzy match score exceeds `85`, the new variation is mathematically resolved to the *exact same fake pseudonym* assigned previously.

## 2. Deterministic Overlap Resolution
**The Problem:** When running both Regex and ML-based NER concurrently, collisions are inevitable. Regex might flag `11/3 Birdewadi` as a partial match, while the NER tags `11/3 Birdewadi, Pune` as an address. Naive replacement causes catastrophic string index out-of-bounds errors when replacing the same text twice.

**The Innovation:** We implemented a greedy interval-scheduling algorithm. All detected entities are collected into a master list and sorted by:
1. `validated` status (cryptographically verified regex beats raw NER)
2. `confidence` score
3. `-length` (longer, more specific spans beat shorter ones)

The algorithm linearly sweeps the list, discarding any span that mathematically overlaps with a higher-priority chosen span.

## 3. Context-Window Expansion Heuristic
**The Problem:** Out-of-the-box spaCy NER (`en_core_web_lg`) is trained primarily on Western datasets. It completely fails on long, complex Indian postal addresses, usually only tagging small fragments (like `"Village Birdewadi"` as a `GPE`).

**The Innovation:** We built a custom **±150-char sliding window algorithm**. When the NER detects a partial geo-fragment, the algorithm anchors onto it and scans the surrounding raw text. It looks for bounding indicators (e.g., `"Registered Office:"`) and trailing indicators (6-digit Indian PIN codes). It then forcefully expands the bounding box to capture the entire contiguous address, while trimming leading context words.

## 4. Cryptographic & Checksum Validation
**The Problem:** Financial prospectuses contain tens of thousands of random digits (financial figures, share counts, account numbers). A basic regex for "16 digits" will flag every bank account as a Credit Card, resulting in massive false positives.

**The Innovation:** We don't trust regex alone. Matches are passed through a cryptographic validation layer:
- **Credit Cards:** Validated using the **Luhn Modulo 10** algorithm.
- **PAN / CIN:** Validated against their strict Indian governmental alphanumeric checksum structures (e.g., CIN must be exactly 21 chars: 1 letter + 5 digits + 2 letters + 4 digits + 3 letters + 6 digits).

## 5. Run-Level XML Recombination (Formatting Preservation)
**The Problem:** A Word `.docx` paragraph consists of multiple "runs" of text, each carrying its own styling (bold, italic, font size, color). If a script simply replaces `paragraph.text`, the underlying XML runs are destroyed, and all formatting is permanently lost.

**The Innovation:** We built a custom `RunInfo` parser that maps continuous text string offsets back to their discrete DOCX XML `<w:r>` tags. When substituting a PII span, the engine calculates exactly which XML runs are touched. It performs surgical text modification strictly at the character boundaries, preserving bold names, colored links, and italicized headers perfectly.

## 6. Role-Anchor Regex Fallback
**The Problem:** In highly dense, tabular legal data, sentence context is missing. NLP models often fail to recognize names in tables because they lack the surrounding verbs/nouns they were trained on.

**The Innovation:** We implemented a secondary detection layer using look-behind regex aimed at "legal role anchors". Even if the ML model completely misses the text, our engine scans for anchors like `"Contact Person:"`, `"Director:"`, or `"Company Secretary:"`, forcefully extracting the following N words as a `FULL_NAME` entity.
