/* oida dashboard — one listen surface, one daemon, every surface in sync.
   Vanilla JS. State lives in the daemon; this client renders it. */

"use strict";

const state = {
  source: "system",
  preset: "basic",
  presets: [],
  skills: [],
  selectedSkills: new Set(),
  presetSkillIds: [],
  lastEvent: null,
  lastEventId: null,
  lastJson: null,
  audioDir: "",
  engine: null,
  engineSignature: null,
  phase: "idle", // idle | waiting | recording | analyzing
  recorder: null,
  recordedChunks: [],
  recordTimer: null,
  captureHintTimer: null,
  captureRequestId: null,
  abortController: null,
  wikiToken: 0,
  sonicfieldAvailable: false,
  micDevices: [],
  monitor: null, // {stream, ctx, analyser, raf, peak}
};

const el = (id) => document.getElementById(id);
const ui = {
  resultCard: el("resultCard"),
  listenButton: el("listenButton"),
  listenLabel: el("listenLabel"),
  listenStatus: el("listenStatus"),
  captureSeconds: el("captureSeconds"),
  systemPanel: el("systemPanel"),
  micPanel: el("micPanel"),
  micDevice: el("micDevice"),
  micMonitor: el("micMonitor"),
  micMeter: el("micMeter"),
  micMeterFill: el("micMeterFill"),
  micMeterPeak: el("micMeterPeak"),
  filePanel: el("filePanel"),
  browseFile: el("browseFile"),
  modeButton: el("modeButton"),
  modeIcon: el("modeIcon"),
  modeName: el("modeName"),
  modeMenu: el("modeMenu"),
  sideLeft: el("sideLeft"),
  sideRight: el("sideRight"),
  leftToggle: el("leftToggle"),
  rightToggle: el("rightToggle"),
  skillList: el("skillList"),
  skillNote: el("skillNote"),
  engineNote: el("engineNote"),
  engineFacts: el("engineFacts"),
  instructModel: el("instructModel"),
  thinkingModel: el("thinkingModel"),
  warmEngine: el("warmEngine"),
  audioPath: el("audioPath"),
  audioDirNote: el("audioDirNote"),
  analyzePath: el("analyzePath"),
  fileInput: el("fileInput"),
  resultTitle: el("resultTitle"),
  resultSummary: el("resultSummary"),
  resultTags: el("resultTags"),
  resultBody: el("resultBody"),
  leftResize: el("leftResize"),
  rightResize: el("rightResize"),
  exportDrop: el("exportDrop"),
  exportMenu: el("exportMenu"),
  rememberItem: el("rememberItem"),
  wikiItem: el("wikiItem"),
  jsonItem: el("jsonItem"),
  soundItem: el("soundItem"),
  promptItem: el("promptItem"),
  skillsFootButton: el("skillsFootButton"),
  configMenu: el("configMenu"),
  jsonWrap: el("jsonWrap"),
  jsonCopy: el("jsonCopy"),
  jsonOutput: el("jsonOutput"),
  germNote: el("germNote"),
  wikiModal: el("wikiModal"),
  wikiQuery: el("wikiQuery"),
  wikiGo: el("wikiGo"),
  wikiTerms: el("wikiTerms"),
  wikiGroups: el("wikiGroups"),
  historyList: el("historyList"),
  historyNote: el("historyNote"),
  memorySearch: el("memorySearch"),
  memoryGo: el("memoryGo"),
  memoryList: el("memoryList"),
  engineAddress: el("engineAddress"),
  engineAudioDir: el("engineAudioDir"),
};

/* ────────────────────────────── helpers ─────────────────────────── */

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      if (body && body.detail) {
        // FastAPI validation errors ship detail as a list of objects.
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch (_) { /* keep status */ }
    throw new Error(detail);
  }
  return response.json();
}

const post = (url, body, options) =>
  fetchJson(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}), ...(options || {}) });

function setListenStatus(text, tone) {
  ui.listenStatus.textContent = text;
  ui.listenStatus.className = `listen-status${tone ? ` ${tone}` : ""}`;
}

function setPhase(phase, label) {
  state.phase = phase;
  const busy = phase !== "idle";
  ui.listenButton.classList.toggle("busy", phase === "analyzing" || phase === "waiting");
  ui.listenButton.classList.toggle("stop", busy);
  ui.listenLabel.textContent = label || (busy ? "Stop" : "Listen");
}

const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

// A mic recording is a purely local flow; daemon events from other surfaces
// must not steal its phase. Same while a local /listen-event fetch owns the UI.
function localFlowOwnsUi() {
  return state.phase === "recording" || Boolean(state.abortController);
}

function timeAgo(iso) {
  if (!iso) return "";
  const delta = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!Number.isFinite(delta) || delta < 0) return "";
  if (delta < 90) return "now";
  if (delta < 3600) return `${Math.round(delta / 60)}m`;
  if (delta < 86400) return `${Math.round(delta / 3600)}h`;
  return `${Math.round(delta / 86400)}d`;
}

/* ─────────────────────────── daemon status ──────────────────────── */

async function refreshHealth() {
  try {
    const health = await fetchJson("/health");
    ui.engineAddress.textContent = `${health.host || "127.0.0.1"}:${health.port}`;
    state.audioDir = health.audio_dir || "";
    state.sonicfieldAvailable = Boolean(health.sonicfield && health.sonicfield.available);
    ui.audioDirNote.textContent = state.audioDir;
    ui.engineAudioDir.textContent = state.audioDir;
    if (!ui.audioPath.value && state.audioDir) ui.audioPath.placeholder = `${state.audioDir}/…`;
    if (state.lastEvent) {
      ui.wikiItem.disabled = !state.sonicfieldAvailable;
    }
    renderEngine(health.engine);
  } catch (_) {
    ui.engineAddress.textContent = "offline";
  }
}

const ENGINE_LABELS = {
  ready: "moss ready",
  warming: "warming…",
  cold: "cold",
  degraded: "unavailable",
  stub: "dsp only",
  remote: "remote",
};

