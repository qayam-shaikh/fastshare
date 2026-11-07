/* QuickShare Frontend Application (Separated from Python Backend) */
const STATE = window.__INITIAL_STATE__ || {
  role: "laptop",
  basePath: "/laptop",
  screen: "home",
  initialFocus: "send",
  phoneConnected: false,
  phoneUrl: "http://quickshare.local:8765/phone",
  phoneIpUrl: "http://192.168.0.103:8765/phone",
  laptopUrl: "http://localhost:8765/laptop",
  qrDataUri: "",
  maxUploadMib: 500,
  freeDiskBytes: 0,
  saveInbox: false,
  remainingSeconds: 3600,
  error: "",
  items: []
};

let currentFilter = "all";
const WORKSPACE_VIEWS = STATE.role === "laptop"
  ? ["sharing", "transfers", "scratchpad", "connect", "devices"]
  : ["sharing", "transfers", "scratchpad", "controls"];
const initialView = window.location.hash.slice(1);
let activeWorkspaceView = WORKSPACE_VIEWS.includes(initialView) ? initialView : "sharing";
let qrManuallyToggled = false;
let qrVisible = true;
let lastRenderedSignature = "";
let filePreviewRequest = 0;
let scratchpadRevision = Number(STATE.scratchpadRevision) || 0;
let scratchpadSaveTimer = null;
let scratchpadSavePending = false;
let scratchpadLastSynced = String(STATE.scratchpad || "");
let installPromptEvent = null;
let voiceRecorder = null;
let voiceRecorderStream = null;
let voiceRecorderChunks = [];
let remoteScreenObjectUrl = "";

document.documentElement.dataset.role = STATE.role;

function isMediaPreviewable(item) {
  const extension = item.name.split(".").pop().toLowerCase();
  return ["audio/", "video/"].some(type => item.mime.startsWith(type))
    || (["mp4", "webm", "mp3", "wav", "m4a", "aac", "ogg", "opus"].includes(extension) && Boolean(item.preview_url));
}

function isDocumentPreviewable(item) {
  const extension = item.name.split(".").pop().toLowerCase();
  return ["pdf", "md", "json", "py"].includes(extension) && Boolean(item.preview_url);
}

function initTheme() {
  try {
    const saved = localStorage.getItem("qs_theme");
    if (saved === "dark" || saved === "light") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (_) {}
  syncThemeIcons();
}

function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "light";
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem("qs_theme", next);
  } catch (_) {}
  syncThemeIcons();
}

function syncThemeIcons() {
  const cur = document.documentElement.getAttribute("data-theme") || "light";
  const sun = document.getElementById("themeIconSun");
  const moon = document.getElementById("themeIconMoon");
  if (sun && moon) {
    sun.style.display = cur === "dark" ? "block" : "none";
    moon.style.display = cur === "dark" ? "none" : "block";
  }
}

function showToast(msg) {
  const el = document.getElementById("toastBanner");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("visible");
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => el.classList.remove("visible"), 2800);
}

function showError(msg) {
  const el = document.getElementById("errorBanner");
  if (!el) return;
  if (!msg) {
    el.classList.remove("visible");
    el.textContent = "";
    return;
  }
  el.textContent = msg;
  el.classList.add("visible");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

function linkifyText(value) {
  const text = String(value == null ? "" : value);
  const pattern = /https?:\/\/[^\s<>"']+|www\.[^\s<>"']+|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?<![@\w])(?:[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?\.)+[A-Z]{2,}(?::\d{1,5})?(?:\/[^\s<>"']*)?|(?<![\w@])\+?\d[\d(). -]{5,}\d(?![\w])/gi;
  const matches = text.matchAll(pattern);
  let output = "";
  let cursor = 0;

  for (const match of matches) {
    const raw = match[0];
    let label = raw;
    let href = "";
    let type = "";

    if (/^(?:https?:\/\/|www\.)/i.test(raw) || /^(?<![@\w])(?:[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?\.)+[A-Z]{2,}/i.test(raw)) {
      label = raw.replace(/[.,!?;:]+$/, "");
      while (/[)\]}]$/.test(label)) {
        const closing = label.slice(-1);
        const opening = closing === ")" ? "(" : closing === "]" ? "[" : "{";
        if (label.split(closing).length <= label.split(opening).length) break;
        label = label.slice(0, -1);
      }
      if (!label) continue;
      href = /^https?:\/\//i.test(label) ? label : `https://${label}`;
      type = "url";
    } else if (raw.includes("@")) {
      href = `mailto:${raw}`;
      type = "email";
    } else {
      const dateStart = raw.search(/\s+\d{4}-\d{2}-\d{2}\b/);
      if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) continue;
      if (dateStart >= 0) label = raw.slice(0, dateStart).trimEnd();
      const digits = label.replace(/\D/g, "");
      if (digits.length < 7 || digits.length > 15) continue;
      href = `tel:${label.startsWith("+") ? "+" : ""}${digits}`;
      type = "phone";
    }

    const matchStart = match.index;
    const matchEnd = matchStart + raw.length;
    const linkEnd = matchStart + label.length;
    const targetAttrs = type === "url" ? ' target="_blank" rel="noopener noreferrer"' : "";
    output += escapeHtml(text.slice(cursor, matchStart));
    output += `<a href="${escapeHtml(href)}" class="text-snippet-link text-snippet-link-${type}"${targetAttrs}>${escapeHtml(label)}</a>`;
    output += escapeHtml(text.slice(linkEnd, matchEnd));
    cursor = matchEnd;
  }

  return output + escapeHtml(text.slice(cursor));
}

function updateCharCounter() {
  const ta = document.getElementById("textMessageInput");
  const counter = document.getElementById("charCounter");
  if (!ta || !counter) return;
  const len = ta.value.length;
  counter.textContent = len > 2000
    ? `${len.toLocaleString()} chars (will save as .txt)`
    : `${len.toLocaleString()} chars`;
}

function getSvgIcon(kind) {
  if (kind === "zip") {
    return `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/><path d="M12 8v8"/></svg>`;
  }
  if (kind === "image") {
    return `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>`;
  }
  if (kind === "text") {
    return `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>`;
  }
  return `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>`;
}

function openLightbox(url) {
  const lb = document.getElementById("lightbox");
  const img = document.getElementById("lightboxImg");
  if (!lb || !img) return;
  img.src = url;
  lb.classList.add("open");
}

