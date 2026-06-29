const state = {
  lastJson: {},
  lastListeningEvent: null,
  lastRouteComparison: null,
  lastSavedTraceId: null,
  conversationId: null,
  lastGeneration: null,
  recentEvents: [],
  pinnedEvents: [],
  recentHistoryPersistent: false,
  historyFilters: {
    route: "all",
    source: "all",
    rerunnable: false,
  },
  comparisonFilters: {
    changedOnly: false,
    minAbsDelta: 0,
  },
  memoryTraces: [],
  mediaRecorder: null,
  recordedChunks: [],
  liveRecorder: null,
  liveStream: null,
  liveSessionId: null,
  backgroundLiveSessionId: null,
  liveUploads: new Set(),
  backgroundPaused: false,
  agentPopoverOpen: false,
  agentSuppressClick: false,
  agentDrag: null,
  akouoManifest: null,
  selectedSkillIds: new Set(),
  agentSettings: {
    visible: true,
    size: "compact",
    pinned: true,
    x: null,
    y: null,
    reduced_motion: false,
  },
};

const elements = {
  statusDot: document.querySelector("#statusDot"),
  statusText: document.querySelector("#statusText"),
  profileValue: document.querySelector("#profileValue"),
  hostValue: document.querySelector("#hostValue"),
  portValue: document.querySelector("#portValue"),
  form: document.querySelector("#taskForm"),
  uploadCard: document.querySelector("#uploadCard"),
  audioFile: document.querySelector("#audioFile"),
  audioPath: document.querySelector("#audioPath"),
  uploadStatus: document.querySelector("#uploadStatus"),
  useSample: document.querySelector("#useSample"),
  recordButton: document.querySelector("#recordButton"),
  recordStatus: document.querySelector("#recordStatus"),
  liveButton: document.querySelector("#liveButton"),
  captureLiveButton: document.querySelector("#captureLiveButton"),
  liveStatus: document.querySelector("#liveStatus"),
  sourceMode: document.querySelector("#sourceMode"),
  audioDevice: document.querySelector("#audioDevice"),
  refreshDevices: document.querySelector("#refreshDevices"),
  sourceStatus: document.querySelector("#sourceStatus"),
  sourceStatusShort: document.querySelector("#sourceStatusShort"),
  systemAudioStatus: document.querySelector("#systemAudioStatus"),
  backgroundStatus: document.querySelector("#backgroundStatus"),
  backgroundCapture: document.querySelector("#backgroundCapture"),
  backgroundPause: document.querySelector("#backgroundPause"),
  backgroundResume: document.querySelector("#backgroundResume"),
  runButton: document.querySelector("#runButton"),
  resultTitle: document.querySelector("#resultTitle"),
  resultSubtitle: document.querySelector("#resultSubtitle"),
  resultPanel: document.querySelector("#resultPanel"),
  renderedResult: document.querySelector("#renderedResult"),
  eventQuestion: document.querySelector("#eventQuestion"),
  askEventQuestion: document.querySelector("#askEventQuestion"),
  conversationResult: document.querySelector("#conversationResult"),
  generationPrompt: document.querySelector("#generationPrompt"),
  deriveGenerationPrompt: document.querySelector("#deriveGenerationPrompt"),
  saveGenerationPrompt: document.querySelector("#saveGenerationPrompt"),
  refreshGenerationHistory: document.querySelector("#refreshGenerationHistory"),
  generationResult: document.querySelector("#generationResult"),
  resultHistory: document.querySelector("#resultHistory"),
  jsonOutput: document.querySelector("#jsonOutput"),
  rerunRouteResult: document.querySelector("#rerunRouteResult"),
  rememberResult: document.querySelector("#rememberResult"),
  forgetResult: document.querySelector("#forgetResult"),
  copyJson: document.querySelector("#copyJson"),
  memoryStatus: document.querySelector("#memoryStatus"),
  memorySearch: document.querySelector("#memorySearch"),
  searchMemory: document.querySelector("#searchMemory"),
  refreshMemory: document.querySelector("#refreshMemory"),
  exportMemory: document.querySelector("#exportMemory"),
  memoryList: document.querySelector("#memoryList"),
  routePreset: document.querySelector("#routePreset"),
  skillManagerStatus: document.querySelector("#skillManagerStatus"),
  skillList: document.querySelector("#skillList"),
  resetPresetSkills: document.querySelector("#resetPresetSkills"),
  timestamps: document.querySelector("#timestamps"),
  question: document.querySelector("#question"),
  thinkingBudget: document.querySelector("#thinkingBudget"),
  agentVisible: document.querySelector("#agentVisible"),
  agentSize: document.querySelector("#agentSize"),
  agentPinned: document.querySelector("#agentPinned"),
  agentReducedMotion: document.querySelector("#agentReducedMotion"),
  agentSettingsStatus: document.querySelector("#agentSettingsStatus"),
  spectralAgent: document.querySelector("#spectralAgent"),
  agentSurface: document.querySelector("#agentSurface"),
  agentStatus: document.querySelector("#agentStatus"),
  agentPopover: document.querySelector("#agentPopover"),
  agentPopoverTitle: document.querySelector("#agentPopoverTitle"),
  agentPopoverMeta: document.querySelector("#agentPopoverMeta"),
  agentQuickCapture: document.querySelector("#agentQuickCapture"),
  agentShowResult: document.querySelector("#agentShowResult"),
  agentTogglePause: document.querySelector("#agentTogglePause"),
  agentHide: document.querySelector("#agentHide"),
  agentBands: [...document.querySelectorAll(".agent-band")],
};

const taskOptions = {
  listen_event: [".listen-option"],
  transcribe: [".transcribe-option"],
  environment: [],
  music: [".thinking-option"],
  qa: [".qa-option", ".thinking-option"],
  report: [],
};

const icons = {
  run: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7Z" /></svg>',
  spinner: '<svg class="spin" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v3" /><path d="M12 18v3" /><path d="M3 12h3" /><path d="M18 12h3" /></svg>',
  mic: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><path d="M12 19v3" /></svg>',
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6h12v12H6z" /></svg>',
  live: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h3l2-6 4 12 2-6h5" /></svg>',
};

const loopbackKeywords = [
  "blackhole",
  "loopback",
  "soundflower",
  "audio hijack",
  "rogue amoeba",
  "vb-audio",
  "virtual cable",
  "cable output",
  "stereo mix",
  "what u hear",
  "monitor of",
  "pipewire monitor",
  "pulse monitor",
];

async function refreshHealth() {
  try {
    const health = await fetchJson("/health");
    elements.statusDot.className = "dot ok";
    elements.statusText.textContent = "Daemon online";
    elements.profileValue.textContent = health.profile || "-";
    elements.hostValue.textContent = health.host || "-";
    elements.portValue.textContent = health.port || "-";
  } catch (error) {
    elements.statusDot.className = "dot error";
    elements.statusText.textContent = "Daemon offline";
    elements.profileValue.textContent = "-";
    elements.hostValue.textContent = "-";
    elements.portValue.textContent = "-";
  }
}

async function refreshSystemAudioStatus() {
  try {
    const status = await fetchJson("/system-audio/status");
    elements.systemAudioStatus.textContent = labelForSystemStatus(status.status);
    if (elements.sourceMode.value === "system_output") {
      elements.sourceStatus.textContent = status.summary || "System audio requires a loopback input device.";
    }
  } catch (error) {
    elements.systemAudioStatus.textContent = "Unavailable";
  }
}

async function refreshBackgroundStatus() {
  try {
    const status = await fetchJson("/background/status");
    const runtime = status.state || {};
    const config = status.config || {};
    state.backgroundPaused = Boolean(config.paused);
    state.backgroundLiveSessionId = runtime.active_live_session_id || null;
    state.recentHistoryPersistent = config.recent_history?.persist !== false;
    syncRecentEvents(runtime.recent_events || [], runtime.pinned_events || []);
    state.agentSettings = normalizeAgentSettings(config);
    applyAgentSettings();
    elements.backgroundStatus.textContent = `${runtime.status || "idle"}${runtime.active_live_session_id ? " / live" : ""}`;
    elements.backgroundCapture.disabled = Boolean(config.paused) || !runtime.active_live_session_id;
    elements.backgroundPause.disabled = Boolean(config.paused);
    elements.backgroundResume.disabled = !Boolean(config.paused);
    syncAgentStateFromBackground(runtime, config);
    updateAgentPopover();
  } catch (error) {
    elements.backgroundStatus.textContent = "Unavailable";
    elements.backgroundCapture.disabled = true;
    elements.agentQuickCapture.disabled = true;
  }
}

async function loadAkouoManifest() {
  try {
    const manifest = await fetchJson("/akouo/skills");
    state.akouoManifest = manifest;
    populateRoutePresets();
    applyPresetSkillDefaults();
    renderSkillManager();
  } catch (error) {
    elements.skillManagerStatus.textContent = "Unavailable";
    elements.skillList.innerHTML = "";
  }
}

function populateRoutePresets() {
  const manifest = state.akouoManifest;
  if (!manifest?.route_presets?.length) return;
  const previous = elements.routePreset.value || "basic";
  elements.routePreset.innerHTML = "";
  for (const preset of manifest.route_presets) {
    const option = document.createElement("option");
    option.value = preset.id;
    option.textContent = preset.name;
    option.dataset.description = preset.description || "";
    elements.routePreset.append(option);
  }
  elements.routePreset.value = manifest.route_presets.some((preset) => preset.id === previous) ? previous : "basic";
}

function applyPresetSkillDefaults() {
  const preset = currentPreset();
  state.selectedSkillIds = new Set(preset?.skill_ids || []);
  renderSkillManager();
}