function renderEngine(engine) {
  if (!engine) return;
  // Health polls every 20 s; rebuilding the selects for identical data closes
  // open dropdowns and stomps in-flight assignment. Skip unchanged payloads.
  const signature = JSON.stringify(engine);
  if (signature === state.engineSignature) return;
  state.engineSignature = signature;
  state.engine = engine;
  ui.engineNote.textContent = `${engine.instruct_model || engine.profile || ""} · ${ENGINE_LABELS[engine.state] || engine.state || ""}`;
  ui.engineNote.dataset.state = engine.state || "";

  const facts = [
    ["state", `${ENGINE_LABELS[engine.state] || engine.state}${engine.detail ? ` — ${engine.detail}` : ""}`],
    ["profile", engine.profile],
    ["device", engine.device || "—"],
    ["resident", (engine.loaded_models || []).join(", ") || "nothing loaded"],
    ["warm-up", engine.warmed_ms ? `${(engine.warmed_ms / 1000).toFixed(1)} s` : "—"],
    ["chunking", engine.chunk_seconds ? `${engine.chunk_seconds.toFixed(0)} s per model pass` : "—"],
  ];
  ui.engineFacts.innerHTML = facts
    .map(([key, value]) => `<div class="fact"><span class="fact-key">${escapeHtml(key)}</span><span class="fact-value">${escapeHtml(value)}</span></div>`)
    .join("");

  const models = engine.available_models || [];
  for (const [select, current] of [
    [ui.instructModel, engine.instruct_model],
    [ui.thinkingModel, engine.thinking_model],
  ]) {
    select.innerHTML = "";
    if (!models.length) {
      select.appendChild(new Option(current || "none found", "", true, true));
      select.disabled = true;
      continue;
    }
    select.disabled = false;
    for (const model of models) {
      const label = model.size_gb ? `${model.name} · ${model.size_gb} GB` : model.name;
      const option = new Option(label, model.name, false, model.name === current);
      if (model.description) option.title = model.description;
      select.appendChild(option);
    }
  }

  const roles = document.getElementById("modelRoles");
  if (roles) {
    const described = models
      .map((model) => `${model.name.replace("MOSS-Audio-", "")} — ${model.description || ""}`)
      .join(" · ");
    roles.textContent = described
      ? `Instruct hears (captions, transcripts, events); Thinking reasons (QA, music, deep routes). ${described}`
      : "Instruct hears (captions, transcripts, events); Thinking reasons (QA, music, deep routes).";
  }
}

async function assignModel(kind, select) {
  const model = select.value;
  if (!model) return;
  select.disabled = true;
  try {
    const result = await post("/engine/model", { model_kind: kind, model });
    renderEngine(result);
    setListenStatus(result.warming ? `Loading ${model}…` : `${kind} → ${model}`, "active");
  } catch (error) {
    setListenStatus(`Engine: ${error.message}`, "error");
  } finally {
    select.disabled = false;
  }
}

ui.instructModel.addEventListener("change", () => assignModel("instruct", ui.instructModel));
ui.thinkingModel.addEventListener("change", () => assignModel("thinking", ui.thinkingModel));
ui.warmEngine.addEventListener("click", async () => {
  try {
    renderEngine(await post("/engine/warm"));
  } catch (error) {
    setListenStatus(`Engine: ${error.message}`, "error");
  }
});

/* ─────────────────────── skill / engine / path modals ───────────── */

const PANEL_DIALOGS = { skill: "skillModal", engine: "engineModal", path: "pathModal" };

// Callable from the native shell and the rail icons; renders each panel as a
// modal dialog. Modals are exclusive: opening one closes whatever is open.
window.oidaOpenPanel = (name) => {
  const target = document.getElementById(PANEL_DIALOGS[name]);
  if (!target || typeof target.showModal !== "function") return;
  document.querySelectorAll("dialog[open]").forEach((other) => { if (other !== target) other.close(); });
  if (!target.open) target.showModal();
};

document.querySelectorAll(".modal").forEach((dialog) => {
  dialog.querySelector("[data-close]")?.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
});

// The native shell injects __oidaNative; that reveals the shell-only actions
// (floating listener, open-in-browser), which post into the app.
if (window.__oidaNative) document.body.classList.add("native");

function shellAction(name) {
  if (name === "reload" && !window.webkit?.messageHandlers?.oidaShell) {
    window.location.reload();
    return;
  }
  window.webkit?.messageHandlers?.oidaShell?.postMessage(name);
}

// the floating-listener corner button (drop-menu items are delegated below)
document.querySelectorAll("button[data-shell]:not(.drop-item)").forEach((button) =>
  button.addEventListener("click", () => shellAction(button.dataset.shell))
);

ui.skillsFootButton.addEventListener("click", () => window.oidaOpenPanel("skill"));

// configuration corner menu: Engine / Path / Reload / Open in browser
ui.configMenu.addEventListener("click", (event) => {
  const item = event.target.closest(".drop-item");
  if (!item) return;
  closeDropdowns();
  if (item.dataset.panel) window.oidaOpenPanel(item.dataset.panel);
  else if (item.dataset.shell) shellAction(item.dataset.shell);
});

/* ─────────────────────────────── SSE ────────────────────────────── */

