"""
Google Calendar service for sharing calendars and creating events.

Handles:
- Calendar listing
- Sharing calendars with users
- Managing permissions (ACL)
- Creating recurring meeting events
"""

import logging
from datetime import datetime, date, timedelta
from typing import Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Day name → weekday int (Monday=0) and RRULE BYDAY code
DAY_MAP = {
    "Monday": (0, "MO"), "Tuesday": (1, "TU"), "Wednesday": (2, "WE"),
    "Thursday": (3, "TH"), "Friday": (4, "FR"),
    "Saturday": (5, "SA"), "Sunday": (6, "SU"),
}

# Default meeting locations
PROJECT_LOCATION = "Moore 416 / Zoom: https://dartmouth.zoom.us/my/contextlab"
INDIVIDUAL_LOCATION = "Moore 349 / Zoom: https://dartmouth.zoom.us/my/contextlab"


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

    def create_recurring_event(
        self,
        calendar_id: str,
        summary: str,
        day: str,
        start_time: str,
        end_time: str,
        term_start: str,
        term_end: str,
        location: str = PROJECT_LOCATION,
        is_biweekly: bool = False,
        attendee_emails: Optional[list] = None,
        description: str = "",
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Create a recurring calendar event.

        Args:
            calendar_id: Calendar to create the event on
            summary: Event title (e.g., "Lab Meeting")
            day: Day of week (e.g., "Monday")
            start_time: Start time "HH:MM" (ET)
            end_time: End time "HH:MM" (ET)
            term_start: Term start date "YYYY-MM-DD"
            term_end: Term end date "YYYY-MM-DD"
            location: Event location string
            is_biweekly: If True, event repeats every 2 weeks
            attendee_emails: Optional list of attendee email addresses
            description: Optional event description

        Returns:
            Tuple of (success, error_message, event_id)
        """
        if day not in DAY_MAP:
            return False, f"Invalid day: {day}", None

        weekday_num, byday_code = DAY_MAP[day]

        # Calculate first occurrence: find the first matching weekday on or after term_start
        start_date = date.fromisoformat(term_start)
        days_ahead = weekday_num - start_date.weekday()
        if days_ahead < 0:
            days_ahead += 7
        first_date = start_date + timedelta(days=days_ahead)

        # Build RRULE
        end_date = date.fromisoformat(term_end)
        until_str = end_date.strftime("%Y%m%dT235959Z")
        interval = "INTERVAL=2;" if is_biweekly else ""
        rrule = f"RRULE:FREQ=WEEKLY;{interval}BYDAY={byday_code};UNTIL={until_str}"

        # Build event times (Eastern Time)
        start_dt = f"{first_date.isoformat()}T{start_time}:00"
        end_dt = f"{first_date.isoformat()}T{end_time}:00"

        event_body = {
            "summary": summary,
            "location": location,
            "start": {"dateTime": start_dt, "timeZone": "America/New_York"},
            "end": {"dateTime": end_dt, "timeZone": "America/New_York"},
            "recurrence": [rrule],
        }

        if description:
            event_body["description"] = description

        if attendee_emails:
            event_body["attendees"] = [{"email": e} for e in attendee_emails]

        try:
            event = self.service.events().insert(
                calendarId=calendar_id,
                body=event_body,
                sendUpdates="all" if attendee_emails else "none",
            ).execute()

            event_id = event.get("id", "")
            logger.info(f"Created recurring event '{summary}' on {day}s: {event_id}")
            return True, None, event_id

        except HttpError as e:
            error_msg = f"Error creating event '{summary}': {e}"
            logger.error(error_msg)
            return False, error_msg, None

    def create_schedule_events(
        self,
        calendar_id: str,
        schedule_df,
        groups: dict,
        term_start: str,
        term_end: str,
        pi_email: str = "",
    ) -> list[dict]:
        """
        Create all recurring calendar events for a term schedule.

        Args:
            calendar_id: Calendar to create events on
            schedule_df: DataFrame with columns Day, Start Time, End Time, Frequency
            groups: dict of meeting_name -> list of member names
            term_start: Term start date "YYYY-MM-DD"
            term_end: Term end date "YYYY-MM-DD"
            pi_email: PI's email for individual meeting invites

        Returns:
            List of {meeting_name, event_id, success, error} dicts
        """
        results = []

        if schedule_df is None or schedule_df.empty:
            return results

        for meeting_name, row in schedule_df.iterrows():
            day = row["Day"]
            start = row["Start Time"][:5]
            end = row["End Time"][:5]
            is_biweekly = "Biweekly" in row["Frequency"]

            # Determine location
            is_individual = meeting_name.endswith(" one-on-one")
            location = INDIVIDUAL_LOCATION if is_individual else PROJECT_LOCATION

            success, error, event_id = self.create_recurring_event(
                calendar_id=calendar_id,
                summary=meeting_name,
                day=day,
                start_time=start,
                end_time=end,
                term_start=term_start,
                term_end=term_end,
                location=location,
                is_biweekly=is_biweekly,
            )

            results.append({
                "meeting_name": meeting_name,
                "event_id": event_id,
                "success": success,
                "error": error,
            })

        return results
