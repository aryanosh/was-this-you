const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const previewWrap = document.getElementById("preview-wrap");
const previewImg = document.getElementById("preview-img");
const previewOverlay = document.getElementById("preview-overlay");
const dropzoneText = document.getElementById("dropzone-text");
const submitBtn = document.getElementById("submit-btn");
const submitLabel = document.getElementById("submit-label");
const traceEl = document.getElementById("trace");
const errorBanner = document.getElementById("error-banner");
const stepIndicatorEl = document.getElementById("step-indicator");
const reverifyPanel = document.getElementById("reverify-panel");
const reverifyUrlInput = document.getElementById("reverify-url");
const reverifyCaptionInput = document.getElementById("reverify-caption");
const reverifyBtn = document.getElementById("reverify-btn");
const reverifyResultEl = document.getElementById("reverify-result");

function showError(message) {
  console.error("[pipeline]", message);
  errorBanner.textContent = message;
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
  errorBanner.textContent = "";
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function hostnameOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url || "";
  }
}

function formatTimestamp(unixSeconds) {
  try {
    return new Date(Number(unixSeconds) * 1000).toLocaleString();
  } catch {
    return String(unixSeconds);
  }
}

// ---- File preview -------------------------------------------------------

function clearOverlay() {
  if (!previewOverlay) return;
  const ctx = previewOverlay.getContext("2d");
  ctx.clearRect(0, 0, previewOverlay.width, previewOverlay.height);
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  previewImg.src = url;
  previewWrap.hidden = false;
  dropzoneText.textContent = file.name;
  clearOverlay();
});

async function loadSamplePhoto(url, filename) {
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const file = new File([blob], filename, { type: "image/jpeg" });
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;
    fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  } catch (err) {
    showError(`Could not load demo photo: ${err.message}`);
  }
}
document.getElementById("load-sample-vk")?.addEventListener("click", () => loadSamplePhoto("/VK.jpg", "VK.jpg"));
document.getElementById("load-sample-b")?.addEventListener("click", () => loadSamplePhoto("/images.jpeg", "images.jpeg"));

function drawFaceBoxes(boxes) {
  if (!previewOverlay || !previewImg.naturalWidth) return;
  previewOverlay.width = previewImg.clientWidth;
  previewOverlay.height = previewImg.clientHeight;
  const scaleX = previewImg.clientWidth / previewImg.naturalWidth;
  const scaleY = previewImg.clientHeight / previewImg.naturalHeight;
  const ctx = previewOverlay.getContext("2d");
  ctx.clearRect(0, 0, previewOverlay.width, previewOverlay.height);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#2f6b3e";
  ctx.font = "bold 12px sans-serif";
  ctx.fillStyle = "#2f6b3e";
  (boxes || []).forEach((box, i) => {
    const x = box.left * scaleX;
    const y = box.top * scaleY;
    const w = (box.right - box.left) * scaleX;
    const h = (box.bottom - box.top) * scaleY;
    ctx.strokeRect(x, y, w, h);
    if (boxes.length > 1) {
      ctx.fillText(`Face ${i + 1}`, x + 2, y > 12 ? y - 4 : y + 12);
    }
  });
}

// ---- Step indicator (one pill per pipeline stage) ----

const STEP_ORDER = ["upload", "detect", "deepfake", "search", "face_verify", "fetch", "fingerprint", "chain_submit", "reverify"];

function resetStepIndicator() {
  STEP_ORDER.forEach((s) => {
    const el = stepIndicatorEl.querySelector(`.step[data-step="${s}"]`);
    el.classList.remove("active", "done", "errored");
  });
  stepIndicatorEl.hidden = false;
}

function markStepActive(stage) {
  const idx = STEP_ORDER.indexOf(stage);
  if (idx < 0) return;
  STEP_ORDER.forEach((s, i) => {
    const el = stepIndicatorEl.querySelector(`.step[data-step="${s}"]`);
    el.classList.remove("active");
    if (i < idx) el.classList.add("done");
    else if (i === idx) el.classList.add("active");
  });
}