function connectStream() {
  const stream = new EventSource("/events/stream");
  stream.onerror = () => {
    // EventSource retries transient drops itself, but gives up for good on an
    // HTTP error response; recreate it so a daemon restart resyncs the page.
    if (stream.readyState === EventSource.CLOSED) {
      setTimeout(connectStream, 5000);
    }
  };
  stream.onmessage = (message) => {
    let payload = null;
    try { payload = JSON.parse(message.data); } catch (_) { return; }
    const data = payload.data || {};
    switch (payload.type) {
      case "engine":
        renderEngine(data);
        break;
      case "capture_requested":
        break;
      case "capture_claimed":
        clearCaptureHint();
        if (localFlowOwnsUi()) break;
        setPhase("analyzing");
        setListenStatus(`Capturing ${Math.round(data.seconds || 10)} s of system audio…`, "active");
        break;
      case "capture_cancelled":
        clearCaptureHint();
        if (state.phase === "waiting") {
          setPhase("idle");
          setListenStatus("Capture cancelled.", "");
        }
        break;
      case "listen_started":
        clearCaptureHint();
        if (localFlowOwnsUi()) break;
        if (state.phase === "idle" || state.phase === "waiting") setPhase("analyzing");
        setListenStatus(`Listening (${data.route_preset || "basic"})…`, "active");
        break;
      case "listen_completed":
        clearCaptureHint();
        if (localFlowOwnsUi()) break;
        setPhase("idle");
        setListenStatus("Done.", "");
        // The local fetch path already rendered this event with its full response.
        if (data.listening_event && data.listening_event.id !== state.lastEventId) {
          renderEvent(data.listening_event, null);
        }
        refreshHistory();
        break;
      case "listen_failed":
        clearCaptureHint();
        if (localFlowOwnsUi()) break;
        setPhase("idle");
        setListenStatus(data.detail || "Listen failed.", "error");
        break;
      default:
        break;
    }
  };
}

/* ─────────────────────── presets + skill manager ─────────────────── */

