const state = {
  token: null,
  userId: null,
  role: null,
  sessionId: null,
  inviteCode: null,
  playlist: [],
  requests: [],
  playback: null,
  websocket: null,
  lastAuthPayload: null,
  selectedRole: "host",
  maxDuration: null,
};

const tokenDisplay = document.getElementById("token-display");
const roleDisplay = document.getElementById("role-display");
const sessionDisplay = document.getElementById("session-id");
const inviteDisplay = document.getElementById("invite-code");
const wsStatus = document.getElementById("ws-status");
const playlistBody = document.getElementById("playlist-body");
const requestList = document.getElementById("request-list");
const playbackState = document.getElementById("playback-state");
const banner = document.getElementById("status-banner");
const activityLog = document.getElementById("activity-log");
const roleInput = document.getElementById("role-input");
const selectedRoleCopy = document.getElementById("selected-role-copy");
const roleCards = document.querySelectorAll("[data-role-select]");
const hostStatus = document.getElementById("host-status");
const guestStatus = document.getElementById("guest-status");
const hostSessionChip = document.getElementById("host-session-chip");
const guestSessionChip = document.getElementById("guest-session-chip");
const hostTokenChip = document.getElementById("host-token-chip");
const guestTokenChip = document.getElementById("guest-token-chip");
const mediaPlayer = document.getElementById("media-player");
const playerTrackName = document.getElementById("player-track-name");
const playerTrackMeta = document.getElementById("player-track-meta");
const playerDurationChip = document.getElementById("player-duration-chip");
const playerCurrentTime = document.getElementById("player-current-time");
const playerDuration = document.getElementById("player-duration");
const playerSeek = document.getElementById("player-seek");
const playerPlayButton = document.getElementById("player-play");
const playerPauseButton = document.getElementById("player-pause");
const playerPrevButton = document.getElementById("player-prev");
const playerNextButton = document.getElementById("player-next");
const mediaFileInput = document.getElementById("media-file-input");
const durationField = document.getElementById("duration-seconds-input");
const durationCopy = document.getElementById("detected-duration-copy");
const maxDurationCopy = document.getElementById("max-duration-copy");
const durationProbe = document.createElement("audio");
durationProbe.preload = "metadata";
let durationProbeUrl = null;

function setBanner(message, tone = "info") {
  banner.textContent = message;
  banner.style.background =
    tone === "error"
      ? "rgba(239, 68, 68, 0.95)"
      : tone === "success"
      ? "rgba(34, 197, 94, 0.95)"
      : "rgba(37, 99, 235, 0.95)";
  banner.classList.remove("hidden");
  clearTimeout(setBanner.timeout);
  setBanner.timeout = setTimeout(() => banner.classList.add("hidden"), 4000);
}

function formatDuration(totalSeconds) {
  if (!Number.isFinite(totalSeconds)) return "0:00";
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return `${minutes}:${remaining.toString().padStart(2, "0")}`;
}

function updateMaxDurationCopy() {
  if (!maxDurationCopy) return;
  if (typeof state.maxDuration === "number" && state.maxDuration > 0) {
    maxDurationCopy.textContent = `${state.maxDuration} seconds`;
  } else {
    maxDurationCopy.textContent = "No limit";
  }
}

async function apiFetch(path, { method = "GET", body, headers = {}, skipToken = false } = {}) {
  const init = { method, headers: { ...headers } };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["Content-Type"] = "application/json";
  }
  if (!skipToken && state.token) {
    init.headers["X-User-Token"] = state.token;
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || response.statusText);
  }
  return response.json();
}

