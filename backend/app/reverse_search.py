"""Stage 2: reverse image search via SerpApi, using Yandex Images and Bing
Reverse Image as the primary engines (with Google Lens as a last-resort
fallback).

Google Lens matches objects, scenes, and textures -- not faces. A selfie in a
garden comes back with flower shows and botanical gardens, not other photos
of the same person. Yandex and Bing's reverse-image engines are materially
better at matching the same face across different photos, so both are tried
first and their results merged; Google Lens is only used if neither finds
anything.

Real API calls only. No cached/sample responses are ever substituted in place
of a live call, and zero-match results are surfaced as-is rather than papered
over with a placeholder.

Yandex (`yandex_images`) and Bing Reverse Image (`bing_reverse_image`) both
require a real public `image_url` -- unlike google_lens, they have no
file-upload path. SerpApi's own upload endpoint (https://serpapi.com/image)
does not return a usable public URL for this account (only an `image_id`
usable by google_lens), so a temporary public URL is obtained from imgbb
(https://api.imgbb.com/1/upload) instead, auto-expired shortly after upload
and explicitly deleted once the search completes. This is a real trade-off,
not a hidden one -- see the Ethics Note in README.md -- and only activates
when IMGBB_API_KEY is configured; without it, this module behaves exactly as
before (Google Lens only).

`google_reverse_image` was deliberately not added: unlike yandex_images and
bing_reverse_image, its `image_results` carry no thumbnail/full-image URL at
all (only page links), so there is nothing for the face-verification filter
to download and check without an extra per-candidate page-scrape.

Response schemas verified against SerpApi's own docs, not assumed --
https://serpapi.com/google-lens-upload-an-image,
https://serpapi.com/google-lens-api,
https://serpapi.com/yandex-images-api,
https://serpapi.com/bing-reverse-image-api -- and parsed defensively since
SerpApi does not guarantee every field is always present.
"""
from __future__ import annotations

import io
import itertools
import logging
from urllib.parse import urlparse

import requests
from PIL import Image

from app.config import IMGBB_API_KEY, SERPAPI_KEY
from app.safety_filter import is_safe_candidate

logger = logging.getLogger(__name__)

# Populated after every call to reverse_image_search() so callers (pipeline.py)
# can inspect which engines actually ran and whether accuracy was degraded.
last_search_info: dict = {}

UPLOAD_URL = "https://serpapi.com/image"
SEARCH_URL = "https://serpapi.com/search"
IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"
MAX_UPLOAD_BYTES = 500 * 1024  # SerpApi's documented upload limit
REQUEST_TIMEOUT_S = 30
IMGBB_TIMEOUT_S = 15
IMGBB_EXPIRATION_S = 60  # imgbb's minimum -- auto-deletes shortly after upload


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


def _upload_image(image_bytes: bytes, api_key: str) -> tuple[str, str | None]:
    """Uploads the image to SerpApi and returns (image_id, image_url).

    `image_id` is used by the google_lens engine. `image_url` -- SerpApi's
    own hosted URL for the uploaded image -- is needed by yandex_images,
    which takes a real image URL rather than an image_id; the field name for
    it isn't consistently documented, so plausible keys are checked
    defensively. If none are present, `image_url` is None and the caller
    skips straight to Google Lens.
    """
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
    image_url = data.get("image_url") or data.get("link") or data.get("url")
    return image_id, image_url


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


def _upload_to_temp_host(image_bytes: bytes) -> tuple[str | None, str | None, str | None]:
    """Best-effort upload to imgbb for a real public URL that yandex_images
    and bing_reverse_image can fetch. Returns (public_url, delete_url,
    failure_reason), where failure_reason is None on success and a
    human-readable string on any failure -- this must never raise, since
    it's an enhancement layered on top of the Google-Lens-only path, not a
    hard requirement.
    """
    if not IMGBB_API_KEY:
        reason = "IMGBB_API_KEY is not set — Yandex/Bing engines are disabled."
        logger.warning(reason)
        return None, None, reason
    try:
        resp = requests.post(
            IMGBB_UPLOAD_URL,
            data={"key": IMGBB_API_KEY, "expiration": IMGBB_EXPIRATION_S},
            files={"image": ("upload.jpg", image_bytes, "image/jpeg")},
            timeout=IMGBB_TIMEOUT_S,
        )
        data = resp.json()
        if resp.status_code != 200 or not data.get("success"):
            reason = (
                f"imgbb upload failed (HTTP {resp.status_code}): "
                f"{data.get('error', {}).get('message', resp.text[:200])}"
            )
            logger.warning(reason)
            return None, None, reason
        payload = data.get("data") or {}
        logger.info("imgbb upload succeeded — Yandex/Bing engines are available.")
        return payload.get("url"), payload.get("delete_url"), None
    except requests.exceptions.RequestException as exc:
        reason = f"imgbb upload network error ({exc.__class__.__name__}): {exc}"
        logger.warning(reason)
        return None, None, reason
    except ValueError as exc:
        reason = f"imgbb returned non-JSON response: {exc}"
        logger.warning(reason)
        return None, None, reason


