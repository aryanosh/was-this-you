"""FastAPI app: exposes each pipeline stage individually (for standalone
testing) plus two streaming endpoints that chain all six stages together
with a live status event per stage, and serves the frontend."""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

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
from app.fingerprint import build_fingerprint, compare_perceptual_hashes
from app.chain_client import (
    AlreadyRegisteredError,
    ChainConfigError,
    ChainConnectionError,
    ChainError,
    NotRegisteredError,
    get_record,
    submit_fingerprint,
)
from app.reverse_search import (
    ImageUploadError,
    MissingApiKeyError,
    NoMatchesFoundError,
    ReverseSearchError,
    SearchApiError,
    reverse_image_search,
)
from app.pipeline import run_detect_and_search, run_process_match

app = FastAPI(title="Face -> Reverse Search -> Blockchain Verification Pipeline")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FRONTEND_DIR = _REPO_ROOT / "frontend"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/detect")
async def detect(
    file: UploadFile = File(...),
    face_index: int | None = Query(
        default=None,
        description="If multiple faces are detected, which one to select as the "
        "primary face. Omit to get the full list back without picking one.",
    ),
) -> JSONResponse:
    image_bytes = await file.read()

    try:
        faces = detect_faces(image_bytes)
    except UnsupportedImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NoFaceFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FaceDetectionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    num_faces = len(faces)
    warning: str | None = None
    selected = None

    if face_index is not None:
        if not (0 <= face_index < num_faces):
            raise HTTPException(
                status_code=422,
                detail=f"face_index {face_index} is out of range; {num_faces} face(s) were detected (valid range 0-{num_faces - 1}).",
            )
        selected = faces[face_index]
    elif num_faces == 1:
        selected = faces[0]
    else:
        warning = (
            f"{num_faces} faces were detected. Defaulting to the first one (index 0). "
            f"Pass ?face_index=N (0-{num_faces - 1}) to pick a different one."
        )
        selected = faces[0]

    return JSONResponse(
        {
            "filename": file.filename,
            "num_faces": num_faces,
            "faces": [f.to_dict() for f in faces],
            "selected_index": selected.index,
            "warning": warning,
        }
    )


@app.post("/api/reverse-search")
async def reverse_search(file: UploadFile = File(...)) -> JSONResponse:
    image_bytes = await file.read()

    try:
        matches = reverse_image_search(image_bytes)
    except MissingApiKeyError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except NoMatchesFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ImageUploadError, SearchApiError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ReverseSearchError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse({"filename": file.filename, "num_matches": len(matches), "matches": matches})


@app.post("/api/fetch-and-fingerprint")
async def fetch_and_fingerprint(match: dict = Body(..., description="One candidate dict from /api/reverse-search's `matches` list.")) -> JSONResponse:
    try:
        fetched = fetch_match_content(match)
    except DeadLinkError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except BlockedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except UnsupportedContentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ContentFetchError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    fingerprint = build_fingerprint(
        image_bytes=fetched.image_bytes,
        url=fetched.source_url,
        caption=fetched.caption,
        scraped_at=fetched.scraped_at,
    )

    return JSONResponse(
        {
            "fetched": fetched.to_dict(include_image_bytes=False),
            "fingerprint": fingerprint.to_dict(),
        }
    )


@app.post("/api/submit-to-chain")
async def submit_to_chain(
    combined_hash: str = Body(..., embed=True),
    source_url: str = Body("", embed=True),
) -> JSONResponse:
    try:
        result = submit_fingerprint(combined_hash, source_url)
    except (ChainConfigError, ChainConnectionError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except AlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ChainError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return JSONResponse(result)


@app.post("/api/reverify")
async def reverify(
    image_url: str = Body(..., embed=True),
    source_url: str | None = Body(None, embed=True),
    caption: str | None = Body(None, embed=True),
    scraped_at: str = Body(..., embed=True),
    original_perceptual_hash: str | None = Body(None, embed=True),
) -> JSONResponse:
    """Stage 6: independently re-fetch the original image, recompute its
    fingerprint from scratch, and do a fresh on-chain read against the
    recomputed hash -- no reuse of any in-memory value from Stage 4/5.

    If `original_perceptual_hash` (from the initial run's fingerprint stage)
    is supplied, also reports the visual (pHash) similarity between that and
    the freshly recomputed image -- useful when the SHA-256 mismatches (e.g.
    the caption/URL was edited) but the underlying image content is still
    visually the same.
    """
    try:
        fresh_image_bytes = fetch_image_bytes(image_url)
    except (DeadLinkError, BlockedError, UnsupportedContentError, ContentFetchError) as exc:
        raise HTTPException(status_code=502, detail=f"Re-fetch failed: {exc}") from exc

    fingerprint = build_fingerprint(fresh_image_bytes, source_url, caption, scraped_at)

    perceptual_similarity = None
    if original_perceptual_hash:
        perceptual_similarity = compare_perceptual_hashes(original_perceptual_hash, fingerprint.perceptual_hash)

    try:
        record = get_record(fingerprint.combined_hash)
        verified = True
        mismatch_reason = None
    except NotRegisteredError:
        record = None
        verified = False
        mismatch_reason = "No on-chain record matches the freshly recomputed hash -- content or metadata has changed since it was registered, or it was never registered."
    except (ChainConfigError, ChainConnectionError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ChainError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return JSONResponse(
        {
            "recomputed_image_hash": fingerprint.image_hash,
            "recomputed_combined_hash": fingerprint.combined_hash,
            "recomputed_perceptual_hash": fingerprint.perceptual_hash,
            "perceptual_similarity": perceptual_similarity,
            "on_chain_record": record,
            "verified": verified,
            "mismatch_reason": mismatch_reason,
        }
    )


async def _ndjson(events: AsyncIterator[dict]) -> AsyncIterator[bytes]:
    async for event in events:
        yield (json.dumps(event) + "\n").encode("utf-8")


@app.post("/api/pipeline/detect-and-search")
async def pipeline_detect_and_search(file: UploadFile = File(...)) -> StreamingResponse:
    """Stages 1-2, streamed as newline-delimited JSON status events. Stops
    after Stage 2 so the frontend can present candidates for selection."""
    image_bytes = await file.read()
    return StreamingResponse(_ndjson(run_detect_and_search(image_bytes)), media_type="application/x-ndjson")


@app.post("/api/pipeline/process-match")
async def pipeline_process_match(
    matches: list[dict] = Body(...),
    face_encoding: list[float] | None = Body(None),
) -> StreamingResponse:
    """Stages 3-6 for the ranked candidate list, streamed as newline-delimited
    JSON status events. Tries candidates in order, skipping any whose image
    fetch genuinely fails, until one succeeds; ends with a "complete" event
    carrying the final result card data. `face_encoding` (from Stage 1) is
    optional so this endpoint keeps working standalone without it."""
    return StreamingResponse(
        _ndjson(run_process_match(matches, face_encoding)), media_type="application/x-ndjson"
    )


# Mounted last so it doesn't shadow the /api/* routes above.
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