function renderPlaylist() {
  playlistBody.innerHTML = "";
  state.playlist.forEach((item, index) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${index + 1}</td>
      <td><strong>${item.name}</strong><span class="table-meta">${item.track_id}</span></td>
      <td>${item.duration_seconds ? formatDuration(item.duration_seconds) : "--"}</td>
      <td>${item.media_type?.startsWith("video") ? "Video" : "Audio"}</td>
    `;
    playlistBody.appendChild(row);
  });
  syncPlayerWithPlayback();
}

function renderRequests() {
  requestList.innerHTML = "";
  if (state.requests.length === 0) {
    requestList.innerHTML = '<li class="empty">No requests yet.</li>';
    return;
  }
  state.requests.forEach((request) => {
    const li = document.createElement("li");
    li.dataset.status = request.status;
    li.innerHTML = `<strong>${request.request_type}</strong> — ${request.status} <br /><small>${JSON.stringify(
      request.payload
    )}</small>`;
    requestList.appendChild(li);
  });
}

function renderPlayback() {
  if (!state.playback) {
    playbackState.querySelectorAll("dd").forEach((node) => (node.textContent = "-"));
    syncPlayerWithPlayback();
    return;
  }
  const rows = playbackState.querySelectorAll("dd");
  rows[0].textContent = state.playback.track_id || "-";
  const positionSeconds = Math.floor((state.playback.position_ms ?? 0) / 1000);
  rows[1].textContent = `${formatDuration(positionSeconds)} (${state.playback.position_ms ?? 0} ms)`;
  rows[2].textContent = state.playback.state;
  rows[3].textContent = state.playback.updated_at;
  syncPlayerWithPlayback();
}

function logActivity(line) {
  const timestamp = new Date().toLocaleTimeString();
  const content = `[${timestamp}] ${line}`;
  if (activityLog.textContent.includes("Waiting")) {
    activityLog.textContent = content;
  } else {
    activityLog.textContent += `\n${content}`;
  }
  activityLog.scrollTop = activityLog.scrollHeight;
}

function getActiveTrack() {
  if (!state.playback) return null;
  return state.playlist.find((item) => item.track_id === state.playback.track_id) || null;
}

function syncPlayerControls() {
  const disabled = !(state.role === "host" && state.sessionId);
  [playerPlayButton, playerPauseButton, playerPrevButton, playerNextButton, playerSeek].forEach((node) => {
    if (node) {
      node.disabled = disabled;
    }
  });
}

function requireHostControl() {
  if (state.role !== "host") {
    setBanner("Only the host can control playback.", "error");
    return false;
  }
  if (!state.sessionId) {
    setBanner("Join or create a session first.", "error");
    return false;
  }
  return true;
}

function syncPlayerWithPlayback() {
  if (!mediaPlayer) return;
  const playback = state.playback;
  const track = getActiveTrack();
  if (playerTrackName) {
    playerTrackName.textContent = track ? track.name : "Upload a track to begin";
  }
  if (playerTrackMeta) {
    playerTrackMeta.textContent = track ? track.track_id : "No track loaded";
  }
  if (playerDurationChip) {
    playerDurationChip.textContent = track?.duration_seconds
      ? formatDuration(track.duration_seconds)
      : "--";
  }
  const positionSeconds = playback ? Math.floor((playback.position_ms ?? 0) / 1000) : 0;
  if (playerCurrentTime) {
    playerCurrentTime.textContent = formatDuration(positionSeconds);
  }
  const trackDuration = track?.duration_seconds ?? Math.floor(mediaPlayer.duration || 0);
  if (playerDuration) {
    playerDuration.textContent = trackDuration ? formatDuration(trackDuration) : "0:00";
  }
  if (playerSeek) {
    playerSeek.max = trackDuration || 0;
    playerSeek.value = Math.min(positionSeconds, Number(playerSeek.max) || 0);
  }
  if (track && mediaPlayer.dataset.currentTrack !== track.media_url) {
    mediaPlayer.src = track.media_url;
    mediaPlayer.dataset.currentTrack = track.media_url;
  }
  if (playback && playback.state === "playing" && track) {
    const diff = Math.abs(mediaPlayer.currentTime - positionSeconds);
    if (diff > 1) {
      mediaPlayer.currentTime = positionSeconds;
    }
    mediaPlayer.play().catch(() => {});
  } else {
    mediaPlayer.pause();
  }
}

function handleMediaSelection(event) {
  if (!durationField || !durationCopy) return;
  durationField.value = "";
  durationCopy.textContent = "Select a file to auto-detect its runtime.";
  const file = event.target.files && event.target.files[0];
  if (!file) {
    return;
  }
  durationCopy.textContent = "Detecting duration...";
  if (durationProbeUrl) {
    URL.revokeObjectURL(durationProbeUrl);
  }
  durationProbeUrl = URL.createObjectURL(file);
  durationProbe.src = durationProbeUrl;
  durationProbe.onloadedmetadata = () => {
    if (Number.isFinite(durationProbe.duration)) {
      const seconds = Math.round(durationProbe.duration);
      durationField.value = seconds;
      durationCopy.textContent = `Detected duration: ${formatDuration(seconds)}`;
    } else {
      durationCopy.textContent = "Couldn't read media length. You can still upload.";
    }
    URL.revokeObjectURL(durationProbeUrl);
    durationProbeUrl = null;
  };
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result === "string") {
        const [, base64 = ""] = result.split(",");
        resolve(base64);
      } else {
        reject(new Error("Unable to read file"));
      }
    };
    reader.onerror = () => reject(reader.error || new Error("Unable to read file"));
    reader.readAsDataURL(file);
  });
}

function syncFieldsets() {
  document.getElementById("host-session").disabled = state.role !== "host";
  document.getElementById("guest-session").disabled = state.role !== "guest";
  const sessionReady = Boolean(state.sessionId);
  document.getElementById("playlist-actions").disabled = !sessionReady;
  document.getElementById("playback-actions").disabled = !(sessionReady && state.role === "host");
  document.getElementById("connect-ws").disabled = !(sessionReady && state.token);
  syncPlayerControls();
}

function updateSessionSummary({ session_id, playlist, playback_state, code, max_media_duration_seconds }) {
  const sessionChanged = session_id && session_id !== state.sessionId;
  state.sessionId = session_id || state.sessionId;
  state.inviteCode = code || state.inviteCode;
  if (typeof max_media_duration_seconds !== "undefined") {
    state.maxDuration = max_media_duration_seconds;
    updateMaxDurationCopy();
  }
  if (sessionChanged) {
    state.requests = [];
    renderRequests();
  }
  if (playlist) {
    state.playlist = playlist;
    renderPlaylist();
  }
  if (playback_state) {
    state.playback = playback_state;
    renderPlayback();
  }
  sessionDisplay.textContent = state.sessionId || "-";
  inviteDisplay.textContent = state.inviteCode || "-";
  syncFieldsets();
  renderRoleCards();
}

function updateRequestsList(requestPayload) {
  const existingIndex = state.requests.findIndex((entry) => entry.id === requestPayload.id);
  if (existingIndex >= 0) {
    state.requests[existingIndex] = requestPayload;
  } else {
    state.requests.unshift(requestPayload);
  }
  renderRequests();
}

function resetWebSocket() {
  if (state.websocket) {
    state.websocket.close();
    state.websocket = null;
  }
  wsStatus.textContent = "Disconnected";
  wsStatus.classList.remove("connected", "error");
}

async function handleLogin(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target).entries());
  try {
    const response = await apiFetch("/auth/login", { method: "POST", body: data, skipToken: true });
    state.token = response.token;
    state.userId = response.user_id;
    state.role = response.role;
    state.lastAuthPayload = response;
    state.selectedRole = response.role;
    if (roleInput) {
      roleInput.value = response.role;
    }
    tokenDisplay.textContent = JSON.stringify(response, null, 2);
    roleDisplay.textContent = response.role;
    setBanner(`Logged in as ${response.role}.`, "success");
    syncFieldsets();
    renderRoleCards();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleCreateSession(event) {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.target).entries());
  if (!body.max_media_duration_seconds) {
    delete body.max_media_duration_seconds;
  } else {
    body.max_media_duration_seconds = Number(body.max_media_duration_seconds);
  }
  try {
    const response = await apiFetch("/sessions", { method: "POST", body });
    updateSessionSummary(response);
    if (state.lastAuthPayload) {
      state.lastAuthPayload = { ...state.lastAuthPayload, host_token: response.host_token };
      tokenDisplay.textContent = JSON.stringify(state.lastAuthPayload, null, 2);
    }
    setBanner("Session created.", "success");
    logActivity(`Session ${response.session_id} created with code ${response.code}.`);
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleJoinSession(event) {
  event.preventDefault();
  const { code, guest_name } = Object.fromEntries(new FormData(event.target).entries());
  try {
    const response = await apiFetch(`/sessions/${encodeURIComponent(code)}/join`, {
      method: "POST",
      body: { guest_name },
      skipToken: true,
    });
    state.token = response.guest_token;
    state.lastAuthPayload = response;
    tokenDisplay.textContent = JSON.stringify(response, null, 2);
    updateSessionSummary({
      session_id: response.session_id,
      playlist: response.playlist,
      playback_state: response.playback_state,
      code,
      max_media_duration_seconds: response.max_media_duration_seconds,
    });
    setBanner("Joined session.", "success");
    syncFieldsets();
    renderRoleCards();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleAddTrack(event) {
  event.preventDefault();
  if (!state.sessionId) return;
  const formValues = new FormData(event.target);
  const file = mediaFileInput?.files?.[0];
  if (!file) {
    setBanner("Please attach an MP3 or MP4 file.", "error");
    return;
  }
  try {
    const media = await readFileAsBase64(file);
    const payload = {
      track_id: formValues.get("track_id"),
      name: formValues.get("name"),
      duration_seconds: formValues.get("duration_seconds")
        ? Number(formValues.get("duration_seconds"))
        : undefined,
      media: {
        filename: file.name,
        content_type: file.type,
        data: media,
      },
    };
    await apiFetch(`/sessions/${state.sessionId}/playlist`, { method: "POST", body: payload });
    setBanner("Track submitted.", "success");
    event.target.reset();
    if (durationCopy) {
      durationCopy.textContent = "Select a file to auto-detect its runtime.";
    }
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleGuestRequest(event) {
  event.preventDefault();
  if (!state.sessionId) return;
  const payloadField = event.target.elements.payload.value;
  try {
    const parsed = JSON.parse(payloadField);
    const response = await apiFetch(`/sessions/${state.sessionId}/requests`, { method: "POST", body: parsed });
    updateRequestsList(response);
    setBanner("Request sent.", "success");
    logActivity(`Request ${response.id} queued.`);
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function dispatchPlaybackCommand(payload) {
  if (!state.sessionId) return;
  if (!state.websocket) {
    await apiFetch(`/sessions/${state.sessionId}/playback`, { method: "POST", body: payload });
  } else {
    state.websocket.send(
      JSON.stringify({ type: "playback_command", payload })
    );
  }
}

async function sendCommandWithBanner(payload) {
  try {
    await dispatchPlaybackCommand(payload);
    setBanner("Playback command dispatched.", "success");
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handlePlayback(event) {
  event.preventDefault();
  if (!state.sessionId) return;
  const formData = Object.fromEntries(new FormData(event.target).entries());
  const payload = {
    action: formData.action,
  };
  if (formData.track_id) payload.track_id = formData.track_id;
  if (formData.position_ms) payload.position_ms = Number(formData.position_ms);
  await sendCommandWithBanner(payload);
}

function connectWebSocket() {
  if (!state.sessionId || !state.token) return;
  resetWebSocket();
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${protocol}://${window.location.host}/ws/sessions/${state.sessionId}?token=${state.token}`;
  const ws = new WebSocket(url);
  state.websocket = ws;
  wsStatus.textContent = "Connecting";
  ws.addEventListener("open", () => {
    wsStatus.textContent = "Connected";
    wsStatus.classList.add("connected");
    logActivity("WebSocket connected.");
  });
  ws.addEventListener("close", () => {
    wsStatus.textContent = "Disconnected";
    wsStatus.classList.remove("connected");
    logActivity("WebSocket closed.");
    state.websocket = null;
  });
  ws.addEventListener("message", (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.type === "playlist_update") {
        state.playlist = message.payload.playlist;
        renderPlaylist();
      } else if (message.type === "playback_state") {
        state.playback = message.payload;
        renderPlayback();
      } else if (message.type === "request_update") {
        updateRequestsList(message.payload);
      }
      logActivity(`WS ${message.type}`);
    } catch (error) {
      console.error("Malformed WebSocket payload", error);
    }
  });
  ws.addEventListener("error", () => {
    wsStatus.textContent = "Error";
    wsStatus.classList.add("error");
  });
}

