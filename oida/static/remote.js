/* oída remote ear — phone-first capture surface.
 *
 * One PCM recorder, two temporal directions:
 *   past   — while armed, a ring buffer holds only the last N seconds of
 *            microphone audio on the device (older samples are overwritten,
 *            nothing accumulates); the listen button snapshots what was
 *            already heard before the trigger.
 *   future — the listen button records the next N seconds, then stops.
 * Either way the capture is encoded to WAV on the device and posted to
 * /remote/listen, where the daemon runs the full listening pipeline, keeps
 * the sound, writes the akousma into the shared store, and answers with the
 * listening event rendered below.
 */

const $ = (id) => document.getElementById(id);

const state = {
  direction: "past",
  seconds: 30,
  armed: false,          // past mode: ring buffer live
  busy: false,           // capturing or uploading
  audio: null,           // { context, stream, source, processor, ring, future }
  armedAt: null,
  history: [],
  wakeLock: null,
};

/* ── ring buffer: the past, bounded ───────────────────────────────────── */

class Ring {
  constructor(seconds, sampleRate) {
    this.sampleRate = sampleRate;
    this.size = Math.max(1, Math.ceil(seconds * sampleRate));
    this.data = new Float32Array(this.size);
    this.write = 0;
    this.filled = 0;
  }
  push(chunk) {
    for (let i = 0; i < chunk.length; i += 1) {
      this.data[this.write] = chunk[i];
      this.write = (this.write + 1) % this.size;
    }
    this.filled = Math.min(this.size, this.filled + chunk.length);
  }
  snapshot(seconds) {
    const want = Math.min(this.filled, Math.ceil(seconds * this.sampleRate));
    const out = new Float32Array(want);
    let index = (this.write - want % this.size + this.size) % this.size;
    for (let i = 0; i < want; i += 1) {
      out[i] = this.data[index];
      index = (index + 1) % this.size;
    }
    return out;
  }
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset, text) => { for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i)); };
  ascii(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); ascii(8, "WAVE");
  ascii(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  ascii(36, "data"); view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i += 1, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/* ── microphone plumbing (ScriptProcessor: universal incl. iOS Safari) ── */

async function openMic() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error(window.isSecureContext ? "no microphone API in this browser" : "microphone needs HTTPS (serve over private-network HTTPS)");
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
  });
  const context = new (window.AudioContext || window.webkitAudioContext)();
  await context.resume();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const audio = { context, stream, source, processor, ring: null, future: null };
  processor.onaudioprocess = (event) => {
    const chunk = event.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < chunk.length; i += 1) sum += chunk[i] * chunk[i];
    setMeter(Math.sqrt(sum / chunk.length));
    if (audio.ring) audio.ring.push(chunk);
    if (audio.future) {
      audio.future.chunks.push(new Float32Array(chunk));
      audio.future.collected += chunk.length;
      if (audio.future.collected >= audio.future.want) {
        const done = audio.future.done;
        audio.future = null;
        done();
      }
    }
  };
  source.connect(processor);
  processor.connect(context.destination); // required for onaudioprocess on some engines; processor emits silence
  return audio;
}

function closeMic() {
  const audio = state.audio;
  if (!audio) return;
  try { audio.processor.disconnect(); } catch {}
  try { audio.source.disconnect(); } catch {}
  for (const track of audio.stream.getTracks()) track.stop();
  audio.context.close().catch(() => {});
  state.audio = null;
  setMeter(0);
}

let meterFrame = 0;
function setMeter(rms) {
  if (meterFrame) return;
  meterFrame = requestAnimationFrame(() => {
    meterFrame = 0;
    const level = Math.min(1, rms * 6);
    $("meter").firstElementChild.style.width = `${Math.round(level * 100)}%`;
  });
}

async function keepAwake(on) {
  try {
    if (on && "wakeLock" in navigator) state.wakeLock = await navigator.wakeLock.request("screen");
    else if (state.wakeLock) { await state.wakeLock.release(); state.wakeLock = null; }
  } catch {}
}

/* ── the two directions ───────────────────────────────────────────────── */

async function armPast() {
  state.audio = await openMic();
  state.audio.ring = new Ring(state.seconds, state.audio.context.sampleRate);
  state.armed = true;
  state.armedAt = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  await keepAwake(true);
  $("listen").classList.add("armed");
  $("listen").textContent = "capture";
  setStatus(`armed — holding the last ${state.seconds}s, trigger when something has been heard`);
}

