import abc
import io
from typing import Any, Dict, Tuple

import numpy as np
from PIL import Image, ImageFilter, ImageOps


class PreprocessingInterface(abc.ABC):
    """
    Abstract interface for document preprocessing.
    Module D specification: orientation correction, deskew, safe denoise,
    contrast normalization, resize where appropriate.
    """

    @abc.abstractmethod
    def preprocess_image(self, image_data: bytes) -> Tuple[bytes, Dict[str, Any]]:
        """
        Process the image and return the processed bytes along with metadata.
        The metadata is stored in document_pages.preprocessing_json.
        """
        pass


# EXIF orientation tag id; value 1 means "normal", no rotation needed.
_EXIF_ORIENTATION_TAG = 0x0112

# Degrees; a measured skew smaller than this is noise, not real skew --
# rotating for it would just recompress the image for no benefit.
_MIN_SKEW_DEGREES_TO_CORRECT = 0.3
# Degrees; bounds how far the projection-profile search looks and acts as
# a sanity cap so a pathological image can never get rotated wildly.
_MAX_SKEW_SEARCH_DEGREES = 10.0
_SKEW_SEARCH_STEP_DEGREES = 0.5

# Pixels (long edge). Below this, OCR engines reliably lose accuracy on
# small text; above this, the extra resolution costs compute without
# helping OCR, so it's downscaled for throughput.
_MIN_LONG_EDGE_PX = 900
_MAX_LONG_EDGE_PX = 3000
_UPSCALE_TARGET_LONG_EDGE_PX = 1200


# Fraction of binarized "foreground" pixels a page must have for skew
# detection to mean anything. A blank/near-uniform image (no text) has
# no real projection signal -- the variance-maximizing search would
# just be picking up on noise and can land anywhere, including the
# search boundary, which is never a real measured skew.
_MIN_FOREGROUND_FRACTION = 0.005
_MAX_FOREGROUND_FRACTION = 0.6


def _detect_skew_angle(image: Image.Image) -> float:
    """
    Projection-profile skew estimation: binarizes a downscaled grayscale
    copy, then searches rotation angles in
    [-_MAX_SKEW_SEARCH_DEGREES, +_MAX_SKEW_SEARCH_DEGREES] for the one
    whose horizontal row-sum projection has the highest variance --
    text lines stacked at the true skew angle produce sharp,
    high-variance peaks/troughs; any other angle blurs them together.
    Returns 0.0 for a page with too little (or too much) foreground to
    give that signal any meaning, rather than fitting noise.
    """
    gray = image.convert("L")
    if max(gray.size) > 600:
        scale = 600 / max(gray.size)
        gray = gray.resize((max(1, int(gray.width * scale)), max(1, int(gray.height * scale))))

    arr = np.asarray(gray, dtype=np.float64)
    threshold = arr.mean()
    binary = np.where(arr < threshold, 255, 0).astype(np.uint8)

    foreground_fraction = (binary > 0).mean()
    if not (_MIN_FOREGROUND_FRACTION <= foreground_fraction <= _MAX_FOREGROUND_FRACTION):
        return 0.0

    binary_img = Image.fromarray(binary)

    best_angle = 0.0
    best_score = -1.0
    angle = -_MAX_SKEW_SEARCH_DEGREES
    while angle <= _MAX_SKEW_SEARCH_DEGREES:
        rotated = np.asarray(
            binary_img.rotate(angle, resample=Image.BILINEAR, fillcolor=0), dtype=np.float64
        )
        score = rotated.sum(axis=1).var()
        if score > best_score:
            best_score = score
            best_angle = angle
        angle += _SKEW_SEARCH_STEP_DEGREES

    return best_angle


class StandardPreprocessor(PreprocessingInterface):
    """
    Real document image preprocessing -- pure Pillow/numpy, no new
    system dependency (e.g. OpenCV). Runs before a page's image is
    handed to OCR (see document_processing/image.py and pdf.py) and
    records exactly which steps actually changed the image, rather
    than a fixed "nothing happened" stub result.
    """

    def preprocess_image(self, image_data: bytes) -> Tuple[bytes, Dict[str, Any]]:
        with Image.open(io.BytesIO(image_data)) as original:
            original.load()

            orientation_tag = original.getexif().get(_EXIF_ORIENTATION_TAG, 1)
            orientation_corrected = orientation_tag != 1
            img = ImageOps.exif_transpose(original)

        if img.mode != "RGB":
            img = img.convert("RGB")

        skew_angle = _detect_skew_angle(img)
        deskewed = abs(skew_angle) >= _MIN_SKEW_DEGREES_TO_CORRECT
        if deskewed:
            img = img.rotate(skew_angle, resample=Image.BILINEAR, expand=True, fillcolor=(255, 255, 255))

        # Safe denoise: a 3x3 median filter removes scanner speckle/salt-
        # and-pepper noise without meaningfully blurring text edges.
        img = img.filter(ImageFilter.MedianFilter(size=3))

        img = ImageOps.autocontrast(img, cutoff=1)

        long_edge = max(img.size)
        resized = False
        if long_edge < _MIN_LONG_EDGE_PX:
            scale = _UPSCALE_TARGET_LONG_EDGE_PX / long_edge
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
            resized = True
        elif long_edge > _MAX_LONG_EDGE_PX:
            scale = _MAX_LONG_EDGE_PX / long_edge
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
            resized = True

        output = io.BytesIO()
        img.save(output, format="PNG")

        metadata = {
            "orientation_corrected": orientation_corrected,
            "deskewed": deskewed,
            "deskew_angle_degrees": round(skew_angle, 2) if deskewed else 0.0,
            "denoised": True,
            "contrast_normalized": True,
            "resized": resized,
            "output_width_px": img.width,
            "output_height_px": img.height,
            "note": "StandardPreprocessor (Pillow/numpy)",
        }
        return output.getvalue(), metadata
