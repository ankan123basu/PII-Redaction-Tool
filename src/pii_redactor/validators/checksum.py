"""
Checksum and sanity-check validators for structured PII.

These validators are applied *after* regex matching to filter out
false positives. For example, a 16-digit number that looks like a
credit card but fails the Luhn checksum is almost certainly not one.

Each validator is a pure function: str -> bool.
"""

from __future__ import annotations

import re


def luhn_check(number: str) -> bool:
    """Validate a number string using the Luhn (mod-10) algorithm.

    Used primarily for credit card number validation.
    Strips spaces and hyphens before checking.

    Args:
        number: A string of digits (may contain spaces/hyphens).

    Returns:
        True if the number passes the Luhn checksum.
    """
    cleaned = re.sub(r"[\s\-]", "", number)
    if not cleaned.isdigit() or len(cleaned) < 2:
        return False

    digits = [int(d) for d in cleaned]
    # Double every second digit from the right
    for i in range(len(digits) - 2, -1, -2):
        doubled = digits[i] * 2
        digits[i] = doubled if doubled <= 9 else doubled - 9

    return sum(digits) % 10 == 0


def validate_phone(number: str) -> bool:
    """Basic sanity check for phone numbers.

    Validates that after stripping formatting, the number has
    a plausible digit count (7-15 digits per ITU-T E.164).

    Args:
        number: Raw phone number string including any formatting.

    Returns:
        True if the digit count is within a valid range.
    """
    digits_only = re.sub(r"\D", "", number)
    # E.164: min 7 digits (some short national numbers), max 15
    if len(digits_only) < 7 or len(digits_only) > 15:
        return False

    # Indian numbers: should be 10 digits (local) or 12 with country code
    if digits_only.startswith("91") and len(digits_only) not in (12, 10):
        # Allow 10 (without code) or 12 (with 91 prefix)
        pass  # Still valid for international formats

    return True


def validate_ssn(ssn: str) -> bool:
    """Validate a US Social Security Number format.

    Rejects invalid area numbers (000, 666, 900-999) and
    all-zero groups per SSA rules.

    Args:
        ssn: SSN string in XXX-XX-XXXX format.

    Returns:
        True if the SSN passes format and range checks.
    """
    cleaned = ssn.replace("-", "").replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != 9:
        return False

    area = int(cleaned[:3])
    group = int(cleaned[3:5])
    serial = int(cleaned[5:])

    # Invalid area numbers
    if area == 0 or area == 666 or area >= 900:
        return False
    # All-zero groups are invalid
    return not (group == 0 or serial == 0)


def validate_pan(pan: str) -> bool:
    """Validate an Indian PAN (Permanent Account Number).

    Format: [A-Z]{5}[0-9]{4}[A-Z]
    The 4th character indicates entity type:
      P=Person, C=Company, H=HUF, F=Firm, A=AOP, T=Trust, etc.

    Args:
        pan: PAN string (10 characters).

    Returns:
        True if the PAN matches the valid format.
    """
    if len(pan) != 10:
        return False

    pattern = r"^[A-Z]{3}[ABCFGHLJPTK][A-Z]\d{4}[A-Z]$"
    return bool(re.match(pattern, pan))


def validate_cin(cin: str) -> bool:
    r"""Validate an Indian CIN (Corporate Identity Number).

    Format: [U/L]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}
    Total length: 21 characters.

    Args:
        cin: CIN string.

    Returns:
        True if the CIN matches the valid format.
    """
    if len(cin) != 21:
        return False

    pattern = r"^[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$"
    return bool(re.match(pattern, cin))


def validate_ipv4(ip: str) -> bool:
    """Validate an IPv4 address (each octet 0-255).

    Also rejects common false positives like version numbers
    (e.g., "2018.1.0.0" — checks for 4-digit first octet).

    Args:
        ip: IPv4 address string (e.g., "192.168.1.1").

    Returns:
        True if valid IPv4 with octets in range.
    """
    parts = ip.split(".")
    if len(parts) != 4:
        return False

    for part in parts:
        if not part.isdigit():
            return False
        val = int(part)
        if val < 0 or val > 255:
            return False
        # Reject leading zeros (e.g., "01.02.03.04") as likely non-IP
        if len(part) > 1 and part[0] == "0":
            return False

    # Reject if first octet is > 3 digits (version numbers like 2018.x.x.x)
    return not (len(parts[0]) > 3)
