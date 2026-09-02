const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const autoToggle = document.getElementById("auto-toggle");
const stagesEl = document.getElementById("stages");
const candidatesEl = document.getElementById("candidates");
const resultEl = document.getElementById("result");
const errorBanner = document.getElementById("error-banner");

function showError(message) {
  console.error("[pipeline]", message);
  errorBanner.textContent = message;
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
  errorBanner.textContent = "";
}

const STAGES = [
  { id: "detect", label: "Detecting face" },
  { id: "search", label: "Searching the web for matching posts" },
  { id: "fetch", label: "Fetching matched content" },
  { id: "fingerprint", label: "Computing SHA-256 fingerprint" },
  { id: "chain_submit", label: "Uploading fingerprint to the blockchain" },
  { id: "reverify", label: "Independently re-verifying on-chain record" },
];

const ICONS = { pending: "○", running: "…", done: "✓", error: "✕", skipped: "–" };

function renderStages() {
  stagesEl.innerHTML = STAGES.map(
    (s) => `
    <div class="stage" id="stage-${s.id}">
      <span class="stage-icon" id="icon-${s.id}">${ICONS.pending}</span>
      <span class="stage-label">${s.label}</span>
      <span class="stage-detail" id="detail-${s.id}"></span>
    </div>`
  ).join("");
}

function updateStage(stage, status, message) {
  const row = document.getElementById(`stage-${stage}`);
  const icon = document.getElementById(`icon-${stage}`);
  const detail = document.getElementById(`detail-${stage}`);
  if (!row) return;
  row.classList.remove("running", "done", "error", "skipped");
  row.classList.add(status);
  icon.textContent = ICONS[status] || ICONS.pending;
  detail.textContent = message || "";
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

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

let currentMatches = [];

// Delegated listener attached once, at module load -- survives every
// candidatesEl.innerHTML replacement in renderCandidates, so there's no
// per-render attachment step that could be skipped or land on the wrong
// (already-replaced) nodes.
candidatesEl.addEventListener("click", (e) => {
  const card = e.target.closest(".candidate");
  if (!card || !candidatesEl.contains(card)) return;
  const idx = parseInt(card.dataset.index, 10);
  const match = currentMatches[idx];
  if (!match) {
    showError(`Clicked candidate index ${idx} but no matching data was found (had ${currentMatches.length} candidates).`);
    return;
  }
  candidatesEl.hidden = true;
  processMatch(match).catch((err) => showError(`Failed to process the selected match: ${err.message}`));
});

function renderCandidates(matches) {
  currentMatches = matches;
  candidatesEl.hidden = false;
  candidatesEl.innerHTML =
    "<h3>Pick a match</h3><div class=\"candidate-grid\">" +
    matches
      .map(
        (m, i) => `
      <div class="candidate" data-index="${i}">
        <img src="${escapeHtml(m.thumbnail || m.image || "")}" alt="" loading="lazy" />
        <div class="candidate-title">${escapeHtml(m.title || m.source || "Untitled")}</div>
        <div class="candidate-source">${escapeHtml(m.source || "")}</div>
      </div>`
      )
      .join("") +
    "</div>";
}

function renderResult(data) {
  resultEl.hidden = false;
  const verified = data.verified;
  const post = data.matched_post || {};
  resultEl.innerHTML = `
    <h3>Result</h3>
    <div class="result-card ${verified ? "verified" : "mismatch"}">
      <img class="result-thumb" src="${escapeHtml(post.thumbnail || "")}" alt="" />
      <div class="result-body">
        <a href="${escapeHtml(post.url || "#")}" target="_blank" rel="noopener">${escapeHtml(post.title || post.url || "Matched post")}</a>
        <div class="result-source">${escapeHtml(post.source || "")}</div>
        <div class="hash-row"><span>Image hash</span><code>${escapeHtml(data.image_hash)}</code></div>
        <div class="hash-row"><span>Combined hash</span><code>${escapeHtml(data.combined_hash)}</code></div>
        ${
          data.chain_result
            ? `<div class="hash-row"><span>Tx hash</span><code>${escapeHtml(data.chain_result.tx_hash)}</code></div>
               <div class="hash-row"><span>Block number</span><code>${escapeHtml(data.chain_result.block_number)}</code></div>`
            : `<div class="hash-row"><span>Chain</span><span>Already registered from a prior run; verified against the existing record.</span></div>`
        }
        <div class="badge ${verified ? "badge-verified" : "badge-mismatch"}">${verified ? "VERIFIED" : "MISMATCH"}</div>
      </div>
    </div>
  `;
}

async function processMatch(match) {
  console.log("[pipeline] processMatch starting for", match && match.link);
  let finalData = null;
  try {
    await streamPipeline(
      "/api/pipeline/process-match",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(match) },
      (evt) => {
        console.log("[pipeline] event", evt.stage, evt.status);
        updateStage(evt.stage, evt.status, evt.message);
        if (evt.status === "error") showError(`Pipeline stopped at "${evt.label}": ${evt.message || "unknown error"}`);
        if (evt.stage === "complete" && evt.status === "done") finalData = evt.data;
      }
    );
  } catch (err) {
    showError(`Request to process the selected match failed: ${err.message}`);
    return;
  }
  if (finalData) renderResult(finalData);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();
  resultEl.hidden = true;
  candidatesEl.hidden = true;
  candidatesEl.innerHTML = "";
  currentMatches = [];
  renderStages();

  const file = fileInput.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);

  let matches = [];
  try {
    await streamPipeline("/api/pipeline/detect-and-search", { method: "POST", body: fd }, (evt) => {
      console.log("[pipeline] event", evt.stage, evt.status);
      updateStage(evt.stage, evt.status, evt.message);
      if (evt.status === "error") showError(`Pipeline stopped at "${evt.label}": ${evt.message || "unknown error"}`);
      if (evt.stage === "search" && evt.status === "done") matches = evt.data.matches;
    });
  } catch (err) {
    showError(`Request failed: ${err.message}`);
    return;
  }

  if (!matches.length) return;

  if (autoToggle.checked) {
    await processMatch(matches[0]);
  } else {
    renderCandidates(matches);
  }
});

renderStages();
