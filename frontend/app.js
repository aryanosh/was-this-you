const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const previewImg = document.getElementById("preview-img");
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

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  previewImg.src = url;
  previewImg.hidden = false;
  dropzoneText.textContent = file.name;
});

// ---- Step indicator (3 broad phases, derived from the 6 fine-grained stages) ----

const STAGE_GROUP = {
  detect: "face",
  search: "web",
  fetch: "chain",
  fingerprint: "chain",
  chain_submit: "chain",
  reverify: "chain",
};
const STEP_ORDER = ["face", "web", "chain"];

function resetStepIndicator() {
  STEP_ORDER.forEach((s) => {
    const el = stepIndicatorEl.querySelector(`.step[data-step="${s}"]`);
    el.classList.remove("active", "done");
  });
  stepIndicatorEl.hidden = false;
}

function markStepActive(stage) {
  const group = STAGE_GROUP[stage];
  if (!group) return;
  const idx = STEP_ORDER.indexOf(group);
  STEP_ORDER.forEach((s, i) => {
    const el = stepIndicatorEl.querySelector(`.step[data-step="${s}"]`);
    el.classList.remove("active", "done");
    if (i < idx) el.classList.add("done");
    else if (i === idx) el.classList.add("active");
  });
}

function markAllStepsDone() {
  STEP_ORDER.forEach((s) => {
    const el = stepIndicatorEl.querySelector(`.step[data-step="${s}"]`);
    el.classList.remove("active");
    el.classList.add("done");
  });
}

// ---- Trace log (append-only chain of thought) ---------------------------

const STAGE_ORDER = ["detect", "search", "fetch", "fingerprint", "chain_submit", "reverify"];

