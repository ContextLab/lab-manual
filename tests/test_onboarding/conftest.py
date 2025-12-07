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
    """Get Anthropic API key from environment."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set - skipping Claude API tests")
    return key


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
    from scripts.onboarding.services.github_service import GitHubService
    return GitHubService(github_token, "ContextLab")


@pytest.fixture
def image_service():
    """Create an ImageService instance for testing."""
    from scripts.onboarding.services.image_service import ImageService
    return ImageService()


@pytest.fixture
def bio_service(anthropic_api_key):
    """Create a BioService instance for testing."""
    from scripts.onboarding.services.bio_service import BioService
    return BioService(anthropic_api_key)
