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


def compare_faces(encoding_a: list[float], encoding_b: list[float], match_threshold: float = 0.6) -> float:
    """Biometric similarity between two 128-d face_recognition encodings, as
    a 0-100% confidence score.

    face_recognition's encodings are trained around Euclidean distance, not
    cosine similarity -- the library's own `compare_faces()` helper treats
    distance <= 0.6 as "the same person" by default (this is the standard,
    widely-documented threshold for this exact model). Cosine similarity on
    this embedding space is a different, uncalibrated metric that can report
    misleadingly high scores for two different people's faces.

    This instead computes the real Euclidean distance and converts it to a
    percentage using a two-piece curve (the same shape commonly used by the
    face_recognition community for exactly this purpose) so the match
    threshold itself lands at 50%, confidently-same faces land solidly above
    80%, and clearly-different faces drop toward 0%.
    """
    a = np.asarray(encoding_a, dtype=np.float64)
    b = np.asarray(encoding_b, dtype=np.float64)
    distance = float(np.linalg.norm(a - b))

    if distance > match_threshold:
        spread = 1.0 - match_threshold
        confidence = (1.0 - distance) / (spread * 2.0)
    else:
        spread = match_threshold
        linear_val = 1.0 - (distance / (spread * 2.0))
        confidence = linear_val + (1.0 - linear_val) * ((linear_val - 0.5) * 2.0) ** 0.2

    confidence = max(0.0, min(1.0, confidence))
    return round(confidence * 100.0, 1)
