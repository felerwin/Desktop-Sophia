const $ = (id) => document.getElementById(id);
const apiBase = window.location.port === "3000" ? "http://127.0.0.1:8766" : "";
let locallyCleared = 0;
let voicesLoaded = false;
let microphonesLoaded = false;
let soundSignature = "";
let videoSignature = "";
let youtubePlayer = null;
let youtubePlayerReady = false;
let youtubePlayerVisible = false;
let lastYoutubeCommandSeq = 0;
let deferredYoutubeCommandSeq = 0;
let latestYoutubeState = null;
let memorySignature = "";
let personalitySignature = "";
let eventSignature = "";
let gameConfigLoaded = false;

function seconds(value) {
  return typeof value === "number" ? `${value.toFixed(2)}s` : "—";
}

function clock(total) {
  const hours = Math.floor(total / 3600).toString().padStart(2, "0");
  const minutes = Math.floor((total % 3600) / 60).toString().padStart(2, "0");
  const secs = Math.floor(total % 60).toString().padStart(2, "0");
  return `${hours}:${minutes}:${secs}`;
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value || "";
  return node.innerHTML;
}

function renderMessages(messages) {
  const visible = messages.slice(locallyCleared);
  if (!visible.length) {
    $("messages").innerHTML = '<div class="emptyState">Conversation will appear here when Sophia hears you.</div>';
    return;
  }
  $("messages").innerHTML = visible.slice(-12).reverse().map((message) => {
    const sophia = message.speaker === "Sophia";
    return `<div class="message ${sophia ? "sophia" : "tony"}"><span>${sophia ? "S" : "T"}</span><div><small>${escapeHtml(message.speaker)} · ${escapeHtml(message.time)}</small><p>${escapeHtml(message.text)}</p></div></div>`;
  }).join("");
}

function renderSounds(sounds) {
  const signature = sounds.map((sound) => `${sound.id}:${sound.bytes}:${sound.status}:${sound.description}:${sound.use_when}`).join("|");
  if (signature === soundSignature) return;
  soundSignature = signature;
  if (!sounds.length) {
    $("soundGrid").innerHTML = '<div class="soundEmpty"><strong>No clips yet</strong><span>Add MP3 or WAV meme clips to give Sophia some ammunition.</span></div>';
    return;
  }
  $("soundGrid").innerHTML = sounds.map((sound, index) =>
    `<div class="soundTile"><button class="soundPlay" data-sound="${encodeURIComponent(sound.id)}" data-name="${escapeHtml(sound.name)}"><span>${String(index + 1).padStart(2, "0")}</span><strong>${escapeHtml(sound.name)}</strong><small>${escapeHtml(sound.description || "Waiting for analysis")}</small><em>${(sound.bytes / 1024 / 1024).toFixed(1)} MB · ${escapeHtml(sound.status)}${sound.affinity ? ` · affinity ${Number(sound.affinity).toFixed(1)}` : ""}</em></button><button class="soundRemove" data-remove="${encodeURIComponent(sound.id)}" aria-label="Remove ${escapeHtml(sound.name)}">×</button></div>`
  ).join("");
}

function formatCue(value) {
  const total = Math.max(0, Math.floor(Number(value) || 0));
  const minutes = Math.floor(total / 60);
  const secs = String(total % 60).padStart(2, "0");
  return `${minutes}:${secs}`;
}

function parseCue(value) {
  const parts = String(value || "0").trim().split(":").map(Number);
  if (!parts.length || parts.some((part) => !Number.isFinite(part) || part < 0)) return null;
  if (parts.length === 1) return parts[0];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  return null;
}