async function disarmPast() {
  state.armed = false;
  state.armedAt = null;
  closeMic();
  await keepAwake(false);
  $("listen").classList.remove("armed");
  $("listen").textContent = "listen";
  setStatus("the ear is quiet");
}

async function capturePast() {
  const ring = state.audio?.ring;
  if (!ring || !ring.filled) { setStatus("nothing in the buffer yet — let it hear for a moment", true); return; }
  const samples = ring.snapshot(state.seconds);
  const heard = (samples.length / ring.sampleRate).toFixed(1);
  await sendCapture(samples, state.audio.context.sampleRate, { heardSeconds: heard, armedAt: state.armedAt });
}

async function captureFuture() {
  state.audio = await openMic();
  const context = state.audio.context;
  const sampleRate = context.sampleRate;
  const want = Math.ceil(state.seconds * sampleRate);
  $("listen").classList.add("busy");
  await keepAwake(true);
  const started = Date.now();
  const ticker = setInterval(() => {
    const left = Math.max(0, state.seconds - (Date.now() - started) / 1000);
    $("listen").textContent = `${Math.ceil(left)}s`;
    setStatus(`listening forward — ${Math.ceil(left)}s to go`);
  }, 250);
  let samples;
  try {
    const collected = await new Promise((resolve) => {
      const future = { chunks: [], collected: 0, want, done: () => resolve(future.chunks) };
      state.audio.future = future;
    });
    samples = new Float32Array(Math.min(want, collected.reduce((n, c) => n + c.length, 0)));
    let offset = 0;
    for (const chunk of collected) {
      const take = Math.min(chunk.length, samples.length - offset);
      samples.set(chunk.subarray(0, take), offset);
      offset += take;
      if (offset >= samples.length) break;
    }
  } finally {
    clearInterval(ticker);
    closeMic();
    await keepAwake(false);
    $("listen").classList.remove("busy");
    $("listen").textContent = "listen";
  }
  await sendCapture(samples, sampleRate, {});
}

/* ── location + upload ────────────────────────────────────────────────── */

function currentPosition() {
  return new Promise((resolve) => {
    if (!$("use-location").checked || !navigator.geolocation) { resolve(null); return; }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve(position),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 },
    );
  });
}

async function sendCapture(samples, sampleRate, { heardSeconds = null, armedAt = null } = {}) {
  if (!samples.length) { setStatus("captured nothing — try again", true); return; }
  state.busy = true;
  $("listen").classList.add("busy");
  setStatus("asking where you are…");
  const position = await currentPosition();
  setStatus(`sending ${(samples.length / sampleRate).toFixed(1)}s to the server ear…`);
  const form = new FormData();
  form.append("file", encodeWav(samples, sampleRate), "remote-capture.wav");
  form.append("direction", state.direction);
  form.append("seconds", String(state.seconds));
  form.append("route_preset_name", $("preset").value);
  form.append("device", `${navigator.platform || "phone"} browser microphone (remote ear)`);
  if (armedAt) form.append("armed_at", armedAt);
  if ($("notes").value.trim()) form.append("notes", $("notes").value.trim());
  if ($("tags").value.trim()) form.append("tags", $("tags").value.trim());
  if (position) {
    form.append("lat", String(position.coords.latitude));
    form.append("lon", String(position.coords.longitude));
    if (position.coords.accuracy) form.append("accuracy_m", String(position.coords.accuracy));
    if (position.coords.altitude != null) form.append("altitude_m", String(position.coords.altitude));
  }
  if ($("place").value.trim()) form.append("location_label", $("place").value.trim());
  try {
    const response = await fetch("remote/listen", { method: "POST", body: form });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const result = await response.json();
    renderResult(result, { heardSeconds, located: Boolean(position) });
    setStatus(state.armed ? `filed — still armed, holding the last ${state.seconds}s` : "filed");
  } catch (error) {
    setStatus(`the server ear did not answer: ${error.message}`, true);
  } finally {
    state.busy = false;
    $("listen").classList.remove("busy");
    if (!state.armed) $("listen").textContent = "listen";
    else $("listen").textContent = "capture";
  }
}

/* ── rendering ────────────────────────────────────────────────────────── */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setStatus(text, bad = false) {
  const node = $("status");
  node.textContent = text;
  node.classList.toggle("bad", bad);
}