function openImagePreview(itemId) {
  const item = (STATE.items || []).find(candidate => candidate.id === itemId);
  if (item?.kind === "image" && item.preview_url) openLightbox(item.preview_url);
}

function closeLightbox() {
  const lb = document.getElementById("lightbox");
  if (lb) lb.classList.remove("open");
}

async function openFilePreview(itemId) {
  const item = (STATE.items || []).find(candidate => candidate.id === itemId);
  if (!item || !isDocumentPreviewable(item)) return;

  const requestId = ++filePreviewRequest;
  const extension = item.name.split(".").pop().toLowerCase();
  const modal = document.getElementById("filePreviewModal");
  const title = document.getElementById("filePreviewTitle");
  const status = document.getElementById("filePreviewStatus");
  const pdf = document.getElementById("filePreviewPdf");
  const code = document.getElementById("filePreviewCode");
  title.textContent = item.name;
  status.hidden = false;
  status.textContent = "Loading preview…";
  pdf.hidden = true;
  code.hidden = true;
  pdf.removeAttribute("src");
  code.textContent = "";
  modal.classList.add("open");

  if (extension === "pdf") {
    pdf.src = item.preview_url;
    pdf.hidden = false;
    status.hidden = true;
    return;
  }

  try {
    const response = await fetch(item.preview_url, { cache: "no-store" });
    if (!response.ok) throw new Error(`Preview request failed (${response.status}).`);
    const contents = await response.text();
    if (requestId !== filePreviewRequest) return;
    code.textContent = contents;
    code.hidden = false;
    status.hidden = true;
  } catch (error) {
    if (requestId !== filePreviewRequest) return;
    status.textContent = error.message || "Could not load file preview.";
  }
}

function closeFilePreview() {
  filePreviewRequest++;
  const modal = document.getElementById("filePreviewModal");
  const pdf = document.getElementById("filePreviewPdf");
  if (modal) modal.classList.remove("open");
  if (pdf) pdf.removeAttribute("src");
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "Unknown";
  if (bytes < 1024 ** 3) return `${(bytes / (1024 ** 2)).toFixed(1)} MB`;
  return `${(bytes / (1024 ** 3)).toFixed(1)} GB`;
}

function saveToLaptopInbox(itemId, button) {
  button.disabled = true;
  button.textContent = "Saving…";
  fetch(`${STATE.basePath}/save-to-inbox/${encodeURIComponent(itemId)}`, {
    method: "POST",
    headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
  }).then(async response => {
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not save to laptop inbox.");
    button.textContent = "Saved to Inbox";
    showToast(`Saved to ${data.path}`);
  }).catch(error => {
    button.disabled = false;
    button.textContent = "Save to Inbox";
    showError(error.message || "Could not save to laptop inbox.");
  });
}

function inboxSaveAction(item) {
  if (STATE.role !== "laptop" || item.sender === STATE.role) return "";
  return `<button type="button" class="btn btn-outline btn-sm" onclick="saveToLaptopInbox('${escapeHtml(item.id)}', this)">Save to Inbox</button>`;
}

function queueScratchpadSave() {
  const input = document.getElementById("scratchpadInput");
  if (!input || input.value === scratchpadLastSynced) return;
  clearTimeout(scratchpadSaveTimer);
  document.getElementById("scratchpadStatus").textContent = "Syncing…";
  scratchpadSaveTimer = setTimeout(saveScratchpad, 250);
}

async function saveScratchpad() {
  const input = document.getElementById("scratchpadInput");
  const status = document.getElementById("scratchpadStatus");
  if (!input) return;
  const content = input.value;
  scratchpadSavePending = true;
  let saved = false;
  try {
    const response = await fetch(`${STATE.basePath}/scratchpad`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ content })
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Scratchpad sync failed.");
    scratchpadRevision = Math.max(scratchpadRevision, Number(data.revision) || 0);
    scratchpadLastSynced = content;
    saved = true;
    status.textContent = "Synced";
  } catch (error) {
    status.textContent = "Sync failed — retry by typing";
    showError(error.message || "Scratchpad sync failed.");
  } finally {
    scratchpadSavePending = false;
    if (saved && input.value !== scratchpadLastSynced) queueScratchpadSave();
  }
}

function updateScratchpadFromServer(data) {
  const input = document.getElementById("scratchpadInput");
  if (!input) return;
  const revision = Number(data.scratchpad_revision) || 0;
  if (revision <= scratchpadRevision) {
    if (!scratchpadRevision && !input.value && data.scratchpad) {
      input.value = String(data.scratchpad);
      scratchpadLastSynced = input.value;
    }
    return;
  }
  scratchpadRevision = revision;
  if (scratchpadSavePending || scratchpadSaveTimer) return;
  input.value = String(data.scratchpad || "");
  scratchpadLastSynced = input.value;
  document.getElementById("scratchpadStatus").textContent = "Synced";
}

async function installQuickShare() {
  if (window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true) {
    showToast("QuickShare is already installed.");
    return;
  }

  if (installPromptEvent) {
    const promptEvent = installPromptEvent;
    installPromptEvent = null;
    try {
      await promptEvent.prompt();
      const choice = await promptEvent.userChoice;
      if (choice.outcome === "accepted") {
        document.getElementById("installAppPanel").hidden = true;
      } else {
        showToast("Install dismissed. You can try again from your browser menu.");
      }
      return;
    } catch (error) {
      showError(error.message || "Could not open the install prompt. Try your browser menu.");
      return;
    }
  }

  const isLocalhost = ["localhost", "127.0.0.1", "::1"].includes(location.hostname);
  if (!window.isSecureContext || (location.protocol !== "https:" && !isLocalhost)) {
    showError("Mobile app installation requires HTTPS with a certificate trusted by this phone. Open QuickShare using its trusted HTTPS URL, then tap Install App again.");
    return;
  }

  const isAppleMobile = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (isAppleMobile) {
    showError("To install on iPhone or iPad, tap Share in Safari, then choose Add to Home Screen.");
    return;
  }

  showError("If no install prompt opened, use your browser menu (⋮) and choose Install app or Add to Home screen. If that option is missing, open this page in Chrome over trusted HTTPS.");
}

function openStopModal() {
  const m = document.getElementById("stopModal");
  if (m) m.classList.add("open");
}

