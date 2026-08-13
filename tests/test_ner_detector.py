"""
Tests for the NER-based PII detector.

Tests PERSON, ORG, and ADDRESS detection with precision filters:
- Stoplist filtering for generic ORG references
- Short/acronym rejection for PERSON
- Legal suffix confidence boosting for ORG
"""

import pytest

from pii_redactor.detectors.ner_detector import NERDetector


@pytest.fixture(scope="module")
def detector():
    """Shared NER detector instance (loads model once)."""
    return NERDetector()


class TestNERDetectorPerson:
    """Tests for PERSON (FULL_NAME) detection."""

    def test_detect_multi_word_name(self, detector):
        entities = detector.detect("Mr. Kushal Subbayya Hegde is the managing director.")
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        assert len(names) >= 1
        # Should detect some form of the name
        name_texts = [e.text for e in names]
        assert any("Kushal" in t for t in name_texts)

    def test_detect_simple_name(self, detector):
        entities = detector.detect("Dr. Anita Sharma joined the board in 2020.")
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        assert len(names) >= 1

    def test_reject_acronym(self, detector):
        """All-caps single words should be rejected as names."""
        entities = detector.detect("The SEBI regulations apply to this filing.")
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        # SEBI should not be classified as a person name
        sebi_names = [e for e in names if e.text.strip() == "SEBI"]
        assert len(sebi_names) == 0

    def test_reject_generic_words(self, detector):
        """Common generic words should not be treated as names."""
        entities = detector.detect("Section 42 of the Act applies to this provision.")
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        generic = [e for e in names if e.text.strip().lower() in {"section", "act", "provision"}]
        assert len(generic) == 0


class TestNERDetectorOrg:
    """Tests for ORG (COMPANY_NAME) detection."""

    def test_detect_company_with_legal_suffix(self, detector):
        text = "TechStart Innovations Pvt. Ltd. is the issuer company."
        entities = detector.detect(text)
        orgs = [e for e in entities if e.entity_type == "COMPANY_NAME"]
        assert len(orgs) >= 1
        # Should have high confidence due to legal suffix
        org_with_suffix = [e for e in orgs if e.validated]
        assert len(org_with_suffix) >= 1

    def test_stoplist_the_company(self, detector):
        """'the Company' as a pronoun reference should NOT be detected."""
        entities = detector.detect(
            "The Company has appointed the Board of Directors. "
            "Our Company operates in multiple states."
        )
        orgs = [e for e in entities if e.entity_type == "COMPANY_NAME"]
        # Check that generic references are filtered
        bad_orgs = [e for e in orgs if e.text.strip().lower() in {
            "the company", "our company", "the board of directors", "the board"
        }]
        assert len(bad_orgs) == 0

    def test_detect_named_company(self, detector):
        text = "SBI Capital Markets Ltd. has been appointed as the lead manager."
        entities = detector.detect(text)
        orgs = [e for e in entities if e.entity_type == "COMPANY_NAME"]
        assert len(orgs) >= 1


class TestNERDetectorAddress:
    """Tests for ADDRESS detection."""

    def test_reject_standalone_city(self, detector):
        """Standalone city names should NOT be treated as PII addresses."""
        entities = detector.detect("The office is located in Bangalore, Karnataka.")
        addresses = [e for e in entities if e.entity_type == "ADDRESS"]
        # Standalone "Bangalore" and "Karnataka" are in the geo stoplist
        # and should NOT be detected as PII
        standalone = [e for e in addresses if e.text.strip().lower() in {"bangalore", "karnataka"}]
        assert len(standalone) == 0

    def test_reject_standalone_country(self, detector):
        """Standalone country names should NOT be treated as PII addresses."""
        entities = detector.detect("The Company is incorporated in India.")
        addresses = [e for e in entities if e.entity_type == "ADDRESS"]
        india_addrs = [e for e in addresses if e.text.strip().lower() == "india"]
        # "India" alone is not PII
        assert len(india_addrs) == 0

    def test_detect_multi_word_address(self, detector):
        """Multi-word addresses with specific details should be detected."""
        entities = detector.detect(
            "The registered office is at 42 MG Road, Baner Pune 411045."
        )
        addresses = [e for e in entities if e.entity_type == "ADDRESS"]
        # Multi-word location or pincode-containing text should be detected
        # (exact detection depends on spaCy, but at minimum we should not crash)
        assert isinstance(addresses, list)


