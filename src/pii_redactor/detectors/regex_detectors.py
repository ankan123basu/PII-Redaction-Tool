"""
Regex-based PII detectors for structured data types.

Covers: email, phone, SSN, credit card, IP address, DOB, PAN, CIN.

Design: Each entity type is a separate method that returns DetectedEntity objects.
The main detect() method runs all enabled regex patterns and aggregates results.
Validators (Luhn, SSN range check, etc.) are applied inline to filter false positives.

DOB detection uses a context-window guard: dates are only flagged if they appear
near keywords like "DOB", "date of birth", "born" — this prevents redacting the
hundreds of unrelated dates in a legal/financial document.
"""

from __future__ import annotations

import re
import logging
from typing import Callable

from pii_redactor.detectors.base import Detector, DetectedEntity
from pii_redactor.validators.checksum import (
    luhn_check,
    validate_phone,
    validate_ssn,
    validate_pan,
    validate_cin,
    validate_ipv4,
)

logger = logging.getLogger(__name__)


class RegexDetector(Detector):
    """Detector that uses regular expressions and optional validators
    to find structured PII types in text.

    Each PII type has its own detection method, making it easy to
    extend with new types or adjust patterns independently.
    """

    # --- Class-level compiled patterns ---

    # Email: RFC-5322-lite pattern
    _EMAIL_PATTERN = re.compile(
        r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"
    )

    # Phone: Indian (+91) and international formats
    _PHONE_PATTERNS = [
        # Phone numbers (international and Indian formats)
        # Supports +91, 0, spaces, hyphens, parentheses
        re.compile(
            r"(?<!\d)(?:(?:\+|00)\s*91[\s\-]*)?(?:\d[\s\-]*){9}\d(?!\d)"
        ),
        # International with country code: +1-XXX-XXX-XXXX etc.
        re.compile(
            r"(?<!\d)\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{1,4}[\s\-]?\d{1,9}(?!\d)"
        ),
        # Generic 10-digit with separators: (XXX) XXX-XXXX or XXX-XXX-XXXX
        re.compile(
            r"(?<!\d)\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}(?!\d)"
        ),
    ]

    # SSN: XXX-XX-XXXX (with optional spaces instead of dashes)
    _SSN_PATTERN = re.compile(
        r"(?<!\d)\d{3}[\-\s]\d{2}[\-\s]\d{4}(?!\d)"
    )

    # Credit Card: 13-19 digit patterns with optional separators
    _CREDIT_CARD_PATTERNS = [
        # Visa: starts with 4, 13 or 16 digits
        re.compile(r"(?<!\d)4\d{3}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,4}(?!\d)"),
        # Mastercard: starts with 5[1-5] or 2[2-7], 16 digits
        re.compile(r"(?<!\d)(?:5[1-5]\d{2}|2[2-7]\d{2})[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)"),
        # Amex: starts with 3[47], 15 digits
        re.compile(r"(?<!\d)3[47]\d{2}[\s\-]?\d{6}[\s\-]?\d{5}(?!\d)"),
        # Generic 16-digit (catch-all, Luhn-validated downstream)
        re.compile(r"(?<!\d)\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)"),
    ]

    # IPv4
    _IPV4_PATTERN = re.compile(
        r"(?<![.\d])"  # negative lookbehind: not preceded by digit or dot
        r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
        r"(?![.\d])"  # negative lookahead: not followed by digit or dot
    )

    # IPv6 (simplified — full and compressed forms)
    _IPV6_PATTERN = re.compile(
        r"(?<![:\w])"
        r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"  # Full
        r"|(?:[0-9a-fA-F]{1,4}:){1,7}:"  # Compressed trailing
        r"|::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}"  # Compressed leading
        r"(?![:\w])"
    )

    # DOB: Multiple date formats
    _DATE_PATTERNS = [
        # DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
        re.compile(r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[/\-\.](?:0?[1-9]|1[0-2])[/\-\.]\d{4}(?!\d)"),
        # YYYY-MM-DD (ISO format)
        re.compile(r"(?<!\d)\d{4}[/\-\.](?:0?[1-9]|1[0-2])[/\-\.](?:0?[1-9]|[12]\d|3[01])(?!\d)"),
        # Month DD, YYYY (e.g., "January 15, 2000")
        re.compile(
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},?\s+\d{4}",
            re.IGNORECASE,
        ),
        # DD Month YYYY (e.g., "15 January 2000")
        re.compile(
            r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            r",?\s+\d{4}",
            re.IGNORECASE,
        ),
    ]

    # DOB context keywords (within a window around the date)
    _DOB_CONTEXT_KEYWORDS = re.compile(
        r"\b(?:d\.?o\.?b\.?|date\s+of\s+birth|born|birth\s*date|birthdate|age)\b",
        re.IGNORECASE,
    )

    # Context window size in characters for DOB detection
    _DOB_CONTEXT_WINDOW = 80

    # Indian PAN: AAAAA9999A
    _PAN_PATTERN = re.compile(
        r"(?<![A-Z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Z0-9])"
    )

    # Indian CIN: U/L + 5 digits + 2 letters + 4 digits + 3 letters + 6 digits
    _CIN_PATTERN = re.compile(
        r"(?<![A-Z0-9])[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}(?![A-Z0-9])"
    )

    def __init__(self, enabled_types: set[str] | None = None) -> None:
        """Initialize the regex detector.

        Args:
            enabled_types: Set of entity type names to enable.
                          If None, all types are enabled.
        """
        self._all_types = {
            "EMAIL", "PHONE", "SSN", "CREDIT_CARD", "IP_ADDRESS",
            "DOB", "PAN", "CIN",
        }
        self._enabled_types = enabled_types or self._all_types

        # Map entity types to their detection methods
        self._detectors: dict[str, Callable[[str], list[DetectedEntity]]] = {
            "EMAIL": self._detect_emails,
            "PHONE": self._detect_phones,
            "SSN": self._detect_ssns,
            "CREDIT_CARD": self._detect_credit_cards,
            "IP_ADDRESS": self._detect_ip_addresses,
            "DOB": self._detect_dobs,
            "PAN": self._detect_pans,
            "CIN": self._detect_cins,
        }

    @property
    def name(self) -> str:
        return "RegexDetector"

    @property
    def supported_entity_types(self) -> list[str]:
        return list(self._enabled_types)

    def detect(self, text: str) -> list[DetectedEntity]:
        """Run all enabled regex detectors on the input text.

        Args:
            text: Input text to scan.

        Returns:
            List of detected entities (may contain overlaps — resolved downstream).
        """
        entities: list[DetectedEntity] = []
        for entity_type in self._enabled_types:
            detector_fn = self._detectors.get(entity_type)
            if detector_fn:
                try:
                    found = detector_fn(text)
                    entities.extend(found)
                except Exception:
                    logger.exception("Error in %s detector for type %s", self.name, entity_type)
        return entities

    # --- Individual type detectors ---

    def _detect_emails(self, text: str) -> list[DetectedEntity]:
        """Detect email addresses using RFC-5322-lite regex."""
        entities = []
        for match in self._EMAIL_PATTERN.finditer(text):
            entities.append(
                DetectedEntity(
                    text=match.group(),
                    entity_type="EMAIL",
                    start=match.start(),
                    end=match.end(),
                    confidence=0.98,
                    detector_name=self.name,
                    validated=True,  # Regex match is sufficient for email
                )
            )
        return entities

    def _detect_phones(self, text: str) -> list[DetectedEntity]:
        """Detect phone numbers (Indian and international formats).

        Applies digit-count validation to filter false positives.
        """
        entities = []
        seen_spans: set[tuple[int, int]] = set()

        for pattern in self._PHONE_PATTERNS:
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                if span in seen_spans:
                    continue

                matched_text = match.group().strip()
                is_valid = validate_phone(matched_text)

                if is_valid:
                    seen_spans.add(span)
                    entities.append(
                        DetectedEntity(
                            text=matched_text,
                            entity_type="PHONE",
                            start=match.start(),
                            end=match.end(),
                            confidence=0.90 if is_valid else 0.60,
                            detector_name=self.name,
                            validated=is_valid,
                        )
                    )
        return entities

    def _detect_ssns(self, text: str) -> list[DetectedEntity]:
        """Detect US Social Security Numbers with range validation."""
        entities = []
        for match in self._SSN_PATTERN.finditer(text):
            matched_text = match.group()
            is_valid = validate_ssn(matched_text)
            entities.append(
                DetectedEntity(
                    text=matched_text,
                    entity_type="SSN",
                    start=match.start(),
                    end=match.end(),
                    confidence=0.95 if is_valid else 0.50,
                    detector_name=self.name,
                    validated=is_valid,
                )
            )
        return entities

    def _detect_credit_cards(self, text: str) -> list[DetectedEntity]:
        """Detect credit card numbers with Luhn checksum validation.

        This is the single biggest precision win: regex-only CC detection
        produces many false positives on 16-digit numbers. Luhn validation
        eliminates nearly all of them.
        """
        entities = []
        seen_spans: set[tuple[int, int]] = set()

        for pattern in self._CREDIT_CARD_PATTERNS:
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                if span in seen_spans:
                    continue

                matched_text = match.group()
                is_valid = luhn_check(matched_text)

                if is_valid:
                    seen_spans.add(span)
                    entities.append(
                        DetectedEntity(
                            text=matched_text,
                            entity_type="CREDIT_CARD",
                            start=match.start(),
                            end=match.end(),
                            confidence=0.97,
                            detector_name=self.name,
                            validated=True,
                            metadata={"luhn_valid": True},
                        )
                    )
                else:
                    logger.debug(
                        "Rejected CC candidate (Luhn fail): %s",
                        matched_text[:4] + "****",
                    )
        return entities

    def _detect_ip_addresses(self, text: str) -> list[DetectedEntity]:
        """Detect IPv4 and IPv6 addresses.

        Applies octet range validation for IPv4 and excludes
        version-number false positives (e.g., "v2018.1.0.0").
        """
        entities = []

        # IPv4
        for match in self._IPV4_PATTERN.finditer(text):
            matched_text = match.group()
            is_valid = validate_ipv4(matched_text)

            # Extra FP guard: check if preceded by 'v' or 'version' (version numbers)
            prefix_start = max(0, match.start() - 10)
            prefix = text[prefix_start:match.start()].lower().strip()
            if prefix.endswith(("v", "version", "ver", "v.")):
                logger.debug("Rejected IP candidate (version prefix): %s", matched_text)
                continue

            if is_valid:
                entities.append(
                    DetectedEntity(
                        text=matched_text,
                        entity_type="IP_ADDRESS",
                        start=match.start(),
                        end=match.end(),
                        confidence=0.90,
                        detector_name=self.name,
                        validated=True,
                    )
                )

        # IPv6
        for match in self._IPV6_PATTERN.finditer(text):
            entities.append(
                DetectedEntity(
                    text=match.group(),
                    entity_type="IP_ADDRESS",
                    start=match.start(),
                    end=match.end(),
                    confidence=0.85,
                    detector_name=self.name,
                    validated=True,
                )
            )

        return entities

    def _detect_dobs(self, text: str) -> list[DetectedEntity]:
        """Detect dates of birth using multi-format date regex + context window.

        CRITICAL PRECISION GUARD: A legal/financial document contains hundreds
        of dates (incorporation dates, filing dates, regulation dates, etc.).
        Redacting all dates would tank precision. We require a DOB-related
        keyword ("DOB", "date of birth", "born", "birthdate") within
        _DOB_CONTEXT_WINDOW characters of the date to classify it as a DOB.

        This is documented as a design tradeoff in the README: we may miss
        DOBs that appear without context keywords (recall cost), but the
        precision gain is significant.
        """
        entities = []
        seen_spans: set[tuple[int, int]] = set()

        for pattern in self._DATE_PATTERNS:
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                if span in seen_spans:
                    continue

                # Context window check
                window_start = max(0, match.start() - self._DOB_CONTEXT_WINDOW)
                window_end = min(len(text), match.end() + self._DOB_CONTEXT_WINDOW)
                context = text[window_start:window_end]

                if self._DOB_CONTEXT_KEYWORDS.search(context):
                    seen_spans.add(span)
                    entities.append(
                        DetectedEntity(
                            text=match.group(),
                            entity_type="DOB",
                            start=match.start(),
                            end=match.end(),
                            confidence=0.85,
                            detector_name=self.name,
                            validated=True,
                            metadata={"context_keyword_found": True},
                        )
                    )
                else:
                    logger.debug(
                        "Rejected date as non-DOB (no context keyword): %s",
                        match.group(),
                    )

        return entities

    def _detect_pans(self, text: str) -> list[DetectedEntity]:
        """Detect Indian PAN (Permanent Account Number).

        Format: [A-Z]{5}[0-9]{4}[A-Z]
        Validated by checking the 4th character entity-type indicator.
        """
        entities = []
        for match in self._PAN_PATTERN.finditer(text):
            matched_text = match.group()
            is_valid = validate_pan(matched_text)
            if is_valid:
                entities.append(
                    DetectedEntity(
                        text=matched_text,
                        entity_type="PAN",
                        start=match.start(),
                        end=match.end(),
                        confidence=0.92,
                        detector_name=self.name,
                        validated=True,
                    )
                )
        return entities

    def _detect_cins(self, text: str) -> list[DetectedEntity]:
        """Detect Indian CIN (Corporate Identity Number).

        Design choice: CINs are included as PII-adjacent data. They are publicly
        registered with the MCA, so they are *not* secret. However, they uniquely
        identify a company and could be used for entity resolution. We include
        CIN detection as ENABLED by default but document this as a deliberate
        policy choice in the README. Users can disable it in entity_rules.yaml.
        """
        entities = []
        for match in self._CIN_PATTERN.finditer(text):
            matched_text = match.group()
            is_valid = validate_cin(matched_text)
            if is_valid:
                entities.append(
                    DetectedEntity(
                        text=matched_text,
                        entity_type="CIN",
                        start=match.start(),
                        end=match.end(),
                        confidence=0.95,
                        detector_name=self.name,
                        validated=True,
                        metadata={
                            "policy_note": (
                                "CIN is publicly registered but company-identifying. "
                                "Included as PII-adjacent by default; disable in config if not needed."
                            )
                        },
                    )
                )
        return entities
