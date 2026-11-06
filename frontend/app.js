/* QuickShare Frontend Application (Separated from Python Backend) */
const STATE = window.__INITIAL_STATE__ || {
  role: "laptop",
  basePath: "/app",
  screen: "home",
  initialFocus: "send",
  phoneConnected: false,
  phoneUrl: "http://192.168.1.10:8765/share",
  qrDataUri: "",
  maxUploadMib: 500,
  remainingSeconds: 3600,
  error: "",
  items: []
};

let currentFilter = "all";
let qrManuallyToggled = false;
let qrVisible = true;
let lastRenderedSignature = "";

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

function closeLightbox() {
  const lb = document.getElementById("lightbox");
  if (lb) lb.classList.remove("open");
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
}

function copyItemTextById(itemId, btnEl) {
  const item = (STATE.items || []).find(it => it.id === itemId);
  if (!item) return;
  copyTextWithFallback(item.text_content || "", btnEl, itemId);
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

  if (item.kind === "text") {
    if (item.text_too_long) {
      bodyHtml = `
        <div class="transfer-body">
          <pre class="text-snippet-pre">${escapeHtml(item.text_content)}</pre>
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
          </div>
        </div>
      `;
    } else {
      bodyHtml = `
        <div class="transfer-body">
          <pre class="text-snippet-pre">${escapeHtml(item.text_content)}</pre>
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
        </div>
      `;
    }
  } else if (item.kind === "image") {
    bodyHtml = `
      <div class="transfer-body">
        <div class="image-stage">
          <img src="${escapeHtml(item.preview_url)}" alt="${escapeHtml(item.name)}" loading="lazy" onclick="openLightbox('${escapeHtml(item.preview_url)}')">
        </div>
      </div>
    `;
    actionsHtml = `
      <div class="transfer-actions">
        <a class="btn btn-primary btn-sm" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>Download Image</span>
        </a>
      </div>
    `;
  } else if (item.kind === "zip") {
    actionsHtml = `
      <div class="transfer-actions">
        <a class="btn btn-primary btn-sm" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>Download ZIP</span>
        </a>
      </div>
    `;
  } else {
    actionsHtml = `
      <div class="transfer-actions">
        <a class="btn btn-primary btn-sm" href="${escapeHtml(item.download_url)}" download="${escapeHtml(item.name)}">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          <span>Download File</span>
        </a>
      </div>
    `;
  }

  return `
    <article class="transfer-card ${dirClass}" data-id="${escapeHtml(item.id)}">
      <div class="transfer-top">
        <div class="transfer-info">
          <div class="kind-badge-icon kind-${escapeHtml(item.kind)}" title="${escapeHtml(typeBadge)}">
            ${getSvgIcon(item.kind)}
          </div>
          <div class="transfer-title-wrap">
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
  STATE.screen = "home";
  updateUI();
}

function updateUI() {
  const isLaptop = STATE.role === "laptop";
  document.getElementById("roleBadge").textContent = isLaptop ? "LAPTOP HUB" : "MOBILE HUB";
  document.getElementById("stopAppBtn").style.display = isLaptop ? "inline-flex" : "none";

  const homeUrlEl = document.getElementById("homePhoneUrl");
  if (homeUrlEl) homeUrlEl.textContent = STATE.phoneUrl || "";

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
  const wsEl = document.getElementById("workspaceScreen");
  const backBtn = document.getElementById("backHomeBtn");

  if (isLaptop && STATE.screen === "home" && !STATE.phoneConnected && (STATE.items || []).length === 0) {
    homeEl.style.display = "block";
    wsEl.style.display = "none";
    document.getElementById("toggleQrBtn").style.display = "none";
    if (backBtn) backBtn.style.display = "none";
  } else {
    homeEl.style.display = "none";
    wsEl.style.display = "grid";
    if (isLaptop) {
      document.getElementById("toggleQrBtn").style.display = "inline-flex";
      if (backBtn) {
        backBtn.style.display = (!STATE.phoneConnected && (STATE.items || []).length === 0) ? "inline-flex" : "none";
      }
      if (!qrManuallyToggled) {
        qrVisible = !STATE.phoneConnected;
      }
      const qrPanel = document.getElementById("qrPanel");
      qrPanel.style.display = qrVisible ? "block" : "none";
      document.getElementById("toggleQrLabel").textContent = qrVisible ? "Hide QR" : "Show QR";
      if (STATE.qrDataUri) {
        document.getElementById("qrImage").src = STATE.qrDataUri;
      }
      document.getElementById("phoneUrlText").textContent = STATE.phoneUrl;
    }
  }

  renderFeed(false);
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
  await performFormDataUpload(fd, "Sending text…");
  if (ta) {
    ta.value = "";
    updateCharCounter();
  }
}

async function uploadSelectedFiles(fileList, isFolder) {
  showError("");
  if (!fileList || fileList.length === 0) return;
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
  await performFormDataUpload(fd, label);
}

function performFormDataUpload(formData, labelText) {
  return new Promise((resolve) => {
    const wrap = document.getElementById("uploadProgressWrap");
    const bar = document.getElementById("uploadProgressBar");
    const pctEl = document.getElementById("uploadProgressPct");
    const lblEl = document.getElementById("uploadProgressLabel");

    lblEl.textContent = labelText;
    pctEl.textContent = "0%";
    bar.style.width = "0%";
    wrap.classList.add("active");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", STATE.basePath + "/upload", true);
    xhr.setRequestHeader("Accept", "application/json");
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");

    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        const pct = Math.min(100, Math.round((ev.loaded / ev.total) * 100));
        bar.style.width = pct + "%";
        pctEl.textContent = pct + "%";
      }
    };

    xhr.onload = async () => {
      wrap.classList.remove("active");
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
          showToast("Shared! Ready in the session feed.");
          await pollState();
        } else {
          showError(data.error || "Upload failed.");
        }
      } catch (_) {
        showError("Upload failed.");
      }
      resolve();
    };

    xhr.onerror = () => {
      wrap.classList.remove("active");
      showError("Network error while uploading.");
      resolve();
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
    if (!wasConnected && STATE.phoneConnected && STATE.role === "laptop") {
      showToast("Phone connected! Two-way sharing is active.");
    }
    updateUI();
  } catch (_) {}
}

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
