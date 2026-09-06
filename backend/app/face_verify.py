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

from dataclasses import dataclass

import requests

from app.face_detect import (
    FaceDetectionError,
    NoFaceFoundError,
    UnsupportedImageError,
    compare_faces,
    detect_faces,
)
from app.fetch_match import BROWSER_HEADERS

FACE_SIMILARITY_THRESHOLD = 55.0  # percent -- reject below this
MAX_CANDIDATES_TO_CHECK = 15
DOWNLOAD_TIMEOUT_S = 5


@dataclass
class VerifiedMatch:
    original_match: dict
    face_similarity: float | None
    face_found: bool
    passed: bool
    reject_reason: str | None


def _download_candidate_image(candidate: dict) -> bytes | None:
    """Downloads the thumbnail (fast, small); falls back to the full image
    if the thumbnail fails. Never raises -- returns None on any failure."""
    for key in ("thumbnail", "image"):
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

    # Sort passed candidates by face similarity descending so that direct,
    # high-confidence photographic matches (e.g. 95%+) are prioritized over
    # lower-confidence artistic reproductions or sketches (e.g. 70-80%).
    passed.sort(key=lambda vm: vm.face_similarity or 0.0, reverse=True)

    return passed, rejected
