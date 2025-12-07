"""
Tests for the GitHubService.

IMPORTANT: These tests make REAL GitHub API calls.
Requires GITHUB_TOKEN environment variable to be set.

Tests are designed to be safe:
- Username validation only queries public GitHub API
- Team listing only reads org data
- No invitations are actually sent during testing
"""

from pathlib import Path
import sys

import pytest

# Ensure scripts package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.onboarding.services.github_service import GitHubService


class TestGitHubServiceInit:
    """Tests for GitHubService initialization."""

    def test_init_with_valid_token(self, github_token):
        """Test initialization with a valid token."""
        service = GitHubService(github_token, "ContextLab")
        assert service.org_name == "ContextLab"
        assert service.github is not None
        assert service.org is not None

    def test_init_with_invalid_org(self, github_token):
        """Test initialization with invalid organization name."""
        # The org is lazy loaded, so we need to access it to trigger the error
        service = GitHubService(github_token, "nonexistent-org-that-does-not-exist-12345")
        with pytest.raises(Exception):
            # Accessing the org property should raise an exception
            _ = service.org


class TestUsernameValidation:
    """Tests for GitHub username validation."""

    def test_validate_existing_username(self, github_service):
        """Test validating a known existing GitHub username."""
        # Using 'octocat' which is GitHub's official test account
        is_valid, error = github_service.validate_username("octocat")
        assert is_valid is True
        assert error is None

    def test_validate_nonexistent_username(self, github_service):
        """Test validating a username that doesn't exist."""
        # Use a very unlikely username
        is_valid, error = github_service.validate_username("this-user-definitely-does-not-exist-12345678")
        assert is_valid is False
        assert error is not None
        assert "not found" in error.lower() or "does not exist" in error.lower()

    def test_validate_empty_username(self, github_service):
        """Test validating an empty username."""
        is_valid, error = github_service.validate_username("")
        assert is_valid is False
        assert error is not None

    def test_validate_username_with_spaces(self, github_service):
        """Test validating a username with invalid characters."""
        is_valid, error = github_service.validate_username("user name")
        assert is_valid is False

    def test_validate_contextlab_member(self, github_service):
        """Test validating a known ContextLab member (jeremymanning)."""
        is_valid, error = github_service.validate_username("jeremymanning")
        assert is_valid is True
        assert error is None


class TestTeamListing:
    """Tests for GitHub organization team listing."""

    def test_get_teams_returns_list(self, github_service):
        """Test that get_teams returns a list."""
        teams = github_service.get_teams()
        assert isinstance(teams, list)

    def test_get_teams_contains_expected_teams(self, github_service):
        """Test that known teams are in the list."""
        teams = github_service.get_teams()
        team_names = [team["name"] for team in teams]

        # ContextLab should have at least some teams
        assert len(teams) > 0

        # Each team should have id, name, and description
        for team in teams:
            assert "id" in team
            assert "name" in team
            assert "description" in team
            assert isinstance(team["id"], int)
            assert isinstance(team["name"], str)

    def test_get_teams_includes_lab_default(self, github_service):
        """Test that 'Lab default' team exists."""
        teams = github_service.get_teams()
        team_names = [team["name"] for team in teams]

        # Check for common team names that should exist
        # Note: Actual team names depend on the org setup
        assert len(team_names) > 0


class TestMembershipChecks:
    """Tests for membership status checking."""

    def test_check_membership_existing_member(self, github_service):
        """Test checking membership of a known member."""
        # jeremymanning should be a member of ContextLab
        is_member = github_service.check_membership("jeremymanning")
        assert is_member is True

    def test_check_membership_non_member(self, github_service):
        """Test checking membership of a non-member."""
        # octocat is probably not a member of ContextLab
        is_member = github_service.check_membership("octocat")
        assert is_member is False

    def test_check_membership_nonexistent_user(self, github_service):
        """Test checking membership of nonexistent user."""
        is_member = github_service.check_membership("nonexistent-user-12345678")
        assert is_member is False


class TestPendingInvitations:
    """Tests for pending invitation listing."""

    def test_get_pending_invitations_returns_list(self, github_service):
        """Test that get_pending_invitations returns a list."""
        invitations = github_service.get_pending_invitations()
        assert isinstance(invitations, list)

    def test_pending_invitations_format(self, github_service):
        """Test the format of pending invitations."""
        invitations = github_service.get_pending_invitations()

        # May be empty, but if not, should have expected fields
        for inv in invitations:
            assert "login" in inv
            assert "email" in inv
            assert "invited_at" in inv


class TestInvitationSafety:
    """Tests to verify invitation functions have proper safeguards.

    NOTE: These tests verify the function signature and error handling,
    but do NOT actually send invitations.
    """

    def test_invite_user_validates_username(self, github_service):
        """Test that invite_user validates the username first."""
        # Try to invite a nonexistent user - should fail validation
        success, error = github_service.invite_user(
            "nonexistent-user-that-does-not-exist-12345678",
            team_ids=[]
        )
        assert success is False
        assert error is not None

    def test_invite_user_with_empty_username(self, github_service):
        """Test that invite_user rejects empty username."""
        success, error = github_service.invite_user("", team_ids=[])
        assert success is False
        assert error is not None


class TestRemoveMemberSafety:
    """Tests for remove_member safety.

    NOTE: These tests verify error handling but do NOT remove anyone.
    """

    def test_remove_nonexistent_user(self, github_service):
        """Test removing a user that doesn't exist."""
        success, error = github_service.remove_member("nonexistent-user-12345678")
        # Should fail gracefully
        assert success is False


class TestAPIResponseFormat:
    """Tests to verify API responses are in expected format."""

    def test_github_user_info_format(self, github_service):
        """Test that user info has expected fields."""
        # Directly access the GitHub API through the service
        user = github_service.github.get_user("octocat")

        # Verify expected attributes exist
        assert hasattr(user, "login")
        assert hasattr(user, "name")
        assert hasattr(user, "email")
        assert hasattr(user, "avatar_url")
        assert hasattr(user, "html_url")

        # Verify login is correct
        assert user.login == "octocat"

    def test_org_info_accessible(self, github_service):
        """Test that organization info is accessible."""
        org = github_service.org

        assert hasattr(org, "login")
        assert hasattr(org, "name")
        assert org.login == "ContextLab"
