"""
Image processing service for adding hand-drawn borders to member photos.

Prefers using the website repo's add_borders.py with real SVG hand-drawn
border templates (matching context-lab.com/people). Falls back to a simple
wobble border if the website repo is not configured.
"""

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional, Union

from PIL import Image

logger = logging.getLogger(__name__)


class ImageService:
    """Service for processing member profile photos."""

    def __init__(
        self,
        border_color: tuple = (0, 105, 62),
        border_width: int = 8,
        website_repo_path: Optional[Path] = None,
    ):
        self.border_color = border_color
        self.border_width = border_width
        self.website_repo_path = website_repo_path

    def _can_use_add_borders(self) -> bool:
        """Check if the website repo's add_borders.py is available."""
        if not self.website_repo_path:
            return False
        script = self.website_repo_path / "scripts" / "add_borders.py"
        svg = self.website_repo_path / "images" / "templates" / "WebsiteDoodles_Posters_v1.svg"
        return script.exists() and svg.exists()

    def add_hand_drawn_border(
        self,
        input_path: Union[str, Path],
        output_path: Union[str, Path],
        use_face_detection: bool = True,
        seed: Optional[int] = None,
        **kwargs,
    ) -> Path:
        """
        Add a hand-drawn border to an image.

        Uses the website repo's add_borders.py (real SVG borders with face
        detection) when available. Falls back to a simple PIL border otherwise.
        """
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._can_use_add_borders():
            raise RuntimeError(
                "Cannot process photo: website repo not configured or add_borders.py not found. "
                "Set WEBSITE_REPO_PATH in .env to the path of the contextlab.github.io repo."
            )
        return self._add_border_via_script(input_path, output_path, use_face_detection)

    def _add_border_via_script(
        self, input_path: Path, output_path: Path, use_face_detection: bool
    ) -> Path:
        """Use the website repo's add_borders.py for real SVG borders."""
        script = self.website_repo_path / "scripts" / "add_borders.py"
        output_dir = output_path.parent

        cmd = [
            sys.executable, str(script),
            str(input_path), str(output_dir),
        ]
        if use_face_detection:
            cmd.append("--face")

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )

        if result.returncode != 0:
            logger.error(f"add_borders.py failed: {result.stderr}")
            raise RuntimeError(f"add_borders.py failed: {result.stderr}")

        # add_borders.py outputs to output_dir with .png extension
        processed = output_dir / f"{input_path.stem}.png"
        if processed.exists():
            # Rename to desired output path if different
            if processed != output_path and output_path.name != processed.name:
                processed.rename(output_path)
                return output_path
            return processed

        raise RuntimeError(f"add_borders.py did not produce expected output at {processed}")

    def find_existing_photo(self, member_name: str) -> Optional[Path]:
        """
        Check if a photo already exists in the website repo's images/people/ dir.

        Looks for files matching the member's name (e.g., "miles_mcdonald.png").
        Returns the path if found and it has proper borders, else None.
        """
        if not self.website_repo_path:
            return None

        people_dir = self.website_repo_path / "images" / "people"
        if not people_dir.exists():
            return None

        # Generate expected filename
        name_slug = member_name.lower().strip().replace(" ", "_")
        for ext in [".png", ".jpg", ".jpeg"]:
            candidate = people_dir / f"{name_slug}{ext}"
            if candidate.exists():
                # Check if it's already been processed (square + transparent corners)
                try:
                    img = Image.open(candidate)
                    w, h = img.size
                    if w == h and img.mode == "RGBA":
                        corners = [
                            img.getpixel((0, 0)),
                            img.getpixel((w - 1, 0)),
                            img.getpixel((0, h - 1)),
                            img.getpixel((w - 1, h - 1)),
                        ]
                        if all(c[3] == 0 for c in corners):
                            logger.info(f"Found existing bordered photo: {candidate}")
                            return candidate
                    # Photo exists but isn't bordered — return it for processing
                    logger.info(f"Found unprocessed photo: {candidate}")
                    return candidate
                except Exception:
                    pass

        return None

    def is_photo_bordered(self, photo_path: Union[str, Path]) -> bool:
        """Check if a photo already has hand-drawn borders applied."""
        try:
            img = Image.open(photo_path)
            w, h = img.size
            if w != h or img.mode != "RGBA":
                return False
            corners = [
                img.getpixel((0, 0)),
                img.getpixel((w - 1, 0)),
                img.getpixel((0, h - 1)),
                img.getpixel((w - 1, h - 1)),
            ]
            return all(c[3] == 0 for c in corners)
        except Exception:
            return False

    def validate_image(self, image_path: Union[str, Path]) -> tuple[bool, Optional[str]]:
        """Validate that an image is suitable for processing."""
        image_path = Path(image_path)

        if not image_path.exists():
            return False, f"Image file not found: {image_path}"

        try:
            with Image.open(image_path) as img:
                width, height = img.size
                if width < 200 or height < 200:
                    return False, f"Image too small ({width}x{height}). Minimum is 200x200."
                if img.format not in ["JPEG", "PNG", "GIF", "WEBP"]:
                    return False, f"Unsupported image format: {img.format}"
                return True, None
        except Exception as e:
            return False, f"Error reading image: {e}"
