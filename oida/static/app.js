/* oida dashboard — one listen surface, one daemon, every surface in sync.
   Vanilla JS. State lives in the daemon; this client renders it. */

"use strict";

const state = {
  source: "system",
  direction: "past",
  preset: "basic",
  customMode: false,
  musicIDEnabled: localStorage.getItem("oida.music-id") === "on",
  theme: localStorage.getItem("oida.theme") === "dark" ? "dark" : "light",
  tagFilters: new Set(),
  presets: [],
  skills: [],
  selectedSkills: new Set(),
  presetSkillIds: [],
  lastEvent: null,
  lastEventId: null,
  lastJson: null,
  sessions: [],
  archivedSessions: [],
  currentSessionId: null,
  currentSessionName: "",
  audioDir: "",
  engine: null,
  engineSignature: null,
  phase: "idle", // idle | waiting/capturing | processing/analyzing | result | failed
  recorder: null,
  recordedChunks: [],
  recordTimer: null,
  recordStopTimer: null,
  captureHintTimer: null,
  captureRequestId: null,
  abortController: null,
  wikiToken: 0,
  sonicfieldAvailable: false,
  micDevices: [],
  monitor: null, // {stream, ctx, analyser, raf, peak}
  diagnostics: [],
  nativeEventId: null,
  historyRequestSerial: 0,
  activeSpectrogram: null,
  mobileRemote: null,
  reasoningSettings: null,
  reasoningProviders: [],
  reasoningModels: new Map(),
  reasoningModelRequests: new Map(),
  reasoningModelLibraryBusy: false,
  reasoningLoaded: false,
  reasoningBusy: false,
  conversationEvent: null,
  conversationId: null,
  conversationTurns: [],
  conversationByEvent: new Map(),
  conversationBusy: false,
  conversationAbort: null,
  conversationDraftAnswer: "",
};

const el = (id) => document.getElementById(id);
const ui = {
  resultCard: el("resultCard"),
  resultScroll: el("resultScroll"),
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
  sessionTitle: el("sessionTitle"),
  resultEntries: el("resultEntries"),
  resultBody: el("resultBody"),
  leftResize: el("leftResize"),
  rightResize: el("rightResize"),
  musicIdToggle: el("musicIdToggle"),
  configButton: el("configButton"),
  sourceModal: el("sourceModal"),
  sourceModalTitle: el("sourceModalTitle"),
  settingsModal: el("settingsModal"),
  themeLight: el("themeLight"),
  themeDark: el("themeDark"),
  resetInterface: el("resetInterface"),
  mobileRemoteUrl: el("mobileRemoteUrl"),
  mobileRemoteNote: el("mobileRemoteNote"),
  mobileRemoteEnable: el("mobileRemoteEnable"),
  mobileRemoteCopy: el("mobileRemoteCopy"),
  mobileRemoteOpen: el("mobileRemoteOpen"),
  reasoningStatus: el("reasoningStatus"),
  reasoningResources: el("reasoningResources"),
  reasoningRefresh: el("reasoningRefresh"),
  reasoningProviders: el("reasoningProviders"),
  reasoningModelFilter: el("reasoningModelFilter"),
  reasoningModelSummary: el("reasoningModelSummary"),
  reasoningModelLibrary: el("reasoningModelLibrary"),
  reasoningRoles: el("reasoningRoles"),
  reasoningProfileSelect: el("reasoningProfileSelect"),
  reasoningProfileAdd: el("reasoningProfileAdd"),
  reasoningProfileRemove: el("reasoningProfileRemove"),
  reasoningProfileName: el("reasoningProfileName"),
  reasoningTone: el("reasoningTone"),
  reasoningDepth: el("reasoningDepth"),
  reasoningInitiative: el("reasoningInitiative"),
  reasoningLanguage: el("reasoningLanguage"),
  reasoningFocus: el("reasoningFocus"),
  reasoningInstructions: el("reasoningInstructions"),
  reasoningInstructionCount: el("reasoningInstructionCount"),
  reasoningTranscript: el("reasoningTranscript"),
  reasoningMemory: el("reasoningMemory"),
  reasoningRelisten: el("reasoningRelisten"),
  reasoningExternalAudio: el("reasoningExternalAudio"),
  reasoningSave: el("reasoningSave"),
  reasoningSaveNote: el("reasoningSaveNote"),
  conversationPanel: el("conversationPanel"),
  conversationClose: el("conversationClose"),
  conversationTitle: el("conversationTitle"),
  conversationAnchor: el("conversationAnchor"),
  conversationProvider: el("conversationProvider"),
  conversationModel: el("conversationModel"),
  conversationProfile: el("conversationProfile"),
  conversationLocality: el("conversationLocality"),
  conversationContextSummary: el("conversationContextSummary"),
  conversationCompareList: el("conversationCompareList"),
  conversationTranscript: el("conversationTranscript"),
  conversationMemory: el("conversationMemory"),
  conversationRelisten: el("conversationRelisten"),
  conversationTurns: el("conversationTurns"),
  conversationForm: el("conversationForm"),
  conversationQuestion: el("conversationQuestion"),
  conversationSend: el("conversationSend"),
  conversationStatus: el("conversationStatus"),
  sessionContextRow: el("sessionContextRow"),
  tagFilterBar: el("tagFilterBar"),
  tagFilterChips: el("tagFilterChips"),
  tagFilterAdd: el("tagFilterAdd"),
  tagFilterMenu: el("tagFilterMenu"),
  tagFilterReset: el("tagFilterReset"),
  germNote: el("germNote"),
  wikiModal: el("wikiModal"),
  wikiQuery: el("wikiQuery"),
  wikiGo: el("wikiGo"),
  wikiTerms: el("wikiTerms"),
  wikiGroups: el("wikiGroups"),
  historyList: el("historyList"),
  archiveSection: el("archiveSection"),
  archiveList: el("archiveList"),
  newSession: el("newSession"),
  memorySearch: el("memorySearch"),
  memoryGo: el("memoryGo"),
  memoryList: el("memoryList"),
  listenProgress: el("listenProgress"),
  listenProgressFill: el("listenProgressFill"),
  listenPhaseText: el("listenPhaseText"),
  consoleOutput: el("consoleOutput"),
  consoleNote: el("consoleNote"),
  consoleCopy: el("consoleCopy"),
  consoleClear: el("consoleClear"),
  engineAddress: el("engineAddress"),
  engineAudioDir: el("engineAudioDir"),
  sonogramModal: el("sonogramModal"),
  sonogramModalCanvas: el("sonogramModalCanvas"),
  sonogramModalMin: el("sonogramModalMin"),
  sonogramModalDuration: el("sonogramModalDuration"),
  sonogramModalMax: el("sonogramModalMax"),
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
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    logActivity(`${options?.method || "GET"} ${url} — ${error.message}`, "error");
    throw error;
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      if (body && body.detail) {
        // FastAPI validation errors ship detail as a list of objects.
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch (_) { /* keep status */ }
    const error = new Error(detail);
    logActivity(`${options?.method || "GET"} ${url} — ${response.status} ${detail}`, "error");
    throw error;
  }
  const result = await response.json();
  if (options?.method && options.method !== "GET") logActivity(`${options.method} ${url} — ${response.status}`);
  return result;
}

const post = (url, body, options) =>
  fetchJson(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}), ...(options || {}) });

function setListenStatus(text, tone) {
  ui.listenStatus.textContent = text;
  ui.listenStatus.className = `listen-status${tone ? ` ${tone}` : ""}`;
  if (tone === "error" && text) logActivity(text, "error");
}

function setPhase(phase, label) {
  const previous = state.phase;
  state.phase = phase;
  const capturing = ["waiting", "recording", "capturing"].includes(phase);
  const processing = ["analyzing", "processing"].includes(phase);
  const busy = capturing || processing;
  ui.listenButton.classList.toggle("busy", processing || phase === "waiting");
  ui.listenButton.classList.toggle("stop", capturing);
  ui.listenButton.classList.toggle("processing", processing);
  ui.listenButton.dataset.phase = capturing ? "hearing" : (processing ? "operating" : phase);
  ui.listenButton.disabled = processing;
  ui.listenLabel.textContent = capturing
    ? "Hearing — press to stop"
    : (processing ? "Operating listening" : (label || "Listen"));
  ui.listenButton.title = capturing ? "Stop hearing" : (processing ? "Operating listening" : "Listen");
  ui.listenProgress.hidden = !busy;
  ui.listenProgress.classList.toggle("hearing", capturing);
  ui.listenProgress.classList.toggle("processing", processing);
  if (!busy) ui.listenProgressFill.style.width = "0%";
  if (previous !== phase) logActivity(`Phase: ${previous} → ${phase}`);
}

function updateListenProgress(progress, status) {
  const value = Math.max(0, Math.min(1, Number(progress) || 0));
  ui.listenProgressFill.style.width = `${Math.round(value * 100)}%`;
  if (status) ui.listenPhaseText.textContent = status;
}

function logActivity(message, tone = "info") {
  if (!message) return;
  const stamp = new Date().toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  state.diagnostics.push({ stamp, message: String(message), tone });
  if (state.diagnostics.length > 160) state.diagnostics.splice(0, state.diagnostics.length - 160);
  if (ui.consoleOutput) {
    ui.consoleOutput.textContent = state.diagnostics.map((entry) => `[${entry.stamp}] ${entry.tone === "error" ? "ERROR " : ""}${entry.message}`).join("\n");
    ui.consoleOutput.scrollTop = ui.consoleOutput.scrollHeight;
  }
  if (ui.consoleNote) {
    const errors = state.diagnostics.filter((entry) => entry.tone === "error").length;
    ui.consoleNote.textContent = errors ? `${errors} error${errors === 1 ? "" : "s"}` : "";
  }
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (_) { /* fall through for restricted WebKit/browser contexts */ }
  }
  const helper = document.createElement("textarea");
  helper.value = text;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.left = "-9999px";
  document.body.appendChild(helper);
  helper.select();
  const copied = document.execCommand("copy");
  helper.remove();
  if (!copied) throw new Error("clipboard access unavailable");
}

function downloadJson(value, filename) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fileSlug(value, fallback = "listening") {
  const slug = String(value || fallback)
    .normalize("NFKD")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  return slug || fallback;
}

function musicResultLead(event) {
  const musicID = event?.music_id;
  if (!musicID?.matched) return "";
  const title = String(musicID.title || "Identified song").trim();
  const artist = String(musicID.artist || "Unknown artist").trim();
  return `${title} by ${artist}.`;
}

function resultSummaryText(event) {
  const aggregate = event?.aggregate || {};
  return [musicResultLead(event), aggregate.short_summary || aggregate.detailed_summary || ""]
    .filter(Boolean)
    .join(" ");
}

function listeningResultText(event) {
  const aggregate = event?.aggregate || {};
  const claims = event?.routes?.[0]?.structured?.claim_summary || {};
  const lines = [
    aggregate.title || "Listening result",
    resultSummaryText(event),
  ];
  if ((event?.tags || []).length) lines.push(`Tags: ${event.tags.join(", ")}`);
  for (const [label, items] of Object.entries(claims)) {
    const statements = (items || []).map((item) => item?.statement).filter(Boolean);
    if (statements.length) lines.push(`\n${label[0].toUpperCase()}${label.slice(1)}\n${statements.map((statement) => `• ${statement}`).join("\n")}`);
  }
  return lines.filter(Boolean).join("\n");
}

ui.consoleCopy.addEventListener("click", async () => {
  try {
    await copyText(ui.consoleOutput.textContent || "");
    ui.consoleCopy.classList.add("copied");
    ui.consoleCopy.title = "Copied";
  } catch (_) {
    ui.consoleCopy.title = "Copy failed";
  }
  setTimeout(() => {
    ui.consoleCopy.classList.remove("copied");
    ui.consoleCopy.title = "Copy console";
  }, 1200);
});
ui.consoleClear.addEventListener("click", () => {
  state.diagnostics = [];
  ui.consoleOutput.textContent = "";
  ui.consoleNote.textContent = "";
});

const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

// A mic recording is a purely local flow; daemon events from other surfaces
// must not steal its phase. Same while a local /listen-event fetch owns the UI.
function localFlowOwnsUi() {
  return Boolean(state.recorder) || Boolean(state.abortController);
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
    renderEngine(health.engine);
  } catch (_) {
    ui.engineAddress.textContent = "offline";
  }
}

function renderMobileRemote(remote) {
  state.mobileRemote = remote || null;
  const url = remote?.remote_ear_url || "";
  if (ui.mobileRemoteUrl) ui.mobileRemoteUrl.value = url;
  if (ui.mobileRemoteCopy) ui.mobileRemoteCopy.disabled = !url;
  if (ui.mobileRemoteOpen) ui.mobileRemoteOpen.disabled = !url;
  if (ui.mobileRemoteEnable) {
    ui.mobileRemoteEnable.textContent = remote?.microphone_ready ? "Refresh" : "Enable";
    ui.mobileRemoteEnable.disabled = false;
  }
  if (ui.mobileRemoteNote) {
    const prefix = remote?.microphone_ready ? "Ready for the phone microphone." : "Not enabled.";
    const instruction = remote?.microphone_ready
      ? "Open the URL on a phone connected to the same private-network network, then allow microphone access."
      : "";
    ui.mobileRemoteNote.textContent = `${prefix} ${remote?.detail || "private-network HTTPS is required for mobile microphone access."} ${instruction}`.trim();
    ui.mobileRemoteNote.classList.toggle("ready", Boolean(remote?.microphone_ready));
  }
}

async function refreshMobileRemote() {
  try {
    renderMobileRemote(await fetchJson("/remote/status"));
  } catch (error) {
    renderMobileRemote({ detail: `Phone remote unavailable: ${error.message}` });
  }
}

ui.mobileRemoteEnable?.addEventListener("click", async () => {
  ui.mobileRemoteEnable.disabled = true;
  ui.mobileRemoteEnable.textContent = "Enabling…";
  if (ui.mobileRemoteNote) ui.mobileRemoteNote.textContent = "Creating the private HTTPS microphone URL…";
  try {
    const remote = await post("/remote/configure");
    renderMobileRemote(remote);
    setListenStatus(remote.microphone_ready ? "Phone microphone remote ready." : (remote.detail || "Phone remote needs attention."), remote.microphone_ready ? "" : "error");
  } catch (error) {
    renderMobileRemote({ detail: `Phone remote: ${error.message}` });
    setListenStatus(`Phone remote: ${error.message}`, "error");
  }
});

ui.mobileRemoteCopy?.addEventListener("click", async () => {
  const url = ui.mobileRemoteUrl?.value || "";
  if (!url) return;
  try {
    await copyText(url);
    ui.mobileRemoteCopy.textContent = "Copied";
    setTimeout(() => { if (ui.mobileRemoteCopy) ui.mobileRemoteCopy.textContent = "Copy URL"; }, 1200);
  } catch (error) {
    setListenStatus(`Copy remote URL: ${error.message}`, "error");
  }
});

ui.mobileRemoteOpen?.addEventListener("click", () => {
  const url = ui.mobileRemoteUrl?.value || "";
  if (url) window.open(url, "_blank", "noopener");
});

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

/* ─────────────────────── settings / skill / source modals ───────────── */

const PANEL_DIALOGS = { skill: "skillModal", settings: "settingsModal" };

// Callable from the native shell and the rail icons; renders each panel as a
// modal dialog. Modals are exclusive: opening one closes whatever is open.
window.oidaOpenPanel = (name) => {
  const target = document.getElementById(PANEL_DIALOGS[name]);
  if (!target || typeof target.showModal !== "function") return;
  document.querySelectorAll("dialog[open]").forEach((other) => { if (other !== target) other.close(); });
  if (!target.open) target.showModal();
  if (name === "settings") {
    refreshMobileRemote();
    refreshReasoning();
  }
};

document.querySelectorAll(".modal").forEach((dialog) => {
  dialog.querySelector("[data-close]")?.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
});
ui.sonogramModal?.addEventListener("close", () => { state.activeSpectrogram = null; });
window.addEventListener("resize", () => {
  if (ui.sonogramModal?.open && state.activeSpectrogram) {
    requestAnimationFrame(() => drawSpectrogram(state.activeSpectrogram, ui.sonogramModalCanvas));
  }
});

// The native shell injects __oidaNative; that reveals the shell-only actions
// (floating listener, open-in-browser), which post into the app.
if (window.__oidaNative) document.body.classList.add("native");

function shellAction(message) {
  const action = typeof message === "string" ? message : message?.action;
  if (action === "reload" && !window.webkit?.messageHandlers?.oidaShell) {
    window.location.reload();
    return;
  }
  window.webkit?.messageHandlers?.oidaShell?.postMessage(message);
}

function applyTheme(theme, notifyNative = true) {
  const normalized = theme === "dark" ? "dark" : "light";
  state.theme = normalized;
  document.documentElement.dataset.theme = normalized;
  localStorage.setItem("oida.theme", normalized);
  const themeColor = document.querySelector('meta[name="theme-color"]');
  if (themeColor) themeColor.content = normalized === "dark" ? "#1b1b19" : "#f6f6f4";
  for (const button of [ui.themeLight, ui.themeDark].filter(Boolean)) {
    const selected = button.dataset.themeChoice === normalized;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-checked", selected ? "true" : "false");
  }
  if (state.lastEvent) {
    requestAnimationFrame(() => {
      drawSpectrogram(state.lastEvent.features?.spectrogram);
      if (ui.sonogramModal?.open && state.activeSpectrogram) {
        drawSpectrogram(state.activeSpectrogram, ui.sonogramModalCanvas);
      }
    });
  }
  if (notifyNative && window.__oidaNative) shellAction(nativeSelectionMessage("sync"));
}

document.querySelectorAll("[data-theme-choice]").forEach((button) => {
  button.addEventListener("click", () => applyTheme(button.dataset.themeChoice));
});
applyTheme(state.theme, false);

ui.configButton?.addEventListener("click", () => window.oidaOpenPanel("settings"));
ui.resetInterface?.addEventListener("click", () => {
  for (const key of ["oida.side.left", "oida.side.right", "oida.side.left.width", "oida.side.right.width"]) {
    localStorage.removeItem(key);
  }
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  window.location.reload();
});

function nativeSelectionMessage(action = "sync") {
  return {
    action,
    source: state.source,
    preset: state.preset,
    direction: state.direction,
    seconds: Number(ui.captureSeconds.value) || 10,
    custom: state.customMode,
    skills: state.customMode ? [...state.selectedSkills] : null,
    musicId: state.musicIDEnabled,
    appearance: state.theme,
    sessionName: state.currentSessionName || null,
  };
}

window.oidaNativeState = (nativeState) => {
  if (!nativeState || typeof nativeState !== "object") return;
  if (nativeState.source) applySourceSelection(nativeState.source, false);
  if (nativeState.direction) applyDirectionSelection(nativeState.direction, false);
  if (nativeState.preset && nativeState.preset !== state.preset) {
    state.preset = nativeState.preset;
    state.customMode = false;
    applyPresetSkills();
  }
  if (nativeState.customMode && Array.isArray(nativeState.selectedSkillIDs)) {
    state.customMode = true;
    state.selectedSkills = new Set(nativeState.selectedSkillIDs);
  } else if (nativeState.customMode === false && state.customMode) {
    state.customMode = false;
    applyPresetSkills();
  }
  if (typeof nativeState.musicIDEnabled === "boolean") {
    state.musicIDEnabled = nativeState.musicIDEnabled;
    localStorage.setItem("oida.music-id", state.musicIDEnabled ? "on" : "off");
  }
  if (nativeState.appearance) applyTheme(nativeState.appearance, false);
  renderPresets();
  renderSkills();
  if (Number(nativeState.captureSeconds) > 0) {
    const value = String(Number(nativeState.captureSeconds));
    if (![...ui.captureSeconds.options].some((option) => option.value === value)) {
      ui.captureSeconds.appendChild(new Option(`${value}sec`, value));
    }
    ui.captureSeconds.value = value;
  }
  // Selections above always mirror the shell; the phase/status surface only
  // follows it while no in-page flow (mic recording, path/upload analyze)
  // owns the UI — same rule the SSE handlers apply.
  if (!localFlowOwnsUi()) {
    setPhase(nativeState.phase || "idle");
    const remaining = Number(nativeState.secondsRemaining);
    const status = nativeState.phase === "capturing" && Number.isFinite(remaining)
      ? `Hearing · ${Math.max(0, Math.ceil(remaining))}s`
      : (nativeState.status || "");
    if (status) {
      setListenStatus(status, ["capturing", "processing"].includes(nativeState.phase) ? "active" : "");
      updateListenProgress(nativeState.progress, status);
    }
    if (nativeState.error) setListenStatus(nativeState.error, "error");
  }
  (nativeState.logs || []).forEach((line) => {
    if (!state.diagnostics.some((entry) => entry.message === line)) logActivity(line);
  });
  if (nativeState.eventId && nativeState.eventId !== state.nativeEventId) {
    state.nativeEventId = nativeState.eventId;
    refreshHistory({ selectLatest: true });
  }
};

// the floating-listener corner button (drop-menu items are delegated below)
document.querySelectorAll("button[data-shell]:not(.drop-item)").forEach((button) =>
  button.addEventListener("click", () => shellAction(button.dataset.shell))
);

/* ─────────────────────────────── SSE ────────────────────────────── */