function renderSkillManager() {
  const manifest = state.akouoManifest;
  if (!manifest?.skills?.length) {
    elements.skillManagerStatus.textContent = "No skills loaded";
    elements.skillList.innerHTML = "";
    return;
  }
  elements.skillList.innerHTML = manifest.skills.map((skill) => `
    <label class="skill-toggle">
      <input type="checkbox" value="${escapeHtml(skill.id)}" ${state.selectedSkillIds.has(skill.id) ? "checked" : ""} />
      <span>
        <b>${escapeHtml(skill.name || skill.id)}</b>
        <small>${escapeHtml(skill.listening_mode || "skill")} / ${escapeHtml(skill.ui_card || "route-card")}</small>
      </span>
    </label>
  `).join("");
  // Toggles are handled by a single delegated listener on #skillList (wired once below),
  // so we no longer rebind per checkbox or full-re-render on every change.
  updateSkillManagerStatus();
}

function updateSkillManagerStatus() {
  const preset = currentPreset();
  const active = activeSkillIds() || [];
  elements.skillManagerStatus.textContent = `${active.length} active / ${preset?.name || "custom"} preset`;
}

function currentPreset() {
  const manifest = state.akouoManifest;
  return manifest?.route_presets?.find((preset) => preset.id === elements.routePreset.value) || null;
}

function activeSkillIds() {
  if (state.selectedSkillIds.size) return [...state.selectedSkillIds];
  const preset = currentPreset();
  return preset?.skill_ids ? [...preset.skill_ids] : null;
}

function syncAgentStateFromBackground(runtime, config) {
  if (config.paused) {
    setAgentState("paused");
  } else if (runtime.status === "capturing") {
    setAgentState("capturing");
  } else if (runtime.status === "error") {
    setAgentState("error");
  } else if (runtime.status === "result_ready") {
    setAgentState("result");
  } else if (runtime.active_live_session_id) {
    setAgentState("listening");
  } else if (!state.lastJson || !Object.keys(state.lastJson).length) {
    setAgentState("idle");
  }
}

async function refreshInputDevices({ requestPermission = false } = {}) {
  try {
    ensureRecordingSupport();
    if (requestPermission) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((track) => track.stop());
    }
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices.filter((device) => device.kind === "audioinput");
    const previous = elements.audioDevice.value;
    elements.audioDevice.innerHTML = '<option value="">Default input</option>';
    for (const device of inputs) {
      const option = document.createElement("option");
      option.value = device.deviceId;
      option.textContent = device.label || `Input ${elements.audioDevice.options.length}`;
      option.dataset.label = device.label || "";
      option.dataset.loopback = String(isLoopbackLabel(device.label || ""));
      elements.audioDevice.append(option);
    }
    if ([...elements.audioDevice.options].some((option) => option.value === previous)) {
      elements.audioDevice.value = previous;
    }
    syncSourceUi();
  } catch (error) {
    elements.sourceStatus.textContent = error.message || "Could not read audio devices.";
    setAgentState("error");
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function selectedTask() {
  return new FormData(elements.form).get("task");
}

function syncTaskOptions() {
  document.querySelectorAll(".option").forEach((node) => node.classList.add("hidden"));
  for (const selector of taskOptions[selectedTask()] || []) {
    document.querySelectorAll(selector).forEach((node) => node.classList.remove("hidden"));
  }
}

function setBusy(isBusy) {
  elements.runButton.disabled = isBusy;
  elements.form.setAttribute("aria-busy", String(isBusy));
  elements.resultPanel.setAttribute("aria-busy", String(isBusy));
  setButtonContent(elements.runButton, isBusy ? "spinner" : "run", isBusy ? "Listening" : "Listen");
  if (isBusy) {
    setAgentState("analyzing");
  } else if (elements.spectralAgent?.dataset.state === "error") {
    updateAgentPopover();
  } else if (state.lastJson && Object.keys(state.lastJson).length) {
    setAgentState("result");
  } else {
    setAgentState(state.liveSessionId ? "listening" : "idle");
  }
}

async function runTask(event) {
  event.preventDefault();
  const path = elements.audioPath.value.trim();
  if (!path) {
    showError(new Error("Audio path is required."));
    return;
  }

  setBusy(true);
  elements.resultTitle.textContent = "Listening";
  elements.resultSubtitle.textContent = "The first model load can take several seconds.";
  elements.renderedResult.innerHTML = "";

  try {
    const task = selectedTask();
    let result;
    state.lastListeningEvent = null;
    state.lastRouteComparison = null;
    state.lastSavedTraceId = null;
    resetConversation();
    elements.rememberResult.disabled = true;
    elements.forgetResult.disabled = true;
    elements.rerunRouteResult.disabled = true;
    if (task === "listen_event") {
      result = await analyzeListenEventPath(path);
    } else if (task === "transcribe") {
      result = await post("/transcribe", { path, timestamps: elements.timestamps.value });
      renderTranscription(result);
    } else if (["environment", "music"].includes(task)) {
      result = await post("/moss-analysis", {
        path,
        mode: task,
        thinking_budget: task === "music" ? Number(elements.thinkingBudget.value || 0) : null,
      });
      renderMossAnalysis(result);
    } else if (task === "qa") {
      result = await post("/qa", {
        path,
        question: elements.question.value.trim(),
        thinking_budget: Number(elements.thinkingBudget.value || 0),
      });
      renderQa(result);
    } else {
      result = await post("/report", { path, profile: "web" });
      renderReport(result);
    }
    setJson(result);
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function analyzeListenEventPath(path, { title = "Listening", privacyMode = "session" } = {}) {
  elements.resultTitle.textContent = title;
  elements.resultSubtitle.textContent = "Routing through AKOUO and local listening contracts.";
  elements.renderedResult.innerHTML = "";
  const result = await post("/listen-event", {
    path,
    route_preset: elements.routePreset.value,
    enabled_skill_ids: activeSkillIds(),
    privacy_mode: privacyMode,
  });
  state.lastListeningEvent = result.listening_event || null;
  state.lastRouteComparison = null;
  resetConversation();
  syncRecentEventsFromBackground(result.background);
  renderListeningEvent(state.lastListeningEvent);
  elements.rememberResult.disabled = !state.lastListeningEvent;
  setJson(result);
  return result;
}

async function uploadFile(file, label = "Uploaded", options = {}) {
  if (!file) return;
  elements.uploadStatus.textContent = `${label}: uploading ${file.name || "recording"}...`;
  const body = new FormData();
  body.append("file", file, file.name || "recording.webm");
  try {
    const response = await fetch("/upload", { method: "POST", body });
    if (!response.ok) throw new Error(await response.text());
    const result = await response.json();
    elements.audioPath.value = result.path;
    const processing = result.processing || {};
    const codecStatus = processing.decoded_to_wav ? "decoded to PCM WAV" : "ready";
    elements.uploadStatus.textContent = `${label}: ${codecStatus}; MOSS will read 16 kHz mono`;
    setAgentState("result");
    setJson({ upload: result });
    if (options.analyze) {
      setBusy(true);
      elements.uploadStatus.textContent = `${label}: analyzing ${file.name || "recording"}...`;
      await analyzeListenEventPath(result.path, { title: `${label} listening` });
      elements.uploadStatus.textContent = `${label}: analyzed ${file.name || "recording"}.`;
    }
  } catch (error) {
    elements.uploadStatus.textContent = `${label}: upload failed`;
    setAgentState("error");
    showError(error);
  } finally {
    if (options.analyze) setBusy(false);
  }
}

function selectedDeviceOption() {
  return elements.audioDevice.options[elements.audioDevice.selectedIndex] || null;
}

function selectedDeviceLabel() {
  const option = selectedDeviceOption();
  return option?.dataset.label || option?.textContent || "";
}

function selectedSourceMetadata() {
  const deviceLabel = selectedDeviceLabel();
  const sourceMode = elements.sourceMode.value;
  const loopback = isLoopbackLabel(deviceLabel);
  const sourceType = sourceMode === "system_output" || loopback ? "system_output" : "live_input";
  return {
    source_type: sourceType,
    source_label: deviceLabel || (sourceType === "system_output" ? "System audio" : "Live input"),
    device_id: elements.audioDevice.value || null,
  };
}

function selectedAudioConstraints() {
  const deviceId = elements.audioDevice.value;
  if (!deviceId) return { audio: true };
  return { audio: { deviceId: { exact: deviceId } } };
}

function validateSelectedSource() {
  if (elements.sourceMode.value !== "system_output") return;
  const label = selectedDeviceLabel();
  if (!label || !isLoopbackLabel(label)) {
    throw new Error("Select a loopback input device before using System audio.");
  }
}

async function toggleRecording() {
  if (state.mediaRecorder && state.mediaRecorder.state === "recording") {
    state.mediaRecorder.stop();
    return;
  }
  let stream = null;
  try {
    ensureRecordingSupport();
    validateSelectedSource();
    stream = await navigator.mediaDevices.getUserMedia(selectedAudioConstraints());
    state.recordedChunks = [];
    const mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
    state.mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    state.mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) state.recordedChunks.push(event.data);
    };
    state.mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(state.recordedChunks, { type: state.mediaRecorder.mimeType || "audio/webm" });
      const file = new File([blob], `hmm-recording-${Date.now()}.webm`, { type: blob.type });
      elements.recordButton.classList.remove("recording");
      elements.recordButton.setAttribute("aria-pressed", "false");
      setButtonContent(elements.recordButton, "mic", "Record");
      elements.recordStatus.textContent = "Uploading recording";
      await uploadFile(file, "Recording");
      elements.recordStatus.textContent = "Mic idle";
    };
    state.mediaRecorder.start();
    elements.recordButton.classList.add("recording");
    elements.recordButton.setAttribute("aria-pressed", "true");
    setButtonContent(elements.recordButton, "stop", "Stop");
    elements.recordStatus.textContent = "Recording...";
    setAgentState("capturing");
  } catch (error) {
    if (stream) stream.getTracks().forEach((track) => track.stop());
    elements.recordStatus.textContent = "Mic unavailable";
    setAgentState("error");
    showError(error);
  }
}

