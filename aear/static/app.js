/* hmm dashboard — one listen surface, one daemon, every surface in sync.
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
  lastJson: null,
  audioDir: "",
  engine: null,
  phase: "idle", // idle | waiting | recording | analyzing
  recorder: null,
  recordedChunks: [],
  captureHintTimer: null,
  abortController: null,
  sonicfieldAvailable: false,
  micDevices: [],
  monitor: null, // {stream, ctx, analyser, raf, peak}
};

const el = (id) => document.getElementById(id);
const ui = {
  daemonDot: el("daemonDot"),
  listenCard: el("listenCard"),
  listenButton: el("listenButton"),
  listenLabel: el("listenLabel"),
  listenStatus: el("listenStatus"),
  captureSeconds: el("captureSeconds"),
  systemPanel: el("systemPanel"),
  systemRoute: el("systemRoute"),
  micPanel: el("micPanel"),
  micDevice: el("micDevice"),
  micRefresh: el("micRefresh"),
  micMonitor: el("micMonitor"),
  micMeterFill: el("micMeterFill"),
  micMeterPeak: el("micMeterPeak"),
  filePanel: el("filePanel"),
  browseFile: el("browseFile"),
  presetRow: el("presetRow"),
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
  resultActions: el("resultActions"),
  evidenceChip: el("evidenceChip"),
  askToggle: el("askToggle"),
  askRow: el("askRow"),
  askInput: el("askInput"),
  askSend: el("askSend"),
  askResult: el("askResult"),
  rememberButton: el("rememberButton"),
  wikiButton: el("wikiButton"),
  jsonToggle: el("jsonToggle"),
  jsonOutput: el("jsonOutput"),
  wikiCard: el("wikiCard"),
  wikiClose: el("wikiClose"),
  wikiQuery: el("wikiQuery"),
  wikiGo: el("wikiGo"),
  wikiTerms: el("wikiTerms"),
  wikiGroups: el("wikiGroups"),
  historyList: el("historyList"),
  historyNote: el("historyNote"),
  memorySearch: el("memorySearch"),
  memoryGo: el("memoryGo"),
  memoryList: el("memoryList"),
  footAudioDir: el("footAudioDir"),
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
      if (body && body.detail) detail = String(body.detail);
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
    ui.daemonDot.className = "dot ok";
    ui.daemonDot.title = `daemon :${health.port}`;
    state.audioDir = health.audio_dir || "";
    state.sonicfieldAvailable = Boolean(health.sonicfield && health.sonicfield.available);
    ui.audioDirNote.textContent = state.audioDir;
    ui.footAudioDir.textContent = state.audioDir;
    if (!ui.audioPath.value && state.audioDir) ui.audioPath.placeholder = `${state.audioDir}/…`;
    if (state.lastEvent) {
      ui.wikiButton.disabled = !state.sonicfieldAvailable;
    }
    renderEngine(health.engine);
  } catch (_) {
    ui.daemonDot.className = "dot bad";
    ui.daemonDot.title = "daemon offline";
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

async function refreshSystemRoute() {
  try {
    const manifest = await fetchJson("/native/system-audio/routes");
    const route = (manifest.routes || [])[0];
    if (route) ui.systemRoute.textContent = String(route.label || "display system mix").toLowerCase();
  } catch (_) { /* keep default */ }
}

/* ─────────────────────────────── SSE ────────────────────────────── */

