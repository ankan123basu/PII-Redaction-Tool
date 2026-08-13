"""
Format-preserving fake value generation using Faker.

Generates realistic replacement values that match the FORMAT of the original
PII. For example:
  - Phone "+91 98765 43210" → "+91 12345 67890" (same prefix, grouping)
  - Email "user@company.com" → "jane.doe@example.com"
  - Name "Mr. Kushal Hegde" → "Mr. John Smith" (preserves honorific)
  - Date "15/03/1990" → "22/07/1985" (same format)
  - Credit card "4111 1111 1111 1111" → "4532 0123 4567 8901" (Luhn-valid)
  - Address → plausible Indian address

The Faker instance is seeded for reproducibility (default seed=42).
"""

from __future__ import annotations

import logging
import random
import re
import string

from faker import Faker

logger = logging.getLogger(__name__)


class FakerProvider:
    """Format-preserving fake value generator backed by Faker.

    All generation methods take the original value and return a fake
    replacement in a matching format. The Faker instance is seeded
    for reproducibility.

    Usage:
        provider = FakerProvider(seed=42)
        fake_name = provider.generate("Kushal Subbayya Hegde", "FULL_NAME")
    """

    def __init__(self, seed: int = 42, locale: str = "en_IN") -> None:
        """Initialize with a Faker instance seeded for reproducibility.

        Args:
            seed: Random seed for deterministic fake generation.
            locale: Faker locale (en_IN for Indian data, en_US for US).
        """
        self._faker = Faker([locale, "en_US"])
        Faker.seed(seed)
        random.seed(seed)
        self._rng = random.Random(seed)

        # Map entity types to generator methods
        self._generators = {
            "FULL_NAME": self._fake_name,
            "EMAIL": self._fake_email,
            "PHONE": self._fake_phone,
            "COMPANY_NAME": self._fake_company,
            "ADDRESS": self._fake_address,
            "SSN": self._fake_ssn,
            "CREDIT_CARD": self._fake_credit_card,
            "DOB": self._fake_dob,
            "IP_ADDRESS": self._fake_ip,
            "PAN": self._fake_pan,
            "CIN": self._fake_cin,
        }

    def generate(self, original: str, entity_type: str) -> str:
        """Generate a format-preserving fake value.

        Args:
            original: The original PII text.
            entity_type: The PII type key.

        Returns:
            A fake value matching the original's format.
        """
        generator = self._generators.get(entity_type)
        if generator is None:
            logger.warning("No generator for entity type '%s', using generic", entity_type)
            return f"[REDACTED-{entity_type}]"
        try:
            return generator(original)
        except Exception:
            logger.exception("Error generating fake for %s", entity_type)
            return f"[REDACTED-{entity_type}]"

    def _fake_name(self, original: str) -> str:
        """Generate a fake name, preserving honorifics and word count.

        Examples:
            "Mr. Kushal Subbayya Hegde" → "Mr. John Michael Smith"
            "Rashi Patil" → "Jane Doe"
        """
        # Extract honorific if present
        honorific = ""
        name_part = original
        honorific_pattern = re.match(
            r"^(Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Prof\.?|Shri|Smt\.?)\s+",
            original,
            re.IGNORECASE,
        )
        if honorific_pattern:
            honorific = honorific_pattern.group(0)
            name_part = original[len(honorific):]

        # Match word count
        tokens = name_part.strip().split()
        if len(tokens) <= 1:
            fake = self._faker.last_name()
        elif len(tokens) == 2:
            fake = f"{self._faker.first_name()} {self._faker.last_name()}"
        else:
            # Multi-part name: first + middle + last
            parts = [self._faker.first_name()]
            for _ in range(len(tokens) - 2):
                parts.append(self._faker.first_name())
            parts.append(self._faker.last_name())
            fake = " ".join(parts)

        return f"{honorific}{fake}"

    def _fake_email(self, original: str) -> str:
        """Generate a fake email address.

        Example: "user@company.com" → "jane.doe@example.com"
        """
        return self._faker.email()

    def _fake_phone(self, original: str) -> str:
        """Generate a fake phone number preserving format.

        Examples:
            "+91 98765 43210" → "+91 12345 67890"
            "+91-9876543210"  → "+91-1234567890"
            "(555) 123-4567"  → "(555) 987-6543"
        """
        # Extract country code prefix if present
        cc_match = re.match(r"^(\+\d{1,3})([\s\-]?)", original)

        if cc_match:
            country_code = cc_match.group(1)
            separator_after_cc = cc_match.group(2)
            remaining = original[cc_match.end():]
        else:
            country_code = ""
            separator_after_cc = ""
            remaining = original

        # Replace digits while preserving non-digit characters (separators, parens)
        fake_remaining = ""
        for char in remaining:
            if char.isdigit():
                fake_remaining += str(self._rng.randint(0, 9))
            else:
                fake_remaining += char

        return f"{country_code}{separator_after_cc}{fake_remaining}"

    def _fake_company(self, original: str) -> str:
        """Generate a fake company name, preserving legal suffix.

        Examples:
            "Acme Corp. Ltd." → "Nexus Industries Ltd."
            "TechStart LLP" → "Bright Solutions LLP"
        """
        # Extract legal suffix
        suffix_match = re.search(
            r"\b(Ltd\.?|Limited|LLP|Pvt\.?\s*Ltd\.?|Private\s+Limited|Inc\.?|"
            r"Corp\.?|Corporation|Trust|Foundation)\s*$",
            original,
            re.IGNORECASE,
        )

        if suffix_match:
            suffix = suffix_match.group(0)
            fake_base = self._faker.company().split()[0]  # Take first word
            return f"{fake_base} {suffix}"
        else:
            return self._faker.company()

    def _fake_address(self, original: str) -> str:
        """Generate a plausible fake address.

        Tries to match the general structure (single-line vs multi-line).
        """
        if "\n" in original:
            # Multi-line address
            return self._faker.address().replace(", ", "\n")
        return self._faker.address().replace("\n", ", ")

    def _fake_ssn(self, original: str) -> str:
        """Generate a fake SSN in the same format.

        Example: "123-45-6789" → "456-78-9012"
        """
        area = self._rng.randint(100, 899)
        while area == 666:
            area = self._rng.randint(100, 899)
        group = self._rng.randint(1, 99)
        serial = self._rng.randint(1, 9999)

        # Preserve separator style
        if "-" in original:
            return f"{area:03d}-{group:02d}-{serial:04d}"
        elif " " in original:
            return f"{area:03d} {group:02d} {serial:04d}"
        return f"{area:03d}-{group:02d}-{serial:04d}"

    def _fake_credit_card(self, original: str) -> str:
        """Generate a Luhn-valid fake credit card number.

        Preserves the digit grouping/separator format of the original.
        """
        # Generate a valid CC number
        fake_cc = self._faker.credit_card_number()

        # Match format: spaces, dashes, or no separators
        if " " in original:
            # Group by 4s with spaces
            return " ".join(fake_cc[i:i+4] for i in range(0, len(fake_cc), 4))
        elif "-" in original:
            return "-".join(fake_cc[i:i+4] for i in range(0, len(fake_cc), 4))
        return fake_cc

    def _fake_dob(self, original: str) -> str:
        """Generate a fake date of birth in the same format.

        Detects the format of the original and generates a matching date.
        """
        fake_date = self._faker.date_of_birth(minimum_age=18, maximum_age=80)

        # Detect format
        if re.match(r"\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}", original):
            # YYYY-MM-DD
            sep = original[4]
            return fake_date.strftime(f"%Y{sep}%m{sep}%d")
        elif re.match(r"\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4}", original):
            # DD/MM/YYYY
            sep = re.search(r"[/\-\.]", original).group()
            return fake_date.strftime(f"%d{sep}%m{sep}%Y")
        elif re.match(r"[A-Z][a-z]+\s+\d", original):
            # Month DD, YYYY
            return fake_date.strftime("%B %d, %Y")
        elif re.match(r"\d{1,2}\s+[A-Z][a-z]+", original):
            # DD Month YYYY
            return fake_date.strftime("%d %B %Y")
        else:
            return fake_date.strftime("%d/%m/%Y")

    def _fake_ip(self, original: str) -> str:
        """Generate a fake IP address (v4 or v6 matching original).

        Example: "192.168.1.1" → "10.45.23.167"
        """
        if ":" in original:
            return self._faker.ipv6()
        return self._faker.ipv4_private()

    def _fake_pan(self, original: str) -> str:
        """Generate a fake Indian PAN number in valid format.

        Format: [A-Z]{3}[ABCFGHLJPTK][A-Z][0-9]{4}[A-Z]
        Preserves the entity-type indicator (4th char) from original.
        """
        fourth_char = original[3] if len(original) > 3 and original[3].isalpha() else "P"
        first_three = "".join(self._rng.choices(string.ascii_uppercase, k=3))
        fifth = self._rng.choice(string.ascii_uppercase)
        digits = "".join(str(self._rng.randint(0, 9)) for _ in range(4))
        last = self._rng.choice(string.ascii_uppercase)
        return f"{first_three}{fourth_char}{fifth}{digits}{last}"

    def _fake_cin(self, original: str) -> str:
        r"""Generate a fake Indian CIN in valid format.

        Format: [U/L]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}
        """
        prefix = self._rng.choice("UL")
        d5 = "".join(str(self._rng.randint(0, 9)) for _ in range(5))
        a2 = "".join(self._rng.choices(string.ascii_uppercase, k=2))
        d4 = "".join(str(self._rng.randint(0, 9)) for _ in range(4))
        a3 = "".join(self._rng.choices(string.ascii_uppercase, k=3))
        d6 = "".join(str(self._rng.randint(0, 9)) for _ in range(6))
        return f"{prefix}{d5}{a2}{d4}{a3}{d6}"