function renderResult(result, { heardSeconds, located }) {
  const event = result.listening_event || {};
  const aggregate = event.aggregate || {};
  const remote = result.remote || {};
  const body = $("result-body");
  body.replaceChildren();
  body.append(el("div", "title", aggregate.title || "listening event"));
  if (aggregate.short_summary) body.append(el("div", "summary", aggregate.short_summary));
  const meta = el("div", "meta");
  meta.append(el("span", "badge warn", `${remote.direction || state.direction} · ${state.seconds}s`));
  if (heardSeconds) meta.append(el("span", "badge", `${heardSeconds}s heard`));
  meta.append(el("span", "badge", located ? "located" : "no location"));
  for (const tag of (event.tags || []).slice(0, 5)) meta.append(el("span", "badge", tag));
  if (remote.akousma_id) meta.append(el("span", "badge", "akousma filed"));
  if (remote.akousma_error) meta.append(el("span", "badge", "store miss"));
  if (result.trace?.id) meta.append(el("span", "badge", "remembered"));
  if (event.covenant?.id) {
    meta.append(el("span", "badge warn", `under ${event.covenant.name || event.covenant.id}`));
    const withheldCount = (event.covenant.withheld || []).length;
    if (withheldCount) meta.append(el("span", "badge", `${withheldCount} withheld`));
  }
  if (remote.akousma_withheld) meta.append(el("span", "badge", "not retained — covenant"));
  body.append(meta);
  const facts = (aggregate.signal_facts || []).slice(0, 4);
  if (facts.length) {
    const list = el("ul", "facts");
    for (const fact of facts) list.append(el("li", "", fact));
    body.append(list);
  }
  if (remote.akousma_id) {
    const link = el("div", "note");
    link.style.marginTop = "8px";
    const anchor = el("a", "", "open the library");
    anchor.href = "library/";
    link.append(document.createTextNode(`${remote.akousma_id} · `), anchor);
    body.append(link);
  }
  $("result").hidden = false;

  state.history.unshift({
    at: new Date().toLocaleTimeString(),
    title: aggregate.title || "listening event",
    direction: remote.direction || state.direction,
  });
  const history = $("history-body");
  history.replaceChildren();
  for (const item of state.history.slice(0, 12)) {
    const row = el("div", "hist-item");
    row.append(el("div", "", item.title));
    row.append(el("div", "when", `${item.at} · ${item.direction}`));
    history.append(row);
  }
  $("history").hidden = false;
}

function modeNote() {
  $("mode-note").textContent = state.direction === "past"
    ? `past: arm the ear and it holds only the last ${state.seconds}s on the phone; capture takes what was heard before you pressed.`
    : `future: pressing listen records the next ${state.seconds}s, then sends.`;
}

/* ── wiring ───────────────────────────────────────────────────────────── */

$("direction").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-dir]");
  if (!button || state.busy) return;
  const next = button.dataset.dir;
  if (next === state.direction) return;
  if (state.armed) await disarmPast();
  state.direction = next;
  for (const item of $("direction").querySelectorAll("button")) item.classList.toggle("active", item === button);
  modeNote();
});

$("seconds").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-s]");
  if (!button || state.busy) return;
  state.seconds = Number(button.dataset.s);
  for (const item of $("seconds").querySelectorAll("button")) item.classList.toggle("active", item === button);
  if (state.armed && state.audio) {
    // resize the ring: keep what fits, forget the rest — never grow unbounded
    const old = state.audio.ring;
    const next = new Ring(state.seconds, old.sampleRate);
    next.push(old.snapshot(state.seconds));
    state.audio.ring = next;
    setStatus(`armed — now holding the last ${state.seconds}s`);
  }
  modeNote();
});

$("listen").addEventListener("click", async () => {
  if (state.busy) return;
  try {
    if (state.direction === "future") {
      await captureFuture();
      return;
    }
    if (!state.armed) {
      await armPast();
      return;
    }
    await capturePast();
  } catch (error) {
    setStatus(error.message, true);
    closeMic();
    state.armed = false;
    $("listen").classList.remove("armed", "busy");
    $("listen").textContent = "listen";
  }
});

$("listen").addEventListener("contextmenu", (event) => {
  // long-press / right-click on the armed ear disarms it
  if (state.armed) { event.preventDefault(); disarmPast(); }
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.armed && state.audio) {
    state.audio.context.resume().catch(() => {});
  }
});

$("use-location").checked = localStorage.getItem("oida.remote.location") !== "0";
$("use-location").addEventListener("change", () => {
  localStorage.setItem("oida.remote.location", $("use-location").checked ? "1" : "0");
});

modeNote();