function renderVideoShelf(videos) {
  const signature = videos.map((video) => `${video.id}:${video.title}:${video.start_seconds}:${video.use_when}`).join("|");
  if (signature === videoSignature) return;
  videoSignature = signature;
  if (!videos.length) {
    $("savedVideos").innerHTML = '<div class="soundEmpty"><strong>The shelf is empty</strong><span>Paste a YouTube link to give Sophia her first video.</span></div>';
    return;
  }
  $("savedVideos").innerHTML = videos.map((video) =>
    `<div class="savedVideo"><div><strong>${escapeHtml(video.title)}</strong><small>${formatCue(video.start_seconds)} · ${escapeHtml(video.use_when || "Play when asked")}${video.affinity ? ` · affinity ${Number(video.affinity).toFixed(1)}` : ""}</small></div><button type="button" data-video-play="${encodeURIComponent(video.id)}">Play</button><button type="button" data-video-remove="${encodeURIComponent(video.id)}" aria-label="Remove ${escapeHtml(video.title)}">×</button></div>`
  ).join("");
}

async function reportYoutubeStatus(extra = {}) {
  if (!youtubePlayerReady || !youtubePlayer) return;
  let video = {};
  try { video = youtubePlayer.getVideoData() || {}; } catch (_) {}
  const payload = {
    status: extra.status || "ready",
    video_id: video.video_id || latestYoutubeState?.video_id || null,
    title: video.title || latestYoutubeState?.title || null,
    current_seconds: Math.round((youtubePlayer.getCurrentTime?.() || 0) * 10) / 10,
    autoplay_blocked: Boolean(extra.autoplay_blocked),
  };
  await fetch(`${apiBase}/api/youtube/status`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  }).catch(() => {});
}

function applyYoutubeCommand(youtube) {
  latestYoutubeState = youtube;
  const command = youtube?.command;
  if (!command || youtube.command_seq <= lastYoutubeCommandSeq || !youtubePlayerReady) return;
  if (command.action === "play" && !youtubePlayerVisible) {
    $("youtubeStatus").textContent = "Player must be visible before playback — scroll it into view";
    if (deferredYoutubeCommandSeq !== youtube.command_seq) {
      deferredYoutubeCommandSeq = youtube.command_seq;
      reportYoutubeStatus({ status: "waiting_visible" });
    }
    return;
  }
  try {
    if (command.action === "play") {
      youtubePlayer.loadVideoById({ videoId: command.video_id, startSeconds: Number(command.seconds || 0) });
    } else if (command.action === "pause") {
      youtubePlayer.pauseVideo();
    } else if (command.action === "resume") {
      youtubePlayer.playVideo();
    } else if (command.action === "stop") {
      youtubePlayer.stopVideo();
    } else if (command.action === "seek") {
      youtubePlayer.seekTo(Number(command.seconds || 0), true);
    }
    lastYoutubeCommandSeq = youtube.command_seq;
  } catch (_) {
    $("youtubeStatus").textContent = "The player couldn’t carry out that command";
  }
}

function renderYoutube(youtube) {
  if (!youtube) return;
  latestYoutubeState = youtube;
  renderVideoShelf(youtube.library || []);
  const label = youtube.autoplay_blocked
    ? "Click play once in the video to allow sound"
    : youtube.title
      ? `${youtube.status} · ${youtube.title} · ${formatCue(youtube.current_seconds)}`
      : youtubePlayerReady ? "Ready for a video" : "Player warming up…";
  $("youtubeStatus").textContent = label;
  applyYoutubeCommand(youtube);
}