function connectStream() {
  const stream = new EventSource("/events/stream");
  stream.onerror = () => {
    // EventSource retries transient drops itself, but gives up for good on an
    // HTTP error response; recreate it so a daemon restart resyncs the page.
    if (stream.readyState === EventSource.CLOSED) {
      logActivity("Event stream disconnected; reconnecting", "error");
      setTimeout(connectStream, 5000);
    }
  };
  stream.onmessage = (message) => {
    let payload = null;
    try { payload = JSON.parse(message.data); } catch (_) { return; }
    const data = payload.data || {};
    if (payload.type !== "engine") logActivity(`Event: ${payload.type}`);
    switch (payload.type) {
      case "engine":
        renderEngine(data);
        break;
      case "capture_requested":
        break;
      case "capture_claimed":
        clearCaptureHint();
        if (localFlowOwnsUi()) break;
        setPhase("capturing");
        state.direction = data.direction || state.direction;
        setListenStatus(`Hearing ${data.direction === "future" ? "forward" : "from the buffer"} · ${Math.round(data.seconds || 10)}s`, "active");
        updateListenProgress(0, `Hearing · ${Math.round(data.seconds || 10)}s`);
        break;
      case "capture_cancelled":
        clearCaptureHint();
        if (["waiting", "capturing"].includes(state.phase)) {
          setPhase("idle");
          setListenStatus("Capture cancelled.", "");
        }
        break;
      case "listen_started":
        clearCaptureHint();
        if (localFlowOwnsUi()) break;
        setPhase("processing");
        setListenStatus("Operating listening…", "active");
        updateListenProgress(1, "Operating listening…");
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
        refreshHistory({ eventId: data.listening_event?.id || state.lastEventId });
        break;
      case "listen_failed":
        clearCaptureHint();
        if (localFlowOwnsUi()) break;
        setPhase("idle");
        setListenStatus(data.detail || "Listen failed.", "error");
        break;
      case "session_changed":
        refreshHistory({
          selectSessionId: data.session_id || data.session?.id || state.currentSessionId,
          eventId: data.listening_event?.id || state.lastEventId,
        });
        break;
      case "covenant_changed":
        refreshCovenant();
        break;
      default:
        break;
    }
  };
}

/* ─────────────────────── presets + skill manager ─────────────────── */

// Tiny inline icons per listening mode / preset (stroke = currentColor).
const ICON = (path) =>
  `<svg class="ci mode-glyph" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">${path}</svg>`;

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
  custom: ICON('<path d="M5 8h14M5 16h14"/><circle cx="9" cy="8" r="2"/><circle cx="15" cy="16" r="2"/>'),
};

const VISIBLE_MODE_IDS = ["basic", "field", "signal", "music", "voice", "deep"];

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
    const byId = new Map((manifest.route_presets || []).map((preset) => [preset.id, preset]));
    state.presets = VISIBLE_MODE_IDS.map((id) => byId.get(id)).filter(Boolean);
    state.skills = manifest.skills || [];
    if (!state.presets.some((preset) => preset.id === state.preset)) state.preset = state.presets[0]?.id || "basic";
    if (!state.customMode || !state.selectedSkills.size) applyPresetSkills();
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
    item.className = `drop-item${!state.customMode && preset.id === state.preset ? " active" : ""}`;
    item.setAttribute("role", "option");
    item.dataset.preset = preset.id;
    item.innerHTML = `${PRESET_ICONS[preset.id] || MODE_ICONS.basic}<span>${escapeHtml(preset.name)}</span>`;
    item.title = presetTooltip(preset);
    item.addEventListener("click", () => {
      state.preset = preset.id;
      state.customMode = false;
      applyPresetSkills();
      renderPresets();
      renderSkills();
      closeDropdowns();
      if (window.__oidaNative) shellAction(nativeSelectionMessage("sync"));
    });
    ui.modeMenu.appendChild(item);
  }
  const custom = document.createElement("button");
  custom.className = `drop-item custom-mode${state.customMode ? " active" : ""}`;
  custom.setAttribute("role", "option");
  custom.innerHTML = `${PRESET_ICONS.custom}<span>Custom</span>`;
  custom.title = "Choose an individual set of listening skills.";
  custom.addEventListener("click", () => {
    if (!state.customMode) {
      state.preset = "deep";
      applyPresetSkills();
    }
    state.customMode = true;
    renderPresets();
    renderSkills();
    closeDropdowns();
    updateModeButton();
    if (window.__oidaNative) shellAction(nativeSelectionMessage("sync"));
    window.oidaOpenPanel("skill");
  });
  ui.modeMenu.appendChild(custom);
  updateModeButton();
}