async function toggleLive() {
  if (state.liveRecorder && state.liveRecorder.state === "recording") {
    await stopLive();
    return;
  }
  try {
    validateSelectedSource();
    const source = selectedSourceMetadata();
    const live = await post("/live/start", {
      ring_seconds: 60,
      vad_threshold_dbfs: -45,
      ...source,
    });
    state.liveSessionId = live.session_id;
    ensureRecordingSupport();
    state.liveStream = await navigator.mediaDevices.getUserMedia(selectedAudioConstraints());
    const mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
    state.liveRecorder = new MediaRecorder(state.liveStream, mimeType ? { mimeType } : undefined);
    state.liveRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) trackLiveUpload(uploadLiveChunk(event.data));
    };
    state.liveRecorder.start(2000);
    elements.liveButton.classList.add("live-active");
    elements.liveButton.setAttribute("aria-pressed", "true");
    elements.captureLiveButton.disabled = false;
    setButtonContent(elements.liveButton, "stop", "Stop Live");
    elements.liveStatus.textContent = `${source.source_label} / ${state.liveSessionId}`;
    setAgentState("listening");
    await refreshBackgroundStatus();
  } catch (error) {
    await cleanupLiveStartFailure();
    elements.liveStatus.textContent = "Live unavailable";
    setAgentState("error");
    showError(error);
  }
}

async function uploadLiveChunk(blob) {
  const sessionId = state.liveSessionId;
  if (!sessionId) return;
  const body = new FormData();
  const file = new File([blob], `hmm-live-${Date.now()}.webm`, { type: blob.type || "audio/webm" });
  body.append("session_id", sessionId);
  body.append("file", file, file.name);
  try {
    const response = await fetch("/live/ingest", { method: "POST", body });
    if (!response.ok) throw new Error(await response.text());
    const result = await response.json();
    const latest = result.latest_chunk || {};
    elements.liveStatus.textContent = `${latest.vad_active ? "Active" : "Quiet"} / ${result.chunk_count} chunks`;
    updateAgentFromChunk(latest);
    setJson({ live: result });
  } catch (error) {
    // A transient per-chunk ingest failure must not hijack the result panel or strobe the
    // agent to "error" every 2 seconds while recording continues; surface it quietly.
    elements.liveStatus.textContent = "Live ingest hiccup; still recording";
    console.warn("live ingest chunk failed", error);
  }
}

async function captureLive() {
  if (!state.liveSessionId) {
    showError(new Error("Live listening is not active."));
    return;
  }
  elements.captureLiveButton.disabled = true;
  setAgentState("capturing");
  try {
    const result = await post("/live/capture", {
      session_id: state.liveSessionId,
      seconds: 10,
      analyze: true,
      route_preset: elements.routePreset.value || "basic",
      enabled_skill_ids: activeSkillIds(),
    });
    if (result.path) elements.audioPath.value = result.path;
    state.lastListeningEvent = result.listening_event || null;
    state.lastRouteComparison = null;
    resetConversation();
    syncRecentEventsFromBackground(result.background);
    if (state.lastListeningEvent) {
      renderListeningEvent(state.lastListeningEvent);
      elements.rememberResult.disabled = false;
    }
    setJson(result);
    setAgentState("result");
  } catch (error) {
    setAgentState("error");
    showError(error);
  } finally {
    elements.captureLiveButton.disabled = !state.liveSessionId;
  }
}

async function stopLive() {
  const sessionId = state.liveSessionId;
  await stopLiveRecorder();
  if (state.liveUploads.size) await Promise.allSettled([...state.liveUploads]);
  if (state.liveStream) {
    state.liveStream.getTracks().forEach((track) => track.stop());
  }
  state.liveRecorder = null;
  state.liveStream = null;
  state.liveSessionId = null;
  elements.liveButton.classList.remove("live-active");
  elements.liveButton.setAttribute("aria-pressed", "false");
  elements.captureLiveButton.disabled = true;
  setButtonContent(elements.liveButton, "live", "Live");
  if (!sessionId) {
    elements.liveStatus.textContent = "Live idle";
    return;
  }
  try {
    const result = await post("/live/stop", { session_id: sessionId });
    elements.liveStatus.textContent = `Stopped / ${result.chunk_count} chunks`;
    setAgentState("idle");
    setJson({ live: result });
    await refreshBackgroundStatus();
  } catch (error) {
    elements.liveStatus.textContent = "Stop failed";
    setAgentState("error");
    showError(error);
  }
}

async function backgroundQuickCapture() {
  elements.backgroundCapture.disabled = true;
  setAgentState("capturing");
  try {
    const result = await post("/background/capture", {
      seconds: 10,
      route_preset: elements.routePreset.value || "basic",
      enabled_skill_ids: activeSkillIds(),
    });
    state.lastListeningEvent = result.listening_event || null;
    state.lastRouteComparison = null;
    resetConversation();
    syncRecentEventsFromBackground(result.background);
    if (state.lastListeningEvent) {
      renderListeningEvent(state.lastListeningEvent);
      elements.rememberResult.disabled = false;
    }
    setJson(result);
    setAgentState(state.lastListeningEvent ? "result" : "idle");
    await refreshBackgroundStatus();
  } catch (error) {
    setAgentState("error");
    showError(error);
    await refreshBackgroundStatus();
  }
}

async function setBackgroundPaused(paused) {
  try {
    const result = await post(paused ? "/background/pause" : "/background/resume", {});
    setJson({ background: result });
    await refreshBackgroundStatus();
  } catch (error) {
    setAgentState("error");
    showError(error);
  }
}

function trackLiveUpload(promise) {
  state.liveUploads.add(promise);
  promise.finally(() => state.liveUploads.delete(promise));
  return promise;
}

function stopLiveRecorder() {
  const recorder = state.liveRecorder;
  if (!recorder || recorder.state !== "recording") return Promise.resolve();
  return new Promise((resolve) => {
    recorder.addEventListener("stop", resolve, { once: true });
    recorder.stop();
  });
}

async function cleanupLiveStartFailure() {
  const sessionId = state.liveSessionId;
  if (state.liveStream) {
    state.liveStream.getTracks().forEach((track) => track.stop());
  }
  state.liveRecorder = null;
  state.liveStream = null;
  state.liveSessionId = null;
  elements.liveButton.classList.remove("live-active");
  elements.liveButton.setAttribute("aria-pressed", "false");
  elements.captureLiveButton.disabled = true;
  setButtonContent(elements.liveButton, "live", "Live");
  if (sessionId) {
    try {
      await post("/live/stop", { session_id: sessionId });
    } catch (error) {
      console.warn("Could not close failed live session", error);
    }
  }
}

function post(url, body) {
  return fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function ensureRecordingSupport() {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    throw new Error("Browser audio capture is not available in this context.");
  }
}

function setButtonContent(button, icon, label) {
  button.innerHTML = `${icons[icon] || ""} ${escapeHtml(label)}`;
}

function setJson(result) {
  state.lastJson = result;
  elements.jsonOutput.textContent = JSON.stringify(result, null, 2);
}

function resetConversation() {
  state.conversationId = null;
  if (elements.conversationResult) elements.conversationResult.innerHTML = "";
  resetGeneration();
}

function resetGeneration() {
  state.lastGeneration = null;
  if (elements.generationPrompt) elements.generationPrompt.value = "";
  if (elements.generationResult) elements.generationResult.innerHTML = "";
  if (elements.deriveGenerationPrompt) elements.deriveGenerationPrompt.disabled = !state.lastListeningEvent;
  if (elements.saveGenerationPrompt) elements.saveGenerationPrompt.disabled = true;
}

function syncRecentEventsFromBackground(background) {
  const runtime = background?.state || {};
  syncRecentEvents(runtime.recent_events || [], runtime.pinned_events || []);
}

function syncRecentEvents(events, pinnedEvents = state.pinnedEvents) {
  state.recentEvents = Array.isArray(events) ? events.filter((event) => event && event.id) : [];
  state.pinnedEvents = Array.isArray(pinnedEvents) ? pinnedEvents.filter((event) => event && event.id) : [];
  renderRecentHistory();
}

function syncHistoryPayload(payload) {
  if (!payload) return;
  if (payload.state) {
    syncRecentEvents(payload.state.recent_events || [], payload.state.pinned_events || []);
  } else {
    syncRecentEvents(payload.recent_events || [], payload.pinned_events || []);
  }
}