function closeStopModal() {
  const m = document.getElementById("stopModal");
  if (m) m.classList.remove("open");
}

async function copyTextWithFallback(text, btnEl, itemId) {
  let copied = false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch (_) {
      copied = false;
    }
  }
  if (!copied) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.top = "-9999px";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      copied = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch (_) {
      copied = false;
    }
  }

  if (copied) {
    if (btnEl) {
      const orig = btnEl.innerHTML;
      btnEl.innerHTML = `<span>✓ Copied</span>`;
      setTimeout(() => {
        btnEl.innerHTML = orig;
      }, 1800);
    }
    showToast("Copied to clipboard!");
  } else {
    showToast("Clipboard unavailable — use Download .txt below");
    if (itemId) {
      const fallbackBoard = document.getElementById("txtBoard_" + itemId);
      if (fallbackBoard) fallbackBoard.style.display = "flex";
    }
  }
  return copied;
}

function copyItemTextById(itemId, btnEl) {
  const item = (STATE.items || []).find(it => it.id === itemId);
  if (!item) return;
  void (async () => {
    const copied = await copyTextWithFallback(item.text_content || "", btnEl, itemId);
    if (copied && item.burn_after_read && item.sender !== STATE.role) {
      await consumeOneTimeItem(itemId);
    }
  })();
}

async function consumeOneTimeItem(itemId) {
  try {
    const response = await fetch(`${STATE.basePath}/consume/${encodeURIComponent(itemId)}`, {
      method: "POST",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    });
    if (!response.ok) throw new Error("This one-time item was already used or could not be removed.");
    showToast("One-time item removed from this session.");
    await pollState();
  } catch (error) {
    showError(error.message || "Could not remove the one-time item.");
  }
}