function renderMemories(memory) {
  if (!memory) return;
  const items = memory.items || [];
  const sessions = memory.sessions || [];
  const signature = items.map((item) => `${item.id}:${item.updated_at}:${item.pinned}`).join("|")
    + "::" + sessions.map((session) => `${session.id}:${session.ended_at}:${session.summary}`).join("|");
  const stats = memory.stats || {};
  $("memoryStats").textContent = `${stats.memories || 0} memories · ${stats.sessions || 0} sessions`;
  if (signature !== memorySignature) {
    memorySignature = signature;
    $("memoryList").innerHTML = items.length ? items.map((item) =>
      `<div class="memoryItem"><span>${escapeHtml(item.category)}${item.pinned ? " · pinned" : ""}</span><div><strong>${escapeHtml(item.subject || "Untitled memory")}</strong><p>${escapeHtml(item.content)}</p><small>importance ${Number(item.importance).toFixed(1)} · confidence ${Number(item.confidence).toFixed(1)} · ${escapeHtml(item.source)}</small></div><button type="button" data-memory-archive="${encodeURIComponent(item.id)}" aria-label="Forget this memory">×</button></div>`
    ).join("") : '<div class="soundEmpty"><strong>No long-term memories yet</strong><span>Tell Sophia “remember that…” or add one here.</span></div>';
    $("sessionList").innerHTML = sessions.length ? sessions.map((session) =>
      `<div class="sessionItem"><time>${escapeHtml(session.started_at)}</time><p>${escapeHtml(session.summary || "Session in progress")}</p><small>${session.tony_turns} Tony · ${session.sophia_turns} Sophia · ${session.tool_actions} tools · $${Number(session.estimated_cost || 0).toFixed(4)}</small></div>`
    ).join("") : '<div class="soundEmpty"><span>No completed sessions yet.</span></div>';
  }
  const profile = memory.profile || {};
  const profileSignature = JSON.stringify(profile);
  if (profileSignature !== personalitySignature && !$("personalityFields").contains(document.activeElement)) {
    personalitySignature = profileSignature;
    $("personalityFields").innerHTML = Object.entries(profile).map(([key, value]) =>
      `<div class="personalityField"><label for="profile-${escapeHtml(key)}">${escapeHtml(key.replaceAll("_", " "))}</label><textarea id="profile-${escapeHtml(key)}" data-profile-value="${escapeHtml(key)}">${escapeHtml(value)}</textarea><button type="button" data-profile-save="${escapeHtml(key)}">Save</button></div>`
    ).join("");
  }
}

function renderGameEvents(game) {
  if (!game) return;
  const statusCopy = {
    watching: "Watching WoW combat log",
    waiting_for_log: "Waiting for /combatlog",
    searching: "Looking for WoW logs…",
    error: "Game log needs attention",
    disabled: "Disabled",
  };
  const telemetry = game.telemetry || {};
  const telemetryStatus = {
    live: "Live pixel bridge",
    searching: "Pixel bridge searching",
    signal_lost: "Pixel signal lost",
    error: "Pixel bridge error",
  }[telemetry.status];
  $("gameEventStatus").textContent = telemetryStatus || statusCopy[game.status] || game.status;
  const live = telemetry.state || {};
  const health = Number.isFinite(live.health) ? live.health : null;
  const power = Number.isFinite(live.power) ? live.power : null;
  $("wowHealth").textContent = health == null ? "—" : `${health}%`;
  $("wowPower").textContent = power == null ? "—" : `${power}%`;
  $("wowHealthBar").style.width = `${health == null ? 0 : health}%`;
  $("wowHealthBar").classList.toggle("critical", health != null && health <= 25);
  $("wowPowerBar").style.width = `${power == null ? 0 : power}%`;
  $("wowTarget").textContent = live.target_name || (live.has_target ? "Target acquired" : "No target");
  $("wowTargetMeta").textContent = telemetry.status === "live"
    ? `${live.combat ? "In combat" : "Out of combat"} · ${live.zone || "zone pending"}`
    : "Pixel bridge searching";
  const gear = telemetry.gear || [];
  $("wowGear").innerHTML = gear.some((item) => item.item_id) ? gear.filter((item) => item.item_id).map((item) =>
    `<div class="wowGearItem quality-${Math.min(4, Number(item.quality || 0))}"><span>${String(item.slot).padStart(2, "0")}</span><strong>${escapeHtml(item.name || `Item ${item.item_id}`)}</strong><small>iLvl ${item.item_level || "?"}</small></div>`
  ).join("") : '<div class="soundEmpty"><strong>Waiting for equipment</strong><span>The live bridge will transmit equipped items after login.</span></div>';
  if (!gameConfigLoaded) {
    $("wowLogPath").value = game.configured_path || "";
    $("wowPlayerName").value = game.player_name || "";
    gameConfigLoaded = true;
  }
  const events = game.recent || [];
  const signature = events.map((event) => `${event.time}:${event.event_type}:${event.title}`).join("|");
  if (signature === eventSignature) return;
  eventSignature = signature;
  $("eventList").innerHTML = events.length ? events.map((event) =>
    `<div class="eventItem"><span>${escapeHtml(event.event_type)}</span><strong>${escapeHtml(event.title)}</strong><time>${escapeHtml(event.time || "now")}</time></div>`
  ).join("") : '<div class="soundEmpty"><strong>No game events yet</strong><span>WoW combat-log events will appear here.</span></div>';
}

