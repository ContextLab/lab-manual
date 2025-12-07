"""
Configuration management for the CDL Onboarding Bot.

Loads credentials from environment variables or .env file.
Never commit credentials to the repository.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Try to load dotenv if available
try:
    from dotenv import load_dotenv
    # Look for .env in the onboarding directory or repo root
    env_paths = [
        Path(__file__).parent / ".env",
        Path(__file__).parent.parent.parent / ".env",
    ]
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass  # dotenv not installed, rely on environment variables


@dataclass
class SlackConfig:
    """Slack bot configuration."""
    bot_token: str  # xoxb-...
    app_token: str  # xapp-...
    admin_user_id: str  # Slack user ID of the admin (Jeremy)

    @classmethod
    def from_env(cls) -> "SlackConfig":
        """Load Slack configuration from environment variables."""
        bot_token = os.environ.get("SLACK_BOT_TOKEN")
        app_token = os.environ.get("SLACK_APP_TOKEN")
        admin_user_id = os.environ.get("SLACK_ADMIN_USER_ID")

        if not bot_token:
            raise ValueError("SLACK_BOT_TOKEN environment variable is required")
        if not app_token:
            raise ValueError("SLACK_APP_TOKEN environment variable is required")
        if not admin_user_id:
            raise ValueError("SLACK_ADMIN_USER_ID environment variable is required")

        return cls(
            bot_token=bot_token,
            app_token=app_token,
            admin_user_id=admin_user_id,
        )


@dataclass
class GitHubConfig:
    """GitHub configuration."""
    token: str  # Personal access token with admin:org scope
    org_name: str = "ContextLab"
    default_team: str = "Lab default"

    @classmethod
    def from_env(cls) -> "GitHubConfig":
        """Load GitHub configuration from environment variables."""
        token = os.environ.get("GITHUB_TOKEN")

        if not token:
            raise ValueError("GITHUB_TOKEN environment variable is required")

        return cls(
            token=token,
            org_name=os.environ.get("GITHUB_ORG_NAME", "ContextLab"),
            default_team=os.environ.get("GITHUB_DEFAULT_TEAM", "Lab default"),
        )


@dataclass
class GoogleCalendarConfig:
    """Google Calendar configuration."""
    credentials_file: str  # Path to service account JSON file
    calendars: dict  # Calendar names to IDs mapping

    # Default calendar permissions
    DEFAULT_PERMISSIONS = {
        "Contextual Dynamics Lab": "reader",  # Read-only
        "Out of lab": "writer",  # Edit
        "CDL Resources": "writer",  # Edit
    }

    @classmethod
    def from_env(cls) -> "GoogleCalendarConfig":
        """Load Google Calendar configuration from environment variables."""
        credentials_file = os.environ.get("GOOGLE_CREDENTIALS_FILE")

        if not credentials_file:
            raise ValueError("GOOGLE_CREDENTIALS_FILE environment variable is required")

        if not Path(credentials_file).exists():
            raise ValueError(f"Google credentials file not found: {credentials_file}")

        # Calendar IDs should be set as environment variables
        calendars = {}
        for name in ["Contextual Dynamics Lab", "Out of lab", "CDL Resources"]:
            env_key = f"GOOGLE_CALENDAR_{name.upper().replace(' ', '_')}"
            calendar_id = os.environ.get(env_key)
            if calendar_id:
                calendars[name] = calendar_id

        return cls(
            credentials_file=credentials_file,
            calendars=calendars,
        )


@dataclass
class AnthropicConfig:
    """Anthropic API configuration for bio editing."""
    api_key: str
    model: str = "claude-sonnet-4-20250514"

    @classmethod
    def from_env(cls) -> "AnthropicConfig":
        """Load Anthropic configuration from environment variables."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")

        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")

        return cls(
            api_key=api_key,
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        )


@dataclass
class Config:
    """Main configuration container."""
    slack: SlackConfig
    github: GitHubConfig
    google_calendar: Optional[GoogleCalendarConfig]
    anthropic: Optional[AnthropicConfig]

    # Image processing settings
    border_color: tuple = (0, 105, 62)  # Dartmouth green RGB
    border_width: int = 8

    # Local storage for processed files
    output_dir: Path = Path(__file__).parent / "output"

    @classmethod
    def from_env(cls) -> "Config":
        """Load all configuration from environment variables."""
        # Required configs
        slack = SlackConfig.from_env()
        github = GitHubConfig.from_env()

        # Optional configs (gracefully handle missing)
        try:
            google_calendar = GoogleCalendarConfig.from_env()
        except ValueError:
            google_calendar = None

        try:
            anthropic = AnthropicConfig.from_env()
        except ValueError:
            anthropic = None

        config = cls(
            slack=slack,
            github=github,
            google_calendar=google_calendar,
            anthropic=anthropic,
        )

        # Ensure output directory exists
        config.output_dir.mkdir(parents=True, exist_ok=True)

        return config


def get_config() -> Config:
    """Get the current configuration."""
    return Config.from_env()
