"""
Persistent project database for CDL scheduling.

Stores project metadata (emoji, channels, description, default duration)
in a JSON file that persists across terms. New projects added during
scheduling are saved for future terms.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "projects.json"


class ProjectStore:
    """Read/write access to the project database."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._data: dict = {}
        self._load()

    def _load(self):
        try:
            if self.db_path.exists():
                self._data = json.loads(self.db_path.read_text())
            else:
                self._data = {}
        except Exception as e:
            logger.error(f"Error loading project database: {e}")
            self._data = {}

    def _save(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.write_text(json.dumps(self._data, indent=2))

    def list_active(self) -> dict:
        """Return all active projects."""
        return {
            name: info for name, info in self._data.items()
            if info.get("active", True)
        }

    def get(self, name: str) -> Optional[dict]:
        """Get a single project by name."""
        return self._data.get(name)

    def upsert(self, name: str, *, emoji: str = "", channels: list = None,
               description: str = "", default_duration: int = 2,
               active: bool = True):
        """Add or update a project in the database."""
        existing = self._data.get(name, {})
        self._data[name] = {
            "description": description or existing.get("description", name),
            "channels": channels if channels is not None else existing.get("channels", []),
            "emoji": emoji or existing.get("emoji", ""),
            "default_duration": default_duration or existing.get("default_duration", 2),
            "active": active,
        }
        self._save()

    def deactivate(self, name: str):
        """Mark a project as inactive (keeps history, hides from defaults)."""
        if name in self._data:
            self._data[name]["active"] = False
            self._save()

    def sync_from_session(self, project_names: list, durations: dict,
                          emojis: dict):
        """
        After a scheduling session, sync any new or changed projects
        back to the database. Only updates emoji/duration if they changed;
        never removes existing projects (just marks them inactive if absent).
        """
        for name in project_names:
            existing = self._data.get(name, {})
            self._data[name] = {
                "description": existing.get("description", name),
                "channels": existing.get("channels", []),
                "emoji": emojis.get(name, existing.get("emoji", "")),
                "default_duration": durations.get(name, existing.get("default_duration", 2)),
                "active": True,
            }
        self._save()

    def get_config_text(self) -> str:
        """
        Format active projects as the text block for the config modal.
        Format: "Project Name | duration | emoji"
        """
        lines = []
        for name, info in self._data.items():
            if not info.get("active", True):
                continue
            dur = info.get("default_duration", 2)
            emoji = info.get("emoji", "")
            lines.append(f"{name} | {dur} | {emoji}")
        return "\n".join(lines)

    def get_survey_project_list(self, project_names: list, emojis: dict,
                               exclude_from_survey: list = None,
                               channel_id_map: dict = None) -> str:
        """
        Format the project list for the When2Meet survey announcement.
        Uses description + channels from the database.
        Excludes projects like office hours that don't need emoji reactions.

        Args:
            channel_id_map: dict of "#channel-name" -> "C12345" Slack channel IDs
                           for rendering clickable links. If None, channels shown as plain text.
        """
        if exclude_from_survey is None:
            exclude_from_survey = []
        if channel_id_map is None:
            channel_id_map = {}

        lines = []
        for name in project_names:
            if any(exc.lower() in name.lower() for exc in exclude_from_survey):
                continue

            info = self._data.get(name, {})
            desc = info.get("description", name)
            channels = info.get("channels", [])
            emoji = emojis.get(name, info.get("emoji", ""))

            if channels:
                linked = []
                for ch in channels:
                    ch_id = channel_id_map.get(ch)
                    if ch_id:
                        linked.append(f"<#{ch_id}|{ch.lstrip('#')}>")
                    else:
                        linked.append(ch)
                channel_str = " + ".join(linked)
                line = f"   • {desc} ({channel_str}): {emoji}"
            elif emoji:
                line = f"   • {desc}: {emoji}"
            else:
                line = f"   • {desc}"
            lines.append(line)
        return "\n".join(lines)


# Module-level singleton
_store: Optional[ProjectStore] = None


def get_project_store(db_path: Optional[Path] = None) -> ProjectStore:
    global _store
    if _store is None:
        _store = ProjectStore(db_path)
    return _store
