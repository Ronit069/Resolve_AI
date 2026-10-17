"""
Issue #8 — real OCR engine behind OCRAdapterInterface.

DeterministicOCRAdapter stays the default (settings.OCR_ENGINE="deterministic")
so every pre-existing test keeps hitting canned output. These tests cover the
new TesseractOCRAdapter and the settings.OCR_ENGINE-driven factory:
  - pure grouping/normalization logic against synthetic Tesseract-shaped data
    (no binary required)
  - error mapping (missing artifact, download failure, timeout, bad engine
    output) to the same IntelligenceError codes DeterministicOCRAdapter uses
  - one real end-to-end run against the actual tesseract-ocr binary, skipped
    if it isn't installed on the machine running the tests
  - the factory picks the adapter class settings.OCR_ENGINE names
"""
import io
import shutil
from unittest.mock import patch

import pytest

from app.services.document_processing.ocr_adapter import (
    DeterministicOCRAdapter,
    TesseractOCRAdapter,
    get_ocr_adapter,
    validate_ocr_result,
)
from app.services.intelligence import IntelligenceError

TESSERACT_INSTALLED = shutil.which("tesseract") is not None


# ---------------------------------------------------------------------------
# Pure grouping/normalization logic -- no tesseract binary, no network.
# ---------------------------------------------------------------------------
def _fake_tesseract_data(rows):
    """rows: list of (text, conf, left, top, width, height, block, par, line)."""
    data = {k: [] for k in ("text", "conf", "left", "top", "width", "height", "block_num", "par_num", "line_num")}
    for text, conf, left, top, w, h, block, par, line in rows:
        data["text"].append(text)
        data["conf"].append(conf)
        data["left"].append(left)
        data["top"].append(top)
        data["width"].append(w)
        data["height"].append(h)
        data["block_num"].append(block)
        data["par_num"].append(par)
        data["line_num"].append(line)
    return data


def test_to_standard_result_groups_words_into_lines_and_normalizes_confidence():
    data = _fake_tesseract_data([
        ("Invoice", 91.0, 20, 20, 60, 15, 1, 1, 1),
        ("Number:", 89.0, 85, 20, 50, 15, 1, 1, 1),
        ("Total:", 93.0, 20, 50, 40, 15, 1, 1, 2),
    ])
    result = TesseractOCRAdapter._to_standard_result(data, width=800, height=600, page_number=1, source_reference="key1")

    assert result["page_number"] == 1
    assert result["source_reference"] == "key1"
    assert len(result["layout_blocks"]) == 2  # two lines
    line1 = result["layout_blocks"][0]
    assert line1["text"] == "Invoice Number:"
    assert line1["confidence"] == pytest.approx((91.0 + 89.0) / 2 / 100.0)
    assert line1["block_type"] == "text"
    assert line1["bbox"] == [20, 20, 135, 35]
    assert "Total:" in result["text"]
    validate_ocr_result(result, 800, 600)


def test_to_standard_result_filters_non_text_rows():
    # Tesseract marks block/line/page-level summary rows with conf == -1.
    data = _fake_tesseract_data([
        ("", -1.0, 0, 0, 800, 600, 1, 0, 0),
        ("Hello", 88.0, 10, 10, 40, 10, 1, 1, 1),
    ])
    result = TesseractOCRAdapter._to_standard_result(data, width=800, height=600, page_number=1, source_reference="key2")
    assert len(result["layout_blocks"]) == 1
    assert result["layout_blocks"][0]["text"] == "Hello"


def test_to_standard_result_empty_page_returns_empty_blocks_and_zero_confidence():
    data = _fake_tesseract_data([])
    result = TesseractOCRAdapter._to_standard_result(data, width=800, height=600, page_number=1, source_reference="key3")
    assert result["text"] == ""
    assert result["confidence"] == 0.0
    assert result["layout_blocks"] == []
    validate_ocr_result(result, 800, 600)