function renderListeningEvent(event) {
  if (!event) {
    elements.resultTitle.textContent = "No listening event";
    elements.resultSubtitle.textContent = "The daemon did not return an event object.";
    elements.renderedResult.innerHTML = "";
    elements.rerunRouteResult.disabled = true;
    elements.askEventQuestion.disabled = true;
    elements.deriveGenerationPrompt.disabled = true;
    elements.saveGenerationPrompt.disabled = true;
    return;
  }
  const aggregate = event.aggregate || {};
  const features = event.features || {};
  const routes = event.routes || [];
  const memory = event.memory || {};
  const facts = aggregate.signal_facts || [];
  const hypotheses = aggregate.hypotheses || [];
  const warnings = aggregate.warnings || [];
  state.lastSavedTraceId = memory.saved_trace_id || memory.savedTraceId || state.lastSavedTraceId;
  elements.forgetResult.disabled = !state.lastSavedTraceId;
  elements.resultTitle.textContent = aggregate.title || "Listening event";
  elements.resultSubtitle.textContent = `${event.segment?.duration_ms ? (event.segment.duration_ms / 1000).toFixed(2) : "-"} s / ${event.source?.label || "local sound"} / raw audio ${event.raw_audio_policy || "unknown"}`;
  elements.renderedResult.innerHTML = `
    <div class="metric-grid">
      ${metric("Duration", event.segment?.duration_ms ? `${(event.segment.duration_ms / 1000).toFixed(2)} s` : "-")}
      ${metric("Peak", formatNumber(features.peakDbfs, "dBFS"))}
      ${metric("RMS", formatNumber(features.rmsDbfs, "dBFS"))}
      ${metric("Centroid", formatNumber(features.spectralCentroidHz, "Hz"))}
    </div>
    ${listHtml([
      { meta: "Summary", body: aggregate.short_summary || "(empty)" },
      ...facts.slice(0, 5).map((fact) => ({ meta: "Signal fact", body: fact })),
      ...hypotheses.slice(0, 4).map((hypothesis) => ({ meta: `Hypothesis ${hypothesis.confidence || ""}`, body: hypothesis.statement || "" })),
      ...warnings.slice(0, 4).map((warning) => ({ meta: "Uncertainty", body: warning })),
      ...memoryItems(memory),
    ])}
    ${routeComparisonHtml(state.lastRouteComparison)}
    ${nextRouteActionsHtml(aggregate.next_actions || [])}
    ${routeCardsHtml(routes)}
  `;
  elements.rerunRouteResult.disabled = false;
  elements.askEventQuestion.disabled = false;
  elements.deriveGenerationPrompt.disabled = false;
  renderRecentHistory();
  setAgentState("result");
}

function renderTranscription(result) {
  const segments = result.transcript?.segments || [];
  elements.resultTitle.textContent = "Transcript";
  elements.resultSubtitle.textContent = `${segments.length} segment${segments.length === 1 ? "" : "s"} from ${result.engine?.model || "MOSS-Audio"}`;
  elements.renderedResult.innerHTML = listHtml(segments.map((segment) => ({
    meta: range(segment.t0, segment.t1),
    body: segment.text || "(empty)",
  })));
}

async function rememberLatest() {
  if (!state.lastListeningEvent) {
    showError(new Error("No listening event is ready to remember."));
    return;
  }
  try {
    const result = await post("/memory/remember", { event: state.lastListeningEvent, tags: [] });
    state.lastSavedTraceId = result.trace?.id || null;
    if (state.lastSavedTraceId) {
      state.lastListeningEvent.memory = {
        ...(state.lastListeningEvent.memory || {}),
        saved_trace_id: state.lastSavedTraceId,
      };
    }
    setJson(result);
    elements.resultSubtitle.textContent = `Saved to Akousmata: ${result.trace?.id || "trace"}`;
    elements.forgetResult.disabled = !state.lastSavedTraceId;
    setAgentState("memory");
    await refreshMemory();
  } catch (error) {
    setAgentState("error");
    showError(error);
  }
}

async function rerunLatestRoute(routePreset = elements.routePreset.value || "basic", { useSelectedSkills = false } = {}) {
  if (!state.lastListeningEvent) {
    showError(new Error("No listening event is ready for a route rerun."));
    return;
  }
  elements.rerunRouteResult.disabled = true;
  setAgentState("analyzing");
  try {
    const payload = {
      event: state.lastListeningEvent,
      route_preset: routePreset,
    };
    if (useSelectedSkills) {
      payload.enabled_skill_ids = activeSkillIds();
    }
    const result = await post("/listen-event/rerun", payload);
    state.lastListeningEvent = result.listening_event || null;
    state.lastRouteComparison = result.route_comparison || null;
    resetConversation();
    syncRecentEventsFromBackground(result.background);
    state.lastSavedTraceId = state.lastListeningEvent?.memory?.saved_trace_id || null;
    renderListeningEvent(state.lastListeningEvent);
    elements.rememberResult.disabled = !state.lastListeningEvent;
    elements.forgetResult.disabled = !state.lastSavedTraceId;
    setJson(result);
  } catch (error) {
    setAgentState("error");
    showError(error);
  } finally {
    elements.rerunRouteResult.disabled = !state.lastListeningEvent;
  }
}

async function forgetLatest() {
  if (!state.lastSavedTraceId) {
    showError(new Error("No saved Akousmata trace is linked to the latest result."));
    return;
  }
  await forgetTrace(state.lastSavedTraceId);
  state.lastSavedTraceId = null;
  if (state.lastListeningEvent?.memory) {
    state.lastListeningEvent.memory.saved_trace_id = null;
  }
  elements.forgetResult.disabled = true;
  elements.resultSubtitle.textContent = "Saved trace removed from Akousmata.";
}

function renderMossAnalysis(result) {
  const analysis = result.analysis || {};
  const modeLabel = labelForMode(analysis.mode || "analysis");
  elements.resultTitle.textContent = modeLabel;
  elements.resultSubtitle.textContent = result.engine?.model || "MOSS-Audio direct analysis";
  elements.renderedResult.innerHTML = listHtml([
    { meta: `${modeLabel} output`, body: analysis.analysis || "(empty)" },
  ]);
}

function renderQa(result) {
  const qa = result.qa || {};
  elements.resultTitle.textContent = "Question Answer";
  elements.resultSubtitle.textContent = qa.question || "Audio QA";
  elements.renderedResult.innerHTML = listHtml([
    { meta: "Answer", body: qa.answer || "(empty)" },
    ...(qa.reasoning_trace ? [{ meta: "Reasoning trace", body: qa.reasoning_trace }] : []),
  ]);
}

async function askEventQuestion() {
  if (!state.lastListeningEvent) {
    showError(new Error("No listening event is ready for conversation."));
    return;
  }
  const question = elements.eventQuestion.value.trim();
  if (!question) {
    showError(new Error("Question is required."));
    return;
  }
  elements.askEventQuestion.disabled = true;
  try {
    const result = await post("/conversation/ask", {
      event: state.lastListeningEvent,
      conversation_id: state.conversationId,
      question,
      include_memory: true,
      allow_remote_model: false,
    });
    state.conversationId = result.conversation_id || state.conversationId;
    renderConversation(result);
    setJson(result);
  } catch (error) {
    showError(error);
  } finally {
    elements.askEventQuestion.disabled = !state.lastListeningEvent;
  }
}

function renderConversation(result) {
  const turn = result.turn || {};
  const evidence = Array.isArray(turn.evidence) ? turn.evidence : [];
  const facts = Array.isArray(turn.known_facts) ? turn.known_facts : [];
  const hypotheses = Array.isArray(turn.hypotheses) ? turn.hypotheses : [];
  const uncertainty = Array.isArray(turn.uncertainty_notes) ? turn.uncertainty_notes : [];
  const memory = Array.isArray(turn.memory_context) ? turn.memory_context : [];
  elements.conversationResult.innerHTML = listHtml([
    { meta: "Answer", body: turn.answer || "(empty)" },
    ...facts.slice(0, 4).map((fact) => ({ meta: "Known fact", body: fact })),
    ...hypotheses.slice(0, 3).map((item) => ({ meta: "Hypothesis", body: item })),
    ...evidence.slice(0, 5).map((item) => ({ meta: `Evidence / ${item.kind || "event"}`, body: `${item.label || ""}${item.label ? ": " : ""}${item.value || ""}` })),
    ...memory.slice(0, 3).map((item) => ({ meta: "Memory context", body: `${item.title || item.trace_id || "trace"}${item.score ? ` (${Math.round(item.score * 100)}%)` : ""}` })),
    ...uncertainty.slice(0, 3).map((note) => ({ meta: "Uncertainty", body: note })),
  ]);
}

async function deriveGenerationPrompt(promptOverride = null) {
  if (!state.lastListeningEvent) {
    showError(new Error("No listening event is ready for generation prompting."));
    return;
  }
  elements.deriveGenerationPrompt.disabled = true;
  elements.saveGenerationPrompt.disabled = true;
  try {
    const result = await post("/generation/prompt", {
      event: state.lastListeningEvent,
      intent: "transform",
      prompt: promptOverride,
      adapter: "prompt_only",
      generate: false,
    });
    renderGeneration(result);
    setJson({ generation: result });
  } catch (error) {
    showError(error);
  } finally {
    elements.deriveGenerationPrompt.disabled = !state.lastListeningEvent;
    elements.saveGenerationPrompt.disabled = !state.lastGeneration;
  }
}

async function saveGenerationPrompt() {
  const prompt = elements.generationPrompt.value.trim();
  if (!prompt) {
    showError(new Error("Generation prompt is empty."));
    return;
  }
  await deriveGenerationPrompt(prompt);
}

async function refreshGenerationHistory() {
  elements.refreshGenerationHistory.disabled = true;
  try {
    const result = await fetchJson("/generation/history?limit=5");
    renderGenerationHistory(result);
    setJson({ generation_history: result });
  } catch (error) {
    showError(error);
  } finally {
    elements.refreshGenerationHistory.disabled = false;
  }
}