function registerListeners() {
  document.getElementById("login-form").addEventListener("submit", handleLogin);
  document.getElementById("create-session-form").addEventListener("submit", handleCreateSession);
  document.getElementById("join-session-form").addEventListener("submit", handleJoinSession);
  document.getElementById("add-track-form").addEventListener("submit", handleAddTrack);
  document.getElementById("guest-request-form").addEventListener("submit", handleGuestRequest);
  document.getElementById("playback-form").addEventListener("submit", handlePlayback);
  document.getElementById("connect-ws").addEventListener("click", connectWebSocket);
  roleCards.forEach((card) => {
    card.addEventListener("click", () => {
      const role = card.dataset.roleSelect;
      state.selectedRole = role;
      if (roleInput) {
        roleInput.value = role;
      }
      renderRoleCards();
    });
  });
}

function registerPlayerControls() {
  if (mediaFileInput) {
    mediaFileInput.addEventListener("change", handleMediaSelection);
  }
  if (playerPlayButton) {
    playerPlayButton.addEventListener("click", () => {
      if (!requireHostControl()) return;
      const trackId = state.playback?.track_id || state.playlist[0]?.track_id;
      if (!trackId) {
        setBanner("Upload a track first.", "error");
        return;
      }
      const position = Math.round(mediaPlayer?.currentTime || 0) * 1000;
      sendCommandWithBanner({ action: "play", track_id: trackId, position_ms: position });
    });
  }
  if (playerPauseButton) {
    playerPauseButton.addEventListener("click", () => {
      if (!requireHostControl()) return;
      const trackId = state.playback?.track_id || state.playlist[0]?.track_id;
      if (!trackId) return;
      sendCommandWithBanner({ action: "pause", track_id: trackId });
    });
  }
  if (playerPrevButton) {
    playerPrevButton.addEventListener("click", () => {
      if (!requireHostControl()) return;
      sendCommandWithBanner({ action: "skip_prev" });
    });
  }
  if (playerNextButton) {
    playerNextButton.addEventListener("click", () => {
      if (!requireHostControl()) return;
      sendCommandWithBanner({ action: "skip_next" });
    });
  }
  if (playerSeek) {
    playerSeek.addEventListener("change", (event) => {
      if (!requireHostControl()) return;
      const seconds = Number(event.target.value);
      if (!Number.isFinite(seconds)) return;
      const trackId = state.playback?.track_id;
      if (!trackId) return;
      playerCurrentTime.textContent = formatDuration(seconds);
      sendCommandWithBanner({ action: "seek", track_id: trackId, position_ms: seconds * 1000 });
    });
  }
  if (mediaPlayer) {
    mediaPlayer.addEventListener("timeupdate", () => {
      if (playerCurrentTime) {
        playerCurrentTime.textContent = formatDuration(Math.floor(mediaPlayer.currentTime));
      }
      const isActive = typeof playerSeek?.matches === "function" && playerSeek.matches(":active");
      if (playerSeek && !isActive) {
        playerSeek.value = Math.floor(mediaPlayer.currentTime);
      }
    });
    mediaPlayer.addEventListener("loadedmetadata", syncPlayerWithPlayback);
  }
}

