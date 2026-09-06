"""Orchestrates all six pipeline stages and yields a live status event after
each one, so the frontend can show progress stage-by-stage instead of
waiting for one big response at the end.

Split into two generators because match selection is a genuine pause point:
`run_detect_and_search` covers Stages 1-2 and hands back candidates for the
user (or an auto-pick toggle) to choose from; `run_process_match` covers
Stages 3-6 once a match is chosen.
"""
from __future__ import annotations

import base64
from typing import AsyncIterator

from app.face_detect import (
    FaceDetectionError,
    NoFaceFoundError,
    UnsupportedImageError,
    compare_faces,
    crop_face,
    detect_faces,
)
from app.fetch_match import (
    BlockedError,
    ContentFetchError,
    DeadLinkError,
    UnsupportedContentError,
    fetch_image_bytes,
    fetch_match_content,
)
from app.fingerprint import build_fingerprint, compare_perceptual_hashes
from app.reverse_search import (
    ImageUploadError,
    MissingApiKeyError,
    NoMatchesFoundError,
    ReverseSearchError,
    SearchApiError,
    reverse_image_search,
)
from app.chain_client import (
    AlreadyRegisteredError,
    ChainConfigError,
    ChainConnectionError,
    ChainError,
    NotRegisteredError,
    get_record,
    submit_fingerprint,
)
from app.deepfake_detect import DeepfakeDetectionError, detect_deepfake


def _event(stage: str, label: str, status: str, data: dict | None = None, message: str | None = None) -> dict:
    return {"stage": stage, "label": label, "status": status, "data": data, "message": message}


async def run_detect_and_search(image_bytes: bytes) -> AsyncIterator[dict]:
    yield _event("detect", "Detecting face", "running")
    try:
        faces = detect_faces(image_bytes)
    except (NoFaceFoundError, UnsupportedImageError) as exc:
        yield _event("detect", "Detecting face", "error", message=str(exc))
        return
    except FaceDetectionError as exc:
        yield _event("detect", "Detecting face", "error", message=str(exc))
        return

    num_faces = len(faces)
    warning = None
    if num_faces > 1:
        warning = f"{num_faces} faces detected; searching with the largest one."
    primary_face = faces[0]
    cropped_face_bytes = crop_face(image_bytes, primary_face)
    cropped_face_b64 = base64.b64encode(cropped_face_bytes).decode("ascii")

    yield _event(
        "detect",
        "Detecting face",
        "done",
        data={
            "num_faces": num_faces,
            "boxes": [f.to_dict()["box"] for f in faces],
            "cropped_face_b64": cropped_face_b64,
            "face_encoding": primary_face.encoding,
        },
        message=warning,
    )

    yield _event("deepfake", "Checking image authenticity", "running")
    try:
        deepfake_result = detect_deepfake(image_bytes)
        yield _event("deepfake", "Checking image authenticity", "done", data=deepfake_result.to_dict())
    except DeepfakeDetectionError as exc:
        # Advisory only -- never blocks the pipeline, so this is reported as
        # "skipped" rather than "error" and execution continues regardless.
        yield _event("deepfake", "Checking image authenticity", "skipped", message=str(exc))

    yield _event("search", "Searching the web for matching posts", "running")
    search_mode = "face_crop"
    try:
        matches = reverse_image_search(cropped_face_bytes)
    except MissingApiKeyError as exc:
        yield _event("search", "Searching the web for matching posts", "error", message=str(exc))
        return
    except NoMatchesFoundError:
        # The tight face crop is more precise but gives Google Lens less to
        # match against than a full photo would -- a real repost of the
        # *whole* photo elsewhere on the web can still be found even when
        # the isolated face crop finds nothing. Retry once with the full
        # original image before giving up. Reported as a separate minor
        # "search_retry" event (like fetch_attempt) rather than a second
        # running/skipped pair on "search" itself, so the main search entry
        # stays a single continuous trace item.
        search_mode = "full_image"
        yield _event(
            "search_retry",
            "Retrying with the full photo",
            "skipped",
            message="No matches for the cropped face -- retrying with the full photo.",
        )
        try:
            matches = reverse_image_search(image_bytes)
        except MissingApiKeyError as exc:
            yield _event("search", "Searching the web for matching posts", "error", message=str(exc))
            return
        except NoMatchesFoundError as exc:
            yield _event(
                "search", "Searching the web for matching posts", "error",
                message=f"{exc} (tried both the cropped face and the full photo.)",
            )
            return
        except (ImageUploadError, SearchApiError, ReverseSearchError) as exc:
            yield _event("search", "Searching the web for matching posts", "error", message=str(exc))
            return
    except (ImageUploadError, SearchApiError, ReverseSearchError) as exc:
        yield _event("search", "Searching the web for matching posts", "error", message=str(exc))
        return

    yield _event(
        "search",
        "Searching the web for matching posts",
        "done",
        data={"num_matches": len(matches), "matches": matches, "search_mode": search_mode},
    )


MAX_FETCH_ATTEMPTS = 10


