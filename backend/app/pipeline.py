"""Orchestrates all six pipeline stages and yields a live status event after
each one, so the frontend can show progress stage-by-stage instead of
waiting for one big response at the end.

Split into two generators because match selection is a genuine pause point:
`run_detect_and_search` covers Stages 1-2 and hands back candidates for the
user (or an auto-pick toggle) to choose from; `run_process_match` covers
Stages 3-6 once a match is chosen.
"""
from __future__ import annotations

from typing import AsyncIterator

from app.face_detect import (
    FaceDetectionError,
    NoFaceFoundError,
    UnsupportedImageError,
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
from app.fingerprint import build_fingerprint
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
        warning = f"{num_faces} faces detected; proceeding with the whole image for search."
    yield _event(
        "detect",
        "Detecting face",
        "done",
        data={"num_faces": num_faces, "boxes": [f.to_dict()["box"] for f in faces]},
        message=warning,
    )

    yield _event("search", "Searching the web for matching posts", "running")
    try:
        matches = reverse_image_search(image_bytes)
    except MissingApiKeyError as exc:
        yield _event("search", "Searching the web for matching posts", "error", message=str(exc))
        return
    except NoMatchesFoundError as exc:
        yield _event("search", "Searching the web for matching posts", "error", message=str(exc))
        return
    except (ImageUploadError, SearchApiError, ReverseSearchError) as exc:
        yield _event("search", "Searching the web for matching posts", "error", message=str(exc))
        return

    yield _event(
        "search",
        "Searching the web for matching posts",
        "done",
        data={"num_matches": len(matches), "matches": matches},
    )


async def run_process_match(match: dict) -> AsyncIterator[dict]:
    yield _event("fetch", "Fetching matched content", "running")
    try:
        fetched = fetch_match_content(match)
    except (DeadLinkError, BlockedError, UnsupportedContentError, ContentFetchError) as exc:
        yield _event("fetch", "Fetching matched content", "error", message=str(exc))
        return

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
        },
    )

    yield _event("fingerprint", "Computing SHA-256 fingerprint", "running")
    fingerprint = build_fingerprint(fetched.image_bytes, fetched.source_url, fetched.caption, fetched.scraped_at)
    yield _event(
        "fingerprint",
        "Computing SHA-256 fingerprint",
        "done",
        data={"image_hash": fingerprint.image_hash, "combined_hash": fingerprint.combined_hash, "blob": fingerprint.blob},
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
            "chain_result": chain_result,
            "verified": verified,
        },
    )
