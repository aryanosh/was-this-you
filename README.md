# Face ID + Blockchain Verification

Built for HH Goa 2026 Task 3.

A pipeline that takes a photo, detects and encodes the face in it, runs a
**genuine, live reverse-image search** to find real places on the web where
that exact photo has been reused, and writes a tamper-evident fingerprint of
what it finds to a **blockchain** — so the discovery can be independently
re-verified later even if the original post is edited or deleted.

Practically: this is a tool for checking whether one of your own photos has
been reused elsewhere without your consent (e.g. a fake/impersonating social
media profile using your picture), with a durable, checkable record of what
was found and when — not just a screenshot someone can dispute.

## Pipeline stages

1. **Face detection & encoding** (`backend/app/face_detect.py`) — `face_recognition`
   (dlib HOG detector + a 128-d ResNet embedding) detects and encodes the
   face(s) in the uploaded image. This is a validation/identity step; it
   confirms a real face is present and produces an encoding, but the search
   in Stage 2 runs on the whole image (see "Known limitations" — this is a
   reverse-*image* search, not a biometric face search).
2. **Reverse image search** (`backend/app/reverse_search.py`) — uploads the
   image to SerpApi and queries its Google Lens engine for real visual
   matches across the web. No cached/sample responses are ever substituted;
   a genuine zero-match result is surfaced as an error, not papered over.
3. **Match selection & content fetch** (`backend/app/fetch_match.py`) — fetches
   the actual matched image bytes plus best-effort page metadata (caption,
   title) from the real source URL. Gracefully degrades (with a visible
   warning, not a crash) when a platform blocks scraping — LinkedIn, DNA
   India, and similar sites do this routinely.
4. **Fingerprinting** (`backend/app/fingerprint.py`) — SHA-256 of the fetched
   image bytes, plus SHA-256 of a canonical JSON blob binding that hash to
   its provenance (see "Fingerprint design" below).
5. **Blockchain upload** (`contracts/`, `backend/app/chain_client.py`) — signs
   and sends a real transaction registering the fingerprint hash on a local
   Hardhat chain, returning a real transaction hash and block number.
6. **Re-verification** (`backend/app/chain_client.py`, `pipeline.py`) —
   independently re-fetches the original image, recomputes both hashes from
   scratch, and does a fresh on-chain read keyed on the recomputed hash. No
   in-memory value from Stage 4/5 is reused — a genuinely tampered/changed
   source will produce a real `MISMATCH`, not a cached false positive.

A single-page frontend (`frontend/`) drives all six stages from one photo
upload, with live per-stage status and a final result card (matched post,
both hashes, tx hash + block number, VERIFIED/MISMATCH).

## Tech stack

- **Backend:** Python 3.11, FastAPI + Uvicorn
- **Face detection:** `face_recognition` (dlib), via the `dlib-bin` prebuilt
  wheel — see "Setup" for why
- **Reverse image search:** SerpApi (Google Lens engine)
- **Content fetch:** `requests` + `BeautifulSoup4`
- **Hashing:** `hashlib.sha256` (stdlib)
- **Blockchain:** Solidity contract on a local Hardhat network, deployed via
  a Hardhat/ethers.js script, read/written from Python via `web3.py`
- **Frontend:** static HTML/CSS/vanilla JS, served by FastAPI

## Which blockchain, and why

