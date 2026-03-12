"""
Tests for the OnboardingRequest model.

These tests do not require external API calls.
"""

import json
from datetime import datetime
from pathlib import Path
import sys

import pytest

# Ensure scripts package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.onboarding.models.onboarding_request import OnboardingRequest, OnboardingStatus


class TestOnboardingStatus:
    """Tests for OnboardingStatus enum."""

    def test_all_statuses_have_values(self):
        """Verify all expected status values exist."""
        expected_statuses = [
            "pending_info",
            "pending_approval",
            "github_pending",
            "calendar_pending",
            "ready_for_website",
            "completed",
            "rejected",
        ]
        actual_statuses = [s.value for s in OnboardingStatus]
        for status in expected_statuses:
            assert status in actual_statuses, f"Missing status: {status}"

    def test_status_from_string(self):
        """Test creating status from string value."""
        status = OnboardingStatus("pending_info")
        assert status == OnboardingStatus.PENDING_INFO


class TestOnboardingRequest:
    """Tests for OnboardingRequest dataclass."""

    def test_create_minimal_request(self):
        """Test creating a request with minimal required fields."""
        request = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
        )
        assert request.slack_user_id == "U12345678"
        assert request.slack_channel_id == "C87654321"
        assert request.name == "Test User"
        assert request.status == OnboardingStatus.PENDING_INFO
        assert request.github_username == ""
        assert request.email == ""
        assert request.github_teams == []
        assert request.github_invitation_sent is False
        assert request.calendar_invites_sent is False

    def test_create_full_request(self):
        """Test creating a request with all fields."""
        request = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
            email="test@example.com",
            github_username="testuser",
            bio_raw="I am a researcher",
            bio_edited="Test is a researcher",
            website_url="https://example.com",
            photo_original_path="/path/to/original.jpg",
            photo_processed_path="/path/to/processed.png",
            github_teams=[1, 2, 3],
            calendar_permissions={"Lab Calendar": "reader"},
            status=OnboardingStatus.PENDING_APPROVAL,
        )
        assert request.email == "test@example.com"
        assert request.github_username == "testuser"
        assert request.bio_raw == "I am a researcher"
        assert request.bio_edited == "Test is a researcher"
        assert request.website_url == "https://example.com"
        assert request.github_teams == [1, 2, 3]
        assert request.calendar_permissions == {"Lab Calendar": "reader"}
        assert request.status == OnboardingStatus.PENDING_APPROVAL

    def test_update_status(self):
        """Test status update functionality."""
        request = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
        )
        assert request.status == OnboardingStatus.PENDING_INFO

        # Update status
        request.update_status(OnboardingStatus.PENDING_APPROVAL)
        assert request.status == OnboardingStatus.PENDING_APPROVAL

        # Update again
        request.update_status(OnboardingStatus.GITHUB_PENDING)
        assert request.status == OnboardingStatus.GITHUB_PENDING

    def test_update_status_with_error(self):
        """Test status update with error message."""
        request = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
        )

        request.update_status(OnboardingStatus.ERROR, "Something went wrong")
        assert request.status == OnboardingStatus.ERROR
        assert request.error_message == "Something went wrong"

    def test_serialization_to_dict(self):
        """Test converting request to dictionary."""
        request = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
            email="test@example.com",
            github_username="testuser",
        )
        data = request.to_dict()

        assert isinstance(data, dict)
        assert data["slack_user_id"] == "U12345678"
        assert data["slack_channel_id"] == "C87654321"
        assert data["name"] == "Test User"
        assert data["email"] == "test@example.com"
        assert data["github_username"] == "testuser"
        assert data["status"] == "pending_info"
        assert "created_at" in data
        assert "updated_at" in data

    def test_deserialization_from_dict(self):
        """Test creating request from dictionary."""
        data = {
            "slack_user_id": "U12345678",
            "slack_channel_id": "C87654321",
            "name": "Test User",
            "email": "test@example.com",
            "github_username": "testuser",
            "bio_raw": "I am a researcher",
            "bio_edited": "Test is a researcher",
            "website_url": "https://example.com",
            "photo_original_path": "",
            "photo_processed_path": "",
            "github_teams": [1, 2],
            "calendar_permissions": {"Lab": "reader"},
            "status": "pending_approval",
            "github_invitation_sent": True,
            "calendar_invites_sent": False,
            "approved_by": "UADMIN123",
            "created_at": "2024-01-15T10:30:00",
            "updated_at": "2024-01-15T11:00:00",
        }

        request = OnboardingRequest.from_dict(data)

        assert request.slack_user_id == "U12345678"
        assert request.name == "Test User"
        assert request.email == "test@example.com"
        assert request.github_username == "testuser"
        assert request.status == OnboardingStatus.PENDING_APPROVAL
        assert request.github_invitation_sent is True
        assert request.approved_by == "UADMIN123"

    def test_roundtrip_serialization(self):
        """Test that to_dict and from_dict are inverses."""
        original = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
            email="test@example.com",
            github_username="testuser",
            bio_raw="Original bio",
            bio_edited="Edited bio",
            website_url="https://example.com",
            github_teams=[1, 2, 3],
            calendar_permissions={"Cal1": "reader", "Cal2": "writer"},
        )
        original.update_status(OnboardingStatus.PENDING_APPROVAL)
        original.github_invitation_sent = True

        # Serialize and deserialize
        data = original.to_dict()
        restored = OnboardingRequest.from_dict(data)

        # Verify key fields match
        assert restored.slack_user_id == original.slack_user_id
        assert restored.name == original.name
        assert restored.email == original.email
        assert restored.github_username == original.github_username
        assert restored.bio_raw == original.bio_raw
        assert restored.bio_edited == original.bio_edited
        assert restored.website_url == original.website_url
        assert restored.github_teams == original.github_teams
        assert restored.calendar_permissions == original.calendar_permissions
        assert restored.status == original.status
        assert restored.github_invitation_sent == original.github_invitation_sent

    def test_json_serialization(self):
        """Test that to_dict output is JSON serializable."""
        request = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
        )
        request.update_status(OnboardingStatus.PENDING_APPROVAL)

        # Should not raise
        json_str = json.dumps(request.to_dict())
        assert isinstance(json_str, str)

        # Should be able to parse back
        parsed = json.loads(json_str)
        assert parsed["slack_user_id"] == "U12345678"

    def test_error_message_field(self):
        """Test error message field functionality."""
        request = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
        )
        assert request.error_message == ""

        data = request.to_dict()
        assert "error_message" in data
        restored = OnboardingRequest.from_dict(data)
        assert restored.error_message == ""

    def test_created_at_timestamp(self):
        """Test that created_at is set automatically."""
        before = datetime.now()
        request = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
        )
        after = datetime.now()

        assert before <= request.created_at <= after

    def test_updated_at_changes_on_status_update(self):
        """Test that updated_at changes when status is updated."""
        request = OnboardingRequest(
            slack_user_id="U12345678",
            slack_channel_id="C87654321",
            name="Test User",
        )
        original_updated = request.updated_at

        # Small delay to ensure time difference
        import time
        time.sleep(0.01)

        request.update_status(OnboardingStatus.PENDING_APPROVAL)
        assert request.updated_at > original_updated
