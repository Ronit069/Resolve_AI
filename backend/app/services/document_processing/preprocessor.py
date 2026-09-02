import abc
from typing import Dict, Any

class PreprocessingInterface(abc.ABC):
    """
    Abstract interface for document preprocessing.
    Module D specification: orientation correction, deskew, safe denoise,
    contrast normalization, resize where appropriate.
    """
    
    @abc.abstractmethod
    def preprocess_image(self, image_data: bytes) -> tuple[bytes, Dict[str, Any]]:
        """
        Process the image and return the processed bytes along with metadata.
        The metadata is stored in document_pages.preprocessing_json.
        """
        pass

class StandardPreprocessor(PreprocessingInterface):
    """
    Default stub implementation. In a production environment, this would
    use OpenCV or similar to actually perform deskew and denoise.
    """
    def preprocess_image(self, image_data: bytes) -> tuple[bytes, Dict[str, Any]]:
        # Deterministic preprocessing mock: returns original image
        metadata = {
            "orientation_corrected": False,
            "deskewed": False,
            "denoised": False,
            "contrast_normalized": False,
            "resized": False,
            "note": "StandardPreprocessor stub active"
        }
        return image_data, metadata