function markStepDone(stage) {
  const el = stepIndicatorEl.querySelector(`.step[data-step="${stage}"]`);
  if (!el) return;
  el.classList.remove("active");
  el.classList.add("done");
}

function markStepErrored(stage) {
  const el = stepIndicatorEl.querySelector(`.step[data-step="${stage}"]`);
  if (!el) return;
  el.classList.remove("active");
  el.classList.add("errored");
}

function markAllStepsDone() {
  STEP_ORDER.forEach((s) => {
    const el = stepIndicatorEl.querySelector(`.step[data-step="${s}"]`);
    el.classList.remove("active");
    el.classList.add("done");
  });
}

// ---- Trace log (append-only chain of thought) ---------------------------

const STAGE_ORDER = ["detect", "deepfake", "search", "face_verify", "fetch", "fingerprint", "chain_submit", "reverify"];

const RUNNING_TEXT = {
  detect: "Detecting the face in the photo…",
  deepfake: "Checking whether the image is AI-generated…",
  search: "Searching the web for matching posts…",
  face_verify: "Verifying faces in the search results…",
  fetch: "Fetching the matched post's content…",
  fingerprint: "Computing a SHA-256 fingerprint…",
  chain_submit: "Writing the fingerprint to the blockchain…",
  reverify: "Independently re-verifying the on-chain record…",
};

// One plain-English line per stage, always visible under the heading, so a
// viewer following along without narration knows *why* each step exists.
const STAGE_EXPLAINER = {
  upload: "Loading the photo into the pipeline.",
  detect: "Finds the face and extracts a unique 128-number biometric signature from it.",
  deepfake: "Sanity-checks that the photo itself isn't AI-generated before trusting anything found from it.",
  search: "Runs a live reverse-image search (Yandex first, Google Lens as fallback) using the cropped face.",
  face_verify: "Downloads every search result and re-checks it against the original face — rejects anything that isn't actually the same person.",
  fetch: "Downloads the real image and page behind a verified match, and re-checks the face against the original.",
  fingerprint: "Turns the downloaded content into two tamper-evident fingerprints: an exact hash and a visual hash.",
  chain_submit: "Writes the fingerprint to the blockchain as a permanent, public, timestamped record.",
  reverify: "Independently re-downloads, re-hashes, and re-reads the chain to prove the record hasn't changed.",
};

const MARKERS = {
  running: '<span class="spinner" aria-hidden="true"></span>',
  done: "✓",
  error: "✕",
  skipped: "–",
};

function entryEl(stage) {
  return document.getElementById(`entry-${stage}`);
}

function appendEntry(stage, text) {
  const div = document.createElement("div");
  div.className = "trace-entry state-running";
  div.id = `entry-${stage}`;
  const explainer = STAGE_EXPLAINER[stage];
  div.innerHTML = `
    <div class="trace-line">
      <span class="marker">${MARKERS.running}</span>
      <span class="trace-text">${escapeHtml(text ?? RUNNING_TEXT[stage] ?? stage)}</span>
    </div>
    ${explainer ? `<div class="trace-explainer">${escapeHtml(explainer)}</div>` : ""}
    <div class="trace-detail"></div>
  `;
  traceEl.appendChild(div);
  return div;
}

function settleEntry(stage, state, text, detailHtml) {
  const entry = entryEl(stage) || appendEntry(stage);
  entry.classList.remove("state-running", "state-done", "state-error", "state-skipped");
  entry.classList.add(`state-${state}`);
  entry.querySelector(".marker").innerHTML = MARKERS[state] || "";
  entry.querySelector(".trace-text").textContent = text;
  if (detailHtml !== undefined) {
    entry.querySelector(".trace-detail").innerHTML = detailHtml;
  }
}

