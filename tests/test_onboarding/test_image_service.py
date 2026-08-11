"""
Tests for the ImageService.

These tests verify actual image processing with real files.
No external API calls required.
"""

import tempfile
from pathlib import Path
import sys

import pytest
import hashlib

from PIL import Image

# Ensure scripts package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cdl_bot.services.image_service import ImageService


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
        """The border is drawn in the website's primary green.

        The colour comes from the SVG template, so it is the website's
        --primary-green rgb(0, 112, 60) after rasterising, not the
        rgb(0, 105, 62) the removed PIL fallback painted. Antialiasing means
        no exact match exists anywhere, so this samples the whole image with a
        tolerance instead of probing one corner -- the corners are
        transparent, which is why the old exact-match probe could not pass.
        """
        service = ImageService()

        input_img = Image.new("RGB", (400, 400), color=(255, 255, 255))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        output_path = temp_dir / "output.png"
        service.add_hand_drawn_border(input_path, output_path)

        output_img = Image.open(output_path).convert("RGBA")
        target = (0, 112, 60)
        green = sum(
            1 for r, g, b, a in output_img.getdata()
            if a > 200
            and abs(r - target[0]) < 25
            and abs(g - target[1]) < 25
            and abs(b - target[2]) < 25
        )

        assert green > 1000, (
            f"only {green} green pixels; the border does not look drawn"
        )

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

    def test_different_seeds_can_produce_different_borders(self, temp_dir):
        """Different seeds select different designs from the SVG template.

        The template holds a handful of borders, so two given seeds may well
        collide. Sampling several and requiring more than one distinct result
        is the honest assertion.
        """
        service = ImageService()

        input_img = Image.new("RGB", (400, 400), color=(128, 128, 128))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        digests = set()
        for seed in range(12):
            output_path = temp_dir / f"seeded_{seed}.png"
            service.add_hand_drawn_border(input_path, output_path, seed=seed)
            digests.add(hashlib.md5(output_path.read_bytes()).hexdigest())

        assert len(digests) > 1, "every seed produced the same border"

    def test_unseeded_calls_are_random(self, temp_dir):
        """Without a seed, add_borders.py picks a border at random.

        This is why the callers pass one: re-running onboarding would
        otherwise hand a member a different border every time.
        """
        service = ImageService()

        input_img = Image.new("RGB", (400, 400), color=(128, 128, 128))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        digests = set()
        for i in range(12):
            output_path = temp_dir / f"unseeded_{i}.png"
            service.add_hand_drawn_border(input_path, output_path)
            digests.add(hashlib.md5(output_path.read_bytes()).hexdigest())

        assert len(digests) > 1, "unseeded output was identical every time"

    def test_non_png_output_is_refused(self, temp_dir):
        """Borders have transparent corners, so only PNG can hold the result.

        The output used to be written to whatever name was asked for, which
        produced a PNG wearing a .jpg extension.
        """
        service = ImageService()

        input_img = Image.new("RGB", (400, 400), color=(200, 200, 200))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        output_path = temp_dir / "output.jpg"
        with pytest.raises(ValueError, match="must be a .png"):
            service.add_hand_drawn_border(input_path, output_path)

        assert not output_path.exists()

    def test_output_is_png_with_transparency(self, temp_dir):
        service = ImageService()

        input_img = Image.new("RGB", (400, 400), color=(200, 200, 200))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        output_path = temp_dir / "output.png"
        service.add_hand_drawn_border(input_path, output_path)

        with Image.open(output_path) as img:
            assert img.format == "PNG"
            assert img.mode == "RGBA"
            # The doodle borders do not fill the square, so the corner is clear.
            assert img.getpixel((0, 0))[3] == 0


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


class TestRemovedApiIsGone:
    """process_photo and the wobble knobs went with the PIL fallback.

    ffa04f2 replaced the hand-rolled border with the website's real SVG
    templates and deleted both, but left these tests behind asserting the old
    behaviour. Restoring either would reintroduce borders that do not match
    context-lab.com/people, so what is worth pinning is that they stay gone
    and that a caller passing a dead argument is told, rather than ignored.
    """

    def test_process_photo_is_not_resurrected(self):
        assert not hasattr(ImageService(), "process_photo")

    def test_wobble_amount_is_rejected(self, temp_dir):
        service = ImageService()
        input_img = Image.new("RGB", (400, 400), color=(255, 255, 255))
        input_path = temp_dir / "input.png"
        input_img.save(input_path)

        # It used to vanish into **kwargs, so a caller tuning a removed knob
        # got silence and no effect.
        with pytest.raises(TypeError):
            service.add_hand_drawn_border(
                input_path, temp_dir / "out.png", wobble_amount=5.0
            )


class TestSeedReachesTheScript:
    """The seed has to reach add_borders.py to mean anything.

    add_borders.py chooses with random.choice, so before --seed existed the
    parameter was accepted and discarded: six runs at seed=42 gave four
    different images, and the bot's seed=hash(user_id) promised a stability
    it never had.
    """

    def test_same_seed_is_byte_identical(self, temp_dir):
        service = ImageService()
        input_img = Image.new("RGB", (400, 400), color=(150, 150, 150))
        input_path = temp_dir / "original.png"
        input_img.save(input_path)

        digests = set()
        for i in range(5):
            output_path = temp_dir / f"same_{i}.png"
            service.add_hand_drawn_border(input_path, output_path, seed=42)
            digests.add(hashlib.md5(output_path.read_bytes()).hexdigest())

        assert len(digests) == 1, f"seed=42 gave {len(digests)} different images"

    def test_a_member_keeps_their_border_across_runs(self, temp_dir):
        """What the callers actually want: seed=hash(user_id) is stable."""
        service = ImageService()
        input_img = Image.new("RGB", (500, 500), color=(150, 150, 150))
        input_path = temp_dir / "original.png"
        input_img.save(input_path)

        member_seed = hash("U123456") % (2 ** 31)
        first = temp_dir / "first.png"
        second = temp_dir / "second.png"
        service.add_hand_drawn_border(input_path, first, seed=member_seed)
        service.add_hand_drawn_border(input_path, second, seed=member_seed)

        assert first.read_bytes() == second.read_bytes()