function renderGeneration(record) {
  state.lastGeneration = record;
  elements.generationPrompt.value = record.prompt || "";
  elements.saveGenerationPrompt.disabled = false;
  const adapter = record.adapter || "prompt_only";
  const status = record.status || "prompt_ready";
  const policy = record.raw_audio_policy || "Derived prompt record only.";
  elements.generationResult.innerHTML = listHtml([
    { meta: "Status", body: `${status} / ${adapter}` },
    { meta: "Source", body: record.source_event_id || state.lastListeningEvent?.id || "event" },
    ...(record.negative_prompt ? [{ meta: "Negative prompt", body: record.negative_prompt }] : []),
    { meta: "Policy", body: policy },
  ]);
}

function renderGenerationHistory(result) {
  const records = Array.isArray(result.records) ? result.records : [];
  if (!records.length) {
    elements.generationResult.innerHTML = listHtml([
      { meta: "Generation history", body: "No prompt records yet." },
    ]);
    return;
  }
  elements.generationResult.innerHTML = listHtml(records.map((record) => ({
    meta: `${record.status || "prompt"} / ${record.adapter || "adapter"}`,
    body: `${record.id || "generation"}: ${shortText(record.prompt || "", 140)}`,
  })));
}

function renderReport(report) {
  const features = report.dsp?.features || {};
  const events = report.events || [];
  const transcript = report.transcript?.segments || [];
  elements.resultTitle.textContent = "Perception Report";
  elements.resultSubtitle.textContent = `${report.source?.duration_s?.toFixed?.(2) || "-"} s, ${events.length} events, ${transcript.length} transcript segments`;
  elements.renderedResult.innerHTML = `
    <div class="metric-grid">
      ${metric("Duration", formatNumber(report.source?.duration_s, "s"))}
      ${metric("LUFS", formatNumber(features.integratedLufs, ""))}
      ${metric("Peak", formatNumber(features.peakDbfs, "dBFS"))}
      ${metric("BPM", formatNumber(features.bpmCandidate, ""))}
    </div>
    ${listHtml([
      ...(report.caption?.dense ? [{ meta: "Dense caption", body: report.caption.dense }] : []),
      ...events.slice(0, 6).map((event) => ({ meta: `Event ${range(event.t0, event.t1)}`, body: `${event.label}${event.description ? ` - ${event.description}` : ""}` })),
      ...transcript.slice(0, 4).map((segment) => ({ meta: `Transcript ${range(segment.t0, segment.t1)}`, body: segment.text })),
    ])}
  `;
}

function metric(label, value) {
  const display = value === 0 ? "0" : (value || "-");
  return `<div class="metric"><b>${escapeHtml(display)}</b><span>${escapeHtml(label)}</span></div>`;
}

function labelForMode(mode) {
  const labels = {
    environment: "Environmental Sound",
    music: "Music Analysis",
  };
  return labels[mode] || "MOSS Analysis";
}

function listHtml(items) {
  if (!items.length) {
    return '<div class="item"><small>No rendered items</small>No content returned.</div>';
  }
  return `<div class="item-list">${items.map((item) => `
    <div class="item">
      <small>${escapeHtml(item.meta || "")}</small>
      ${escapeHtml(item.body || "")}
    </div>
  `).join("")}</div>`;
}

function routeComparisonHtml(comparison) {
  if (!comparison) return "";
  const addedRoutes = comparison.added_routes || [];
  const removedRoutes = comparison.removed_routes || [];
  const signalDeltas = filteredSignalDeltas(comparison).slice(0, 4);
  const warningDelta = comparison.warning_delta || {};
  const summaryShift = comparison.summary_shift || {};
  const routeChanged = Boolean(addedRoutes.length || removedRoutes.length);
  const summaryChanged = Boolean(summaryShift.changed);
  const warningsChanged = Boolean((warningDelta.added || []).length || (warningDelta.resolved || []).length);
  const signalChanged = Boolean(signalDeltas.length);
  const routeText = [
    addedRoutes.length ? `Added ${addedRoutes.join(", ")}` : "",
    removedRoutes.length ? `Removed ${removedRoutes.join(", ")}` : "",
  ].filter(Boolean).join("; ") || "Route set unchanged";
  const metrics = [
    { label: "Routes", value: routeText, changed: routeChanged },
    { label: "Summary", value: summaryChanged ? "changed" : "unchanged", changed: summaryChanged },
    { label: "Warnings +", value: (warningDelta.added || []).length, changed: warningsChanged },
    { label: "Warnings -", value: (warningDelta.resolved || []).length, changed: warningsChanged },
  ].filter((item) => !state.comparisonFilters.changedOnly || item.changed);
  return `
    <section class="comparison-panel" aria-label="Route rerun comparison">
      <header>
        <strong>Route comparison</strong>
        <span>${comparison.same_segment ? "same segment" : "segment uncertain"}</span>
      </header>
      <div class="comparison-controls">
        <label><input class="comparison-changed-only" type="checkbox" ${state.comparisonFilters.changedOnly ? "checked" : ""} /> Changed only</label>
        <label>DSP min <input class="comparison-min-delta" type="number" min="0" step="0.5" value="${escapeHtml(state.comparisonFilters.minAbsDelta)}" /></label>
      </div>
      ${metrics.length ? `<div class="comparison-grid">${metrics.map((item) => metric(item.label, item.value)).join("")}</div>` : listHtml([{ meta: "Comparison filter", body: "No changed fields match the active filters." }])}
      ${signalDeltas.length ? `<div class="comparison-deltas">${signalDeltas.map(([key, delta]) => `
        <span>${escapeHtml(delta.label || key)} ${escapeHtml(formatDelta(delta.delta))}</span>
      `).join("")}</div>` : ""}
      ${summaryChanged && (!state.comparisonFilters.changedOnly || summaryChanged) ? listHtml([
        { meta: "Previous summary", body: summaryShift.from || "(empty)" },
        { meta: "Current summary", body: summaryShift.to || "(empty)" },
      ]) : ""}
    </section>
  `;
}

function filteredSignalDeltas(comparison) {
  const minimum = state.comparisonFilters.changedOnly
    ? Math.max(0.000001, Number(state.comparisonFilters.minAbsDelta || 0))
    : Number(state.comparisonFilters.minAbsDelta || 0);
  return Object.entries(comparison.signal_delta || {})
    .filter(([, delta]) => Math.abs(Number(delta.delta || 0)) >= minimum);
}

function renderRecentHistory() {
  if (!elements.resultHistory) return;
  if (!state.recentEvents.length && !state.pinnedEvents.length) {
    elements.resultHistory.innerHTML = "";
    return;
  }
  const routeOptions = historyRouteOptions();
  const sourceOptions = historySourceOptions();
  const visibleEvents = filteredRecentEvents();
  const visiblePinned = filteredPinnedEvents();
  elements.resultHistory.innerHTML = `
    <div class="result-history-header">
      <strong>Recent results</strong>
      <span>${state.recentEvents.length} kept / ${state.pinnedEvents.length} pinned ${state.recentHistoryPersistent ? "persistently" : "in memory"}</span>
    </div>
    <div class="history-management-row">
      <button class="ghost-button history-export" type="button">Export</button>
      <button class="ghost-button history-pin-current" type="button" ${state.lastListeningEvent ? "" : "disabled"}>${isPinned(state.lastListeningEvent?.id) ? "Unpin current" : "Pin current"}</button>
      <button class="ghost-button history-clear" type="button">Clear recents</button>
      <button class="ghost-button history-clear-all" type="button">Clear all</button>
    </div>
    <div class="history-filter-row">
      <label>Route
        <select class="history-filter-route">
          <option value="all">All</option>
          ${routeOptions.map((route) => `<option value="${escapeHtml(route)}" ${state.historyFilters.route === route ? "selected" : ""}>${escapeHtml(route)}</option>`).join("")}
        </select>
      </label>
      <label>Source
        <select class="history-filter-source">
          <option value="all">All</option>
          ${sourceOptions.map((source) => `<option value="${escapeHtml(source)}" ${state.historyFilters.source === source ? "selected" : ""}>${escapeHtml(source)}</option>`).join("")}
        </select>
      </label>
      <label class="history-filter-check"><input class="history-filter-rerunnable" type="checkbox" ${state.historyFilters.rerunnable ? "checked" : ""} /> Rerunnable</label>
    </div>
    ${visiblePinned.length ? `<div class="history-section-label">Pinned</div><div class="history-chip-row pinned-history-row">
      ${visiblePinned.slice(0, 8).map((event) => historyChipHtml(event, { pinned: true })).join("")}
    </div>` : ""}
    ${visibleEvents.length ? `<div class="history-chip-row">
      ${visibleEvents.slice(0, 8).map((event) => historyChipHtml(event, { pinned: isPinned(event.id) })).join("")}
    </div>` : '<div class="history-empty">No recent results match these filters.</div>'}
  `;
}

function historyChipHtml(event, { pinned = false } = {}) {
  const aggregate = event.aggregate || {};
  const routes = Array.isArray(event.routes) ? event.routes.map((route) => route.route_id).filter(Boolean) : [];
  const active = state.lastListeningEvent?.id && state.lastListeningEvent.id === event.id;
  return `
    <div class="history-chip-wrap${active ? " active" : ""}">
      <button class="history-chip" type="button" data-event-id="${escapeHtml(event.id)}">
        <strong>${escapeHtml(aggregate.title || "Listening event")}</strong>
        <span>${escapeHtml(historyEventMeta(event, routes))}</span>
      </button>
      <button class="history-pin ${pinned ? "pinned" : ""}" type="button" data-event-id="${escapeHtml(event.id)}">${pinned ? "Unpin" : "Pin"}</button>
    </div>
  `;
}

function filteredRecentEvents() {
  return state.recentEvents.filter((event) => eventPassesHistoryFilters(event));
}

