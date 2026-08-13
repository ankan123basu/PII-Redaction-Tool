"""
Tests for regex-based PII detectors and validators.

Covers all 8 regex-detected PII types with positive and negative test cases.
Key precision tests:
- Credit card Luhn validation rejects non-CC 16-digit numbers
- DOB context-window guard rejects dates without DOB keywords
- IP detection rejects version-number false positives
- PAN/CIN format validation
"""

import pytest

from pii_redactor.detectors.regex_detectors import RegexDetector
from pii_redactor.validators.checksum import (
    luhn_check,
    validate_cin,
    validate_ipv4,
    validate_pan,
    validate_phone,
    validate_ssn,
)

# ===========================================================================
# Validator unit tests
# ===========================================================================


class TestLuhnCheck:
    """Tests for the Luhn checksum validator."""

    def test_valid_visa(self):
        assert luhn_check("4111111111111111") is True

    def test_valid_mastercard(self):
        assert luhn_check("5500000000000004") is True

    def test_valid_amex(self):
        assert luhn_check("378282246310005") is True

    def test_valid_with_spaces(self):
        assert luhn_check("4111 1111 1111 1111") is True

    def test_valid_with_dashes(self):
        assert luhn_check("4111-1111-1111-1111") is True

    def test_invalid_number(self):
        assert luhn_check("4111111111111112") is False

    def test_random_16_digits(self):
        # Most random 16-digit numbers should fail Luhn
        assert luhn_check("1234567890123456") is False

    def test_too_short(self):
        assert luhn_check("1") is False

    def test_non_digit(self):
        assert luhn_check("abcdef") is False

    def test_empty(self):
        assert luhn_check("") is False


class TestValidatePhone:
    """Tests for phone number validation."""

    def test_indian_10_digit(self):
        assert validate_phone("9876543210") is True

    def test_indian_with_country_code(self):
        assert validate_phone("+91 9876543210") is True

    def test_international(self):
        assert validate_phone("+1-555-123-4567") is True

    def test_too_short(self):
        assert validate_phone("12345") is False

    def test_too_long(self):
        assert validate_phone("1" * 20) is False


class TestValidateSSN:
    """Tests for SSN validation."""

    def test_valid_ssn(self):
        assert validate_ssn("123-45-6789") is True

    def test_area_000(self):
        assert validate_ssn("000-45-6789") is False

    def test_area_666(self):
        assert validate_ssn("666-45-6789") is False

    def test_area_900_plus(self):
        assert validate_ssn("900-45-6789") is False

    def test_group_00(self):
        assert validate_ssn("123-00-6789") is False

    def test_serial_0000(self):
        assert validate_ssn("123-45-0000") is False


class TestValidatePAN:
    """Tests for Indian PAN validation."""

    def test_valid_person_pan(self):
        assert validate_pan("ABCPD1234E") is True

    def test_valid_company_pan(self):
        assert validate_pan("AABCC1234D") is True

    def test_invalid_4th_char(self):
        # 4th char must be from ABCFGHLJPTK
        assert validate_pan("ABCXD1234E") is False

    def test_too_short(self):
        assert validate_pan("ABCPD123") is False

    def test_wrong_format(self):
        assert validate_pan("1234567890") is False


class TestValidateCIN:
    """Tests for Indian CIN validation."""

    def test_valid_cin_u(self):
        assert validate_cin("U28129PN1979PLC141032") is True

    def test_valid_cin_l(self):
        assert validate_cin("L67890MH2005PLC123456") is True

    def test_invalid_prefix(self):
        assert validate_cin("X28129PN1979PLC141032") is False

    def test_too_short(self):
        assert validate_cin("U28129PN1979PLC") is False


class TestValidateIPv4:
    """Tests for IPv4 validation."""

    def test_valid_private(self):
        assert validate_ipv4("192.168.1.100") is True

    def test_valid_public(self):
        assert validate_ipv4("8.8.8.8") is True

    def test_valid_localhost(self):
        assert validate_ipv4("127.0.0.1") is True

    def test_octet_out_of_range(self):
        assert validate_ipv4("256.1.1.1") is False

    def test_too_few_octets(self):
        assert validate_ipv4("192.168.1") is False

    def test_leading_zeros(self):
        # Leading zeros suggest non-IP usage
        assert validate_ipv4("01.02.03.04") is False


# ===========================================================================
# Regex detector tests
# ===========================================================================


class TestRegexDetectorEmail:
    """Tests for email detection."""

    @pytest.fixture
    def detector(self):
        return RegexDetector(enabled_types={"EMAIL"})

    def test_simple_email(self, detector):
        entities = detector.detect("Contact us at info@example.com for details.")
        assert len(entities) == 1
        assert entities[0].entity_type == "EMAIL"
        assert entities[0].text == "info@example.com"

    def test_multiple_emails(self, detector):
        text = "Email user@domain.com or admin@company.co.in for help."
        entities = detector.detect(text)
        assert len(entities) == 2

    def test_no_email(self, detector):
        entities = detector.detect("There are no email addresses here.")
        assert len(entities) == 0

    def test_email_with_dots_and_plus(self, detector):
        entities = detector.detect("Send to first.last+tag@domain.org")
        assert len(entities) == 1


class TestRegexDetectorPhone:
    """Tests for phone number detection."""

    @pytest.fixture
    def detector(self):
        return RegexDetector(enabled_types={"PHONE"})

    def test_indian_phone_with_code(self, detector):
        entities = detector.detect("Call +91 98765 43210 for details.")
        assert len(entities) >= 1
        phone_entities = [e for e in entities if e.entity_type == "PHONE"]
        assert len(phone_entities) >= 1

    def test_indian_phone_with_dash(self, detector):
        entities = detector.detect("Phone: +91-9876543210")
        assert len(entities) >= 1

    def test_no_phone(self, detector):
        entities = detector.detect("No phone numbers here. The year 2024 is not a phone.")
        assert len(entities) == 0


