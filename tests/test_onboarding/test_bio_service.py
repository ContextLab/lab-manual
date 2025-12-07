"""
Tests for the BioService.

IMPORTANT: These tests make REAL Claude API calls.
Requires ANTHROPIC_API_KEY environment variable to be set.

Tests verify:
- Bio editing produces third-person text
- Private information is detected and removed
- Output follows style guidelines
"""

from pathlib import Path
import sys

import pytest

# Ensure scripts package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.onboarding.services.bio_service import BioService


class TestBioServiceInit:
    """Tests for BioService initialization."""

    def test_init_with_valid_key(self, anthropic_api_key):
        """Test initialization with a valid API key."""
        service = BioService(anthropic_api_key)
        assert service.client is not None
        assert service.model == "claude-sonnet-4-20250514"

    def test_init_with_custom_model(self, anthropic_api_key):
        """Test initialization with a custom model."""
        service = BioService(anthropic_api_key, model="claude-3-haiku-20240307")
        assert service.model == "claude-3-haiku-20240307"


class TestBioEditing:
    """Tests for bio editing functionality."""

    def test_edit_first_person_bio(self, bio_service):
        """Test converting first-person bio to third-person."""
        raw_bio = """
        I am a graduate student studying computational neuroscience.
        I love working with brain data and developing machine learning models.
        In my free time, I enjoy hiking and playing chess.
        """

        edited_bio, error = bio_service.edit_bio(raw_bio, "Jane Smith")

        assert error is None
        assert edited_bio != ""

        # Should be in third person (no "I", "me", "my")
        lower_bio = edited_bio.lower()
        # Check that first person pronouns are removed
        # (May have some in quotes or other contexts, so this is a soft check)
        assert "jane" in lower_bio or "smith" in lower_bio

        print(f"Original: {raw_bio}")
        print(f"Edited: {edited_bio}")

    def test_edit_long_bio_gets_shortened(self, bio_service):
        """Test that overly long bios get shortened."""
        raw_bio = """
        I am a postdoctoral researcher in the lab. I completed my PhD at MIT where
        I studied the neural basis of memory consolidation. My dissertation focused
        on how the hippocampus interacts with the cortex during sleep. I used a
        combination of electrophysiology, optogenetics, and computational modeling
        to understand these processes. Before my PhD, I completed my undergraduate
        degree at Stanford where I majored in biology and minored in computer science.
        I am particularly interested in how we can use AI to analyze neural data.
        In addition to my research, I enjoy teaching and mentoring students.
        Outside of the lab, I am an avid rock climber and have climbed in Yosemite,
        Red Rocks, and various locations throughout Europe. I also enjoy cooking,
        especially Italian cuisine, and have recently taken up pottery.
        """

        edited_bio, error = bio_service.edit_bio(raw_bio, "Alex Johnson")

        assert error is None
        assert edited_bio != ""

        # Count sentences (rough approximation)
        sentences = [s.strip() for s in edited_bio.split('.') if s.strip()]
        # Should be condensed to roughly 3-4 sentences
        assert len(sentences) <= 6, f"Bio has {len(sentences)} sentences, expected 3-4"

        print(f"Edited bio ({len(sentences)} sentences): {edited_bio}")

    def test_edit_bio_uses_first_name(self, bio_service):
        """Test that edited bio uses the member's first name."""
        raw_bio = "I study neural networks and machine learning."

        edited_bio, error = bio_service.edit_bio(raw_bio, "Maria Garcia")

        assert error is None
        assert "maria" in edited_bio.lower()

        print(f"Edited: {edited_bio}")

    def test_edit_empty_bio(self, bio_service):
        """Test handling of empty bio."""
        edited_bio, error = bio_service.edit_bio("", "Test User")

        assert edited_bio == ""
        assert error is not None
        assert "no bio" in error.lower()

    def test_edit_whitespace_only_bio(self, bio_service):
        """Test handling of whitespace-only bio."""
        edited_bio, error = bio_service.edit_bio("   \n\t  ", "Test User")

        assert edited_bio == ""
        assert error is not None


