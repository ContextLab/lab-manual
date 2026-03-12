"""
GitHub service for organization management.

Handles:
- Username validation
- Team listing
- Organization invitations
"""

import logging
from typing import Optional

from github import Github, GithubException
from github.NamedUser import NamedUser
from github.Organization import Organization
from github.Team import Team

logger = logging.getLogger(__name__)


class GitHubService:
    """Service for GitHub organization operations."""

    def __init__(self, token: str, org_name: str = "ContextLab"):
        """
        Initialize the GitHub service.

        Args:
            token: GitHub personal access token with admin:org scope
            org_name: Name of the GitHub organization
        """
        self.github = Github(token)
        self.org_name = org_name
        self._org: Optional[Organization] = None

    @property
    def org(self) -> Organization:
        """Get the organization object (lazy loaded)."""
        if self._org is None:
            self._org = self.github.get_organization(self.org_name)
        return self._org

    def validate_username(self, username: str) -> tuple[bool, Optional[str]]:
        """
        Check if a GitHub username exists and is valid.

        Args:
            username: GitHub username to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            user = self.github.get_user(username)
            # Access a property to ensure the user exists
            _ = user.login
            logger.info(f"Validated GitHub username: {username}")
            return True, None
        except GithubException as e:
            if e.status == 404:
                error_msg = f"GitHub user '{username}' not found"
                logger.warning(error_msg)
                return False, error_msg
            else:
                error_msg = f"Error validating GitHub user '{username}': {e}"
                logger.error(error_msg)
                return False, error_msg

    def get_user(self, username: str) -> Optional[NamedUser]:
        """
        Get a GitHub user by username.

        Args:
            username: GitHub username

        Returns:
            NamedUser object or None if not found
        """
        try:
            return self.github.get_user(username)
        except GithubException:
            return None

    def get_teams(self) -> list[dict]:
        """
        Get all teams in the organization.

        Returns:
            List of team dictionaries with id, name, slug, and description
        """
        teams = []
        try:
            for team in self.org.get_teams():
                teams.append({
                    "id": team.id,
                    "name": team.name,
                    "slug": team.slug,
                    "description": team.description or "",
                })
            logger.info(f"Retrieved {len(teams)} teams from {self.org_name}")
        except GithubException as e:
            logger.error(f"Error retrieving teams: {e}")
        return teams

    def get_team_by_name(self, team_name: str) -> Optional[Team]:
        """
        Get a team by its name.

        Args:
            team_name: Name of the team

        Returns:
            Team object or None if not found
        """
        try:
            for team in self.org.get_teams():
                if team.name == team_name:
                    return team
        except GithubException as e:
            logger.error(f"Error finding team '{team_name}': {e}")
        return None

    def get_team_by_id(self, team_id: int) -> Optional[Team]:
        """
        Get a team by its ID.

        Args:
            team_id: ID of the team

        Returns:
            Team object or None if not found
        """
        try:
            return self.org.get_team(team_id)
        except GithubException as e:
            logger.error(f"Error getting team {team_id}: {e}")
            return None

    def check_membership(self, username: str) -> bool:
        """
        Check if a user is already a member of the organization.

        Args:
            username: GitHub username

        Returns:
            True if the user is a member, False otherwise
        """
        try:
            return self.org.has_in_members(self.github.get_user(username))
        except GithubException:
            return False

    def invite_user(
        self,
        username: str,
        team_ids: Optional[list[int]] = None,
        role: str = "direct_member",
    ) -> tuple[bool, Optional[str]]:
        """
        Invite a user to the organization and optionally to specific teams.

        Args:
            username: GitHub username to invite
            team_ids: List of team IDs to add the user to
            role: Role in the organization (direct_member, admin, billing_manager)

        Returns:
            Tuple of (success, error_message)
        """
        try:
            user = self.github.get_user(username)

            # Check if already a member
            if self.check_membership(username):
                logger.info(f"User {username} is already a member of {self.org_name}")
                # If they're already a member, just add to teams
                if team_ids:
                    for team_id in team_ids:
                        team = self.get_team_by_id(team_id)
                        if team:
                            team.add_membership(user, role="member")
                            logger.info(f"Added {username} to team {team.name}")
                return True, None

            # Get team objects
            teams = []
            if team_ids:
                for team_id in team_ids:
                    team = self.get_team_by_id(team_id)
                    if team:
                        teams.append(team)

            # Send invitation
            if teams:
                self.org.invite_user(user=user, role=role, teams=teams)
            else:
                self.org.invite_user(user=user, role=role)

            logger.info(f"Sent organization invitation to {username}")
            return True, None

        except GithubException as e:
            error_msg = f"Error inviting {username}: {e}"
            logger.error(error_msg)
            return False, error_msg

    def remove_member(self, username: str) -> tuple[bool, Optional[str]]:
        """
        Remove a user from the organization.

        Note: This should only be done after admin confirmation.

        Args:
            username: GitHub username to remove

        Returns:
            Tuple of (success, error_message)
        """
        try:
            user = self.github.get_user(username)
            self.org.remove_from_membership(user)
            logger.info(f"Removed {username} from {self.org_name}")
            return True, None
        except GithubException as e:
            error_msg = f"Error removing {username}: {e}"
            logger.error(error_msg)
            return False, error_msg

    def get_pending_invitations(self) -> list[dict]:
        """
        Get list of pending organization invitations.

        Returns:
            List of invitation dictionaries
        """
        invitations = []
        try:
            for inv in self.org.invitations():
                invitations.append({
                    "id": inv.id,
                    "login": inv.login,
                    "email": inv.email,
                    "created_at": inv.created_at.isoformat() if inv.created_at else None,
                })
        except GithubException as e:
            logger.error(f"Error getting invitations: {e}")
        return invitations
