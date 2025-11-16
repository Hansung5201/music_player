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

async function apiFetch(path, { method = "GET", body, headers = {}, skipToken = false } = {}) {
  const init = { method, headers: { ...headers } };
  if (body !== undefined) {
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
      <td>${item.title}</td>
      <td>${item.artist}</td>
      <td>${item.track_id}</td>
    `;
    playlistBody.appendChild(row);
  });
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
    return;
  }
  const rows = playbackState.querySelectorAll("dd");
  rows[0].textContent = state.playback.track_id || "-";
  rows[1].textContent = `${state.playback.position_ms ?? 0} ms`;
  rows[2].textContent = state.playback.state;
  rows[3].textContent = state.playback.updated_at;
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

function syncFieldsets() {
  document.getElementById("host-session").disabled = state.role !== "host";
  document.getElementById("guest-session").disabled = state.role !== "guest";
  const sessionReady = Boolean(state.sessionId);
  document.getElementById("playlist-actions").disabled = !sessionReady;
  document.getElementById("playback-actions").disabled = !(sessionReady && state.role === "host");
  document.getElementById("connect-ws").disabled = !(sessionReady && state.token);
}

function updateSessionSummary({ session_id, playlist, playback_state, code }) {
  const sessionChanged = session_id && session_id !== state.sessionId;
  state.sessionId = session_id || state.sessionId;
  state.inviteCode = code || state.inviteCode;
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
    tokenDisplay.textContent = JSON.stringify(response, null, 2);
    roleDisplay.textContent = response.role;
    setBanner(`Logged in as ${response.role}.`, "success");
    syncFieldsets();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleCreateSession(event) {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.target).entries());
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
    updateSessionSummary({ session_id: response.session_id, playlist: response.playlist, playback_state: response.playback_state, code });
    setBanner("Joined session.", "success");
    syncFieldsets();
  } catch (error) {
    setBanner(error.message, "error");
  }
}

async function handleAddTrack(event) {
  event.preventDefault();
  if (!state.sessionId) return;
  const body = Object.fromEntries(new FormData(event.target).entries());
  try {
    await apiFetch(`/sessions/${state.sessionId}/playlist`, { method: "POST", body });
    setBanner("Track submitted.", "success");
    event.target.reset();
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

async function handlePlayback(event) {
  event.preventDefault();
  if (!state.sessionId) return;
  const formData = Object.fromEntries(new FormData(event.target).entries());
  const payload = {
    action: formData.action,
  };
  if (formData.track_id) payload.track_id = formData.track_id;
  if (formData.position_ms) payload.position_ms = Number(formData.position_ms);
  try {
    if (!state.websocket) {
      await apiFetch(`/sessions/${state.sessionId}/playback`, { method: "POST", body: payload });
    } else {
      state.websocket.send(
        JSON.stringify({ type: "playback_command", payload })
      );
    }
    setBanner("Playback command dispatched.", "success");
  } catch (error) {
    setBanner(error.message, "error");
  }
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
}

registerListeners();
syncFieldsets();
renderPlaylist();
renderRequests();
renderPlayback();