function settleUploadEntryIfPresent() {
  const entry = document.getElementById("entry-upload");
  if (!entry || !entry.classList.contains("state-running")) return;
  entry.classList.remove("state-running");
  entry.classList.add("state-done");
  entry.querySelector(".marker").innerHTML = MARKERS.done;
  entry.querySelector(".trace-text").textContent = "Photo uploaded.";
}

const ENGINE_LABELS = {
  yandex: "Yandex",
  bing_reverse_image: "Bing Reverse Image",
  google_lens: "Google Lens",
};

function engineListLabel(engines) {
  if (!engines || !engines.length) return null;
  return engines.map((e) => ENGINE_LABELS[e] || e).join(" + ");
}

function doneText(stage, data) {
  switch (stage) {
    case "detect":
      return data.num_faces === 1 ? "Detected 1 face." : `Detected ${data.num_faces} faces; searching with the largest one.`;
    case "search": {
      const engineLabel = engineListLabel(data.search_engines);
      const engineNote = engineLabel ? ` via ${engineLabel}` : "";
      return `Found ${data.num_matches} raw visual match${data.num_matches === 1 ? "" : "es"}${engineNote} — verifying faces next.`;
    }
    case "face_verify":
      return `${data.passed_count} of ${data.total_checked} candidate${data.total_checked === 1 ? "" : "s"} confirmed as the same face.`;
    case "fetch": {
      const skipped = (data.attempts_tried || 1) - 1;
      const suffix = skipped > 0 ? ` (after skipping ${skipped} unreachable match${skipped === 1 ? "" : "es"})` : "";
      return `Fetched the image and page metadata from ${hostnameOf(data.source_url)}${suffix}.`;
    }
    case "deepfake":
      return `Authenticity check: ${data.label} (${Math.round(data.confidence * 100)}% confidence).`;
    case "fingerprint":
      return "Fingerprint computed.";
    case "chain_submit":
      return `Registered on-chain — block ${data.block_number}.`;
    case "reverify":
      return data.verified ? "Recomputed hash matches the on-chain record." : "Recomputed hash does not match any on-chain record.";
    default:
      return "Done.";
  }
}

function faceSimilarityBadgeHtml(score, found) {
  if (!found || score === null || score === undefined || score < 50) {
    return `<span class="badge badge-grey">Face not confirmed in match</span>`;
  }
  if (score >= 80) {
    return `<span class="badge badge-green">Face Match: ${score.toFixed(1)}%</span>`;
  }
  return `<span class="badge badge-yellow">Possible Match: ${score.toFixed(1)}%</span>`;
}

