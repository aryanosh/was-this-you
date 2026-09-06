# Was This You? 👁️ ⛓️
### Deep Biometric Face Identification & Forensic Blockchain Provenance

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Solidity](https://img.shields.io/badge/Solidity-^0.8.24-363636?logo=solidity&logoColor=white)](https://soliditylang.org)
[![Ethereum Sepolia](https://img.shields.io/badge/Ethereum-Sepolia_Testnet-627EEA?logo=ethereum&logoColor=white)](https://sepolia.etherscan.io)
[![PyTorch ViT](https://img.shields.io/badge/AI_Model-ViT_Deepfake_Detector-EE4C2C?logo=pytorch&logoColor=white)](https://huggingface.co/dima806/deepfake_vs_real_image_detection)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)


> *A production-grade pipeline that detects faces, validates authenticity against AI-generation, searches web appearances specifically using face-cropped biometrics, filters out false visual matches with an on-the-fly facial verification gate, and permanently anchors dual cryptographic + perceptual fingerprints on Ethereum.*

---

## ⚡ Engineering Challenges in Face Search & Provenance

Building an automated pipeline that traces face appearances across the web and certifies them on a public ledger involves critical technical hurdles:

1. **Scenery Bias & False Positives**: Naive reverse image searches frequently match background environments, lighting, and clothing rather than facial features, leading to false visual matches.
2. **Synthetic & Deepfake Content**: AI-generated portraits need to be screened early in the ingestion lifecycle to evaluate authenticity before web discovery.
3. **Candidate Verification**: Search engines often return dozens of noisy results; candidates must be biometrically cross-referenced against the original face encoding to filter out false leads.
4. **Resilience to Innocent Transformations**: While SHA-256 is ideal for strict byte-level provenance, minor compression or metadata changes break traditional hashes. A dual-fingerprint architecture pairing SHA-256 with perceptual hashing (pHash) provides both mathematical tamper-evidence and visual similarity metrics.

---

## 🎯 Key Capabilities

* **Contextual Face-Cropped Search**: Isolates the face bounding box with 50% contextual padding to focus search queries strictly on facial biometrics rather than background scenery.
* **Multi-Engine Reverse Search**: Automatically routes through Yandex Images and Bing Reverse Image with Google Lens fallback for comprehensive face discovery.
* **Stage 2.5 Biometric Verification Gate**: Downloads search candidate thumbnails, extracts facial landmarks, and evaluates biometric Euclidean distance against the source face—filtering out matches below 55% similarity and prioritizing high-confidence matches.
* **ViT Deepfake Classification**: Employs an on-device Vision Transformer (`dima806/deepfake_vs_real_image_detection`) to compute real-time authenticity confidence before querying the web.
* **Dual Fingerprint Generation**: Produces a canonical SHA-256 hash (binding image bytes, URL, caption, and scrape timestamp) alongside a 64-bit DCT perceptual hash (pHash).
* **Ethereum Sepolia Blockchain Anchoring**: Submits raw signed EIP-155 transactions to deploy and interact with `FingerprintRegistry.sol` on public testnet with verifiable public Etherscan transaction logs.
* **Interactive Tamper Playground**: Built-in verification sandbox allowing live simulation of content or URL tampering to verify cryptographic immutability and visual similarity.
* **Real-time Streaming Chain-of-Thought**: NDJSON events stream bounding box coordinates, candidates, fetch attempts, and gas metrics in real time.

---

## 🛠️ Complete Pipeline Architecture

```mermaid
flowchart TD
    A[📷 Upload Photo] --> B[🔍 Detect & Encode Face<br/>128-d Biometric Vector]
    B --> B2[✂️ Crop Contextual Face Region]
    B --> C1[🛡️ ViT Deepfake / Synthetic Check<br/>Real-time Authenticity Score]
    B2 --> C[🌐 Reverse Image Search<br/>Yandex & Bing primary, Google Lens fallback]
    C1 -. Advisory signal .-> C
    C --> V{🎯 Stage 2.5 Biometric Gate<br/>Face similarity ≥ 55%?}
    V -- 0 matches from crop --> C2[🔁 Retry search with full photo]
    C2 --> V
    V -- Match passed --> D[📥 Fetch Top Verified Candidate<br/>Image bytes + OpenGraph metadata]
    D -- Blocked / 403 --> D2[⏭️ Auto-fallback to next verified candidate]
    D2 --> D
    D -- Success --> D3[🔬 Re-detect face in downloaded match<br/>Calibrate Euclidean biometric confidence]
    D3 --> E[🔐 Dual Fingerprint Generation<br/>SHA-256 + Perceptual pHash]
    E --> F[⛓️ Submit Raw Tx to Ethereum<br/>FingerprintRegistry.sol on Sepolia]
    F --> G[🕵️ Independent Re-verification<br/>Fresh fetch + Re-hash + Chain read]
    G --> H{Hashes match?}
    H -- Yes --> I[✅ VERIFIED]
    H -- No --> J[❌ MISMATCH]
```

### 🔬 Stage-by-Stage Breakdown

| Stage | Name | Technical Implementation |
| :---: | :--- | :--- |
| **1** | **Face Detection & Encoding** | Uses `dlib`'s HOG detector + a 128-dimensional ResNet model via `face_recognition`. Generates facial landmarks, bounding boxes, and an invariant embedding vector. |
| **1.5**| **Deepfake / AI-Generated Check** | Evaluates the face using a pre-trained Vision Transformer (`dima806/deepfake_vs_real_image_detection`) on PyTorch. Outputs an advisory confidence score (e.g. `Real: 99%`). |
| **2** | **Face-Cropped Multi-Search** | Encodes the cropped face as JPEG bytes. Queries **Yandex Images** and **Bing Reverse Image** via SerpApi (utilizing ephemeral auto-deleting imgbb hosting for image URL access) with **Google Lens** as a fallback. |
| **2.5**| **Biometric Verification Gate** | Downloads thumbnails of all candidates, detects faces in each, and evaluates biometric Euclidean distance against the original face. **Candidates below 55% similarity are rejected immediately.** |
| **3** | **Content & Provenance Ingestion** | Ingests full-resolution image bytes and parses Open Graph / Twitter Card metadata. Re-measures the face similarity between the original photo and the matched post. |
| **4** | **Dual Fingerprinting** | Computes: <br>1. **SHA-256 Image Hash** (raw payload) <br>2. **Canonical Combined Hash**: `SHA-256({image_hash, url, caption, scraped_at})` <br>3. **Perceptual Hash (pHash)**: 64-bit DCT frequency fingerprint via `imagehash`. |
| **5** | **Smart Contract Registration** | Signs an EIP-155 raw transaction with `web3.py` and writes the combined hash to `FingerprintRegistry.sol`. Emits indexed event logs with gas profiling. |
| **6** | **Independent Proof & Re-Verification** | Without reusing cached memory, performs a fresh HTTP GET of the external image, recomputes all hashes, and reads the blockchain state to prove tamper-evidence. |

---

## 🔐 Smart Contract Architecture (`FingerprintRegistry.sol`)

Deployed on **Ethereum Sepolia** testnet (`^0.8.24`):

```solidity
contract FingerprintRegistry {
    struct Record {
        uint256 timestamp;
        address submitter;
        string sourceUrl;
    }

    mapping(bytes32 => Record) private records;

    event FingerprintRegistered(
        bytes32 indexed fingerprintHash,
        address indexed submitter,
        uint256 timestamp,
        string sourceUrl
    );

    error AlreadyRegistered(bytes32 fingerprintHash);
    error NotRegistered(bytes32 fingerprintHash);

    function registerFingerprint(bytes32 fingerprintHash, string calldata sourceUrl) external {
        if (records[fingerprintHash].timestamp != 0) {
            revert AlreadyRegistered(fingerprintHash);
        }
        records[fingerprintHash] = Record({
            timestamp: block.timestamp,
            submitter: msg.sender,
            sourceUrl: sourceUrl
        });
        emit FingerprintRegistered(fingerprintHash, msg.sender, block.timestamp, sourceUrl);
    }
}
```

* **First-Write Tamper Immutability**: Any attempt to overwrite an existing record reverts with custom error `AlreadyRegistered`, preserving undisputed historical priority.
* **Gas-Optimized Custom Errors**: Uses zero-string revert selectors (`AlreadyRegistered`, `NotRegistered`) saving runtime deployment and transaction gas.

---

## 🚀 Getting Started

### Prerequisites
* **Python 3.10+**
* **Node.js 18+** & `npm`
* A **SerpApi Key** ([Free tier: 250 searches/month](https://serpapi.com))
* *(Optional)* An **Imgbb API Key** ([Free anonymous image host](https://api.imgbb.com)) for unlocking Yandex & Bing face engines.

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/aryanosh/was-this-you.git
cd was-this-you

# Set up Python virtual environment & dependencies
python -m venv backend/.venv
backend/.venv/Scripts/pip install -r backend/requirements.txt

# Install Hardhat smart contract dependencies
cd contracts
npm install
npx hardhat compile
cd ..
```

### 2. Environment Configuration

Copy `.env.example` to `.env`:

```env
# Required for web search
SERPAPI_KEY=your_serpapi_key_here

# Optional: Unlocks Yandex & Bing multi-engine search
IMGBB_API_KEY=your_imgbb_key_here

# Mode A: Local Hardhat Development
HARDHAT_RPC_URL=http://127.0.0.1:8545
HARDHAT_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
CONTRACT_ADDRESS=your_deployed_contract_address

# Mode B: Ethereum Sepolia Testnet (For submission & live demo)
CHAIN_RPC_URL=https://sepolia.infura.io/v3/YOUR_INFURA_KEY
CHAIN_PRIVATE_KEY=your_funded_sepolia_private_key
BLOCK_EXPLORER_URL=https://sepolia.etherscan.io
```

---

### 3. Running the Application

#### Option A: Local Hardhat Network (Instant testing)

```powershell
# Terminal 1: Start local node
cd contracts
npx hardhat node

# Terminal 2: Deploy contract to local network
cd contracts
npx hardhat run scripts/deploy.js --network localhost
# (Copy printed contract address into .env as CONTRACT_ADDRESS)

# Terminal 3: Start the FastAPI backend & UI
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

#### Option B: Sepolia Testnet (Production / Hackathon Submission)

```powershell
# 1. Deploy contract directly to Sepolia
cd contracts
npx hardhat run scripts/deploy.js --network sepolia
# (Copy printed contract address into .env as CONTRACT_ADDRESS)

# 2. Launch backend
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Visit **`http://127.0.0.1:8000`** in your browser.

---

## 🧪 Testing

### Smart Contract Test Suite (Mocha/Chai)
```bash
cd contracts
npx hardhat test
```
*Tests coverage: registration immutability, custom revert validation, duplicate collision rejection, and multi-submitter account isolation.*

---

## 🛡️ Ethical Framework & Safety

1. **Self-Provenance Only**: Designed for victims of digital impersonation to locate where their own portraits are being scraped and misused.
2. **Ephemeral Public Cache**: When `IMGBB_API_KEY` is utilized for multi-engine routing, uploaded search images are programmed for immediate deletion upon search completion (and auto-expire within 60 seconds on the host).
3. **No Biometric Data On-Chain**: To comply with data privacy standards (GDPR, EU AI Act), raw face coordinates and 128-dimensional biometric embeddings are **never** stored on the public ledger—only content fingerprints of publicly discovered web artifacts are anchored.

---

## 📄 License

Distributed under the **MIT License**. See `LICENSE` for details.