function updateModeButton() {
  const preset = state.presets.find((item) => item.id === state.preset);
  ui.modeIcon.innerHTML = state.customMode ? PRESET_ICONS.custom : (PRESET_ICONS[state.preset] || MODE_ICONS.basic);
  ui.modeName.textContent = state.customMode ? "Custom" : (preset ? preset.name : (state.preset === "basic" ? "General" : state.preset || "General"));
  const showMusicID = state.preset === "music" && !state.customMode;
  ui.musicIdToggle.hidden = !showMusicID;
  ui.musicIdToggle.classList.toggle("on", state.musicIDEnabled);
  ui.musicIdToggle.setAttribute("aria-pressed", state.musicIDEnabled ? "true" : "false");
  ui.musicIdToggle.title = state.musicIDEnabled ? "Music ID on — disable ShazamIO recognition" : "Music ID off — enable ShazamIO recognition";
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

function wireDropdown(dd) {
  if (!dd || dd.dataset.dropdownWired === "true") return;
  const button = dd.querySelector("[aria-haspopup]");
  const menu = dd.querySelector(".drop-menu");
  if (!button || !menu) return;
  dd.dataset.dropdownWired = "true";
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const willOpen = menu.hidden;
    closeDropdowns();
    if (willOpen) {
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
    }
  });
}
document.querySelectorAll(".dropdown").forEach(wireDropdown);
document.addEventListener("click", (event) => { if (!event.target.closest(".dropdown")) closeDropdowns(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDropdowns(); });

/* ─────────────────────────── sidebars ───────────────────────────── */

// Collapse (persisted) + drag-resize (persisted). Collapsing clears the inline
// width so the rail leaves the grid entirely; expanding restores its width.
function setSidebarCollapsed(key, side, collapsed) {
  const widthKey = `oida.side.${key}.width`;
  const currentWidth = side.getBoundingClientRect().width;
  if (!side.classList.contains("collapsed") && currentWidth > 80) {
    const width = `${Math.round(currentWidth)}px`;
    localStorage.setItem(widthKey, width);
    side.style.setProperty("--side-open-width", width);
  }
  side.classList.toggle("collapsed", collapsed);
  localStorage.setItem(`oida.side.${key}`, collapsed ? "collapsed" : "open");
  if (collapsed) {
    side.style.width = "";
  } else {
    const remembered = localStorage.getItem(widthKey);
    if (remembered) {
      side.style.width = remembered;
      side.style.setProperty("--side-open-width", remembered);
    }
  }
  return collapsed;
}

const sidebarDefinitions = [
  ["left", ui.sideLeft, ui.leftToggle, ui.leftResize, 1],
  ["right", ui.sideRight, ui.rightToggle, ui.rightResize, -1],
];

window.oidaToggleSidebar = (key) => {
  const definition = sidebarDefinitions.find(([name]) => name === key);
  if (!definition) return false;
  const side = definition[1];
  return setSidebarCollapsed(key, side, !side.classList.contains("collapsed"));
};

for (const [key, side, toggle, handle, dir] of sidebarDefinitions) {
  const widthKey = `oida.side.${key}.width`;
  const storedWidth = localStorage.getItem(widthKey);
  side.style.setProperty("--side-open-width", storedWidth || "232px");
  if (localStorage.getItem(`oida.side.${key}`) === "collapsed") {
    side.classList.add("collapsed");
  } else if (storedWidth) {
    side.style.width = storedWidth;
  }

  toggle?.addEventListener("click", () => {
    const collapsed = window.oidaToggleSidebar(key);
    // The browser fallback control lives inside its rail. Drop pointer focus
    // after collapsing so :focus-within does not hold the hover overlay open.
    if (collapsed) toggle.blur();
  });

  handle.addEventListener("pointerdown", (event) => {
    if (side.classList.contains("collapsed")) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    side.classList.add("dragging");
    const startX = event.clientX;
    const startWidth = side.getBoundingClientRect().width;
    const onMove = (moveEvent) => {
      const width = Math.min(560, Math.max(172, startWidth + dir * (moveEvent.clientX - startX)));
      side.style.width = `${width}px`;
      side.style.setProperty("--side-open-width", `${width}px`);
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
      state.customMode = true;
      updateSkillNote();
      renderPresets();
      updateModeButton();
      if (window.__oidaNative) shellAction(nativeSelectionMessage("sync"));
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
  if (state.customMode) return ids;
  const presetSet = new Set(state.presetSkillIds);
  const isDefault = ids.length === presetSet.size && ids.every((id) => presetSet.has(id));
  return isDefault ? null : ids;
}

ui.musicIdToggle.addEventListener("click", () => {
  state.musicIDEnabled = !state.musicIDEnabled;
  localStorage.setItem("oida.music-id", state.musicIDEnabled ? "on" : "off");
  updateModeButton();
  setListenStatus(`Music ID ${state.musicIDEnabled ? "on" : "off"}.`, "");
  if (window.__oidaNative) shellAction(nativeSelectionMessage("sync"));
});

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

function applySourceSelection(source, notifyNative = true) {
  const button = document.querySelector(`.source[data-source="${source}"]`);
  if (!button) return;
  markRadioSelection([...document.querySelectorAll(".source")], (other) => other === button);
  state.source = source;
  ui.systemPanel.hidden = source !== "system";
  ui.micPanel.hidden = source !== "mic";
  ui.filePanel.hidden = source !== "file";
  if (source === "mic" && !state.micDevices.length) refreshMicDevices(false);
  if (notifyNative && window.__oidaNative) shellAction(nativeSelectionMessage("sync"));
}

window.oidaSelectSource = (source) => {
  const normalized = ["system", "mic", "file"].includes(source) ? source : "system";
  if (state.phase !== "idle") return;
  applySourceSelection(normalized);
  setListenStatus("", "");
};

function positionSourceMenu(anchor) {
  if (!anchor || !ui.sourceModal) return;
  const rect = anchor.getBoundingClientRect();
  const menuWidth = Math.min(304, window.innerWidth - 16);
  const left = Math.max(8, Math.min(window.innerWidth - menuWidth - 8, rect.left));
  const top = Math.min(window.innerHeight - 120, rect.bottom + 6);
  ui.sourceModal.style.setProperty("--source-menu-left", `${Math.round(left)}px`);
  ui.sourceModal.style.setProperty("--source-menu-top", `${Math.round(top)}px`);
}

window.oidaOpenSource = (source, anchor = null) => {
  const normalized = ["system", "mic", "file"].includes(source) ? source : "system";
  if (state.phase !== "idle") return;
  applySourceSelection(normalized);
  ui.sourceModalTitle.textContent = { system: "System audio", mic: "Microphone", file: "Audio file" }[normalized];
  document.querySelectorAll("dialog[open]").forEach((dialog) => {
    if (dialog !== ui.sourceModal) dialog.close();
  });
  const sourceButton = anchor || document.querySelector(`.source[data-source="${normalized}"]`);
  positionSourceMenu(sourceButton);
  if (!ui.sourceModal.open) {
    if (typeof ui.sourceModal.show === "function") ui.sourceModal.show();
    else if (typeof ui.sourceModal.showModal === "function") ui.sourceModal.showModal();
  }
  setListenStatus("", "");
};

document.querySelectorAll(".source").forEach((button) => {
  button.addEventListener("click", (interaction) => {
    interaction.stopPropagation();
    window.oidaOpenSource(button.dataset.source, button);
  });
});
document.addEventListener("click", (interaction) => {
  if (!ui.sourceModal?.open) return;
  if (interaction.target.closest("#sourceModal, .source")) return;
  ui.sourceModal.close();
});
window.addEventListener("resize", () => {
  if (!ui.sourceModal?.open) return;
  positionSourceMenu(document.querySelector(`.source[data-source="${state.source}"]`));
});
radioKeyNav(document.querySelector(".sources"), ".source");

function applyDirectionSelection(direction, notifyNative = true) {
  state.direction = direction === "future" ? "future" : "past";
  document.querySelectorAll(".direction-button").forEach((button) => {
    const active = button.dataset.direction === state.direction;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", active ? "true" : "false");
  });
  if (notifyNative && window.__oidaNative) shellAction(nativeSelectionMessage("sync"));
}

document.querySelectorAll(".direction-button").forEach((button) => {
  button.addEventListener("click", () => {
    if (state.phase !== "idle") return;
    applyDirectionSelection(button.dataset.direction);
  });
});

ui.captureSeconds.addEventListener("change", () => {
  if (window.__oidaNative) shellAction(nativeSelectionMessage("sync"));
});

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
  if (["waiting", "recording", "capturing"].includes(state.phase)) return stopListening();
  if (["analyzing", "processing"].includes(state.phase)) return;
  if (window.__oidaNative) {
    setPhase("capturing");
    const seconds = Number(ui.captureSeconds.value) || 10;
    const status = state.source === "file" ? "Choose an audio file" : `Hearing · ${Math.round(seconds)}s`;
    setListenStatus(status, "active");
    updateListenProgress(0, status);
    shellAction(nativeSelectionMessage("listen"));
    return;
  }
  if (state.source === "system") return listenSystem();
  if (state.source === "mic") return listenMic();
  ui.fileInput.click();
});

ui.browseFile.addEventListener("click", () => {
  if (state.phase !== "idle") return;
  ui.fileInput.click();
});

async function stopListening() {
  if (window.__oidaNative) {
    shellAction(nativeSelectionMessage("stop"));
    setPhase("idle");
    setListenStatus("Stopped.", "");
    return;
  }
  if (["recording", "capturing"].includes(state.phase) && state.recorder) {
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
    setListenStatus("Stopped waiting — the daemon finishes in the background; the result lands in Sessions.", "");
  }
}

async function listenSystem() {
  const seconds = Number(ui.captureSeconds.value) || 10;
  setPhase("waiting");
  setListenStatus("Asking the oída app to capture system audio…", "active");
  try {
    const response = await post("/background/capture-request", {
      seconds,
      route_preset: state.preset,
      direction: state.direction,
      source: "system",
      enabled_skill_ids: selectedSkillIds(),
      song_id: state.preset === "music" && !state.customMode && state.musicIDEnabled,
    });
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
          setPhase("capturing");
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
  if (state.recordStopTimer) {
    clearTimeout(state.recordStopTimer);
    state.recordStopTimer = null;
  }
}

async function listenMic() {
  let stream = null;
  try {
    const captureSeconds = Number(ui.captureSeconds.value) || 10;
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
      setPhase("processing");
      setListenStatus("Operating listening…", "active");
      updateListenProgress(1, "Operating listening…");
      try {
        await uploadAndAnalyze(
          blob,
          `oida-mic-${Date.now()}.${extension}`,
          "mic",
          `Microphone · ${deviceLabel}`,
          deviceId,
          { direction: state.direction, seconds: captureSeconds, trigger: "dashboard-microphone" }
        );
      } catch (error) {
        setPhase("idle");
        if (error.name !== "AbortError") setListenStatus(error.message, "error");
      }
    };
    recorder.start();
    setPhase("capturing", "Stop");
    const startedAt = Date.now();
    const directionNote = state.direction === "past"
      ? "Browser input has no armed history; capturing the next bounded window"
      : "Recording the future window";
    setListenStatus(`${directionNote} · ${Math.round(captureSeconds)}s`, "active");
    updateListenProgress(0, `Hearing · ${Math.round(captureSeconds)}s`);
    stopRecordTimer();
    state.recordTimer = setInterval(() => {
      const elapsed = Math.max(0, (Date.now() - startedAt) / 1000);
      const remaining = Math.max(0, captureSeconds - elapsed);
      setListenStatus(`Hearing · ${Math.ceil(remaining)}s`, "active");
      updateListenProgress(elapsed / captureSeconds, `Hearing · ${Math.ceil(remaining)}s`);
    }, 200);
    state.recordStopTimer = setTimeout(() => {
      if (recorder.state === "recording") recorder.stop();
    }, captureSeconds * 1000);
  } catch (error) {
    // Without this, a failed recorder leaves the acquired mic hot forever.
    stream?.getTracks().forEach((track) => track.stop());
    state.recorder = null;
    stopRecordTimer();
    setPhase("idle");
    setListenStatus(`Microphone: ${error.message}`, "error");
  }
}

async function uploadBlob(blob, filename, signal) {
  const form = new FormData();
  form.append("file", blob, filename);
  return fetchJson("/upload", { method: "POST", body: form, signal });
}

async function uploadAndAnalyze(blob, filename, sourceKind, sourceLabel, deviceId, analysisOptions = {}) {
  const controller = new AbortController();
  state.abortController = controller;
  try {
    const upload = await uploadBlob(blob, filename, controller.signal);
    await analyzePath(upload.path, sourceKind, { controller, sourceLabel, deviceId, ...analysisOptions });
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
  body.song_id = state.preset === "music" && !state.customMode && state.musicIDEnabled;
  if (sourceKind === "mic") {
    body.source_type = "live_input";
    body.source_label = options.sourceLabel || "Microphone recording";
    body.privacy_mode = "ephemeral";
    body.raw_audio_policy = "temp";
    if (options.deviceId) body.device_id = options.deviceId;
  }
  if (options.direction || options.seconds || options.trigger) {
    body.capture_direction = options.direction || state.direction;
    body.capture_seconds = Number(options.seconds) || Number(ui.captureSeconds.value) || 10;
    body.capture_trigger = options.trigger || "dashboard";
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

const RESULT_EXPORT_ICON = `<svg class="ci" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M12 15V4M8 7.5 12 3.5l4 4"/><path d="M5 12v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6"/></svg>`;
const RESULT_COPY_ICON = `<svg class="ci" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>`;

function visibleEvents(events) {
  const filters = [...state.tagFilters];
  if (!filters.length) return events || [];
  return (events || []).filter((event) => {
    const tags = new Set((event.tags || []).map((tag) => String(tag)));
    return filters.every((tag) => tags.has(tag));
  });
}

function hasTagFilters() {
  return state.tagFilters.size > 0;
}

function tagFilterDescription() {
  return [...state.tagFilters].map((tag) => `#${tag}`).join(" + ");
}

function knownTags() {
  const tags = new Set();
  for (const session of [...state.sessions, ...state.archivedSessions]) {
    for (const event of (session.events || [])) {
      for (const tag of (event.tags || [])) {
        const value = String(tag || "").trim();
        if (value) tags.add(value);
      }
    }
  }
  return [...tags].sort((a, b) => a.localeCompare(b));
}

function refreshTagFilter() {
  if (!ui.tagFilterBar) return;
  const active = [...state.tagFilters];
  const filtering = active.length > 0;
  ui.tagFilterBar.hidden = !filtering;
  ui.sessionContextRow?.classList.toggle("filtering", filtering);
  ui.tagFilterChips.replaceChildren();
  for (const value of active) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "tag-filter-chip";
    chip.textContent = `#${value}`;
    chip.title = `Remove #${value}`;
    chip.setAttribute("aria-label", `Remove tag filter ${value}`);
    chip.addEventListener("click", () => toggleTagFilter(value));
    ui.tagFilterChips.appendChild(chip);
  }
  const available = knownTags().filter((tag) => !state.tagFilters.has(tag));
  ui.tagFilterAdd.disabled = available.length === 0;
  ui.tagFilterMenu.replaceChildren();
  for (const value of available) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "drop-item";
    item.textContent = `#${value}`;
    item.addEventListener("click", () => {
      const next = new Set(state.tagFilters);
      next.add(value);
      setTagFilters(next);
      closeDropdowns();
    });
    ui.tagFilterMenu.appendChild(item);
  }
}

function setTagFilters(tags) {
  state.tagFilters = new Set([...tags].map((tag) => String(tag || "").trim()).filter(Boolean));
  refreshTagFilter();
  renderSessionList(ui.historyList, state.sessions, false);
  renderSessionList(ui.archiveList, state.archivedSessions, true);
  const current = [...state.sessions, ...state.archivedSessions].find((session) => session.id === state.currentSessionId) || null;
  renderSession(current, state.lastEventId, null);
}

function toggleTagFilter(tag) {
  const next = new Set(state.tagFilters);
  if (next.has(tag)) next.delete(tag);
  else next.add(tag);
  setTagFilters(next);
}

ui.tagFilterReset?.addEventListener("click", () => setTagFilters([]));

function selectEvent(event, fullResponse = null, options = {}) {
  if (!event) return;
  state.lastEvent = event;
  state.lastEventId = event.id || null;
  state.lastJson = fullResponse || { listening_event: event };
  document.querySelectorAll(".result-entry").forEach((entry) => {
    entry.classList.toggle("selected", entry.dataset.eventId === String(event.id || ""));
  });
  document.querySelectorAll(".session-event").forEach((button) => {
    button.classList.toggle("selected", button.dataset.eventId === String(event.id || ""));
  });
  renderBreakdown(buildResultGroups(event), event);
  ui.germNote.hidden = true;
  if (options.scroll) {
    document.querySelector(`.result-entry[data-event-id="${CSS.escape(String(event.id || ""))}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion.matches ? "auto" : "smooth" });
  }
}

function renderSession(session, selectedEventId = null, fullResponse = null, options = {}) {
  if (!session) {
    state.currentSessionId = null;
    state.lastEvent = null;
    state.lastEventId = null;
    state.currentSessionName = "";
    ui.sessionTitle.textContent = "Current session";
    ui.resultEntries.innerHTML = `<article class="result-entry placeholder-entry"><div class="result-head"><h2>Ready</h2></div><p class="result-summary placeholder">Pick a source and press Listen. Results from the current session appear here; details on the right.</p></article>`;
    ui.resultBody.innerHTML = `<p class="empty-note">Select a listening result to see its analysis.</p>`;
    if (window.__oidaNative) shellAction({ action: "session", sessionName: "" });
    return;
  }

  state.currentSessionId = session.id;
  state.currentSessionName = session.name || "Listening session";
  ui.sessionTitle.textContent = state.currentSessionName;
  if (window.__oidaNative) shellAction({ action: "session", sessionName: state.currentSessionName });
  const allEvents = session.events || [];
  const events = visibleEvents(allEvents);
  ui.resultEntries.innerHTML = "";
  if (!events.length) {
    const heading = hasTagFilters() ? "No results match these tags" : "No results yet";
    const note = hasTagFilters()
      ? `No result has every active filter (${escapeHtml(tagFilterDescription())}). Remove a tag or reset the filter.`
      : "Press Listen to add the first result to this session.";
    ui.resultEntries.innerHTML = `<article class="result-entry placeholder-entry"><div class="result-head"><h2>${heading}</h2></div><p class="result-summary placeholder">${note}</p></article>`;
    state.lastEvent = null;
    state.lastEventId = null;
    state.lastJson = null;
    ui.resultBody.innerHTML = `<p class="empty-note">This session has no analysis yet.</p>`;
    return;
  }

  for (const event of events) {
    const aggregate = event.aggregate || {};
    const article = document.createElement("article");
    article.className = "result-entry";
    article.dataset.eventId = String(event.id || "");
    article.tabIndex = 0;
    article.setAttribute("role", "button");
    article.setAttribute("aria-label", `Show analysis for ${aggregate.title || "listening result"}`);

    const header = document.createElement("div");
    header.className = "result-entry-head";
    const heading = document.createElement("h2");
    const titleButton = document.createElement("button");
    titleButton.type = "button";
    titleButton.className = "result-title-button";
    titleButton.textContent = aggregate.title || "Listening result";
    titleButton.title = "Click to rename this listening result";
    titleButton.setAttribute("aria-label", `Rename ${aggregate.title || "listening result"}`);
    titleButton.addEventListener("click", (interaction) => {
      interaction.preventDefault();
      interaction.stopPropagation();
      selectEvent(event);
      beginResultTitleEdit(titleButton, event, session);
    });
    heading.appendChild(titleButton);
    header.appendChild(heading);
    const summary = document.createElement("p");
    summary.className = "result-summary";
    summary.textContent = resultSummaryText(event);

    const footer = document.createElement("div");
    footer.className = "result-entry-foot";
    const tags = document.createElement("div");
    tags.className = "tags";
    for (const value of (event.tags || []).slice(0, 8)) {
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = `tag${state.tagFilters.has(String(value)) ? " active" : ""}`;
      tag.textContent = value;
      tag.title = state.tagFilters.has(String(value)) ? `Remove tag filter ${value}` : `Add tag filter ${value}`;
      tag.addEventListener("click", (interaction) => {
        interaction.preventDefault();
        interaction.stopPropagation();
        toggleTagFilter(String(value));
      });
      tags.appendChild(tag);
    }
    if ((event.tags || []).length > 8) {
      const more = document.createElement("span");
      more.className = "tag more";
      more.textContent = `+${event.tags.length - 8}`;
      tags.appendChild(more);
    }
    const actions = resultActions(event, session);
    footer.append(tags, actions);
    article.append(header, summary);
    article.append(footer);

    const choose = (interaction) => {
      if (interaction.target.closest(".result-entry-actions")) return;
      selectEvent(event);
    };
    article.addEventListener("click", choose);
    article.addEventListener("keydown", (interaction) => {
      if (["Enter", " "].includes(interaction.key)) {
        interaction.preventDefault();
        selectEvent(event);
      }
    });
    ui.resultEntries.appendChild(article);
  }

  const selected = events.find((event) => event.id === selectedEventId) ||
    events.find((event) => event.id === state.lastEventId) || events[0];
  selectEvent(selected, selected.id === selectedEventId ? fullResponse : null, options);
}

function resultActions(event, session) {
  const actions = document.createElement("div");
  actions.className = "result-entry-actions";

  const time = document.createElement("time");
  time.className = "result-time";
  time.dateTime = event.created_at || "";
  time.textContent = timeAgo(event.created_at);

  const dropdown = document.createElement("div");
  dropdown.className = "dropdown result-action-menu";
  const exportButton = document.createElement("button");
  exportButton.className = "result-tool";
  exportButton.type = "button";
  exportButton.setAttribute("aria-haspopup", "true");
  exportButton.setAttribute("aria-expanded", "false");
  exportButton.title = "Act on this listening result";
  exportButton.setAttribute("aria-label", "Act on this listening result");
  exportButton.innerHTML = RESULT_EXPORT_ICON;
  const menu = document.createElement("div");
  menu.className = "drop-menu";
  menu.hidden = true;
  const items = [
    ["conversation", "Ask about this result"],
    ["remember", event.memory?.saved_trace_id ? "Remembered" : "Remember"],
    ["wiki", "Expand on Wiki"],
    ["json", "Export JSON"],
    ["sound", "Generate derived sound"],
    ["prompt", "Convert listening to prompt"],
  ];
  for (const [action, label] of items) {
    const item = document.createElement("button");
    item.className = "drop-item";
    item.dataset.action = action;
    item.textContent = label;
    if (action === "remember" && event.memory?.saved_trace_id) item.disabled = true;
    if (action === "wiki" && !state.sonicfieldAvailable) {
      item.disabled = true;
      item.title = "Sonic Field root not found";
    }
    if (action === "sound" && !segmentUri(event)) {
      item.disabled = true;
      item.title = "This result keeps no audio reference";
    }
    menu.appendChild(item);
  }
  menu.addEventListener("click", (interaction) => {
    const item = interaction.target.closest(".drop-item");
    if (!item || item.disabled) return;
    interaction.stopPropagation();
    closeDropdowns();
    handleResultAction(item.dataset.action, event, session, item);
  });
  dropdown.append(exportButton, menu);
  wireDropdown(dropdown);

  const copy = document.createElement("button");
  copy.className = "result-tool";
  copy.type = "button";
  copy.title = "Copy listening result";
  copy.setAttribute("aria-label", "Copy listening result");
  copy.innerHTML = RESULT_COPY_ICON;
  copy.addEventListener("click", async (interaction) => {
    interaction.stopPropagation();
    try {
      await copyText(listeningResultText(event));
      copy.classList.add("copied");
      setListenStatus("Listening result copied.", "");
      setTimeout(() => copy.classList.remove("copied"), 900);
    } catch (error) {
      setListenStatus(`Copy: ${error.message}`, "error");
    }
  });
  const ask = document.createElement("button");
  ask.className = "result-tool result-ask";
  ask.type = "button";
  ask.textContent = "Ask";
  ask.title = "Ask about this listening result";
  ask.setAttribute("aria-label", "Ask about this listening result");
  ask.addEventListener("click", (interaction) => {
    interaction.preventDefault();
    interaction.stopPropagation();
    selectEvent(event);
    openConversation(event);
  });
  actions.append(time, ask, dropdown, copy);
  return actions;
}

function beginResultTitleEdit(button, event, session) {
  const initial = event.aggregate?.title || "Listening result";
  const input = document.createElement("input");
  input.className = "result-title-input";
  input.value = initial;
  input.maxLength = 160;
  input.setAttribute("aria-label", "Listening result title");
  let finished = false;

  const finish = async (save) => {
    if (finished) return;
    finished = true;
    const title = input.value.replace(/\s+/g, " ").trim();
    if (!save || !title || title === initial) {
      input.replaceWith(button);
      return;
    }
    input.disabled = true;
    try {
      await fetchJson(`/sessions/${encodeURIComponent(session.id)}/events/${encodeURIComponent(event.id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title }),
      });
      event.aggregate = { ...(event.aggregate || {}), title };
      setListenStatus("Listening result renamed.", "");
      await refreshHistory({ selectSessionId: session.id, eventId: event.id });
    } catch (error) {
      input.disabled = false;
      finished = false;
      setListenStatus(`Rename: ${error.message}`, "error");
      input.focus();
      input.select();
    }
  };

  input.addEventListener("keydown", (interaction) => {
    interaction.stopPropagation();
    if (interaction.key === "Enter") {
      interaction.preventDefault();
      finish(true);
    } else if (interaction.key === "Escape") {
      interaction.preventDefault();
      finish(false);
    }
  });
  input.addEventListener("click", (interaction) => interaction.stopPropagation());
  input.addEventListener("blur", () => finish(true));
  button.replaceWith(input);
  input.focus();
  input.select();
}

function renderEvent(event, fullResponse) {
  if (!event) return;
  showListeningView();
  const sessionId = event.session?.id || "session_legacy";
  let session = [...state.sessions, ...state.archivedSessions].find((item) => item.id === sessionId);
  if (!session) {
    session = {
      id: sessionId,
      name: event.session?.name || "Listening session",
      updated_at: event.created_at,
      events: [],
      active: true,
    };
    state.sessions.unshift(session);
  }
  session.events = [event, ...(session.events || []).filter((item) => item.id !== event.id)];
  session.event_count = session.events.length;
  session.updated_at = event.created_at || session.updated_at;
  renderSession(session, event.id, fullResponse, { scroll: true });
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

  const musicID = event.music_id;
  if (musicID) {
    const identity = musicID.matched
      ? `<div class="music-id-module"><strong>${escapeHtml(musicID.title || "Identified music")}</strong>${musicID.artist ? `<span>${escapeHtml(musicID.artist)}</span>` : ""}${musicID.album ? `<small>${escapeHtml(musicID.album)}</small>` : ""}</div>`
      : `<p class="empty-note">${escapeHtml(musicID.note || "No Music ID match.")}</p>`;
    groups.push({ key: "music-id", label: "Music ID", count: musicID.matched ? 1 : 0, html: identity });
  }

  const heard = (claims.heard || []).slice(0, 12);
  if (heard.length) groups.push({ key: "heard", label: "Heard", count: heard.length, html: claimList(heard, true) });

  const measured = claims.measured || [];
  if (measured.length || event.features) {
    groups.push({
      key: "measured",
      label: "Measured",
      count: measured.length,
      html: measurementModules(event.features || {}, measured, claimList),
    });
  }

  const undetermined = claims.undetermined || [];
  if (undetermined.length) groups.push({ key: "undetermined", label: "Undetermined", count: undetermined.length, html: claimList(undetermined, false) });

  const memoryMatches = event.memory?.similarity || [];
  if (memoryMatches.length) {
    const items = memoryMatches
      .slice(0, 6)
      .map((match) => {
        const trace = match.trace || {};
        const score = typeof match.score === "number" ? ` · ${Math.round(match.score * 100)}%` : "";
        return `<li><button class="related-memory" type="button" data-trace-id="${escapeHtml(trace.id || "")}"><span>${escapeHtml(trace.title || trace.id || "trace")}</span><small>${escapeHtml(score.replace(/^ · /, ""))}</small></button></li>`;
      })
      .join("");
    groups.push({ key: "related", label: "Related", count: memoryMatches.length, html: `<div class="block related-list"><ul>${items}</ul></div>` });
  }
  return groups;
}

function measurementModules(features, measured, claimList) {
  const number = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
  const field = (...keys) => keys.map((key) => features[key]).find((value) => value !== undefined && value !== null);
  const metric = (label, value, unit = "") =>
    `<div class="metric-module"><span class="metric-value">${escapeHtml(value)}${unit ? ` <small>${escapeHtml(unit)}</small>` : ""}</span><span class="metric-label">${escapeHtml(label)}</span></div>`;
  const metrics = [
    metric("Loudness", number(field("integratedLufs", "integrated_lufs")), "LUFS"),
    metric("Peak", number(field("peakDbfs", "peak_dbfs")), "dBFS"),
    metric("RMS", number(field("rmsDbfs", "rms_dbfs")), "dBFS"),
    metric("Dynamics", number(field("crestFactorDb", "crest_factor_db")), "dB"),
    metric("Flatness", Number.isFinite(Number(field("spectralFlatness", "spectral_flatness"))) ? `${Math.round(Number(field("spectralFlatness", "spectral_flatness")) * 100)}%` : "—"),
    metric("Centroid", Number.isFinite(Number(field("spectralCentroidHz", "spectral_centroid_hz"))) ? number(Number(field("spectralCentroidHz", "spectral_centroid_hz")) / 1000, 2) : "—", "kHz"),
  ].join("");

  const bands = features.bandEnergy || features.band_energy || {};
  const bandKeys = [["sub", "Sub"], ["bass", "Bass"], ["lowMid", "Low mid"], ["mid", "Mid"], ["high", "High"], ["air", "Air"]];
  const bandValue = (key) => bands[key] ?? (key === "lowMid" ? bands.low_mid : undefined);
  const maximumBand = Math.max(0.001, ...bandKeys.map(([key]) => Number(bandValue(key)) || 0));
  const bandChart = bandKeys.some(([key]) => Number.isFinite(Number(bandValue(key))))
    ? `<div class="band-chart">${bandKeys.map(([key, label]) => {
        const value = Math.max(0, Number(bandValue(key)) || 0);
        const height = Math.max(2, Math.round((value / maximumBand) * 66));
        return `<div class="band-column" title="${escapeHtml(label)} · ${Math.round(value * 1000) / 10}%"><span class="band-bar" style="height:${height}px"></span><span class="band-label">${escapeHtml(label)}</span></div>`;
      }).join("")}</div>`
    : `<p class="empty-note">Frequency energy will appear with the next measured listening result.</p>`;

  const spectrogramData = features.spectrogram;
  const spectrogram = hasRenderableSpectrogram(spectrogramData)
    ? `<div class="spectrogram-wrap"><button class="spectrogram-trigger" id="openEventSpectrogram" type="button" aria-label="Open a larger high-definition sonogram" title="Open larger sonogram"><canvas class="spectrogram" id="eventSpectrogram" aria-label="Sonogram of the listened audio"></canvas></button><div class="spectrogram-axis"><span>${Math.round(spectrogramData.minimumHz || spectrogramData.minimum_hz || 20)} Hz</span><span>${number(spectrogramData.durationSeconds || spectrogramData.duration_seconds, 1)} s</span><span>${Math.round((spectrogramData.maximumHz || spectrogramData.maximum_hz || 20000) / 1000)} kHz</span></div></div>`
    : `<div class="spectrogram-wrap is-empty"><div class="spectrogram-empty" role="img" aria-label="Empty sonogram"></div></div>`;
  const visualization = `
    <div class="measurement-viz">
      <div class="viz-tabs" role="tablist" aria-label="Measured visualization">
        <button class="viz-tab active" role="tab" aria-selected="true" data-viz-tab="sonogram">Sonogram</button>
        <button class="viz-tab" role="tab" aria-selected="false" data-viz-tab="energy">Frequency energy</button>
      </div>
      <div class="viz-panel" data-viz-panel="sonogram">${spectrogram}</div>
      <div class="viz-panel" data-viz-panel="energy" hidden>${bandChart}</div>
    </div>`;
  const rawClaims = measured.length
    ? `<details class="measure-details"><summary>All ${measured.length} measured claims</summary>${claimList(measured, false)}</details>`
    : "";
  return `<div class="measurement-grid">${metrics}</div>${visualization}${rawClaims}`;
}

// The breakdown lives in the right rail as stacked sections. Hypotheses is the
// default reading surface; measured and every supporting group stay quiet
// until requested.
function renderBreakdown(groups, event) {
  if (!groups.length) {
    ui.resultBody.innerHTML = `<p class="empty-note">No claims were produced.</p>`;
    return;
  }
  const caret = `<svg class="ci bd-caret" viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`;
  ui.resultBody.innerHTML = groups
    .map((g) => `<details class="bd-section"${g.key === "hypotheses" ? " open" : ""}><summary>${caret}${escapeHtml(g.label)}<span class="bd-count">${g.count}</span></summary>${g.html}</details>`)
    .join("");
  wireMeasurementTabs(event?.features?.spectrogram);
  ui.resultBody.querySelectorAll(".related-memory[data-trace-id]").forEach((button) => {
    button.addEventListener("click", () => openRelatedTrace(button.dataset.traceId));
  });
  // The canvas has zero size while its <details> is closed, so the initial
  // draw is skipped for collapsed sections; draw again when one opens.
  ui.resultBody.querySelectorAll(".bd-section").forEach((section) => {
    section.addEventListener("toggle", () => {
      if (section.open && section.querySelector("#eventSpectrogram")) {
        requestAnimationFrame(() => drawSpectrogram(event?.features?.spectrogram));
      }
    });
  });
  requestAnimationFrame(() => drawSpectrogram(event?.features?.spectrogram));
}

async function openRelatedTrace(traceId) {
  if (!traceId) return;
  closeConversation();
  try {
    const data = await fetchJson(`/memory/trace/${encodeURIComponent(traceId)}`);
    const trace = data.trace || {};
    const short = trace.summaries?.short || trace.summaries?.detailed || "No written summary.";
    akousmataUi.title.textContent = trace.title || "Related listening";
    akousmataUi.detail.innerHTML = [
      `<p class="memory-meta">${escapeHtml(trace.id || traceId)} · ${escapeHtml(trace.sourceLabel || trace.sourceKind || "source")} · ${escapeHtml(String(trace.createdAt || "").slice(0, 16).replace("T", " "))}</p>`,
      `<section class="memory-block"><div class="memory-kicker">Listening</div><p>${escapeHtml(short)}</p></section>`,
      (trace.tags || []).length ? `<section class="memory-block"><div class="memory-kicker">Tags</div><p>${trace.tags.map((tag) => `#${escapeHtml(tag)}`).join(" · ")}</p></section>` : "",
    ].join("");
    akousmataUi.modal.hidden = false;
  } catch (error) {
    akousmataUi.title.textContent = "Related listening unavailable";
    akousmataUi.detail.innerHTML = `<p class="empty-note">${escapeHtml(error.message)}</p>`;
    akousmataUi.modal.hidden = false;
  }
}

function wireMeasurementTabs(spectrogram) {
  document.getElementById("openEventSpectrogram")?.addEventListener("click", () => openSonogramModal(spectrogram));
  document.querySelectorAll(".viz-tab").forEach((button) => {
    button.addEventListener("click", () => {
      const selected = button.dataset.vizTab;
      document.querySelectorAll(".viz-tab").forEach((candidate) => {
        const active = candidate.dataset.vizTab === selected;
        candidate.classList.toggle("active", active);
        candidate.setAttribute("aria-selected", active ? "true" : "false");
      });
      document.querySelectorAll(".viz-panel").forEach((panel) => {
        panel.hidden = panel.dataset.vizPanel !== selected;
      });
      if (selected === "sonogram") requestAnimationFrame(() => drawSpectrogram(spectrogram));
    });
  });
}

function spectrogramStats(spectrogram) {
  const values = spectrogram?.values;
  if (!Array.isArray(values) || !values.length || !Array.isArray(values[0]) || !values[0].length) return null;
  const rows = values[0].length;
  if (!values.every((column) => Array.isArray(column) && column.length === rows)) return null;
  let minimum = Infinity;
  let maximum = -Infinity;
  for (const column of values) {
    for (const value of column) {
      const energy = Number(value);
      if (!Number.isFinite(energy)) continue;
      minimum = Math.min(minimum, energy);
      maximum = Math.max(maximum, energy);
    }
  }
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return null;
  return { values, columns: values.length, rows, minimum, maximum };
}

function hasRenderableSpectrogram(spectrogram) {
  const stats = spectrogramStats(spectrogram);
  return Boolean(stats && stats.maximum > 0.01 && stats.maximum - stats.minimum > 0.01);
}

function openSonogramModal(spectrogram) {
  if (!hasRenderableSpectrogram(spectrogram) || !ui.sonogramModal || !ui.sonogramModalCanvas) return;
  state.activeSpectrogram = spectrogram;
  const minimumHz = Number(spectrogram.minimumHz ?? spectrogram.minimum_hz ?? 20);
  const maximumHz = Number(spectrogram.maximumHz ?? spectrogram.maximum_hz ?? 20000);
  const duration = Number(spectrogram.durationSeconds ?? spectrogram.duration_seconds);
  ui.sonogramModalMin.textContent = `${Math.round(minimumHz)} Hz`;
  ui.sonogramModalMax.textContent = `${Math.round(maximumHz / 1000)} kHz`;
  ui.sonogramModalDuration.textContent = Number.isFinite(duration) ? `${duration.toFixed(1)} s` : "—";
  document.querySelectorAll("dialog[open]").forEach((dialog) => {
    if (dialog !== ui.sonogramModal) dialog.close();
  });
  if (!ui.sonogramModal.open) ui.sonogramModal.showModal();
  requestAnimationFrame(() => drawSpectrogram(spectrogram, ui.sonogramModalCanvas));
}

function drawSpectrogram(spectrogram, targetCanvas = null) {
  const canvas = targetCanvas || document.getElementById("eventSpectrogram");
  const stats = spectrogramStats(spectrogram);
  if (!canvas || !stats) return;
  const ratio = Math.min(3, Math.max(1, window.devicePixelRatio || 1));
  const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
  const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
  if (width <= 1 || height <= 1) return;
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.clearRect(0, 0, width, height);
  if (!hasRenderableSpectrogram(spectrogram)) return;

  // Draw the compact feature matrix once, then scale it into a DPR-sized
  // backing store with high-quality interpolation. This keeps the payload
  // lightweight while avoiding the former blocky, pixelated presentation.
  const source = document.createElement("canvas");
  source.width = stats.columns;
  source.height = stats.rows;
  const sourceContext = source.getContext("2d");
  if (!sourceContext) return;
  const image = sourceContext.createImageData(stats.columns, stats.rows);
  const dark = document.documentElement.dataset.theme === "dark";
  const background = dark ? [41, 41, 38] : [238, 238, 234];
  const foreground = dark ? [241, 240, 235] : [29, 29, 27];
  for (let x = 0; x < stats.columns; x += 1) {
    for (let y = 0; y < stats.rows; y += 1) {
      const raw = Math.max(0, Math.min(1, Number(stats.values[x][y]) || 0));
      const energy = Math.pow(raw, 0.82);
      const offset = ((stats.rows - 1 - y) * stats.columns + x) * 4;
      for (let channel = 0; channel < 3; channel += 1) {
        image.data[offset + channel] = Math.round(background[channel] + (foreground[channel] - background[channel]) * energy);
      }
      image.data[offset + 3] = 255;
    }
  }
  sourceContext.putImageData(image, 0, 0);
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.drawImage(source, 0, 0, width, height);
}

/* ─────────────────────────── result actions ─────────────────────── */

async function rememberReading(event, trigger = null) {
  if (!event) return;
  if (trigger) trigger.disabled = true;
  try {
    const result = await post("/memory/remember", { event, tags: [state.preset] });
    if (result.trace?.id) {
      event.memory = { ...(event.memory || {}), saved_trace_id: result.trace.id };
      if (trigger) trigger.textContent = "Remembered";
    }
    setListenStatus("Listening result remembered.", "");
    refreshMemory();
  } catch (error) {
    setListenStatus(`Memory: ${error.message}`, "error");
    if (trigger) trigger.disabled = false;
  }
}

function handleResultAction(action, event, session, trigger = null) {
  switch (action) {
    case "conversation":
      selectEvent(event);
      openConversation(event);
      break;
    case "remember":
      rememberReading(event, trigger);
      break;
    case "wiki":
      if (ui.wikiModal && typeof ui.wikiModal.showModal === "function" && !ui.wikiModal.open) ui.wikiModal.showModal();
      exploreWiki({ event });
      break;
    case "json":
      downloadJson({ listening_event: event }, `${fileSlug(event.aggregate?.title, "listening-result")}.json`);
      break;
    case "sound":
      germHandoff("sound", event, session?.id);
      break;
    case "prompt":
      germHandoff("prompt", event, session?.id);
      break;
  }
}

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

function germPayload(mode, event, sessionId = null) {
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
  return {
    mode,
    audio,
    listening,
    origin: GERM_ORIGINS[event.source?.type] || "file",
    session_id: sessionId || event.id,
    tags: (event.tags || []).slice(0, 8),
  };
}

async function germHandoff(mode, event = state.lastEvent, sessionId = null, options = {}) {
  if (!event) return null;
  setGermNote(options.status || `Handing to germ (${mode})…`);
  try {
    const result = await post("/germ/handoff", germPayload(mode, event, sessionId));
    if (!options.quiet) setGermNote(`akousma ${result.akousma_id} → germ`);
    if (options.open !== false) window.open(result.germ_url, "_blank", "noopener");
    return result;
  } catch (error) {
    const detail = String(error.message) === "404" ? "germ bridge unavailable (akousma package not installed)" : error.message;
    setGermNote(`germ: ${detail}`, "error");
    if (options.throwOnError) throw error;
    return null;
  }
}

async function batchGermHandoff(session, mode) {
  const events = (session.events || []).filter((event) => mode !== "sound" || segmentUri(event));
  if (!events.length) {
    setListenStatus(mode === "sound" ? "No session results retain an audio reference." : "This session has no results.", "error");
    return;
  }
  setGermNote(`Handing ${events.length} session result${events.length === 1 ? "" : "s"} to germ (${mode})…`);
  const results = [];
  for (const event of events) {
    const result = await germHandoff(mode, event, session.id, { open: false, quiet: true });
    if (result) results.push(result);
  }
  if (results[0]?.germ_url) window.open(results[0].germ_url, "_blank", "noopener");
  setGermNote(`${results.length} of ${events.length} session results → germ${results.length > 1 ? " · opened first" : ""}`, results.length ? "" : "error");
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

async function refreshHistory(options = {}) {
  const requestSerial = ++state.historyRequestSerial;
  try {
    const history = await fetchJson("/sessions");
    if (requestSerial !== state.historyRequestSerial) return;
    const sessions = history.sessions || [];
    const archived = history.archived_sessions || [];
    state.sessions = sessions;
    state.archivedSessions = archived;
    refreshTagFilter();
    renderSessionList(ui.historyList, sessions, false);
    ui.archiveSection.hidden = archived.length === 0;
    renderSessionList(ui.archiveList, archived, true);

    const combined = [...sessions, ...archived];
    const targetEventId = options.eventId || (options.selectLatest ? state.nativeEventId : null) || state.lastEventId;
    const eventSession = targetEventId
      ? combined.find((session) => (session.events || []).some((event) => event.id === targetEventId))
      : null;
    const selectedSession = eventSession ||
      combined.find((session) => session.id === options.selectSessionId) ||
      combined.find((session) => session.id === state.currentSessionId) ||
      sessions.find((session) => session.active) || sessions[0] || archived[0] || null;
    const selectedEventId = targetEventId && eventSession?.id === selectedSession?.id
      ? targetEventId
      : ((selectedSession?.events || []).some((event) => event.id === state.lastEventId) ? state.lastEventId : null);
    renderSession(selectedSession, selectedEventId, null, { scroll: Boolean(options.selectLatest) });
  } catch (error) {
    if (requestSerial !== state.historyRequestSerial) return;
    const detail = String(error?.message || error || "unknown error");
    ui.historyList.innerHTML = `<p class="empty-note">History unavailable: ${escapeHtml(detail)}</p>`;
    logActivity(`Sessions: ${detail}`, "error");
  }
}

function renderSessionList(container, sessions, archived) {
  container.innerHTML = "";
  const filteredSessions = hasTagFilters()
    ? sessions.filter((session) => visibleEvents(session.events).length)
    : sessions;
  if (!filteredSessions.length) {
    if (!archived) {
      container.innerHTML = `<p class="empty-note">${hasTagFilters() ? `No sessions match ${escapeHtml(tagFilterDescription())}.` : "No listening sessions yet."}</p>`;
    }
    return;
  }
  for (const session of filteredSessions) {
    const sessionEvents = visibleEvents(session.events);
    const group = document.createElement("details");
    group.className = `session-group${archived ? " archived" : ""}`;
    group.open = Boolean(session.active || session.id === state.currentSessionId);
    const summary = document.createElement("summary");
    summary.className = "session-summary";
    if (session.active) {
      const active = document.createElement("span");
      active.className = "session-active";
      active.title = "Current session";
      summary.appendChild(active);
    }
    const name = document.createElement("span");
    name.className = "session-name";
    name.textContent = session.name || "Listening session";
    const meta = document.createElement("span");
    meta.className = "session-meta";
    const count = document.createElement("span");
    count.className = "session-count";
    count.textContent = String(hasTagFilters() ? sessionEvents.length : (session.event_count || 0));
    const time = document.createElement("time");
    time.className = "session-time";
    time.dateTime = archived ? (session.archived_at || "") : (session.updated_at || "");
    time.textContent = timeAgo(archived ? session.archived_at : session.updated_at);
    meta.append(count, time);
    const menu = sessionMenu(session, archived);
    summary.append(name, menu, meta);
    summary.addEventListener("click", (interaction) => {
      if (interaction.target.closest(".session-menu")) return;
      showListeningView();
      renderSession(session, null, null);
    });
    group.appendChild(summary);

    const events = document.createElement("div");
    events.className = "session-events";
    for (const event of sessionEvents) {
      const row = document.createElement("div");
      row.className = "session-event-row";
      const button = document.createElement("button");
      button.className = "row-item session-event";
      button.dataset.eventId = String(event.id || "");
      const title = document.createElement("span");
      title.className = "ri-title";
      title.textContent = event.aggregate?.title || event.id || "Listening result";
      button.append(title);
      button.addEventListener("click", (interaction) => {
        interaction.preventDefault();
        interaction.stopPropagation();
        group.open = true;
        showListeningView();
        renderSession(session, event.id, null, { scroll: true });
      });
      row.append(button, listeningMenu(event, session));
      events.appendChild(row);
    }
    group.appendChild(events);
    container.appendChild(group);
  }
}

function sessionMenu(session, archived) {
  const dropdown = document.createElement("span");
  dropdown.className = "dropdown session-menu";
  const button = document.createElement("button");
  button.className = "session-more";
  button.type = "button";
  button.textContent = "•••";
  button.title = `Session actions for ${session.name || "listening session"}`;
  button.setAttribute("aria-label", button.title);
  button.setAttribute("aria-haspopup", "true");
  button.setAttribute("aria-expanded", "false");
  const menu = document.createElement("span");
  menu.className = "drop-menu";
  menu.hidden = true;
  const actions = [];
  if (!archived && !session.active && session.id !== "session_legacy") actions.push(["activate", "Use for new listens"]);
  if (session.id !== "session_legacy") actions.push(["rename", "Rename"]);
  if ((session.events || []).length) {
    actions.push(
      ["remember", "Remember session"],
      ["wiki", "Expand session on Wiki"],
      ["json", "Export session JSON"],
      ["sound", "Generate derived sounds"],
      ["prompt", "Convert session to prompts"],
    );
  }
  actions.push(archived ? ["restore", "Restore session"] : ["archive", "Archive session"]);
  actions.push(["delete", "Delete session"]);
  for (const [action, label] of actions) {
    const item = document.createElement("button");
    item.className = `drop-item${action === "delete" ? " destructive" : ""}`;
    item.dataset.action = action;
    item.textContent = label;
    if (action === "wiki" && !state.sonicfieldAvailable) item.disabled = true;
    if (action === "sound" && !(session.events || []).some(segmentUri)) item.disabled = true;
    menu.appendChild(item);
  }
  menu.addEventListener("click", (interaction) => {
    const item = interaction.target.closest(".drop-item");
    if (!item || item.disabled) return;
    interaction.preventDefault();
    interaction.stopPropagation();
    closeDropdowns();
    handleSessionAction(item.dataset.action, session);
  });
  dropdown.addEventListener("click", (interaction) => {
    interaction.preventDefault();
    interaction.stopPropagation();
  });
  dropdown.append(button, menu);
  wireDropdown(dropdown);
  return dropdown;
}

function listeningMenu(event, session) {
  const dropdown = document.createElement("span");
  dropdown.className = "dropdown session-menu listening-menu";
  const button = document.createElement("button");
  button.className = "session-more";
  button.type = "button";
  button.textContent = "•••";
  button.title = `Listening actions for ${event.aggregate?.title || "listening result"}`;
  button.setAttribute("aria-label", button.title);
  button.setAttribute("aria-haspopup", "true");
  button.setAttribute("aria-expanded", "false");
  const menu = document.createElement("span");
  menu.className = "drop-menu";
  menu.hidden = true;
  const actions = [];
  if (!session.active && session.id !== "session_legacy" && !session.archived) actions.push(["activate", "Use for new listens"]);
  actions.push(
    ["rename", "Rename"],
    ["remember", event.memory?.saved_trace_id ? "Remembered" : "Remember sound"],
    ["wiki", "Expand listening on Wiki"],
    ["json", "Export listening JSON"],
    ["sound", "Generate derived sound"],
    ["prompt", "Convert listening to prompt"],
    ["delete", "Delete listening"],
  );
  for (const [action, label] of actions) {
    const item = document.createElement("button");
    item.className = `drop-item${action === "delete" ? " destructive" : ""}`;
    item.dataset.action = action;
    item.textContent = label;
    if (action === "remember" && event.memory?.saved_trace_id) item.disabled = true;
    if (action === "wiki" && !state.sonicfieldAvailable) item.disabled = true;
    if (action === "sound" && !segmentUri(event)) item.disabled = true;
    menu.appendChild(item);
  }
  menu.addEventListener("click", (interaction) => {
    const item = interaction.target.closest(".drop-item");
    if (!item || item.disabled) return;
    interaction.preventDefault();
    interaction.stopPropagation();
    closeDropdowns();
    handleListeningAction(item.dataset.action, event, session, item);
  });
  dropdown.addEventListener("click", (interaction) => {
    interaction.preventDefault();
    interaction.stopPropagation();
  });
  dropdown.append(button, menu);
  wireDropdown(dropdown);
  return dropdown;
}

async function renameListeningResult(event, session) {
  const initial = event.aggregate?.title || "Listening result";
  const title = window.prompt("Rename listening result", initial)?.replace(/\s+/g, " ").trim();
  if (!title || title === initial) return false;
  await fetchJson(`/sessions/${encodeURIComponent(session.id)}/events/${encodeURIComponent(event.id)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title }),
  });
  event.aggregate = { ...(event.aggregate || {}), title };
  await refreshHistory({ selectSessionId: session.id, eventId: event.id });
  setListenStatus("Listening result renamed.", "");
  return true;
}

async function handleListeningAction(action, event, session, trigger = null) {
  try {
    if (action === "activate") {
      await post(`/sessions/${encodeURIComponent(session.id)}/activate`);
      await refreshHistory({ selectSessionId: session.id, eventId: event.id });
    } else if (action === "rename") {
      await renameListeningResult(event, session);
    } else if (action === "delete") {
      if (!window.confirm(`Delete “${event.aggregate?.title || "this listening result"}”? This removes it from Oída history.`)) return;
      await fetchJson(`/sessions/${encodeURIComponent(session.id)}/events/${encodeURIComponent(event.id)}`, { method: "DELETE" });
      if (state.lastEventId === event.id) state.lastEventId = null;
      setListenStatus("Listening result deleted.", "");
      await refreshHistory({ selectSessionId: session.id });
    } else {
      handleResultAction(action, event, session, trigger);
    }
  } catch (error) {
    setListenStatus(`Listening result: ${error.message}`, "error");
  }
}

async function handleSessionAction(action, session) {
  try {
    if (action === "activate") {
      await post(`/sessions/${encodeURIComponent(session.id)}/activate`);
      state.currentSessionId = session.id;
      await refreshHistory({ selectSessionId: session.id });
    } else if (action === "rename") {
      const name = window.prompt("Rename listening session", session.name || "Listening session");
      if (!name?.trim()) return;
      await fetchJson(`/sessions/${encodeURIComponent(session.id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      await refreshHistory({ selectSessionId: session.id });
    } else if (action === "remember") {
      const result = await post(`/sessions/${encodeURIComponent(session.id)}/remember`);
      setListenStatus(`Remembered ${result.remembered_count || 0} session result${result.remembered_count === 1 ? "" : "s"}.`, "");
      refreshMemory();
    } else if (action === "wiki") {
      const query = (session.events || []).flatMap((event) => [event.aggregate?.title, ...(event.tags || [])]).filter(Boolean).join(" ").slice(0, 600);
      if (ui.wikiModal && typeof ui.wikiModal.showModal === "function" && !ui.wikiModal.open) ui.wikiModal.showModal();
      exploreWiki({ query: query || session.name });
    } else if (action === "json") {
      downloadJson({ session }, `${fileSlug(session.name, "listening-session")}.json`);
    } else if (["sound", "prompt"].includes(action)) {
      batchGermHandoff(session, action);
    } else if (action === "archive") {
      await post(`/sessions/${encodeURIComponent(session.id)}/archive`);
      if (state.currentSessionId === session.id) state.currentSessionId = null;
      setListenStatus("Session archived.", "");
      await refreshHistory();
    } else if (action === "restore") {
      await post(`/sessions/${encodeURIComponent(session.id)}/restore`);
      state.currentSessionId = session.id;
      setListenStatus("Session restored.", "");
      await refreshHistory({ selectSessionId: session.id });
    } else if (action === "delete") {
      if (!window.confirm(`Delete session “${session.name || "Listening session"}” and its ${session.event_count || (session.events || []).length} listening result(s)?`)) return;
      await fetchJson(`/sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
      if (state.currentSessionId === session.id) state.currentSessionId = null;
      setListenStatus("Session deleted.", "");
      await refreshHistory();
    }
  } catch (error) {
    setListenStatus(`Session: ${error.message}`, "error");
  }
}

ui.newSession.addEventListener("click", async (interaction) => {
  interaction.preventDefault();
  interaction.stopPropagation();
  try {
    showListeningView();
    const result = await post("/sessions", {});
    state.currentSessionId = result.session?.id || null;
    setListenStatus("New listening session ready.", "");
    refreshHistory({ selectSessionId: state.currentSessionId });
  } catch (error) {
    setListenStatus(`Session: ${error.message}`, "error");
  }
});

/* ───────────────────────────── memory ───────────────────────────── */

async function refreshMemory(query) {
  try {
    const url = query ? `/akousmata/records?text=${encodeURIComponent(query)}&limit=24` : "/akousmata/records?limit=24";
    const result = await fetchJson(url);
    const records = result.records || [];
    if (!records.length) {
      ui.memoryList.innerHTML = `<p class="empty-note">${query ? "No memories match." : "No saved memories yet. Use Remember on a result or session."}</p>`;
      return;
    }
    ui.memoryList.innerHTML = "";
    records.slice(0, 16).forEach((record) => {
      const wrapper = document.createElement("div");
      wrapper.className = "memory-row";
      const row = document.createElement("button");
      row.className = "row-item memory-item";
      const title = document.createElement("span");
      title.className = "ri-title";
      title.textContent = record.summary || record.akousma_id;
      const meta = document.createElement("span");
      meta.className = "ri-meta";
      meta.textContent = timeAgo(record.created_at);
      row.append(title, meta);
      row.addEventListener("click", () => openAkousma(record.akousma_id));
      wrapper.append(row, memoryMenu(record));
      ui.memoryList.appendChild(wrapper);
    });
  } catch (error) {
    ui.memoryList.innerHTML = `<p class="empty-note">Memory unavailable: ${escapeHtml(error.message)}</p>`;
  }
}

function linkedListening(record) {
  const session = [...state.sessions, ...state.archivedSessions]
    .find((candidate) => candidate.id === record.session_id);
  const event = session?.events?.find((candidate) => candidate.id === record.event_id) || null;
  return { session, event };
}

function memoryMenu(record) {
  const dropdown = document.createElement("span");
  dropdown.className = "dropdown session-menu memory-menu";
  const button = document.createElement("button");
  button.className = "session-more";
  button.type = "button";
  button.textContent = "•••";
  button.title = `Memory actions for ${record.summary || record.akousma_id}`;
  button.setAttribute("aria-label", button.title);
  button.setAttribute("aria-haspopup", "true");
  button.setAttribute("aria-expanded", "false");
  const menu = document.createElement("span");
  menu.className = "drop-menu";
  menu.hidden = true;
  const linked = linkedListening(record);
  const actions = [
    ["activate", "Use for new listens"],
    ["rename", "Rename"],
    ["remember", "Remembered"],
    ["wiki", "Expand listening on Wiki"],
    ["json", "Export memory JSON"],
    ["sound", "Generate derived sound"],
    ["prompt", "Convert listening to prompt"],
    ["delete", "Delete memory"],
  ];
  for (const [action, label] of actions) {
    const item = document.createElement("button");
    item.className = `drop-item${action === "delete" ? " destructive" : ""}`;
    item.dataset.action = action;
    item.textContent = label;
    if (action === "remember") item.disabled = true;
    if (action === "activate" && (!record.session_id || record.session_id === "session_legacy" || linked.session?.archived)) item.disabled = true;
    if (action === "wiki" && !state.sonicfieldAvailable) item.disabled = true;
    menu.appendChild(item);
  }
  menu.addEventListener("click", (interaction) => {
    const item = interaction.target.closest(".drop-item");
    if (!item || item.disabled) return;
    interaction.preventDefault();
    interaction.stopPropagation();
    closeDropdowns();
    handleMemoryAction(item.dataset.action, record);
  });
  dropdown.addEventListener("click", (interaction) => {
    interaction.preventDefault();
    interaction.stopPropagation();
  });
  dropdown.append(button, menu);
  wireDropdown(dropdown);
  return dropdown;
}

async function handleMemoryAction(action, record) {
  try {
    const linked = linkedListening(record);
    if (action === "activate") {
      await post(`/sessions/${encodeURIComponent(record.session_id)}/activate`);
      await refreshHistory({ selectSessionId: record.session_id, eventId: record.event_id });
    } else if (action === "rename") {
      const name = window.prompt("Rename memory", record.summary || "Listening memory")?.replace(/\s+/g, " ").trim();
      if (!name || name === record.summary) return;
      await fetchJson(`/akousmata/records/${encodeURIComponent(record.akousma_id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ summary: name }),
      });
      setListenStatus("Memory renamed.", "");
      await refreshMemory(ui.memorySearch.value.trim() || undefined);
    } else if (action === "wiki") {
      if (ui.wikiModal && typeof ui.wikiModal.showModal === "function" && !ui.wikiModal.open) ui.wikiModal.showModal();
      exploreWiki(linked.event ? { event: linked.event } : { query: record.summary || record.akousma_id });
    } else if (action === "json") {
      const data = await fetchJson(`/akousmata/records/${encodeURIComponent(record.akousma_id)}`);
      downloadJson(data, `${fileSlug(record.summary, "listening-memory")}.json`);
    } else if (["sound", "prompt"].includes(action)) {
      const data = await fetchJson(`/germ/link?akousma_id=${encodeURIComponent(record.akousma_id)}&mode=${action}`);
      window.open(data.germ_url, "_blank", "noopener");
    } else if (action === "delete") {
      if (!window.confirm(`Delete memory “${record.summary || record.akousma_id}”? Its referenced audio will not be erased.`)) return;
      await fetchJson(`/akousmata/records/${encodeURIComponent(record.akousma_id)}`, { method: "DELETE" });
      setListenStatus("Memory deleted.", "");
      await refreshMemory(ui.memorySearch.value.trim() || undefined);
    }
  } catch (error) {
    setListenStatus(`Memory: ${error.message}`, "error");
  }
}

ui.memoryGo.addEventListener("click", () => refreshMemory(ui.memorySearch.value.trim() || undefined));
ui.memorySearch.addEventListener("keydown", (event) => {
  if (event.key === "Enter") refreshMemory(ui.memorySearch.value.trim() || undefined);
});

/* ─────────── memory detail (shared Akousmata store underneath) ───────── */

const akousmataUi = {
  modal: document.getElementById("akousmataModal"),
  title: document.getElementById("akousmataTitle"),
  detail: document.getElementById("akousmataDetail"),
};

function showListeningView() {
  if (akousmataUi.modal) akousmataUi.modal.hidden = true;
  closeConversation();
}

async function openAkousma(akousmaId) {
  closeConversation();
  try {
    const data = await fetchJson(`/akousmata/records/${encodeURIComponent(akousmaId)}`);
    const record = data.record;
    akousmataUi.title.textContent = data.summary || akousmaId;
    const rows = [];
    const provenance = record.provenance || {};
    rows.push(`<p class="memory-meta">${escapeHtml(akousmaId)} · ${escapeHtml(provenance.originating_app || "?")} · ${escapeHtml(provenance.origin || "?")} · ${escapeHtml((record.created_at || "").slice(0, 16).replace("T", " "))}</p>`);
    if (data.audio_available) rows.push(`<section class="memory-block"><div class="memory-kicker">Audio</div><audio controls style="width:100%" src="/akousmata/audio/${encodeURIComponent(akousmaId)}"></audio></section>`);
    const listening = record.listening || {};
    for (const namespace of Object.keys(listening).sort()) {
      const entry = listening[namespace];
      if (typeof entry !== "object" || entry === null) continue;
      const payload = entry.payload && typeof entry.payload === "object" ? entry.payload : entry;
      const text = entry.summary || payload.caption || payload.summary || payload.main_reading || payload.notes || "";
      rows.push(`<section class="memory-block"><div class="memory-kicker">${escapeHtml(namespace)}${entry.contract ? ` · ${escapeHtml(entry.contract)}` : ""}</div><p>${escapeHtml(String(text).slice(0, 800)) || "<em>structured payload</em>"}</p></section>`);
    }
    const link = (ref) => `<a href="#" data-akousma="${escapeHtml(ref.akousma_id)}" class="${ref.missing ? "ri-meta" : ""}">${escapeHtml(ref.summary || ref.akousma_id)}</a>`;
    if (data.parents.length) rows.push(`<section class="memory-block"><div class="memory-kicker">Made from</div><div class="memory-links">${data.parents.map(link).join("")}</div></section>`);
    if (data.children.length) rows.push(`<section class="memory-block"><div class="memory-kicker">Became</div><div class="memory-links">${data.children.map(link).join("")}</div></section>`);
    if (data.related.length) {
      rows.push(`<section class="memory-block"><div class="memory-kicker">Kinship</div><div class="memory-links">${data.related.map((item) => `<span>${escapeHtml((item.type || "").replaceAll("_", " "))} ${item.direction === "incoming" ? "←" : "→"} ${link(item)}</span>`).join("")}</div></section>`);
    }
    if ((record.tags || []).length) rows.push(`<section class="memory-block"><div class="memory-kicker">Tags</div><p>${record.tags.map((tag) => `#${escapeHtml(tag)}`).join(" · ")}</p></section>`);
    rows.push(
      `<div class="memory-actions">` +
      ["sound", "prompt", "lineage"].map((mode) => `<button class="pill-button small" data-germ-mode="${mode}" data-germ-id="${escapeHtml(akousmaId)}">germ: ${mode}</button>`).join("") +
      `</div>`,
    );
    akousmataUi.detail.innerHTML = rows.join("");
    akousmataUi.detail.querySelectorAll("a[data-akousma]").forEach((anchor) => {
      anchor.addEventListener("click", (event) => {
        event.preventDefault();
        openAkousma(anchor.dataset.akousma);
      });
    });
    akousmataUi.detail.querySelectorAll("button[data-germ-mode]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const data = await fetchJson(`/germ/link?akousma_id=${encodeURIComponent(button.dataset.germId)}&mode=${button.dataset.germMode}`);
          window.open(data.germ_url, "_blank", "noopener");
        } catch (error) {
          button.textContent = "germ unavailable";
        }
      });
    });
    akousmataUi.modal.hidden = false;
  } catch (error) {
    akousmataUi.title.textContent = "Memory unavailable";
    akousmataUi.detail.innerHTML = `<p class="empty-note">${escapeHtml(error.message)}</p>`;
    akousmataUi.modal.hidden = false;
  }
}