function matchCardHtml(match, isPicked) {
  const thumb = match.thumbnail || match.image || "";
  const title = match.title || match.source || "Untitled";
  const source = match.source || hostnameOf(match.link);
  return `
    <div class="match-card${isPicked ? " picked" : ""}">
      ${isPicked ? '<span class="match-card-picked-badge">picked</span>' : ""}
      ${thumb ? `<a href="${escapeHtml(match.link || "#")}" target="_blank" rel="noopener"><img src="${escapeHtml(thumb)}" alt="" loading="lazy" /></a>` : ""}
      <div class="match-card-body">
        <div class="match-card-title">${escapeHtml(title)}</div>
        <div class="match-card-domain">${escapeHtml(source)}</div>
      </div>
    </div>
  `;
}

function searchDetailHtml(data) {
  const matches = data.matches || [];
  const domains = new Set(matches.map((m) => hostnameOf(m.link) || m.source).filter(Boolean));
  const modeNote =
    data.search_mode === "full_image"
      ? `<div class="match-stats">Found using the full photo (the cropped face alone returned no verified matches).</div>`
      : "";
  const reducedAccuracyBanner = data.reduced_accuracy
    ? `<div class="reduced-accuracy-banner">⚠ Reduced accuracy mode — only Google Lens was available. ${escapeHtml(data.fallback_reason || "Yandex/Bing unavailable.")} Results may match scenery instead of faces.</div>`
    : "";
  const engineLabelForDetail = engineListLabel(data.search_engines);
  const engineNote = engineLabelForDetail
    ? engineLabelForDetail === "Google Lens"
      ? `<div class="match-stats">Found via Google Lens (Yandex/Bing returned no matches, or no IMGBB_API_KEY is configured).</div>`
      : `<div class="match-stats">Found via ${engineLabelForDetail}.</div>`
    : "";
  const stats = `<div class="match-stats">${matches.length} raw visual match${matches.length === 1 ? "" : "es"} found across ${domains.size} platform${domains.size === 1 ? "" : "s"} — not yet face-verified.</div>`;
  const grid = `<div class="match-grid">${matches.map((m) => matchCardHtml(m, false)).join("")}</div>`;
  return reducedAccuracyBanner + modeNote + engineNote + stats + grid;
}

function faceVerifyCandidateLineHtml(c) {
  const domain = c.source || c.title || "unknown source";
  const engineTag = c.search_engine ? ` [${ENGINE_LABELS[c.search_engine] || c.search_engine}]` : "";
  let statusText;
  let cls;
  let icon;
  if (c.undetermined) {
    statusText = `${c.reject_reason || "image too small"} → UNDETERMINED`;
    cls = "fa-undetermined";
    icon = "?";
  } else if (!c.face_found) {
    const reason = c.reject_reason && c.reject_reason.toLowerCase().includes("download")
      ? "couldn't download image"
      : "no face found";
    statusText = `${reason} → REJECTED`;
    cls = "fa-blocked";
    icon = "✕";
  } else if (c.passed) {
    statusText = `${c.face_similarity}% → CONFIRMED`;
    cls = "fa-ok";
    icon = "✓";
  } else {
    statusText = `${c.face_similarity}% → REJECTED`;
    cls = "fa-blocked";
    icon = "✕";
  }
  return `<div class="fetch-attempt-line ${cls}"><span class="fa-icon">${icon}</span>${escapeHtml(domain)}${escapeHtml(engineTag)} → ${escapeHtml(statusText)}</div>`;
}

function faceVerifyCandidatesListHtml(candidates) {
  if (!candidates.length) return "";
  return `<div class="fetch-attempts">${candidates.map(faceVerifyCandidateLineHtml).join("")}</div>`;
}

function faceVerifyDetailHtml(data) {
  const summary = `<div class="match-stats">${data.passed_count} of ${data.total_checked} candidate${data.total_checked === 1 ? "" : "s"} confirmed as the same face (best match: ${data.best_similarity}%).</div>`;
  return faceVerifyCandidatesListHtml(currentFaceVerifyCandidates) + summary;
}

function detectDetailHtml(data) {
  const box = (data.boxes || [])[0];
  const boxLine = box
    ? `<div class="hash-line"><span>Bounding box</span><span>(${box.left}, ${box.top}) → (${box.right}, ${box.bottom})</span></div>`
    : "";
  const thumb = data.cropped_face_b64
    ? `<div class="trace-match">
        <img src="data:image/jpeg;base64,${data.cropped_face_b64}" alt="Cropped face" />
        <div class="trace-match-body">
          <span>Searching with this face →</span>
          <div class="trace-match-source">128-dimensional biometric encoding computed</div>
        </div>
      </div>`
    : "";
  return thumb + boxLine;
}

function fetchAttemptsListHtml(attempts) {
  if (!attempts.length) return "";
  const lines = attempts.map((a) => {
    const domain = a.source || (a.title ? a.title : "");
    return `<div class="fetch-attempt-line fa-blocked"><span class="fa-icon">✕</span>${a.attempt}/${a.total} ${escapeHtml(domain)} → ${escapeHtml(a.message || "unreachable")}</div>`;
  });
  return `<div class="fetch-attempts">${lines.join("")}</div>`;
}

function contentPreviewCardHtml(data) {
  return `
    <div class="content-preview-card">
      ${data.image_url ? `<img src="${escapeHtml(data.image_url)}" alt="" loading="lazy" />` : ""}
      <div class="content-preview-body">
        <div class="cp-title">${escapeHtml(data.page_title || "Untitled")}</div>
        ${data.caption ? `<div class="cp-caption">"${escapeHtml(data.caption)}"</div>` : ""}
        <div class="cp-meta">${escapeHtml(data.source_url || "")}</div>
        <div class="cp-meta">Scraped: ${escapeHtml(data.scraped_at || "")}</div>
      </div>
    </div>
  `;
}

function fetchDetailHtml(data) {
  const successLine = `<div class="fetch-attempt-line fa-ok"><span class="fa-icon">✓</span>${data.attempts_tried}/${data.attempts_tried} ${escapeHtml(hostnameOf(data.source_url))} → 200 OK</div>`;
  return (
    fetchAttemptsListHtml(currentFetchAttempts) +
    `<div class="fetch-attempts">${successLine}</div>` +
    contentPreviewCardHtml(data) +
    `<div style="margin-top:8px;">${faceSimilarityBadgeHtml(data.face_similarity_score, data.face_found_in_match)}</div>`
  );
}

function deepfakeDetailHtml(data) {
  const pct = Math.round(data.confidence * 100);
  let cls = "badge-grey";
  let icon = "";
  if (data.label === "Real") {
    cls = data.confidence > 0.7 ? "badge-green" : "badge-yellow";
    icon = data.confidence > 0.7 ? "✓" : "⚠️";
  } else if (data.label === "AI-Generated") {
    cls = "badge-red";
    icon = "✕";
  }
  const warning = data.warning ? `<div class="hash-line"><span></span><span>${escapeHtml(data.warning)}</span></div>` : "";
  return `<span class="badge ${cls}">${icon} ${escapeHtml(data.label)}: ${pct}% confidence</span>${warning}`;
}

function fingerprintDetailHtml(data) {
  const kb = data.image_size_bytes ? (data.image_size_bytes / 1024).toFixed(1) : "?";
  const flow = `
    <div class="hash-flow">
      <div class="hash-flow-row">Image bytes (${kb} KB) <span class="hash-flow-arrow">→ SHA-256 →</span> <code>${escapeHtml(data.image_hash)}</code></div>
      <div class="hash-flow-row">+ URL + Caption + Timestamp <span class="hash-flow-arrow">→ SHA-256 →</span> <code>${escapeHtml(data.combined_hash)}</code></div>
      <div class="hash-flow-row">Perceptual hash (pHash): <code>${escapeHtml(data.perceptual_hash || "")}</code></div>
    </div>
  `;
  const blob = data.blob
    ? `<details class="blob-details"><summary>Canonical JSON blob that was hashed</summary><pre>${escapeHtml(JSON.stringify(data.blob, null, 2))}</pre></details>`
    : "";
  return flow + blob;
}

function chainSubmitDetailHtml(data) {
  const steps = [
    "→ Transaction built",
    `→ Signed by <code>${escapeHtml(data.submitter_address || data.submitter || "")}</code>`,
    "→ Broadcast to network",
    `→ Confirmed in block #${escapeHtml(String(data.block_number))}`,
  ];
  let html = `<div class="tx-timeline">${steps.map((s) => `<div class="tx-timeline-step">${s}</div>`).join("")}</div>`;
  html += `<div class="hash-line"><span>Contract</span><code>${escapeHtml(data.contract_address || "")}</code></div>`;
  html += `<div class="hash-line"><span>Tx hash</span><code>${escapeHtml(data.tx_hash)}</code></div>`;
  if (data.gas_used) {
    html += `<div class="hash-line"><span>Gas used</span><span>${escapeHtml(String(data.gas_used))}</span></div>`;
  }
  if (data.block_explorer_url) {
    html += `<div class="hash-line"><span>Explorer</span><a href="${escapeHtml(data.block_explorer_url)}" target="_blank" rel="noopener">View on Etherscan ↗</a></div>`;
  }
  return html;
}

