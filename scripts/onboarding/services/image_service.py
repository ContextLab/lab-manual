"""
Image processing service for adding hand-drawn borders to member photos.

The borders match the style used on https://www.context-lab.com/people
with Dartmouth green color and slight variations for a hand-drawn look.
"""

import logging
import math
import random
from pathlib import Path
from typing import Optional, Union

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


class ImageService:
    """Service for processing member profile photos."""

    # Dartmouth green RGB
    DARTMOUTH_GREEN = (0, 105, 62)

    # Default settings
    DEFAULT_BORDER_WIDTH = 8
    DEFAULT_OUTPUT_SIZE = (400, 400)  # Square output

    def __init__(
        self,
        border_color: tuple = DARTMOUTH_GREEN,
        border_width: int = DEFAULT_BORDER_WIDTH,
    ):
        """
        Initialize the image service.

        Args:
            border_color: RGB tuple for border color
            border_width: Width of the border in pixels
        """
        self.border_color = border_color
        self.border_width = border_width

    def add_hand_drawn_border(
        self,
        input_path: Union[str, Path],
        output_path: Union[str, Path],
        border_width: Optional[int] = None,
        wobble_amount: float = 1.5,
        seed: Optional[int] = None,
    ) -> Path:
        """
        Add a hand-drawn style green border to an image.

        The border has slight random variations to simulate a hand-drawn look,
        making each image unique while maintaining consistency.

        Args:
            input_path: Path to the input image
            output_path: Path for the output image
            border_width: Width of the border (uses default if not specified)
            wobble_amount: Maximum pixels of random variation (0 = straight lines)
            seed: Random seed for reproducible results

        Returns:
            Path to the processed image
        """
        if seed is not None:
            random.seed(seed)

        input_path = Path(input_path)
        output_path = Path(output_path)
        border_width = border_width or self.border_width

        # Open and process the image
        img = Image.open(input_path)

        # Convert to RGBA for transparency support
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Make it square (center crop if needed)
        img = self._make_square(img)

        # Resize to standard size
        img = img.resize(self.DEFAULT_OUTPUT_SIZE, Image.Resampling.LANCZOS)

        width, height = img.size

        # Create a new image with space for the border
        border_padding = border_width + int(wobble_amount) + 2
        new_size = (width + 2 * border_padding, height + 2 * border_padding)
        new_img = Image.new("RGBA", new_size, (255, 255, 255, 0))

        # Paste the original image centered
        new_img.paste(img, (border_padding, border_padding))

        # Draw the hand-drawn border
        draw = ImageDraw.Draw(new_img)
        self._draw_wobbly_border(
            draw,
            offset=border_padding,
            width=width,
            height=height,
            stroke_width=border_width,
            wobble=wobble_amount,
        )

        # Save the result
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to RGB if saving as JPEG
        if output_path.suffix.lower() in [".jpg", ".jpeg"]:
            # Create white background
            rgb_img = Image.new("RGB", new_img.size, (255, 255, 255))
            rgb_img.paste(new_img, mask=new_img.split()[3] if new_img.mode == "RGBA" else None)
            rgb_img.save(output_path, quality=95)
        else:
            new_img.save(output_path)

        logger.info(f"Processed image saved to {output_path}")
        return output_path

    def _make_square(self, img: Image.Image) -> Image.Image:
        """Center crop an image to make it square."""
        width, height = img.size

        if width == height:
            return img

        # Determine crop dimensions
        size = min(width, height)
        left = (width - size) // 2
        top = (height - size) // 2
        right = left + size
        bottom = top + size

        return img.crop((left, top, right, bottom))

    def _draw_wobbly_border(
        self,
        draw: ImageDraw.ImageDraw,
        offset: int,
        width: int,
        height: int,
        stroke_width: int,
        wobble: float,
    ):
        """
        Draw a border with hand-drawn wobble effect.

        Uses multiple overlapping lines with slight variations to create
        a natural, hand-drawn appearance.
        """
        # Draw multiple passes for a more natural look
        for pass_num in range(3):
            # Slightly vary the stroke width for each pass
            current_width = stroke_width - pass_num

            if current_width <= 0:
                continue

            # Generate wobbly points for each edge
            # Top edge
            top_points = self._generate_wobbly_line(
                start=(offset, offset),
                end=(offset + width, offset),
                wobble=wobble,
                step=4,
            )

            # Right edge
            right_points = self._generate_wobbly_line(
                start=(offset + width, offset),
                end=(offset + width, offset + height),
                wobble=wobble,
                step=4,
            )

            # Bottom edge
            bottom_points = self._generate_wobbly_line(
                start=(offset + width, offset + height),
                end=(offset, offset + height),
                wobble=wobble,
                step=4,
            )

            # Left edge
            left_points = self._generate_wobbly_line(
                start=(offset, offset + height),
                end=(offset, offset),
                wobble=wobble,
                step=4,
            )

            # Draw the lines
            for points in [top_points, right_points, bottom_points, left_points]:
                if len(points) >= 2:
                    draw.line(points, fill=self.border_color, width=current_width)

    def _generate_wobbly_line(
        self,
        start: tuple,
        end: tuple,
        wobble: float,
        step: int = 4,
    ) -> list:
        """
        Generate points for a wobbly line between two points.

        Args:
            start: Starting point (x, y)
            end: Ending point (x, y)
            wobble: Maximum random offset in pixels
            step: Distance between points

        Returns:
            List of (x, y) tuples
        """
        points = []

        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.sqrt(dx * dx + dy * dy)

        if length == 0:
            return [start, end]

        # Number of segments
        num_points = max(2, int(length / step))

        for i in range(num_points + 1):
            t = i / num_points

            # Base position
            x = start[0] + t * dx
            y = start[1] + t * dy

            # Add wobble (perpendicular to line direction)
            if 0 < i < num_points:  # Don't wobble endpoints
                # Perpendicular direction
                perp_x = -dy / length
                perp_y = dx / length

                # Random offset with smooth variation
                offset = random.uniform(-wobble, wobble)

                # Apply Perlin-like smoothing using sin waves
                smooth_factor = math.sin(t * math.pi) * 0.5 + 0.5
                offset *= smooth_factor

                x += perp_x * offset
                y += perp_y * offset

            points.append((x, y))

        return points

    def process_photo(
        self,
        input_path: Union[str, Path],
        output_dir: Union[str, Path],
        member_id: str,
    ) -> Path:
        """
        Process a member's photo for the website.

        Args:
            input_path: Path to the original photo
            output_dir: Directory to save processed photos
            member_id: Unique identifier for the member (used in filename)

        Returns:
            Path to the processed photo
        """
        input_path = Path(input_path)
        output_dir = Path(output_dir)

        # Generate output filename
        output_filename = f"{member_id}_bordered.png"
        output_path = output_dir / output_filename

        # Process with a random but reproducible seed based on member_id
        seed = hash(member_id) % (2**32)

        return self.add_hand_drawn_border(
            input_path=input_path,
            output_path=output_path,
            seed=seed,
        )

    def validate_image(self, image_path: Union[str, Path]) -> tuple[bool, Optional[str]]:
        """
        Validate that an image is suitable for processing.

        Args:
            image_path: Path to the image file

        Returns:
            Tuple of (is_valid, error_message)
        """
        image_path = Path(image_path)

        if not image_path.exists():
            return False, f"Image file not found: {image_path}"

        try:
            with Image.open(image_path) as img:
                width, height = img.size

                # Check minimum size
                if width < 200 or height < 200:
                    return False, f"Image too small ({width}x{height}). Minimum is 200x200."

                # Check format
                if img.format not in ["JPEG", "PNG", "GIF", "WEBP"]:
                    return False, f"Unsupported image format: {img.format}"

                return True, None

        except Exception as e:
            return False, f"Error reading image: {e}"