// Tiny inline icons per listening mode / preset (stroke = currentColor).
const ICON = (path) =>
  `<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;

const MODE_ICONS = {
  basic: ICON('<path d="M4 12c1.8-5 3.6-5 5.4 0s3.6 5 5.4 0 3.4-5 5.2 0"/>'),
  spectral: ICON('<path d="M4 14v-4M8 18V6M12 15V9M16 19V5M20 13v-2"/>'),
  signal: ICON('<path d="M3 12h4l2-6 4 12 2-6h6"/>'),
  music: ICON('<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>'),
  speech: ICON('<path d="M20 12a8 8 0 1 0-14.9 4L4 20l4.2-1.1A8 8 0 0 0 20 12Z"/>'),
  soundscape: ICON('<path d="M3 16c3-6 6-6 9 0s6 6 9 0"/><path d="M3 10c3-6 6-6 9 0s6 6 9 0" opacity="0.45"/>'),
  ecological: ICON('<path d="M6 19C6 9 13 5 20 5c0 8-4 14-11 14"/><path d="M6 19c2-4 5-7 9-9"/>'),
  material: ICON('<path d="M12 3 4 7.5v9L12 21l8-4.5v-9L12 3Z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>'),
  comparative: ICON('<circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6"/>'),
  generative: ICON('<path d="M12 4v4M12 16v4M4 12h4M16 12h4M6.5 6.5l2.8 2.8M14.7 14.7l2.8 2.8M17.5 6.5l-2.8 2.8M9.3 14.7l-2.8 2.8"/>'),
  experimental: ICON('<path d="M10 3v6L4.5 19a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9V3"/><path d="M8 3h8"/>'),
};

const PRESET_ICONS = {
  basic: MODE_ICONS.basic,
  field: MODE_ICONS.ecological,
  signal: MODE_ICONS.signal,
  music: MODE_ICONS.music,
  voice: MODE_ICONS.speech,
  recall: ICON('<path d="M6 3h12v18l-6-4-6 4V3Z"/>'),
  remember: ICON('<path d="M6 3h12v18l-6-4-6 4V3Z"/><path d="M9 8h6M12 5v6"/>'),
  deep: ICON('<path d="M3 8c3-5 6-5 9 0s6 5 9 0"/><path d="M3 13c3-5 6-5 9 0s6 5 9 0" opacity="0.6"/><path d="M3 18c3-5 6-5 9 0s6 5 9 0" opacity="0.3"/>'),
  "extended-spectrum": MODE_ICONS.experimental,
  generative: MODE_ICONS.generative,
};

const PASS_LABELS = {
  transcribe: "transcript",
  events: "event timeline",
  caption: "caption",
  speech: "speech dimensions",
  music: "music analysis",
};

function presetTooltip(preset) {
  const passes = (preset.moss_passes || []).map((name) => PASS_LABELS[name] || name);
  const passLine = passes.length ? `Model passes: ${passes.join(", ")}.` : "DSP only — no model passes, instant.";
  return `${preset.description || ""}\n${passLine}\nSkills: ${(preset.skill_ids || []).join(", ")}`;
}

async function loadManifest() {
  try {
    const manifest = await fetchJson("/akouo/skills");
    state.presets = (manifest.route_presets || []).filter((preset) => preset.enabled_by_default !== false);
    state.skills = manifest.skills || [];
    if (!state.presets.some((preset) => preset.id === state.preset)) state.preset = state.presets[0]?.id || "basic";
    applyPresetSkills();
    renderPresets();
    renderSkills();
  } catch (_) {
    ui.modeMenu.innerHTML = `<span class="empty-note">Presets unavailable.</span>`;
  }
}

function applyPresetSkills() {
  const preset = state.presets.find((item) => item.id === state.preset);
  state.presetSkillIds = preset ? [...preset.skill_ids] : [];
  state.selectedSkills = new Set(state.presetSkillIds);
  updateSkillNote();
}

// The listening mode lives in a dropdown beside the Listen button.
function renderPresets() {
  ui.modeMenu.innerHTML = "";
  for (const preset of state.presets) {
    const item = document.createElement("button");
    item.className = `drop-item${preset.id === state.preset ? " active" : ""}`;
    item.setAttribute("role", "option");
    item.dataset.preset = preset.id;
    item.innerHTML = `${PRESET_ICONS[preset.id] || MODE_ICONS.basic}<span>${escapeHtml(preset.name)}</span>`;
    item.title = presetTooltip(preset);
    item.addEventListener("click", () => {
      state.preset = preset.id;
      applyPresetSkills();
      renderPresets();
      renderSkills();
      closeDropdowns();
    });
    ui.modeMenu.appendChild(item);
  }
  updateModeButton();
}

function updateModeButton() {
  const preset = state.presets.find((item) => item.id === state.preset);
  ui.modeIcon.innerHTML = PRESET_ICONS[state.preset] || MODE_ICONS.basic;
  ui.modeName.textContent = preset ? preset.name : (state.preset || "Basic");
}

/* ─────────────────────────── dropdowns ──────────────────────────── */

// One mechanism for every .dropdown (mode, export, configuration): open one,
// close the rest; click-outside and Escape close all.
function closeDropdowns() {
  document.querySelectorAll(".dropdown").forEach((dd) => {
    const menu = dd.querySelector(".drop-menu");
    if (menu) menu.hidden = true;
    dd.querySelector("[aria-haspopup]")?.setAttribute("aria-expanded", "false");
  });
}

document.querySelectorAll(".dropdown").forEach((dd) => {
  const button = dd.querySelector("[aria-haspopup]");
  const menu = dd.querySelector(".drop-menu");
  if (!button || !menu) return;
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    const willOpen = menu.hidden;
    closeDropdowns();
    if (willOpen) {
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
    }
  });
});
document.addEventListener("click", (event) => { if (!event.target.closest(".dropdown")) closeDropdowns(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDropdowns(); });

/* ─────────────────────────── sidebars ───────────────────────────── */

// Collapse (persisted) + drag-resize (persisted). Collapsing clears the inline
// width so the 44px rail class wins; expanding restores the stored width.
for (const [key, side, toggle, handle, dir] of [
  ["left", ui.sideLeft, ui.leftToggle, ui.leftResize, 1],
  ["right", ui.sideRight, ui.rightToggle, ui.rightResize, -1],
]) {
  const widthKey = `oida.side.${key}.width`;
  const storedWidth = localStorage.getItem(widthKey);
  if (localStorage.getItem(`oida.side.${key}`) === "collapsed") {
    side.classList.add("collapsed");
  } else if (storedWidth) {
    side.style.width = storedWidth;
  }

  toggle.addEventListener("click", () => {
    const collapsed = side.classList.toggle("collapsed");
    localStorage.setItem(`oida.side.${key}`, collapsed ? "collapsed" : "open");
    if (collapsed) {
      side.style.width = "";
    } else {
      const remembered = localStorage.getItem(widthKey);
      if (remembered) side.style.width = remembered;
    }
  });

  handle.addEventListener("pointerdown", (event) => {
    if (side.classList.contains("collapsed")) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    side.classList.add("dragging");
    const startX = event.clientX;
    const startWidth = side.getBoundingClientRect().width;
    const onMove = (moveEvent) => {
      const width = Math.min(440, Math.max(172, startWidth + dir * (moveEvent.clientX - startX)));
      side.style.width = `${width}px`;
    };
    const onUp = () => {
      side.classList.remove("dragging");
      if (side.style.width) localStorage.setItem(widthKey, side.style.width);
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
  });
}

function renderSkills() {
  ui.skillList.innerHTML = "";
  for (const skill of state.skills) {
    const label = document.createElement("label");
    label.className = "skill";
    label.title = skill.description || "";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedSkills.has(skill.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedSkills.add(skill.id);
      else state.selectedSkills.delete(skill.id);
      if (state.selectedSkills.size === 0) {
        state.selectedSkills.add(skill.id);
        checkbox.checked = true;
        setListenStatus("At least one skill stays enabled.", "error");
      }
      updateSkillNote();
    });
    const icon = document.createElement("span");
    icon.className = "skill-icon";
    icon.innerHTML = MODE_ICONS[skill.listening_mode] || MODE_ICONS.basic;
    const text = document.createElement("span");
    text.className = "skill-text";
    const name = document.createElement("span");
    name.className = "skill-name";
    name.textContent = skill.name;
    const desc = document.createElement("span");
    desc.className = "skill-desc";
    desc.textContent = skill.description || "";
    text.append(name, desc);
    const mode = document.createElement("span");
    mode.className = "skill-mode";
    mode.textContent = skill.listening_mode;
    label.append(checkbox, icon, text, mode);
    ui.skillList.appendChild(label);
  }
  updateSkillNote();
}

function updateSkillNote() {
  ui.skillNote.textContent = `${state.selectedSkills.size} on`;
}

function selectedSkillIds() {
  const ids = [...state.selectedSkills];
  const presetSet = new Set(state.presetSkillIds);
  const isDefault = ids.length === presetSet.size && ids.every((id) => presetSet.has(id));
  return isDefault ? null : ids;
}

/* ─────────────────────────── source panels ──────────────────────── */

// Arrow-key navigation for the radiogroup rows (sources, presets).
function radioKeyNav(container, selector) {
  if (!container) return;
  container.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const items = [...container.querySelectorAll(selector)];
    const index = items.indexOf(document.activeElement);
    if (index === -1 || !items.length) return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : items.length - 1;
    const next = items[(index + step) % items.length];
    next.focus();
    next.click();
  });
}

function markRadioSelection(buttons, isActive) {
  buttons.forEach((button) => {
    const active = isActive(button);
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
  });
}

document.querySelectorAll(".source").forEach((button) => {
  button.addEventListener("click", () => {
    if (state.phase !== "idle") return;
    markRadioSelection([...document.querySelectorAll(".source")], (other) => other === button);
    state.source = button.dataset.source;
    ui.systemPanel.hidden = state.source !== "system";
    ui.micPanel.hidden = state.source !== "mic";
    ui.filePanel.hidden = state.source !== "file";
    setListenStatus("", "");
    if (state.source === "mic" && !state.micDevices.length) refreshMicDevices(false);
  });
});
radioKeyNav(document.querySelector(".sources"), ".source");

/* mic devices + monitor */

async function refreshMicDevices(requestPermission) {
  try {
    if (requestPermission) {
      const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
      probe.getTracks().forEach((track) => track.stop());
    }
    const devices = await navigator.mediaDevices.enumerateDevices();
    state.micDevices = devices.filter((device) => device.kind === "audioinput");
    const current = ui.micDevice.value;
    ui.micDevice.innerHTML = "";
    ui.micDevice.appendChild(new Option("default input", "", false, !current));
    state.micDevices.forEach((device, index) => {
      const label = device.label || `input ${index + 1}`;
      ui.micDevice.appendChild(new Option(label, device.deviceId, false, Boolean(current) && device.deviceId === current));
    });
  } catch (error) {
    setListenStatus(`Inputs: ${error.message}`, "error");
  }
}

ui.micDevice.addEventListener("change", () => {
  if (state.monitor) {
    stopMonitor();
    startMonitor();
  }
});

async function startMonitor() {
  try {
    const constraints = { audio: ui.micDevice.value ? { deviceId: { exact: ui.micDevice.value } } : true };
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    const monitor = { stream, ctx, analyser, raf: 0, peak: 0 };
    const tick = () => {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      let peak = 0;
      for (let i = 0; i < samples.length; i += 1) {
        const value = Math.abs(samples[i]);
        sum += value * value;
        if (value > peak) peak = value;
      }
      const rms = Math.sqrt(sum / samples.length);
      const level = Math.min(1, rms * 3.2);
      monitor.peak = Math.max(peak, monitor.peak * 0.96);
      ui.micMeterFill.style.width = `${Math.round(level * 100)}%`;
      ui.micMeterPeak.style.left = `${Math.round(Math.min(1, monitor.peak) * 100)}%`;
      ui.micMeter.setAttribute("aria-valuenow", String(Math.round(level * 100)));
      monitor.raf = requestAnimationFrame(tick);
    };
    monitor.raf = requestAnimationFrame(tick);
    state.monitor = monitor;
    ui.micMonitor.setAttribute("aria-pressed", "true");
    ui.micMonitor.classList.add("on");
    if (!state.micDevices.length) refreshMicDevices(false);
  } catch (error) {
    setListenStatus(`Monitor: ${error.message}`, "error");
  }
}

function stopMonitor() {
  const monitor = state.monitor;
  if (!monitor) return;
  cancelAnimationFrame(monitor.raf);
  monitor.stream.getTracks().forEach((track) => track.stop());
  monitor.ctx.close().catch(() => {});
  state.monitor = null;
  ui.micMeterFill.style.width = "0%";
  ui.micMeterPeak.style.left = "0%";
  ui.micMonitor.setAttribute("aria-pressed", "false");
  ui.micMonitor.classList.remove("on");
}

ui.micMonitor.addEventListener("click", () => (state.monitor ? stopMonitor() : startMonitor()));

/* ──────────────────────────── listening ─────────────────────────── */

ui.listenButton.addEventListener("click", () => {
  if (state.phase !== "idle") return stopListening();
  if (state.source === "system") return listenSystem();
  if (state.source === "mic") return listenMic();
  ui.fileInput.click();
});

ui.browseFile.addEventListener("click", () => {
  if (state.phase !== "idle") return;
  ui.fileInput.click();
});

async function stopListening() {
  if (state.phase === "recording") {
    if (state.recorder && state.recorder.state === "recording") state.recorder.stop();
    return;
  }
  if (state.phase === "waiting") {
    clearCaptureHint();
    try {
      await post("/background/capture-request/cancel", state.captureRequestId ? { id: state.captureRequestId } : {});
    } catch (_) { /* already gone */ }
    state.captureRequestId = null;
    setPhase("idle");
    setListenStatus("Capture cancelled.", "");
    return;
  }
  if (state.phase === "analyzing") {
    state.abortController?.abort();
    state.abortController = null;
    setPhase("idle");
    setListenStatus("Stopped waiting — the daemon finishes in the background; the result lands in Recent.", "");
  }
}

async function listenSystem() {
  const seconds = Number(ui.captureSeconds.value) || 10;
  setPhase("waiting");
  setListenStatus("Asking the oída app to capture system audio…", "active");
  try {
    const response = await post("/background/capture-request", { seconds, route_preset: state.preset });
    state.captureRequestId = response.capture_request?.id || null;
    clearCaptureHint();
    // If nothing claims the request, check with the daemon before declaring
    // failure — the claim can happen while this page's SSE is down.
    state.captureHintTimer = setTimeout(async () => {
      state.captureHintTimer = null;
      if (state.phase !== "waiting") return;
      try {
        const status = await fetchJson("/background/status");
        if (!status.state?.capture_request) {
          // The request disappeared before its 30 s TTL, so the native shell
          // claimed it even if this page missed the SSE notification.
          setPhase("analyzing");
          setListenStatus(`Capturing ${Math.round(seconds)} s of system audio…`, "active");
          return;
        }
      } catch (_) { /* daemon unreachable; fall through to the hint */ }
      if (state.phase !== "waiting") return;
      setPhase("idle");
      setListenStatus("No capture yet — open the oída mac app (system audio is captured through it).", "error");
    }, 15000);
  } catch (error) {
    setPhase("idle");
    setListenStatus(error.message, "error");
  }
}

function clearCaptureHint() {
  if (state.captureHintTimer) {
    clearTimeout(state.captureHintTimer);
    state.captureHintTimer = null;
  }
}

function stopRecordTimer() {
  if (state.recordTimer) {
    clearInterval(state.recordTimer);
    state.recordTimer = null;
  }
}

async function listenMic() {
  let stream = null;
  try {
    const deviceId = ui.micDevice.value || null;
    const deviceLabel = ui.micDevice.selectedOptions[0]?.textContent || "default input";
    const constraints = { audio: ui.micDevice.value ? { deviceId: { exact: ui.micDevice.value } } : true };
    stream = await navigator.mediaDevices.getUserMedia(constraints);
    const recorder = new MediaRecorder(stream);
    state.recorder = recorder;
    state.recordedChunks = [];
    recorder.ondataavailable = (event) => { if (event.data.size) state.recordedChunks.push(event.data); };
    recorder.onstop = async () => {
      stopRecordTimer();
      stream.getTracks().forEach((track) => track.stop());
      const mime = recorder.mimeType || "audio/webm";
      const extension = mime.includes("mp4") ? "m4a" : mime.includes("ogg") ? "ogg" : "webm";
      const blob = new Blob(state.recordedChunks, { type: mime });
      state.recorder = null;
      setPhase("analyzing");
      setListenStatus("Uploading recording…", "active");
      try {
        await uploadAndAnalyze(
          blob,
          `oida-mic-${Date.now()}.${extension}`,
          "mic",
          `Microphone · ${deviceLabel}`,
          deviceId
        );
      } catch (error) {
        setPhase("idle");
        if (error.name !== "AbortError") setListenStatus(error.message, "error");
      }
    };
    recorder.start();
    setPhase("recording", "Stop");
    const startedAt = Date.now();
    setListenStatus("Recording… press Stop when done.", "active");
    stopRecordTimer();
    state.recordTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      const minutes = Math.floor(seconds / 60);
      setListenStatus(`Recording ${minutes}:${String(seconds % 60).padStart(2, "0")} — press Stop when done.`, "active");
    }, 1000);
  } catch (error) {
    // Without this, a failed recorder leaves the acquired mic hot forever.
    stream?.getTracks().forEach((track) => track.stop());
    state.recorder = null;
    stopRecordTimer();
    setListenStatus(`Microphone: ${error.message}`, "error");
  }
}

async function uploadBlob(blob, filename, signal) {
  const form = new FormData();
  form.append("file", blob, filename);
  return fetchJson("/upload", { method: "POST", body: form, signal });
}

async function uploadAndAnalyze(blob, filename, sourceKind, sourceLabel, deviceId) {
  const controller = new AbortController();
  state.abortController = controller;
  try {
    const upload = await uploadBlob(blob, filename, controller.signal);
    await analyzePath(upload.path, sourceKind, { controller, sourceLabel, deviceId });
  } finally {
    if (state.abortController === controller) state.abortController = null;
  }
}

ui.fileInput.addEventListener("change", async () => {
  const file = ui.fileInput.files?.[0];
  ui.fileInput.value = "";
  if (!file) return;
  if (state.phase !== "idle") return;
  setPhase("analyzing");
  setListenStatus(`Uploading ${file.name}…`, "active");
  try {
    await uploadAndAnalyze(file, file.name, "file");
  } catch (error) {
    setPhase("idle");
    if (error.name !== "AbortError") setListenStatus(error.message, "error");
  }
});

["dragover", "dragleave", "drop"].forEach((kind) => {
  ui.resultCard.addEventListener(kind, (event) => {
    event.preventDefault();
    ui.resultCard.classList.toggle("dragover", kind === "dragover");
    if (kind !== "drop") return;
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    if (state.phase !== "idle") {
      setListenStatus("Still busy with the current listen — drop it again in a moment.", "error");
      return;
    }
    setPhase("analyzing");
    setListenStatus(`Uploading ${file.name}…`, "active");
    uploadAndAnalyze(file, file.name, "file")
      .catch((error) => {
        setPhase("idle");
        if (error.name !== "AbortError") setListenStatus(error.message, "error");
      });
  });
});

ui.analyzePath.addEventListener("click", () => {
  if (state.phase !== "idle") return;
  const path = ui.audioPath.value.trim();
  if (!path) return setListenStatus("Enter an audio path first.", "error");
  setPhase("analyzing");
  analyzePath(path, "path").catch((error) => {
    setPhase("idle");
    if (error.name !== "AbortError") setListenStatus(error.message, "error");
  });
});

async function analyzePath(path, sourceKind, options = {}) {
  setListenStatus(`Listening (${state.preset})…`, "active");
  const body = { path, route_preset: state.preset };
  if (sourceKind === "mic") {
    body.source_type = "live_input";
    body.source_label = options.sourceLabel || "Microphone recording";
    body.privacy_mode = "ephemeral";
    body.raw_audio_policy = "temp";
    if (options.deviceId) body.device_id = options.deviceId;
  }
  const skills = selectedSkillIds();
  if (skills) body.enabled_skill_ids = skills;
  const controller = options.controller || new AbortController();
  state.abortController = controller;
  try {
    const result = await post("/listen-event", body, { signal: controller.signal });
    setPhase("idle");
    setListenStatus("Done.", "");
    renderEvent(result.listening_event, result);
    refreshHistory();
  } finally {
    if (state.abortController === controller) state.abortController = null;
  }
}

/* ───────────────────────────── rendering ────────────────────────── */

function renderEvent(event, fullResponse) {
  if (!event) return;
  state.lastEvent = event;
  state.lastEventId = event.id || null;
  state.lastJson = fullResponse || { listening_event: event };
  const aggregate = event.aggregate || {};

  ui.resultTitle.textContent = aggregate.title || "Listening event";
  ui.resultSummary.textContent = aggregate.short_summary || aggregate.detailed_summary || "";
  ui.resultSummary.classList.remove("placeholder");

  const tags = event.tags || [];
  ui.resultTags.innerHTML =
    tags.slice(0, 8).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("") +
    (tags.length > 8 ? `<span class="tag more">+${tags.length - 8}</span>` : "");

  renderBreakdown(buildResultGroups(event));
  ui.exportDrop.hidden = false;
  ui.jsonWrap.hidden = true;
  ui.germNote.hidden = true;
  ui.rememberItem.disabled = false;
  ui.rememberItem.textContent = event.memory?.saved_trace_id ? "Remembered" : "Remember";
  ui.wikiItem.disabled = !state.sonicfieldAvailable;
  ui.wikiItem.title = state.sonicfieldAvailable ? "" : "Sonic Field root not found";
  ui.soundItem.disabled = !segmentUri(event);
  ui.soundItem.title = segmentUri(event) ? "" : "This event keeps no audio reference, so germ cannot load it as sound";
}

// Group the event's claims into the tabbed sections shown under the reading.
function buildResultGroups(event) {
  const aggregate = event.aggregate || {};
  const claims = event.routes?.[0]?.structured?.claim_summary || {};
  const groups = [];

  const conf = (c) => `<span class="conf ${escapeHtml(c || "")}">${escapeHtml(c || "")}</span>`;
  const claimList = (items, withConf) =>
    `<div class="block"><ul>${items
      .map((it) => `<li>${withConf ? conf(it.confidence) : ""}<span>${escapeHtml(it.statement)}</span></li>`)
      .join("")}</ul></div>`;

  const summaryPrefix = (aggregate.short_summary || "").slice(0, 60);
  const hypotheses = (aggregate.hypotheses || []).filter(
    (h) => !summaryPrefix || !String(h.statement || "").startsWith(summaryPrefix)
  );
  if (hypotheses.length) groups.push({ key: "hypotheses", label: "Hypotheses", count: hypotheses.length, html: claimList(hypotheses.slice(0, 8), true) });

  const heard = (claims.heard || []).slice(0, 12);
  if (heard.length) groups.push({ key: "heard", label: "Heard", count: heard.length, html: claimList(heard, true) });

  const interpreted = (claims.interpreted || []).slice(0, 10);
  if (interpreted.length) groups.push({ key: "interpreted", label: "Interpreted", count: interpreted.length, html: claimList(interpreted, true) });

  const measured = claims.measured || [];
  if (measured.length) groups.push({ key: "measured", label: "Measured", count: measured.length, html: claimList(measured, false) });

  const undetermined = claims.undetermined || [];
  if (undetermined.length) groups.push({ key: "undetermined", label: "Undetermined", count: undetermined.length, html: claimList(undetermined, false) });

  const memoryMatches = event.memory?.similarity || [];
  if (memoryMatches.length) {
    const items = memoryMatches
      .slice(0, 6)
      .map((match) => {
        const trace = match.trace || {};
        const score = typeof match.score === "number" ? ` · ${Math.round(match.score * 100)}%` : "";
        return `<li><span>${escapeHtml(trace.title || trace.id || "trace")}${score}</span></li>`;
      })
      .join("");
    groups.push({ key: "memory", label: "Memory", count: memoryMatches.length, html: `<div class="block"><ul>${items}</ul></div>` });
  }
  return groups;
}

// The breakdown lives in the right rail as stacked sections, each collapsible
// and open by default.
function renderBreakdown(groups) {
  if (!groups.length) {
    ui.resultBody.innerHTML = `<p class="empty-note">No claims were produced.</p>`;
    return;
  }
  const caret = `<svg class="ci bd-caret" viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`;
  ui.resultBody.innerHTML = groups
    .map((g) => `<details class="bd-section" open><summary>${caret}${escapeHtml(g.label)}<span class="bd-count">${g.count}</span></summary>${g.html}</details>`)
    .join("");
}

/* ─────────────────────────── result actions ─────────────────────── */

async function rememberReading() {
  if (!state.lastEvent) return;
  ui.rememberItem.disabled = true;
  try {
    const result = await post("/memory/remember", { event: state.lastEvent, tags: [state.preset] });
    if (result.trace?.id) {
      state.lastEvent.memory = { ...(state.lastEvent.memory || {}), saved_trace_id: result.trace.id };
      ui.rememberItem.textContent = "Remembered";
    }
    refreshMemory();
  } catch (error) {
    setListenStatus(`Memory: ${error.message}`, "error");
    ui.rememberItem.disabled = false;
  }
}

ui.exportMenu.addEventListener("click", (event) => {
  const item = event.target.closest(".drop-item");
  if (!item || item.disabled) return;
  closeDropdowns();
  switch (item.dataset.action) {
    case "remember":
      rememberReading();
      break;
    case "wiki":
      if (!state.lastEvent) break;
      if (ui.wikiModal && typeof ui.wikiModal.showModal === "function" && !ui.wikiModal.open) ui.wikiModal.showModal();
      exploreWiki({ event: state.lastEvent });
      break;
    case "json":
      ui.jsonWrap.hidden = !ui.jsonWrap.hidden;
      if (!ui.jsonWrap.hidden) ui.jsonOutput.textContent = JSON.stringify(state.lastJson, null, 2);
      break;
    case "sound":
      germHandoff("sound");
      break;
    case "prompt":
      germHandoff("prompt");
      break;
  }
});

ui.jsonCopy.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(JSON.stringify(state.lastJson, null, 2));
    ui.jsonCopy.textContent = "copied";
    setTimeout(() => { ui.jsonCopy.textContent = "copy"; }, 1200);
  } catch (_) {
    ui.jsonCopy.textContent = "copy failed";
    setTimeout(() => { ui.jsonCopy.textContent = "copy"; }, 1800);
  }
});

/* ─────────────────────────── germ handoff ───────────────────────── */

// oída is generative ears; germ is generative voice. The germ dropdown's
// options persist the listen as an akousma in the shared store and deep-link germ.
function segmentUri(event) {
  const dataRef = event?.segment?.data_ref;
  if (!dataRef || !dataRef.uri) return null;
  return dataRef.kind === "path" ? `file://${dataRef.uri}` : String(dataRef.uri);
}

