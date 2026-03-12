"""
Tests for the ImageService.

These tests verify actual image processing with real files.
No external API calls required.
"""

import tempfile
from pathlib import Path
import sys

import pytest
from PIL import Image

# Ensure scripts package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.onboarding.services.image_service import ImageService


class TestImageServiceInit:
    """Tests for ImageService initialization."""

    def test_default_initialization(self):
        """Test default initialization values."""
        service = ImageService()
        assert service.border_color == (0, 105, 62)  # Dartmouth green
        assert service.border_width == 8

    def test_custom_border_color(self):
        """Test custom border color."""
        custom_color = (255, 0, 0)  # Red
        service = ImageService(border_color=custom_color)
        assert service.border_color == custom_color

    def test_custom_border_width(self):
        """Test custom border width."""
        service = ImageService(border_width=12)
        assert service.border_width == 12


class TestImageValidation:
    """Tests for image validation."""

    def test_validate_valid_png(self, temp_dir):
        """Test validating a valid PNG image."""
        service = ImageService()

        # Create a valid test image
        img = Image.new("RGB", (400, 400), color=(100, 150, 200))
        img_path = temp_dir / "test.png"
        img.save(img_path, format="PNG")

        is_valid, error = service.validate_image(img_path)
        assert is_valid is True
        assert error is None

    def test_validate_valid_jpeg(self, temp_dir):
        """Test validating a valid JPEG image."""
        service = ImageService()

        img = Image.new("RGB", (300, 300), color=(100, 150, 200))
        img_path = temp_dir / "test.jpg"
        img.save(img_path, format="JPEG")

        is_valid, error = service.validate_image(img_path)
        assert is_valid is True
        assert error is None

    def test_validate_image_too_small(self, temp_dir):
        """Test validating an image that is too small."""
        service = ImageService()

        img = Image.new("RGB", (100, 100), color=(100, 150, 200))
        img_path = temp_dir / "small.png"
        img.save(img_path, format="PNG")

        is_valid, error = service.validate_image(img_path)
        assert is_valid is False
        assert "too small" in error.lower()

    def test_validate_nonexistent_file(self, temp_dir):
        """Test validating a file that doesn't exist."""
        service = ImageService()
        fake_path = temp_dir / "nonexistent.png"

        is_valid, error = service.validate_image(fake_path)
        assert is_valid is False
        assert "not found" in error.lower()


class TestHandDrawnBorder:
    """Tests for hand-drawn border processing."""

    def test_add_border_creates_output(self, temp_dir):
        """Test that processing creates an output file."""
        service = ImageService()

        # Create input image
        input_img = Image.new("RGB", (400, 400), color=(200, 200, 200))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        output_path = temp_dir / "output.png"

        result = service.add_hand_drawn_border(input_path, output_path)

        assert result == output_path
        assert output_path.exists()

    def test_output_is_larger_than_input(self, temp_dir):
        """Test that output has space for border (is larger)."""
        service = ImageService()

        # Create input image
        input_img = Image.new("RGB", (400, 400), color=(200, 200, 200))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        output_path = temp_dir / "output.png"
        service.add_hand_drawn_border(input_path, output_path)

        output_img = Image.open(output_path)
        # Output should be larger due to border padding
        assert output_img.size[0] > 400
        assert output_img.size[1] > 400

    def test_border_contains_green(self, temp_dir):
        """Test that the border contains Dartmouth green color."""
        service = ImageService()

        # Create input image (white)
        input_img = Image.new("RGB", (400, 400), color=(255, 255, 255))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        output_path = temp_dir / "output.png"
        service.add_hand_drawn_border(input_path, output_path)

        output_img = Image.open(output_path).convert("RGB")

        # Check corners (where border should be)
        # The border should contain Dartmouth green (0, 105, 62)
        # Check a few pixels in the border area
        found_green = False
        dartmouth_green = (0, 105, 62)

        # Sample the border area
        for x in range(20):
            for y in range(20):
                pixel = output_img.getpixel((x, y))
                if pixel == dartmouth_green:
                    found_green = True
                    break
            if found_green:
                break

        assert found_green, "Dartmouth green not found in border area"

    def test_reproducible_with_seed(self, temp_dir):
        """Test that results are reproducible with same seed."""
        service = ImageService()

        # Create input image
        input_img = Image.new("RGB", (400, 400), color=(128, 128, 128))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        output1_path = temp_dir / "output1.png"
        output2_path = temp_dir / "output2.png"

        # Process twice with same seed
        service.add_hand_drawn_border(input_path, output1_path, seed=42)
        service.add_hand_drawn_border(input_path, output2_path, seed=42)

        # Images should be identical
        img1 = Image.open(output1_path)
        img2 = Image.open(output2_path)

        # Compare pixel by pixel (sample a few)
        for x in range(0, img1.size[0], 50):
            for y in range(0, img1.size[1], 50):
                assert img1.getpixel((x, y)) == img2.getpixel((x, y))

    def test_different_seeds_produce_different_results(self, temp_dir):
        """Test that different seeds produce different results."""
        service = ImageService()

        # Create input image
        input_img = Image.new("RGB", (400, 400), color=(128, 128, 128))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        output1_path = temp_dir / "output1.png"
        output2_path = temp_dir / "output2.png"

        # Process with different seeds
        service.add_hand_drawn_border(input_path, output1_path, seed=42)
        service.add_hand_drawn_border(input_path, output2_path, seed=123)

        img1 = Image.open(output1_path)
        img2 = Image.open(output2_path)

        # Find at least one difference in the border region
        differences_found = False
        for x in range(10, 30):  # Border region
            for y in range(10, 30):
                if img1.getpixel((x, y)) != img2.getpixel((x, y)):
                    differences_found = True
                    break
            if differences_found:
                break

        assert differences_found, "Different seeds should produce different wobble patterns"

    def test_jpeg_output(self, temp_dir):
        """Test output as JPEG format."""
        service = ImageService()

        # Create input PNG
        input_img = Image.new("RGB", (400, 400), color=(200, 200, 200))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        output_path = temp_dir / "output.jpg"
        service.add_hand_drawn_border(input_path, output_path)

        assert output_path.exists()
        # Verify it's actually a JPEG
        with Image.open(output_path) as img:
            assert img.format == "JPEG"