/* ───────────────────────── rules (covenants underneath) ─────────────────
   The user-facing surface calls these Rules. The daemon keeps its covenant
   vocabulary and file format so existing documents remain compatible. */

const covenantUi = {
  note: el("covenantNote"),
  select: el("covenantSelect"),
  toggle: el("covenantToggle"),
  editor: el("covenantEditor"),
  name: el("covenantName"),
  text: el("covenantText"),
  save: el("covenantSave"),
  saveActivate: el("covenantSaveActivate"),
  summary: el("covenantSummary"),
};

async function refreshCovenant() {
  if (!covenantUi.select) return;
  try {
    const priorSelection = covenantUi.select.value;
    const data = await fetchJson("/covenant");
    const active = data.active;
    covenantUi.select.innerHTML = "";
    for (const name of data.available || []) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      covenantUi.select.append(option);
    }
    if (!(data.available || []).length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No rules yet — open Edit rules to write one";
      covenantUi.select.append(option);
    }
    const activeName = active?.name || active?.id || "";
    const availableNames = new Set(data.available || []);
    if (activeName && availableNames.has(activeName)) covenantUi.select.value = activeName;
    else if (priorSelection && availableNames.has(priorSelection)) covenantUi.select.value = priorSelection;
    covenantUi.toggle.checked = Boolean(active);
    if (active) {
      covenantUi.note.textContent = `On · ${activeName}`;
      const rules = active.rules?.length || 0;
      const commitments = active.commitments?.length || 0;
      covenantUi.summary.textContent =
        `${rules} enforceable rule${rules === 1 ? "" : "s"} · ${commitments} commitment${commitments === 1 ? "" : "s"} carried` +
        (active.extends?.length ? ` · stands on ${active.extends.join(", ")}` : "");
    } else {
      covenantUi.note.textContent = "Off — opted in, never imposed";
      covenantUi.summary.textContent = covenantUi.select.value ? "Choose the toggle to apply these rules." : "";
    }
  } catch (error) {
    covenantUi.note.textContent = String(error.message || error);
    logActivity(`Rules: ${error.message || error}`, "error");
  }
}