function similarityClass(score) {
  if (score >= 90) return "verified";
  if (score >= 70) return "yellow";
  return "mismatch";
}

function verdictDetailHtml(data) {
  const verified = data.verified;
  const record = data.on_chain_record;
  const post = data.matched_post || {};
  const steps = [
    `Re-downloaded image from ${escapeHtml(hostnameOf(post.url))} (independent fetch)`,
    "Recomputed SHA-256 and perceptual hash from the fresh bytes",
    "Queried the blockchain for the on-chain record",
  ];
  const stepsHtml = `<ol class="reverify-steps">${steps.map((s) => `<li>${s}</li>`).join("")}</ol>`;
  return `
    ${stepsHtml}
    <div class="hash-compare">
      <div class="hash-line"><span>Fingerprint (this run)</span><code>${escapeHtml(data.combined_hash)}</code></div>
      <div class="hash-line"><span>Recomputed just now</span><code>${escapeHtml(data.recomputed_combined_hash)}</code></div>
      <div class="hash-line"><span>Exact match (SHA-256)</span><span>${verified ? "✓" : "✕"}</span></div>
      ${
        data.perceptual_similarity !== undefined && data.perceptual_similarity !== null
          ? `<div class="hash-line"><span>Visual similarity (pHash)</span><span class="similarity-${similarityClass(data.perceptual_similarity)}">${data.perceptual_similarity.toFixed(1)}%</span></div>`
          : ""
      }
    </div>
    ${
      record
        ? `<div class="hash-line"><span>Recorded on-chain</span><span>${escapeHtml(formatTimestamp(record.timestamp))} by <code>${escapeHtml(record.submitter)}</code></span></div>`
        : `<div class="hash-line"><span>On-chain record</span><span>none found for the recomputed hash</span></div>`
    }
    ${post.url ? `<div class="hash-line"><span>Matched post</span><a href="${escapeHtml(post.url)}" target="_blank" rel="noopener">${escapeHtml(post.title || post.url)}</a></div>` : ""}
    <div class="verdict-banner ${verified ? "verified" : "mismatch"}">
      <div class="verdict-icon">${verified ? "✓" : "✕"}</div>
      <div class="verdict-word">${verified ? "VERIFIED" : "MISMATCH"}</div>
      <div class="verdict-sub">${
        verified
          ? `This exact image, link, and caption are permanently recorded on the blockchain.`
          : `The freshly recomputed fingerprint does not match any on-chain record.`
      }</div>
    </div>
  `;
}