function render(state) {
  $("phaseLabel").textContent = state.phase_label;
  $("statusPill").innerHTML = `<i></i> ${escapeHtml(state.phase_label)}`;
  $("systemLine").textContent = `${state.microphone} · ${state.voice} · ${state.model}`;
  $("micState").textContent = state.phase === "listening" ? "Mic live" : state.phase;
  $("uptime").textContent = clock(state.uptime_seconds);
  $("firstAudio").textContent = seconds(state.first_audio);
  $("firstText").textContent = seconds(state.first_text);
  $("endpointWait").textContent = seconds(state.endpoint_wait);
  $("sttTime").textContent = state.stt_seconds ? `STT ${seconds(state.stt_seconds)}` : "STT awaiting speech";
  $("sessionCost").textContent = `$${Number(state.session_cost || 0).toFixed(4)}`;
  $("apiCalls").textContent = `${state.api_calls || 0} Luna calls`;
  $("hearingText").textContent = state.phase === "listening" ? "Listening for Tony…" : state.phase_label;
  $("diagnosticState").textContent = "Connected";
  $("diagnosticLog").textContent = state.logs.slice(0, 12).map((row) => `[${row.event}] ${row.text}`).join("\n") || "All systems quiet.";
  document.querySelectorAll("[data-control]").forEach((input) => {
    input.checked = Boolean(state.controls[input.dataset.control]);
  });
  if (!voicesLoaded) {
    $("voiceSelector").innerHTML = state.voice_options.map((voice) =>
      `<option value="${escapeHtml(voice.voice)}">${escapeHtml(voice.name)}</option>`
    ).join("");
    voicesLoaded = true;
  }
  if ($("voiceSelector") !== document.activeElement) {
    $("voiceSelector").value = state.selected_voice;
  }
  if (!microphonesLoaded && state.microphone_options.length) {
    $("microphoneSelector").innerHTML = state.microphone_options.map((device) =>
      `<option value="${device.index}">${escapeHtml(device.name)}</option>`
    ).join("");
    microphonesLoaded = true;
  }
  $("microphoneSelector").disabled = !state.microphone_options.length;
  if ($("microphoneSelector") !== document.activeElement && state.selected_microphone != null) {
    $("microphoneSelector").value = String(state.selected_microphone);
  }
  renderSounds(state.sounds || []);
  renderYoutube(state.youtube);
  renderMemories(state.memory);
  renderGameEvents(state.game_events);
  $("soundStatus").textContent = state.soundboard_now_playing ? `Playing ${state.soundboard_now_playing}` : "Ready";
  renderMessages(state.messages || []);
}

async function youtubeCommand(action, id = null, seconds = null) {
  const response = await fetch(`${apiBase}/api/youtube/command`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, id, seconds }),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    $("youtubeStatus").textContent = result.error || "YouTube command failed";
  }
  refresh();
}

async function stopSound() {
  await fetch(`${apiBase}/api/sound/stop`, { method: "POST" });
  refresh();
}

async function playSound(id, name) {
  $("soundStatus").textContent = `Playing ${name}`;
  const response = await fetch(`${apiBase}/api/sound/play`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: decodeURIComponent(id), volume: Number($("soundVolume").value) }),
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    $("soundStatus").textContent = result.error || "Couldn’t play clip";
  }
}

function fileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function refresh() {
  try {
    const response = await fetch(`${apiBase}/api/state`, { cache: "no-store" });
    if (!response.ok) throw new Error("Dashboard API unavailable");
    render(await response.json());
  } catch (error) {
    $("diagnosticState").textContent = "Waiting for Sophia";
  }
}

