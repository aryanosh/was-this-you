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
HARDHAT_RPC_URL = get_env("HARDHAT_RPC_URL") or "http://127.0.0.1:8545"
HARDHAT_PRIVATE_KEY = get_env("HARDHAT_PRIVATE_KEY")
CONTRACT_ADDRESS = get_env("CONTRACT_ADDRESS")