A **local Hardhat network** (`npx hardhat node`), not a public testnet or
mainnet. This is explicitly permitted by the task ("any blockchain may be
used — public testnet, mainnet, or a local/simulated chain"). Reasons:

- **Demo reliability.** A hackathon demo/screen-recording can't depend on
  testnet faucet availability, RPC provider uptime, or confirmation times —
  a local chain is instant and fully under our control.
- **No key management risk.** Hardhat prints pre-funded, well-known
  local-only dev accounts on startup; one of those signs transactions. No
  real private key or real funds are ever involved.
- **The mechanism is identical either way.** The contract, the
  submit/read/re-verify logic, and the tamper-evidence guarantees are
  exactly what they'd be on a public chain — only the RPC endpoint would
  change. Swapping `HARDHAT_RPC_URL` for a public testnet endpoint and
  funding a real testnet account would be the only change needed to move
  this off localhost.

## Fingerprint design

Two hashes are computed per match, both SHA-256:

1. `image_hash` — the raw bytes of the fetched image.
2. `combined_hash` — of the canonical JSON blob:
   ```json
   {"image_hash": "...", "url": "...", "caption": "...", "scraped_at": "..."}
   ```
   serialized with sorted keys and no incidental whitespace
   (`json.dumps(blob, sort_keys=True, separators=(",", ":"))`), so the same
   blob always hashes identically regardless of dict ordering.

This is a deliberate design choice: `combined_hash` (not just `image_hash`)
is what's written on-chain, so the on-chain record attests not just "this
exact image existed" but "this exact image was found at this URL with this
caption at this time." Re-verification therefore checks the *whole claim* —
if the image is unchanged but the caption or URL is edited, that's a real,
detectable mismatch, not a false VERIFIED.

## Setup

### Prerequisites

- **Python 3.11 specifically** (not whatever your system default is —
  prebuilt Windows wheels for `dlib` only exist up to Python 3.11/3.12; on
  Python 3.13+ you'd need CMake + Visual Studio C++ build tools to compile it
  from source, which is unnecessary if 3.11 is available)
- Node.js (tested on v24) and npm
- A SerpApi account/API key (free tier: 250 searches/month) — https://serpapi.com/manage-api-key

### Install

```bash
# Backend
py -3.11 -m venv backend/.venv
backend/.venv/Scripts/pip install -r backend/requirements.txt

# Contracts
cd contracts
npm install
npx hardhat compile
cd ..
```

### Configure

```bash
cp .env.example .env
```

Fill in `.env`:
- `SERPAPI_KEY` — from https://serpapi.com/manage-api-key
- `HARDHAT_RPC_URL` — leave as `http://127.0.0.1:8545` (default)
- `HARDHAT_PRIVATE_KEY` — one of the pre-funded account keys Hardhat prints
  when you run `npx hardhat node` (see below); these are public, well-known,
  local-only test keys
- `CONTRACT_ADDRESS` — filled in after you deploy (see below)

### Run end-to-end

Three terminals, all from the repo root unless noted:

**1. Local blockchain:**
```bash
cd contracts
npx hardhat node
```
Leave this running. Copy `Account #0`'s private key into `.env` as
`HARDHAT_PRIVATE_KEY` the first time.

**2. Deploy the contract** (once per fresh `hardhat node` run — its state is
in-memory and resets when the node restarts):
```bash
cd contracts
npx hardhat run scripts/deploy.js --network localhost
```
Copy the printed address into `.env` as `CONTRACT_ADDRESS`.

**3. Backend + frontend:**
```bash
cd backend
.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```
Open **http://127.0.0.1:8000**, upload a photo, and watch the six stages run
live. Manual match selection is the default; check "Auto-select top match"
for a faster hands-off run.

### Tests

```bash
cd contracts
npx hardhat test
```
Covers the smart contract: registering a fingerprint, rejecting a duplicate
registration, reading back a record, reading an unregistered hash, and
per-submitter record isolation.

## Known limitations

- **Reverse-image search, not biometric face search.** This tool finds
  places on the web where *this specific photo* (or a near-duplicate/crop/
  edit of it) has been reused — it does not find *different* photos of the
  same person's face. Catching "someone posted an unrelated photo of me"
  would require a dedicated facial-recognition-across-the-web service (e.g.
  PimEyes, FaceCheck.ID), which is out of scope here, both technically (a
  different, paid API) and deliberately, given how much more sensitive
  biometric identity search across the open web is than checking whether one
  specific image was reused.
- **Search match quality depends on prior public indexing of the image.** If
  a photo has never been publicly posted anywhere before, reverse image
  search has nothing to find — that's a real "no matches found" result, not
  a bug.
- **No liveness detection.** Face detection confirms a face is present in
  the uploaded image; it does not verify the image was captured live from a
  real person in front of a camera.
- **The fingerprint covers fetched content, not a full page render.** The
  hash binds the fetched image bytes plus URL/caption/scrape-time metadata
  scraped at the time of fetch — not a full snapshot of the page's visual
  rendering, embedded scripts, or content added dynamically after load.
  Some platforms (LinkedIn, DNA India, and others) block automated page
  scraping outright; in that case caption/title metadata gracefully falls
  back to what the search engine itself provided, with a visible warning —
  the image fingerprint itself is unaffected.
- **Local chain means no public block-explorer link.** Since this runs on an
  ephemeral local Hardhat network (see "Which blockchain, and why"), there's
  no public URL to independently inspect the transaction the way there would
  be on a testnet/mainnet explorer. Re-verification is still fully
  demonstrated end-to-end (Stage 6 re-derives everything from scratch against
  the live local chain) — it just isn't independently checkable by a third
  party without access to this machine's chain state. Moving to a public
  testnet would only require changing `HARDHAT_RPC_URL` and funding a real
  testnet account.
- **Free-tier SerpApi quota.** 250 searches/month on the free plan used for
  this build; each pipeline run (or retry) consumes one search.

## Ethics note

This tool is demoed on public figures' publicly available photos for
reproducibility. It is not intended for non-consensual lookup of private
individuals — its intended, legitimate use is checking whether *your own*
photos have been reused elsewhere without your consent (e.g. detecting a
fake profile impersonating you), not searching for other people without
their knowledge or consent.