class TestRegexDetectorCreditCard:
    """Tests for credit card detection with Luhn validation."""

    @pytest.fixture
    def detector(self):
        return RegexDetector(enabled_types={"CREDIT_CARD"})

    def test_valid_visa(self, detector):
        entities = detector.detect("Card: 4111 1111 1111 1111")
        assert len(entities) == 1
        assert entities[0].entity_type == "CREDIT_CARD"
        assert entities[0].validated is True

    def test_invalid_luhn_rejected(self, detector):
        """16-digit numbers that fail Luhn should NOT be detected."""
        entities = detector.detect("Reference: 1234 5678 9012 3456")
        # This should be rejected by Luhn check
        credit_cards = [e for e in entities if e.entity_type == "CREDIT_CARD"]
        assert len(credit_cards) == 0

    def test_no_credit_card(self, detector):
        entities = detector.detect("This is just regular text.")
        assert len(entities) == 0


class TestRegexDetectorDOB:
    """Tests for DOB detection with context-window guard."""

    @pytest.fixture
    def detector(self):
        return RegexDetector(enabled_types={"DOB"})

    def test_dob_with_keyword(self, detector):
        entities = detector.detect("Date of Birth (DOB): 15/03/1985")
        assert len(entities) == 1
        assert entities[0].entity_type == "DOB"

    def test_dob_with_born_keyword(self, detector):
        entities = detector.detect("She was born on 22/07/1990 in Mumbai.")
        assert len(entities) == 1

    def test_date_without_context_rejected(self, detector):
        """Dates without DOB context keywords should NOT be detected."""
        entities = detector.detect(
            "The Company was incorporated on 15/01/2005. "
            "The filing date is 22/03/2024."
        )
        assert len(entities) == 0

    def test_date_with_distant_keyword_rejected(self, detector):
        """DOB keyword too far from date should not trigger detection."""
        # Create text where DOB keyword is > 80 chars from the date
        filler = "x" * 100
        text = f"DOB information follows: {filler} The date is 15/03/1985."
        entities = detector.detect(text)
        assert len(entities) == 0


class TestRegexDetectorIP:
    """Tests for IP address detection."""

    @pytest.fixture
    def detector(self):
        return RegexDetector(enabled_types={"IP_ADDRESS"})

    def test_valid_ipv4(self, detector):
        entities = detector.detect("Server at 192.168.1.100 is running.")
        assert len(entities) == 1
        assert entities[0].entity_type == "IP_ADDRESS"

    def test_version_number_rejected(self, detector):
        """Version numbers should NOT be detected as IPs."""
        entities = detector.detect("Running version 2.4.1.0 of the software.")
        ip_entities = [e for e in entities if e.entity_type == "IP_ADDRESS"]
        # version 2.4.1.0 might match regex but should be filtered
        # The prefix "version" should help reject it
        assert len(ip_entities) == 0


class TestRegexDetectorPAN:
    """Tests for Indian PAN detection."""

    @pytest.fixture
    def detector(self):
        return RegexDetector(enabled_types={"PAN"})

    def test_valid_pan(self, detector):
        entities = detector.detect("PAN: ABCPD1234E")
        assert len(entities) == 1
        assert entities[0].entity_type == "PAN"

    def test_invalid_pan_rejected(self, detector):
        entities = detector.detect("Code: XXXXX9999X")
        pan_entities = [e for e in entities if e.entity_type == "PAN"]
        # XXXXX - 4th char X is not valid for PAN
        assert len(pan_entities) == 0


class TestRegexDetectorCIN:
    """Tests for Indian CIN detection."""

    @pytest.fixture
    def detector(self):
        return RegexDetector(enabled_types={"CIN"})

    def test_valid_cin(self, detector):
        entities = detector.detect("CIN: U28129PN1979PLC141032")
        assert len(entities) == 1
        assert entities[0].entity_type == "CIN"

    def test_no_cin(self, detector):
        entities = detector.detect("This text has no CIN numbers.")
        assert len(entities) == 0


class TestRegexDetectorSSN:
    """Tests for SSN detection."""

    @pytest.fixture
    def detector(self):
        return RegexDetector(enabled_types={"SSN"})

    def test_valid_ssn(self, detector):
        entities = detector.detect("SSN: 123-45-6789")
        assert len(entities) == 1
        assert entities[0].entity_type == "SSN"

    def test_invalid_area_rejected(self, detector):
        entities = detector.detect("Number: 000-45-6789")
        ssn_entities = [e for e in entities if e.validated]
        assert len(ssn_entities) == 0


class TestRegexDetectorAllTypes:
    """Integration tests running all regex types together."""

    @pytest.fixture
    def detector(self):
        return RegexDetector()

    def test_mixed_text(self, detector):
        text = (
            "Contact kushal@example.com or call +91 98765 43210. "
            "PAN: ABCPD1234E. CIN: U28129PN1979PLC141032. "
            "DOB: 15/03/1985. Card: 4111 1111 1111 1111."
        )
        entities = detector.detect(text)
        types_found = {e.entity_type for e in entities}
        assert "EMAIL" in types_found
        assert "PAN" in types_found
        assert "CIN" in types_found
        assert "DOB" in types_found
        assert "CREDIT_CARD" in types_found