function setGermNote(text, tone) {
  ui.germNote.hidden = !text;
  ui.germNote.textContent = text || "";
  ui.germNote.className = `germ-note${tone ? ` ${tone}` : ""}`;
}

const GERM_ORIGINS = {
  live_input: "live-input",
  system_output: "system-output",
  buffer: "live-input",
  external_stream: "system-output",
  file: "file",
};

async function germHandoff(mode) {
  const event = state.lastEvent;
  if (!event) return;
  const segment = event.segment || {};
  const audio = { asset_id: segment.id || event.id };
  const uri = segmentUri(event);
  if (uri) audio.uri = uri;
  if (segment.duration_ms != null) audio.duration_seconds = segment.duration_ms / 1000;
  if (segment.sample_rate) audio.sample_rate = segment.sample_rate;
  if (segment.channels) audio.channels = segment.channels;
  const listening = {
    "oida.listen": {
      title: event.aggregate?.title || "",
      short_summary: event.aggregate?.short_summary || "",
      route_preset: state.preset,
    },
  };
  const structured = event.routes?.[0]?.structured;
  if (structured) listening["akouo.describe"] = structured;
  setGermNote(`Handing to germ (${mode})…`);
  try {
    const result = await post("/germ/handoff", {
      mode,
      audio,
      listening,
      origin: GERM_ORIGINS[event.source?.type] || "file",
      session_id: event.id,
      tags: (event.tags || []).slice(0, 8),
    });
    setGermNote(`akousma ${result.akousma_id} → germ`);
    window.open(result.germ_url, "_blank", "noopener");
  } catch (error) {
    const detail = String(error.message) === "404" ? "germ bridge unavailable (akousma package not installed)" : error.message;
    setGermNote(`germ: ${detail}`, "error");
  }
}