function laptopOpenUrlAction(item) {
  if (STATE.role !== "laptop" || item.sender !== "phone" || item.kind !== "text") return "";
  const rawUrl = String(item.text_content || "").match(/https?:\/\/[^\s<>"']+/i)?.[0]
    ?.replace(/[),.;!?]+$/, "");
  if (!rawUrl) return "";
  try {
    const url = new URL(rawUrl);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return "";
  } catch (_) {
    return "";
  }
  return `<button type="button" class="btn btn-outline btn-sm" onclick="openUrlOnLaptop('${escapeHtml(item.id)}')">Open Tab on Laptop</button>`;
}

async function openUrlOnLaptop(itemId) {
  const item = (STATE.items || []).find(candidate => candidate.id === itemId);
  if (!item || item.sender !== "phone") return;
  const url = String(item.text_content || "").match(/https?:\/\/[^\s<>"']+/i)?.[0]
    ?.replace(/[),.;!?]+$/, "");
  if (!url) return;
  try {
    const response = await fetch(`${STATE.basePath}/open-url`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ url })
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not open link on laptop.");
    showToast("Opened a new tab on the laptop.");
  } catch (error) {
    showError(error.message || "Could not open link on laptop.");
  }
}

async function browseZip(itemId) {
  const modal = document.getElementById("zipBrowserModal");
  const title = document.getElementById("zipBrowserTitle");
  const status = document.getElementById("zipBrowserStatus");
  const list = document.getElementById("zipEntryList");
  const item = (STATE.items || []).find(candidate => candidate.id === itemId);
  if (!item || item.kind !== "zip") return;
  title.textContent = item.name;
  status.textContent = "Reading archive contents…";
  list.replaceChildren();
  modal.classList.add("open");
  try {
    const response = await fetch(`${STATE.basePath}/zip?id=${encodeURIComponent(itemId)}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not browse this ZIP.");
    status.textContent = `${data.entries.length} files`;
    if (data.entries.length === 0) {
      list.textContent = "No safe, downloadable files were found in this archive.";
      return;
    }
    list.innerHTML = data.entries.map(entry => `
      <div class="zip-entry-row">
        <div><strong>${escapeHtml(entry.name)}</strong><small>${escapeHtml(entry.size_human)}</small></div>
        ${entry.downloadable
          ? `<a class="btn btn-outline btn-sm" href="${STATE.basePath}/zip/file?id=${encodeURIComponent(itemId)}&entry=${encodeURIComponent(entry.index)}">Download</a>`
          : `<span class="zip-entry-limit">Over session file limit</span>`}
      </div>
    `).join("");
  } catch (error) {
    status.textContent = error.message || "Could not browse this ZIP.";
  }
}

function closeZipBrowser() {
  document.getElementById("zipBrowserModal").classList.remove("open");
}

function renderItemCard(item) {
  const isIncoming = item.sender !== STATE.role;
  const dirClass = isIncoming ? "incoming" : "outgoing";
  const dirLabel = isIncoming
    ? (STATE.role === "laptop" ? "From Phone" : "From Laptop")
    : (STATE.role === "laptop" ? "Sent to Phone" : "Sent to Laptop");

  let typeBadge = "File";
  if (item.kind === "text") typeBadge = "Text";
  else if (item.kind === "image") typeBadge = "Image";
  else if (item.kind === "zip") typeBadge = item.is_folder_zip ? "Folder (ZIP)" : "ZIP Archive";

  let bodyHtml = "";
  let actionsHtml = "";
  const imageThumbnail = item.kind === "image"
    ? `<button type="button" class="image-thumb-button" onclick="openImagePreview('${escapeHtml(item.id)}')" aria-label="View full image: ${escapeHtml(item.name)}"><img src="${escapeHtml(item.preview_url)}" alt="" loading="lazy"></button>`
    : "";

  if (item.kind === "text") {
    if (item.text_too_long) {
      bodyHtml = `
        <div class="transfer-body">
          <pre class="text-snippet-pre">${linkifyText(item.text_content)}</pre>
          <div class="txt-board">
            <div class="txt-board-left">
              ${getSvgIcon("text")}
              <div>
                <strong>${escapeHtml(item.name)}</strong> (${escapeHtml(item.size_human)})
                <div style="font-size:0.75rem">Text exceeds clipboard limit — click to save as .txt</div>
              </div>
            </div>
            <a class="btn btn-primary btn-sm" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              <span>Download .txt</span>
            </a>
            ${inboxSaveAction(item)}
          </div>
        </div>
      `;
    } else {
      bodyHtml = `
        <div class="transfer-body">
          <pre class="text-snippet-pre">${linkifyText(item.text_content)}</pre>
          <div class="txt-board" id="txtBoard_${escapeHtml(item.id)}" style="display:none">
            <div class="txt-board-left">
              ${getSvgIcon("text")}
              <div>
                <strong>${escapeHtml(item.name)}</strong> (${escapeHtml(item.size_human)})
                <div style="font-size:0.75rem">Save text directly as a .txt file</div>
              </div>
            </div>
            <a class="btn btn-primary btn-xs" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}">
              <span>Download .txt</span>
            </a>
            ${inboxSaveAction(item)}
          </div>
        </div>
      `;
      actionsHtml = `
        <div class="transfer-actions">
          <button type="button" class="btn btn-primary btn-sm" onclick="copyItemTextById('${escapeHtml(item.id)}', this)">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            <span>Copy to Clipboard</span>
          </button>
          <a class="btn btn-outline btn-sm" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}">
            <span>Save .txt</span>
          </a>
          ${laptopOpenUrlAction(item)}
          ${inboxSaveAction(item)}
        </div>
      `;
    }
  } else if (item.kind === "image") {
    actionsHtml = `
      <div class="transfer-actions">
        ${inboxSaveAction(item)}
        <a class="btn btn-primary btn-sm" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>Download Image</span>
        </a>
      </div>
    `;
  } else if (isMediaPreviewable(item)) {
    const isVideo = item.mime.startsWith("video/")
      || (["mp4", "webm"].includes(item.name.split(".").pop().toLowerCase()) && !item.mime.startsWith("audio/"));
    bodyHtml = `
      <div class="transfer-body">
        <div class="media-stage">
          ${isVideo
            ? `<video controls playsinline preload="metadata" src="${escapeHtml(item.preview_url)}">Your browser does not support video playback.</video>`
            : `<audio controls preload="metadata" src="${escapeHtml(item.preview_url)}">Your browser does not support audio playback.</audio>`}
        </div>
      </div>
    `;
    actionsHtml = `
      <div class="transfer-actions">
        ${inboxSaveAction(item)}
        <a class="btn btn-primary btn-sm" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}"><span>Download ${isVideo ? "Video" : "Audio"}</span></a>
      </div>
    `;
  } else if (item.kind === "zip") {
    actionsHtml = `
      <div class="transfer-actions">
        <button type="button" class="btn btn-outline btn-sm" onclick="browseZip('${escapeHtml(item.id)}')">Browse Contents</button>
        ${inboxSaveAction(item)}
        <a class="btn btn-primary btn-sm" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>Download ZIP</span>
        </a>
      </div>
    `;
  } else {
    const previewAction = isDocumentPreviewable(item)
      ? `<button type="button" class="btn btn-outline btn-sm" onclick="openFilePreview('${escapeHtml(item.id)}')">Preview</button>`
      : "";
    actionsHtml = `
      <div class="transfer-actions">
        ${previewAction}
        ${inboxSaveAction(item)}
        <a class="btn btn-primary btn-sm" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>Download File</span>
        </a>
      </div>
    `;
  }

  const previewable = item.kind !== "image" && isDocumentPreviewable(item);
  return `
    <article class="transfer-card ${dirClass}${previewable ? " previewable" : ""}" data-id="${escapeHtml(item.id)}"${previewable ? ` onclick="if (!event.target.closest('button, a')) openFilePreview('${escapeHtml(item.id)}')"` : ""}>
      <div class="transfer-top">
        <div class="transfer-info">
          <div class="kind-badge-icon kind-${escapeHtml(item.kind)}" title="${escapeHtml(typeBadge)}">
            ${getSvgIcon(item.kind)}
          </div>
          <div class="transfer-title-wrap">
            <div class="transfer-title-copy">
              <h3 class="transfer-name">${escapeHtml(item.name)}</h3>
              <div class="transfer-meta">
                <span class="dir-pill ${dirClass}">${dirLabel}</span>
                <span>${escapeHtml(typeBadge)}</span>
                <span>•</span>
                <span>${escapeHtml(item.size_human)}</span>
                <span>•</span>
                <span>${escapeHtml(item.created_at)}</span>
              </div>
            </div>
            ${imageThumbnail}
          </div>
        </div>
        <button type="button" class="btn btn-icon" title="Remove item" onclick="deleteItem('${escapeHtml(item.id)}')" style="width:28px;height:28px;font-size:0.75rem">✕</button>
      </div>
      ${bodyHtml}
      ${actionsHtml}
    </article>
  `;
}

function renderFeed(force = false) {
  const allItems = STATE.items || [];
  const incomingItems = allItems.filter(it => it.sender !== STATE.role);
  const outgoingItems = allItems.filter(it => it.sender === STATE.role);

  document.getElementById("countAll").textContent = allItems.length;
  document.getElementById("countIncoming").textContent = incomingItems.length;
  document.getElementById("countOutgoing").textContent = outgoingItems.length;
  const downloadAllBtn = document.getElementById("downloadAllBtn");
  const feedBatchActions = document.getElementById("feedBatchActions");
  if (downloadAllBtn && feedBatchActions) {
    downloadAllBtn.href = `${STATE.basePath}/download-all`;
    feedBatchActions.hidden = incomingItems.length < 2;
  }

  let filtered = allItems;
  if (currentFilter === "incoming") filtered = incomingItems;
  if (currentFilter === "outgoing") filtered = outgoingItems;

  const sig = JSON.stringify({ f: currentFilter, items: filtered.map(i => i.id + ":" + i.size) });
  if (!force && sig === lastRenderedSignature) return;
  lastRenderedSignature = sig;

  const container = document.getElementById("itemsContainer");
  if (!container) return;

  if (filtered.length === 0) {
    const emptyMsg = currentFilter === "incoming"
      ? "No incoming items yet. Send something from your other device and it will appear here live."
      : currentFilter === "outgoing"
      ? "You haven't sent any items yet. Use the Send panel to share photos, files, folders, or text."
      : "No items shared yet in this session. Send text, images, files, or a folder from either device!";
    container.innerHTML = `
      <div class="empty-feed-box">
        <div class="empty-feed-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
        </div>
        <p style="margin:0;font-weight:650;font-size:0.88rem">${emptyMsg}</p>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.map(renderItemCard).join("");
}

function setFeedFilter(filterName) {
  currentFilter = filterName;
  document.querySelectorAll(".seg-tab").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-filter") === filterName);
  });
  renderFeed(true);
}