const RUNNING_TEXT = {
  detect: "Detecting the face in the photo…",
  search: "Searching the web for matching posts…",
  fetch: "Fetching the matched post's content…",
  fingerprint: "Computing a SHA-256 fingerprint…",
  chain_submit: "Writing the fingerprint to the blockchain…",
  reverify: "Independently re-verifying the on-chain record…",
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
  div.innerHTML = `
    <div class="trace-line">
      <span class="marker">${MARKERS.running}</span>
      <span class="trace-text">${escapeHtml(text ?? RUNNING_TEXT[stage] ?? stage)}</span>
    </div>
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

function doneText(stage, data) {
  switch (stage) {
    case "detect":
      return data.num_faces === 1 ? "Detected 1 face." : `Detected ${data.num_faces} faces; searching with the whole image.`;
    case "search":
      return `Found ${data.num_matches} matching post${data.num_matches === 1 ? "" : "s"}. Using the top result.`;
    case "fetch": {
      const skipped = (data.attempts_tried || 1) - 1;
      const suffix = skipped > 0 ? ` (after skipping ${skipped} unreachable match${skipped === 1 ? "" : "es"})` : "";
      return `Fetched the image and page metadata from ${hostnameOf(data.source_url)}${suffix}.`;
    }
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

function matchDetailHtml(match, total) {
  const thumb = match.thumbnail || match.image || "";
  const title = match.title || match.source || "Untitled";
  const source = match.source || hostnameOf(match.link);
  const rank = match.position ? `Match ${match.position} of ${total}` : `Top of ${total} matches`;
  return `
    <div class="trace-match">
      ${thumb ? `<img src="${escapeHtml(thumb)}" alt="" loading="lazy" />` : ""}
      <div class="trace-match-body">
        <a href="${escapeHtml(match.link || "#")}" target="_blank" rel="noopener">${escapeHtml(title)}</a>
        <div class="trace-match-source">${escapeHtml(source)}</div>
        <div class="trace-match-rank">${escapeHtml(rank)}</div>
      </div>
    </div>
  `;
}

function fingerprintDetailHtml(data) {
  return `<div class="hash-line"><span>Combined hash</span><code>${escapeHtml(data.combined_hash)}</code></div>`;
}

function chainSubmitDetailHtml(data) {
  return `<div class="hash-line"><span>Tx hash</span><code>${escapeHtml(data.tx_hash)}</code></div>`;
}

function verdictDetailHtml(data) {
  const verified = data.verified;
  const record = data.on_chain_record;
  const post = data.matched_post || {};
  return `
    <div class="hash-compare">
      <div class="hash-line"><span>Fingerprint (this run)</span><code>${escapeHtml(data.combined_hash)}</code></div>
      <div class="hash-line"><span>Recomputed just now</span><code>${escapeHtml(data.recomputed_combined_hash)}</code></div>
    </div>
    ${
      record
        ? `<div class="hash-line"><span>Recorded on-chain</span><span>${escapeHtml(formatTimestamp(record.timestamp))} by <code>${escapeHtml(record.submitter)}</code></span></div>`
        : `<div class="hash-line"><span>On-chain record</span><span>none found for the recomputed hash</span></div>`
    }
    ${post.url ? `<div class="hash-line"><span>Matched post</span><a href="${escapeHtml(post.url)}" target="_blank" rel="noopener">${escapeHtml(post.title || post.url)}</a></div>` : ""}
    <div class="verdict-word ${verified ? "verified" : "mismatch"}">${verified ? "VERIFIED" : "MISMATCH"}</div>
  `;
}

// ---- Re-verify panel ------------------------------------------------------

let fetchedMeta = null; // { image_url, source_url, caption, scraped_at }

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
    ${
      data.on_chain_record
        ? `<div class="hash-line"><span>Recorded on-chain</span><span>${escapeHtml(formatTimestamp(data.on_chain_record.timestamp))} by <code>${escapeHtml(data.on_chain_record.submitter)}</code></span></div>`
        : `<div class="hash-line"><span>On-chain record</span><span>${escapeHtml(data.mismatch_reason || "none found for this hash")}</span></div>`
    }
    <div class="verdict-word ${verified ? "verified" : "mismatch"}">${verified ? "VERIFIED" : "MISMATCH"}</div>
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

  if (stage === "fetch_attempt") {
    if (status === "skipped") {
      const div = document.createElement("div");
      div.className = "trace-entry state-skipped trace-entry-minor";
      div.id = `entry-fetch-attempt-${data.attempt}`;
      div.innerHTML = `
        <div class="trace-line">
          <span class="marker">${MARKERS.skipped}</span>
          <span class="trace-text">Match ${data.attempt} of ${data.total}${data.source ? ` (${escapeHtml(data.source)})` : ""} was unreachable — trying the next one.</span>
        </div>
      `;
      traceEl.appendChild(div);
    }
    return;
  }

  if (!STAGE_ORDER.includes(stage)) return;

  if (status === "running") {
    settleUploadEntryIfPresent();
    markStepActive(stage);
    appendEntry(stage);
    return;
  }
  if (stage === "fetch" && status === "done") {
    fetchedMeta = {
      image_url: data.image_url,
      source_url: data.source_url,
      caption: data.caption,
      scraped_at: data.scraped_at,
    };
  }
  if (status === "error") {
    settleEntry(stage, "error", message || `"${label}" failed.`, "");
    showError(`Pipeline stopped at "${label}": ${message || "unknown error"}`);
    return;
  }
  if (status === "skipped") {
    settleEntry(stage, "skipped", message || `"${label}" skipped.`, "");
    return;
  }
  if (status === "done") {
    let detailHtml = "";
    if (stage === "search") detailHtml = matchDetailHtml(data.matches[0], data.num_matches);
    if (stage === "fingerprint") detailHtml = fingerprintDetailHtml(data);
    if (stage === "chain_submit") detailHtml = chainSubmitDetailHtml(data);
    settleEntry(stage, "done", doneText(stage, data), detailHtml);
  }
}

async function processMatch(matches) {
  console.log("[pipeline] processMatch starting with", matches.length, "candidates");
  try {
    await streamPipeline(
      "/api/pipeline/process-match",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(matches) },
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
  resetStepIndicator();

  const file = fileInput.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);

  setBusy(true);
  appendEntry("upload", "Uploading photo…");

  let allMatches = null;
  try {
    await streamPipeline("/api/pipeline/detect-and-search", { method: "POST", body: fd }, (evt) => {
      handleStageEvent(evt);
      if (evt.stage === "search" && evt.status === "done") allMatches = evt.data.matches;
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
  await processMatch(allMatches);
  setBusy(false);
});
