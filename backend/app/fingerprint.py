"""Stage 4: SHA-256 fingerprinting of fetched content.

Two hashes are computed:
  1. `image_hash` -- SHA-256 of the raw image bytes actually fetched in Stage 3.
  2. `combined_hash` -- SHA-256 of a canonical JSON blob
     {image_hash, url, caption, scraped_at}, i.e. the image fingerprint plus
     the provenance data collected alongside it. This is a deliberate design
     choice (documented in the README): binding the image hash to the URL,
     caption, and scrape time it was found with means the on-chain record
     attests not just "this exact image existed" but "this exact image was
     found at this URL with this caption at this time" -- which is what
     later gets re-verified in Stage 6.

Nothing here ever hashes anything other than the bytes/fields actually
produced by Stage 3's live fetch.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass
class Fingerprint:
    image_hash: str
    combined_hash: str
    blob: dict

    def to_dict(self) -> dict:
        return {
            "image_hash": self.image_hash,
            "combined_hash": self.combined_hash,
            "blob": self.blob,
        }


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_fingerprint(image_bytes: bytes, url: str | None, caption: str | None, scraped_at: str) -> Fingerprint:
    image_hash = sha256_hex(image_bytes)

    blob = {
        "image_hash": image_hash,
        "url": url or "",
        "caption": caption or "",
        "scraped_at": scraped_at,
    }
    # Canonical serialization (sorted keys, no incidental whitespace) so the
    # same blob always hashes to the same value regardless of dict ordering.
    blob_json = json.dumps(blob, sort_keys=True, separators=(",", ":"))
    combined_hash = sha256_hex(blob_json.encode("utf-8"))

    return Fingerprint(image_hash=image_hash, combined_hash=combined_hash, blob=blob)