function filteredPinnedEvents() {
  return state.pinnedEvents.filter((event) => eventPassesHistoryFilters(event));
}

function eventPassesHistoryFilters(event) {
  const routes = Array.isArray(event.routes) ? event.routes.map((route) => route.route_id).filter(Boolean) : [];
  const sourceType = event.source?.type || "unknown";
  const hasUri = Boolean(event.segment?.data_ref?.uri);
  if (state.historyFilters.route !== "all" && !routes.includes(state.historyFilters.route)) return false;
  if (state.historyFilters.source !== "all" && sourceType !== state.historyFilters.source) return false;
  if (state.historyFilters.rerunnable && !hasUri) return false;
  return true;
}

function historyRouteOptions() {
  const routes = new Set();
  for (const event of [...state.recentEvents, ...state.pinnedEvents]) {
    for (const route of event.routes || []) {
      if (route?.route_id) routes.add(route.route_id);
    }
  }
  return [...routes].sort();
}

function historySourceOptions() {
  return [...new Set([...state.recentEvents, ...state.pinnedEvents].map((event) => event.source?.type || "unknown"))].sort();
}

function historyEventMeta(event, routes) {
  const source = event.source?.label || "local sound";
  const routeText = routes.length ? routes.slice(0, 2).join(", ") : "route";
  return `${source} / ${routeText}`;
}

function selectRecentEvent(eventId) {
  const event = [...state.pinnedEvents, ...state.recentEvents].find((candidate) => candidate.id === eventId);
  if (!event) return;
  state.lastListeningEvent = event;
  state.lastRouteComparison = null;
  state.lastSavedTraceId = event.memory?.saved_trace_id || event.memory?.savedTraceId || null;
  resetConversation();
  renderListeningEvent(event);
  elements.rememberResult.disabled = false;
  elements.forgetResult.disabled = !state.lastSavedTraceId;
  setJson({ source: "recent_history", listening_event: event });
}

function isPinned(eventId) {
  return Boolean(eventId && state.pinnedEvents.some((event) => event.id === eventId));
}

async function setHistoryPinned(eventId, pinned) {
  if (!eventId) return;
  try {
    const result = await post("/background/history/pin", { event_id: eventId, pinned });
    syncHistoryPayload(result.history);
    setJson(result);
  } catch (error) {
    showError(error);
  }
}

async function clearHistory(keepPinned) {
  try {
    const result = await post("/background/history/clear", { keep_pinned: keepPinned });
    syncHistoryPayload(result.history);
    setJson(result);
  } catch (error) {
    showError(error);
  }
}

async function exportHistory() {
  try {
    const result = await fetchJson("/background/history/export");
    setJson({ recent_history_export: result });
  } catch (error) {
    showError(error);
  }
}

function memoryItems(memory) {
  const items = [];
  const similar = Array.isArray(memory.similarity) ? memory.similarity : [];
  const ids = Array.isArray(memory.similar_trace_ids) ? memory.similar_trace_ids : [];
  if (memory.saved_trace_id || memory.savedTraceId) {
    items.push({ meta: "Memory", body: `Saved as ${memory.saved_trace_id || memory.savedTraceId}` });
  }
  if (similar.length) {
    items.push({
      meta: "Memory match",
      body: similar.slice(0, 3).map((item) => `${item.trace?.title || item.trace?.id || "trace"} (${Math.round((item.score || 0) * 100)}%)`).join("; "),
    });
  } else if (ids.length) {
    items.push({ meta: "Memory match", body: ids.slice(0, 3).join(", ") });
  }
  return items;
}

function routeCardsHtml(routes) {
  if (!routes.length) return "";
  return `<div class="route-card-grid">${routes.map((route) => {
    const structured = route.structured || {};
    const skillIds = Array.isArray(route.skill_ids) ? route.skill_ids.join(", ") : "";
    return `
      <article class="route-card">
        <header>
          <div>
            <h4>${escapeHtml(route.route_name || route.route_id || "Route")}</h4>
            <small>${escapeHtml(skillIds || route.route_id || "")}</small>
          </div>
          <span class="route-chip">${escapeHtml(structured.ui_card || "route")}</span>
        </header>
        <p>${escapeHtml(route.summary || "No summary returned for this skill.")}</p>
      </article>
    `;
  }).join("")}</div>`;
}

function nextRouteActionsHtml(actions) {
  const routeActions = actions
    .filter((action) => action && action.route_preset && action.id !== "remember")
    .slice(0, 5);
  if (!routeActions.length) return "";
  return `<div class="route-action-row">${routeActions.map((action) => `
    <button class="ghost-button route-rerun" type="button" data-route-preset="${escapeHtml(action.route_preset)}">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-15.3 6.4" /><path d="M3 12A9 9 0 0 1 18.3 5.6" /><path d="M3 18h5v-5" /></svg>
      ${escapeHtml(action.label || `Run ${action.route_preset}`)}
    </button>
  `).join("")}</div>`;
}

async function refreshMemory() {
  try {
    const params = new URLSearchParams();
    const query = elements.memorySearch.value.trim();
    if (query) params.set("q", query);
    const result = await fetchJson(`/memory${params.toString() ? `?${params}` : ""}`);
    state.memoryTraces = result.traces || [];
    elements.memoryStatus.textContent = `${result.trace_count || 0} saved trace${result.trace_count === 1 ? "" : "s"} / raw audio explicit`;
    renderMemoryList();
  } catch (error) {
    elements.memoryStatus.textContent = "Memory unavailable";
    showError(error);
  }
}

function renderMemoryList() {
  if (!state.memoryTraces.length) {
    elements.memoryList.innerHTML = '<div class="memory-empty">No saved traces match this search.</div>';
    return;
  }
  elements.memoryList.innerHTML = state.memoryTraces.map((trace) => {
    const summaries = trace.summaries || {};
    const tags = Array.isArray(trace.tags) ? trace.tags.slice(0, 5) : [];
    const audioPolicy = trace.audioPolicy || {};
    return `
      <article class="memory-trace" data-trace-id="${escapeHtml(trace.id || "")}">
        <header>
          <h4>${escapeHtml(trace.title || "Listening trace")}</h4>
          <p>${escapeHtml(summaries.short || "No summary stored.")}</p>
        </header>
        <div class="memory-meta">
          <span>${escapeHtml(trace.sourceKind || "source")}</span>
          <span>${escapeHtml(trace.rawAudioPolicy || audioPolicy.rawAudioPolicy || "policy")}</span>
          ${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
        </div>
        <div class="memory-trace-actions">
          <button class="ghost-button memory-open" type="button" data-trace-id="${escapeHtml(trace.id || "")}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h12" /><path d="M8 12h12" /><path d="M8 18h12" /><path d="M4 6h.01" /><path d="M4 12h.01" /><path d="M4 18h.01" /></svg>
            Open
          </button>
          <button class="ghost-button memory-forget" type="button" data-trace-id="${escapeHtml(trace.id || "")}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /></svg>
            Forget
          </button>
        </div>
      </article>
    `;
  }).join("");
  // Open/forget clicks are handled by a single delegated listener on #memoryList
  // (wired once below) rather than rebinding a listener per button on every render.
}

