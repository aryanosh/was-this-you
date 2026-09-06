"""Stage 5/6: read/write bridge to the local Hardhat chain via web3.py.

Every write is a real signed transaction sent to a real (local, ephemeral)
Hardhat network; every read is a fresh RPC call against the live contract
state, never a cached/in-memory value from a prior call in this process.
"""
from __future__ import annotations

import json
from pathlib import Path

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError

from app.config import BLOCK_EXPLORER_URL, CHAIN_PRIVATE_KEY, CHAIN_RPC_URL, CONTRACT_ADDRESS

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARTIFACT_PATH = (
    _REPO_ROOT
    / "contracts"
    / "artifacts"
    / "contracts"
    / "FingerprintRegistry.sol"
    / "FingerprintRegistry.json"
)

# Chain IDs of well-known local dev networks -- anything else is treated as a
# real network and gets an explicit chainId set on the transaction.
_LOCAL_CHAIN_IDS = {31337, 1337}
SEPOLIA_CHAIN_ID = 11155111


class ChainError(Exception):
    """Base class for all Stage 5/6 errors."""


class ChainConfigError(ChainError):
    pass


class ChainConnectionError(ChainError):
    pass


class AlreadyRegisteredError(ChainError):
    pass


class NotRegisteredError(ChainError):
    pass


def _load_abi() -> list:
    if not _ARTIFACT_PATH.exists():
        raise ChainConfigError(
            f"Contract artifact not found at {_ARTIFACT_PATH}. "
            "Run `npx hardhat compile` in contracts/ first."
        )
    with open(_ARTIFACT_PATH) as f:
        artifact = json.load(f)
    return artifact["abi"]


def _get_web3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(CHAIN_RPC_URL, request_kwargs={"timeout": 10}))
    try:
        connected = w3.is_connected()
    except Exception as exc:
        raise ChainConnectionError(f"Could not reach the chain RPC at {CHAIN_RPC_URL}: {exc}") from exc
    if not connected:
        raise ChainConnectionError(
            f"Chain RPC at {CHAIN_RPC_URL} is not responding. "
            "For local dev, make sure `npx hardhat node` is running; for Sepolia, check CHAIN_RPC_URL."
        )
    return w3


def _get_contract(w3: Web3):
    if not CONTRACT_ADDRESS:
        raise ChainConfigError(
            "CONTRACT_ADDRESS is not set. Deploy the contract "
            "(npx hardhat run scripts/deploy.js --network localhost) and set it in .env."
        )
    abi = _load_abi()
    return w3.eth.contract(address=Web3.to_checksum_address(CONTRACT_ADDRESS), abi=abi)


def _hex_to_bytes32(hash_hex: str) -> bytes:
    clean = hash_hex[2:] if hash_hex.startswith("0x") else hash_hex
    b = bytes.fromhex(clean)
    if len(b) != 32:
        raise ChainError(f"Expected a 32-byte (64 hex char) hash, got {len(b)} bytes: {hash_hex}")
    return b


def submit_fingerprint(combined_hash_hex: str, source_url: str) -> dict:
    """Signs and sends a real transaction registering the hash on-chain.

    Returns the actual tx hash + block number read back from a mined receipt
    -- never a placeholder.
    """
    if not CHAIN_PRIVATE_KEY:
        raise ChainConfigError(
            "CHAIN_PRIVATE_KEY is not set. For local dev, copy one of the pre-funded account "
            "keys printed by `npx hardhat node` into .env; for Sepolia, use a funded testnet key."
        )

    w3 = _get_web3()
    contract = _get_contract(w3)
    account = Account.from_key(CHAIN_PRIVATE_KEY)
    hash_bytes = _hex_to_bytes32(combined_hash_hex)

    try:
        chain_id = w3.eth.chain_id
        tx_params = {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
        }
        if chain_id not in _LOCAL_CHAIN_IDS:
            tx_params["chainId"] = chain_id
        tx = contract.functions.registerFingerprint(hash_bytes, source_url or "").build_transaction(tx_params)
        signed = account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = w3.eth.send_raw_transaction(raw)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    except Exception as exc:
        err_str = str(exc)
        if "AlreadyRegistered" in err_str or "77caf672" in err_str:
            raise AlreadyRegisteredError(
                f"This exact fingerprint hash is already registered on-chain: {combined_hash_hex}"
            ) from exc
        raise ChainError(f"Failed to submit fingerprint to the chain: {exc}") from exc

    tx_hash_hex = "0x" + receipt["transactionHash"].hex()
    result = {
        "tx_hash": tx_hash_hex,
        "block_number": receipt["blockNumber"],
        "submitter": account.address,
        "submitter_address": account.address,
        "status": receipt["status"],
        "gas_used": receipt["gasUsed"],
        "contract_address": contract.address,
        "chain_id": chain_id,
    }
    if BLOCK_EXPLORER_URL:
        result["block_explorer_url"] = f"{BLOCK_EXPLORER_URL.rstrip('/')}/tx/{tx_hash_hex}"
    return result


def get_record(combined_hash_hex: str) -> dict:
    """Fresh, independent on-chain read -- no caching, no reuse of a prior
    submission's in-memory result. Call this again each time re-verification
    runs."""
    w3 = _get_web3()
    contract = _get_contract(w3)
    hash_bytes = _hex_to_bytes32(combined_hash_hex)

    try:
        timestamp, submitter, source_url = contract.functions.getRecord(hash_bytes).call()
    except Exception as exc:
        err_str = str(exc)
        if "NotRegistered" in err_str or "8cc00d16" in err_str:
            raise NotRegisteredError(f"No on-chain record exists for hash {combined_hash_hex}") from exc
        raise ChainError(f"Failed to read from the chain: {exc}") from exc

    return {"timestamp": timestamp, "submitter": submitter, "source_url": source_url}
