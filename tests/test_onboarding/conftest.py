"""
Pytest configuration and fixtures for onboarding tests.

IMPORTANT: These tests use REAL API calls. Ensure credentials are configured.

Environment variables required for full test suite:
- GITHUB_TOKEN: For GitHub API tests
- ANTHROPIC_API_KEY: For Claude bio editing tests
- GOOGLE_CREDENTIALS_FILE: For Calendar tests (optional)

Tests will skip if required credentials are not available.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Ensure scripts package is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def github_token():
    """Get GitHub token from environment."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        pytest.skip("GITHUB_TOKEN not set - skipping GitHub API tests")
    return token


@pytest.fixture
def anthropic_api_key():
    """Get Anthropic API key from environment, and check the account can use it.

    A key that exists but cannot serve requests -- an exhausted credit
    balance, a rate limit, an overloaded API -- is the same situation as no
    key at all: the service is unavailable, and nothing about this repository
    is under test. Without this check those runs surfaced as eight assertion
    failures inside BioService that read exactly like code defects.

    The check is deliberately narrow. Only availability conditions skip;
    every other error, including a malformed request of ours, still fails.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set - skipping Claude API tests")

    reason = _anthropic_unavailable_reason(key)
    if reason:
        pytest.skip(f"Anthropic API unavailable: {reason}")
    return key


# Probed once per session: a live call per test would multiply the cost of
# the very thing we are checking is affordable.
_ANTHROPIC_STATUS = {}


def _anthropic_unavailable_reason(key):
    """Return why the Anthropic API cannot serve us, or None if it can."""
    if "reason" in _ANTHROPIC_STATUS:
        return _ANTHROPIC_STATUS["reason"]

    reason = None
    try:
        import anthropic

        anthropic.Anthropic(api_key=key).messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=4,
            messages=[{"role": "user", "content": "ok"}],
        )
    except Exception as exc:  # noqa: BLE001 - classified below
        text = str(exc)
        unavailable = (
            "credit balance is too low",
            "rate_limit",
            "overloaded",
            "insufficient_quota",
        )
        if any(marker in text.lower() for marker in unavailable):
            reason = text.split("message':")[-1].strip(" '\"}]")[:160]
        else:
            # Anything else is a real failure and must not be skipped away.
            reason = None

    _ANTHROPIC_STATUS["reason"] = reason
    return reason


@pytest.fixture
def google_credentials_file():
    """Get Google credentials file path from environment."""
    path = os.environ.get("GOOGLE_CREDENTIALS_FILE")
    if not path or not Path(path).exists():
        pytest.skip("GOOGLE_CREDENTIALS_FILE not set or file not found - skipping Calendar tests")
    return path


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_image(temp_dir):
    """Create a sample test image."""
    from PIL import Image

    # Create a simple test image
    img = Image.new("RGB", (400, 400), color=(100, 150, 200))
    img_path = temp_dir / "test_photo.png"
    img.save(img_path)
    return img_path


@pytest.fixture
def test_email():
    """Get test email address from environment or use default."""
    return os.environ.get("TEST_EMAIL", "contextualdynamicslab@gmail.com")


@pytest.fixture
def github_service(github_token):
    """Create a GitHubService instance for testing."""
    from cdl_bot.services.github_service import GitHubService
    return GitHubService(github_token, "ContextLab")


@pytest.fixture
def image_service():
    """Create an ImageService instance for testing."""
    from cdl_bot.services.image_service import ImageService
    return ImageService()


@pytest.fixture
def bio_service(anthropic_api_key):
    """Create a BioService instance for testing."""
    from cdl_bot.services.bio_service import BioService
    return BioService(anthropic_api_key)