async function openMemoryTrace(traceId) {
  if (!traceId) return;
  try {
    const result = await fetchJson(`/memory/trace/${encodeURIComponent(traceId)}`);
    const trace = result.trace || {};
    const event = trace.event || null;
    setJson(result);
    if (event) {
      state.lastListeningEvent = event;
      state.lastRouteComparison = null;
      state.lastSavedTraceId = trace.id || null;
      resetConversation();
      renderListeningEvent(event);
      elements.rememberResult.disabled = false;
      elements.forgetResult.disabled = !state.lastSavedTraceId;
      elements.resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    showError(error);
  }
}

async function forgetTrace(traceId) {
  if (!traceId) return;
  try {
    const result = await post("/memory/forget", { trace_id: traceId });
    setJson({ memory: result });
    if (state.lastSavedTraceId === traceId) {
      state.lastSavedTraceId = null;
      elements.forgetResult.disabled = true;
    }
    await refreshMemory();
  } catch (error) {
    showError(error);
  }
}

async function exportMemory() {
  try {
    const params = new URLSearchParams();
    const query = elements.memorySearch.value.trim();
    if (query) params.set("q", query);
    const result = await fetchJson(`/memory/export${params.toString() ? `?${params}` : ""}`);
    setJson({ memory_export: result });
    elements.memoryStatus.textContent = `Export ready / ${result.trace_count || 0} trace${result.trace_count === 1 ? "" : "s"}`;
  } catch (error) {
    showError(error);
  }
}

function range(t0, t1) {
  if (typeof t0 === "number" && typeof t1 === "number") {
    return `${t0.toFixed(2)}-${t1.toFixed(2)}s`;
  }
  return "untimed";
}

function formatNumber(value, suffix) {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return `${value.toFixed(1)}${suffix ? ` ${suffix}` : ""}`;
}

function formatDelta(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}`;
}

function isLoopbackLabel(label) {
  const lowered = String(label || "").toLowerCase();
  return loopbackKeywords.some((keyword) => lowered.includes(keyword));
}

function syncSourceUi() {
  const source = selectedSourceMetadata();
  const selectedLabel = selectedDeviceLabel();
  const sourceMode = elements.sourceMode.value;
  const loopback = isLoopbackLabel(selectedLabel);
  elements.sourceStatusShort.textContent = source.source_type === "system_output" ? "System audio" : "Live input";
  if (sourceMode === "system_output") {
    elements.sourceStatus.textContent = loopback
      ? `System audio via ${selectedLabel}.`
      : "Select a loopback input device for system audio.";
  } else if (loopback) {
    elements.sourceStatus.textContent = `${selectedLabel} looks like a loopback source.`;
  } else {
    elements.sourceStatus.textContent = selectedLabel ? `Live input: ${selectedLabel}.` : "Live input uses browser permission.";
  }
}

function labelForSystemStatus(status) {
  const labels = {
    needs_loopback_device: "Loopback",
    adapter_pending: "Pending",
    unsupported: "Unsupported",
    ready: "Ready",
  };
  return labels[status] || status || "-";
}

function normalizeAgentSettings(config = {}) {
  const floating = config.floating_agent && typeof config.floating_agent === "object" ? config.floating_agent : {};
  const size = floating.size === "medium" ? "medium" : "compact";
  return {
    visible: config.show_floating_agent !== false && floating.visible !== false,
    size,
    pinned: floating.pinned !== false,
    x: numericAgentSetting(floating.x),
    y: numericAgentSetting(floating.y),
    reduced_motion: Boolean(floating.reduced_motion),
  };
}

function numericAgentSetting(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function applyAgentSettings({ syncControls = true } = {}) {
  const agent = elements.spectralAgent;
  if (!agent) return;
  const settings = state.agentSettings;
  agent.classList.toggle("agent-hidden", !settings.visible);
  agent.dataset.size = settings.size;
  agent.dataset.pinned = String(settings.pinned);
  agent.dataset.reducedMotion = String(settings.reduced_motion);
  if (Number.isFinite(settings.x) && Number.isFinite(settings.y) && settings.visible) {
    const next = clampAgentPosition(settings.x, settings.y);
    agent.style.left = `${next.x}px`;
    agent.style.top = `${next.y}px`;
    agent.style.right = "auto";
    agent.style.bottom = "auto";
  } else {
    agent.style.left = "";
    agent.style.top = "";
    agent.style.right = "";
    agent.style.bottom = "";
  }
  if (syncControls) {
    elements.agentVisible.checked = settings.visible;
    elements.agentSize.value = settings.size;
    elements.agentPinned.checked = settings.pinned;
    elements.agentReducedMotion.checked = settings.reduced_motion;
  }
  updateAgentSettingsStatus();
}

function updateAgentSettingsStatus() {
  const settings = state.agentSettings;
  elements.agentSettingsStatus.textContent = `${settings.visible ? "Visible" : "Hidden"} / ${settings.size}${settings.pinned ? " / pinned" : ""}`;
}

function clampAgentPosition(x, y) {
  const agent = elements.spectralAgent;
  const rect = agent?.getBoundingClientRect();
  const width = rect?.width || (state.agentSettings.size === "medium" ? 154 : 118);
  const height = rect?.height || (state.agentSettings.size === "medium" ? 84 : 64);
  const margin = 8;
  return {
    x: Math.min(Math.max(margin, x), Math.max(margin, window.innerWidth - width - margin)),
    y: Math.min(Math.max(margin, y), Math.max(margin, window.innerHeight - height - margin)),
  };
}

async function persistAgentSettings(changes = {}) {
  state.agentSettings = { ...state.agentSettings, ...changes };
  applyAgentSettings();
  try {
    await post("/background/config", {
      updates: {
        show_floating_agent: Boolean(state.agentSettings.visible),
        floating_agent: {
          visible: Boolean(state.agentSettings.visible),
          size: state.agentSettings.size,
          pinned: Boolean(state.agentSettings.pinned),
          x: state.agentSettings.x,
          y: state.agentSettings.y,
          reduced_motion: Boolean(state.agentSettings.reduced_motion),
        },
      },
    });
    await refreshBackgroundStatus();
  } catch (error) {
    showError(error);
  }
}

function syncAgentSettingsFromControls() {
  persistAgentSettings({
    visible: elements.agentVisible.checked,
    size: elements.agentSize.value === "medium" ? "medium" : "compact",
    pinned: elements.agentPinned.checked,
    reduced_motion: elements.agentReducedMotion.checked,
  });
}

function agentPopoverFocusables() {
  return [...elements.agentPopover.querySelectorAll("button:not([disabled])")];
}

function openAgentPopover() {
  state.agentPopoverOpen = true;
  const rect = elements.spectralAgent.getBoundingClientRect();
  elements.spectralAgent.dataset.popoverAlign = rect.left < 250 ? "left" : "right";
  elements.agentPopover.classList.remove("hidden");
  elements.agentSurface.setAttribute("aria-expanded", "true");
  updateAgentPopover();
  // Move focus into the menu so keyboard and screen-reader users can operate it.
  agentPopoverFocusables()[0]?.focus();
}

function closeAgentPopover({ restoreFocus = false } = {}) {
  state.agentPopoverOpen = false;
  elements.agentPopover.classList.add("hidden");
  elements.agentSurface.setAttribute("aria-expanded", "false");
  if (restoreFocus) elements.agentSurface.focus();
}

function toggleAgentPopover() {
  if (state.agentPopoverOpen) {
    closeAgentPopover({ restoreFocus: true });
  } else {
    openAgentPopover();
  }
}

function updateAgentPopover() {
  if (!elements.agentPopoverMeta) return;
  const agentState = elements.spectralAgent?.dataset.state || "idle";
  const title = state.lastListeningEvent?.aggregate?.title || "hmm";
  const liveId = state.liveSessionId || state.backgroundLiveSessionId;
  let meta = liveId ? `Live buffer ${liveId}` : "No live buffer";
  if (state.backgroundPaused) meta = "Paused";
  if (agentState === "result" && state.lastListeningEvent?.aggregate?.short_summary) {
    meta = state.lastListeningEvent.aggregate.short_summary;
  } else if (agentState === "memory") {
    meta = "Saved to Akousmata";
  } else if (agentState === "error") {
    meta = "Error";
  }
  elements.agentPopoverTitle.textContent = title;
  elements.agentPopoverMeta.textContent = meta;
  elements.agentQuickCapture.disabled = state.backgroundPaused || !liveId;
  elements.agentTogglePause.innerHTML = state.backgroundPaused
    ? `${icons.run} Resume`
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14" /><path d="M16 5v14" /></svg> Pause';
}

function beginAgentDrag(event) {
  if (event.button !== 0) return;
  const rect = elements.spectralAgent.getBoundingClientRect();
  state.agentDrag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startLeft: rect.left,
    startTop: rect.top,
    moved: false,
  };
  elements.agentSurface.setPointerCapture(event.pointerId);
}

function moveAgentDrag(event) {
  const drag = state.agentDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  const dx = event.clientX - drag.startX;
  const dy = event.clientY - drag.startY;
  if (Math.abs(dx) + Math.abs(dy) < 4 && !drag.moved) return;
  drag.moved = true;
  elements.spectralAgent.classList.add("dragging");
  const next = clampAgentPosition(drag.startLeft + dx, drag.startTop + dy);
  elements.spectralAgent.style.left = `${next.x}px`;
  elements.spectralAgent.style.top = `${next.y}px`;
  elements.spectralAgent.style.right = "auto";
  elements.spectralAgent.style.bottom = "auto";
  state.agentSettings.x = next.x;
  state.agentSettings.y = next.y;
}

function endAgentDrag(event) {
  const drag = state.agentDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  elements.spectralAgent.classList.remove("dragging");
  state.agentSuppressClick = drag.moved;
  state.agentDrag = null;
  if (state.agentSuppressClick) {
    persistAgentSettings({ x: state.agentSettings.x, y: state.agentSettings.y });
  }
}

function shortText(value, maxLength = 120) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 3))}...`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setAgentState(agentState) {
  if (!elements.spectralAgent) return;
  elements.spectralAgent.dataset.state = agentState;
  elements.agentStatus.textContent = agentState;
  updateAgentPopover();
}

function updateAgentFromChunk(chunk) {
  // Skip the per-chunk style writes entirely when reduced motion is requested.
  if (!state.agentSettings.reduced_motion) {
    const rms = typeof chunk.rms_dbfs === "number" ? chunk.rms_dbfs : -80;
    const peak = typeof chunk.peak_dbfs === "number" ? chunk.peak_dbfs : -80;
    // Map -60..0 dBFS so real audio grades smoothly instead of saturating near-binary.
    const activity = Math.max(0.08, Math.min(1, (rms + 60) / 60));
    const peakActivity = Math.max(0.08, Math.min(1, (peak + 60) / 60));
    elements.spectralAgent?.style.setProperty("--agent-activity", activity.toFixed(2));
    elements.spectralAgent?.style.setProperty("--agent-peak", peakActivity.toFixed(2));
    elements.agentBands.forEach((bar, index) => {
      const harmonic = Math.abs(Math.sin((index + 1) * 0.74 + peakActivity * 2.2));
      const value = Math.max(0.08, Math.min(1, activity * 0.52 + peakActivity * 0.34 + harmonic * 0.14));
      bar.style.setProperty("--bar", value.toFixed(2));
    });
  }
  setAgentState(chunk.vad_active ? "listening" : "idle");
}

function installDropTarget(node, label, options = {}) {
  if (!node) return;
  node.addEventListener("dragover", (event) => {
    event.preventDefault();
    node.classList.add("drag-over");
  });
  node.addEventListener("dragleave", () => node.classList.remove("drag-over"));
  node.addEventListener("drop", async (event) => {
    event.preventDefault();
    node.classList.remove("drag-over");
    const file = event.dataTransfer?.files?.[0];
    if (file) await uploadFile(file, label, options);
  });
}

function showError(error) {
  elements.resultTitle.textContent = "Request failed";
  elements.resultSubtitle.textContent = "Check the path and daemon logs.";
  elements.renderedResult.innerHTML = listHtml([{ meta: "Error", body: error.message || String(error) }]);
  setJson({ error: error.message || String(error) });
}

elements.form.addEventListener("change", syncTaskOptions);
elements.form.addEventListener("submit", runTask);
elements.audioFile.addEventListener("change", (event) => uploadFile(event.target.files?.[0], "Upload"));
elements.recordButton.addEventListener("click", toggleRecording);
elements.liveButton.addEventListener("click", toggleLive);
elements.captureLiveButton.addEventListener("click", captureLive);
elements.backgroundCapture.addEventListener("click", backgroundQuickCapture);
elements.backgroundPause.addEventListener("click", () => setBackgroundPaused(true));
elements.backgroundResume.addEventListener("click", () => setBackgroundPaused(false));
elements.agentVisible.addEventListener("change", syncAgentSettingsFromControls);
elements.agentSize.addEventListener("change", syncAgentSettingsFromControls);
elements.agentPinned.addEventListener("change", syncAgentSettingsFromControls);
elements.agentReducedMotion.addEventListener("change", syncAgentSettingsFromControls);
elements.sourceMode.addEventListener("change", syncSourceUi);
elements.audioDevice.addEventListener("change", syncSourceUi);
elements.refreshDevices.addEventListener("click", () => refreshInputDevices({ requestPermission: true }));
elements.routePreset.addEventListener("change", applyPresetSkillDefaults);
elements.resetPresetSkills.addEventListener("click", applyPresetSkillDefaults);
elements.useSample.addEventListener("click", () => {
  elements.audioPath.value = "MOSS-Audio/test/test_en.mp3";
  elements.uploadStatus.textContent = "Using bundled sample.";
});
elements.rememberResult.addEventListener("click", rememberLatest);
elements.forgetResult.addEventListener("click", forgetLatest);
elements.rerunRouteResult.addEventListener("click", () => rerunLatestRoute(elements.routePreset.value || "basic", { useSelectedSkills: true }));
elements.askEventQuestion.addEventListener("click", askEventQuestion);
elements.eventQuestion.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    askEventQuestion();
  }
});
elements.deriveGenerationPrompt.addEventListener("click", () => deriveGenerationPrompt());
elements.saveGenerationPrompt.addEventListener("click", saveGenerationPrompt);
elements.refreshGenerationHistory.addEventListener("click", refreshGenerationHistory);
elements.renderedResult.addEventListener("click", (event) => {
  const button = event.target.closest(".route-rerun");
  if (button) {
    rerunLatestRoute(button.dataset.routePreset || "basic");
  }
});
elements.renderedResult.addEventListener("change", (event) => {
  if (event.target.matches(".comparison-changed-only")) {
    state.comparisonFilters.changedOnly = event.target.checked;
    renderListeningEvent(state.lastListeningEvent);
  }
  if (event.target.matches(".comparison-min-delta")) {
    state.comparisonFilters.minAbsDelta = Math.max(0, Number(event.target.value || 0));
    renderListeningEvent(state.lastListeningEvent);
  }
});
elements.resultHistory.addEventListener("click", (event) => {
  const pinButton = event.target.closest(".history-pin");
  if (pinButton) {
    setHistoryPinned(pinButton.dataset.eventId, !pinButton.classList.contains("pinned"));
    return;
  }
  if (event.target.closest(".history-pin-current")) {
    const eventId = state.lastListeningEvent?.id;
    setHistoryPinned(eventId, !isPinned(eventId));
    return;
  }
  if (event.target.closest(".history-export")) {
    exportHistory();
    return;
  }
  if (event.target.closest(".history-clear")) {
    clearHistory(true);
    return;
  }
  if (event.target.closest(".history-clear-all")) {
    clearHistory(false);
    return;
  }
  const button = event.target.closest(".history-chip");
  if (button) {
    selectRecentEvent(button.dataset.eventId);
  }
});
elements.resultHistory.addEventListener("change", (event) => {
  if (event.target.matches(".history-filter-route")) {
    state.historyFilters.route = event.target.value || "all";
    renderRecentHistory();
  }
  if (event.target.matches(".history-filter-source")) {
    state.historyFilters.source = event.target.value || "all";
    renderRecentHistory();
  }
  if (event.target.matches(".history-filter-rerunnable")) {
    state.historyFilters.rerunnable = event.target.checked;
    renderRecentHistory();
  }
});
elements.refreshMemory.addEventListener("click", refreshMemory);
elements.searchMemory.addEventListener("click", refreshMemory);
elements.exportMemory.addEventListener("click", exportMemory);
elements.memorySearch.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    refreshMemory();
  }
});
elements.agentSurface.addEventListener("pointerdown", beginAgentDrag);
elements.agentSurface.addEventListener("pointermove", moveAgentDrag);
elements.agentSurface.addEventListener("pointerup", endAgentDrag);
elements.agentSurface.addEventListener("pointercancel", endAgentDrag);
let agentClickTimer = null;
elements.agentSurface.addEventListener("click", () => {
  if (state.agentSuppressClick) {
    state.agentSuppressClick = false;
    return;
  }
  // Debounce against dblclick so a double-click runs quick-capture WITHOUT also leaving
  // the popover toggled into an arbitrary open/closed state.
  if (agentClickTimer) return;
  agentClickTimer = window.setTimeout(() => {
    agentClickTimer = null;
    toggleAgentPopover();
  }, 220);
});
elements.agentSurface.addEventListener("dblclick", (event) => {
  event.preventDefault();
  if (agentClickTimer) {
    window.clearTimeout(agentClickTimer);
    agentClickTimer = null;
  }
  backgroundQuickCapture();
});
elements.agentSurface.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    toggleAgentPopover();
  }
});
elements.spectralAgent.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  openAgentPopover();
});
elements.agentQuickCapture.addEventListener("click", backgroundQuickCapture);
elements.agentShowResult.addEventListener("click", () => {
  closeAgentPopover();
  elements.resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
});
elements.agentTogglePause.addEventListener("click", () => setBackgroundPaused(!state.backgroundPaused));
elements.agentHide.addEventListener("click", () => {
  closeAgentPopover();
  persistAgentSettings({ visible: false });
});
window.addEventListener("resize", () => {
  if (Number.isFinite(state.agentSettings.x) && Number.isFinite(state.agentSettings.y)) {
    const next = clampAgentPosition(state.agentSettings.x, state.agentSettings.y);
    state.agentSettings.x = next.x;
    state.agentSettings.y = next.y;
    applyAgentSettings({ syncControls: false });
  }
});
installDropTarget(elements.uploadCard, "Drop");
installDropTarget(elements.spectralAgent, "Spectral agent", { analyze: true });
elements.copyJson.addEventListener("click", async () => {
  try {
    if (!navigator.clipboard?.writeText) {
      throw new Error("Clipboard access is not available in this browser context.");
    }
    await navigator.clipboard.writeText(JSON.stringify(state.lastJson, null, 2));
    elements.copyJson.classList.add("copied");
    elements.copyJson.title = "Copied";
    window.setTimeout(() => {
      elements.copyJson.classList.remove("copied");
      elements.copyJson.title = "Copy JSON";
    }, 1200);
  } catch (error) {
    showError(error);
  }
});