function renderRoleCards() {
  if (selectedRoleCopy) {
    selectedRoleCopy.textContent = state.selectedRole === "host" ? "Host console" : "Guest console";
  }
  roleCards.forEach((card) => {
    const isActive = card.dataset.roleSelect === state.selectedRole;
    card.classList.toggle("active", isActive);
    card.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
  if (hostStatus) {
    hostStatus.textContent =
      state.role === "host" && state.userId
        ? `Ready (user #${state.userId})`
        : state.selectedRole === "host"
        ? "Tap login to get started"
        : "Not authenticated";
  }
  if (guestStatus) {
    guestStatus.textContent =
      state.role === "guest" && state.userId
        ? `Ready (user #${state.userId})`
        : state.selectedRole === "guest"
        ? "Tap login to get started"
        : "Not authenticated";
  }
  if (hostSessionChip) {
    hostSessionChip.textContent = state.role === "host" && state.sessionId ? state.sessionId : "No session";
  }
  if (guestSessionChip) {
    guestSessionChip.textContent = state.role === "guest" && state.sessionId ? state.sessionId : "No session";
  }
  if (hostTokenChip) {
    hostTokenChip.textContent = state.role === "host" && state.token ? "Token ready" : "Tap card & login";
  }
  if (guestTokenChip) {
    guestTokenChip.textContent = state.role === "guest" && state.token ? "Token ready" : "Tap card & login";
  }
}

registerListeners();
registerPlayerControls();
syncFieldsets();
renderPlaylist();
renderRequests();
renderPlayback();
renderRoleCards();
updateMaxDurationCopy();
