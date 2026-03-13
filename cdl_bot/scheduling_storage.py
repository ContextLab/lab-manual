"""
Persistent storage for scheduling sessions.

Stores sessions in a JSON file to survive bot restarts.
Separate from onboarding request storage.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from .models.scheduling_session import SchedulingSession

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_PATH = Path(__file__).parent / "data" / "scheduling_sessions.json"


class SchedulingStorage:
    """Persistent storage for scheduling sessions."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or DEFAULT_STORAGE_PATH
        self._cache: dict[str, SchedulingSession] = {}
        self._ensure_storage_exists()
        self._load()

    def _ensure_storage_exists(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.write_text("{}")

    def _load(self):
        try:
            data = json.loads(self.storage_path.read_text())
            self._cache = {
                sid: SchedulingSession.from_dict(sess_data)
                for sid, sess_data in data.items()
            }
            logger.info(f"Loaded {len(self._cache)} scheduling sessions from storage")
        except Exception as e:
            logger.error(f"Error loading scheduling sessions: {e}")
            self._cache = {}

    def _save(self):
        try:
            data = {
                sid: sess.to_dict()
                for sid, sess in self._cache.items()
            }
            self.storage_path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.error(f"Error saving scheduling sessions: {e}")

    def get(self, session_id: str) -> Optional[SchedulingSession]:
        return self._cache.get(session_id)

    def save(self, session: SchedulingSession):
        self._cache[session.session_id] = session
        self._save()

    def delete(self, session_id: str):
        if session_id in self._cache:
            del self._cache[session_id]
            self._save()

    def get_active(self) -> Optional[SchedulingSession]:
        """Get the most recent non-completed session, if any."""
        active = [
            s for s in self._cache.values()
            if s.status not in (s.status.COMPLETED, s.status.ERROR)
        ]
        if active:
            return max(active, key=lambda s: s.updated_at)
        return None

    def get_latest_completed(self) -> Optional[SchedulingSession]:
        """Get the most recently completed session (for emoji persistence)."""
        completed = [
            s for s in self._cache.values()
            if s.status == s.status.COMPLETED
        ]
        if completed:
            return max(completed, key=lambda s: s.updated_at)
        return None


# Global storage instance (lazy initialized)
_storage: Optional[SchedulingStorage] = None


def get_scheduling_storage(storage_path: Optional[Path] = None) -> SchedulingStorage:
    global _storage
    if _storage is None:
        _storage = SchedulingStorage(storage_path)
    return _storage


def get_session(session_id: str) -> Optional[SchedulingSession]:
    return get_scheduling_storage().get(session_id)


def save_session(session: SchedulingSession):
    get_scheduling_storage().save(session)


def get_active_session() -> Optional[SchedulingSession]:
    return get_scheduling_storage().get_active()


def get_latest_completed_session() -> Optional[SchedulingSession]:
    return get_scheduling_storage().get_latest_completed()