def _delete_temp_host_image(delete_url: str | None) -> None:
    """Best-effort early deletion of the temp-hosted image once the search
    that needed it has completed. Never raises -- IMGBB_EXPIRATION_S is the
    real safety net if this fails or is skipped."""
    if not delete_url:
        return
    try:
        requests.get(delete_url, timeout=IMGBB_TIMEOUT_S)
    except requests.exceptions.RequestException:
        pass


def _domain_from_link(link: str | None) -> str | None:
    if not link:
        return None
    try:
        return urlparse(link).netloc.replace("www.", "") or None
    except ValueError:
        return None


def _run_yandex_search(image_url: str, api_key: str) -> list[dict]:
    """Runs SerpApi's yandex_images engine against an already-hosted image
    URL and returns normalized candidate dicts (link, title, source,
    thumbnail, image, position, search_engine). Yandex's results come back
    under "image_results", not "visual_matches" like Google Lens.
    """
    params = {
        "engine": "yandex_images",
        "url": image_url,
        "api_key": api_key,
    }
    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        raise SearchApiError("Timed out calling SerpApi's Yandex Images search.") from exc
    except requests.exceptions.RequestException as exc:
        raise SearchApiError(f"Network error calling SerpApi Yandex search: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise SearchApiError(
            f"SerpApi Yandex search returned a non-JSON response (HTTP {resp.status_code})."
        ) from exc

    error_detail = data.get("error")
    if resp.status_code != 200 or (error_detail and "hasn't returned any results" not in error_detail.lower()):
        detail = error_detail or resp.text[:300]
        raise SearchApiError(f"SerpApi Yandex search failed (HTTP {resp.status_code}): {detail}")

    raw_results = data.get("image_results") or []
    normalized = []
    for position, item in enumerate(raw_results, start=1):
        link = item.get("link") or item.get("source")
        # Yandex nests the actual URLs one level down -- {"thumbnail":
        # {"link": "...", "width": ..., ...}, "original_image": {"link":
        # "...", ...}} -- not plain strings under "thumbnail"/"original".
        thumbnail_obj = item.get("thumbnail") or {}
        original_obj = item.get("original_image") or {}
        thumbnail_url = thumbnail_obj.get("link") if isinstance(thumbnail_obj, dict) else thumbnail_obj
        image_url = original_obj.get("link") if isinstance(original_obj, dict) else original_obj
        normalized.append(
            {
                "position": item.get("position", position),
                "title": item.get("title"),
                "link": link,
                "source": item.get("source") or _domain_from_link(link),
                "thumbnail": thumbnail_url,
                "image": image_url or thumbnail_url,
                "search_engine": "yandex",
            }
        )
    return normalized


def _run_bing_reverse_image_search(image_url: str, api_key: str) -> list[dict]:
    """Runs SerpApi's bing_reverse_image engine against a public image URL.

    Bing's response has no single "results" array -- it splits matches
    across `pages_with_this_image` (other pages hosting this exact/near-
    duplicate image, the highest-precision signal) and `related_content`
    (visually similar but potentially different images, the same noisy
    signal Lens/Yandex already provide and which the face-verification
    filter is built to handle). Both are included and normalized the same
    way, since every candidate gets face-verified regardless of source.
    """
    params = {
        "engine": "bing_reverse_image",
        "image_url": image_url,
        "api_key": api_key,
    }
    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        raise SearchApiError("Timed out calling SerpApi's Bing Reverse Image search.") from exc
    except requests.exceptions.RequestException as exc:
        raise SearchApiError(f"Network error calling SerpApi Bing search: {exc}") from exc

    try:
        data = resp.json()
    except ValueError as exc:
        raise SearchApiError(
            f"SerpApi Bing search returned a non-JSON response (HTTP {resp.status_code})."
        ) from exc

    error_detail = data.get("error")
    if resp.status_code != 200 or (error_detail and "hasn't returned any results" not in error_detail.lower()):
        detail = error_detail or resp.text[:300]
        raise SearchApiError(f"SerpApi Bing search failed (HTTP {resp.status_code}): {detail}")

    raw_results = (data.get("pages_with_this_image") or []) + (data.get("related_content") or [])
    normalized = []
    for position, item in enumerate(raw_results, start=1):
        # In `related_content`, Bing's own "link" field is its internal
        # image-viewer URL (bing.com/images/search?...), not the actual
        # destination page -- that's in "source" instead (verified against
        # a real response, not assumed). Guard against the reverse being
        # true elsewhere by picking whichever field is a real external URL.
        link = None
        for candidate in (item.get("source"), item.get("link")):
            if candidate and candidate.startswith("http") and "bing.com/images/search" not in candidate:
                link = candidate
                break
        normalized.append(
            {
                "position": position,
                "title": item.get("title"),
                "link": link,
                "source": _domain_from_link(link) or item.get("domain"),
                "thumbnail": item.get("thumbnail"),
                "image": item.get("original") or item.get("cdn_original") or item.get("thumbnail"),
                "search_engine": "bing_reverse_image",
            }
        )
    return normalized