function connectStream() {
  const stream = new EventSource("/events/stream");
  stream.onopen = () => { ui.daemonDot.className = "dot ok"; };
  stream.onerror = () => { ui.daemonDot.className = "dot bad"; };
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
        if (state.phase === "idle" || state.phase === "waiting") setPhase("analyzing");
        setListenStatus(`Listening (${data.route_preset || "basic"})…`, "active");
        break;
      case "listen_completed":
        clearCaptureHint();
        setPhase("idle");
        setListenStatus("Done.", "");
        if (data.listening_event) renderEvent(data.listening_event, null);
        refreshHistory();
        break;
      case "listen_failed":
        clearCaptureHint();
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
  environment: MODE_ICONS.ecological,
  signal: MODE_ICONS.signal,
  music: MODE_ICONS.music,
  speech: MODE_ICONS.speech,
  memory: ICON('<path d="M6 3h12v18l-6-4-6 4V3Z"/>'),
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
    ui.presetRow.innerHTML = `<span class="empty-note">Presets unavailable.</span>`;
  }
}

function applyPresetSkills() {
  const preset = state.presets.find((item) => item.id === state.preset);
  state.presetSkillIds = preset ? [...preset.skill_ids] : [];
  state.selectedSkills = new Set(state.presetSkillIds);
  updateSkillNote();
}

function renderPresets() {
  ui.presetRow.innerHTML = "";
  for (const preset of state.presets) {
    const button = document.createElement("button");
    button.className = `preset${preset.id === state.preset ? " active" : ""}`;
    button.innerHTML = `${PRESET_ICONS[preset.id] || MODE_ICONS.basic}<span>${escapeHtml(preset.name)}</span>`;
    button.title = presetTooltip(preset);
    button.addEventListener("click", () => {
      state.preset = preset.id;
      applyPresetSkills();
      renderPresets();
      renderSkills();
    });
    ui.presetRow.appendChild(button);
  }
  renderPresetHint();
}

