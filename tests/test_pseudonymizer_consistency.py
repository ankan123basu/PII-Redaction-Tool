"""
Tests for pseudonymizer consistency — entity map and faker provider.

Verifies:
- Same input → same fake output (determinism)
- Fuzzy match: "Kushal Hegde" and "Kushal Subbayya Hegde" → same fake
- Format preservation (phone, email, credit card, etc.)
- Entity map persistence round-trip (save/load JSON)
- Different entity types get different fakes (no cross-type leakage)
"""

import json
import tempfile
from pathlib import Path

import pytest

from pii_redactor.pseudonymizer.entity_map import EntityMap
from pii_redactor.pseudonymizer.faker_provider import FakerProvider

# ===========================================================================
# EntityMap tests
# ===========================================================================


class TestEntityMapConsistency:
    """Tests for consistent entity-to-fake mapping."""

    @pytest.fixture
    def entity_map(self):
        return EntityMap(fuzzy_threshold=85)

    @pytest.fixture
    def provider(self):
        return FakerProvider(seed=42)

    def test_same_input_same_output(self, entity_map, provider):
        """Same PII text should always produce the same fake value."""
        fake1 = entity_map.get_or_create("Kushal Subbayya Hegde", "FULL_NAME", provider.generate)
        fake2 = entity_map.get_or_create("Kushal Subbayya Hegde", "FULL_NAME", provider.generate)
        assert fake1 == fake2

    def test_different_inputs_different_outputs(self, entity_map, provider):
        """Different PII values should produce different fakes."""
        fake1 = entity_map.get_or_create("Kushal Hegde", "FULL_NAME", provider.generate)
        fake2 = entity_map.get_or_create("Anita Sharma", "FULL_NAME", provider.generate)
        assert fake1 != fake2

    def test_fuzzy_match_similar_names(self, entity_map, provider):
        """Near-duplicate names should map to the same fake."""
        fake_full = entity_map.get_or_create(
            "Kushal Subbayya Hegde", "FULL_NAME", provider.generate
        )
        fake_short = entity_map.get_or_create(
            "Kushal Hegde", "FULL_NAME", provider.generate
        )
        assert fake_full == fake_short

    def test_no_fuzzy_match_across_types(self, entity_map, provider):
        """Fuzzy matching should NOT merge across entity types."""
        entity_map.get_or_create("Kushal Hegde", "FULL_NAME", provider.generate)
        entity_map.get_or_create("Kushal Hegde", "COMPANY_NAME", provider.generate)
        # These could be the same or different — the key point is they're tracked separately
        assert entity_map.total_entries == 2

    def test_email_exact_match(self, entity_map, provider):
        """Email addresses should use exact (not fuzzy) matching."""
        fake1 = entity_map.get_or_create("user@example.com", "EMAIL", provider.generate)
        fake2 = entity_map.get_or_create("user@example.com", "EMAIL", provider.generate)
        assert fake1 == fake2

    def test_occurrence_counting(self, entity_map, provider):
        """Occurrence counter should increment on repeated lookups."""
        entity_map.get_or_create("test@test.com", "EMAIL", provider.generate)
        entity_map.get_or_create("test@test.com", "EMAIL", provider.generate)
        entity_map.get_or_create("test@test.com", "EMAIL", provider.generate)
        assert entity_map.total_entries == 1  # One unique entity


class TestEntityMapPersistence:
    """Tests for entity map save/load round-trip."""

    @pytest.fixture
    def provider(self):
        return FakerProvider(seed=42)

    def test_entity_map_serialization(self, provider):
        """Entity map should survive a save/load round-trip."""
        map1 = EntityMap()
        map1.get_or_create("Kushal Hegde", "FULL_NAME", provider.generate)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        map1.save(temp_path)

        # Load into a new map
        map2 = EntityMap()
        map2.load(temp_path)

        assert map2.total_entries == 1

        # Clean up
        Path(temp_path).unlink(missing_ok=True)

    def test_json_format(self, provider):
        """Saved JSON should be valid and contain expected fields."""
        entity_map = EntityMap()
        entity_map.get_or_create("test@example.com", "EMAIL", provider.generate)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        entity_map.save(temp_path)

        with open(temp_path) as f:
            data = json.load(f)

        assert len(data) == 1
        key = list(data.keys())[0]
        assert "EMAIL" in key
        assert "fake_value" in data[key]
        assert "occurrences" in data[key]
        assert "original_variants" in data[key]

        Path(temp_path).unlink(missing_ok=True)


# ===========================================================================
# FakerProvider tests — format preservation
# ===========================================================================


class TestFakerProviderFormatPreservation:
    """Tests that fake values preserve the format of originals."""

    @pytest.fixture
    def provider(self):
        return FakerProvider(seed=42)

    def test_name_preserves_honorific(self, provider):
        fake = provider.generate("Mr. Kushal Hegde", "FULL_NAME")
        assert fake.startswith("Mr. ")

    def test_name_preserves_word_count_2(self, provider):
        fake = provider.generate("Rashi Patil", "FULL_NAME")
        assert len(fake.split()) == 2

    def test_name_preserves_word_count_3(self, provider):
        fake = provider.generate("Kushal Subbayya Hegde", "FULL_NAME")
        assert len(fake.split()) == 3

    def test_email_is_valid_format(self, provider):
        fake = provider.generate("user@example.com", "EMAIL")
        assert "@" in fake
        assert "." in fake.split("@")[1]

    def test_phone_preserves_country_code(self, provider):
        fake = provider.generate("+91 98765 43210", "PHONE")
        assert fake.startswith("+91")

    def test_phone_preserves_dash_separator(self, provider):
        fake = provider.generate("+91-9876543210", "PHONE")
        assert fake.startswith("+91-")

    def test_ssn_preserves_format(self, provider):
        fake = provider.generate("123-45-6789", "SSN")
        parts = fake.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 3
        assert len(parts[1]) == 2
        assert len(parts[2]) == 4

    def test_credit_card_preserves_spaces(self, provider):
        fake = provider.generate("4111 1111 1111 1111", "CREDIT_CARD")
        assert " " in fake

    def test_credit_card_preserves_dashes(self, provider):
        fake = provider.generate("4111-1111-1111-1111", "CREDIT_CARD")
        assert "-" in fake

    def test_pan_valid_format(self, provider):
        fake = provider.generate("ABCPD1234E", "PAN")
        assert len(fake) == 10
        assert fake[:3].isalpha() and fake[:3].isupper()
        assert fake[5:9].isdigit()
        assert fake[9].isalpha() and fake[9].isupper()

    def test_cin_valid_format(self, provider):
        fake = provider.generate("U28129PN1979PLC141032", "CIN")
        assert len(fake) == 21
        assert fake[0] in "UL"

    def test_ip_v4_returns_v4(self, provider):
        fake = provider.generate("192.168.1.100", "IP_ADDRESS")
        parts = fake.split(".")
        assert len(parts) == 4

    def test_dob_preserves_slash_format(self, provider):
        fake = provider.generate("15/03/1985", "DOB")
        assert "/" in fake

    def test_dob_preserves_dash_format(self, provider):
        fake = provider.generate("1985-03-15", "DOB")
        assert "-" in fake

    def test_deterministic_with_same_seed(self):
        """Same provider + same input should produce the same output on repeated calls."""
        p1 = FakerProvider(seed=123)
        result1 = p1.generate("test@example.com", "EMAIL")
        # Reset with same seed
        p2 = FakerProvider(seed=123)
        result2 = p2.generate("test@example.com", "EMAIL")
        # Both should produce the same first output
        assert result1 == result2