async function loadSelectedCovenant() {
  const name = covenantUi.select.value;
  if (!name) return;
  try {
    const data = await fetchJson(`/covenant/${encodeURIComponent(name)}`);
    covenantUi.name.value = data.name || name;
    covenantUi.text.value = data.text || "";
  } catch (error) {
    logActivity(`Rules document: ${error.message || error}`, "error");
  }
}

function wireCovenant() {
  if (!covenantUi.select) return;
  covenantUi.toggle.addEventListener("change", async () => {
    const name = covenantUi.toggle.checked ? covenantUi.select.value : null;
    if (covenantUi.toggle.checked && !name) {
      covenantUi.toggle.checked = false;
      covenantUi.note.textContent = "Write or select a rules document first.";
      return;
    }
    try {
      await post("/covenant/activate", { name });
      await refreshCovenant();
    } catch (error) {
      covenantUi.toggle.checked = !covenantUi.toggle.checked;
      covenantUi.note.textContent = String(error.message || error);
    }
  });
  covenantUi.select.addEventListener("change", async () => {
    await loadSelectedCovenant();
    if (!covenantUi.toggle.checked) return;
    try {
      await post("/covenant/activate", { name: covenantUi.select.value || null });
      await refreshCovenant();
    } catch (error) {
      covenantUi.note.textContent = String(error.message || error);
    }
  });
  covenantUi.select.addEventListener("focus", () => {
    if (!covenantUi.text.value) loadSelectedCovenant();
  });
  const saveCovenant = async (activate) => {
    const name = covenantUi.name.value.trim();
    const text = covenantUi.text.value;
    if (!name || !text.trim()) {
      covenantUi.note.textContent = "A name and rules text are required.";
      return;
    }
    try {
      await fetchJson("/covenant", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, text, activate }),
      });
      await refreshCovenant();
      covenantUi.select.value = name;
      if (activate) covenantUi.toggle.checked = true;
    } catch (error) {
      covenantUi.note.textContent = String(error.message || error);
    }
  };
  covenantUi.save.addEventListener("click", () => saveCovenant(false));
  covenantUi.saveActivate.addEventListener("click", () => saveCovenant(true));
}
wireCovenant();

/* ───────────────────── reasoning providers + profiles ─────────────────── */

const REASONING_ROLES = [
  ["fast_perception", "Fast perception", "Quick captions, transcripts, and event timelines."],
  ["deep_perception", "Deep perception", "Slower musical, contextual, and analytical listening."],
  ["transcription", "Transcription", "Speech-to-text and diarization passes."],
  ["music_analysis", "Music analysis", "Music-specialized structure, instrumentation, and production listening."],
  ["conversation", "Conversation", "Dialogue grounded in the selected listening result."],
  ["targeted_relisten", "Targeted re-listen", "One focused local pass when the evidence needs detail."],
];

const MODEL_LIBRARY_PROVIDER_IDS = [
  "oida_moss",
  "local_audio",
  "google",
  "alibaba",
  "nvidia",
  "openrouter",
  "local_structured",
];

const DEFAULT_REASONING_PROFILE = {
  id: "grounded_companion",
  name: "Grounded companion",
  tone: "warm",
  depth: "balanced",
  initiative: "suggest_followups",
  focus: [],
  language: "auto",
  custom_instructions: "",
};

function collectionFromPayload(payload, key) {
  const root = payload?.[key] ?? payload?.data?.[key] ?? payload;
  if (Array.isArray(root)) return root;
  if (root && typeof root === "object") {
    return Object.entries(root).map(([id, value]) => (
      value && typeof value === "object" ? { id, ...value } : { id, value }
    ));
  }
  return [];
}

function normalizeReasoningSettings(payload) {
  const raw = payload?.settings || payload?.data?.settings || payload || {};
  let profiles = raw.profiles;
  if (!Array.isArray(profiles) && profiles && typeof profiles === "object") {
    profiles = Object.entries(profiles).map(([id, profile]) => ({ id, ...(profile || {}) }));
  }
  profiles = (profiles || []).map((profile, index) => ({
    ...DEFAULT_REASONING_PROFILE,
    ...(profile || {}),
    id: String(profile?.id || `profile_${index + 1}`),
    name: String(profile?.name || profile?.id || `Profile ${index + 1}`),
    focus: Array.isArray(profile?.focus) ? profile.focus.map(String) : [],
  }));
  if (!profiles.length) profiles = [{ ...DEFAULT_REASONING_PROFILE }];
  const assignments = raw.role_assignments || raw.roles || {};
  const roleAssignments = {};
  for (const [role] of REASONING_ROLES) {
    const assignment = assignments?.[role];
    roleAssignments[role] = typeof assignment === "string"
      ? { provider_id: null, model_id: assignment }
      : { provider_id: null, model_id: null, ...(assignment || {}) };
  }
  const conversation = roleAssignments.conversation;
  if (!conversation.provider_id && raw.active_provider_id) conversation.provider_id = raw.active_provider_id;
  if (!conversation.model_id && raw.active_model_id) conversation.model_id = raw.active_model_id;
  const activeProfile = profiles.some((profile) => profile.id === raw.active_profile_id)
    ? raw.active_profile_id
    : profiles[0].id;
  const configuredProviders = raw.providers && typeof raw.providers === "object" ? raw.providers : {};
  const enabledProviderIds = Array.isArray(raw.enabled_provider_ids)
    ? raw.enabled_provider_ids.map(String)
    : Object.entries(configuredProviders).filter(([, provider]) => provider?.enabled).map(([id]) => id);
  const providerOptions = { ...(raw.provider_options || {}) };
  for (const [id, provider] of Object.entries(configuredProviders)) {
    if (provider?.base_url || provider?.default_model || provider?.options) {
      providerOptions[id] = {
        ...(provider.options || {}),
        ...(providerOptions[id] || {}),
        ...(provider.base_url ? { base_url: provider.base_url } : {}),
        ...(provider.default_model ? { default_model: provider.default_model } : {}),
      };
    }
  }
  return {
    version: raw.version || raw.contract || "oida/reasoning-settings/v0.2",
    active_provider_id: raw.active_provider_id || conversation.provider_id || "local_structured",
    active_model_id: raw.active_model_id || conversation.model_id || null,
    active_profile_id: activeProfile,
    enabled_provider_ids: [...new Set(["local_structured", "oida_moss", ...enabledProviderIds])],
    role_assignments: roleAssignments,
    profiles,
    provider_options: providerOptions,
    include_transcript: Boolean(raw.include_transcript ?? raw.share_transcript),
    include_memory_content: Boolean(raw.include_memory_content ?? raw.share_memory_content),
    allow_targeted_relisten: raw.allow_targeted_relisten !== false,
    allow_external_audio: Boolean(raw.allow_external_audio),
    resources: raw.resources || null,
    incognito: Boolean(raw.incognito),
  };
}

function normalizeReasoningProvider(provider) {
  const id = String(provider?.id || provider?.provider_id || provider?.name || "provider");
  const locality = String(provider?.locality || provider?.data_locality || (id.startsWith("local") ? "local" : "unknown"));
  const detail = String(provider?.detail || provider?.note || "");
  const installed = provider?.installed ?? !/(not installed|not found|missing executable)/i.test(detail);
  return {
    ...provider,
    id,
    label: String(provider?.label || provider?.display_name || provider?.name || id.replaceAll("_", " ")),
    locality,
    installed,
    authenticated: provider?.authenticated,
    reachable: provider?.reachable ?? provider?.available,
    enabled: Boolean(provider?.enabled),
  };
}

function reasoningProviderById(providerId) {
  return state.reasoningProviders.find((provider) => provider.id === providerId) || null;
}

function reasoningProviderEnabled(provider) {
  if (!provider) return false;
  return state.reasoningSettings?.enabled_provider_ids?.includes(provider.id) || provider.enabled;
}

function localityLabel(provider) {
  const locality = provider?.locality || "unknown";
  if (locality === "local") return "local";
  if (locality === "external") return "external";
  return "external / unknown";
}

function isLoopbackDashboard() {
  return ["127.0.0.1", "localhost", "::1", "[::1]"].includes(window.location.hostname);
}

function setReasoningStatus(text, tone = "") {
  if (!ui.reasoningStatus) return;
  ui.reasoningStatus.textContent = text || "";
  ui.reasoningStatus.className = `reasoning-banner${tone ? ` ${tone}` : ""}`;
}

async function refreshReasoning(force = false) {
  if (!ui.reasoningProviders || state.reasoningBusy || (state.reasoningLoaded && !force)) {
    if (state.reasoningLoaded && ui.conversationPanel && !ui.conversationPanel.hidden) populateConversationControls();
    return;
  }
  state.reasoningBusy = true;
  setReasoningStatus("Loading reasoning providers…", "active");
  try {
    const [settingsPayload, providersPayload] = await Promise.all([
      fetchJson("/reasoning/settings"),
      fetchJson("/reasoning/providers"),
    ]);
    state.reasoningSettings = normalizeReasoningSettings(settingsPayload);
    state.reasoningProviders = collectionFromPayload(providersPayload, "providers").map(normalizeReasoningProvider);
    if (!state.reasoningProviders.some((provider) => provider.id === "local_structured")) {
      state.reasoningProviders.unshift(normalizeReasoningProvider({
        id: "local_structured",
        label: "Oída local structured",
        locality: "local",
        installed: true,
        authenticated: true,
        reachable: true,
        enabled: true,
        note: "Deterministic evidence-grounded conversation",
      }));
    }
    state.reasoningLoaded = true;
    // Mark the library busy before the first render so Settings never flashes a
    // misleading "no catalog" state while model discovery is starting.
    void refreshReasoningModelLibrary(force);
    renderReasoningSettings();
    populateConversationControls();
    const enabled = state.reasoningProviders.filter(reasoningProviderEnabled).length;
    setReasoningStatus(
      state.reasoningSettings.incognito
        ? "Incognito is active — reasoning is forced local and conversations are not retained."
        : `${enabled} provider${enabled === 1 ? "" : "s"} enabled · filtered evidence is the default packet${state.reasoningSettings.allow_external_audio ? " · external audio opt-in is on" : ""}.`,
      state.reasoningSettings.incognito ? "warning" : "ready",
    );
  } catch (error) {
    state.reasoningLoaded = false;
    setReasoningStatus(`Reasoning settings unavailable: ${error.message}`, "error");
    logActivity(`Reasoning: ${error.message}`, "error");
  } finally {
    state.reasoningBusy = false;
  }
}

function renderReasoningSettings() {
  renderReasoningProviders();
  renderReasoningModelLibrary();
  renderReasoningRoles();
  renderReasoningProfiles();
  renderReasoningResources();
  ui.reasoningTranscript.checked = Boolean(state.reasoningSettings.include_transcript);
  ui.reasoningMemory.checked = Boolean(state.reasoningSettings.include_memory_content);
  ui.reasoningRelisten.checked = state.reasoningSettings.allow_targeted_relisten !== false;
  ui.reasoningExternalAudio.checked = Boolean(state.reasoningSettings.allow_external_audio);
  for (const input of [ui.reasoningTranscript, ui.reasoningMemory, ui.reasoningRelisten, ui.reasoningExternalAudio]) {
    input.disabled = Boolean(state.reasoningSettings.incognito && input !== ui.reasoningRelisten);
  }
}

function renderReasoningResources() {
  if (!ui.reasoningResources) return;
  const resources = state.reasoningSettings?.resources;
  if (!resources) {
    ui.reasoningResources.hidden = true;
    return;
  }
  ui.reasoningResources.hidden = false;
  const physical = Number.isFinite(resources.physical_ram_gb) ? `${resources.physical_ram_gb} GB physical RAM` : "physical RAM unknown";
  const peak = Number.isFinite(resources.estimated_peak_ram_gb) ? ` · estimated peak ${resources.estimated_peak_ram_gb} GB` : "";
  const models = Array.isArray(resources.selected_local_models) ? resources.selected_local_models.length : 0;
  const warnings = Array.isArray(resources.warnings) ? resources.warnings : [];
  ui.reasoningResources.className = `reasoning-banner reasoning-resources ${resources.level === "exceeds" ? "error" : (warnings.length ? "warning" : "ready")}`;
  ui.reasoningResources.replaceChildren();
  const summary = document.createElement("strong");
  summary.textContent = `Local model budget · ${physical}${peak} · ${resources.resident_mode || "single"} residency · ${models} selected`;
  ui.reasoningResources.appendChild(summary);
  if (warnings.length) {
    const list = document.createElement("ul");
    for (const warning of warnings.slice(0, 5)) {
      const item = document.createElement("li");
      item.textContent = warning;
      list.appendChild(item);
    }
    ui.reasoningResources.appendChild(list);
  } else {
    const note = document.createElement("span");
    note.textContent = resources.estimate_note || "Selected local models fit the planning estimate.";
    ui.reasoningResources.appendChild(note);
  }
}

function providerStatusText(provider) {
  if (provider.note || provider.detail) return provider.note || provider.detail;
  if (provider.installed === false) return "Not installed on this computer.";
  if (provider.authenticated === false) return "Installed · sign in with the provider before use.";
  if (provider.reachable === false) return "Configured but not reachable.";
  if (provider.reachable === true) return "Ready for a reasoning request.";
  return "Available · probe to check readiness.";
}

function reasoningProviderIntegrationText(provider) {
  if (provider.id === "local_audio") {
    return "Oída calls this loopback endpoint for assigned audio roles. The model runtime stays user-managed and audio does not leave this computer.";
  }
  if (provider.id === "ollama" || provider.id === "openai_compatible") {
    return "Oída sends the filtered evidence packet to the configured endpoint for assigned conversation roles. Declare audio capability only for an endpoint that actually accepts audio.";
  }
  if (provider.kind === "host_cli") {
    return "Oída uses the existing CLI installation and its own authenticated session. The host receives a grounded prompt with tools disabled for the turn.";
  }
  if (provider.locality === "external") {
    return "Oída sends filtered listening evidence through this API only when the provider is enabled. Sending audio additionally requires the separate External audio models opt-in.";
  }
  return "Enable this provider, select a model, and assign it to one or more roles below.";
}