def reverse_image_search(image_bytes: bytes) -> list[dict]:
    """Run a real reverse image search and return ranked candidate matches.

    Tries Yandex and Bing Reverse Image first (both materially better at
    matching the same *face* across different photos than Google Lens),
    merging and deduplicating (by link) their results. Both require a real
    public image URL, obtained via a temporary imgbb upload -- if
    IMGBB_API_KEY isn't set, or the upload fails, or both engines return
    nothing, this falls back to Google Lens (which uses SerpApi's own
    image-upload path and needs no public URL).

    Each candidate dict is tagged with `search_engine` ("yandex",
    "bing_reverse_image", or "google_lens") so callers/UI can show which
    engine found it. Raises NoMatchesFoundError if nothing is found at all
    -- callers must not substitute a placeholder result in that case.

    After every call, the module-level `last_search_info` dict is updated
    with metadata about which engines were attempted, which succeeded, and
    whether the search fell back to reduced-accuracy mode.
    """
    global last_search_info
    api_key = _require_api_key()

    engines_attempted: list[str] = []
    engines_succeeded: list[str] = []
    engine_errors: dict[str, str] = {}
    fallback_reason: str | None = None
    reduced_accuracy = False

    public_url, delete_url, imgbb_failure = _upload_to_temp_host(image_bytes)
    merged: list[dict] = []
    if public_url:
        per_engine_results = []
        engine_names = ["yandex", "bing_reverse_image"]
        search_fns = [_run_yandex_search, _run_bing_reverse_image_search]
        for engine_name, search_fn in zip(engine_names, search_fns):
            engines_attempted.append(engine_name)
            try:
                results = search_fn(public_url, api_key)
                per_engine_results.append(results)
                if results:
                    engines_succeeded.append(engine_name)
                    logger.info("[%s] returned %d result(s).", engine_name, len(results))
                else:
                    logger.info("[%s] returned 0 results.", engine_name)
            except SearchApiError as exc:
                logger.warning("[%s] search failed: %s", engine_name, exc)
                engine_errors[engine_name] = str(exc)
                per_engine_results.append([])

        # Interleaved (round-robin across engines), not concatenated -- the
        # face-verification filter only checks the first
        # MAX_CANDIDATES_TO_CHECK candidates, so simply appending one
        # engine's results after another would let a high-volume engine
        # crowd out every candidate from the other one before it's ever
        # checked.
        seen_links: set[str] = set()
        for row in itertools.zip_longest(*per_engine_results):
            for r in row:
                if r is None:
                    continue
                link = r.get("link")
                if link and link in seen_links:
                    continue
                safe, reason = is_safe_candidate(r)
                if not safe:
                    logger.info(
                        "Dropping unsafe/spam candidate '%s' (%s): %s",
                        r.get("source") or r.get("title") or "unknown",
                        link,
                        reason,
                    )
                    continue
                if link:
                    seen_links.add(link)
                merged.append(r)
        _delete_temp_host_image(delete_url)
    else:
        # imgbb failed — Yandex/Bing are completely unavailable.
        fallback_reason = imgbb_failure or "imgbb upload failed (unknown reason)"

    if merged:
        last_search_info = {
            "engines_attempted": engines_attempted,
            "engines_succeeded": engines_succeeded,
            "engine_errors": engine_errors,
            "fallback_reason": None,
            "reduced_accuracy": False,
        }
        return merged

    # Falling back to Google Lens — explicitly loud about it.
    if not fallback_reason:
        if engine_errors:
            fallback_reason = (
                f"Yandex/Bing returned no results. Errors: "
                + "; ".join(f"{k}: {v}" for k, v in engine_errors.items())
            )
        else:
            fallback_reason = "Yandex/Bing returned no matches."

    reduced_accuracy = True
    engines_attempted.append("google_lens")
    logger.warning(
        "FALLBACK TO GOOGLE LENS — reduced accuracy mode. Reason: %s",
        fallback_reason,
    )

    image_id, _ = _upload_image(image_bytes, api_key)
    lens_data = _run_lens_search(image_id, api_key)
    lens_matches = lens_data.get("visual_matches") or []

    if lens_matches:
        engines_succeeded.append("google_lens")

    last_search_info = {
        "engines_attempted": engines_attempted,
        "engines_succeeded": engines_succeeded,
        "engine_errors": engine_errors,
        "fallback_reason": fallback_reason,
        "reduced_accuracy": reduced_accuracy,
    }

    if not lens_matches:
        raise NoMatchesFoundError(
            "Reverse image search completed successfully (tried Yandex, Bing, "
            "and Google Lens) but found zero matches for this image."
        )

    tagged = []
    for m in lens_matches:
        if not is_safe_candidate(m)[0]:
            continue
        m = dict(m)
        m.setdefault("search_engine", "google_lens")
        tagged.append(m)

    if not tagged:
        raise NoMatchesFoundError(
            "Reverse image search completed successfully (tried Yandex, Bing, "
            "and Google Lens) but all results were filtered out as spam/unsafe."
        )
    return tagged
