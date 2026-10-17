"""
Tests for app/services/document_processing/preprocessor.py --
StandardPreprocessor's real (Pillow/numpy) image preprocessing:
orientation correction, projection-profile deskew, denoise, contrast
normalization, and resize. Replaces the old no-op stub that always
returned the input bytes unchanged with preprocessing_json=None.
"""

import io

import pytest
from PIL import Image, ImageDraw

from app.services.document_processing.preprocessor import StandardPreprocessor


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_output_is_valid_rgb_png():
    img = Image.new("RGB", (1000, 1000), "white")
    out_bytes, metadata = StandardPreprocessor().preprocess_image(_png_bytes(img))

    decoded = Image.open(io.BytesIO(out_bytes))
    assert decoded.format == "PNG"
    assert decoded.mode == "RGB"
    assert metadata["output_width_px"] == decoded.width
    assert metadata["output_height_px"] == decoded.height


def test_always_denoises_and_normalizes_contrast():
    img = Image.new("RGB", (1000, 1000), "white")
    _, metadata = StandardPreprocessor().preprocess_image(_png_bytes(img))
    assert metadata["denoised"] is True
    assert metadata["contrast_normalized"] is True


def test_exif_orientation_is_corrected_and_reported():
    img = Image.new("RGB", (40, 20), "white")
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 270 -- a real orientation tag, not "normal"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())

    _, metadata = StandardPreprocessor().preprocess_image(buf.getvalue())
    assert metadata["orientation_corrected"] is True


def test_no_exif_orientation_tag_is_not_reported_as_corrected():
    img = Image.new("RGB", (1000, 1000), "white")
    _, metadata = StandardPreprocessor().preprocess_image(_png_bytes(img))
    assert metadata["orientation_corrected"] is False


def test_blank_page_is_never_deskewed():
    """A page with no real content gives the projection-profile search
    no signal to measure -- it must not fit noise and rotate anyway."""
    img = Image.new("RGB", (1000, 1000), "white")
    _, metadata = StandardPreprocessor().preprocess_image(_png_bytes(img))
    assert metadata["deskewed"] is False
    assert metadata["deskew_angle_degrees"] == 0.0


def test_skewed_text_like_page_is_detected_and_corrected():
    base = Image.new("L", (800, 1000), 255)
    draw = ImageDraw.Draw(base)
    for y in range(50, 950, 30):
        draw.rectangle([50, y, 750, y + 10], fill=0)
    skewed = base.rotate(4, resample=Image.BICUBIC, expand=True, fillcolor=255).convert("RGB")

    _, metadata = StandardPreprocessor().preprocess_image(_png_bytes(skewed))
    assert metadata["deskewed"] is True
    # The detector should find a correcting angle close to -4 degrees
    # (undoing the +4 degree skew that was applied above).
    assert -6.0 <= metadata["deskew_angle_degrees"] <= -2.0


def test_small_image_is_upscaled():
    img = Image.new("RGB", (100, 50), "white")
    _, metadata = StandardPreprocessor().preprocess_image(_png_bytes(img))
    assert metadata["resized"] is True
    assert max(metadata["output_width_px"], metadata["output_height_px"]) > 1000


def test_huge_image_is_downscaled():
    img = Image.new("RGB", (6000, 3000), "white")
    _, metadata = StandardPreprocessor().preprocess_image(_png_bytes(img))
    assert metadata["resized"] is True
    assert max(metadata["output_width_px"], metadata["output_height_px"]) <= 3000


def test_reasonably_sized_image_is_not_resized():
    img = Image.new("RGB", (1500, 2000), "white")
    _, metadata = StandardPreprocessor().preprocess_image(_png_bytes(img))
    assert metadata["resized"] is False
    assert metadata["output_width_px"] == 1500
    assert metadata["output_height_px"] == 2000