function reasoningModelAvailability(model, provider = reasoningProviderById(model?.provider_id)) {
  const metadata = model?.metadata || {};
  const local = model?.locality === "local" || provider?.locality === "local";
  const installed = metadata.installed === true;
  const discovered = metadata.available === true || metadata.discovered === true;
  const enabled = reasoningProviderEnabled(provider);
  if (metadata.selectable === false) {
    return {
      key: "dependency",
      label: "Setup dependency",
      short: "dependency",
      tone: "muted",
      ready: installed || discovered,
      detail: installed
        ? "Installed setup dependency; it is not assignable to an Oída role."
        : "Supported setup dependency; it is not a standalone Oída role model.",
    };
  }
  if (local) {
    if (installed && metadata.loaded === true) {
      return {
        key: "loaded",
        label: "Loaded",
        short: "loaded",
        tone: "ready",
        ready: true,
        detail: "Installed and currently resident in the local Oída runtime.",
      };
    }
    if (installed) {
      return {
        key: "installed",
        label: "Installed",
        short: "installed",
        tone: "ready",
        ready: true,
        detail: "Weights were detected on this computer. The model loads only when its assigned role needs it.",
      };
    }
    if (discovered && enabled && provider?.reachable !== false) {
      return {
        key: "hosted",
        label: "Available on local host",
        short: "local host ready",
        tone: "ready",
        ready: true,
        detail: "The configured loopback model host reports this model as available.",
      };
    }
    return {
      key: "supported",
      label: "Supported · not installed",
      short: "supported, not installed",
      tone: "warning",
      ready: false,
      detail: provider?.id === "local_audio"
        ? "Oída supports this model, but it was not detected through the configured local audio host."
        : "Oída supports this checkpoint, but its weights were not detected on this computer.",
    };
  }
  if (enabled && provider?.reachable === true && provider?.authenticated !== false) {
    return {
      key: "api",
      label: "Available via API",
      short: "API ready",
      tone: "ready",
      ready: true,
      detail: "The cloud provider is enabled and reachable. Audio is still sent only with the separate external-audio opt-in.",
    };
  }
  if (enabled) {
    return {
      key: "provider_unavailable",
      label: "Provider unavailable",
      short: "provider unavailable",
      tone: "warning",
      ready: false,
      detail: provider?.authenticated === false
        ? "This model is supported, but the provider needs a credential or sign-in."
        : "This model is supported, but the enabled provider is not currently reachable.",
    };
  }
  return {
    key: "cloud_supported",
    label: "Supported · setup required",
    short: "supported, setup required",
    tone: "warning",
    ready: false,
    detail: "Oída supports this API model, but its provider is not enabled and configured yet.",
  };
}

function reasoningModelSetupText(model, provider) {
  if (provider?.id === "local_structured") {
    return "Bundled with Oída. It uses the filtered evidence packet without loading a model or calling a network service.";
  }
  if (provider?.id === "oida_moss") {
    return "Place the complete checkpoint directory in Oída’s weights/ folder and restart Oída. Automatic Hugging Face lookup remains off unless OIDA_ALLOW_HF_HUB=1 is explicitly set.";
  }
  if (model?.locality === "local" || provider?.locality === "local") {
    return "Run the model behind an OpenAI-compatible audio endpoint on loopback, set that endpoint under Local audio model host, enable the provider, then assign the model to a compatible role.";
  }
  if (provider?.id === "openrouter") {
    return "Connect OpenRouter with OAuth or save an API key, enable the provider, then assign the model. External audio remains blocked until its separate opt-in is enabled.";
  }
  return `Save an API key for ${provider?.label || model?.provider_id || "the provider"}, enable the provider, then assign the model. Oída sends filtered evidence by default; raw audio requires the separate external-audio opt-in.`;
}

function reasoningModelLibraryProviderIds() {
  const assigned = Object.values(state.reasoningSettings?.role_assignments || {})
    .map((assignment) => assignment?.provider_id)
    .filter(Boolean);
  const enabled = state.reasoningProviders.filter(reasoningProviderEnabled).map((provider) => provider.id);
  return [...new Set([...MODEL_LIBRARY_PROVIDER_IDS, ...assigned, ...enabled])]
    .filter((providerId) => Boolean(reasoningProviderById(providerId)));
}