document.querySelectorAll("[data-control]").forEach((input) => {
  input.addEventListener("change", async () => {
    await fetch(`${apiBase}/api/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: input.dataset.control, value: input.checked }),
    });
    refresh();
  });
});

$("voiceSelector").addEventListener("change", async (event) => {
  const selector = event.currentTarget;
  selector.disabled = true;
  try {
    const response = await fetch(`${apiBase}/api/voice`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice: selector.value }),
    });
    if (!response.ok) throw new Error("Voice change failed");
  } finally {
    selector.disabled = false;
    refresh();
  }
});

$("microphoneSelector").addEventListener("change", async (event) => {
  const selector = event.currentTarget;
  selector.disabled = true;
  try {
    const response = await fetch(`${apiBase}/api/microphone`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device: Number(selector.value) }),
    });
    if (!response.ok) throw new Error("Microphone change failed");
  } finally {
    selector.disabled = false;
    refresh();
  }
});

$("soundGrid").addEventListener("click", async (event) => {
  const play = event.target.closest("[data-sound]");
  if (play) {
    playSound(play.dataset.sound, play.dataset.name);
    return;
  }
  const remove = event.target.closest("[data-remove]");
  if (remove && window.confirm("Remove this clip from the soundboard?")) {
    await fetch(`${apiBase}/api/sound/archive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: decodeURIComponent(remove.dataset.remove) }),
    });
    soundSignature = "";
    refresh();
  }
});