async def run_process_match(matches: list[dict], face_encoding: list[float] | None = None) -> AsyncIterator[dict]:
    candidates = matches[:MAX_FETCH_ATTEMPTS]

    yield _event("fetch", "Fetching matched content", "running")
    fetched = None
    match = None
    attempts_tried = 0
    for candidate in candidates:
        attempts_tried += 1
        try:
            fetched = fetch_match_content(candidate)
            match = candidate
            break
        except (DeadLinkError, BlockedError, UnsupportedContentError, ContentFetchError) as exc:
            yield _event(
                "fetch_attempt",
                "Fetching matched content",
                "skipped",
                data={
                    "attempt": attempts_tried,
                    "total": len(candidates),
                    "source": candidate.get("source"),
                    "title": candidate.get("title"),
                },
                message=str(exc),
            )
            continue

    if fetched is None:
        yield _event(
            "fetch",
            "Fetching matched content",
            "error",
            message=f"None of the top {len(candidates)} matches' images could be fetched -- all were blocked or unreachable.",
        )
        return

    face_similarity_score: float | None = None
    face_found_in_match = False
    if face_encoding is not None:
        try:
            matched_faces = detect_faces(fetched.image_bytes)
            face_found_in_match = True
            face_similarity_score = compare_faces(face_encoding, matched_faces[0].encoding)
        except (NoFaceFoundError, UnsupportedImageError, FaceDetectionError):
            face_found_in_match = False
            face_similarity_score = None

    yield _event(
        "fetch",
        "Fetching matched content",
        "done",
        data={
            "source_url": fetched.source_url,
            "image_url": fetched.image_url,
            "caption": fetched.caption,
            "page_title": fetched.page_title,
            "scraped_at": fetched.scraped_at,
            "warning": fetched.page_fetch_warning,
            "attempts_tried": attempts_tried,
            "face_similarity_score": face_similarity_score,
            "face_found_in_match": face_found_in_match,
        },
    )

    yield _event("fingerprint", "Computing SHA-256 fingerprint", "running")
    fingerprint = build_fingerprint(fetched.image_bytes, fetched.source_url, fetched.caption, fetched.scraped_at)
    yield _event(
        "fingerprint",
        "Computing SHA-256 fingerprint",
        "done",
        data={
            "image_hash": fingerprint.image_hash,
            "combined_hash": fingerprint.combined_hash,
            "blob": fingerprint.blob,
            "perceptual_hash": fingerprint.perceptual_hash,
            "image_size_bytes": len(fetched.image_bytes),
        },
    )

    yield _event("chain_submit", "Uploading fingerprint to the blockchain", "running")
    try:
        chain_result = submit_fingerprint(fingerprint.combined_hash, fetched.source_url or "")
        yield _event("chain_submit", "Uploading fingerprint to the blockchain", "done", data=chain_result)
    except AlreadyRegisteredError:
        chain_result = None
        yield _event(
            "chain_submit",
            "Uploading fingerprint to the blockchain",
            "skipped",
            message="This exact fingerprint is already registered on-chain (e.g. from a prior run) -- proceeding to verification against the existing record.",
        )
    except (ChainConfigError, ChainConnectionError, ChainError) as exc:
        yield _event("chain_submit", "Uploading fingerprint to the blockchain", "error", message=str(exc))
        return

    yield _event("reverify", "Independently re-verifying on-chain record", "running")
    try:
        fresh_bytes = fetch_image_bytes(fetched.image_url)
    except (DeadLinkError, BlockedError, UnsupportedContentError, ContentFetchError) as exc:
        yield _event(
            "reverify", "Independently re-verifying on-chain record", "error",
            message=f"Re-fetch for verification failed: {exc}",
        )
        return

    recomputed = build_fingerprint(fresh_bytes, fetched.source_url, fetched.caption, fetched.scraped_at)
    perceptual_similarity = compare_perceptual_hashes(fingerprint.perceptual_hash, recomputed.perceptual_hash)
    try:
        record = get_record(recomputed.combined_hash)
        verified = True
        mismatch_reason = None
    except NotRegisteredError:
        record = None
        verified = False
        mismatch_reason = "No on-chain record matches the freshly recomputed hash."
    except (ChainConfigError, ChainConnectionError, ChainError) as exc:
        yield _event("reverify", "Independently re-verifying on-chain record", "error", message=str(exc))
        return

    yield _event(
        "reverify",
        "Independently re-verifying on-chain record",
        "done",
        data={
            "recomputed_image_hash": recomputed.image_hash,
            "recomputed_combined_hash": recomputed.combined_hash,
            "recomputed_perceptual_hash": recomputed.perceptual_hash,
            "perceptual_similarity": perceptual_similarity,
            "on_chain_record": record,
            "verified": verified,
            "mismatch_reason": mismatch_reason,
        },
    )

    yield _event(
        "complete",
        "Pipeline complete",
        "done",
        data={
            "matched_post": {
                "url": fetched.source_url,
                "thumbnail": match.get("thumbnail") or fetched.image_url,
                "title": fetched.page_title,
                "source": match.get("source"),
            },
            "image_hash": fingerprint.image_hash,
            "combined_hash": fingerprint.combined_hash,
            "perceptual_hash": fingerprint.perceptual_hash,
            "chain_result": chain_result,
            "verified": verified,
            "recomputed_image_hash": recomputed.image_hash,
            "recomputed_combined_hash": recomputed.combined_hash,
            "recomputed_perceptual_hash": recomputed.perceptual_hash,
            "perceptual_similarity": perceptual_similarity,
            "on_chain_record": record,
        },
    )