function renderPresetHint() {
  const hint = document.getElementById("presetHint");
  if (!hint) return;
  const preset = state.presets.find((item) => item.id === state.preset);
  if (!preset) {
    hint.textContent = "";
    return;
  }
  const passes = (preset.moss_passes || []).map((name) => PASS_LABELS[name] || name);
  const passText = passes.length ? `model: ${passes.join(" + ")}` : "dsp only, instant";
  hint.textContent = `${preset.description || ""} · ${passText}`;
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

const SOURCE_HINTS = {
  system: "Capture what the computer is playing.",
  mic: "Record from the selected input.",
  file: "Choose or drop an audio file.",
};

document.querySelectorAll(".source").forEach((button) => {
  button.addEventListener("click", () => {
    if (state.phase !== "idle") return;
    document.querySelectorAll(".source").forEach((other) => other.classList.remove("active"));
    button.classList.add("active");
    state.source = button.dataset.source;
    ui.systemPanel.hidden = state.source !== "system";
    ui.micPanel.hidden = state.source !== "mic";
    ui.filePanel.hidden = state.source !== "file";
    setListenStatus(SOURCE_HINTS[state.source] || "", "");
    if (state.source === "mic" && !state.micDevices.length) refreshMicDevices(false);
  });
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
    ui.micDevice.appendChild(new Option("default input", ""));
    state.micDevices.forEach((device, index) => {
      const label = device.label || `input ${index + 1}`;
      ui.micDevice.appendChild(new Option(label, device.deviceId, false, device.deviceId === current));
    });
  } catch (error) {
    setListenStatus(`Inputs: ${error.message}`, "error");
  }
}

ui.micRefresh.addEventListener("click", () => refreshMicDevices(true));
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

ui.browseFile.addEventListener("click", () => ui.fileInput.click());

async function stopListening() {
  if (state.phase === "recording") {
    state.recorder?.stop();
    return;
  }
  if (state.phase === "waiting") {
    clearCaptureHint();
    try { await post("/background/capture-request/cancel", {}); } catch (_) { /* already gone */ }
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
  setListenStatus("Asking the hmm app to capture system audio…", "active");
  try {
    await post("/background/capture-request", { seconds, route_preset: state.preset });
    clearCaptureHint();
    state.captureHintTimer = setTimeout(() => {
      setPhase("idle");
      setListenStatus("No capture yet — open the hmm mac app (system audio is captured through it).", "error");
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

async function listenMic() {
  try {
    const constraints = { audio: ui.micDevice.value ? { deviceId: { exact: ui.micDevice.value } } : true };
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    const recorder = new MediaRecorder(stream);
    state.recorder = recorder;
    state.recordedChunks = [];
    recorder.ondataavailable = (event) => { if (event.data.size) state.recordedChunks.push(event.data); };
    recorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(state.recordedChunks, { type: recorder.mimeType || "audio/webm" });
      state.recorder = null;
      setPhase("analyzing");
      setListenStatus("Uploading recording…", "active");
      try {
        const upload = await uploadBlob(blob, `hmm-mic-${Date.now()}.webm`);
        await analyzePath(upload.path, "mic");
      } catch (error) {
        setPhase("idle");
        if (error.name !== "AbortError") setListenStatus(error.message, "error");
      }
    };
    recorder.start();
    setPhase("recording", "Stop");
    setListenStatus("Recording… press Stop when done.", "active");
  } catch (error) {
    setListenStatus(`Microphone: ${error.message}`, "error");
  }
}

async function uploadBlob(blob, filename) {
  const form = new FormData();
  form.append("file", blob, filename);
  const response = await fetch("/upload", { method: "POST", body: form });
  if (!response.ok) throw new Error(`upload failed (${response.status})`);
  return response.json();
}

ui.fileInput.addEventListener("change", async () => {
  const file = ui.fileInput.files?.[0];
  ui.fileInput.value = "";
  if (!file) return;
  setPhase("analyzing");
  setListenStatus(`Uploading ${file.name}…`, "active");
  try {
    const upload = await uploadBlob(file, file.name);
    await analyzePath(upload.path, "file");
  } catch (error) {
    setPhase("idle");
    if (error.name !== "AbortError") setListenStatus(error.message, "error");
  }
});

["dragover", "dragleave", "drop"].forEach((kind) => {
  ui.listenCard.addEventListener(kind, (event) => {
    event.preventDefault();
    ui.listenCard.classList.toggle("dragover", kind === "dragover");
    if (kind !== "drop") return;
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    setPhase("analyzing");
    setListenStatus(`Uploading ${file.name}…`, "active");
    uploadBlob(file, file.name)
      .then((upload) => analyzePath(upload.path, "file"))
      .catch((error) => {
        setPhase("idle");
        if (error.name !== "AbortError") setListenStatus(error.message, "error");
      });
  });
});

ui.analyzePath.addEventListener("click", () => {
  const path = ui.audioPath.value.trim();
  if (!path) return setListenStatus("Enter an audio path first.", "error");
  setPhase("analyzing");
  analyzePath(path, "path").catch((error) => {
    setPhase("idle");
    if (error.name !== "AbortError") setListenStatus(error.message, "error");
  });
});

async function analyzePath(path, sourceKind) {
  setListenStatus(`Listening (${state.preset})…`, "active");
  const body = { path, route_preset: state.preset };
  const skills = selectedSkillIds();
  if (skills) body.enabled_skill_ids = skills;
  state.abortController = new AbortController();
  try {
    const result = await post("/listen-event", body, { signal: state.abortController.signal });
    setPhase("idle");
    setListenStatus("Done.", "");
    renderEvent(result.listening_event, result);
    refreshHistory();
  } finally {
    state.abortController = null;
  }
}

/* ───────────────────────────── rendering ────────────────────────── */

function renderEvent(event, fullResponse) {
  if (!event) return;
  state.lastEvent = event;
  state.lastJson = fullResponse || { listening_event: event };
  const aggregate = event.aggregate || {};

  ui.resultTitle.textContent = aggregate.title || "Listening event";
  ui.resultSummary.textContent = aggregate.short_summary || aggregate.detailed_summary || "";
  ui.resultSummary.classList.remove("placeholder");

  const evidence = event.routes?.[0]?.structured?.evidence_level;
  if (evidence) {
    ui.evidenceChip.hidden = false;
    ui.evidenceChip.dataset.level = evidence;
    ui.evidenceChip.textContent = String(evidence).replaceAll("_", " ");
  } else {
    ui.evidenceChip.hidden = true;
  }

  ui.resultTags.innerHTML = (event.tags || [])
    .slice(0, 8)
    .map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`)
    .join("");

  ui.resultBody.innerHTML = buildResultBody(event);
  ui.resultActions.hidden = false;
  ui.askResult.hidden = true;
  ui.jsonOutput.hidden = true;
  ui.rememberButton.disabled = false;
  ui.rememberButton.textContent = event.memory?.saved_trace_id ? "Remembered" : "Remember";
  ui.wikiButton.disabled = !state.sonicfieldAvailable;
  ui.wikiButton.title = state.sonicfieldAvailable ? "" : "Sonic Field root not found";
}

function buildResultBody(event) {
  const aggregate = event.aggregate || {};
  const claims = event.routes?.[0]?.structured?.claim_summary || {};
  const parts = [];

  const summaryPrefix = (aggregate.short_summary || "").slice(0, 60);
  const hypotheses = (aggregate.hypotheses || []).filter(
    (hypothesis) => !summaryPrefix || !String(hypothesis.statement || "").startsWith(summaryPrefix)
  );
  if (hypotheses.length) {
    parts.push(`<div class="block"><h4>Hypotheses</h4><ul>${hypotheses
      .slice(0, 6)
      .map(
        (hypothesis) =>
          `<li><span class="conf ${escapeHtml(hypothesis.confidence || "")}">${escapeHtml(hypothesis.confidence || "")}</span><span>${escapeHtml(hypothesis.statement)}</span></li>`
      )
      .join("")}</ul></div>`);
  }

  const heard = (claims.heard || []).slice(0, 10);
  if (heard.length) {
    parts.push(`<div class="block"><h4>Heard</h4><ul>${heard
      .map((claim) => `<li><span class="conf ${escapeHtml(claim.confidence || "")}">${escapeHtml(claim.confidence || "")}</span><span>${escapeHtml(claim.statement)}</span></li>`)
      .join("")}</ul></div>`);
  }

  const interpreted = (claims.interpreted || []).slice(0, 6);
  if (interpreted.length) {
    parts.push(`<div class="block"><h4>Interpreted</h4><ul>${interpreted
      .map((claim) => `<li><span class="conf ${escapeHtml(claim.confidence || "")}">${escapeHtml(claim.confidence || "")}</span><span>${escapeHtml(claim.statement)}</span></li>`)
      .join("")}</ul></div>`);
  }

  const measured = claims.measured || [];
  if (measured.length) {
    parts.push(`<details class="block"><summary>Measured (${measured.length})</summary><ul>${measured
      .map((claim) => `<li><span class="conf"></span><span>${escapeHtml(claim.statement)}</span></li>`)
      .join("")}</ul></details>`);
  }

  const undetermined = claims.undetermined || [];
  if (undetermined.length) {
    parts.push(`<details class="block"><summary>Undetermined (${undetermined.length})</summary><ul>${undetermined
      .map((claim) => `<li><span class="conf"></span><span>${escapeHtml(claim.statement)}</span></li>`)
      .join("")}</ul></details>`);
  }

  const memoryMatches = event.memory?.similarity || [];
  if (memoryMatches.length) {
    parts.push(`<div class="block"><h4>Memory echoes</h4><ul>${memoryMatches
      .slice(0, 4)
      .map((match) => {
        const trace = match.trace || {};
        const score = typeof match.score === "number" ? ` · ${Math.round(match.score * 100)}%` : "";
        return `<li><span class="conf"></span><span>${escapeHtml(trace.title || trace.id || "trace")}${score}</span></li>`;
      })
      .join("")}</ul></div>`);
  }

  return parts.join("") || `<p class="empty-note">No claims were produced.</p>`;
}

/* ─────────────────────────── result actions ─────────────────────── */

ui.askToggle.addEventListener("click", () => {
  ui.askRow.hidden = !ui.askRow.hidden;
  if (!ui.askRow.hidden) ui.askInput.focus();
});

async function sendAsk() {
  const question = ui.askInput.value.trim();
  if (!question || !state.lastEvent) return;
  ui.askSend.disabled = true;
  try {
    const result = await post("/conversation/ask", { question, event: state.lastEvent });
    const turn = result.turn || result;
    const answer =
      turn.answer ||
      (Array.isArray(turn.known_facts) && turn.known_facts.length ? turn.known_facts.join(" ") : "") ||
      (Array.isArray(turn.uncertainty_notes) && turn.uncertainty_notes.length ? turn.uncertainty_notes.join(" ") : "") ||
      "No grounded answer was produced.";
    ui.askResult.hidden = false;
    ui.askResult.innerHTML = `<div class="ask-q">${escapeHtml(question)}</div><div>${escapeHtml(answer)}</div>`;
    ui.askInput.value = "";
  } catch (error) {
    ui.askResult.hidden = false;
    ui.askResult.innerHTML = `<div class="ask-q">${escapeHtml(question)}</div><div>${escapeHtml(error.message)}</div>`;
  } finally {
    ui.askSend.disabled = false;
  }
}
ui.askSend.addEventListener("click", sendAsk);
ui.askInput.addEventListener("keydown", (event) => { if (event.key === "Enter") sendAsk(); });

ui.rememberButton.addEventListener("click", async () => {
  if (!state.lastEvent) return;
  ui.rememberButton.disabled = true;
  try {
    const result = await post("/memory/remember", { event: state.lastEvent, tags: [state.preset] });
    if (result.trace?.id) {
      state.lastEvent.memory = { ...(state.lastEvent.memory || {}), saved_trace_id: result.trace.id };
      ui.rememberButton.textContent = "Remembered";
    }
    refreshMemory();
  } catch (error) {
    setListenStatus(`Memory: ${error.message}`, "error");
    ui.rememberButton.disabled = false;
  }
});

ui.jsonToggle.addEventListener("click", () => {
  ui.jsonOutput.hidden = !ui.jsonOutput.hidden;
  if (!ui.jsonOutput.hidden) ui.jsonOutput.textContent = JSON.stringify(state.lastJson, null, 2);
});

/* ─────────────────────────── wiki explore ───────────────────────── */

ui.wikiButton.addEventListener("click", () => {
  if (!state.lastEvent) return;
  ui.wikiCard.hidden = false;
  ui.wikiCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  exploreWiki({ event: state.lastEvent });
});

ui.wikiClose.addEventListener("click", () => { ui.wikiCard.hidden = true; });
ui.wikiGo.addEventListener("click", () => exploreWiki({ query: ui.wikiQuery.value.trim() || null, event: state.lastEvent }));
ui.wikiQuery.addEventListener("keydown", (event) => {
  if (event.key === "Enter") exploreWiki({ query: ui.wikiQuery.value.trim() || null, event: state.lastEvent });
});

async function exploreWiki(body) {
  ui.wikiGroups.innerHTML = `<p class="wiki-empty">Searching the Sonic Field…</p>`;
  ui.wikiTerms.textContent = "";
  try {
    const result = await post("/sonicfield/explore", { ...body, limit_per_surface: 5 });
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
    const status = await fetchJson("/background/status");
    const recent = status.state?.recent_events || [];
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
      row.className = "row-item";
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
          ui.memoryList.insertAdjacentHTML("afterbegin", `<p class="empty-note">${escapeHtml(error.message)}</p>`);
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
refreshSystemRoute();
connectStream();
setInterval(refreshHealth, 20000);
window.addEventListener("pagehide", stopMonitor);