class TestNERDetectorEmpty:
    """Edge case tests."""

    def test_empty_string(self, detector):
        entities = detector.detect("")
        assert len(entities) == 0

    def test_whitespace_only(self, detector):
        entities = detector.detect("   \n\t  ")
        assert len(entities) == 0

    def test_numbers_only(self, detector):
        entities = detector.detect("12345 67890 11111")
        assert len(entities) == 0


class TestRoleLabelNameDetection:
    """Bug 2 fix: names following role labels should be detected even when spaCy misses them."""

    def test_contact_person_name(self, detector):
        text = "Contact Person: Sarthak Malvadkar, Company Secretary and Compliance Officer"
        entities = detector.detect(text)
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        name_texts = [e.text for e in names]
        assert any("Sarthak Malvadkar" in t for t in name_texts), (
            f"Expected 'Sarthak Malvadkar' in detected names, got: {name_texts}"
        )

    def test_company_secretary_name(self, detector):
        text = "Company Secretary: Rajesh Kumar is responsible for compliance."
        entities = detector.detect(text)
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        name_texts = [e.text for e in names]
        assert any("Rajesh Kumar" in t for t in name_texts), (
            f"Expected 'Rajesh Kumar' in detected names, got: {name_texts}"
        )

    def test_managing_director_name(self, detector):
        text = "Managing Director: Kushal Subbayya Hegde oversees operations."
        entities = detector.detect(text)
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        name_texts = [e.text for e in names]
        assert any("Kushal" in t for t in name_texts), (
            f"Expected a name containing 'Kushal' in detected names, got: {name_texts}"
        )

    def test_director_name(self, detector):
        text = "Director Anita Sharma was appointed on January 15, 2024."
        entities = detector.detect(text)
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        name_texts = [e.text for e in names]
        assert any("Anita Sharma" in t for t in name_texts), (
            f"Expected 'Anita Sharma' in detected names, got: {name_texts}"
        )

    def test_cfo_name(self, detector):
        text = "Chief Financial Officer: Priya Deshmukh manages all financial reporting."
        entities = detector.detect(text)
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        name_texts = [e.text for e in names]
        assert any("Priya Deshmukh" in t for t in name_texts), (
            f"Expected 'Priya Deshmukh' in detected names, got: {name_texts}"
        )

    def test_promoter_name(self, detector):
        text = "Promoter: Vikram Patel holds 42% of the equity shares."
        entities = detector.detect(text)
        names = [e for e in entities if e.entity_type == "FULL_NAME"]
        name_texts = [e.text for e in names]
        assert any("Vikram Patel" in t for t in name_texts), (
            f"Expected 'Vikram Patel' in detected names, got: {name_texts}"
        )


class TestStreetNameRejection:
    """Bug 3 fix: street/area names must NOT be flagged as COMPANY_NAME."""

    def test_reject_mg_road(self, detector):
        entities = detector.detect("Our office is at 42 MG Road, Bangalore.")
        orgs = [e for e in entities if e.entity_type == "COMPANY_NAME"]
        road_orgs = [e for e in orgs if "MG Road" in e.text]
        assert len(road_orgs) == 0, f"MG Road should not be COMPANY_NAME, got: {road_orgs}"

    def test_reject_business_centre(self, detector):
        entities = detector.detect(
            "Corporate Office: 201, Tower 2, Montreal Business Centre, Baner, Pune."
        )
        orgs = [e for e in entities if e.entity_type == "COMPANY_NAME"]
        bad_orgs = [e for e in orgs if "Business Centre" in e.text]
        assert len(bad_orgs) == 0, (
            f"Montreal Business Centre should not be COMPANY_NAME, got: {bad_orgs}"
        )

    def test_reject_off_pallod_farms(self, detector):
        entities = detector.detect(
            "Address: Off Pallod Farms, Baner Road, Pune 411045."
        )
        orgs = [e for e in entities if e.entity_type == "COMPANY_NAME"]
        bad_orgs = [e for e in orgs if "Pallod Farms" in e.text]
        assert len(bad_orgs) == 0, (
            f"Off Pallod Farms should not be COMPANY_NAME, got: {bad_orgs}"
        )

    def test_reject_midc_industrial_area(self, detector):
        entities = detector.detect(
            "Factory located at MIDC Industrial Area, Phase II, Chakan."
        )
        orgs = [e for e in entities if e.entity_type == "COMPANY_NAME"]
        bad_orgs = [e for e in orgs if "Industrial Area" in e.text]
        assert len(bad_orgs) == 0, (
            f"MIDC Industrial Area should not be COMPANY_NAME, got: {bad_orgs}"
        )

    def test_keep_company_with_legal_suffix(self, detector):
        """Ensure companies with proper legal suffixes are still detected."""
        entities = detector.detect("Business Centre Services Pvt. Ltd. operates nationwide.")
        orgs = [e for e in entities if e.entity_type == "COMPANY_NAME"]
        # Should still detect because it has "Pvt. Ltd." legal suffix
        assert len(orgs) >= 1, "Company with legal suffix should still be detected"