function toggleQrPanel() {
  qrManuallyToggled = true;
  qrVisible = !qrVisible;
  updateUI();
}

async function goHomeScreen() {
  try {
    await fetch(STATE.basePath + "/home", {
      method: "POST",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    });
  } catch (_) {}
  activeWorkspaceView = "sharing";
  window.history.replaceState({ quickshareView: "sharing" }, "", `${window.location.pathname}${window.location.search}`);
  STATE.screen = "home";
  updateUI();
}

function navigateWorkspace(view, updateHistory = true) {
  if (!WORKSPACE_VIEWS.includes(view)) return;
  const changed = activeWorkspaceView !== view;
  if (changed) preserveWorkspaceScroll(view);
  activeWorkspaceView = view;
  if (updateHistory && changed) {
    const hash = view === "sharing" ? "" : `#${view}`;
    window.history.pushState({ quickshareView: view }, "", `${window.location.pathname}${window.location.search}${hash}`);
  }
  updateUI();
  if (!changed) return;
  const pageId = {
    sharing: "workspaceScreen",
    transfers: "transfersScreen",
    scratchpad: "scratchpadScreen",
    connect: "connectScreen",
    controls: "connectScreen",
    devices: "devicesScreen"
  }[view];
  const page = document.getElementById(pageId);
  page?.classList.remove("page-enter");
  requestAnimationFrame(() => page?.classList.add("page-enter"));
  window.setTimeout(() => page?.classList.remove("page-enter"), 350);
}

window.addEventListener("popstate", () => {
  const view = window.location.hash.slice(1);
  const nextView = WORKSPACE_VIEWS.includes(view) ? view : "sharing";
  if (activeWorkspaceView !== nextView) preserveWorkspaceScroll(nextView);
  activeWorkspaceView = nextView;
  updateUI();
});

function preserveWorkspaceScroll(view) {
  const pageIds = {
    sharing: "workspaceScreen",
    transfers: "transfersScreen",
    scratchpad: "scratchpadScreen",
    connect: "connectScreen",
    controls: "connectScreen",
    devices: "devicesScreen"
  };
  const currentPage = document.getElementById(pageIds[activeWorkspaceView]);
  const nextPage = document.getElementById(pageIds[view]);
  if (!currentPage || !nextPage) return;
  if (window.scrollY <= 0) {
    nextPage.style.minHeight = "";
    return;
  }

  const pageTop = currentPage.getBoundingClientRect().top + window.scrollY;
  const neededHeight = window.scrollY + window.innerHeight - pageTop + 8;
  nextPage.style.minHeight = `${Math.max(0, neededHeight)}px`;
}

function updateUI() {
  const isLaptop = STATE.role === "laptop";
  document.getElementById("roleBadge").textContent = isLaptop ? "LAPTOP HUB" : "MOBILE HUB";
  document.getElementById("stopAppBtn").style.display = isLaptop ? "inline-flex" : "none";
  document.getElementById("cameraCaptureBtn").hidden = isLaptop;
  document.getElementById("voiceNotePanel").hidden = isLaptop;
  document.getElementById("laptopRemotePanel").hidden = isLaptop;
  document.getElementById("connectPageEyebrow").textContent = isLaptop ? "PAIR A PHONE" : "LAPTOP CONTROLS";
  document.getElementById("connectPageTitle").textContent = isLaptop ? "Connect your phone" : "Control your laptop";
  document.getElementById("connectPageSubtitle").textContent = isLaptop
    ? "Scan the QR code or open the link on your phone to start sharing."
    : "Take a quick look at your laptop or control media and presentations.";
  renderDeviceApprovals(STATE.pendingDevices || [], STATE.trustedDevices || []);

  const homeUrlEl = document.getElementById("homePhoneUrl");
  if (homeUrlEl) homeUrlEl.textContent = STATE.phoneUrl || "";
  const homeLapEl = document.getElementById("homeLaptopUrl");
  if (homeLapEl) homeLapEl.textContent = STATE.laptopUrl || "http://localhost:8765/laptop";
  const lapUrlEl = document.getElementById("laptopUrlText");
  if (lapUrlEl) lapUrlEl.textContent = STATE.laptopUrl || "http://localhost:8765/laptop";
  const ipUrlEl = document.getElementById("phoneIpUrlText");
  if (ipUrlEl) ipUrlEl.textContent = STATE.phoneIpUrl || "";

  // Connection badge
  const badge = document.getElementById("connectionBadge");
  const badgeText = document.getElementById("connectionText");
  if (isLaptop) {
    if (STATE.phoneConnected) {
      badge.classList.add("connected");
      badgeText.textContent = "Phone Connected • Two-way active";
    } else {
      badge.classList.remove("connected");
      badgeText.textContent = "Scan QR or open link on phone";
    }
  } else {
    badge.classList.add("connected");
    badgeText.textContent = "Connected to Laptop • Two-way active";
  }

  // Send panel headings
  document.getElementById("sendPanelTitle").textContent = isLaptop ? "Send to Phone" : "Send to Laptop";
  document.getElementById("sendPanelSub").textContent = isLaptop
    ? "Share files, images, folders, clipboard, or text with your phone"
    : "Share photos, files, folders, or text with your laptop";
  document.getElementById("clipboardBtnLabel").textContent = "Clipboard";

  const homeEl = document.getElementById("homeScreen");
  const navigationEl = document.getElementById("workspaceNavigation");
  const backBtn = document.getElementById("backHomeBtn");
  const workspacePageIds = {
    sharing: "workspaceScreen",
    transfers: "transfersScreen",
    scratchpad: "scratchpadScreen",
    connect: "connectScreen",
    controls: "connectScreen",
    devices: "devicesScreen"
  };
  const isHome = isLaptop && STATE.screen === "home" && !STATE.phoneConnected && (STATE.items || []).length === 0;

  if (isHome) {
    homeEl.style.display = "block";
    navigationEl.hidden = true;
    document.getElementById("toggleQrBtn").style.display = "none";
    for (const pageId of new Set(Object.values(workspacePageIds))) {
      const page = document.getElementById(pageId);
      if (pageId === "workspaceScreen") {
        page.style.display = "none";
      } else {
        page.hidden = true;
      }
    }
    if (backBtn) backBtn.style.display = "none";
  } else {
    homeEl.style.display = "none";
    navigationEl.hidden = false;
    for (const pageId of new Set(Object.values(workspacePageIds))) {
      const page = document.getElementById(pageId);
      const isActivePage = workspacePageIds[activeWorkspaceView] === pageId;
      if (pageId === "workspaceScreen") {
        page.style.display = isActivePage ? "grid" : "none";
      } else {
        page.hidden = !isActivePage;
      }
    }
    document.getElementById("sharingNavBtn").hidden = false;
    document.getElementById("transfersNavBtn").hidden = false;
    document.getElementById("scratchpadNavBtn").hidden = false;
    document.getElementById("connectNavBtn").hidden = !isLaptop;
    document.getElementById("controlsNavBtn").hidden = isLaptop;
    document.getElementById("devicesNavBtn").hidden = !isLaptop;
    document.getElementById("connectNavLabel").textContent = isLaptop ? "Connect" : "Controls";
    for (const view of WORKSPACE_VIEWS) {
      const buttonId = {
        sharing: "sharingNavBtn",
        transfers: "transfersNavBtn",
        scratchpad: "scratchpadNavBtn",
        connect: "connectNavBtn",
        controls: "controlsNavBtn",
        devices: "devicesNavBtn"
      }[view];
      const button = document.getElementById(buttonId);
      button.classList.toggle("active", view === activeWorkspaceView);
      button.setAttribute("aria-current", view === activeWorkspaceView ? "page" : "false");
    }
    document.getElementById("toggleQrBtn").style.display =
      isLaptop && activeWorkspaceView === "connect" ? "inline-flex" : "none";
    if (isLaptop) {
      if (backBtn) {
        backBtn.style.display = (!STATE.phoneConnected && (STATE.items || []).length === 0) ? "inline-flex" : "none";
      }
      if (!qrManuallyToggled) {
        qrVisible = !STATE.phoneConnected;
      }
      const qrPanel = document.getElementById("qrPanel");
      qrPanel.style.display = activeWorkspaceView === "connect" && qrVisible ? "block" : "none";
      document.getElementById("toggleQrLabel").textContent = qrVisible ? "Hide QR" : "Show QR";
      if (STATE.qrDataUri) {
        document.getElementById("qrImage").src = STATE.qrDataUri;
      }
      document.getElementById("phoneUrlText").textContent = STATE.phoneUrl;
    }
  }

  renderFeed(false);
}

