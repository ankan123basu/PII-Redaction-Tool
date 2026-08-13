# ⚖️ Design Tradeoffs & Limitations

Building a production-grade PII redactor for dense financial documents requires making conscious engineering tradeoffs. This document details the specific compromises made to maximize precision and maintain readability in the output document.

## Deliberate Algorithmic Tradeoffs

### 1. DOB Context-Window Guard (Recall vs. Precision)
- **The Implementation:** Dates are only classified as a `DATE_OF_BIRTH` if a specific context keyword (e.g., "DOB", "date of birth", "born") appears within an 80-character sliding window of the date string.
- **The Tradeoff:** We sacrifice a small amount of recall (missing isolated birth dates in unstructured text) to gain massive precision. 
- **The Why:** A 400-page legal prospectus contains thousands of dates (incorporation dates, regulation filing dates, meeting dates). If we blindly redact every `dd/mm/yyyy` string, the document becomes completely unreadable and the tool generates unacceptable amounts of false positives.

### 2. Luhn Modulo 10 Checksum (Rigid Formatting vs. Broad Catching)
- **The Implementation:** Regex matches for 13-19 digit strings are passed through a cryptographic Luhn Modulo 10 checksum algorithm. If the checksum fails, the match is discarded.
- **The Tradeoff:** We risk missing poorly generated "fake" or "test" credit card numbers in the data if they don't conform to actual issuing standards.
- **The Why:** Financial documents are packed with 16-digit account numbers, long reference IDs, and transaction codes. Treating all 16-digit numbers as credit cards is a naive approach that destroys document integrity.

### 3. ORG Stoplist Filtering (Aggressive Exclusion)
- **The Implementation:** Common legal pronoun references (e.g., "the Company", "our Company", "the Board", "the Promoters") are explicitly added to a negative stoplist and excluded from `COMPANY_NAME` detection, even if the NER model highly scores them.
- **The Tradeoff:** We might accidentally miss a genuinely named company if its legal registered name is highly generic.
- **The Why:** In legal prose, "the Company" is used as a stand-in for the primary entity hundreds of times per page. Redacting it makes the legal contract impossible to parse for human reviewers.

### 4. Levenshtein Fuzzy Matching Threshold (token_sort_ratio ≥ 85)
- **The Implementation:** When pseudonymizing, entities are clustered using a fuzzy string matching algorithm (RapidFuzz). "Kushal Hegde" and "Mr. Kushal Subbayya Hegde" resolve to the same internal state.
- **The Tradeoff:** There is a mathematical risk of a "false merge" where two genuinely different individuals with extremely similar names (e.g., "John Smith" and "Jon Smith") are mistakenly given the same pseudonym.
- **The Why:** The alternative (strict exact-string matching) guarantees that slight typographical variations of a target's name are assigned different pseudonyms. This breaks entity-resolution down the line, as analysts can no longer track a single individual's footprint across the document.

### 5. CIN as PII (Policy Decision)
- **The Implementation:** Corporate Identity Numbers (CINs) are detected and redacted by default.
- **The Tradeoff:** CINs are technically public records registered with the Ministry of Corporate Affairs (MCA). They are not "secret".
- **The Why:** While public, CINs uniquely identify a specific corporate entity and can be trivially used to de-anonymize the document via a quick MCA lookup. We treat them as PII-adjacent identifiers to ensure true anonymization.

---

## Known System Limitations

### False Positives (Over-Redaction)
- **Short Geographical Entities:** Short location names (e.g., "India", "Mumbai", "Pune") are aggressively tagged by the NER model as `ADDRESS`. While not strictly PII in isolation, they are redacted. We assign these a low confidence score (0.40 - 0.60) to flag them for human review.
- **Legal Table Headers:** Abbreviations like "Ltd." or "Pvt." when used as isolated column headers in tables are occasionally misclassified by the NER as standalone companies.

### False Negatives (Under-Redaction)
- **Cross-Run Word Splits:** If a name is formatted such that half the word is **bold** and half is not, the underlying `python-docx` XML splits the word into two separate `<w:r>` tags. While our run-recombination logic handles many styling boundaries, extreme edge cases can fragment the text and evade regex/NER detection.
- **Fragmented Table Addresses:** If an address is structured across multiple separate table cells (e.g., Column 1: "Street", Column 2: "City"), the system processes them as separate entities, often failing to recognize the semantic link between them.
