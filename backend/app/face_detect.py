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