// ---- Re-verify panel ------------------------------------------------------

let fetchedMeta = null; // { image_url, source_url, caption, scraped_at }
let originalPerceptualHash = null;
let currentFetchAttempts = [];
let currentFaceVerifyCandidates = [];

function showReverifyPanel(originalCombinedHash) {
  if (!fetchedMeta) return;
  reverifyUrlInput.value = fetchedMeta.source_url || "";
  reverifyCaptionInput.value = fetchedMeta.caption || "";
  reverifyResultEl.innerHTML = "";
  reverifyPanel.hidden = false;
  reverifyPanel.dataset.originalHash = originalCombinedHash || "";
}

function renderReverifyResult(data) {
  const verified = data.verified;
  reverifyResultEl.innerHTML = `
    <div class="hash-line"><span>Recomputed hash</span><code>${escapeHtml(data.recomputed_combined_hash)}</code></div>
    <div class="hash-line"><span>Exact match (SHA-256)</span><span>${verified ? "✓" : "✕"}</span></div>
    ${
      data.perceptual_similarity !== undefined && data.perceptual_similarity !== null
        ? `<div class="hash-line"><span>Visual similarity (pHash)</span><span class="similarity-${similarityClass(data.perceptual_similarity)}">${data.perceptual_similarity.toFixed(1)}%</span></div>`
        : ""
    }
    ${
      data.on_chain_record
        ? `<div class="hash-line"><span>Recorded on-chain</span><span>${escapeHtml(formatTimestamp(data.on_chain_record.timestamp))} by <code>${escapeHtml(data.on_chain_record.submitter)}</code></span></div>`
        : `<div class="hash-line"><span>On-chain record</span><span>${escapeHtml(data.mismatch_reason || "none found for this hash")}</span></div>`
    }
    <div class="verdict-banner ${verified ? "verified" : "mismatch"}">
      <div class="verdict-icon">${verified ? "✓" : "✕"}</div>
      <div class="verdict-word">${verified ? "VERIFIED" : "MISMATCH"}</div>
      <div class="verdict-sub">${
        verified
          ? `Still matches the on-chain record.`
          : `No longer matches — the edited content produces a different fingerprint.`
      }</div>
    </div>
  `;
}

