"""
Google Calendar service for sharing calendars.

Handles:
- Calendar listing
- Sharing calendars with users
- Managing permissions (ACL)
"""

import logging
from typing import Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class CalendarService:
    """Service for Google Calendar operations."""

    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    # Permission role mappings
    ROLE_READER = "reader"  # Can see event details
    ROLE_WRITER = "writer"  # Can create, edit, delete events
    ROLE_OWNER = "owner"  # Full control

    def __init__(self, credentials_file: str, calendars: Optional[dict] = None):
        """
        Initialize the Calendar service.

        Args:
            credentials_file: Path to the Google service account JSON file
            calendars: Optional dictionary mapping calendar names to IDs
        """
        self.credentials_file = credentials_file
        self.calendars = calendars or {}
        self._service = None

    @property
    def service(self):
        """Get the Calendar API service (lazy loaded)."""
        if self._service is None:
            creds = Credentials.from_service_account_file(
                self.credentials_file, scopes=self.SCOPES
            )
            self._service = build("calendar", "v3", credentials=creds)
        return self._service

    def list_calendars(self) -> list[dict]:
        """
        List all calendars accessible to the service account.

        Returns:
            List of calendar dictionaries with id, summary, and description
        """
        calendars = []
        try:
            calendar_list = self.service.calendarList().list().execute()
            for calendar in calendar_list.get("items", []):
                calendars.append({
                    "id": calendar["id"],
                    "summary": calendar.get("summary", ""),
                    "description": calendar.get("description", ""),
                    "access_role": calendar.get("accessRole", ""),
                })
            logger.info(f"Retrieved {len(calendars)} calendars")
        except HttpError as e:
            logger.error(f"Error listing calendars: {e}")
        return calendars

    def get_calendar_id(self, name: str) -> Optional[str]:
        """
        Get a calendar ID by its name.

        Args:
            name: Calendar name (as configured)

        Returns:
            Calendar ID or None if not found
        """
        return self.calendars.get(name)

    def share_calendar(
        self,
        calendar_id: str,
        email: str,
        role: str = ROLE_READER,
        send_notifications: bool = True,
    ) -> tuple[bool, Optional[str]]:
        """
        Share a calendar with a user.

        Args:
            calendar_id: The calendar's ID
            email: User's email to share with
            role: Permission level (reader, writer, owner)
            send_notifications: Whether to send email notification

        Returns:
            Tuple of (success, error_message)
        """
        try:
            acl_rule = {
                "scope": {"type": "user", "value": email},
                "role": role,
            }

            result = (
                self.service.acl()
                .insert(
                    calendarId=calendar_id,
                    body=acl_rule,
                    sendNotifications=send_notifications,
                )
                .execute()
            )

            logger.info(
                f"Shared calendar {calendar_id} with {email} as {role} "
                f"(rule ID: {result.get('id')})"
            )
            return True, None

        except HttpError as e:
            error_msg = f"Error sharing calendar with {email}: {e}"
            logger.error(error_msg)
            return False, error_msg

    def share_multiple_calendars(
        self,
        email: str,
        calendar_permissions: dict[str, str],
        send_notifications: bool = True,
    ) -> dict[str, tuple[bool, Optional[str]]]:
        """
        Share multiple calendars with a user.

        Args:
            email: User's email to share with
            calendar_permissions: Dictionary mapping calendar names to roles
            send_notifications: Whether to send email notifications

        Returns:
            Dictionary mapping calendar names to (success, error_message) tuples
        """
        results = {}

        for calendar_name, role in calendar_permissions.items():
            calendar_id = self.get_calendar_id(calendar_name)
            if not calendar_id:
                results[calendar_name] = (
                    False,
                    f"Calendar '{calendar_name}' not configured",
                )
                continue

            success, error = self.share_calendar(
                calendar_id=calendar_id,
                email=email,
                role=role,
                send_notifications=send_notifications,
            )
            results[calendar_name] = (success, error)

        return results

    def remove_calendar_access(
        self, calendar_id: str, email: str
    ) -> tuple[bool, Optional[str]]:
        """
        Remove a user's access to a calendar.

        Args:
            calendar_id: The calendar's ID
            email: User's email to remove

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # First, find the ACL rule ID for this user
            acl_list = self.service.acl().list(calendarId=calendar_id).execute()

            rule_id = None
            for rule in acl_list.get("items", []):
                scope = rule.get("scope", {})
                if scope.get("type") == "user" and scope.get("value") == email:
                    rule_id = rule.get("id")
                    break

            if not rule_id:
                return True, None  # User doesn't have access, nothing to remove

            # Delete the ACL rule
            self.service.acl().delete(calendarId=calendar_id, ruleId=rule_id).execute()

            logger.info(f"Removed {email}'s access to calendar {calendar_id}")
            return True, None

        except HttpError as e:
            error_msg = f"Error removing calendar access for {email}: {e}"
            logger.error(error_msg)
            return False, error_msg

    def get_user_permissions(self, calendar_id: str, email: str) -> Optional[str]:
        """
        Get a user's current permission level for a calendar.

        Args:
            calendar_id: The calendar's ID
            email: User's email

        Returns:
            Permission role or None if no access
        """
        try:
            acl_list = self.service.acl().list(calendarId=calendar_id).execute()

            for rule in acl_list.get("items", []):
                scope = rule.get("scope", {})
                if scope.get("type") == "user" and scope.get("value") == email:
                    return rule.get("role")

            return None

        except HttpError as e:
            logger.error(f"Error getting permissions for {email}: {e}")
            return None
