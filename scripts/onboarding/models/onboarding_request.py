"""
Data models for onboarding requests.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class OnboardingStatus(Enum):
    """Status of an onboarding request."""
    PENDING_INFO = "pending_info"  # Waiting for member to provide info
    PENDING_APPROVAL = "pending_approval"  # Waiting for admin approval
    GITHUB_PENDING = "github_pending"  # GitHub invite sent, awaiting acceptance
    CALENDAR_PENDING = "calendar_pending"  # Calendar invites being sent
    PHOTO_PENDING = "photo_pending"  # Waiting for photo upload
    PROCESSING = "processing"  # Processing bio/photo
    READY_FOR_WEBSITE = "ready_for_website"  # Ready for website update
    WEBSITE_PENDING = "website_pending"  # Waiting for website PR approval
    WEBSITE_PR_CREATED = "website_pr_created"  # Website PR has been created
    COMPLETED = "completed"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass
class OnboardingRequest:
    """
    Represents an onboarding request for a new lab member.

    Tracks all information needed to complete the onboarding process.
    """
    # Slack identifiers
    slack_user_id: str
    slack_channel_id: str  # DM channel with the new member

    # Basic info
    name: str = ""
    email: str = ""

    # GitHub
    github_username: str = ""
    github_teams: list = field(default_factory=list)
    github_invitation_sent: bool = False

    # Google Calendar
    calendar_permissions: dict = field(default_factory=dict)
    calendar_invites_sent: bool = False

    # Role and position info
    role: str = ""  # Graduate Student, Undergraduate, etc.
    grad_type: str = ""  # "Doctoral" or "Masters" (for grad students)
    grad_field: str = ""  # Field for Masters students (e.g., "Quantitative Biomedical Sciences")
    start_year: int = 0  # Year joined the lab (for CV entry)

    # Website info
    bio_raw: str = ""
    bio_edited: str = ""
    website_url: str = ""

    # Website PR tracking
    website_pr_url: str = ""
    website_branch: str = ""

    # Photo
    photo_original_path: Optional[Path] = None
    photo_processed_path: Optional[Path] = None

    # Status tracking
    status: OnboardingStatus = OnboardingStatus.PENDING_INFO
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    error_message: str = ""

    # Admin approval tracking
    admin_approval_message_ts: str = ""  # Timestamp of the approval message in Slack
    approved_by: str = ""  # Admin who approved

    def update_status(self, new_status: OnboardingStatus, error_message: str = ""):
        """Update the status and timestamp."""
        self.status = new_status
        self.updated_at = datetime.now()
        if error_message:
            self.error_message = error_message

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "slack_user_id": self.slack_user_id,
            "slack_channel_id": self.slack_channel_id,
            "name": self.name,
            "email": self.email,
            "github_username": self.github_username,
            "github_teams": self.github_teams,
            "github_invitation_sent": self.github_invitation_sent,
            "calendar_permissions": self.calendar_permissions,
            "calendar_invites_sent": self.calendar_invites_sent,
            "role": self.role,
            "grad_type": self.grad_type,
            "grad_field": self.grad_field,
            "start_year": self.start_year,
            "bio_raw": self.bio_raw,
            "bio_edited": self.bio_edited,
            "website_url": self.website_url,
            "website_pr_url": self.website_pr_url,
            "website_branch": self.website_branch,
            "photo_original_path": str(self.photo_original_path) if self.photo_original_path else None,
            "photo_processed_path": str(self.photo_processed_path) if self.photo_processed_path else None,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "error_message": self.error_message,
            "admin_approval_message_ts": self.admin_approval_message_ts,
            "approved_by": self.approved_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OnboardingRequest":
        """Create from dictionary."""
        return cls(
            slack_user_id=data["slack_user_id"],
            slack_channel_id=data["slack_channel_id"],
            name=data.get("name", ""),
            email=data.get("email", ""),
            github_username=data.get("github_username", ""),
            github_teams=data.get("github_teams", []),
            github_invitation_sent=data.get("github_invitation_sent", False),
            calendar_permissions=data.get("calendar_permissions", {}),
            calendar_invites_sent=data.get("calendar_invites_sent", False),
            role=data.get("role", ""),
            grad_type=data.get("grad_type", ""),
            grad_field=data.get("grad_field", ""),
            start_year=data.get("start_year", 0),
            bio_raw=data.get("bio_raw", ""),
            bio_edited=data.get("bio_edited", ""),
            website_url=data.get("website_url", ""),
            website_pr_url=data.get("website_pr_url", ""),
            website_branch=data.get("website_branch", ""),
            photo_original_path=Path(data["photo_original_path"]) if data.get("photo_original_path") else None,
            photo_processed_path=Path(data["photo_processed_path"]) if data.get("photo_processed_path") else None,
            status=OnboardingStatus(data.get("status", "pending_info")),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
            error_message=data.get("error_message", ""),
            admin_approval_message_ts=data.get("admin_approval_message_ts", ""),
            approved_by=data.get("approved_by", ""),
        )

    def get_summary(self) -> str:
        """Get a human-readable summary of the request."""
        lines = [
            f"*Name:* {self.name or 'Not provided'}",
            f"*Email:* {self.email or 'Not provided'}",
            f"*GitHub:* {self.github_username or 'Not provided'}",
            f"*Status:* {self.status.value}",
        ]

        if self.role:
            role_str = self.role
            if self.grad_type:
                role_str += f" ({self.grad_type})"
                if self.grad_field:
                    role_str += f" - {self.grad_field}"
            lines.append(f"*Role:* {role_str}")

        if self.github_teams:
            lines.append(f"*GitHub Teams:* {', '.join(self.github_teams)}")

        if self.bio_raw:
            bio_preview = self.bio_raw[:100] + "..." if len(self.bio_raw) > 100 else self.bio_raw
            lines.append(f"*Bio:* {bio_preview}")

        if self.website_url:
            lines.append(f"*Website:* {self.website_url}")

        return "\n".join(lines)