/* ─────────────────────────── wiki explore ───────────────────────── */

ui.wikiGo.addEventListener("click", () => exploreWiki({ query: ui.wikiQuery.value.trim() || null, event: state.lastEvent }));
ui.wikiQuery.addEventListener("keydown", (event) => {
  if (event.key === "Enter") exploreWiki({ query: ui.wikiQuery.value.trim() || null, event: state.lastEvent });
});

async function exploreWiki(body) {
  const token = ++state.wikiToken; // out-of-order responses must not paint stale results
  ui.wikiGroups.innerHTML = `<p class="wiki-empty">Searching the Sonic Field…</p>`;
  ui.wikiTerms.textContent = "";
  try {
    const result = await post("/sonicfield/explore", { ...body, limit_per_surface: 5 });
    if (token !== state.wikiToken) return;
    ui.wikiTerms.textContent = (result.query_terms || []).join(" · ");
    const groups = result.groups || {};
    const surfaces = Object.keys(groups);
    if (!surfaces.length) {
      ui.wikiGroups.innerHTML = `<p class="wiki-empty">No connections found. Try different terms.</p>`;
      return;
    }
    ui.wikiGroups.innerHTML = surfaces
      .map((surface) => {
        const rows = groups[surface]
          .map(
            (item) => `
            <button class="wiki-item" data-path="${escapeHtml(item.path)}" title="Reveal in Finder">
              <span class="wi-title">${escapeHtml(item.title)}</span>
              <span class="wi-excerpt">${escapeHtml(item.excerpt || item.summary || "")}</span>
              <span class="wi-route">${escapeHtml(item.route || "")}</span>
            </button>`
          )
          .join("");
        return `<div class="wiki-group"><h4>${escapeHtml(surface)}</h4>${rows}</div>`;
      })
      .join("");
  } catch (error) {
    if (token !== state.wikiToken) return;
    ui.wikiGroups.innerHTML = `<p class="wiki-empty">${escapeHtml(error.message)}</p>`;
  }
}

