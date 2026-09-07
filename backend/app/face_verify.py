"""Stage 2.5: post-search face verification filter.

Neither Yandex nor Google Lens are guaranteed to return results that
actually contain the same face -- they match on visual similarity of the
whole image (scene, texture, composition), which is a different signal from
"is this the same person". Left unchecked, that produces confidently wrong
results: e.g. a search result that happens to share a background or color
palette with the original photo, but shows a completely different person.

This module downloads each search-result candidate's thumbnail (falling
back to its full image), detects faces in it, and compares every face found
against the original uploaded face's encoding. A candidate is only accepted
if its best-matching face clears FACE_SIMILARITY_THRESHOLD; everything else
(no face found, or face found but below threshold) is rejected before it
ever reaches the fetch/fingerprint/blockchain stages.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

import requests
from PIL import Image

from app.config import FACE_SIMILARITY_THRESHOLD
from app.face_detect import (
    FaceDetectionError,
    NoFaceFoundError,
    UnsupportedImageError,
    compare_faces,
    detect_faces,
)
from app.fetch_match import BROWSER_HEADERS
from app.safety_filter import domain_trust_bonus, is_safe_candidate

logger = logging.getLogger(__name__)

MAX_CANDIDATES_TO_CHECK = 15
DOWNLOAD_TIMEOUT_S = 5
MIN_FACE_DETECT_PX = 80  # HOG detector struggles below ~80px on shortest side


@dataclass
class VerifiedMatch:
    original_match: dict
    face_similarity: float | None
    face_found: bool
    passed: bool
    reject_reason: str | None
    undetermined: bool = field(default=False)


def _image_short_side(image_bytes: bytes) -> int | None:
    """Returns the shorter dimension (width or height) of the image, or
    None if the bytes can't be decoded."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        return min(img.size)
    except Exception:
        return None


def _download_candidate_image(candidate: dict, *, prefer_full: bool = False) -> bytes | None:
    """Downloads the thumbnail (fast, small); falls back to the full image
    if the thumbnail fails. When `prefer_full` is True, tries the full-size
    image URL first (used when a previous thumbnail was too small for face
    detection). Never raises -- returns None on any failure."""
    keys = ("image", "thumbnail") if prefer_full else ("thumbnail", "image")
    for key in keys:
        url = candidate.get(key)
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT_S, headers=BROWSER_HEADERS)
        except requests.exceptions.RequestException:
            continue
        if resp.status_code == 200 and resp.content:
            return resp.content
    return None


def _best_face_similarity(original_encoding: list[float], candidate_bytes: bytes) -> float | None:
    """Returns the highest similarity among all faces found in the
    candidate image, or None if no face could be detected in it at all."""
    try:
        faces = detect_faces(candidate_bytes)
    except (NoFaceFoundError, UnsupportedImageError, FaceDetectionError):
        return None
    return max(compare_faces(original_encoding, f.encoding) for f in faces)


def verify_candidates(
    candidates: list[dict],
    original_encoding: list[float],
) -> tuple[list[VerifiedMatch], list[VerifiedMatch]]:
    """Checks each candidate (capped at MAX_CANDIDATES_TO_CHECK) for the same
    face as `original_encoding`. Returns (passed, rejected) lists of
    VerifiedMatch, preserving the candidates' original order within each
    list.
    """
    passed: list[VerifiedMatch] = []
    rejected: list[VerifiedMatch] = []

    for candidate in candidates[:MAX_CANDIDATES_TO_CHECK]:
        safe, reason = is_safe_candidate(candidate)
        if not safe:
            rejected.append(
                VerifiedMatch(
                    original_match=candidate,
                    face_similarity=None,
                    face_found=False,
                    passed=False,
                    reject_reason=f"Filtered by content safety/spam filter: {reason}",
                )
            )
            continue

        image_bytes = _download_candidate_image(candidate)
        if image_bytes is None:
            rejected.append(
                VerifiedMatch(
                    original_match=candidate,
                    face_similarity=None,
                    face_found=False,
                    passed=False,
                    reject_reason="Could not download a thumbnail or image for this candidate.",
                )
            )
            continue

        # Fix 5: check dimensions -- if the thumbnail is too small for
        # reliable face detection, try the full-size image instead.
        short_side = _image_short_side(image_bytes)
        if short_side is not None and short_side < MIN_FACE_DETECT_PX:
            logger.info(
                "Thumbnail too small (%dpx) for %s — trying full-size image.",
                short_side,
                candidate.get("source") or candidate.get("link", "?"),
            )
            full_bytes = _download_candidate_image(candidate, prefer_full=True)
            if full_bytes is not None:
                full_short = _image_short_side(full_bytes)
                if full_short is not None and full_short >= MIN_FACE_DETECT_PX:
                    image_bytes = full_bytes
                    short_side = full_short
                    logger.info("Using full-size image (%dpx).", full_short)
                else:
                    logger.info(
                        "Full-size image still too small (%s px) — marking undetermined.",
                        full_short,
                    )

        # If still too small after trying the full image, mark as undetermined
        # rather than silently rejecting as "no face found".
        if short_side is not None and short_side < MIN_FACE_DETECT_PX:
            rejected.append(
                VerifiedMatch(
                    original_match=candidate,
                    face_similarity=None,
                    face_found=False,
                    passed=False,
                    reject_reason=f"Image too small for reliable face detection ({short_side}px).",
                    undetermined=True,
                )
            )
            continue

        similarity = _best_face_similarity(original_encoding, image_bytes)
        if similarity is None:
            rejected.append(
                VerifiedMatch(
                    original_match=candidate,
                    face_similarity=None,
                    face_found=False,
                    passed=False,
                    reject_reason="No face detected in this candidate's image.",
                )
            )
            continue

        if similarity >= FACE_SIMILARITY_THRESHOLD:
            passed.append(
                VerifiedMatch(
                    original_match=candidate,
                    face_similarity=similarity,
                    face_found=True,
                    passed=True,
                    reject_reason=None,
                )
            )
        else:
            rejected.append(
                VerifiedMatch(
                    original_match=candidate,
                    face_similarity=similarity,
                    face_found=True,
                    passed=False,
                    reject_reason=f"Face similarity {similarity}% is below the {FACE_SIMILARITY_THRESHOLD}% threshold.",
                )
            )

    # Sort passed candidates by composite score: face similarity + domain trust bonus
    # so that verified matches on authoritative, legitimate platforms (YouTube,
    # Wikipedia, Filmibeat, News18, etc.) are prioritized over obscure or spammy sites.
    def _rank_score(vm: VerifiedMatch) -> float:
        base = vm.face_similarity or 0.0
        bonus = domain_trust_bonus(vm.original_match.get("link"))
        return base + bonus

    passed.sort(key=_rank_score, reverse=True)

    return passed, rejected