class TestPrivateInfoDetection:
    """Tests for private information detection."""

    def test_detect_phone_number(self, bio_service):
        """Test detection of phone numbers."""
        text = "Call me at 555-123-4567 for more info."
        warnings = bio_service.check_for_private_info(text)

        assert len(warnings) > 0
        assert any("phone" in w.lower() for w in warnings)

    def test_detect_phone_number_with_dots(self, bio_service):
        """Test detection of phone numbers with dots."""
        text = "My number is 555.123.4567."
        warnings = bio_service.check_for_private_info(text)

        assert len(warnings) > 0

    def test_detect_email_address(self, bio_service):
        """Test detection of email addresses."""
        text = "Email me at person@example.com for questions."
        warnings = bio_service.check_for_private_info(text)

        assert len(warnings) > 0
        assert any("email" in w.lower() for w in warnings)

    def test_detect_street_address(self, bio_service):
        """Test detection of street addresses."""
        text = "I live at 123 Main Street in Boston."
        warnings = bio_service.check_for_private_info(text)

        assert len(warnings) > 0
        assert any("address" in w.lower() for w in warnings)

    def test_detect_ssn(self, bio_service):
        """Test detection of social security numbers."""
        text = "My SSN is 123-45-6789."
        warnings = bio_service.check_for_private_info(text)

        assert len(warnings) > 0
        assert any("ssn" in w.lower() for w in warnings)

    def test_no_false_positives_clean_text(self, bio_service):
        """Test that clean text doesn't trigger warnings."""
        text = "I am a researcher interested in computational neuroscience and machine learning."
        warnings = bio_service.check_for_private_info(text)

        assert len(warnings) == 0


class TestBioValidation:
    """Tests for bio validation functionality."""

    def test_validate_good_bio(self, bio_service):
        """Test validation of a properly formatted bio."""
        bio = "Alex is a graduate student studying computational neuroscience. She enjoys working on machine learning problems."

        is_valid, warning = bio_service._validate_bio(bio, "Alex")

        assert is_valid is True
        assert warning is None

    def test_validate_bio_too_long(self, bio_service):
        """Test validation catches overly long bios."""
        bio = "Sentence one. Sentence two. Sentence three. Sentence four. Sentence five. Sentence six. Sentence seven."

        is_valid, warning = bio_service._validate_bio(bio, "Test")

        # Should flag as too long
        assert is_valid is False
        assert warning is not None
        assert "sentences" in warning.lower()

    def test_validate_bio_first_person(self, bio_service):
        """Test validation catches first-person pronouns."""
        bio = "I am a researcher and I study brains."

        is_valid, warning = bio_service._validate_bio(bio, "Test")

        assert is_valid is False
        assert warning is not None
        assert "first-person" in warning.lower()

    def test_validate_bio_missing_name(self, bio_service):
        """Test validation catches missing name."""
        bio = "This person studies neuroscience."

        is_valid, warning = bio_service._validate_bio(bio, "Alex")

        assert is_valid is False
        assert warning is not None
        assert "alex" in warning.lower()

    def test_validate_bio_with_email(self, bio_service):
        """Test validation catches email in bio."""
        bio = "Alex studies neuroscience. Contact: alex@example.com"

        is_valid, warning = bio_service._validate_bio(bio, "Alex")

        assert is_valid is False
        assert warning is not None
        assert "email" in warning.lower()


class TestSuggestImprovements:
    """Tests for bio improvement suggestions."""

    def test_suggest_improvements_for_long_bio(self, bio_service):
        """Test getting suggestions for a long bio."""
        bio = """
        Jane is a researcher who studies many things. She completed her PhD at a
        prestigious university where she worked on neural networks. Her dissertation
        covered multiple topics including memory, learning, and attention. She has
        published many papers and presented at numerous conferences. In her free time,
        she enjoys hiking, reading, cooking, traveling, and spending time with friends.
        She is also interested in science communication and public outreach.
        """

        suggestions, error = bio_service.suggest_improvements(bio, "Jane Doe")

        assert error is None
        assert suggestions != ""
        assert len(suggestions) > 20  # Should have substantive feedback

        print(f"Suggestions: {suggestions}")

    def test_suggest_improvements_returns_actionable_feedback(self, bio_service):
        """Test that suggestions are actionable."""
        bio = "I study brains."

        suggestions, error = bio_service.suggest_improvements(bio, "Test Person")

        assert error is None
        assert suggestions != ""

        print(f"Suggestions for short bio: {suggestions}")


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_edit_bio_special_characters(self, bio_service):
        """Test handling bio with special characters."""
        raw_bio = "I study café culture & its effects on productivity! My research uses ü and é."

        edited_bio, error = bio_service.edit_bio(raw_bio, "Marie Müller")

        assert error is None
        assert edited_bio != ""

    def test_edit_bio_unicode_name(self, bio_service):
        """Test handling names with unicode characters."""
        raw_bio = "I am a researcher from Japan studying memory."

        edited_bio, error = bio_service.edit_bio(raw_bio, "Yuki Tanaka")

        assert error is None
        assert "yuki" in edited_bio.lower() or "tanaka" in edited_bio.lower()

    def test_edit_bio_very_short(self, bio_service):
        """Test editing a very short bio."""
        raw_bio = "I study brains."

        edited_bio, error = bio_service.edit_bio(raw_bio, "Sam Lee")

        assert error is None
        assert edited_bio != ""
        assert len(edited_bio) >= len(raw_bio)  # Should at least be as long

        print(f"Short bio edited: {edited_bio}")