ui.wikiGroups.addEventListener("click", async (event) => {
  const item = event.target.closest(".wiki-item");
  if (!item) return;
  try {
    await post("/sonicfield/reveal", { path: item.dataset.path });
  } catch (error) {
    ui.wikiTerms.textContent = `reveal: ${error.message}`;
  }
});

/* ──────────────────────────── history ───────────────────────────── */

async function refreshHistory() {
  try {
    const history = await fetchJson("/background/history?limit=10");
    const recent = history.recent_events || [];
    ui.historyNote.textContent = recent.length ? `${recent.length}` : "";
    if (!recent.length) {
      ui.historyList.innerHTML = `<p class="empty-note">Nothing listened yet.</p>`;
      return;
    }
    ui.historyList.innerHTML = "";
    recent.slice(0, 10).forEach((event) => {
      const button = document.createElement("button");
      button.className = "row-item";
      const title = document.createElement("span");
      title.className = "ri-title";
      title.textContent = event.aggregate?.title || event.id || "event";
      const meta = document.createElement("span");
      meta.className = "ri-meta";
      meta.textContent = timeAgo(event.created_at);
      button.append(title, meta);
      button.addEventListener("click", () => renderEvent(event, null));
      ui.historyList.appendChild(button);
    });
    if (!state.lastEvent && recent[0]) renderEvent(recent[0], null);
  } catch (_) {
    ui.historyList.innerHTML = `<p class="empty-note">History unavailable.</p>`;
  }
}