reverifyBtn.addEventListener("click", async () => {
  if (!fetchedMeta) return;
  reverifyBtn.disabled = true;
  reverifyResultEl.innerHTML = '<span class="spinner" aria-hidden="true"></span> Re-verifying…';
  try {
    const resp = await fetch("/api/reverify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_url: fetchedMeta.image_url,
        source_url: reverifyUrlInput.value,
        caption: reverifyCaptionInput.value,
        scraped_at: fetchedMeta.scraped_at,
        original_perceptual_hash: originalPerceptualHash,
      }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`);
    renderReverifyResult(body);
  } catch (err) {
    showError(`Re-verify failed: ${err.message}`);
    reverifyResultEl.textContent = "";
  } finally {
    reverifyBtn.disabled = false;
  }
});

// ---- Streaming ------------------------------------------------------------

async function streamPipeline(url, options, onEvent) {
  const resp = await fetch(url, options);
  if (!resp.ok || !resp.body) {
    let detail = "";
    try {
      const body = await resp.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await resp.text().catch(() => "");
    }
    throw new Error(`Request failed (HTTP ${resp.status}): ${detail}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  const handleLine = (line) => {
    let evt;
    try {
      evt = JSON.parse(line);
    } catch (err) {
      showError(`Could not parse a pipeline status line: ${err.message}\nRaw line: ${line}`);
      return;
    }
    try {
      onEvent(evt);
    } catch (err) {
      showError(`Error handling "${evt.stage || "unknown"}" stage event: ${err.message}`);
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (line) handleLine(line);
    }
  }
  const rest = buf.trim();
  if (rest) handleLine(rest);
}

function handleStageEvent(evt) {
  console.log("[pipeline] event", evt.stage, evt.status);
  const { stage, status, data, message, label } = evt;

  if (stage === "complete") {
    if (status === "done") {
      settleEntry("reverify", "done", doneText("reverify", data), verdictDetailHtml(data));
      markAllStepsDone();
      showReverifyPanel(data.combined_hash);
    }
    return;
  }

  if (stage === "search_retry" || stage === "search_fallback") {
    if (status === "skipped" || status === "warning") {
      const div = document.createElement("div");
      const isWarning = stage === "search_fallback";
      div.className = `trace-entry ${isWarning ? "state-skipped" : "state-skipped"} trace-entry-minor`;
      div.innerHTML = `
        <div class="trace-line">
          <span class="marker">${isWarning ? "⚠" : MARKERS.skipped}</span>
          <span class="trace-text">${escapeHtml(message || (isWarning ? "Reduced accuracy mode." : "Retrying with the full photo."))}</span>
        </div>
      `;
      traceEl.appendChild(div);
    }
    return;
  }

  if (stage === "fetch_attempt") {
    if (status === "skipped") {
      currentFetchAttempts.push({
        attempt: data.attempt,
        total: data.total,
        source: data.source,
        title: data.title,
        message: message,
      });
      const fetchEntry = entryEl("fetch");
      if (fetchEntry) {
        fetchEntry.querySelector(".trace-detail").innerHTML = fetchAttemptsListHtml(currentFetchAttempts);
      }
    }
    return;
  }

  if (stage === "face_verify_candidate") {
    currentFaceVerifyCandidates.push(data);
    const faceVerifyEntry = entryEl("face_verify");
    if (faceVerifyEntry) {
      faceVerifyEntry.querySelector(".trace-detail").innerHTML = faceVerifyCandidatesListHtml(currentFaceVerifyCandidates);
    }
    return;
  }

  if (!STAGE_ORDER.includes(stage)) return;

  if (status === "running") {
    settleUploadEntryIfPresent();
    markStepActive(stage);
    if (stage === "fetch") currentFetchAttempts = [];
    if (stage === "face_verify") currentFaceVerifyCandidates = [];
    appendEntry(stage);
    return;
  }
  if (stage === "detect" && status === "done") {
    drawFaceBoxes(data.boxes);
  }
  if (stage === "fetch" && status === "done") {
    fetchedMeta = {
      image_url: data.image_url,
      source_url: data.source_url,
      caption: data.caption,
      scraped_at: data.scraped_at,
    };
  }
  if (stage === "fingerprint" && status === "done") {
    originalPerceptualHash = data.perceptual_hash || null;
  }
  if (status === "error") {
    const errorDetailHtml = stage === "face_verify" ? faceVerifyCandidatesListHtml(currentFaceVerifyCandidates) : "";
    settleEntry(stage, "error", message || `"${label}" failed.`, errorDetailHtml);
    markStepErrored(stage);
    showError(`Pipeline stopped at "${label}": ${message || "unknown error"}`);
    return;
  }
  if (status === "skipped") {
    settleEntry(stage, "skipped", message || `"${label}" skipped.`, "");
    return;
  }
  if (status === "done") {
    let detailHtml = "";
    if (stage === "detect") detailHtml = detectDetailHtml(data);
    if (stage === "deepfake") detailHtml = deepfakeDetailHtml(data);
    if (stage === "search") detailHtml = searchDetailHtml(data);
    if (stage === "face_verify") detailHtml = faceVerifyDetailHtml(data);
    if (stage === "fetch") detailHtml = fetchDetailHtml(data);
    if (stage === "fingerprint") detailHtml = fingerprintDetailHtml(data);
    if (stage === "chain_submit") detailHtml = chainSubmitDetailHtml(data);
    settleEntry(stage, "done", doneText(stage, data), detailHtml);
  }
}

async function processMatch(matches, faceEncoding) {
  console.log("[pipeline] processMatch starting with", matches.length, "candidates");
  try {
    await streamPipeline(
      "/api/pipeline/process-match",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ matches, face_encoding: faceEncoding || null }),
      },
      handleStageEvent
    );
  } catch (err) {
    showError(`Request to process the selected match failed: ${err.message}`);
  }
}

function setBusy(busy) {
  submitBtn.disabled = busy;
  submitLabel.innerHTML = busy ? '<span class="spinner" aria-hidden="true"></span> Starting…' : "Start verification";
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();
  traceEl.innerHTML = "";
  reverifyPanel.hidden = true;
  fetchedMeta = null;
  originalPerceptualHash = null;
  currentFetchAttempts = [];
  currentFaceVerifyCandidates = [];
  resetStepIndicator();

  const file = fileInput.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);

  setBusy(true);
  markStepActive("upload");
  appendEntry("upload", "Uploading photo…");

  let allMatches = null;
  let faceEncoding = null;
  try {
    await streamPipeline("/api/pipeline/detect-and-search", { method: "POST", body: fd }, (evt) => {
      handleStageEvent(evt);
      if (evt.stage === "detect" && evt.status === "done") faceEncoding = evt.data.face_encoding;
      if (evt.stage === "face_verify" && evt.status === "done") allMatches = evt.data.verified_matches;
    });
  } catch (err) {
    showError(`Request failed: ${err.message}`);
    setBusy(false);
    return;
  }

  if (!allMatches || !allMatches.length) {
    setBusy(false);
    return;
  }
  await processMatch(allMatches, faceEncoding);
  setBusy(false);
});