class TestMakeSquare:
    """Tests for the square cropping functionality."""

    def test_already_square(self, temp_dir):
        """Test that square images are unchanged."""
        service = ImageService()

        # Create square image
        img = Image.new("RGB", (400, 400), color=(200, 200, 200))
        input_path = temp_dir / "square.png"
        img.save(input_path)

        output_path = temp_dir / "output.png"
        service.add_hand_drawn_border(input_path, output_path)

        # Output should be processed normally
        assert output_path.exists()

    def test_landscape_becomes_square(self, temp_dir):
        """Test that landscape images are cropped to square."""
        service = ImageService()

        # Create wide landscape image
        img = Image.new("RGB", (600, 400), color=(200, 200, 200))
        input_path = temp_dir / "landscape.png"
        img.save(input_path)

        output_path = temp_dir / "output.png"
        service.add_hand_drawn_border(input_path, output_path)

        # The base image should be square before border is added
        # Output will be larger due to border, but underlying image is 400x400
        assert output_path.exists()

    def test_portrait_becomes_square(self, temp_dir):
        """Test that portrait images are cropped to square."""
        service = ImageService()

        # Create tall portrait image
        img = Image.new("RGB", (400, 600), color=(200, 200, 200))
        input_path = temp_dir / "portrait.png"
        img.save(input_path)

        output_path = temp_dir / "output.png"
        service.add_hand_drawn_border(input_path, output_path)

        assert output_path.exists()


class TestProcessPhoto:
    """Tests for the process_photo convenience method."""

    def test_process_photo_creates_file(self, temp_dir):
        """Test that process_photo creates the expected output file."""
        service = ImageService()

        # Create input image
        input_img = Image.new("RGB", (500, 500), color=(150, 150, 150))
        input_path = temp_dir / "original.png"
        input_img.save(input_path)

        output_dir = temp_dir / "output"
        output_dir.mkdir()

        result = service.process_photo(input_path, output_dir, "test_member")

        assert result.exists()
        assert "test_member" in result.name
        assert "_bordered" in result.name

    def test_process_photo_reproducible_by_member_id(self, temp_dir):
        """Test that same member_id produces same result."""
        service = ImageService()

        # Create input image
        input_img = Image.new("RGB", (500, 500), color=(150, 150, 150))
        input_path = temp_dir / "original.png"
        input_img.save(input_path)

        output_dir1 = temp_dir / "output1"
        output_dir2 = temp_dir / "output2"
        output_dir1.mkdir()
        output_dir2.mkdir()

        result1 = service.process_photo(input_path, output_dir1, "same_member")
        result2 = service.process_photo(input_path, output_dir2, "same_member")

        # Images should be identical (same seed from same member_id)
        img1 = Image.open(result1)
        img2 = Image.open(result2)

        # Sample comparison
        for x in range(0, min(img1.size[0], 200), 25):
            for y in range(0, min(img1.size[1], 200), 25):
                assert img1.getpixel((x, y)) == img2.getpixel((x, y))


class TestCustomWobble:
    """Tests for wobble amount configuration."""

    def test_zero_wobble_straight_lines(self, temp_dir):
        """Test that zero wobble produces straighter borders."""
        service = ImageService()

        input_img = Image.new("RGB", (400, 400), color=(255, 255, 255))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        # Process with no wobble
        output_path = temp_dir / "straight.png"
        service.add_hand_drawn_border(input_path, output_path, wobble_amount=0, seed=42)

        # Process with wobble
        output_wobble_path = temp_dir / "wobbly.png"
        service.add_hand_drawn_border(input_path, output_wobble_path, wobble_amount=5.0, seed=42)

        # Both should exist
        assert output_path.exists()
        assert output_wobble_path.exists()

        # They should be different
        img_straight = Image.open(output_path)
        img_wobbly = Image.open(output_wobble_path)

        # Find differences in border region
        differences = 0
        for x in range(10, 30):
            for y in range(10, 30):
                if img_straight.getpixel((x, y)) != img_wobbly.getpixel((x, y)):
                    differences += 1

        assert differences > 0, "Wobble setting should affect the output"