function reasoningModelLibraryEntries() {
  const entries = [];
  const seen = new Set();
  for (const providerId of reasoningModelLibraryProviderIds()) {
    for (const model of state.reasoningModels.get(providerId) || []) {
      const metadata = model.metadata || {};
      if (metadata.integration_status === "configured_alias") continue;
      if (!(metadata.catalog || metadata.discovered || metadata.available || metadata.installed || metadata.configured_default)) continue;
      const key = `${providerId}:${model.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      entries.push({ ...model, provider_id: model.provider_id || providerId });
    }
  }
  return entries;
}

async function refreshReasoningModelLibrary(force = false) {
  if (!ui.reasoningModelLibrary || state.reasoningModelLibraryBusy) return;
  state.reasoningModelLibraryBusy = true;
  ui.reasoningModelSummary.textContent = "Checking installed, hosted, and supported models…";
  try {
    await Promise.all(reasoningModelLibraryProviderIds().map((providerId) => loadReasoningModels(providerId, force)));
  } finally {
    state.reasoningModelLibraryBusy = false;
    renderReasoningModelLibrary();
  }
}

function openReasoningProviderConfig(providerId) {
  const card = [...ui.reasoningProviders.children].find((item) => item.dataset.providerId === providerId);
  if (!card) return;
  const details = card.querySelector(".reasoning-provider-config");
  if (details) details.open = true;
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  card.classList.add("attention");
  setTimeout(() => card.classList.remove("attention"), 1200);
}

function assignReasoningModelToRole(model, role) {
  state.reasoningSettings.role_assignments[role] = {
    provider_id: model.provider_id,
    model_id: model.id,
  };
  if (role === "conversation") {
    state.reasoningSettings.active_provider_id = model.provider_id;
    state.reasoningSettings.active_model_id = model.id;
  }
  renderReasoningRoles();
  ui.reasoningSaveNote.textContent = `${model.label || model.name || model.id} assigned to ${REASONING_ROLES.find(([id]) => id === role)?.[1] || role}. Save to apply.`;
  ui.reasoningRoles.querySelector(`[data-role="${role}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function reasoningModelRequirementLabels(model) {
  const metadata = model.metadata || {};
  const labels = [];
  if (Number.isFinite(metadata.weight_gb)) labels.push(`${metadata.weight_gb} GB weights`);
  if (Number.isFinite(metadata.min_ram_gb)) labels.push(`${metadata.min_ram_gb} GB minimum RAM`);
  if (Number.isFinite(metadata.recommended_ram_gb)) labels.push(`${metadata.recommended_ram_gb} GB recommended`);
  if (metadata.license) labels.push(metadata.license);
  for (const platform of Array.isArray(metadata.platforms) ? metadata.platforms : []) labels.push(platform);
  if (metadata.runtime) labels.push(String(metadata.runtime).replaceAll("_", " "));
  return labels;
}

function safeExternalModelURL(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" ? url.href : null;
  } catch (_) {
    return null;
  }
}

function renderReasoningModelLibrary() {
  if (!ui.reasoningModelLibrary || !ui.reasoningModelSummary) return;
  const allEntries = reasoningModelLibraryEntries();
  if (!allEntries.length) {
    ui.reasoningModelLibrary.replaceChildren();
    if (!state.reasoningModelLibraryBusy) ui.reasoningModelSummary.textContent = "No model catalog was returned by the daemon.";
    return;
  }
  const installedCount = allEntries.filter((model) => model.metadata?.installed === true).length;
  const readyCount = allEntries.filter((model) => reasoningModelAvailability(model).ready).length;
  const cloudCount = allEntries.filter((model) => model.locality === "external").length;
  ui.reasoningModelSummary.textContent = `${installedCount} installed locally · ${readyCount} installed or ready · ${allEntries.length} supported · ${cloudCount} cloud/API`;

  const filter = ui.reasoningModelFilter?.value || "all";
  const entries = allEntries.filter((model) => {
    const provider = reasoningProviderById(model.provider_id);
    const local = model.locality === "local" || provider?.locality === "local";
    if (filter === "ready") return reasoningModelAvailability(model, provider).ready;
    if (filter === "local") return local;
    if (filter === "cloud") return !local;
    return true;
  });
  ui.reasoningModelLibrary.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "settings-note reasoning-model-empty";
    empty.textContent = "No models match this filter.";
    ui.reasoningModelLibrary.appendChild(empty);
    return;
  }

  const groups = new Map();
  for (const model of entries) {
    if (!groups.has(model.provider_id)) groups.set(model.provider_id, []);
    groups.get(model.provider_id).push(model);
  }
  for (const providerId of reasoningModelLibraryProviderIds()) {
    const models = groups.get(providerId);
    if (!models?.length) continue;
    const provider = reasoningProviderById(providerId);
    models.sort((left, right) => {
      const readyDelta = Number(reasoningModelAvailability(right, provider).ready) - Number(reasoningModelAvailability(left, provider).ready);
      return readyDelta || String(left.label || left.name || left.id).localeCompare(String(right.label || right.name || right.id));
    });
    const group = document.createElement("details");
    group.className = "reasoning-model-group";
    group.open = providerId === "oida_moss" || models.some((model) => model.metadata?.installed === true);
    const groupSummary = document.createElement("summary");
    const groupTitle = document.createElement("strong");
    groupTitle.textContent = provider?.label || providerId.replaceAll("_", " ");
    const groupCount = document.createElement("span");
    const detected = models.filter((model) => reasoningModelAvailability(model, provider).ready).length;
    groupCount.textContent = `${detected} ready · ${models.length} supported`;
    groupSummary.append(groupTitle, groupCount);
    const list = document.createElement("div");
    list.className = "reasoning-model-list";

    for (const model of models) {
      const metadata = model.metadata || {};
      const availability = reasoningModelAvailability(model, provider);
      const card = document.createElement("details");
      card.className = `reasoning-model-card ${availability.tone}`;
      card.open = metadata.installed === true;
      const summary = document.createElement("summary");
      const identity = document.createElement("span");
      identity.className = "reasoning-model-identity";
      const name = document.createElement("strong");
      name.textContent = model.label || model.name || model.id;
      const id = document.createElement("small");
      id.textContent = model.id;
      identity.append(name, id);
      const status = document.createElement("span");
      status.className = `reasoning-model-status ${availability.tone}`;
      status.textContent = availability.label;
      summary.append(identity, status);

      const body = document.createElement("div");
      body.className = "reasoning-model-body";
      const availabilityNote = document.createElement("p");
      availabilityNote.className = "reasoning-model-availability";
      availabilityNote.textContent = availability.detail;
      body.appendChild(availabilityNote);
      if (metadata.notes) {
        const notes = document.createElement("p");
        notes.textContent = metadata.notes;
        body.appendChild(notes);
      }
      const setup = document.createElement("p");
      setup.className = "reasoning-model-setup";
      setup.textContent = reasoningModelSetupText(model, provider);
      body.appendChild(setup);

      const requirements = reasoningModelRequirementLabels(model);
      if (requirements.length) {
        const requirementList = document.createElement("div");
        requirementList.className = "reasoning-model-requirements";
        for (const label of requirements) {
          const item = document.createElement("span");
          item.textContent = label;
          requirementList.appendChild(item);
        }
        body.appendChild(requirementList);
      }

      const actions = document.createElement("div");
      actions.className = "reasoning-model-actions";
      const sourceURL = safeExternalModelURL(metadata.source_url);
      if (sourceURL) {
        const source = document.createElement("a");
        source.className = "pill-button small";
        source.href = sourceURL;
        source.target = "_blank";
        source.rel = "noopener";
        source.textContent = model.locality === "external"
          ? "API documentation"
          : (sourceURL.includes("huggingface.co") ? "Get on Hugging Face" : "Setup guide");
        actions.appendChild(source);
      }
      if (!["oida_moss", "local_structured"].includes(providerId)) {
        const configure = document.createElement("button");
        configure.type = "button";
        configure.className = "pill-button small";
        configure.textContent = "Configure provider";
        configure.addEventListener("click", () => openReasoningProviderConfig(providerId));
        actions.appendChild(configure);
      }
      const supportedRoles = REASONING_ROLES.filter(([role]) => (
        Array.isArray(model.capabilities) && model.capabilities.includes(role)
      ));
      if (providerId === "local_structured" && !supportedRoles.some(([role]) => role === "conversation")) {
        supportedRoles.push(REASONING_ROLES.find(([role]) => role === "conversation"));
      }
      if (metadata.selectable !== false && supportedRoles.length) {
        const assign = document.createElement("select");
        assign.className = "quiet-select reasoning-model-assign";
        assign.setAttribute("aria-label", `Assign ${model.label || model.name || model.id} to a role`);
        assign.appendChild(new Option("Assign to role…", "", true, true));
        for (const [role, roleLabel] of supportedRoles.filter(Boolean)) assign.appendChild(new Option(roleLabel, role));
        assign.addEventListener("change", () => {
          if (!assign.value) return;
          assignReasoningModelToRole(model, assign.value);
          assign.value = "";
        });
        actions.appendChild(assign);
      }
      if (actions.children.length) body.appendChild(actions);
      card.append(summary, body);
      list.appendChild(card);
    }
    group.append(groupSummary, list);
    ui.reasoningModelLibrary.appendChild(group);
  }
}

function renderReasoningProviders() {
  ui.reasoningProviders.replaceChildren();
  const localCredentialAccess = isLoopbackDashboard();
  for (const provider of state.reasoningProviders) {
    const card = document.createElement("article");
    card.className = `reasoning-provider${reasoningProviderEnabled(provider) ? " enabled" : ""}`;
    card.dataset.providerId = provider.id;
    const head = document.createElement("div");
    head.className = "reasoning-provider-head";
    const identity = document.createElement("div");
    identity.className = "reasoning-provider-identity";
    const title = document.createElement("strong");
    title.textContent = provider.label;
    const locality = document.createElement("span");
    locality.className = `reasoning-badge ${provider.locality}`;
    locality.textContent = localityLabel(provider);
    identity.append(title, locality);
    const actions = document.createElement("div");
    actions.className = "reasoning-provider-actions";
    const probe = document.createElement("button");
    probe.type = "button";
    probe.className = "pill-button small";
    probe.textContent = "Probe";
    probe.disabled = provider.kind === "host_cli" && provider.installed === false;
    probe.addEventListener("click", () => probeReasoningProvider(provider, probe));
    const enable = document.createElement("button");
    enable.type = "button";
    enable.className = "pill-button small provider-enable";
    const enabled = reasoningProviderEnabled(provider);
    const alwaysOn = ["local_structured", "oida_moss"].includes(provider.id);
    enable.textContent = alwaysOn ? "Always on" : (enabled ? "Disable" : "Enable");
    enable.setAttribute("aria-pressed", enabled ? "true" : "false");
    enable.disabled = alwaysOn || (provider.kind === "host_cli" && provider.installed === false) || state.reasoningSettings.incognito;
    enable.addEventListener("click", () => toggleReasoningProvider(provider, enable));
    actions.append(probe, enable);
    head.append(identity, actions);
    const status = document.createElement("p");
    status.className = "reasoning-provider-status";
    status.textContent = providerStatusText(provider);
    const signals = document.createElement("div");
    signals.className = "reasoning-provider-signals";
    const signalValues = [
      [
        provider.kind === "host_cli"
          ? (provider.installed ? "CLI installed" : "CLI not installed")
          : (enabled ? "enabled" : "not enabled"),
        provider.kind === "host_cli" ? provider.installed : enabled,
      ],
      [provider.authenticated === true ? "authenticated" : (provider.authenticated === false ? "sign-in needed" : "host auth"), provider.authenticated !== false],
      [provider.reachable === true ? "reachable" : (provider.reachable === false ? "unreachable" : "not probed"), provider.reachable !== false],
    ];
    for (const [text, okay] of signalValues) {
      const signal = document.createElement("span");
      signal.className = okay ? "" : "attention";
      signal.textContent = text;
      signals.appendChild(signal);
    }
    card.append(head, status, signals);

    const configurableEndpoint = provider.endpoint_configurable || ["ollama", "openai_compatible", "local_audio", "google", "alibaba", "nvidia", "opencode"].includes(provider.id);
    const modelConfigurable = !["local_structured", "oida_moss"].includes(provider.id);
    const credentialSupported = provider.credential_supported || ["openrouter", "openai_compatible", "local_audio", "google", "alibaba", "nvidia"].includes(provider.id);
    const oauthSupported = provider.oauth_supported || provider.id === "openrouter";
    if (configurableEndpoint || modelConfigurable || credentialSupported || oauthSupported) {
      const details = document.createElement("details");
      details.className = "reasoning-provider-config";
      const summary = document.createElement("summary");
      summary.textContent = "Connection";
      const body = document.createElement("div");
      body.className = "reasoning-provider-config-body";
      const integration = document.createElement("p");
      integration.className = "settings-note reasoning-provider-integration";
      integration.textContent = reasoningProviderIntegrationText(provider);
      body.appendChild(integration);
      if (configurableEndpoint) {
        const endpoint = document.createElement("label");
        endpoint.className = "cfg provider-endpoint";
        endpoint.innerHTML = `<span class="cfg-label">Endpoint</span>`;
        const endpointInput = document.createElement("input");
        endpointInput.type = "url";
        endpointInput.spellcheck = false;
        endpointInput.value = state.reasoningSettings.provider_options?.[provider.id]?.base_url || provider.base_url || "";
        endpointInput.placeholder = provider.id === "ollama" ? "http://127.0.0.1:11434" : "http://127.0.0.1:8000/v1";
        endpointInput.addEventListener("change", () => {
          const options = { ...(state.reasoningSettings.provider_options?.[provider.id] || {}) };
          options.base_url = endpointInput.value.trim();
          if (provider.id === "opencode") options.managed = !options.base_url;
          state.reasoningSettings.provider_options = { ...state.reasoningSettings.provider_options, [provider.id]: options };
          state.reasoningModels.delete(provider.id);
          state.reasoningModelRequests.delete(provider.id);
          ui.reasoningSaveNote.textContent = "Unsaved provider endpoint.";
        });
        endpoint.appendChild(endpointInput);
        body.appendChild(endpoint);
      }
      if (modelConfigurable) {
        const model = document.createElement("label");
        model.className = "cfg provider-default-model";
        model.innerHTML = `<span class="cfg-label">Default model ID</span>`;
        const modelInput = document.createElement("input");
        modelInput.type = "text";
        modelInput.spellcheck = false;
        modelInput.autocomplete = "off";
        modelInput.maxLength = 255;
        modelInput.value = state.reasoningSettings.provider_options?.[provider.id]?.default_model || "";
        modelInput.placeholder = "Provider default, or enter an exact model ID";
        modelInput.addEventListener("change", () => {
          const options = { ...(state.reasoningSettings.provider_options?.[provider.id] || {}) };
          options.default_model = modelInput.value.trim();
          state.reasoningSettings.provider_options = { ...state.reasoningSettings.provider_options, [provider.id]: options };
          state.reasoningModels.delete(provider.id);
          state.reasoningModelRequests.delete(provider.id);
          ui.reasoningSaveNote.textContent = "Unsaved provider model.";
        });
        model.appendChild(modelInput);
        body.appendChild(model);
      }
      if (provider.id === "local_audio") {
        const processor = document.createElement("label");
        processor.className = "cfg provider-thinking-processor";
        processor.innerHTML = `<span class="cfg-label">SGLang budget processor · advanced</span>`;
        const processorInput = document.createElement("textarea");
        processorInput.rows = 2;
        processorInput.spellcheck = false;
        processorInput.maxLength = 262144;
        processorInput.value = state.reasoningSettings.provider_options?.[provider.id]?.sglang_thinking_processor || "";
        processorInput.placeholder = "Serialized Qwen3InstructionInjectionThinkingBudgetLogitProcessor";
        processorInput.addEventListener("change", () => {
          const options = { ...(state.reasoningSettings.provider_options?.[provider.id] || {}) };
          const value = processorInput.value.trim();
          if (value) options.sglang_thinking_processor = value;
          else delete options.sglang_thinking_processor;
          state.reasoningSettings.provider_options = { ...state.reasoningSettings.provider_options, [provider.id]: options };
          ui.reasoningSaveNote.textContent = "Unsaved SGLang budget processor.";
        });
        processor.appendChild(processorInput);
        body.appendChild(processor);
      }
      if (credentialSupported) {
        const credentialRow = document.createElement("div");
        credentialRow.className = "provider-credential-row";
        const credential = document.createElement("input");
        credential.type = "password";
        credential.autocomplete = "off";
        credential.placeholder = localCredentialAccess ? "API key (never shown again)" : "Available only on this computer";
        credential.setAttribute("aria-label", `${provider.label} API key`);
        credential.disabled = !localCredentialAccess;
        const save = document.createElement("button");
        save.type = "button";
        save.className = "pill-button small";
        save.textContent = "Save key";
        save.disabled = !localCredentialAccess;
        save.addEventListener("click", () => saveReasoningCredential(provider, credential, save));
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "pill-button small";
        remove.textContent = "Remove";
        remove.disabled = !localCredentialAccess;
        remove.addEventListener("click", () => deleteReasoningCredential(provider, remove));
        credentialRow.append(credential, save, remove);
        body.appendChild(credentialRow);
      }
      if (oauthSupported) {
        const connect = document.createElement("button");
        connect.type = "button";
        connect.className = "pill-button small provider-oauth";
        connect.textContent = "Connect OpenRouter";
        connect.disabled = !localCredentialAccess;
        connect.addEventListener("click", () => startOpenRouterOAuth(connect));
        body.appendChild(connect);
      }
      if (!localCredentialAccess && (credentialSupported || oauthSupported)) {
        const note = document.createElement("p");
        note.className = "settings-note";
        note.textContent = "Credential changes are disabled on remote dashboard connections.";
        body.appendChild(note);
      }
      details.append(summary, body);
      card.appendChild(details);
    }
    ui.reasoningProviders.appendChild(card);
  }
}

async function toggleReasoningProvider(provider, button) {
  const enabled = new Set(state.reasoningSettings.enabled_provider_ids || []);
  if (enabled.has(provider.id)) enabled.delete(provider.id);
  else enabled.add(provider.id);
  enabled.add("local_structured");
  enabled.add("oida_moss");
  state.reasoningSettings.enabled_provider_ids = [...enabled];
  button.disabled = true;
  try {
    await saveReasoningSettings({ quiet: true });
    await refreshReasoning(true);
  } catch (_) {
    renderReasoningProviders();
  }
}

async function probeReasoningProvider(provider, button) {
  button.disabled = true;
  button.textContent = "Probing…";
  try {
    const result = await post(`/reasoning/providers/${encodeURIComponent(provider.id)}/probe`);
    const merged = normalizeReasoningProvider({ ...provider, ...(result.provider || result) });
    state.reasoningProviders = state.reasoningProviders.map((item) => item.id === provider.id ? merged : item);
    renderReasoningProviders();
    setReasoningStatus(`${provider.label}: ${providerStatusText(merged)}`, merged.reachable === false ? "warning" : "ready");
  } catch (error) {
    setReasoningStatus(`${provider.label}: ${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Probe";
  }
}

async function saveReasoningCredential(provider, input, button) {
  const credential = input.value.trim();
  if (!credential) {
    setReasoningStatus(`Enter a key for ${provider.label}.`, "warning");
    return;
  }
  button.disabled = true;
  try {
    await fetchJson(`/reasoning/providers/${encodeURIComponent(provider.id)}/credential`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ credential }),
    });
    input.value = "";
    setReasoningStatus(`${provider.label} credential saved securely.`, "ready");
    await refreshReasoning(true);
  } catch (error) {
    setReasoningStatus(`${provider.label}: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

async function deleteReasoningCredential(provider, button) {
  button.disabled = true;
  try {
    await fetchJson(`/reasoning/providers/${encodeURIComponent(provider.id)}/credential`, { method: "DELETE" });
    setReasoningStatus(`${provider.label} credential removed.`, "ready");
    await refreshReasoning(true);
  } catch (error) {
    setReasoningStatus(`${provider.label}: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

async function startOpenRouterOAuth(button) {
  const popup = window.open("about:blank", "oida-openrouter-oauth", "width=620,height=760");
  button.disabled = true;
  try {
    const result = await post("/reasoning/openrouter/oauth/start");
    const url = result.authorization_url || result.auth_url || result.url;
    if (!url) throw new Error("The authorization URL was not returned.");
    if (popup) popup.location.href = url;
    else window.open(url, "_blank", "noopener");
    setReasoningStatus("Finish connecting OpenRouter in the opened window, then refresh providers.", "active");
  } catch (error) {
    popup?.close();
    setReasoningStatus(`OpenRouter: ${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

async function loadReasoningModels(providerId, force = false) {
  if (!providerId) return [];
  if (state.reasoningModels.has(providerId) && !force) return state.reasoningModels.get(providerId);
  if (state.reasoningModelRequests.has(providerId) && !force) return state.reasoningModelRequests.get(providerId);
  const provider = reasoningProviderById(providerId);
  if (Array.isArray(provider?.models) && provider.models.length && !force) {
    const models = provider.models.map((model) => typeof model === "string" ? { id: model, label: model } : model);
    state.reasoningModels.set(providerId, models);
    return models;
  }
  const request = (async () => {
    const payload = await fetchJson(`/reasoning/models?provider_id=${encodeURIComponent(providerId)}`);
    return collectionFromPayload(payload, "models").map((model) => ({
      ...model,
      id: String(model.id || model.model_id || model.name || "default"),
      label: String(model.label || model.display_name || model.name || model.id || model.model_id || "Default"),
    }));
  })();
  state.reasoningModelRequests.set(providerId, request);
  try {
    const models = await request;
    state.reasoningModels.set(providerId, models);
    return models;
  } catch (error) {
    logActivity(`Reasoning models (${providerId}): ${error.message}`, "error");
    return [];
  } finally {
    state.reasoningModelRequests.delete(providerId);
  }
}

function reasoningModelOptionLabel(model, providerId) {
  const provider = reasoningProviderById(providerId);
  const availability = reasoningModelAvailability(model, provider);
  const ram = model.metadata?.recommended_ram_gb;
  const parts = [model.label, availability.short];
  if (Number.isFinite(ram) && (model.locality === "local" || provider?.locality === "local")) parts.push(`~${ram} GB RAM`);
  return parts.filter(Boolean).join(" · ");
}

function updateReasoningRoleStatus(element, providerId, modelId, models = []) {
  if (!element) return;
  const provider = reasoningProviderById(providerId);
  if (!provider) {
    element.textContent = "Choose a provider. Oída keeps the existing local fallback until this role is configured.";
    element.className = "reasoning-role-status warning";
    return;
  }
  const defaultModel = state.reasoningSettings.provider_options?.[providerId]?.default_model || null;
  const effectiveModelId = modelId || defaultModel;
  if (!effectiveModelId) {
    const enabled = reasoningProviderEnabled(provider);
    element.textContent = enabled
      ? "Provider default selected; Oída will check the model when this role runs."
      : "Provider default selected, but the provider is not enabled. Oída will use its local fallback.";
    element.className = `reasoning-role-status ${enabled ? "" : "warning"}`.trim();
    return;
  }
  const model = models.find((item) => item.id === effectiveModelId);
  if (!model) {
    element.textContent = `Exact model ID “${effectiveModelId}” was not reported by this provider. Oída will try it when available and otherwise use its local fallback.`;
    element.className = "reasoning-role-status warning";
    return;
  }
  const availability = reasoningModelAvailability(model, provider);
  element.textContent = availability.detail;
  element.className = `reasoning-role-status ${availability.ready ? "ready" : "warning"}`;
}

async function fillReasoningModelSelect(select, providerId, selectedModelId, role = null) {
  select.disabled = true;
  select.replaceChildren(new Option(providerId ? "Loading models…" : "Choose a provider", "", true, true));
  let models = await loadReasoningModels(providerId);
  if (role && role !== "conversation") {
    const capability = role;
    if (capability) {
      models = models.filter((model) => (
        model.id === selectedModelId || (Array.isArray(model.capabilities) && model.capabilities.includes(capability))
      ));
    }
  }
  select.replaceChildren(new Option("Provider default", "", false, !selectedModelId));
  for (const model of models) {
    const label = reasoningModelOptionLabel(model, providerId);
    const option = new Option(label, model.id, false, model.id === selectedModelId);
    const details = [
      ...(Array.isArray(model.capabilities) ? model.capabilities : []),
      model.metadata?.integration_status,
      model.metadata?.notes,
    ].filter(Boolean);
    option.title = details.join(" · ");
    option.disabled = model.metadata?.selectable === false;
    select.appendChild(option);
  }
  if (selectedModelId && !models.some((model) => model.id === selectedModelId)) {
    select.appendChild(new Option(`${selectedModelId} · not detected`, selectedModelId, true, true));
  }
  select.disabled = !providerId;
  return models;
}

function providerOptionsForSelect(select, selectedProviderId, options = {}) {
  select.replaceChildren();
  const providers = state.reasoningProviders.filter((provider) => {
    if (!options.role) return true;
    if (options.role === "conversation") return provider.id !== "oida_moss";
    if (provider.id === "local_structured") return false;
    const declaredAudio = provider.id === "oida_moss"
      || provider.id === "local_audio"
      || Array.isArray(provider.capabilities) && provider.capabilities.includes("audio")
      || Boolean(state.reasoningSettings.provider_options?.[provider.id]?.audio_capable);
    if (!declaredAudio) return false;
    return options.role !== "targeted_relisten" || provider.locality === "local";
  });
  if (!providers.length) select.appendChild(new Option("No compatible provider", "", true, true));
  for (const provider of providers) {
    const enabled = reasoningProviderEnabled(provider);
    const stateLabel = enabled ? (provider.reachable === false ? "unavailable" : "enabled") : "supported · not enabled";
    const label = `${provider.label} · ${localityLabel(provider)} · ${stateLabel}`;
    const option = new Option(label, provider.id, false, provider.id === selectedProviderId);
    select.appendChild(option);
  }
  if (options.localOnly) {
    for (const option of select.options) {
      const provider = reasoningProviderById(option.value);
      option.disabled = option.disabled || Boolean(provider && provider.locality !== "local");
    }
  }
}

function renderReasoningRoles() {
  ui.reasoningRoles.replaceChildren();
  for (const [role, label, description] of REASONING_ROLES) {
    const assignment = state.reasoningSettings.role_assignments[role] || { provider_id: null, model_id: null };
    const row = document.createElement("div");
    row.className = "reasoning-role";
    row.dataset.role = role;
    const copy = document.createElement("div");
    copy.className = "reasoning-role-copy";
    copy.innerHTML = `<strong>${escapeHtml(label)}</strong><span>${escapeHtml(description)}</span>`;
    const controls = document.createElement("div");
    controls.className = "reasoning-role-controls";
    const selection = document.createElement("div");
    selection.className = "reasoning-role-selection";
    const roleStatus = document.createElement("p");
    roleStatus.className = "reasoning-role-status";
    const providerSelect = document.createElement("select");
    providerSelect.className = "quiet-select";
    providerSelect.setAttribute("aria-label", `${label} provider`);
    providerOptionsForSelect(providerSelect, assignment.provider_id, { localOnly: role === "targeted_relisten", role });
    const modelSelect = document.createElement("select");
    modelSelect.className = "quiet-select";
    modelSelect.setAttribute("aria-label", `${label} model`);
    fillReasoningModelSelect(modelSelect, assignment.provider_id, assignment.model_id, role)
      .then((models) => updateReasoningRoleStatus(roleStatus, assignment.provider_id, assignment.model_id, models));
    const modelInput = document.createElement("input");
    modelInput.type = "text";
    modelInput.className = "quiet-input reasoning-model-id";
    modelInput.maxLength = 255;
    modelInput.spellcheck = false;
    modelInput.autocomplete = "off";
    modelInput.placeholder = "Exact model ID (optional)";
    modelInput.value = assignment.model_id || "";
    modelInput.setAttribute("aria-label", `${label} exact model ID`);
    providerSelect.addEventListener("change", async () => {
      assignment.provider_id = providerSelect.value || null;
      assignment.model_id = null;
      modelInput.value = "";
      state.reasoningSettings.role_assignments[role] = assignment;
      if (role === "conversation") {
        state.reasoningSettings.active_provider_id = assignment.provider_id;
        state.reasoningSettings.active_model_id = null;
      }
      ui.reasoningSaveNote.textContent = "Unsaved role assignment.";
      const models = await fillReasoningModelSelect(modelSelect, assignment.provider_id, null, role);
      updateReasoningRoleStatus(roleStatus, assignment.provider_id, null, models);
    });
    modelSelect.addEventListener("change", () => {
      assignment.model_id = modelSelect.value || null;
      modelInput.value = assignment.model_id || "";
      state.reasoningSettings.role_assignments[role] = assignment;
      if (role === "conversation") state.reasoningSettings.active_model_id = assignment.model_id;
      ui.reasoningSaveNote.textContent = "Unsaved role assignment.";
      updateReasoningRoleStatus(roleStatus, assignment.provider_id, assignment.model_id, state.reasoningModels.get(assignment.provider_id) || []);
    });
    modelInput.addEventListener("change", () => {
      assignment.model_id = modelInput.value.trim() || null;
      state.reasoningSettings.role_assignments[role] = assignment;
      if (assignment.model_id && ![...modelSelect.options].some((option) => option.value === assignment.model_id)) {
        modelSelect.appendChild(new Option(assignment.model_id, assignment.model_id));
      }
      modelSelect.value = assignment.model_id || "";
      if (role === "conversation") state.reasoningSettings.active_model_id = assignment.model_id;
      ui.reasoningSaveNote.textContent = "Unsaved exact model ID.";
      updateReasoningRoleStatus(roleStatus, assignment.provider_id, assignment.model_id, state.reasoningModels.get(assignment.provider_id) || []);
    });
    controls.append(providerSelect, modelSelect, modelInput);
    selection.append(controls, roleStatus);
    row.append(copy, selection);
    ui.reasoningRoles.appendChild(row);
  }
}

function activeReasoningProfile() {
  return state.reasoningSettings?.profiles?.find((profile) => profile.id === state.reasoningSettings.active_profile_id)
    || state.reasoningSettings?.profiles?.[0]
    || DEFAULT_REASONING_PROFILE;
}

function captureReasoningProfileEditor() {
  if (!state.reasoningSettings) return;
  const profile = activeReasoningProfile();
  profile.name = ui.reasoningProfileName.value.trim() || profile.name || "Conversation profile";
  profile.tone = ui.reasoningTone.value;
  profile.depth = ui.reasoningDepth.value;
  profile.initiative = ui.reasoningInitiative.value;
  profile.language = ui.reasoningLanguage.value.trim() || "auto";
  profile.custom_instructions = ui.reasoningInstructions.value.slice(0, 4000);
  profile.focus = [...ui.reasoningFocus.querySelectorAll('input[type="checkbox"]:checked')].map((input) => input.value);
  const option = [...ui.reasoningProfileSelect.options].find((item) => item.value === profile.id);
  if (option) option.textContent = profile.name;
}

function loadReasoningProfileEditor() {
  const profile = activeReasoningProfile();
  ui.reasoningProfileName.value = profile.name || "Conversation profile";
  ui.reasoningTone.value = profile.tone || "warm";
  ui.reasoningDepth.value = profile.depth || "balanced";
  ui.reasoningInitiative.value = profile.initiative || "suggest_followups";
  ui.reasoningLanguage.value = profile.language || "auto";
  ui.reasoningInstructions.value = profile.custom_instructions || "";
  const focuses = new Set(profile.focus || []);
  ui.reasoningFocus.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = focuses.has(input.value); });
  ui.reasoningInstructionCount.textContent = `${ui.reasoningInstructions.value.length} / 4000`;
  ui.reasoningProfileRemove.disabled = state.reasoningSettings.profiles.length <= 1;
}

function renderReasoningProfiles() {
  ui.reasoningProfileSelect.replaceChildren();
  for (const profile of state.reasoningSettings.profiles) {
    ui.reasoningProfileSelect.appendChild(new Option(profile.name, profile.id, false, profile.id === state.reasoningSettings.active_profile_id));
  }
  loadReasoningProfileEditor();
}

function reasoningSettingsPayload() {
  captureReasoningProfileEditor();
  const settings = state.reasoningSettings;
  const conversation = settings.role_assignments.conversation || {};
  return {
    version: settings.version,
    active_provider_id: conversation.provider_id || settings.active_provider_id || "local_structured",
    active_model_id: conversation.model_id || settings.active_model_id || null,
    active_profile_id: settings.active_profile_id,
    enabled_provider_ids: [...new Set(["local_structured", "oida_moss", ...(settings.enabled_provider_ids || [])])],
    role_assignments: settings.role_assignments,
    profiles: settings.profiles,
    provider_options: settings.provider_options,
    include_transcript: Boolean(ui.reasoningTranscript.checked),
    include_memory_content: Boolean(ui.reasoningMemory.checked),
    allow_targeted_relisten: Boolean(ui.reasoningRelisten.checked),
    allow_external_audio: Boolean(ui.reasoningExternalAudio.checked),
  };
}

async function saveReasoningSettings(options = {}) {
  if (!state.reasoningSettings) return;
  ui.reasoningSave.disabled = true;
  if (!options.quiet) ui.reasoningSaveNote.textContent = "Saving…";
  try {
    const payload = reasoningSettingsPayload();
    const result = await fetchJson("/reasoning/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.reasoningSettings = normalizeReasoningSettings(result?.settings || result || payload);
    if (!options.quiet) {
      renderReasoningSettings();
      populateConversationControls();
      ui.reasoningSaveNote.textContent = "Reasoning settings saved.";
      setReasoningStatus("Reasoning settings saved. The original listening evidence remains authoritative.", "ready");
    }
    return state.reasoningSettings;
  } catch (error) {
    ui.reasoningSaveNote.textContent = `Save failed: ${error.message}`;
    setReasoningStatus(`Reasoning settings: ${error.message}`, "error");
    throw error;
  } finally {
    ui.reasoningSave.disabled = false;
  }
}

ui.reasoningRefresh?.addEventListener("click", () => {
  state.reasoningModels.clear();
  state.reasoningModelRequests.clear();
  refreshReasoning(true);
});
ui.reasoningModelFilter?.addEventListener("change", renderReasoningModelLibrary);
ui.reasoningSave?.addEventListener("click", () => saveReasoningSettings());
ui.reasoningProfileSelect?.addEventListener("change", () => {
  captureReasoningProfileEditor();
  state.reasoningSettings.active_profile_id = ui.reasoningProfileSelect.value;
  loadReasoningProfileEditor();
  ui.reasoningSaveNote.textContent = "Unsaved active profile.";
});
ui.reasoningProfileAdd?.addEventListener("click", () => {
  captureReasoningProfileEditor();
  const suffix = typeof crypto?.randomUUID === "function" ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36);
  const profile = { ...DEFAULT_REASONING_PROFILE, id: `profile_${suffix}`, name: "New profile", focus: [] };
  state.reasoningSettings.profiles.push(profile);
  state.reasoningSettings.active_profile_id = profile.id;
  renderReasoningProfiles();
  ui.reasoningProfileName.focus();
  ui.reasoningProfileName.select();
  ui.reasoningSaveNote.textContent = "Unsaved new profile.";
});
ui.reasoningProfileRemove?.addEventListener("click", () => {
  if (state.reasoningSettings.profiles.length <= 1) return;
  state.reasoningSettings.profiles = state.reasoningSettings.profiles.filter((profile) => profile.id !== state.reasoningSettings.active_profile_id);
  state.reasoningSettings.active_profile_id = state.reasoningSettings.profiles[0].id;
  renderReasoningProfiles();
  ui.reasoningSaveNote.textContent = "Unsaved profile removal.";
});
ui.reasoningInstructions?.addEventListener("input", () => {
  ui.reasoningInstructionCount.textContent = `${ui.reasoningInstructions.value.length} / 4000`;
  ui.reasoningSaveNote.textContent = "Unsaved profile changes.";
});
for (const input of [ui.reasoningProfileName, ui.reasoningTone, ui.reasoningDepth, ui.reasoningInitiative, ui.reasoningLanguage]) {
  input?.addEventListener("input", () => { ui.reasoningSaveNote.textContent = "Unsaved profile changes."; });
}
ui.reasoningFocus?.addEventListener("change", () => { ui.reasoningSaveNote.textContent = "Unsaved profile changes."; });
for (const input of [ui.reasoningTranscript, ui.reasoningMemory, ui.reasoningRelisten, ui.reasoningExternalAudio]) {
  input?.addEventListener("change", () => { ui.reasoningSaveNote.textContent = "Unsaved data-sharing changes."; });
}

/* ───────────── event-anchored conversation (reasoning is optional) ─────── */

function conversationSessionEvents() {
  const session = [...state.sessions, ...state.archivedSessions].find((item) => item.id === state.currentSessionId);
  return session?.events || [];
}

function selectedComparisonEventIds() {
  return [...ui.conversationCompareList.querySelectorAll('input[type="checkbox"]:checked')]
    .slice(0, 3)
    .map((input) => input.value);
}

function renderConversationComparisons() {
  ui.conversationCompareList.replaceChildren();
  const events = conversationSessionEvents().filter((event) => event.id !== state.conversationEvent?.id);
  if (!events.length) {
    const note = document.createElement("span");
    note.className = "settings-note";
    note.textContent = "No other results in this session.";
    ui.conversationCompareList.appendChild(note);
    return;
  }
  for (const event of events) {
    const label = document.createElement("label");
    label.className = "conversation-compare-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = event.id;
    const text = document.createElement("span");
    text.textContent = event.aggregate?.title || event.id || "Listening result";
    input.addEventListener("change", () => {
      const selected = [...ui.conversationCompareList.querySelectorAll('input[type="checkbox"]:checked')];
      if (input.checked && selected.length > 3) input.checked = false;
      const count = selectedComparisonEventIds().length;
      ui.conversationCompareList.querySelectorAll('input[type="checkbox"]:not(:checked)').forEach((candidate) => {
        candidate.disabled = count >= 3;
      });
    });
    label.append(input, text);
    ui.conversationCompareList.appendChild(label);
  }
}

function conversationProvider() {
  return reasoningProviderById(ui.conversationProvider.value) || normalizeReasoningProvider({
    id: ui.conversationProvider.value || "local_structured",
    label: ui.conversationProvider.selectedOptions?.[0]?.textContent || "Oída local structured",
    locality: "local",
  });
}

function updateConversationPrivacySummary() {
  const provider = conversationProvider();
  const locality = localityLabel(provider);
  ui.conversationLocality.textContent = locality;
  ui.conversationLocality.className = `reasoning-badge ${provider.locality || "unknown"}`;
  const shared = [];
  if (ui.conversationTranscript.checked) shared.push("transcript");
  if (ui.conversationMemory.checked) shared.push("memory");
  const base = provider.locality === "local" ? "stays local" : "filtered evidence";
  ui.conversationContextSummary.textContent = shared.length ? `${base} + ${shared.join(" + ")}` : `${base} only`;
}

async function populateConversationControls(options = {}) {
  if (!ui.conversationProvider || !state.reasoningSettings || !state.reasoningProviders.length) return;
  const assignment = state.reasoningSettings.role_assignments?.conversation || {};
  const priorProvider = options.resetProvider ? "" : ui.conversationProvider.value;
  let providerId = priorProvider || assignment.provider_id || state.reasoningSettings.active_provider_id || "local_structured";
  if (state.reasoningSettings.incognito) providerId = "local_structured";
  providerOptionsForSelect(ui.conversationProvider, providerId, { localOnly: state.reasoningSettings.incognito });
  if (![...ui.conversationProvider.options].some((option) => option.value === providerId && !option.disabled)) {
    providerId = [...ui.conversationProvider.options].find((option) => !option.disabled)?.value || "local_structured";
    ui.conversationProvider.value = providerId;
  }
  const priorModel = options.resetProvider ? null : ui.conversationModel.value;
  await fillReasoningModelSelect(
    ui.conversationModel,
    providerId,
    priorModel || (providerId === assignment.provider_id ? assignment.model_id : null) || state.reasoningSettings.active_model_id,
  );
  const selectedProfile = options.resetProvider ? state.reasoningSettings.active_profile_id : (ui.conversationProfile.value || state.reasoningSettings.active_profile_id);
  ui.conversationProfile.replaceChildren();
  for (const profile of state.reasoningSettings.profiles || []) {
    ui.conversationProfile.appendChild(new Option(profile.name, profile.id, false, profile.id === selectedProfile));
  }
  if (options.resetPermissions) {
    ui.conversationTranscript.checked = Boolean(state.reasoningSettings.include_transcript);
    ui.conversationMemory.checked = Boolean(state.reasoningSettings.include_memory_content);
    ui.conversationRelisten.checked = state.reasoningSettings.allow_targeted_relisten !== false;
  }
  const privacyLocked = Boolean(state.reasoningSettings.incognito);
  ui.conversationTranscript.disabled = privacyLocked;
  ui.conversationMemory.disabled = privacyLocked;
  if (privacyLocked) {
    ui.conversationTranscript.checked = false;
    ui.conversationMemory.checked = false;
  }
  updateConversationPrivacySummary();
}

async function openConversation(event = state.lastEvent) {
  if (!event || !ui.conversationPanel) {
    setListenStatus("Select a listening result before starting a conversation.", "error");
    return;
  }
  const changedAnchor = state.conversationEvent?.id !== event.id;
  if (changedAnchor) {
    if (state.conversationEvent?.id) {
      state.conversationByEvent.set(state.conversationEvent.id, {
        conversationId: state.conversationId,
        turns: state.conversationTurns,
      });
    }
    state.conversationAbort?.abort();
    state.conversationEvent = event;
    const existing = state.conversationByEvent.get(event.id);
    state.conversationId = existing?.conversationId || null;
    state.conversationTurns = existing?.turns || [];
    state.conversationDraftAnswer = "";
  }
  if (akousmataUi.modal) akousmataUi.modal.hidden = true;
  ui.conversationTitle.textContent = "Ask about this listening";
  ui.conversationAnchor.textContent = event.aggregate?.title || event.id || "Listening result";
  ui.conversationPanel.hidden = false;
  renderConversationComparisons();
  renderConversationTurns();
  if (!state.reasoningLoaded) await refreshReasoning();
  if (!state.reasoningSettings) {
    state.reasoningSettings = normalizeReasoningSettings({});
    state.reasoningProviders = [normalizeReasoningProvider({
      id: "local_structured", label: "Oída local structured", locality: "local", enabled: true,
    })];
  }
  await populateConversationControls({ resetProvider: changedAnchor, resetPermissions: changedAnchor });
  ui.conversationQuestion.focus();
}

function closeConversation() {
  if (!ui.conversationPanel || ui.conversationPanel.hidden) return;
  state.conversationAbort?.abort();
  state.conversationAbort = null;
  if (state.conversationEvent?.id) {
    state.conversationByEvent.set(state.conversationEvent.id, {
      conversationId: state.conversationId,
      turns: state.conversationTurns,
    });
  }
  state.conversationBusy = false;
  ui.conversationSend.disabled = false;
  ui.conversationPanel.hidden = true;
  ui.conversationStatus.textContent = "";
}

function evidenceRefsHtml(refs) {
  if (!Array.isArray(refs) || !refs.length) return "";
  return `<div class="conversation-evidence-refs">${refs.slice(0, 12).map((ref) => `<span>${escapeHtml(typeof ref === "string" ? ref : ref?.id || ref?.label || "evidence")}</span>`).join("")}</div>`;
}

function readableConversationValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(readableConversationValue).filter(Boolean).join(" · ");
  if (typeof value === "object") {
    return value.text || value.statement || value.observation || value.note || value.summary || JSON.stringify(value);
  }
  return String(value);
}

function normalizeConversationTurn(response, question) {
  const envelope = response?.response || response?.result || response || {};
  const turn = envelope.turn || envelope;
  const conversationId = envelope.conversation_id || turn.conversation_id || null;
  let answerBlocks = turn.answer_blocks || envelope.answer_blocks || [];
  if (!Array.isArray(answerBlocks)) answerBlocks = [];
  if (!answerBlocks.length && (turn.answer || envelope.answer)) {
    answerBlocks = [{ kind: "answer", text: turn.answer || envelope.answer, evidence_refs: [] }];
  }
  answerBlocks = answerBlocks.map((block) => typeof block === "string"
    ? { kind: "answer", text: block, evidence_refs: [] }
    : { kind: block?.kind || "answer", text: readableConversationValue(block?.text || block?.answer), evidence_refs: block?.evidence_refs || [] });
  const hypotheses = (turn.hypotheses || envelope.hypotheses || []).map((item) => typeof item === "string"
    ? { statement: item, confidence: null, evidence_refs: [] }
    : { statement: readableConversationValue(item?.statement || item?.text), confidence: item?.confidence, evidence_refs: item?.evidence_refs || [] });
  const uncertainties = (turn.uncertainties || turn.uncertainty_notes || envelope.uncertainties || []).map(readableConversationValue).filter(Boolean);
  const suggestedQuestions = (turn.suggested_questions || envelope.suggested_questions || []).map(readableConversationValue).filter(Boolean);
  return {
    question: turn.question || question,
    answerBlocks,
    hypotheses,
    uncertainties,
    suggestedQuestions,
    evidence: turn.evidence || envelope.evidence || [],
    reasoner: turn.reasoner || envelope.reasoner || turn.remote_model || envelope.remote_model || null,
    fallback: turn.fallback || envelope.fallback || null,
    relisten: turn.relisten || envelope.relisten || turn.targeted_relisten || envelope.targeted_relisten || null,
    conversationId,
    pending: false,
  };
}

function reasonerMetaHtml(turn) {
  const reasoner = turn.reasoner;
  const pieces = [];
  if (reasoner) {
    const provider = reasoner.provider_id || reasoner.provider || reasoner.label;
    const model = reasoner.model_id || reasoner.model;
    if (provider) pieces.push(provider);
    if (model) pieces.push(model);
  }
  if (turn.pending && turn.providerLabel) pieces.push(turn.providerLabel);
  return pieces.length ? `<span class="conversation-reasoner">${escapeHtml(pieces.join(" · "))}</span>` : "";
}

function fallbackHtml(fallback) {
  if (!fallback) return "";
  const used = typeof fallback === "boolean" ? fallback : fallback.used !== false;
  if (!used) return "";
  const note = typeof fallback === "string"
    ? fallback
    : fallback.note || fallback.reason || fallback.message || "The selected provider could not return a valid response; Oída used its local structured answer.";
  return `<div class="conversation-fallback"><strong>Local fallback</strong><span>${escapeHtml(note)}</span></div>`;
}

function relistenHtml(relisten, pendingStatus = "") {
  if (!relisten && !pendingStatus) return "";
  const status = pendingStatus || relisten?.status || "completed";
  const observation = readableConversationValue(relisten?.observation || relisten?.result || relisten?.summary);
  const limitations = readableConversationValue(relisten?.limitations);
  const model = relisten?.model || relisten?.model_id || relisten?.engine;
  return `<details class="conversation-relisten"${status === "completed" ? "" : " open"}>
    <summary><span>Targeted local re-listen</span><small>${escapeHtml(status)}</small></summary>
    <div>${model ? `<span class="conversation-relisten-model">${escapeHtml(model)}</span>` : ""}${observation ? `<p>${escapeHtml(observation)}</p>` : `<p>${escapeHtml(pendingStatus || "A focused local pass was requested.")}</p>`}${limitations ? `<small>${escapeHtml(limitations)}</small>` : ""}</div>
  </details>`;
}

function renderConversationTurns() {
  if (!ui.conversationTurns) return;
  if (!state.conversationTurns.length) {
    ui.conversationTurns.innerHTML = `<div class="conversation-empty"><strong>Stay with what was heard.</strong><p>Ask for detail, interpretation, comparison, or a closer local re-listen. Oída keeps the original listening result unchanged.</p></div>`;
    return;
  }
  ui.conversationTurns.innerHTML = state.conversationTurns.map((turn, turnIndex) => {
    const blocks = turn.pending && !turn.answerBlocks?.length
      ? (turn.draft ? [{ kind: "answer", text: turn.draft, evidence_refs: [] }] : [])
      : (turn.answerBlocks || []);
    const answerHtml = blocks.map((block) => `<section class="conversation-answer-block ${escapeHtml(block.kind || "answer")}">${block.kind && block.kind !== "answer" ? `<span class="conversation-block-kind">${escapeHtml(block.kind.replaceAll("_", " "))}</span>` : ""}<p>${escapeHtml(block.text || "")}</p>${evidenceRefsHtml(block.evidence_refs)}</section>`).join("");
    const hypotheses = (turn.hypotheses || []).length
      ? `<details class="conversation-support"><summary>Hypotheses <span>${turn.hypotheses.length}</span></summary><ul>${turn.hypotheses.map((item) => `<li>${item.confidence ? `<small>${escapeHtml(item.confidence)}</small>` : ""}<span>${escapeHtml(item.statement)}</span>${evidenceRefsHtml(item.evidence_refs)}</li>`).join("")}</ul></details>`
      : "";
    const uncertainties = (turn.uncertainties || []).length
      ? `<div class="conversation-uncertainty"><strong>Uncertainty</strong>${turn.uncertainties.map((note) => `<p>${escapeHtml(note)}</p>`).join("")}</div>`
      : "";
    const evidence = (turn.evidence || []).length
      ? `<details class="conversation-support"><summary>Evidence <span>${turn.evidence.length}</span></summary><ul>${turn.evidence.slice(0, 16).map((item) => `<li><span>${escapeHtml(typeof item === "string" ? item : [item.label, item.value].filter(Boolean).join(": ") || item.kind || "evidence")}</span></li>`).join("")}</ul></details>`
      : "";
    const suggestions = (turn.suggestedQuestions || []).length
      ? `<div class="conversation-suggestions">${turn.suggestedQuestions.slice(0, 5).map((question, suggestionIndex) => `<button type="button" data-turn-index="${turnIndex}" data-suggestion-index="${suggestionIndex}">${escapeHtml(question)}</button>`).join("")}</div>`
      : "";
    const pending = turn.pending && !turn.draft ? `<div class="conversation-thinking"><span></span><span></span><span></span><em>${escapeHtml(turn.relistenStatus || "Grounding the answer in this listening…")}</em></div>` : "";
    return `<article class="conversation-turn${turn.pending ? " pending" : ""}">
      <div class="conversation-question"><span>You</span><p>${escapeHtml(turn.question || "")}</p></div>
      <div class="conversation-response">
        <div class="conversation-response-meta"><span>Oída</span>${reasonerMetaHtml(turn)}</div>
        ${pending}${answerHtml}${relistenHtml(turn.relisten, turn.relistenStatus)}${fallbackHtml(turn.fallback)}${hypotheses}${uncertainties}${evidence}${suggestions}
      </div>
    </article>`;
  }).join("");
  ui.conversationTurns.querySelectorAll("button[data-suggestion-index]").forEach((button) => {
    button.addEventListener("click", () => {
      const turn = state.conversationTurns[Number(button.dataset.turnIndex)];
      const question = turn?.suggestedQuestions?.[Number(button.dataset.suggestionIndex)];
      if (!question) return;
      ui.conversationQuestion.value = question;
      ui.conversationQuestion.focus();
    });
  });
  ui.conversationTurns.scrollTop = ui.conversationTurns.scrollHeight;
}

function conversationRequestPayload(question) {
  const providerId = ui.conversationProvider.value || "local_structured";
  const provider = reasoningProviderById(providerId);
  return {
    question,
    event_id: state.conversationEvent.id,
    conversation_id: state.conversationId,
    provider_id: providerId,
    model_id: ui.conversationModel.value || null,
    profile_id: ui.conversationProfile.value || state.reasoningSettings?.active_profile_id || null,
    comparison_event_ids: selectedComparisonEventIds(),
    allow_targeted_relisten: Boolean(ui.conversationRelisten.checked),
    include_transcript: Boolean(ui.conversationTranscript.checked),
    include_memory_content: Boolean(ui.conversationMemory.checked),
    // Compatibility aliases keep older daemons privacy-correct while the
    // v0.2 request fields above remain authoritative.
    include_memory: Boolean(ui.conversationMemory.checked),
    allow_remote_model: Boolean(provider && provider.locality !== "local"),
    provider: providerId,
  };
}

async function responseErrorDetail(response) {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
  } catch (_) {
    return `${response.status} ${response.statusText}`.trim();
  }
}

async function streamConversationAsk(payload, pendingTurn, signal) {
  const response = await fetch("/conversation/ask/stream", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    const error = new Error(await responseErrorDetail(response));
    error.streamUnavailable = [404, 405, 501].includes(response.status);
    throw error;
  }
  if ((response.headers.get("content-type") || "").includes("application/json")) return response.json();
  if (!response.body) {
    const error = new Error("Streaming is not available in this browser.");
    error.streamUnavailable = true;
    throw error;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = null;
  const dispatch = (block) => {
    if (!block.trim()) return;
    let eventName = "message";
    const dataLines = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) return;
    const raw = dataLines.join("\n");
    let data;
    try { data = JSON.parse(raw); } catch (_) { data = { text: raw }; }
    const type = data.type || eventName;
    if (type === "started") {
      pendingTurn.conversationId = data.conversation_id || data.conversationId || pendingTurn.conversationId;
      const label = data.provider_id || data.provider || pendingTurn.providerLabel;
      if (state.conversationEvent?.id === pendingTurn.eventId) {
        ui.conversationStatus.textContent = label ? `${label} is grounding the answer…` : "Grounding the answer…";
      }
    } else if (type === "delta") {
      pendingTurn.draft += readableConversationValue(data.delta ?? data.text ?? data.content);
      if (state.conversationEvent?.id === pendingTurn.eventId) renderConversationTurns();
    } else if (type === "relisten_started") {
      pendingTurn.relistenStatus = "listening again locally…";
      if (state.conversationEvent?.id === pendingTurn.eventId) renderConversationTurns();
    } else if (type === "relisten_completed") {
      pendingTurn.relistenStatus = "local re-listen completed";
      pendingTurn.relisten = data.relisten || data.result || data;
      if (state.conversationEvent?.id === pendingTurn.eventId) renderConversationTurns();
    } else if (type === "completed") {
      completed = data.response || data.result || data.payload || data;
    } else if (type === "error") {
      throw new Error(data.detail || data.message || data.error || "Reasoning request failed.");
    }
  };
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    for (const block of blocks) dispatch(block);
    if (done) break;
  }
  if (buffer.trim()) dispatch(buffer);
  return completed || {
    conversation_id: pendingTurn.conversationId,
    turn: { question: pendingTurn.question, answer: pendingTurn.draft },
  };
}

function setConversationBusy(busy) {
  state.conversationBusy = busy;
  ui.conversationSend.disabled = busy;
  ui.conversationProvider.disabled = busy;
  ui.conversationModel.disabled = busy;
  ui.conversationProfile.disabled = busy;
  ui.conversationQuestion.disabled = busy;
}

async function submitConversation(question) {
  const trimmed = String(question || "").trim();
  if (!trimmed || !state.conversationEvent || state.conversationBusy) return;
  const payload = conversationRequestPayload(trimmed);
  const anchorEventId = state.conversationEvent.id;
  const turnCollection = state.conversationTurns;
  const priorConversationId = state.conversationId;
  const provider = conversationProvider();
  const pendingTurn = {
    question: trimmed,
    answerBlocks: [],
    hypotheses: [],
    uncertainties: [],
    suggestedQuestions: [],
    evidence: [],
    pending: true,
    draft: "",
    providerLabel: provider.label,
    relistenStatus: "",
    eventId: anchorEventId,
    conversationId: priorConversationId,
  };
  turnCollection.push(pendingTurn);
  renderConversationTurns();
  ui.conversationQuestion.value = "";
  ui.conversationStatus.textContent = provider.locality === "local"
    ? "Reasoning locally from the selected evidence…"
    : "Sending the covenant-filtered evidence packet…";
  const controller = new AbortController();
  state.conversationAbort = controller;
  setConversationBusy(true);
  try {
    let response;
    try {
      response = await streamConversationAsk(payload, pendingTurn, controller.signal);
    } catch (error) {
      if (!error.streamUnavailable) throw error;
      ui.conversationStatus.textContent = "Streaming unavailable · waiting for the complete answer…";
      response = await post("/conversation/ask", payload, { signal: controller.signal });
    }
    const completed = normalizeConversationTurn(response, trimmed);
    const responseConversationId = completed.conversationId || pendingTurn.conversationId || priorConversationId;
    const index = turnCollection.indexOf(pendingTurn);
    if (index >= 0) turnCollection[index] = completed;
    if (state.conversationEvent?.id === anchorEventId) {
      state.conversationId = responseConversationId;
      state.conversationTurns = turnCollection;
    }
    state.conversationByEvent.set(anchorEventId, {
      conversationId: responseConversationId,
      turns: turnCollection,
    });
    if (state.conversationEvent?.id === anchorEventId) renderConversationTurns();
    const fallbackUsed = Boolean(completed.fallback && (typeof completed.fallback === "boolean" || completed.fallback.used !== false));
    if (state.conversationEvent?.id === anchorEventId) {
      ui.conversationStatus.textContent = fallbackUsed
        ? "The selected provider failed validation; a disclosed local fallback was used."
        : "Answer grounded in this listening.";
    }
  } catch (error) {
    if (error.name === "AbortError") {
      const cleanedTurns = turnCollection.filter((turn) => turn !== pendingTurn);
      state.conversationByEvent.set(anchorEventId, {
        conversationId: priorConversationId,
        turns: cleanedTurns,
      });
      if (state.conversationEvent?.id === anchorEventId) {
        state.conversationTurns = cleanedTurns;
        renderConversationTurns();
      }
      return;
    }
    pendingTurn.pending = false;
    pendingTurn.answerBlocks = [{ kind: "error", text: `The reasoning request failed: ${error.message}`, evidence_refs: [] }];
    pendingTurn.draft = "";
    state.conversationByEvent.set(anchorEventId, {
      conversationId: priorConversationId,
      turns: turnCollection,
    });
    if (state.conversationEvent?.id === anchorEventId) {
      renderConversationTurns();
      ui.conversationQuestion.value = trimmed;
      ui.conversationStatus.textContent = "No other cloud provider was tried. You can retry or choose local structured reasoning.";
    }
    logActivity(`Conversation: ${error.message}`, "error");
  } finally {
    if (state.conversationAbort === controller) {
      state.conversationAbort = null;
      setConversationBusy(false);
      ui.conversationQuestion.focus();
    }
  }
}

ui.conversationClose?.addEventListener("click", closeConversation);
ui.conversationForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  submitConversation(ui.conversationQuestion.value);
});
ui.conversationQuestion?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    event.preventDefault();
    ui.conversationForm.requestSubmit();
  }
});
ui.conversationPanel?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeConversation();
});
ui.conversationProvider?.addEventListener("change", async () => {
  await fillReasoningModelSelect(ui.conversationModel, ui.conversationProvider.value, null);
  updateConversationPrivacySummary();
});
for (const input of [ui.conversationTranscript, ui.conversationMemory, ui.conversationRelisten]) {
  input?.addEventListener("change", updateConversationPrivacySummary);
}

/* ────────────────────────────── boot ────────────────────────────── */

refreshHealth().finally(() => {
  refreshHistory();
});
refreshMobileRemote();
loadManifest();
refreshMemory();
refreshCovenant();
refreshMicDevices(false); // load input devices by default, no permission prompt
connectStream();
setInterval(refreshHealth, 20000);
logActivity("Dashboard ready");
window.addEventListener("pagehide", () => {
  stopMonitor();
  stopRecordTimer();
  // A recording in flight must not leave the microphone hot after the page goes away.
  if (state.recorder) {
    state.recorder.stream?.getTracks().forEach((track) => track.stop());
    state.recorder = null;
  }
});