function renderDeviceApprovals(devices, trustedDevices) {
  const list = document.getElementById("deviceApprovalList");
  const savedList = document.getElementById("trustedDevicesList");
  if (!list || !savedList || STATE.role !== "laptop") {
    return;
  }
  const requestEmpty = document.getElementById("deviceRequestsEmpty");
  const trustedEmpty = document.getElementById("trustedDevicesEmpty");
  const requestCount = document.getElementById("deviceRequestsSectionCount");
  const trustedCount = document.getElementById("trustedDevicesSectionCount");
  const navCount = document.getElementById("pendingDevicesCount");
  requestEmpty.hidden = devices.length > 0;
  trustedEmpty.hidden = trustedDevices.length > 0;
  requestCount.textContent = `${devices.length} ${devices.length === 1 ? "request" : "requests"}`;
  trustedCount.textContent = `${trustedDevices.length} ${trustedDevices.length === 1 ? "device" : "devices"}`;
  navCount.hidden = devices.length === 0;
  navCount.textContent = String(devices.length);
  list.innerHTML = devices.map(device => `
    <div class="approval-request">
      <div><strong>${escapeHtml(device.name)}</strong><small>Request from ${escapeHtml(device.ip)}</small></div>
      <div class="approval-actions">
        <button type="button" class="btn btn-outline btn-xs" onclick="decidePhoneApproval('${escapeHtml(device.id)}', 'once')">Allow once</button>
        <button type="button" class="btn btn-primary btn-xs" onclick="decidePhoneApproval('${escapeHtml(device.id)}', 'remember')">Remember</button>
        <button type="button" class="btn btn-outline btn-xs" onclick="decidePhoneApproval('${escapeHtml(device.id)}', false)">Deny</button>
      </div>
    </div>
  `).join("");
  savedList.innerHTML = trustedDevices.map(device => `
    <div class="approval-request">
      <div><strong>${escapeHtml(device.name)}</strong><small>Remembered ${escapeHtml(device.approved_at || "previously")}</small></div>
      <div class="approval-actions">
        <button type="button" class="btn btn-outline btn-xs" onclick="forgetPhoneDevice('${escapeHtml(device.id)}')">Forget device</button>
      </div>
    </div>
  `).join("");
}

