/* ==============================================================
   AI Maintenance Voice Copilot - Frontend Logic
   --------------------------------------------------------------
   Talks to the Flask API defined in backend/app.py. All requests
   are same-origin relative paths, since Flask serves this file
   itself (see FRONTEND_FOLDER / static_folder in app.py), and the
   session cookie rides along automatically.

   Flow:
     1. On load, ask /api/auth/me who we are. Signed in -> straight
        to the right view. Not signed in -> login screen.
     2. Role decides the view. A technician runs inspection
        sessions; a supervisor reviews everyone's records. Nobody
        sees the other's screen - the server enforces this too,
        this is just the UI half.
     3. Any 401 mid-session drops back to login rather than failing
        silently, which is what happens when a session expires.

   There is no registration screen. Accounts are created by an
   administrator with backend/scripts/manage_users.py.
============================================================== */

(() => {
  "use strict";

  const API = {
    login: () => "/api/auth/login",
    logout: () => "/api/auth/logout",
    me: () => "/api/auth/me",
    createSession: () => "/api/sessions",
    session: (id) => `/api/sessions/${id}`,
    sendVoice: (id) => `/api/sessions/${id}/voice`,
    sendMessage: (id) => `/api/sessions/${id}/message`,
    speak: (id) => `/api/sessions/${id}/speak`,
    records: (params) => `/api/records${params ? `?${params}` : ""}`,
    record: (id) => `/api/records/${id}`,
    report: (id) => `/api/records/${id}/report`,
  };

  const ROLE_TECHNICIAN = "TECHNICIAN";
  const ROLE_SUPERVISOR = "SUPERVISOR";

  // ------------------------------------------------------------
  // State
  // ------------------------------------------------------------

  const state = {
    user: null,
    sessionId: null,
    recordId: null,
    isRecording: false,
    isBusy: false,
    mediaRecorder: null,
    audioChunks: [],
    selectedRecordId: null,
  };

  // ------------------------------------------------------------
  // Element refs
  // ------------------------------------------------------------

  const els = {
    // shell
    shellUser: document.getElementById("shell-user"),
    shellUserName: document.getElementById("shell-user-name"),
    shellUserRole: document.getElementById("shell-user-role"),
    btnSignOut: document.getElementById("btn-sign-out"),

    // views
    viewLogin: document.getElementById("view-login"),
    viewTechnician: document.getElementById("view-technician"),
    viewSupervisor: document.getElementById("view-supervisor"),

    // login
    formLogin: document.getElementById("form-login"),
    inputUsername: document.getElementById("input-username"),
    inputPassword: document.getElementById("input-password"),
    btnLogin: document.getElementById("btn-login"),
    loginError: document.getElementById("login-error"),

    // technician
    panelStart: document.getElementById("panel-start"),
    btnStartSession: document.getElementById("btn-start-session"),
    startSessionError: document.getElementById("start-session-error"),
    workspace: document.getElementById("workspace"),
    sessionTechnicianName: document.getElementById("session-technician-name"),
    btnEndSession: document.getElementById("btn-end-session"),
    micBtn: document.getElementById("btn-mic"),
    voiceStatus: document.getElementById("voice-status"),
    formTextFallback: document.getElementById("form-text-fallback"),
    inputTextMessage: document.getElementById("input-text-message"),
    transcript: document.getElementById("transcript"),
    replyAudio: document.getElementById("reply-audio"),
    toggleSpeak: document.getElementById("toggle-speak"),
    recordStatusPill: document.getElementById("record-status-pill"),
    recordFields: document.getElementById("record-fields"),
    btnDownloadReport: document.getElementById("btn-download-report"),

    // supervisor
    inputFilterAircraft: document.getElementById("input-filter-aircraft"),
    btnRefreshRecords: document.getElementById("btn-refresh-records"),
    recordsTableBody: document.getElementById("records-table-body"),
    recordDetail: document.getElementById("record-detail"),
  };

  // ------------------------------------------------------------
  // HTTP helpers
  // ------------------------------------------------------------

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
    });

    if (response.status === 401 && !url.endsWith("/api/auth/me")) {
      showLogin("Your session expired. Sign in again.");
      throw new ApiError("Not signed in", 401);
    }

    let payload = null;
    const type = response.headers.get("content-type") || "";
    if (type.includes("application/json")) {
      payload = await response.json().catch(() => null);
    }

    if (!response.ok) {
      const message = (payload && payload.error) || `Request failed (${response.status})`;
      throw new ApiError(message, response.status);
    }

    return payload;
  }

  function postJson(url, body) {
    return api(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }

  // ------------------------------------------------------------
  // View switching
  // ------------------------------------------------------------

  function showView(view) {
    [els.viewLogin, els.viewTechnician, els.viewSupervisor].forEach((section) => {
      section.classList.toggle("is-active", section === view);
    });
  }

  function showLogin(message) {
    state.user = null;
    state.sessionId = null;
    state.recordId = null;
    els.shellUser.hidden = true;
    els.loginError.textContent = message || "";
    showView(els.viewLogin);
    els.inputPassword.value = "";
    els.inputUsername.focus();
  }

  function showForRole(user) {
    state.user = user;

    els.shellUserName.textContent = user.full_name;
    els.shellUserRole.textContent =
      user.role === ROLE_SUPERVISOR ? "Supervisor" : "Technician";
    els.shellUser.hidden = false;

    if (user.role === ROLE_SUPERVISOR) {
      showView(els.viewSupervisor);
      loadRecords();
    } else {
      showView(els.viewTechnician);
      els.panelStart.hidden = false;
      els.workspace.hidden = true;
    }
  }

  // ------------------------------------------------------------
  // Authentication
  // ------------------------------------------------------------

  async function restoreSession() {
    try {
      const data = await api(API.me());
      if (data && data.user) {
        showForRole(data.user);
        return;
      }
    } catch (_err) {
      /* not signed in - fall through */
    }
    showLogin();
  }

  async function handleLogin(event) {
    event.preventDefault();

    const username = els.inputUsername.value.trim();
    const password = els.inputPassword.value;

    if (!username || !password) {
      els.loginError.textContent = "Enter your username and password.";
      return;
    }

    els.btnLogin.disabled = true;
    els.btnLogin.textContent = "Signing in…";
    els.loginError.textContent = "";

    try {
      const data = await postJson(API.login(), { username, password });
      els.inputPassword.value = "";
      showForRole(data.user);
    } catch (err) {
      els.loginError.textContent = err.message;
      els.inputPassword.value = "";
      els.inputPassword.focus();
    } finally {
      els.btnLogin.disabled = false;
      els.btnLogin.textContent = "Sign in";
    }
  }

  async function handleSignOut() {
    try {
      if (state.sessionId) {
        await api(API.session(state.sessionId), { method: "DELETE" }).catch(() => {});
      }
      await postJson(API.logout());
    } finally {
      clearTranscript();
      showLogin();
    }
  }

  // ------------------------------------------------------------
  // Technician: sessions
  // ------------------------------------------------------------

  async function startSession() {
    els.btnStartSession.disabled = true;
    els.startSessionError.textContent = "";

    try {
      const data = await postJson(API.createSession());
      state.sessionId = data.session_id;
      state.recordId = null;

      els.sessionTechnicianName.textContent = data.technician;
      els.panelStart.hidden = true;
      els.workspace.hidden = false;

      clearTranscript();
      resetRecordCard();
      setVoiceStatus("Tap to speak a finding");
    } catch (err) {
      els.startSessionError.textContent = err.message;
    } finally {
      els.btnStartSession.disabled = false;
    }
  }

  async function endSession() {
    if (!state.sessionId) return;

    try {
      await api(API.session(state.sessionId), { method: "DELETE" });
    } catch (_err) {
      /* ending a session that's already gone is fine */
    }

    state.sessionId = null;
    state.recordId = null;
    els.workspace.hidden = true;
    els.panelStart.hidden = false;
    stopPlayback();
  }

  // ------------------------------------------------------------
  // Technician: conversation
  // ------------------------------------------------------------

  function appendTurn(role, text) {
    const empty = els.transcript.querySelector(".transcript__empty");
    if (empty) empty.remove();

    const turn = document.createElement("div");
    turn.className = `transcript-turn transcript-turn--${role}`;

    const label = document.createElement("span");
    label.className = "transcript-turn__label";
    label.textContent = role === "technician" ? "You" : "Copilot";

    const body = document.createElement("span");
    body.textContent = text;

    turn.append(label, body);
    els.transcript.appendChild(turn);
    els.transcript.scrollTop = els.transcript.scrollHeight;
  }

  function clearTranscript() {
    els.transcript.innerHTML =
      '<p class="transcript__empty">Your conversation with the copilot will appear here.</p>';
  }

  function playReply(url) {
    if (!url || !els.toggleSpeak.checked) return;
    els.replyAudio.src = url;
    els.replyAudio.play().catch(() => {
      // Autoplay can be blocked until the user interacts with the
      // page. Tapping the mic counts, so this rarely fires twice.
      setVoiceStatus("Tap the mic once to enable audio replies");
    });
  }

  function stopPlayback() {
    els.replyAudio.pause();
    els.replyAudio.removeAttribute("src");
  }

  async function sendText(event) {
    event.preventDefault();

    const text = els.inputTextMessage.value.trim();
    if (!text || !state.sessionId || state.isBusy) return;

    els.inputTextMessage.value = "";
    appendTurn("technician", text);
    setBusy(true, "Thinking…");

    try {
      const data = await postJson(API.sendMessage(state.sessionId), { text });
      appendTurn("assistant", data.reply);
      applyRecordState(data);

      // The text path returns no audio, so ask for it separately
      // when the technician wants replies read aloud.
      if (els.toggleSpeak.checked) {
        try {
          const audio = await postJson(API.speak(state.sessionId), { text: data.reply });
          playReply(audio.reply_audio_url);
        } catch (_err) {
          /* silent: a missing voice model shouldn't break the turn */
        }
      }
    } catch (err) {
      appendTurn("assistant", `Couldn't send that: ${err.message}`);
    } finally {
      setBusy(false, "Tap to speak a finding");
    }
  }

  async function sendVoice(blob) {
    if (!state.sessionId) return;

    const form = new FormData();
    form.append("audio", blob, "recording.webm");

    setBusy(true, "Transcribing…");

    try {
      const data = await api(API.sendVoice(state.sessionId), {
        method: "POST",
        body: form,
      });

      appendTurn("technician", data.transcript);
      appendTurn("assistant", data.reply);
      applyRecordState(data);
      playReply(data.reply_audio_url);
    } catch (err) {
      appendTurn("assistant", `Couldn't process that recording: ${err.message}`);
    } finally {
      setBusy(false, "Tap to speak a finding");
    }
  }

  // ------------------------------------------------------------
  // Technician: microphone
  // ------------------------------------------------------------

  async function toggleRecording() {
    if (state.isBusy) return;

    if (state.isRecording) {
      state.mediaRecorder.stop();
      return;
    }

    if (!navigator.mediaDevices || !window.MediaRecorder) {
      setVoiceStatus("This browser can't record audio — type instead");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);

      state.mediaRecorder = recorder;
      state.audioChunks = [];

      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) state.audioChunks.push(event.data);
      });

      recorder.addEventListener("stop", () => {
        stream.getTracks().forEach((track) => track.stop());
        state.isRecording = false;
        els.micBtn.classList.remove("is-recording");
        els.micBtn.setAttribute("aria-label", "Start recording");

        const blob = new Blob(state.audioChunks, { type: "audio/webm" });
        if (blob.size > 0) sendVoice(blob);
      });

      recorder.start();
      state.isRecording = true;
      els.micBtn.classList.add("is-recording");
      els.micBtn.setAttribute("aria-label", "Stop recording");
      setVoiceStatus("Listening — tap again when you're done");
    } catch (_err) {
      setVoiceStatus("Microphone access was blocked — type instead");
    }
  }

  function setVoiceStatus(text) {
    els.voiceStatus.textContent = text;
  }

  function setBusy(busy, status) {
    state.isBusy = busy;
    els.micBtn.classList.toggle("is-busy", busy);
    els.micBtn.disabled = busy;
    if (status) setVoiceStatus(status);
  }

  // ------------------------------------------------------------
  // Technician: record card
  // ------------------------------------------------------------

  function resetRecordCard() {
    els.recordFields.querySelectorAll("dd").forEach((cell) => {
      cell.textContent = "—";
      cell.className = "is-empty";
    });
    els.recordStatusPill.textContent = "Not started";
    els.recordStatusPill.className = "status-pill";
    els.btnDownloadReport.disabled = true;
  }

  async function applyRecordState(turnData) {
    state.recordId = turnData.record_id || state.recordId;

    if (!state.recordId) {
      resetRecordCard();
      return;
    }

    els.recordStatusPill.textContent = turnData.record_complete ? "Complete" : "In progress";
    els.recordStatusPill.className =
      "status-pill " + (turnData.record_complete ? "status-pill--complete" : "status-pill--open");
    els.btnDownloadReport.disabled = false;

    try {
      const data = await api(API.session(state.sessionId));
      if (data.record) fillRecordFields(data.record);
    } catch (_err) {
      /* the card is a convenience, not the source of truth */
    }
  }

  function fillRecordFields(record) {
    els.recordFields.querySelectorAll("dd").forEach((cell) => {
      const value = record[cell.dataset.field];
      if (value) {
        cell.textContent = value;
        cell.className = "";
        if (cell.dataset.field === "SEVERITY") {
          cell.className = `severity--${String(value).toLowerCase()}`;
        }
      } else {
        cell.textContent = "—";
        cell.className = "is-empty";
      }
    });
  }

  function downloadReport() {
    if (!state.recordId) return;
    window.open(API.report(state.recordId), "_blank");
  }

  // ------------------------------------------------------------
  // Supervisor: records
  // ------------------------------------------------------------

  async function loadRecords() {
    const filter = els.inputFilterAircraft.value.trim();
    const params = filter ? `aircraft_reg=${encodeURIComponent(filter)}` : "";

    els.recordsTableBody.innerHTML =
      '<tr><td colspan="6" class="records-table__empty">Loading records…</td></tr>';

    try {
      const data = await api(API.records(params));
      renderRecords(data.records);
    } catch (err) {
      els.recordsTableBody.innerHTML =
        `<tr><td colspan="6" class="records-table__empty">${escapeHtml(err.message)}</td></tr>`;
    }
  }

  function renderRecords(records) {
    if (!records.length) {
      els.recordsTableBody.innerHTML =
        '<tr><td colspan="6" class="records-table__empty">No findings logged yet.</td></tr>';
      return;
    }

    els.recordsTableBody.innerHTML = "";

    records.forEach((record) => {
      const row = document.createElement("tr");
      row.dataset.recordId = record.RECORD_ID;

      const severity = (record.SEVERITY || "").toLowerCase();
      const badgeClass = ["minor", "major", "critical", "aog"].includes(severity)
        ? `badge--${severity}`
        : "badge--default";

      row.innerHTML = `
        <td>${escapeHtml(record.AIRCRAFT_REG || "—")}</td>
        <td>${escapeHtml(record.COMPONENT || "—")}</td>
        <td><span class="badge ${badgeClass}">${escapeHtml(record.SEVERITY || "—")}</span></td>
        <td>${escapeHtml(record.STATUS || "—")}</td>
        <td>${escapeHtml(record.TECHNICIAN || "—")}</td>
        <td>${formatDate(record.CREATED_AT)}</td>
      `;

      row.addEventListener("click", () => selectRecord(record.RECORD_ID));
      els.recordsTableBody.appendChild(row);
    });
  }

  async function selectRecord(recordId) {
    state.selectedRecordId = recordId;

    els.recordsTableBody.querySelectorAll("tr").forEach((row) => {
      row.classList.toggle("is-selected", row.dataset.recordId === recordId);
    });

    els.recordDetail.innerHTML =
      '<p class="record-detail__placeholder">Loading…</p>';

    try {
      const data = await api(API.record(recordId));
      renderRecordDetail(data.record, data.conversation || []);
    } catch (err) {
      els.recordDetail.innerHTML =
        `<p class="record-detail__placeholder">${escapeHtml(err.message)}</p>`;
    }
  }

  function renderRecordDetail(record, conversation) {
    const severity = (record.SEVERITY || "").toLowerCase();

    const rows = [
      ["Aircraft", record.AIRCRAFT_REG],
      ["Component", record.COMPONENT],
      ["Location", record.LOCATION],
      ["Severity", record.SEVERITY],
      ["Status", record.STATUS],
      ["Technician", record.TECHNICIAN],
      ["Inspected", formatDate(record.INSPECTION_TS)],
    ];

    const turns = conversation
      .map((turn) => {
        const who = turn.ROLE === "technician" ? "Technician" : "Copilot";
        return `<div class="transcript-turn transcript-turn--${escapeHtml(turn.ROLE)}">
                  <span class="transcript-turn__label">${who}</span>
                  <span>${escapeHtml(turn.MESSAGE || "")}</span>
                </div>`;
      })
      .join("");

    els.recordDetail.innerHTML = `
      <div class="record-detail__header">
        <div>
          <h2 class="record-detail__title">${escapeHtml(record.AIRCRAFT_REG || "Unassigned")}</h2>
          <p class="record-detail__subtitle">${escapeHtml(record.COMPONENT || "No component recorded")}</p>
        </div>
        <span class="badge badge--${severity || "default"}">${escapeHtml(record.SEVERITY || "—")}</span>
      </div>

      <dl class="record-fields">
        ${rows
          .map(
            ([label, value]) => `
          <div class="record-fields__row">
            <dt>${label}</dt>
            <dd class="${value ? "" : "is-empty"}">${escapeHtml(value || "—")}</dd>
          </div>`
          )
          .join("")}
      </dl>

      <p class="record-detail__section-title">Finding</p>
      <p>${escapeHtml(record.FINDING || "Not recorded")}</p>

      <p class="record-detail__section-title">Recommended action</p>
      <p>${escapeHtml(record.RECOMMENDED_ACTION || "Not recorded")}</p>

      ${turns ? `<p class="record-detail__section-title">Transcript</p>
                 <div class="record-detail__conversation">${turns}</div>` : ""}

      <button class="btn btn--primary btn--full" id="btn-detail-report">Download PDF report</button>
    `;

    document
      .getElementById("btn-detail-report")
      .addEventListener("click", () => window.open(API.report(record.RECORD_ID), "_blank"));
  }

  // ------------------------------------------------------------
  // Utilities
  // ------------------------------------------------------------

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return escapeHtml(value);
    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  // ------------------------------------------------------------
  // Wiring
  // ------------------------------------------------------------

  els.formLogin.addEventListener("submit", handleLogin);
  els.btnSignOut.addEventListener("click", handleSignOut);

  els.btnStartSession.addEventListener("click", startSession);
  els.btnEndSession.addEventListener("click", endSession);
  els.micBtn.addEventListener("click", toggleRecording);
  els.formTextFallback.addEventListener("submit", sendText);
  els.btnDownloadReport.addEventListener("click", downloadReport);
  els.toggleSpeak.addEventListener("change", () => {
    if (!els.toggleSpeak.checked) stopPlayback();
  });

  els.btnRefreshRecords.addEventListener("click", loadRecords);
  els.inputFilterAircraft.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadRecords();
  });

  restoreSession();
})();