$("addSoundButton").addEventListener("click", () => $("soundUpload").click());
$("soundUpload").addEventListener("change", async (event) => {
  const files = [...event.target.files];
  for (const file of files) {
    if (file.size > 12 * 1024 * 1024) {
      $("soundStatus").textContent = `${file.name} is over 12 MB`;
      continue;
    }
    $("soundStatus").textContent = `Adding ${file.name}…`;
    const response = await fetch(`${apiBase}/api/sound/upload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: file.name, data: await fileAsBase64(file) }),
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      $("soundStatus").textContent = result.error || `Couldn’t add ${file.name}`;
      continue;
    }
  }
  event.target.value = "";
  soundSignature = "";
  $("soundStatus").textContent = "Ready";
  refresh();
});

$("stopSound").addEventListener("click", stopSound);

$("memoryForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch(`${apiBase}/api/memory/add`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      category: $("memoryCategory").value,
      subject: $("memorySubject").value,
      content: $("memoryContent").value,
      importance: Number($("memoryImportance").value),
      pinned: $("memoryPinned").checked,
    }),
  });
  if (response.ok) {
    event.currentTarget.reset();
    $("memoryImportance").value = "0.6";
    memorySignature = "";
    refresh();
  }
});

$("memoryList").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-memory-archive]");
  if (!button || !window.confirm("Let Sophia forget this memory?")) return;
  await fetch(`${apiBase}/api/memory/archive`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: decodeURIComponent(button.dataset.memoryArchive) }),
  });
  memorySignature = "";
  refresh();
});

$("personalityFields").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-profile-save]");
  if (!button) return;
  const key = button.dataset.profileSave;
  const field = document.querySelector(`[data-profile-value="${CSS.escape(key)}"]`);
  await fetch(`${apiBase}/api/personality`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value: field.value }),
  });
  personalitySignature = "";
  refresh();
});

$("gameConfigForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  await fetch(`${apiBase}/api/game/config`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ log_path: $("wowLogPath").value, player_name: $("wowPlayerName").value }),
  });
  $("gameEventStatus").textContent = "Saved · restart Sophia if changing an active log";
});

document.querySelectorAll("[data-test-event]").forEach((button) => {
  button.addEventListener("click", async () => {
    await fetch(`${apiBase}/api/game/event`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_type: button.dataset.testEvent }),
    });
    eventSignature = "";
    refresh();
  });
});

$("youtubeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const cue = parseCue($("youtubeStart").value);
  if (cue === null) {
    $("youtubeStatus").textContent = "Start time should look like 23 or 0:23";
    return;
  }
  const response = await fetch(`${apiBase}/api/youtube/add`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: $("youtubeUrl").value,
      title: $("youtubeTitle").value,
      use_when: $("youtubeUseWhen").value,
      start_seconds: cue,
    }),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    $("youtubeStatus").textContent = result.error || "Couldn’t save that video";
    return;
  }
  event.currentTarget.reset();
  videoSignature = "";
  $("youtubeStatus").textContent = "Saved to Sophia’s shelf";
  refresh();
});

$("savedVideos").addEventListener("click", async (event) => {
  const play = event.target.closest("[data-video-play]");
  if (play) {
    youtubeCommand("play", decodeURIComponent(play.dataset.videoPlay));
    return;
  }
  const remove = event.target.closest("[data-video-remove]");
  if (remove && window.confirm("Remove this video from Sophia’s shelf?")) {
    await fetch(`${apiBase}/api/youtube/remove`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: decodeURIComponent(remove.dataset.videoRemove) }),
    });
    videoSignature = "";
    refresh();
  }
});

document.querySelectorAll("[data-video-action]").forEach((button) => {
  button.addEventListener("click", () => youtubeCommand(button.dataset.videoAction));
});

$("youtubeSeekButton").addEventListener("click", () => {
  const cue = parseCue($("youtubeSeek").value);
  if (cue === null) {
    $("youtubeStatus").textContent = "Jump time should look like 23 or 0:23";
    return;
  }
  youtubeCommand("seek", null, cue);
});

$("youtubeVolume").addEventListener("input", (event) => {
  if (youtubePlayerReady) youtubePlayer.setVolume(Number(event.currentTarget.value));
});

$("sleepButton").addEventListener("click", async () => {
  if (!window.confirm("Put Sophia to sleep?")) return;
  await fetch(`${apiBase}/api/sleep`, { method: "POST" });
});

$("clearTranscript").addEventListener("click", async () => {
  try {
    const state = await (await fetch(`${apiBase}/api/state`)).json();
    locallyCleared = state.messages.length;
    renderMessages(state.messages);
  } catch (_) {}
});

function prefillYoutubeFromExtension() {
  const params = new URLSearchParams(window.location.search);
  const url = params.get("youtube_url");
  if (!url) return;
  $("youtubeUrl").value = url;
  $("youtubeTitle").value = params.get("youtube_title") || "";
  const start = Number(params.get("youtube_start") || 0);
  $("youtubeStart").value = start > 0 ? formatCue(start) : "";
  $("youtubeStatus").textContent = "Video captured from Chrome — add when to use it, then save";
  $("youtubeForm").scrollIntoView({ behavior: "smooth", block: "center" });
  window.history.replaceState({}, "", `${window.location.pathname}#the-tube`);
}

refresh();
prefillYoutubeFromExtension();
setInterval(refresh, 750);

window.onYouTubeIframeAPIReady = () => {
  youtubePlayer = new YT.Player("youtubePlayer", {
    width: "100%", height: "100%",
    playerVars: { playsinline: 1, enablejsapi: 1, origin: window.location.origin },
    events: {
      onReady: (event) => {
        youtubePlayerReady = true;
        event.target.setVolume(Number($("youtubeVolume").value));
        $("youtubeStatus").textContent = "Ready for a video";
        applyYoutubeCommand(latestYoutubeState);
        reportYoutubeStatus({ status: "ready" });
      },
      onStateChange: (event) => {
        const labels = { "-1": "unstarted", 0: "ended", 1: "playing", 2: "paused", 3: "buffering", 5: "cued" };
        reportYoutubeStatus({ status: labels[event.data] || "ready" });
      },
      onAutoplayBlocked: () => reportYoutubeStatus({ status: "blocked", autoplay_blocked: true }),
      onError: () => reportYoutubeStatus({ status: "unavailable", autoplay_blocked: false }),
    },
  });
};

new IntersectionObserver((entries) => {
  youtubePlayerVisible = entries[0]?.intersectionRatio >= 0.5;
  if (youtubePlayerVisible) applyYoutubeCommand(latestYoutubeState);
}, { threshold: [0.5] }).observe($("youtubeStage"));

const youtubeApiScript = document.createElement("script");
youtubeApiScript.src = "https://www.youtube.com/iframe_api";
youtubeApiScript.async = true;
document.head.appendChild(youtubeApiScript);

setInterval(() => {
  if (youtubePlayerReady && youtubePlayer?.getPlayerState?.() === 1) {
    reportYoutubeStatus({ status: "playing" });
  }
}, 2000);