async function decidePhoneApproval(deviceId, decision) {
  try {
    const action = decision === "remember" ? "remember" : decision === "once" ? "approve" : "deny";
    const response = await fetch(`${STATE.basePath}/${action}-device/${encodeURIComponent(deviceId)}`, {
      method: "POST",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not update device approval.");
    showToast(decision === "remember"
      ? "Phone remembered on this laptop."
      : decision === "once"
        ? "Phone approved for this session."
        : "Phone request denied.");
    await pollState();
  } catch (error) {
    showError(error.message || "Could not update device approval.");
  }
}

async function forgetPhoneDevice(deviceId) {
  if (!window.confirm("Forget this phone? It will need approval the next time it connects.")) return;
  try {
    const response = await fetch(`${STATE.basePath}/forget-device/${encodeURIComponent(deviceId)}`, {
      method: "POST",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Could not forget this device.");
    showToast("Remembered phone removed.");
    await pollState();
  } catch (error) {
    showError(error.message || "Could not forget this device.");
  }
}

async function startWorkspace(mode) {
  showError("");
  const endpoint = mode === "receive" ? "/receive" : "/open-send";
  try {
    const res = await fetch(STATE.basePath + endpoint, {
      method: "POST",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    });
    const data = await res.json();
    if (data.ok) {
      STATE.screen = "workspace";
      qrManuallyToggled = true;
      qrVisible = true;
      updateUI();
    }
  } catch (_) {
    STATE.screen = "workspace";
    updateUI();
  }
}

async function shareClipboard() {
  showError("");
  if (STATE.role === "laptop") {
    try {
      const res = await fetch(STATE.basePath + "/clipboard", {
        method: "POST",
        headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
      });
      const data = await res.json();
      if (!data.ok) {
        if (navigator.clipboard && navigator.clipboard.readText) {
          const clipText = await navigator.clipboard.readText();
          if (clipText && clipText.trim()) {
            document.getElementById("textMessageInput").value = clipText;
            return sendTextMessage();
          }
        }
        showError(data.error || "Could not read clipboard.");
        return;
      }
      showToast("Clipboard shared!");
      await pollState();
    } catch (_) {
      showError("Could not share clipboard.");
    }
  } else {
    if (navigator.clipboard && navigator.clipboard.readText) {
      try {
        const text = await navigator.clipboard.readText();
        if (text && text.trim()) {
          document.getElementById("textMessageInput").value = text;
          return sendTextMessage();
        }
      } catch (_) {}
    }
    const ta = document.getElementById("textMessageInput");
    if (ta) ta.focus();
    showToast("Long-press inside the text box to paste & send");
  }
}

async function sendTextMessage() {
  showError("");
  const ta = document.getElementById("textMessageInput");
  const text = ta ? ta.value : "";
  if (!text || !text.trim()) {
    showError("Please enter or paste some text first.");
    return;
  }
  const fd = new FormData();
  fd.append("message", text);
  if (document.getElementById("burnAfterRead").checked) {
    fd.append("burn_after_read", "1");
  }
  const sent = await performFormDataUpload(fd, "Sending text…");
  if (!sent) return;
  if (ta) {
    ta.value = "";
    updateCharCounter();
  }
  document.getElementById("burnAfterRead").checked = false;
}

async function toggleVoiceNote() {
  const button = document.getElementById("voiceRecordBtn");
  const label = document.getElementById("voiceRecordLabel");
  const status = document.getElementById("voiceRecordStatus");
  if (voiceRecorder && voiceRecorder.state === "recording") {
    button.disabled = true;
    label.textContent = "Finishing…";
    voiceRecorder.stop();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    showError("Voice notes need microphone recording support. Open QuickShare over trusted HTTPS and use a supported browser.");
    return;
  }
  try {
    voiceRecorderStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const supportedTypes = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"];
    const mimeType = supportedTypes.find(type => MediaRecorder.isTypeSupported(type));
    voiceRecorderChunks = [];
    voiceRecorder = new MediaRecorder(voiceRecorderStream, mimeType ? { mimeType } : undefined);
    voiceRecorder.addEventListener("dataavailable", event => {
      if (event.data.size) voiceRecorderChunks.push(event.data);
    });
    voiceRecorder.addEventListener("stop", async () => {
      const actualMime = voiceRecorder.mimeType || mimeType || "audio/webm";
      const extension = actualMime.includes("ogg") ? "ogg" : actualMime.includes("mp4") ? "m4a" : "webm";
      const audio = new File(
        voiceRecorderChunks,
        `voice-note-${new Date().toISOString().replace(/[:.]/g, "-")}.${extension}`,
        { type: actualMime }
      );
      voiceRecorderStream?.getTracks().forEach(track => track.stop());
      voiceRecorderStream = null;
      voiceRecorder = null;
      voiceRecorderChunks = [];
      button.disabled = false;
      label.textContent = "Record voice note";
      status.textContent = "Uploading voice note…";
      const uploaded = await uploadSelectedFiles([audio], false);
      status.textContent = uploaded ? "Voice note sent." : "Could not upload the voice note.";
    });
    voiceRecorder.start();
    button.classList.add("recording");
    label.textContent = "Stop & send voice note";
    status.textContent = "Recording… tap stop when finished.";
    button.addEventListener("click", () => button.classList.remove("recording"), { once: true });
  } catch (error) {
    voiceRecorderStream?.getTracks().forEach(track => track.stop());
    voiceRecorderStream = null;
    button.disabled = false;
    label.textContent = "Record voice note";
    status.textContent = "Microphone permission was not granted.";
    showError(error.message || "Could not start microphone recording.");
  }
}

async function remoteAction(action) {
  const status = document.getElementById("remoteActionStatus");
  status.textContent = "Sending command…";
  try {
    const response = await fetch(`${STATE.basePath}/remote/${encodeURIComponent(action)}`, {
      method: "POST",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "Laptop control failed.");
    status.textContent = "Command sent.";
  } catch (error) {
    status.textContent = error.message || "Laptop control failed.";
  }
}

async function viewLaptopScreen() {
  const status = document.getElementById("remoteActionStatus");
  const image = document.getElementById("remoteScreenPreview");
  status.textContent = "Capturing laptop screen…";
  try {
    const response = await fetch(`${STATE.basePath}/remote/screenshot`, { cache: "no-store" });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error || "Could not capture laptop screen.");
    }
    if (remoteScreenObjectUrl) URL.revokeObjectURL(remoteScreenObjectUrl);
    remoteScreenObjectUrl = URL.createObjectURL(await response.blob());
    image.src = remoteScreenObjectUrl;
    image.hidden = false;
    status.textContent = "Laptop screen captured. Tap the image to enlarge.";
  } catch (error) {
    status.textContent = error.message || "Could not capture laptop screen.";
  }
}

async function uploadSelectedFiles(fileList, isFolder) {
  showError("");
  if (!fileList || fileList.length === 0) return false;
  const fileBytes = Array.from(fileList).reduce((total, file) => total + file.size, 0);
  const requestBytes = fileBytes + fileList.length * 1024 + 4096;
  if (requestBytes > STATE.maxUploadMib * 1024 ** 2) {
    showError(`Selected files exceed the ${STATE.maxUploadMib} MiB upload limit.`);
    return false;
  }
  const requiredDiskBytes = requestBytes * (STATE.saveInbox ? 3 : 2);
  if (Number.isFinite(STATE.freeDiskBytes) && requiredDiskBytes > STATE.freeDiskBytes) {
    showError(`Upload may need up to ${formatBytes(requiredDiskBytes)} temporary storage; ${formatBytes(STATE.freeDiskBytes)} is available.`);
    return false;
  }
  const fd = new FormData();
  if (isFolder) {
    fd.append("is_folder", "1");
    for (let i = 0; i < fileList.length; i++) {
      const f = fileList[i];
      fd.append("folderitems", f, f.webkitRelativePath || f.name);
    }
  } else {
    for (let i = 0; i < fileList.length; i++) {
      fd.append("files", fileList[i], fileList[i].name);
    }
  }
  const label = isFolder
    ? `Packing & sending folder (${fileList.length} files)…`
    : `Sending ${fileList.length} file${fileList.length > 1 ? "s" : ""}…`;
  return performFormDataUpload(fd, label, isFolder ? "/folder" : "/upload");
}

function getPastedImageFiles(clipboardData) {
  if (!clipboardData || !clipboardData.items) return [];

  const imageTypes = {
    "image/avif": "avif",
    "image/bmp": "bmp",
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/svg+xml": "svg",
    "image/tiff": "tiff",
    "image/webp": "webp"
  };
  const files = [];

  Array.from(clipboardData.items).forEach((item, index) => {
    if (item.kind !== "file" || !item.type.startsWith("image/")) return;
    const image = item.getAsFile();
    if (!image) return;

    const extension = image.name.match(/\.([a-z0-9]+)$/i)?.[1]
      || imageTypes[image.type.toLowerCase()]
      || image.type.split("/")[1]?.split("+")[0]
      || "png";
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    files.push(new File([image], `screenshot-${timestamp}-${index + 1}.${extension}`, {
      type: image.type || item.type
    }));
  });

  return files;
}

function performFormDataUpload(formData, labelText, endpoint = "/upload") {
  return new Promise((resolve) => {
    const wrap = document.getElementById("uploadProgressWrap");
    const bar = document.getElementById("uploadProgressBar");
    const detailsEl = document.getElementById("uploadProgressDetails");
    const lblEl = document.getElementById("uploadProgressLabel");

    lblEl.textContent = labelText;
    detailsEl.textContent = "0%";
    document.getElementById("storageInfo").textContent =
      `Laptop storage available: ${formatBytes(STATE.freeDiskBytes)}`;
    bar.style.width = "0%";
    wrap.classList.add("active");

    const xhr = new XMLHttpRequest();
    let lastProgressAt = performance.now();
    let lastLoaded = 0;
    xhr.open("POST", STATE.basePath + endpoint, true);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        const pct = Math.min(100, Math.round((ev.loaded / ev.total) * 100));
        const now = performance.now();
        const seconds = (now - lastProgressAt) / 1000;
        const speed = seconds > 0 ? (ev.loaded - lastLoaded) / seconds : 0;
        const remaining = speed > 0 ? Math.ceil((ev.total - ev.loaded) / speed) : null;
        bar.style.width = pct + "%";
        detailsEl.textContent = `${pct}% · ${(speed / (1024 ** 2)).toFixed(2)} MB/s${remaining === null ? "" : ` · ~${remaining}s`}`;
        lastProgressAt = now;
        lastLoaded = ev.loaded;
      }
    };

    xhr.onload = async () => {
      wrap.classList.remove("active");
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
          showToast("Shared! Ready in the session feed.");
          await pollState();
          resolve(true);
        } else {
          showError(data.error || "Upload failed.");
          resolve(false);
        }
      } catch (_) {
        showError("Upload failed.");
        resolve(false);
      }
    };

    xhr.onerror = () => {
      wrap.classList.remove("active");
      showError("Network error while uploading.");
      resolve(false);
    };

    xhr.send(formData);
  });
}

