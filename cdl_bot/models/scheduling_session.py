"""
Data models for scheduling sessions.

Tracks the multi-step meeting scheduling workflow:
1. Director configures projects/members via modal
2. Bot creates when2meet survey + posts to #general
3. Director triggers response collection
4. Bot scrapes when2meet, fuzzy-matches names, director confirms
5. Algorithm runs, director reviews schedule
6. Calendar events created, announcement posted
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SchedulingStatus(Enum):
    """Status of a scheduling session."""
    CONFIGURING = "configuring"  # Director filling out project config
    SURVEY_POSTED = "survey_posted"  # When2meet link posted, waiting for responses
    COLLECTING = "collecting"  # Scraping when2meet responses
    NAME_MATCHING = "name_matching"  # Director confirming name mappings
    SCHEDULING = "scheduling"  # Algorithm running
    REVIEW = "review"  # Director reviewing proposed schedule
    CREATING_EVENTS = "creating_events"  # Calendar events being created
    ANNOUNCING = "announcing"  # Posting announcement to #general
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class SchedulingSession:
    """
    Represents a term scheduling session.

    Tracks all state needed across the multi-step scheduling flow.
    """
    # Session identity
    session_id: str  # Unique ID (timestamp-based)
    initiated_by: str  # Slack user ID of director

    # Term info
    term: str = ""  # e.g., "Spring 2026"
    term_start: str = ""  # ISO date string
    term_end: str = ""  # ISO date string

    # Project configuration
    # groups: dict of meeting_name -> list of member first names
    groups: dict = field(default_factory=dict)
    # preferred_durations: dict of meeting_name -> 15-min blocks (e.g., 4=60min, 2.5=biweekly 30min)
    preferred_durations: dict = field(default_factory=dict)
    # project_emojis: dict of meeting_name -> emoji string
    project_emojis: dict = field(default_factory=dict)
    # project_descriptions: dict of meeting_name -> description string
    project_descriptions: dict = field(default_factory=dict)
    # project_channels: dict of meeting_name -> list of "#channel" strings
    project_channels: dict = field(default_factory=dict)

    # Priority lists
    pi: list = field(default_factory=list)  # PI names
    senior: list = field(default_factory=list)  # Senior member names
    external: list = field(default_factory=list)  # External members (skip lab meeting)

    # When2meet
    when2meet_url: str = ""
    survey_message_ts: str = ""  # Timestamp of the #general message with survey link
    survey_channel: str = ""  # Channel where survey was posted

    # Name matching
    # when2meet_to_slack: dict of when2meet name -> slack display name
    name_mapping: dict = field(default_factory=dict)
    # unmatched_names: list of when2meet names that couldn't be auto-matched
    unmatched_names: list = field(default_factory=list)

    # Schedule results
    # scheduled: dict of meeting_name -> {day, times, scores, etc.}
    scheduled: dict = field(default_factory=dict)
    # schedule_summary: formatted text summary
    schedule_summary: str = ""

    # Calendar event IDs (for potential rollback)
    calendar_event_ids: list = field(default_factory=list)

    # Individual meeting requests (from :zoom: reactions)
    # zoom_requests: list of {user_id, name, accepted, duration_blocks}
    zoom_requests: list = field(default_factory=list)

    # Announcement
    announcement_message_ts: str = ""

    # Status tracking
    status: SchedulingStatus = SchedulingStatus.CONFIGURING
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    error_message: str = ""

    # DM channel with director for the interactive flow
    dm_channel: str = ""

    def update_status(self, new_status: SchedulingStatus, error_message: str = ""):
        """Update the status and timestamp."""
        self.status = new_status
        self.updated_at = datetime.now()
        if error_message:
            self.error_message = error_message

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "session_id": self.session_id,
            "initiated_by": self.initiated_by,
            "term": self.term,
            "term_start": self.term_start,
            "term_end": self.term_end,
            "groups": self.groups,
            "preferred_durations": self.preferred_durations,
            "project_emojis": self.project_emojis,
            "project_descriptions": self.project_descriptions,
            "project_channels": self.project_channels,
            "pi": self.pi,
            "senior": self.senior,
            "external": self.external,
            "when2meet_url": self.when2meet_url,
            "survey_message_ts": self.survey_message_ts,
            "survey_channel": self.survey_channel,
            "name_mapping": self.name_mapping,
            "unmatched_names": self.unmatched_names,
            "scheduled": self.scheduled,
            "schedule_summary": self.schedule_summary,
            "calendar_event_ids": self.calendar_event_ids,
            "zoom_requests": self.zoom_requests,
            "announcement_message_ts": self.announcement_message_ts,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "error_message": self.error_message,
            "dm_channel": self.dm_channel,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SchedulingSession":
        """Create from dictionary."""
        return cls(
            session_id=data["session_id"],
            initiated_by=data["initiated_by"],
            term=data.get("term", ""),
            term_start=data.get("term_start", ""),
            term_end=data.get("term_end", ""),
            groups=data.get("groups", {}),
            preferred_durations=data.get("preferred_durations", {}),
            project_emojis=data.get("project_emojis", {}),
            project_descriptions=data.get("project_descriptions", {}),
            project_channels=data.get("project_channels", {}),
            pi=data.get("pi", []),
            senior=data.get("senior", []),
            external=data.get("external", []),
            when2meet_url=data.get("when2meet_url", ""),
            survey_message_ts=data.get("survey_message_ts", ""),
            survey_channel=data.get("survey_channel", ""),
            name_mapping=data.get("name_mapping", {}),
            unmatched_names=data.get("unmatched_names", []),
            scheduled=data.get("scheduled", {}),
            schedule_summary=data.get("schedule_summary", ""),
            calendar_event_ids=data.get("calendar_event_ids", []),
            zoom_requests=data.get("zoom_requests", []),
            announcement_message_ts=data.get("announcement_message_ts", ""),
            status=SchedulingStatus(data.get("status", "configuring")),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
            error_message=data.get("error_message", ""),
            dm_channel=data.get("dm_channel", ""),
        )

    def get_all_members(self) -> list:
        """Get a deduplicated list of all members across all groups."""
        members = set()
        for group_members in self.groups.values():
            members.update(group_members)
        members.update(self.pi)
        return sorted(members)
