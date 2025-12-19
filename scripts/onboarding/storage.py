"""
Persistent storage for onboarding requests.

Stores requests in a JSON file to survive bot restarts.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from .models.onboarding_request import OnboardingRequest

logger = logging.getLogger(__name__)

# Default storage location
DEFAULT_STORAGE_PATH = Path(__file__).parent / "data" / "requests.json"


class RequestStorage:
    """Persistent storage for onboarding requests."""

    def __init__(self, storage_path: Optional[Path] = None):
        """
        Initialize storage.

        Args:
            storage_path: Path to JSON file. Defaults to data/requests.json
        """
        self.storage_path = storage_path or DEFAULT_STORAGE_PATH
        self._cache: dict[str, OnboardingRequest] = {}
        self._ensure_storage_exists()
        self._load()

    def _ensure_storage_exists(self):
        """Ensure storage directory and file exist."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.write_text("{}")

    def _load(self):
        """Load requests from disk."""
        try:
            data = json.loads(self.storage_path.read_text())
            self._cache = {
                user_id: OnboardingRequest.from_dict(req_data)
                for user_id, req_data in data.items()
            }
            logger.info(f"Loaded {len(self._cache)} requests from storage")
        except Exception as e:
            logger.error(f"Error loading requests: {e}")
            self._cache = {}

    def _save(self):
        """Save requests to disk."""
        try:
            data = {
                user_id: req.to_dict()
                for user_id, req in self._cache.items()
            }
            self.storage_path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.error(f"Error saving requests: {e}")

    def get(self, user_id: str) -> Optional[OnboardingRequest]:
        """Get an onboarding request by user ID."""
        return self._cache.get(user_id)

    def save(self, request: OnboardingRequest):
        """Save an onboarding request."""
        self._cache[request.slack_user_id] = request
        self._save()

    def delete(self, user_id: str):
        """Delete an onboarding request."""
        if user_id in self._cache:
            del self._cache[user_id]
            self._save()

    def get_all(self) -> dict[str, OnboardingRequest]:
        """Get all active requests."""
        return self._cache.copy()

    def get_by_status(self, status) -> list[OnboardingRequest]:
        """Get all requests with a specific status."""
        return [req for req in self._cache.values() if req.status == status]


# Global storage instance (lazy initialized)
_storage: Optional[RequestStorage] = None


def get_storage(storage_path: Optional[Path] = None) -> RequestStorage:
    """Get the global storage instance."""
    global _storage
    if _storage is None:
        _storage = RequestStorage(storage_path)
    return _storage


# Convenience functions for backwards compatibility
def get_request(user_id: str) -> Optional[OnboardingRequest]:
    """Get an active onboarding request for a user."""
    return get_storage().get(user_id)


def save_request(request: OnboardingRequest):
    """Save an onboarding request."""
    get_storage().save(request)


def delete_request(user_id: str):
    """Delete an onboarding request."""
    get_storage().delete(user_id)
