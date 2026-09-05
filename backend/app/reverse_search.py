"""Stage 2: reverse image search via SerpApi's Google Lens engine.

Real API calls only. No cached/sample responses are ever substituted in place
of a live call, and zero-match results are surfaced as-is rather than papered
over with a placeholder.

SerpApi has no direct base64/file-upload param on the search endpoint itself;
an image must first be uploaded to https://serpapi.com/image (max 500 KB) to
get an `image_id`, which is then passed to the google_lens search engine.
Both calls are made here, using the real response schema as documented at
https://serpapi.com/google-lens-upload-an-image and
https://serpapi.com/google-lens-api (verified against SerpApi's own docs, not
assumed) -- parsed defensively since SerpApi does not guarantee every field
is always present.
"""
from __future__ import annotations

import io

import requests
from PIL import Image

from app.config import SERPAPI_KEY

UPLOAD_URL = "https://serpapi.com/image"
SEARCH_URL = "https://serpapi.com/search"
MAX_UPLOAD_BYTES = 500 * 1024  # SerpApi's documented upload limit
REQUEST_TIMEOUT_S = 30


class ReverseSearchError(Exception):
    """Base class for all Stage 2 errors."""


class MissingApiKeyError(ReverseSearchError):
    pass


class ImageUploadError(ReverseSearchError):
    pass


class SearchApiError(ReverseSearchError):
    pass


class NoMatchesFoundError(ReverseSearchError):
    """The search ran successfully but SerpApi returned zero visual matches."""


def _require_api_key() -> str:
    if not SERPAPI_KEY:
        raise MissingApiKeyError(
            "SERPAPI_KEY is not set. Add it to .env at the repo root "
            "(see .env.example) before running reverse image search."
        )
    return SERPAPI_KEY


def _compress_if_needed(image_bytes: bytes) -> bytes:
    """Re-encode the image so it fits SerpApi's 500 KB upload limit.

    Only touches images that would otherwise be rejected -- this is a real
    re-encoding of the actual uploaded bytes, not a substitution of different
    content.
    """
    if len(image_bytes) <= MAX_UPLOAD_BYTES:
        return image_bytes

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    for quality in (85, 75, 65, 55, 45):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        if buf.tell() <= MAX_UPLOAD_BYTES:
            return buf.getvalue()

    # Still too big at low quality: downscale dimensions and retry once.
    width, height = img.size
    img = img.resize((width // 2, height // 2))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    if buf.tell() <= MAX_UPLOAD_BYTES:
        return buf.getvalue()

    raise ImageUploadError(
        f"Could not compress the image under SerpApi's {MAX_UPLOAD_BYTES // 1024} KB "
        "upload limit even after re-encoding and downscaling."
    )


def _upload_image(image_bytes: bytes, api_key: str) -> str:
    payload = _compress_if_needed(image_bytes)
    try:
        resp = requests.post(
            UPLOAD_URL,
            files={"image": ("upload.jpg", payload, "image/jpeg")},
            data={"api_key": api_key},
            timeout=REQUEST_TIMEOUT_S,
        )
    except requests.exceptions.Timeout as exc:
        raise ImageUploadError("Timed out uploading the image to SerpApi.") from exc
    except requests.exceptions.RequestException as exc:
        raise ImageUploadError(f"Network error uploading image to SerpApi: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise ImageUploadError(
            f"SerpApi image upload returned a non-JSON response (HTTP {resp.status_code})."
        ) from exc

    if resp.status_code != 200:
        detail = data.get("error") or data.get("message") or resp.text[:300]
        raise ImageUploadError(f"SerpApi image upload failed (HTTP {resp.status_code}): {detail}")

    image_id = data.get("image_id")
    if not image_id:
        raise ImageUploadError(
            f"SerpApi image upload response did not include an image_id: {data}"
        )
    return image_id


def _run_lens_search(image_id: str, api_key: str) -> dict:
    params = {
        "engine": "google_lens",
        "image_id": image_id,
        "api_key": api_key,
        "type": "visual_matches",
    }
    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        raise SearchApiError("Timed out calling SerpApi's Google Lens search.") from exc
    except requests.exceptions.RequestException as exc:
        raise SearchApiError(f"Network error calling SerpApi search: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise SearchApiError(
            f"SerpApi search returned a non-JSON response (HTTP {resp.status_code})."
        ) from exc

    error_detail = data.get("error")
    if resp.status_code != 200 or (error_detail and "hasn't returned any results" not in error_detail.lower()):
        detail = error_detail or resp.text[:300]
        raise SearchApiError(f"SerpApi search failed (HTTP {resp.status_code}): {detail}")

    return data


def reverse_image_search(image_bytes: bytes) -> list[dict]:
    """Run a real reverse image search and return ranked candidate matches.

    Each candidate dict mirrors SerpApi's own `visual_matches` field names
    (position, title, link, source, thumbnail, image, ...) rather than
    renaming/reshaping them, since we don't want to assume a fixed schema.
    Raises NoMatchesFoundError if the search succeeds but finds nothing --
    callers must not substitute a placeholder result in that case.
    """
    api_key = _require_api_key()
    image_id = _upload_image(image_bytes, api_key)
    data = _run_lens_search(image_id, api_key)

    matches = data.get("visual_matches") or []
    if not matches:
        raise NoMatchesFoundError(
            "SerpApi's reverse image search completed successfully but found "
            "zero visual matches for this image."
        )

    return matches
