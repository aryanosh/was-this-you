"""Stage 1: face detection & encoding.

Uses `face_recognition` (dlib HOG detector + a 128-d ResNet embedding model)
to turn an uploaded image into one or more face encodings. Every failure mode
raises a specific exception with a human-readable message instead of letting
a raw exception/crash reach the caller.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import face_recognition
import numpy as np
from PIL import Image, UnidentifiedImageError


class FaceDetectionError(Exception):
    """Base class for all Stage 1 errors."""


class UnsupportedImageError(FaceDetectionError):
    """The uploaded bytes could not be decoded as an image."""


class NoFaceFoundError(FaceDetectionError):
    """The image decoded fine but no face was detected in it."""


@dataclass
class DetectedFace:
    index: int
    top: int
    right: int
    bottom: int
    left: int
    encoding: list[float]

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "box": {"top": self.top, "right": self.right, "bottom": self.bottom, "left": self.left},
            "encoding": self.encoding,
        }


def _load_image_as_array(image_bytes: bytes) -> np.ndarray:
    if not image_bytes:
        raise UnsupportedImageError("Uploaded file is empty.")
    try:
        pil_image = Image.open(io.BytesIO(image_bytes))
        pil_image.load()  # force full decode now, not lazily later
    except UnidentifiedImageError as exc:
        raise UnsupportedImageError(
            "Could not decode the uploaded file as an image. "
            "Supported formats include JPEG, PNG, BMP, and WEBP."
        ) from exc
    except OSError as exc:
        raise UnsupportedImageError(
            f"Image file appears to be corrupted or truncated: {exc}"
        ) from exc

    try:
        rgb_image = pil_image.convert("RGB")
    except Exception as exc:  # pragma: no cover - defensive, PIL convert rarely fails
        raise UnsupportedImageError(f"Could not convert image to RGB: {exc}") from exc

    return np.array(rgb_image)


def detect_faces(image_bytes: bytes) -> list[DetectedFace]:
    """Detect and encode every face in the image.

    Returns a list of DetectedFace (may contain more than one entry).
    Raises UnsupportedImageError if the bytes aren't a decodable image, or
    NoFaceFoundError if the image decodes fine but contains zero faces.
    Never returns an empty list -- that case always raises.
    """
    image_array = _load_image_as_array(image_bytes)

    try:
        locations = face_recognition.face_locations(image_array, model="hog")
    except Exception as exc:
        raise FaceDetectionError(f"Face detection failed unexpectedly: {exc}") from exc

    if not locations:
        raise NoFaceFoundError("No face was detected in the uploaded image.")

    try:
        encodings = face_recognition.face_encodings(image_array, known_face_locations=locations)
    except Exception as exc:
        raise FaceDetectionError(f"Face encoding failed unexpectedly: {exc}") from exc

    faces = []
    for idx, ((top, right, bottom, left), encoding) in enumerate(zip(locations, encodings)):
        faces.append(
            DetectedFace(
                index=idx,
                top=top,
                right=right,
                bottom=bottom,
                left=left,
                encoding=encoding.tolist(),
            )
        )
    return faces


def crop_face(image_bytes: bytes, face: DetectedFace, padding_ratio: float = 0.5) -> bytes:
    """Crop the image to the given face's bounding box, padded by
    `padding_ratio` on each side (clamped to the image bounds) so the crop
    includes forehead/chin/some context instead of just the tight box.
    Returns the crop re-encoded as JPEG bytes.
    """
    pil_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = pil_image.size

    box_width = face.right - face.left
    box_height = face.bottom - face.top
    pad_x = int(box_width * padding_ratio)
    pad_y = int(box_height * padding_ratio)

    left = max(0, face.left - pad_x)
    top = max(0, face.top - pad_y)
    right = min(width, face.right + pad_x)
    bottom = min(height, face.bottom + pad_y)

    cropped = pil_image.crop((left, top, right, bottom))
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def compare_faces(encoding_a: list[float], encoding_b: list[float]) -> float:
    """Cosine similarity between two 128-d face encodings, returned as a
    0-100% score. 100% means identical vectors; face_recognition encodings
    from the same person's face typically land well above 90% here."""
    a = np.asarray(encoding_a, dtype=np.float64)
    b = np.asarray(encoding_b, dtype=np.float64)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    cosine_sim = float(np.dot(a, b) / (norm_a * norm_b))
    # Cosine similarity for these encodings is already close to [0, 1] for
    # plausible face pairs, but clamp defensively before scaling to a percent.
    cosine_sim = max(-1.0, min(1.0, cosine_sim))
    return round(((cosine_sim + 1.0) / 2.0) * 100.0, 1)