def test_to_standard_result_clips_bbox_to_page_bounds():
    # A word whose box overruns the page (preprocessing rounding) must be
    # clipped, never raise OCR_INVALID_OUTPUT downstream.
    data = _fake_tesseract_data([
        ("Edge", 80.0, 790, 590, 50, 50, 1, 1, 1),
    ])
    result = TesseractOCRAdapter._to_standard_result(data, width=800, height=600, page_number=1, source_reference="key4")
    bbox = result["layout_blocks"][0]["bbox"]
    assert bbox[2] <= 800
    assert bbox[3] <= 600
    validate_ocr_result(result, 800, 600)


# ---------------------------------------------------------------------------
# Error mapping -- same IntelligenceError codes DeterministicOCRAdapter uses.
# ---------------------------------------------------------------------------
def test_missing_artifact_key_raises_ocr_invalid_output():
    adapter = TesseractOCRAdapter()
    with pytest.raises(IntelligenceError) as exc:
        adapter.perform_ocr("", 800, 600, 1)
    assert exc.value.code == "OCR_INVALID_OUTPUT"


def test_storage_download_failure_raises_ocr_invalid_output():
    adapter = TesseractOCRAdapter()
    with patch("app.core.storage.storage_client.download_file", side_effect=RuntimeError("boom")):
        with pytest.raises(IntelligenceError) as exc:
            adapter.perform_ocr("derived/case/doc/job/page_1.png", 800, 600, 1)
    assert exc.value.code == "OCR_INVALID_OUTPUT"


def test_tesseract_timeout_raises_ocr_timeout():
    adapter = TesseractOCRAdapter(timeout_seconds=1)
    fake_png = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (10, 10), color="white").save(fake_png, format="PNG")

    with patch("app.core.storage.storage_client.download_file", return_value=fake_png.getvalue()):
        with patch("pytesseract.image_to_data", side_effect=RuntimeError("Tesseract process timeout")):
            with pytest.raises(IntelligenceError) as exc:
                adapter.perform_ocr("derived/case/doc/job/page_1.png", 800, 600, 1)
    assert exc.value.code == "OCR_TIMEOUT"


def test_corrupt_image_bytes_raise_ocr_invalid_output():
    adapter = TesseractOCRAdapter()
    with patch("app.core.storage.storage_client.download_file", return_value=b"not an image"):
        with pytest.raises(IntelligenceError) as exc:
            adapter.perform_ocr("derived/case/doc/job/page_1.png", 800, 600, 1)
    assert exc.value.code == "OCR_INVALID_OUTPUT"


# ---------------------------------------------------------------------------
# Factory: settings.OCR_ENGINE picks the adapter class.
# ---------------------------------------------------------------------------
def test_factory_defaults_to_deterministic():
    with patch("app.core.config.settings.OCR_ENGINE", "deterministic"):
        assert isinstance(get_ocr_adapter(), DeterministicOCRAdapter)


def test_factory_returns_tesseract_when_configured():
    with patch("app.core.config.settings.OCR_ENGINE", "tesseract"):
        assert isinstance(get_ocr_adapter(), TesseractOCRAdapter)


# ---------------------------------------------------------------------------
# Real end-to-end run against the actual tesseract-ocr binary.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not TESSERACT_INSTALLED, reason="tesseract-ocr binary not installed on this machine")
def test_real_tesseract_recognizes_rendered_invoice_text():
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=28)
    img = Image.new("RGB", (700, 220), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "Invoice Number: INV-1001", fill="black", font=font)
    draw.text((20, 90), "Total: 12500.00", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    adapter = TesseractOCRAdapter()
    with patch("app.core.storage.storage_client.download_file", return_value=buf.getvalue()):
        result = adapter.perform_ocr("derived/case/doc/job/page_1.png", 700, 220, 1)

    validate_ocr_result(result, 700, 220)
    assert "INV-1001" in result["text"]
    assert "12500.00" in result["text"]
    assert result["confidence"] > 0.5
    assert len(result["layout_blocks"]) >= 2
    for block in result["layout_blocks"]:
        assert 0.0 <= block["confidence"] <= 1.0