/* ───────────────────────────── memory ───────────────────────────── */

async function refreshMemory(query) {
  try {
    const url = query ? `/memory?q=${encodeURIComponent(query)}` : "/memory";
    const result = await fetchJson(url);
    const traces = result.traces || [];
    if (!traces.length) {
      ui.memoryList.innerHTML = `<p class="empty-note">${query ? "No traces match." : "No saved traces yet. Use Remember on a result."}</p>`;
      return;
    }
    ui.memoryList.innerHTML = "";
    traces.slice(0, 12).forEach((trace) => {
      const row = document.createElement("div");
      row.className = "row-item static";
      const title = document.createElement("span");
      title.className = "ri-title";
      title.textContent = trace.title || trace.id;
      const meta = document.createElement("span");
      meta.className = "ri-meta";
      meta.textContent = (trace.tags || []).slice(0, 3).join(" ");
      const forget = document.createElement("button");
      forget.className = "ri-forget";
      forget.textContent = "forget";
      forget.addEventListener("click", async (event) => {
        event.stopPropagation();
        try {
          await post("/memory/forget", { trace_id: trace.id });
          refreshMemory(ui.memorySearch.value.trim() || undefined);
        } catch (error) {
          ui.memoryList.querySelector(".memory-error")?.remove();
          ui.memoryList.insertAdjacentHTML("afterbegin", `<p class="empty-note memory-error">${escapeHtml(error.message)}</p>`);
        }
      });
      row.append(title, meta, forget);
      ui.memoryList.appendChild(row);
    });
  } catch (_) {
    ui.memoryList.innerHTML = `<p class="empty-note">Memory unavailable.</p>`;
  }
}

ui.memoryGo.addEventListener("click", () => refreshMemory(ui.memorySearch.value.trim() || undefined));
ui.memorySearch.addEventListener("keydown", (event) => {
  if (event.key === "Enter") refreshMemory(ui.memorySearch.value.trim() || undefined);
});

/* ────────────────────────────── boot ────────────────────────────── */

refreshHealth().finally(() => {
  refreshHistory();
});
loadManifest();
refreshMemory();
refreshMicDevices(false); // load input devices by default, no permission prompt
connectStream();
setInterval(refreshHealth, 20000);
window.addEventListener("pagehide", () => {
  stopMonitor();
  stopRecordTimer();
  // A recording in flight must not leave the microphone hot after the page goes away.
  if (state.recorder) {
    state.recorder.stream?.getTracks().forEach((track) => track.stop());
    state.recorder = null;
  }
});
