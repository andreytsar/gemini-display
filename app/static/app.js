(() => {
  const $ = (id) => document.getElementById(id);

  // State
  let appSettings = null;
  let pendingCity = null;
  let ws = null;
  let audioCtx = null;
  let micStream = null;
  let captureNode = null;
  let playerNode = null;
  let isSpeaking = false;
  let isMicMuted = false;
  let isVideoOn = false;
  let inCall = false;
  let lastRole = null;
  let lastBubbleEl = null;

  // --- Weather SVG Icons ---
  function weatherSvg(icon) {
    const icons = {
      clear: `<svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="14" fill="#fbbf24"/><g stroke="#fbbf24" stroke-width="4" stroke-linecap="round"><line x1="32" y1="6" x2="32" y2="12"/><line x1="32" y1="52" x2="32" y2="58"/><line x1="6" y1="32" x2="12" y2="32"/><line x1="52" y1="32" x2="58" y2="32"/><line x1="13.6" y1="13.6" x2="17.9" y2="17.9"/><line x1="46.1" y1="46.1" x2="50.4" y2="50.4"/><line x1="13.6" y1="50.4" x2="17.9" y2="46.1"/><line x1="46.1" y1="17.9" x2="50.4" y2="13.6"/></g></svg>`,
      clear_night: `<svg viewBox="0 0 64 64"><path d="M42 38A18 18 0 0 1 26 14a18 18 0 1 0 24 24 17.8 17.8 0 0 1-8 0z" fill="#fde68a"/></svg>`,
      mostly_clear: `<svg viewBox="0 0 64 64"><circle cx="24" cy="24" r="11" fill="#fbbf24"/><path d="M24 46h24a10 10 0 0 0 1-19.9A13 13 0 0 0 24.5 23 11 11 0 0 0 24 46z" fill="#e2e8f0"/></svg>`,
      mostly_clear_night: `<svg viewBox="0 0 64 64"><path d="M32 24a12 12 0 0 1-10-14 12 12 0 1 0 14 14z" fill="#fde68a"/><path d="M22 48h25a9 9 0 0 0 1-18 12 12 0 0 0-22-3 10 10 0 0 0-4 21z" fill="#cbd5e1"/></svg>`,
      partly_cloudy: `<svg viewBox="0 0 64 64"><circle cx="24" cy="24" r="11" fill="#fbbf24"/><path d="M20 48h28a10 10 0 0 0 1-20 14 14 0 0 0-27-3 11 11 0 0 0-2 23z" fill="#cbd5e1"/></svg>`,
      partly_cloudy_night: `<svg viewBox="0 0 64 64"><path d="M32 24a12 12 0 0 1-10-14 12 12 0 1 0 14 14z" fill="#fde68a"/><path d="M20 48h28a10 10 0 0 0 1-20 14 14 0 0 0-27-3 11 11 0 0 0-2 23z" fill="#94a3b8"/></svg>`,
      cloudy: `<svg viewBox="0 0 64 64"><path d="M18 48h30a11 11 0 0 0 1-22 15 15 0 0 0-29-3 12 12 0 0 0-2 25z" fill="#94a3b8"/></svg>`,
      fog: `<svg viewBox="0 0 64 64"><path d="M18 36h30a10 10 0 0 0 1-20 13 13 0 0 0-25-2 10 10 0 0 0-6 22z" fill="#94a3b8"/><g stroke="#cbd5e1" stroke-width="3.5" stroke-linecap="round"><line x1="14" y1="44" x2="50" y2="44"/><line x1="20" y1="52" x2="44" y2="52"/></g></svg>`,
      drizzle: `<svg viewBox="0 0 64 64"><path d="M18 38h29a10 10 0 0 0 1-20 13 13 0 0 0-25-2 10 10 0 0 0-5 22z" fill="#94a3b8"/><g stroke="#38bdf8" stroke-width="3.5" stroke-linecap="round"><line x1="23" y1="45" x2="20" y2="52"/><line x1="34" y1="45" x2="31" y2="52"/><line x1="45" y1="45" x2="42" y2="52"/></g></svg>`,
      rain: `<svg viewBox="0 0 64 64"><path d="M18 37h29a10 10 0 0 0 1-20 13 13 0 0 0-25-2 10 10 0 0 0-5 22z" fill="#64748b"/><g stroke="#38bdf8" stroke-width="4" stroke-linecap="round"><line x1="22" y1="43" x2="18" y2="55"/><line x1="33" y1="43" x2="29" y2="55"/><line x1="44" y1="43" x2="40" y2="55"/></g></svg>`,
      snow: `<svg viewBox="0 0 64 64"><path d="M18 37h29a10 10 0 0 0 1-20 13 13 0 0 0-25-2 10 10 0 0 0-5 22z" fill="#94a3b8"/><g fill="#e0f2fe"><circle cx="22" cy="48" r="3"/><circle cx="33" cy="52" r="3"/><circle cx="44" cy="48" r="3"/></g></svg>`,
      storm: `<svg viewBox="0 0 64 64"><path d="M18 36h29a10 10 0 0 0 1-20 13 13 0 0 0-25-2 10 10 0 0 0-5 22z" fill="#475569"/><polygon points="34,38 24,51 32,51 29,60 41,46 33,46" fill="#fbbf24"/></svg>`,
    };
    return icons[icon] || icons.cloudy;
  }

  function showToast(msg, ms = 3200) {
    const el = $("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.add("hidden"), ms);
  }

  // --- Clock ---
  function updateClock() {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, "0");
    const mm = String(now.getMinutes()).padStart(2, "0");
    const ss = String(now.getSeconds()).padStart(2, "0");
    $("clock-hm").textContent = `${hh}:${mm}`;
    $("clock-sec").textContent = ss;
    $("weekday-text").textContent = now.toLocaleDateString("en-GB", { weekday: "long" });
    $("date-text").textContent = now.toLocaleDateString("en-GB", {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  }
  updateClock();
  setInterval(updateClock, 1000);

  // --- Dynamic SVG Moon Phase ---
  function moonPhaseSvg(frac) {
    // frac: 0 = New Moon, 0.25 = First Quarter, 0.5 = Full Moon, 0.75 = Last Quarter
    const p = ((frac % 1) + 1) % 1;
    const r = 12;
    const cx = 14;
    const cy = 14;
    const base = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="#1e293b" stroke="#64748b" stroke-width="1.2"/>`;
    if (p < 0.02 || p > 0.98) {
      return `<svg viewBox="0 0 28 28">${base}</svg>`;
    }
    if (p > 0.48 && p < 0.52) {
      return `<svg viewBox="0 0 28 28">${base}<circle cx="${cx}" cy="${cy}" r="${r}" fill="#fde68a"/></svg>`;
    }
    const waxing = p < 0.5;
    const phaseAngle = p * 2 * Math.PI;
    const rx = Math.abs(Math.cos(phaseAngle)) * r;
    // Outer limb sweep: right side for waxing, left side for waning
    const outerSweep = waxing ? 1 : 0;
    // Inner terminator sweep depends on crescent vs gibbous
    const gibbous = p > 0.25 && p < 0.75;
    const innerSweep = waxing ? (gibbous ? 1 : 0) : (gibbous ? 0 : 1);
    const d = `M ${cx} ${cy - r} A ${r} ${r} 0 0 ${outerSweep} ${cx} ${cy + r} A ${rx.toFixed(2)} ${r} 0 0 ${innerSweep} ${cx} ${cy - r} Z`;
    return `<svg viewBox="0 0 28 28">${base}<path d="${d}" fill="#fde68a"/></svg>`;
  }

  // --- Weather ---
  let weatherRetryTimer = null;
  async function loadWeather(force = false) {
    clearTimeout(weatherRetryTimer);
    try {
      const resp = await fetch(`/api/weather${force ? "?refresh=true" : ""}`);
      if (!resp.ok) throw new Error("Weather request failed");
      const w = await resp.json();

      $("topbar-city").textContent = w.city;
      $("topbar-updated").textContent = `Updated ${w.updated_at}`;

      const cur = w.current;
      $("weather-icon").innerHTML = weatherSvg(cur.icon);
      $("temp-now").textContent = `${cur.temp}°`;
      $("temp-hl").textContent = `H:${cur.temp_max ?? "--"}°  L:${cur.temp_min ?? "--"}°`;
      $("weather-desc").textContent = cur.description;
      $("metric-feels").textContent = `${cur.feels_like}°`;
      $("metric-humidity").textContent = `${cur.humidity}%`;
      $("metric-wind").textContent = `${cur.wind_kmh} km/h`;

      const strip = $("forecast-strip");
      strip.innerHTML = "";
      (w.forecast || []).forEach((day) => {
        const div = document.createElement("div");
        div.className = "forecast-day";
        div.innerHTML = `
          <span class="f-day">${day.day}</span>
          <div class="f-icon">${weatherSvg(day.icon)}</div>
          <div class="f-temps">${day.temp_max ?? "--"}°<span class="f-min">${day.temp_min ?? "--"}°</span></div>
          <span class="f-pop">${day.precip_prob > 0 ? `💧 ${day.precip_prob}%` : "&nbsp;"}</span>
        `;
        strip.appendChild(div);
      });

      if (w.astronomy) {
        const a = w.astronomy;
        $("sun-sunrise").textContent = a.sunrise || "--:--";
        $("sun-sunset").textContent = a.sunset || "--:--";
        $("sun-daylight").textContent = a.daylight ? `Daylight ${a.daylight}` : "";
        $("moon-svg").innerHTML = moonPhaseSvg(a.moon_phase_frac ?? 0);
        $("moon-phase-name").textContent = a.moon_phase_name || "Moon";
        $("moon-illum").textContent = `${a.moon_illumination ?? "--"}% illuminated`;
        $("moon-rise").textContent = a.moonrise || "--:--";
        $("moon-set").textContent = a.moonset || "--:--";
      }
    } catch (err) {
      $("weather-desc").textContent = "Retrying weather...";
      weatherRetryTimer = setTimeout(() => loadWeather(false), 15000);
    }
  }

  // --- Volume ---
  async function refreshVolume() {
    try {
      const r = await fetch("/api/volume");
      const d = await r.json();
      $("vol-label").textContent = d.muted ? "🔇 Mute" : `🔊 ${d.volume}%`;
    } catch {}
  }

  async function adjustVolume(delta) {
    try {
      const r = await fetch("/api/volume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ delta }),
      });
      const d = await r.json();
      $("vol-label").textContent = d.muted ? "🔇 Mute" : `🔊 ${d.volume}%`;
    } catch {}
  }

  // --- Settings ---
  async function loadSettings() {
    try {
      const r = await fetch("/api/settings");
      appSettings = await r.json();
      const dot = $("api-status-dot");
      if (appSettings.api_key_set) {
        dot.className = "status-dot ready";
        $("gemini-ready-sub").textContent = `${appSettings.gemini_voice} · EN / עב / RU`;
      } else {
        dot.className = "status-dot warn";
        $("gemini-ready-sub").textContent = "Tap Settings (⚙) to add your Gemini API key";
      }
      $("call-model-tag").textContent = appSettings.gemini_model;
    } catch {}
  }

  async function openSettings() {
    await loadSettings();
    if (!appSettings) return;
    pendingCity = null;
    $("setting-api-key").value = "";
    $("setting-city-input").value = appSettings.weather_city || "";
    $("city-results").innerHTML = "";
    $("settings-save-msg").textContent = "";

    const badge = $("api-key-badge");
    if (appSettings.api_key_set) {
      badge.textContent = "Configured ✓";
      badge.className = "badge-state ok";
    } else {
      badge.textContent = "Not set";
      badge.className = "badge-state missing";
    }

    const mp = $("model-pills");
    mp.innerHTML = "";
    appSettings.available_models.forEach((m) => {
      const b = document.createElement("button");
      b.className = "pill-opt" + (m === appSettings.gemini_model ? " selected" : "");
      b.textContent = m;
      b.dataset.model = m;
      b.onclick = () => {
        mp.querySelectorAll(".pill-opt").forEach((x) => x.classList.remove("selected"));
        b.classList.add("selected");
      };
      mp.appendChild(b);
    });

    const vp = $("voice-pills");
    vp.innerHTML = "";
    appSettings.available_voices.forEach((v) => {
      const b = document.createElement("button");
      b.className = "pill-opt" + (v === appSettings.gemini_voice ? " selected" : "");
      b.textContent = v;
      b.dataset.voice = v;
      b.onclick = () => {
        vp.querySelectorAll(".pill-opt").forEach((x) => x.classList.remove("selected"));
        b.classList.add("selected");
      };
      vp.appendChild(b);
    });

    document.querySelectorAll("#rotate-pills .pill-opt[data-rot]").forEach((b) => {
      b.classList.toggle("selected", Number(b.dataset.rot) === Number(appSettings.camera_rotate));
      b.onclick = () => {
        document.querySelectorAll("#rotate-pills .pill-opt[data-rot]").forEach((x) => x.classList.remove("selected"));
        b.classList.add("selected");
      };
    });

    const hflipBtn = $("hflip-pill");
    if (hflipBtn) {
      hflipBtn.classList.toggle("selected", Boolean(appSettings.camera_hflip));
      hflipBtn.onclick = () => hflipBtn.classList.toggle("selected");
    }

    $("settings-modal").classList.remove("hidden");
  }

  function closeSettings() {
    const img = $("settings-cam-preview");
    img.src = "";
    img.classList.add("hidden");
    $("settings-modal").classList.add("hidden");
  }

  async function searchCity() {
    const q = $("setting-city-input").value.trim();
    if (q.length < 2) return;
    const resEl = $("city-results");
    resEl.innerHTML = `<span style="color:#94a3b8;font-size:14px">Searching...</span>`;
    try {
      const r = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
      const d = await r.json();
      resEl.innerHTML = "";
      if (!d.results || d.results.length === 0) {
        resEl.innerHTML = `<span style="color:#94a3b8;font-size:14px">No cities found</span>`;
        return;
      }
      d.results.forEach((c) => {
        const btn = document.createElement("button");
        btn.className = "pill-opt";
        btn.textContent = c.label;
        btn.onclick = () => {
          pendingCity = { name: c.name, lat: c.lat, lon: c.lon };
          $("setting-city-input").value = c.name;
          resEl.querySelectorAll(".pill-opt").forEach((x) => x.classList.remove("selected"));
          btn.classList.add("selected");
        };
        resEl.appendChild(btn);
      });
    } catch {
      resEl.innerHTML = `<span style="color:#fb7185;font-size:14px">Search error</span>`;
    }
  }

  async function saveSettings() {
    const selModel = $("model-pills").querySelector(".selected")?.dataset.model;
    const selVoice = $("voice-pills").querySelector(".selected")?.dataset.voice;
    const selRot = $("rotate-pills").querySelector(".selected[data-rot]")?.dataset.rot;
    const selHflip = $("hflip-pill")?.classList.contains("selected");
    const apiKey = $("setting-api-key").value.trim();

    const payload = {
      gemini_model: selModel,
      gemini_voice: selVoice,
      camera_rotate: selRot !== undefined ? Number(selRot) : undefined,
      camera_hflip: selHflip,
    };
    if (apiKey) payload.gemini_api_key = apiKey;
    const typedCity = $("setting-city-input").value.trim();
    if (pendingCity && pendingCity.name.toLowerCase() === typedCity.toLowerCase()) {
      payload.weather_city = pendingCity.name;
      payload.weather_lat = pendingCity.lat;
      payload.weather_lon = pendingCity.lon;
    } else if (typedCity && (!appSettings || typedCity.toLowerCase() !== appSettings.weather_city.toLowerCase())) {
      payload.weather_city = typedCity;
    }

    try {
      const r = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        $("settings-save-msg").textContent = err.detail || "Failed to save";
        return;
      }
      appSettings = await r.json();
      await loadSettings();
      await loadWeather(true);
      $("settings-save-msg").textContent = "Saved ✓";
      setTimeout(closeSettings, 600);
    } catch {
      $("settings-save-msg").textContent = "Failed to save";
    }
  }

  // --- Unified Audio Engine (Native AudioContext + Jitter-Buffered Playback Worklet) ---
  function flushPlayback() {
    if (playerNode) {
      playerNode.port.postMessage({ type: "clear" });
    }
    setSpeakingState(false);
  }

  function playPcmAudio(arrayBuffer) {
    if (!playerNode || arrayBuffer.byteLength < 2) return;
    playerNode.port.postMessage({ type: "audio", pcm: arrayBuffer }, [arrayBuffer]);
  }

  function setSpeakingState(speaking) {
    isSpeaking = speaking;
    const pill = $("call-status-pill");
    const orb = $("voice-orb");
    if (!inCall) return;
    if (speaking) {
      pill.classList.add("speaking");
      orb.classList.add("speaking");
      $("call-status-text").textContent = "Gemini Speaking";
    } else {
      pill.classList.remove("speaking");
      orb.classList.remove("speaking");
      if (ws && ws.readyState === WebSocket.OPEN) {
        $("call-status-text").textContent = isMicMuted ? "Mic Muted" : "Listening...";
      }
    }
  }

  // --- Transcript UI ---
  function clearTranscript() {
    const list = $("transcript-list");
    list.innerHTML = `<div class="transcript-empty">Listening for your voice...</div>`;
    lastRole = null;
    lastBubbleEl = null;
  }

  function appendTranscript(role, text, isFinal) {
    if (!text) return;
    const list = $("transcript-list");
    const empty = list.querySelector(".transcript-empty");
    if (empty) empty.remove();

    if (lastRole === role && lastBubbleEl) {
      lastBubbleEl.textContent += text;
    } else {
      const div = document.createElement("div");
      div.className = `bubble ${role}`;
      div.dir = "auto";
      div.textContent = text;
      list.appendChild(div);
      lastRole = role;
      lastBubbleEl = div;
    }
    if (isFinal) {
      lastRole = null;
      lastBubbleEl = null;
    }
    list.scrollTop = list.scrollHeight;
  }

  // --- Camera Stage ---
  function setCameraStage(enabled) {
    isVideoOn = enabled;
    const camStage = $("camera-stage");
    const orbStage = $("orb-stage");
    const img = $("camera-feed");
    const camBtn = $("toggle-cam-btn");

    if (enabled) {
      camStage.classList.remove("hidden");
      orbStage.classList.add("hidden");
      img.src = `/api/camera/stream?t=${Date.now()}`;
      camBtn.classList.add("active");
      $("toggle-cam-label").textContent = "Cam On";
    } else {
      img.src = "";
      camStage.classList.add("hidden");
      orbStage.classList.remove("hidden");
      camBtn.classList.remove("active");
      $("toggle-cam-label").textContent = "Cam Off";
    }
  }

  // --- Start Unified Audio (Mic Capture + Smooth Speaker Playback) ---
  async function startAudio() {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });

    audioCtx = new (window.AudioContext || window.webkitAudioContext)({
      latencyHint: "playback",
    });
    if (audioCtx.state === "suspended") {
      await audioCtx.resume();
    }

    await audioCtx.audioWorklet.addModule("/static/pcm-worklet.js?v=9");
    await audioCtx.audioWorklet.addModule("/static/pcm-player-worklet.js?v=9");

    // Capture worklet (native -> 16kHz anti-aliased PCM)
    const source = audioCtx.createMediaStreamSource(micStream);
    captureNode = new AudioWorkletNode(audioCtx, "pcm-capture-processor");
    captureNode.port.onmessage = (event) => {
      const { pcm, level } = event.data;
      $("mic-meter-bar").style.transform = `scaleX(${level.toFixed(3)})`;
      if (ws && ws.readyState === WebSocket.OPEN && !isMicMuted) {
        ws.send(pcm);
      }
    };
    source.connect(captureNode);

    // Playback worklet (24kHz PCM -> native rate smooth ring buffer)
    playerNode = new AudioWorkletNode(audioCtx, "pcm-player-processor", {
      outputChannelCount: [2],
    });
    playerNode.port.onmessage = (event) => {
      if (event.data && event.data.type === "state") {
        setSpeakingState(!!event.data.playing);
      }
    };
    playerNode.connect(audioCtx.destination);
  }

  function stopAudio() {
    $("mic-meter-bar").style.transform = "scaleX(0)";
    if (captureNode) {
      try { captureNode.disconnect(); } catch {}
      captureNode = null;
    }
    if (playerNode) {
      try {
        playerNode.port.postMessage({ type: "clear" });
        playerNode.disconnect();
      } catch {}
      playerNode = null;
    }
    if (audioCtx) {
      try { audioCtx.close(); } catch {}
      audioCtx = null;
    }
    if (micStream) {
      micStream.getTracks().forEach((t) => t.stop());
      micStream = null;
    }
    isSpeaking = false;
  }

  // --- Live Session Lifecycle ---
  async function startCall(withVideo, initialPrompt = null) {
    if (inCall) return;
    if (appSettings && !appSettings.api_key_set) {
      showToast("Add your GEMINI_API_KEY in Settings first.");
      openSettings();
      return;
    }

    inCall = true;
    isMicMuted = false;
    $("toggle-mic-btn").classList.remove("muted");
    $("toggle-mic-label").textContent = "Mute";

    $("dashboard-view").classList.add("hidden");
    $("call-view").classList.remove("hidden");
    $("call-status-pill").classList.remove("error", "speaking");
    $("call-status-text").textContent = "Starting audio...";
    clearTranscript();
    setCameraStage(withVideo);

    try {
      await startAudio();
    } catch (err) {
      showToast(`Audio error: ${err.message}`);
      endCall();
      return;
    }

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws/live`);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      $("call-status-text").textContent = "Connecting to Gemini...";
      ws.send(JSON.stringify({ type: "start", video: withVideo, prompt: initialPrompt }));
    };

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        playPcmAudio(event.data);
        return;
      }
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "audio_format" && msg.rate && playerNode) {
          playerNode.port.postMessage({ type: "config", sampleRate: msg.rate });
        } else if (msg.type === "status") {
          $("call-status-text").textContent = msg.message || msg.state;
          if (msg.state === "error") {
            $("call-status-pill").classList.add("error");
            showToast(msg.message || "Gemini connection error", 5000);
          }
        } else if (msg.type === "transcript") {
          appendTranscript(msg.role, msg.text, !!msg.final);
        } else if (msg.type === "interrupted") {
          flushPlayback();
          lastRole = null;
          lastBubbleEl = null;
        } else if (msg.type === "turn_complete") {
          if (playerNode) {
            playerNode.port.postMessage({ type: "flush_start" });
          }
          lastRole = null;
          lastBubbleEl = null;
        }
      } catch {}
    };

    ws.onerror = () => {
      $("call-status-pill").classList.add("error");
      $("call-status-text").textContent = "Connection error";
    };

    ws.onclose = () => {
      if (inCall) {
        setTimeout(() => {
          if (inCall) endCall();
        }, 1200);
      }
    };
  }

  function endCall() {
    inCall = false;
    stopAudio();
    setCameraStage(false);

    if (ws) {
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "stop" }));
        }
        ws.close();
      } catch {}
      ws = null;
    }

    $("call-view").classList.add("hidden");
    $("dashboard-view").classList.remove("hidden");
  }

  // --- Wire UI Events ---
  $("start-voice-btn").onclick = () => startCall(false);
  $("start-video-btn").onclick = () => startCall(true);

  document.querySelectorAll(".chip-btn").forEach((btn) => {
    btn.onclick = () => startCall(btn.dataset.mode === "video", btn.dataset.prompt);
  });

  $("end-call-btn").onclick = endCall;

  $("toggle-mic-btn").onclick = () => {
    isMicMuted = !isMicMuted;
    if (captureNode) {
      captureNode.port.postMessage({ muted: isMicMuted });
    }
    $("toggle-mic-btn").classList.toggle("muted", isMicMuted);
    $("toggle-mic-label").textContent = isMicMuted ? "Unmute" : "Mute";
    if (!isSpeaking) {
      $("call-status-text").textContent = isMicMuted ? "Mic Muted" : "Listening...";
    }
  };

  $("toggle-cam-btn").onclick = () => {
    const next = !isVideoOn;
    setCameraStage(next);
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "video_toggle", enabled: next }));
    }
  };

  $("vol-down-btn").onclick = () => adjustVolume(-10);
  $("vol-up-btn").onclick = () => adjustVolume(10);
  $("call-vol-down").onclick = () => adjustVolume(-10);
  $("call-vol-up").onclick = () => adjustVolume(10);

  $("refresh-weather-btn").onclick = () => {
    loadWeather(true);
    showToast("Weather refreshed");
  };

  $("settings-btn").onclick = openSettings;
  $("close-settings-btn").onclick = closeSettings;
  $("city-search-btn").onclick = searchCity;
  $("save-settings-btn").onclick = saveSettings;
  $("setting-city-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") searchCity();
  });

  $("test-cam-btn").onclick = () => {
    const img = $("settings-cam-preview");
    if (img.classList.contains("hidden")) {
      img.src = `/api/camera/stream?t=${Date.now()}`;
      img.classList.remove("hidden");
      requestAnimationFrame(() => img.scrollIntoView({ behavior: "smooth", block: "nearest" }));
    } else {
      img.src = "";
      img.classList.add("hidden");
    }
  };

  // Boot
  loadSettings();
  loadWeather(false);
  refreshVolume();
  setInterval(() => loadWeather(false), 10 * 60 * 1000);
  setInterval(refreshVolume, 30 * 1000);
})();