elements.skillList.addEventListener("change", (event) => {
  const input = event.target;
  if (!input.matches('input[type="checkbox"]')) return;
  if (input.checked) {
    state.selectedSkillIds.add(input.value);
  } else {
    state.selectedSkillIds.delete(input.value);
    if (!state.selectedSkillIds.size) {
      input.checked = true;
      state.selectedSkillIds.add(input.value);
      showError(new Error("At least one AKOUO listening skill must stay enabled."));
    }
  }
  updateSkillManagerStatus();
});
elements.memoryList.addEventListener("click", (event) => {
  const openButton = event.target.closest(".memory-open");
  if (openButton) {
    openMemoryTrace(openButton.dataset.traceId);
    return;
  }
  const forgetButton = event.target.closest(".memory-forget");
  if (forgetButton) {
    forgetTrace(forgetButton.dataset.traceId);
  }
});
elements.agentPopover.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    closeAgentPopover({ restoreFocus: true });
    return;
  }
  const focusables = agentPopoverFocusables();
  if (!focusables.length) return;
  if (event.key === "Tab") {
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const index = focusables.indexOf(document.activeElement);
    const delta = event.key === "ArrowDown" ? 1 : -1;
    const next = (Math.max(0, index) + delta + focusables.length) % focusables.length;
    focusables[next].focus();
  }
});
document.addEventListener("pointerdown", (event) => {
  if (state.agentPopoverOpen && !elements.spectralAgent.contains(event.target)) {
    closeAgentPopover();
  }
});
window.addEventListener("pagehide", () => {
  // Release mic/stream and best-effort close the daemon live session if the tab is
  // closed or reloaded mid-session, so the microphone isn't held and the session is freed.
  try {
    if (state.liveRecorder && state.liveRecorder.state === "recording") state.liveRecorder.stop();
  } catch (error) {
    console.warn("live recorder stop failed during pagehide", error);
  }
  if (state.liveStream) state.liveStream.getTracks().forEach((track) => track.stop());
  const sessionId = state.liveSessionId;
  if (sessionId && navigator.sendBeacon) {
    navigator.sendBeacon("/live/stop", new Blob([JSON.stringify({ session_id: sessionId })], { type: "application/json" }));
  }
});

syncTaskOptions();
refreshHealth();
refreshSystemAudioStatus();
refreshBackgroundStatus();
loadAkouoManifest();
refreshInputDevices();
refreshMemory();
setAgentState("idle");