async function deleteItem(itemId) {
  try {
    await fetch(STATE.basePath + "/delete/" + encodeURIComponent(itemId), {
      method: "POST",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    });
    await pollState();
  } catch (_) {}
}

async function confirmStopSession() {
  closeStopModal();
  try {
    await fetch(STATE.basePath + "/stop", {
      method: "POST",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" }
    });
  } catch (_) {}
  document.body.innerHTML = `
    <div style="max-width:460px;margin:90px auto;background:var(--bg-card);padding:32px;border-radius:20px;text-align:center;border:1px solid var(--border-color);box-shadow:var(--shadow-card)">
      <h2 style="margin:0 0 8px">QuickShare Session Ended</h2>
      <p style="color:var(--text-secondary);margin:0">The local sharing server has stopped. You can close this tab.</p>
    </div>
  `;
}

async function pollState() {
  try {
    const res = await fetch(STATE.basePath + "/api/state", {
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      cache: "no-store"
    });
    if (!res.ok) return;
    const data = await res.json();
    if (!data.ok) return;
    const wasConnected = STATE.phoneConnected;
    STATE.phoneConnected = data.phone_connected;
    STATE.screen = STATE.role === "laptop" ? data.screen : "workspace";
    STATE.items = data.items || [];
    STATE.pendingDevices = data.pending_devices || [];
    STATE.trustedDevices = data.trusted_devices || [];
    STATE.freeDiskBytes = Number(data.free_disk_bytes) || 0;
    document.getElementById("storageInfo").textContent =
      `Laptop storage available: ${formatBytes(STATE.freeDiskBytes)}`;
    updateScratchpadFromServer(data);
    if (!wasConnected && STATE.phoneConnected && STATE.role === "laptop") {
      showToast("Phone connected! Two-way sharing is active.");
    }
    updateUI();
  } catch (_) {}
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPromptEvent = event;
});

window.addEventListener("appinstalled", () => {
  installPromptEvent = null;
  document.getElementById("installAppPanel").hidden = true;
  showToast("QuickShare installed.");
});

const installAppBtn = document.getElementById("installAppBtn");
const installAppPanel = document.getElementById("installAppPanel");
installAppPanel.hidden = STATE.role !== "phone"
  || window.matchMedia("(display-mode: standalone)").matches
  || window.navigator.standalone === true;

document.getElementById("scratchpadInput").addEventListener("input", queueScratchpadSave);
document.getElementById("scratchpadInput").value = String(STATE.scratchpad || "");
document.getElementById("storageInfo").textContent =
  `Laptop storage available: ${formatBytes(STATE.freeDiskBytes)}`;

document.addEventListener("paste", (event) => {
  if (STATE.role !== "laptop") return;
  const images = getPastedImageFiles(event.clipboardData);
  if (images.length === 0) return;

  event.preventDefault();
  void uploadSelectedFiles(images, false);
});

// Drag & Drop Setup
const dz = document.getElementById("dropzone");
if (dz) {
  ["dragenter", "dragover"].forEach(evName => {
    dz.addEventListener(evName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach(evName => {
    dz.addEventListener(evName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.remove("dragover");
    });
  });
  dz.addEventListener("drop", (e) => {
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length > 0) {
      uploadSelectedFiles(files, false);
    }
  });
}

initTheme();
if (STATE.error) {
  showError(STATE.error);
}
updateUI();
setInterval(pollState, 1500);