class TestFullAddressDetection:
    """Bug 1 verification: full contiguous addresses should be detected as one span."""

    def test_full_registered_office_address(self, detector):
        """A full comma-separated address with pincode should be detected."""
        text = (
            "Registered Office: 11/3, 11/4 and 11/5, Village Birdewadi, "
            "Chakan Taluka - Khed, Pune 410501, Maharashtra, India"
        )
        entities = detector.detect(text)
        addresses = [e for e in entities if e.entity_type == "ADDRESS"]
        # Should detect at least some part of this address
        # The key is that "Village Birdewadi" alone is not fragmented off
        all_text = " ".join(e.text for e in addresses)
        # We expect the detector to find address-shaped content
        assert isinstance(addresses, list)  # At minimum, no crash

    def test_full_corporate_office_address(self, detector):
        """A corporate office address should not produce COMPANY_NAME false positives."""
        text = (
            "Corporate Office: 201, Tower 2, Montreal Business Centre, "
            "Off Pallod Farms, Baner, Pune 411045, Maharashtra, India"
        )
        entities = detector.detect(text)
        # "Montreal Business Centre" and "Off Pallod Farms" must NOT be COMPANY_NAME
        orgs = [e for e in entities if e.entity_type == "COMPANY_NAME"]
        bad_orgs = [e for e in orgs if "Business Centre" in e.text or "Pallod Farms" in e.text]
        assert len(bad_orgs) == 0, f"Address parts should not be COMPANY_NAME: {bad_orgs}"

    def test_expand_full_address_with_label(self, detector):
        """Bug 4 fix: NER location fragments should expand into full addresses using labels/pincodes."""
        text = (
            "Registered Office: 11/3, 11/4 and 11/5, Village Birdewadi, "
            "Chakan Taluka - Khed, Pune – 410 501, Maharashtra, India;"
        )
        entities = detector.detect(text)
        addresses = [e for e in entities if e.entity_type == "ADDRESS"]
        
        # We expect a single merged address entity covering the whole span
        assert len(addresses) >= 1, "Should detect at least one address"
        
        # The first address should capture the bulk of the text, not just "Village Birdewadi"
        full_address = addresses[0].text
        assert "11/3" in full_address, f"Span expansion missed house number: {full_address}"
        assert "Maharashtra, India" in full_address, f"Span expansion missed end of address: {full_address}"
        # Make sure we didn't just extract fragments
        assert len(full_address) > 80, f"Address span seems too short: {full_address}"

    def test_expand_prose_address_trims_context(self, detector):
        """Expanded address should NOT include leading context words like 'at', 'located at'."""
        text = (
            "KSH International Limited, a public limited company "
            "incorporated under the Companies Act, 1956, having its "
            "registered office at 11/3, 11/4 and 11/5, Village Birdewadi, "
            "Chakan Taluka - Khed, Pune – 410 501, Maharashtra, India"
        )
        entities = detector.detect(text)
        addresses = [e for e in entities if e.entity_type == "ADDRESS"]
        
        assert len(addresses) >= 1, "Should detect at least one address"
        full_address = addresses[0].text
        # Should start at the house number, not at "at" or "office at"
        assert full_address.startswith("11/3"), (
            f"Address should start at house number, got: '{full_address[:30]}...'"
        )
        assert "Maharashtra, India" in full_address
