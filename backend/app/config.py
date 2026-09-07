"""Central place for reading configuration from environment variables.

Nothing here has a hardcoded secret or fallback value for anything sensitive --
missing required variables are surfaced as clear errors at the point they're
actually needed, not guessed at import time.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py -> backend/ -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_REPO_ROOT / ".env")


def get_env(name: str, *, required: bool = False) -> str | None:
    value = os.environ.get(name)
    if required and not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            f"Copy .env.example to .env at the repo root and fill it in."
        )
    return value


SERPAPI_KEY = get_env("SERPAPI_KEY")

# Optional. Unlocks Yandex/Bing reverse-image search (both need a real public
# image URL, which SerpApi's own upload endpoint doesn't provide) by getting
# one from a free imgbb.com account. Without it, search falls back to
# Google Lens only -- exactly the pre-existing behavior.
IMGBB_API_KEY = get_env("IMGBB_API_KEY")

# CHAIN_RPC_URL/CHAIN_PRIVATE_KEY are the current names; HARDHAT_RPC_URL/
# HARDHAT_PRIVATE_KEY are kept as fallbacks for backward compatibility with
# existing .env files that predate the Sepolia support.
HARDHAT_RPC_URL = get_env("HARDHAT_RPC_URL") or "http://127.0.0.1:8545"
HARDHAT_PRIVATE_KEY = get_env("HARDHAT_PRIVATE_KEY")
CHAIN_RPC_URL = get_env("CHAIN_RPC_URL") or HARDHAT_RPC_URL
CHAIN_PRIVATE_KEY = get_env("CHAIN_PRIVATE_KEY") or HARDHAT_PRIVATE_KEY
BLOCK_EXPLORER_URL = get_env("BLOCK_EXPLORER_URL")
CONTRACT_ADDRESS = get_env("CONTRACT_ADDRESS")

# Face verification: minimum biometric similarity (0-100%) a search-result
# candidate must reach to be accepted as the same person.  62% corresponds
# to a Euclidean distance of ~0.46 in face_recognition's 128-d embedding
# space — comfortably inside the library's own 0.6 "same person" threshold,
# with margin against borderline false positives.
FACE_SIMILARITY_THRESHOLD = float(get_env("FACE_SIMILARITY_THRESHOLD") or "62.0")
