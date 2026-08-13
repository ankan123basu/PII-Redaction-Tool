"""
NER-based PII detector using spaCy for unstructured PII types.

Detects: full names (PERSON), company/organization names (ORG),
and physical addresses (LOC/GPE/FAC).

Design decisions:
- Uses spaCy en_core_web_lg (or en_core_web_sm as fallback).
- ORG precision guards: stoplist of generic terms ("the Company", "our Company",
  "the Board") and optional legal-suffix cross-check.
- PERSON precision guards: reject single-token entities < 3 chars,
  reject all-caps abbreviations (likely acronyms not names).
- ADDRESS: combines GPE/LOC/FAC labels with nearby pincode detection
  to build address spans.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pii_redactor.detectors.base import DetectedEntity, Detector

logger = logging.getLogger(__name__)

# Lazy-loaded spaCy model
_nlp_model: Any = None


def _load_spacy_model() -> Any:
    """Load spaCy model with fallback chain."""
    global _nlp_model
    if _nlp_model is not None:
        return _nlp_model

    import spacy

    model_names = ["en_core_web_lg", "en_core_web_sm"]
    for model_name in model_names:
        try:
            _nlp_model = spacy.load(model_name)
            logger.info("Loaded spaCy model: %s", model_name)
            return _nlp_model
        except OSError:
            logger.warning("spaCy model '%s' not found, trying fallback...", model_name)

    raise RuntimeError(
        "No spaCy model available. Install one with: "
        "python -m spacy download en_core_web_lg"
    )


class NERDetector(Detector):
    """Named Entity Recognition detector using spaCy.

    Detects FULL_NAME, COMPANY_NAME, and ADDRESS entities using
    spaCy's built-in NER pipeline with precision-focused post-processing.
    """

    # Generic references that should NOT be treated as company names.
    # Built from manual inspection of actual false positives in Red Herring
    # Prospectus output (low_confidence_review.csv, 1365 entries analyzed).
    _ORG_STOPLIST: set[str] = {
        # Pronoun-style references common in legal documents
        "the company", "our company", "company", "the board", "board",
        "the board of directors", "board of directors",
        "the registrar", "registrar", "the auditor", "the auditors",
        "the committee", "the management", "management",
        "the government", "government", "government of india",
        "the promoter", "the promoters", "promoter", "promoters",
        "the investor", "the investors", "investor", "investors",
        "the subscriber", "the subscribers",
        "the applicant", "the applicants",
        "the lender", "the lenders", "the borrower", "the borrowers",
        "the issuer", "issuer", "the underwriter", "the underwriters",
        "the tribunal", "the court", "the bench",
        # Regulatory body abbreviations / acronyms
        "sebi", "rbi", "mca", "roc", "bse", "nse", "upi", "asba",
        "nsdl", "cdsl", "irda", "fema", "rera", "gst", "scsbs",
        "ifrs", "ind as", "us gaap", "indian gaap",
        # IPO / securities terminology (non-PII defined terms)
        "offer", "the offer", "the offer for sale", "offer for sale",
        "the offer price", "offer price", "the price band", "price band",
        "anchor investors", "anchor investor", "the anchor investors",
        "retail individual investors", "the retail individual investors",
        "non-institutional investors", "the non-institutional investors",
        "non-institutional portion", "the non-institutional portion",
        "qualified institutional buyers", "the qualified institutional buyers",
        "the net proceeds", "net proceeds",
        "the book running lead managers", "book running lead managers",
        "the syndicate", "syndicate",
        "the registrar of companies", "registrar of companies",
        "the stock exchanges", "stock exchanges",
        "allotment", "the allotment",
        "chartered accountants",
        # Document self-references
        "prospectus", "the prospectus",
        "red herring", "red herring prospectus", "this red herring prospectus",
        "the red herring prospectus", "draft red herring",
        "draft red herring prospectus", "the draft red herring prospectus",
        "makalu family trust",
        # Financial / legal generic terms
        "the restated financial statements", "restated financial statements",
        "life insurance companies and pension funds",
        "operations", "cin",
        # Common standalone labels
        "n.a.", "na", "nil", "not applicable",
    }

    # Additional prefixes: if ORG text starts with "the" and the remainder
    # matches these patterns, reject it
    _ORG_GENERIC_SUFFIX_PATTERNS: set[str] = {
        "company", "board", "registrar", "auditor", "committee",
        "management", "government", "promoter", "promoters",
        "offer", "syndicate", "allotment", "issuer",
        "tribunal", "court", "bench", "act",
        "promoter selling shareholders",
        "sebi icdr regulations", "sebi regulations",
        "listing regulations", "companies act",
    }

    # Legal suffixes that increase confidence for ORG detection
    _LEGAL_SUFFIXES = re.compile(
        r"\b(?:Ltd\.?|Limited|LLP|Pvt\.?\s*Ltd\.?|Private\s+Limited|Inc\.?|"
        r"Corp\.?|Corporation|Trust|Foundation|Associates|Partners|"
        r"Holdings|Group|Enterprises|Industries|Solutions|Services|"
        r"Technologies|Consultants|Co\.?)\b",
        re.IGNORECASE,
    )

    # Address-indicator terms: if a COMPANY_NAME candidate contains one of
    # these and does NOT have a legal suffix, it's likely part of an address.
    _ADDRESS_INDICATOR_TERMS = re.compile(
        r"\b(?:Road|Rd|Marg|Nagar|Colony|Chowk|Gali|Lane|Street|St\.?"
        r"|Industrial\s+Area|Business\s+Centre|Business\s+Center"
        r"|Business\s+Park|Farms|Farm|Estate|Enclave|Layout"
        r"|Sector|Block|Phase|Plot|Tower|Floor|Wing|MIDC"
        r"|Village|Taluka|Tehsil|Mandal|Birdewadi|Pallod)\b",
        re.IGNORECASE,
    )

    # Address-context pattern: preceded by house/plot numbers
    _ADDRESS_CONTEXT_PATTERN = re.compile(
        r"(?:^|,\s*)(?:\d+[/,\-]\s*|Plot\s+(?:No\.?\s*)?\d|Tower\s+\d|Floor\s+\d|\d+\s*,)",
        re.IGNORECASE,
    )

    # Patterns for address span expansion (Bug 4 fix)
    _ADDRESS_LABEL_PATTERN = re.compile(
        r"(?:Registered\s+Office|Corporate\s+Office|Address|Registered\s+office\s+of\s+the\s+Company)\s*:?\s*",
        re.IGNORECASE,
    )

    _HOUSE_NUMBER_PATTERN = re.compile(
        r"\b(?:\d+[-/]\d+|Plot\s+No\.?\s*\d+|H\.?No\.?\s*\d+|Block\s+No\.?\s*\d+|Flat\s+No\.?\s*\d+|Shop\s+No\.?\s*\d+|Office\s+No\.?\s*\d+|Unit\s+No\.?\s*\d+)\b",
        re.IGNORECASE,
    )

    # Role-label patterns for deterministic name detection (Bug 2 fix).
    # Captures 2–4 title-cased words immediately following a role label.
    # Name words can be terminated by whitespace, comma, semicolon, or EOL.
    _ROLE_LABEL_PATTERN = re.compile(
        r"(?:Contact\s+Person|Company\s+Secretary|Compliance\s+Officer"
        r"|Managing\s+Director|Chief\s+Financial\s+Officer"
        r"|Chief\s+Executive\s+Officer|Promoter|Director|Chairman"
        r"|Whole\s+Time\s+Director|Independent\s+Director)"
        r"\s*:?\s*"
        r"((?:[A-Z][a-z]+(?:\s+|(?=[,;.\)\]\s])|$)){2,4})",
        re.MULTILINE,
    )

    # Indian pincode pattern (6 digits, first digit 1-9)
    _PINCODE_PATTERN = re.compile(r"\b[1-9]\d{5}\b")

    def __init__(
        self,
        enabled_types: set[str] | None = None,
        confidence_threshold: float = 0.60,
    ) -> None:
        """Initialize the NER detector.

        Args:
            enabled_types: Set of entity type names to detect.
                          If None, all NER types are enabled.
            confidence_threshold: Minimum confidence to report a detection.
        """
        self._all_types = {"FULL_NAME", "COMPANY_NAME", "ADDRESS"}
        self._enabled_types = enabled_types or self._all_types
        self._confidence_threshold = confidence_threshold
        self._nlp = None  # Lazy load

    @property
    def name(self) -> str:
        return "NERDetector"

    @property
    def supported_entity_types(self) -> list[str]:
        return list(self._enabled_types)

    def _ensure_model(self) -> None:
        """Ensure spaCy model is loaded (lazy initialization)."""
        if self._nlp is None:
            self._nlp = _load_spacy_model()

    def detect(self, text: str) -> list[DetectedEntity]:
        """Detect named entities in text using spaCy NER.

        Args:
            text: Input text to analyze.

        Returns:
            List of detected entities after precision filtering.
        """
        self._ensure_model()
        if not text or not text.strip():
            return []

        # Limit text length for spaCy processing (very long texts can be slow)
        # Process in chunks if needed
        max_length = 100_000
        if len(text) > max_length:
            return self._detect_chunked(text, max_length)

        doc = self._nlp(text)
        entities: list[DetectedEntity] = []

        # Bug 4 Fix: Address Span Expansion
        # Pre-collect all location entities to expand them into full addresses
        if "ADDRESS" in self._enabled_types:
            raw_address_ents = [
                (ent.start_char, ent.end_char)
                for ent in doc.ents
                if self._map_spacy_label(ent.label_) == "ADDRESS"
            ]
            expanded_address_spans = self._expand_and_merge_addresses(text, raw_address_ents)
        else:
            expanded_address_spans = []

        for ent in doc.ents:
            mapped_type = self._map_spacy_label(ent.label_)
            if mapped_type is None or mapped_type not in self._enabled_types:
                continue

            # Skip address entities here, they are handled in batch below
            if mapped_type == "ADDRESS":
                continue

            # Apply type-specific precision filters
            filtered_entity = self._apply_precision_filters(
                ent.text, mapped_type, ent.start_char, ent.end_char, ent.label_
            )
            if filtered_entity:
                entities.append(filtered_entity)

        # Process the expanded/merged addresses
        for start, end, is_expanded in expanded_address_spans:
            span_text = text[start:end].strip()
            if not span_text:
                continue

            if is_expanded:
                # If we confidently expanded it via heuristics, bypass normal strict filtering
                # (which might reject it if it looks weird, but our heuristics are strong)
                entities.append(
                    DetectedEntity(
                        text=span_text,
                        entity_type="ADDRESS",
                        start=start,
                        end=end,
                        confidence=0.90,
                        detector_name=self.name,
                        validated=True,
                        metadata={"source": "expanded_address"},
                    )
                )
            else:
                # Fall back to normal precision filter for unexpanded raw locations
                filtered_entity = self._apply_precision_filters(
                    span_text, "ADDRESS", start, end, "LOC"
                )
                if filtered_entity:
                    entities.append(filtered_entity)

        # Supplemental: deterministic role-label name detection (Bug 2 fix)
        if "FULL_NAME" in self._enabled_types:
            role_names = self._detect_role_label_names(text)
            entities.extend(role_names)

        return entities

    def _detect_chunked(self, text: str, chunk_size: int) -> list[DetectedEntity]:
        """Process very long text in overlapping chunks."""
        entities: list[DetectedEntity] = []
        overlap = 200  # Character overlap between chunks

        for start in range(0, len(text), chunk_size - overlap):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end]
            doc = self._nlp(chunk)

            for ent in doc.ents:
                mapped_type = self._map_spacy_label(ent.label_)
                if mapped_type is None or mapped_type not in self._enabled_types:
                    continue

                # Adjust offsets to full text coordinates
                abs_start = start + ent.start_char
                abs_end = start + ent.end_char

                # Skip entities in overlap zone of non-first chunks
                if start > 0 and ent.start_char < overlap:
                    continue

                filtered_entity = self._apply_precision_filters(
                    ent.text, mapped_type, abs_start, abs_end, ent.label_
                )
                if filtered_entity:
                    entities.append(filtered_entity)

        return entities

    def _map_spacy_label(self, label: str) -> str | None:
        """Map spaCy NER labels to our entity types.

        Args:
            label: spaCy label string (e.g., "PERSON", "ORG", "GPE").

        Returns:
            Our entity type string, or None if not mapped.
        """
        mapping = {
            "PERSON": "FULL_NAME",
            "ORG": "COMPANY_NAME",
            "GPE": "ADDRESS",
            "LOC": "ADDRESS",
            "FAC": "ADDRESS",
        }
        return mapping.get(label)

    def _apply_precision_filters(
        self,
        text: str,
        entity_type: str,
        start: int,
        end: int,
        spacy_label: str,
    ) -> DetectedEntity | None:
        """Apply type-specific precision filters to reduce false positives.

        Returns a DetectedEntity if the entity passes all filters, else None.
        """
        if entity_type == "FULL_NAME":
            return self._filter_person(text, start, end)
        elif entity_type == "COMPANY_NAME":
            return self._filter_org(text, start, end)
        elif entity_type == "ADDRESS":
            return self._filter_address(text, start, end, spacy_label)
        return None

    def _filter_person(
        self, text: str, start: int, end: int
    ) -> DetectedEntity | None:
        """Precision filters for PERSON entities.

        Rejects:
        - Single-token entities shorter than 3 characters
        - All-caps text (likely abbreviations/acronyms, not names)
        - Entities that are purely numeric
        - Common non-name patterns
        """
        cleaned = text.strip()
        if not cleaned:
            return None

        # Reject very short names (likely false positives)
        if len(cleaned) < 3:
            logger.debug("Rejected PERSON (too short): '%s'", cleaned)
            return None

        # Reject all-caps (likely acronyms: "SEBI", "RBI", not names)
        # Exception: multi-word all-caps can still be names in legal docs
        if cleaned.isupper() and len(cleaned.split()) <= 2:
            logger.debug("Rejected PERSON (all-caps short): '%s'", cleaned)
            return None

        # Reject if contains digits (not a name)
        if any(c.isdigit() for c in cleaned):
            logger.debug("Rejected PERSON (contains digits): '%s'", cleaned)
            return None

        # Reject common generic words / document title patterns
        lower = cleaned.lower()
        generic_words = {
            "section", "clause", "article", "chapter", "schedule",
            "annexure", "appendix", "exhibit", "table", "figure",
            "act", "rule", "regulation", "provision", "part",
            # Document titles / headings that NER misclassifies
            "red herring", "red herring prospectus", "prospectus",
            "draft red herring prospectus",
            # Financial terms sometimes tagged as PERSON
            "fiscal", "fiscals", "offer",
            # Locale/language names
            "marathi", "hindi", "english", "gujarati",
        }
        if lower in generic_words:
            logger.debug("Rejected PERSON (generic word): '%s'", cleaned)
            return None

        # Reject if it looks like a defined legal term (starts with article +
        # multi-word capitalized phrase like "Promoter Selling Shareholders")
        if lower.startswith("the ") or lower.startswith("our "):
            logger.debug("Rejected PERSON (article-prefixed): '%s'", cleaned)
            return None

        # Reject location descriptors that spaCy misclassifies as names
        if "taluka" in lower or "khed" in lower or "district" in lower:
            logger.debug("Rejected PERSON (location descriptor): '%s'", cleaned)
            return None

        # Confidence based on token count (multi-word names are more reliable)
        tokens = cleaned.split()
        confidence = 0.75 if len(tokens) == 1 else 0.85 if len(tokens) == 2 else 0.90

        return DetectedEntity(
            text=cleaned,
            entity_type="FULL_NAME",
            start=start,
            end=end,
            confidence=confidence,
            detector_name=self.name,
            validated=False,
        )

    def _filter_org(
        self, text: str, start: int, end: int
    ) -> DetectedEntity | None:
        """Precision filters for ORG entities.

        Rejects:
        - Entries in the stoplist (generic references like "the Company")
        - Very short org names (< 2 chars)

        Boosts confidence for entities with legal suffixes (Ltd, LLP, etc.)
        """
        cleaned = text.strip()
        if not cleaned or len(cleaned) < 2:
            return None

        # Stoplist check (case-insensitive)
        lower = cleaned.lower().strip()
        if lower in self._ORG_STOPLIST:
            logger.debug("Rejected ORG (stoplist): '%s'", cleaned)
            return None

        # Check with article prefix stripped
        for prefix in ("the ", "our ", "said ", "such "):
            if lower.startswith(prefix):
                remaining = lower[len(prefix):]
                if remaining in self._ORG_STOPLIST:
                    logger.debug("Rejected ORG (stoplist with prefix): '%s'", cleaned)
                    return None
                if remaining in self._ORG_GENERIC_SUFFIX_PATTERNS:
                    logger.debug("Rejected ORG (generic suffix): '%s'", cleaned)
                    return None

        # Reject pure abbreviations/acronyms that aren't real company names
        # (e.g., "UPI", "ASBA", "BSE", "IFRS") — single all-caps token
        if cleaned.isupper() and len(cleaned.split()) <= 1 and len(cleaned) <= 6:
            logger.debug("Rejected ORG (abbreviation): '%s'", cleaned)
            return None

        # Reject entries that are clearly fiscal/date references
        if re.match(r'^Fiscals?\s+\d', cleaned):
            logger.debug("Rejected ORG (fiscal reference): '%s'", cleaned)
            return None

        # Confidence boost if it has a legal suffix
        has_legal_suffix = bool(self._LEGAL_SUFFIXES.search(cleaned))

        # Bug 3 fix: Reject address-indicator terms when no legal suffix
        # (e.g., "Montreal Business Centre", "Off Pallod Farms", "MG Road")
        if not has_legal_suffix and self._ADDRESS_INDICATOR_TERMS.search(cleaned):
            logger.debug("Rejected ORG (address indicator, no legal suffix): '%s'", cleaned)
            return None

        confidence = 0.92 if has_legal_suffix else 0.72

        return DetectedEntity(
            text=cleaned,
            entity_type="COMPANY_NAME",
            start=start,
            end=end,
            confidence=confidence,
            detector_name=self.name,
            validated=has_legal_suffix,
            metadata={"has_legal_suffix": has_legal_suffix},
        )

    def _filter_address(
        self, text: str, start: int, end: int, spacy_label: str
    ) -> DetectedEntity | None:
        """Precision filters for ADDRESS entities (GPE/LOC/FAC).

        Standalone GPE/LOC labels (e.g., "Mumbai", "India") are common
        and often not PII by themselves. We REJECT single location names
        (countries, states, cities) and only keep multi-word addresses
        or those containing pincodes.
        """
        cleaned = text.strip()
        if not cleaned or len(cleaned) < 2:
            return None

        # Reject standalone country/state/city names — they appear hundreds
        # of times in legal documents and are not PII in isolation
        lower = cleaned.lower()
        _geo_stoplist = {
            # Countries
            "india", "usa", "us", "uk", "united states", "united kingdom",
            "mumbai", "delhi", "bangalore", "bengaluru", "chennai",
            "hyderabad", "kolkata", "pune", "ahmedabad", "jaipur",
            "lucknow", "bhopal", "chandigarh", "patna", "noida",
            "gurgaon", "gurugram", "kochi", "coimbatore", "nagpur",
            "bombay",
            # Generic location references
            "registered office", "corporate office", "head office",
        }
        if lower in _geo_stoplist:
            logger.debug("Rejected ADDRESS (geo stoplist): '%s'", cleaned)
            return None

        # Reject fiscal year references misclassified as locations
        if re.match(r'^Fiscals?\s+\d', cleaned):
            logger.debug("Rejected ADDRESS (fiscal reference): '%s'", cleaned)
            return None

        # Reject if it has a company legal suffix (spaCy misclassified a company as a location)
        if self._LEGAL_SUFFIXES.search(cleaned):
            logger.debug("Rejected ADDRESS (has company legal suffix): '%s'", cleaned)
            return None

        tokens = cleaned.split()
        has_pincode = bool(self._PINCODE_PATTERN.search(cleaned))

        if has_pincode:
            confidence = 0.90
        elif len(tokens) >= 3:
            confidence = 0.80
        elif len(tokens) == 2:
            confidence = 0.70
        else:
            # Single-word location without pincode — skip it
            logger.debug("Rejected ADDRESS (single word, no pincode): '%s'", cleaned)
            return None

        return DetectedEntity(
            text=cleaned,
            entity_type="ADDRESS",
            start=start,
            end=end,
            confidence=confidence,
            detector_name=self.name,
            validated=has_pincode,
            metadata={"spacy_label": spacy_label, "has_pincode": has_pincode},
        )

    def _detect_role_label_names(
        self, text: str
    ) -> list[DetectedEntity]:
        """Deterministic fallback: detect names following role-label patterns.

        spaCy often misses names in dense legal contexts when they immediately
        follow role titles like 'Contact Person:', 'Company Secretary', etc.
        This regex-based fallback catches those cases.

        Overlap resolution downstream deduplicates if spaCy already found
        the same name.
        """
        entities: list[DetectedEntity] = []

        for match in self._ROLE_LABEL_PATTERN.finditer(text):
            name = match.group(1).strip()
            if not name or len(name) < 3:
                continue

            # Reject if it looks like a generic word, not a name
            lower = name.lower()
            skip_words = {
                "the", "our", "said", "such", "this", "that",
                "company secretary", "compliance officer",
                "managing director", "chief financial officer",
                "and compliance",
            }
            if lower in skip_words or lower.startswith("the "):
                continue

            # Must have at least 2 tokens (first + last name)
            tokens = name.split()
            if len(tokens) < 2:
                continue

            # All tokens should be title-cased
            if not all(t[0].isupper() for t in tokens if t):
                continue

            abs_start = match.start(1)
            abs_end = match.end(1)

            entities.append(
                DetectedEntity(
                    text=name,
                    entity_type="FULL_NAME",
                    start=abs_start,
                    end=abs_end,
                    confidence=0.90,
                    detector_name=self.name,
                    validated=True,
                    metadata={"source": "role_label_fallback"},
                )
            )
            logger.debug("Role-label fallback detected FULL_NAME: '%s'", name)

        return entities

    def _expand_and_merge_addresses(
        self, text: str, raw_address_ents: list[tuple[int, int]]
    ) -> list[tuple[int, int, bool]]:
        """Expand raw location spans into full addresses based on text cues.

        Returns a list of (start, end, is_expanded) tuples representing the
        merged and expanded addresses.
        """
        if not raw_address_ents:
            return []

        expanded_spans = []

        for start, end in raw_address_ents:
            window_start = max(0, start - 150)
            window_end = min(len(text), end + 150)

            new_start = start
            new_end = end
            is_expanded = False

            prefix = text[window_start:start]
            suffix = text[end:window_end]

            # 1. Expand backwards to address label and forwards to punctuation
            label_matches = list(self._ADDRESS_LABEL_PATTERN.finditer(prefix))
            if label_matches:
                last_match = label_matches[-1]
                new_start = window_start + last_match.end()
                is_expanded = True

                # Expand end to next sentence-ending punctuation
                match = re.search(r'[;.]+(?:\s|$)', suffix)
                if match:
                    new_end = end + match.start()

            # 2. Expand forwards to PIN code (if not already covered)
            pin_matches = list(self._PINCODE_PATTERN.finditer(suffix))
            if pin_matches:
                first_pin = pin_matches[0]
                pin_end = end + first_pin.end()
                if pin_end > new_end:
                    new_end = pin_end
                    is_expanded = True

                    # Capture state/country after pin (up to 30 chars, usually stops at punctuation)
                    after_pin = text[new_end:new_end + 30]
                    match = re.search(r'^[\s,]*[A-Za-z\s]+(?:,[\sA-Za-z\s]+)?', after_pin)
                    if match:
                        new_end += match.end()

            # 3. Expand backwards to House/Plot Number (if no label was found)
            if not is_expanded:
                hn_matches = list(self._HOUSE_NUMBER_PATTERN.finditer(prefix))
                if hn_matches:
                    last_match = hn_matches[-1]
                    # Check if space between is address-like (commas, spaces, numbers, 'and')
                    between = prefix[last_match.end():]
                    if re.match(r'^[\s,A-Za-z0-9\-&/]*$', between):
                        new_start = window_start + last_match.start()
                        is_expanded = True

            # 4. Trim leading non-address context words from the expanded span.
            # The expansion can grab prepositions / context ("at", "located at",
            # "of our company located at") that precede the actual address.
            # Walk new_start forward to the first house-number or digit.
            if is_expanded:
                span_text = text[new_start:new_end]
                trim_match = re.match(
                    r'^(?:(?:at|located|of|our|the|company|is|was|being)\s+)*',
                    span_text,
                    re.IGNORECASE,
                )
                if trim_match and trim_match.end() > 0:
                    # Only trim if what remains still starts with an address
                    # signal (digit, Plot, H.No, etc.) — don't trim into garbage
                    remaining = span_text[trim_match.end():]
                    if remaining and (remaining[0].isdigit() or re.match(r'^(?:Plot|H\.?No|Block|Flat|Shop|Office|Unit)\b', remaining, re.IGNORECASE)):
                        new_start += trim_match.end()

            expanded_spans.append((new_start, new_end, is_expanded))

        # Merge overlapping or adjacent spans to prevent double-counting
        expanded_spans.sort()
        merged = [expanded_spans[0]]

        for current_start, current_end, current_exp in expanded_spans[1:]:
            last_start, last_end, last_exp = merged[-1]

            # Merge if overlapping or separated by just a comma/space
            between = text[last_end:current_start]
            if current_start <= last_end or re.match(r'^[\s,;]*$', between):
                new_start = min(last_start, current_start)
                new_end = max(last_end, current_end)
                merged[-1] = (new_start, new_end, last_exp or current_exp)
            else:
                merged.append((current_start, current_end, current_exp))

        return merged
