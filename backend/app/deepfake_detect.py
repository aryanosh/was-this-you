"""Stage 1.5: deepfake / AI-generated image detection.

Advisory only -- this never blocks the pipeline. It runs a pre-trained
HuggingFace image classifier (`dima806/deepfake_vs_real_image_detection`, a
ViT fine-tuned specifically to separate real photos from AI-generated/deepfake
ones -- the closest real, publicly available model to the
`dima806/deepfake_vs_real_faces_detection` name referenced in the plan, which
does not actually exist on the Hub) to score whether an uploaded image looks
authentic or synthetic.

The model is loaded lazily on first call and cached in-process for every
subsequent call, since loading it takes real time and doing it per-request
would be wasteful. If loading or inference fails for any reason (no internet
on first run, missing torch/transformers, corrupted weights, etc.), this
degrades to a neutral result carrying a warning instead of raising -- the
caller (pipeline.py) always gets a `DeepfakeResult` back and continues.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MODEL_NAME = "dima806/deepfake_vs_real_image_detection"

logger = logging.getLogger(__name__)


class DeepfakeDetectionError(Exception):
    """Base class for all deepfake-detection errors."""


class UnsupportedImageError(DeepfakeDetectionError):
    """The bytes given could not be decoded as an image."""


@dataclass
class DeepfakeResult:
    is_likely_real: bool
    confidence: float  # 0-1, probability the image is real (not AI-generated)
    label: str  # "Real" or "AI-Generated"
    warning: str | None = None

    def to_dict(self) -> dict:
        return {
            "is_likely_real": self.is_likely_real,
            "confidence": self.confidence,
            "label": self.label,
            "warning": self.warning,
        }


_pipeline = None
_load_failed = False


def _get_pipeline():
    """Load the HF pipeline once and cache it. Returns None if loading fails
    (missing deps, no network, etc.) -- callers must handle that."""
    global _pipeline, _load_failed
    if _pipeline is not None:
        return _pipeline
    if _load_failed:
        return None
    try:
        from transformers import pipeline as hf_pipeline

        _pipeline = hf_pipeline("image-classification", model=MODEL_NAME)
        return _pipeline
    except Exception:
        _load_failed = True
        return None


def warm_up() -> None:
    """Pre-load the deepfake detection model so the first real request doesn't
    pay the cold-load cost (~350 MB download + model init). Called once at
    server startup via the FastAPI lifespan handler."""
    logger.info("Pre-warming deepfake detection model (%s)...", MODEL_NAME)
    clf = _get_pipeline()
    if clf is not None:
        logger.info("Deepfake model loaded successfully.")
    else:
        logger.warning(
            "Deepfake model could not be loaded (missing torch/transformers, "
            "no network, or download failure). Detection will be skipped at runtime."
        )


def _neutral_result(warning: str) -> DeepfakeResult:
    return DeepfakeResult(is_likely_real=True, confidence=0.5, label="Unknown", warning=warning)


def detect_deepfake(image_bytes: bytes) -> DeepfakeResult:
    """Classify whether an image looks real or AI-generated.

    Never raises for model-availability reasons -- returns a neutral,
    clearly-labeled result with a warning instead, since this signal is
    advisory and must not crash or block the rest of the pipeline. Only
    raises UnsupportedImageError if the bytes aren't a decodable image at all
    (the same hard failure mode as every other stage).
    """
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except UnidentifiedImageError as exc:
        raise UnsupportedImageError("Could not decode the image for deepfake analysis.") from exc
    except OSError as exc:
        raise UnsupportedImageError(f"Image appears corrupted: {exc}") from exc

    clf = _get_pipeline()
    if clf is None:
        return _neutral_result(
            "Deepfake detection model could not be loaded (missing torch/transformers, "
            "no network access on first run, or a download failure). Skipping this check."
        )

    try:
        predictions = clf(image, top_k=None)
    except Exception as exc:
        return _neutral_result(f"Deepfake model inference failed: {exc}. Skipping this check.")

    scores = {p["label"]: float(p["score"]) for p in predictions}
    real_score = scores.get("Real")
    if real_score is None:
        return _neutral_result(
            f"Deepfake model returned unexpected labels {list(scores)}; expected 'Real'/'Fake'."
        )

    is_likely_real = real_score >= 0.5
    return DeepfakeResult(
        is_likely_real=is_likely_real,
        confidence=round(real_score, 4),
        label="Real" if is_likely_real else "AI-Generated",
    )
