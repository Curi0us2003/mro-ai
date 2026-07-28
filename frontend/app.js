/* ==============================================================
   AI Maintenance Voice Copilot - Frontend Logic
   --------------------------------------------------------------
   Talks to the Flask API defined in backend/app.py. All requests
   are same-origin relative paths, since Flask serves this file
   itself (see FRONTEND_FOLDER / static_folder in app.py).

   Two views:
     - Technician: start a session, speak or type findings,
       watch the structured record fill in, download the report.
     - Supervisor: browse all maintenance records, drill into one,
       read its transcript, download its report.
============================================================== */

(() => {
  "use strict";

  const API = {
    createSession: () => "/api/sessions",
    session: (id) => `/api/sessions/${id}`,
    sendVoice: (id) => `/api/sessions/${id}/voice`,
    sendMessage: (id) => `/api/sessions/${id}/message`,
    records: (params) => `/api/records${params ? `?${params}` : ""}`,
    record: (id) => `/api/records/${id}`,
    report: (id) => `/api/records/${id}/report`,
  };

  // ------------------------------------------------------------
  // State
  // ------------------------------------------------------------

  const state = {
    sessionId: null,
    technician: null,
    recordId: null,
    isRecording: false,
    mediaRecorder: null,
    audioChunks: [],
  };

  // ------------------------------------------------------------
  // Element refs
  // ------------------------------------------------------------

  const els = {
    tabTechnician: document.getElementById("tab-technician"),
    tabSupervisor: document.getElementById("tab-supervisor"),
    viewTechnician: document.getElementById("view-technician"),
    viewSupervisor: document.getElementById("view-supervisor"),

    panelStart: document.getElementById("panel-start"),
    inputTechnicianName: document.getElementById("input-technician-name"),
    btnStartSession: document.getElementById("btn-start-session"),
    startSessionError: document.getElementById("start-session-error"),

    workspace: document.getElementById("workspace"),
    sessionTechnicianName: document.getElementById("session-technician-name"),
    btnEndSession: document.getElementById("btn-end-session"),

    micBtn: document.getElementById("btn-mic"),
    voiceStatus: document.getElementById("voice-status"),
    replyAudio: document.getElementById("reply-audio"),

    formTextFallback: document.getElementById("form-text-fallback"),
    inputTextMessage: document.getElementById("input-text-message"),

    transcript: document.getElementById("transcript"),
    recordStatusPill: document.getElementById("record-status-pill"),
    recordFields: document.getElementById("record-fields"),
    btnDownloadReport: document.getElementById("btn-download-report"),

    inputFilterAircraft: document.getElementById("input-filter-aircraft"),
    btnRefreshRecords: document.getElementById("btn-refresh-records"),
    recordsTableBody: document.getElementById("records-table-body"),
    recordDetail: document.getElementById("record-detail"),
  };

  // ------------------------------------------------------------
  // View switching
  // ------------------------------------------------------------

  function showView(name) {
    const isTechnician = name === "technician";
    els.viewTechnician.classList.toggle("is-active", isTechnician);
    els.viewSupervisor.classList.toggle("is-active", !isTechnician);
    els.tabTechnician.classList.toggle("is-active", isTechnician);
    els.tabSupervisor.classList.toggle("is-active", !isTechnician);
    els.tabTechnician.setAttribute("aria-selected", String(isTechnician));
    els.tabSupervisor.setAttribute("aria-selected", String(!isTechnician));

    if (!isTechnician) {
      loadRecords();
    }
  }

  els.tabTechnician.addEventListener("click", () => showView("technician"));
  els.tabSupervisor.addEventListener("click", () => showView("supervisor"));

  // ------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------

  function severityClass(prefix, severity) {
    const key = (severity || "").trim().toLowerCase();
    if (["minor", "major", "critical", "aog"].includes(key)) {
      return `${prefix}--${key}`;
    }
    return `${prefix}--default`;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  async function requestJSON(url, options) {
    const response = await fetch(url, options);
    let body = null;
    try {
      body = await response.json();
    } catch (_err) {
      body = null;
    }
    if (!response.ok) {
      const message = (body && body.error) || `Request failed (${response.status})`;
      throw new Error(message);
    }
    return body;
  }

  // ------------------------------------------------------------
  // Technician: session lifecycle
  // ------------------------------------------------------------

  els.btnStartSession.addEventListener("click", async () => {
    const technician = els.inputTechnicianName.value.trim();
    els.startSessionError.textContent = "";

    if (!technician) {
      els.startSessionError.textContent = "Please enter your name to start.";
      return;
    }

    els.btnStartSession.disabled = true;
    try {
      const data = await requestJSON(API.createSession(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ technician }),
      });

      state.sessionId = data.session_id;
      state.technician = data.technician;
      state.recordId = null;

      els.sessionTechnicianName.textContent = state.technician;
      els.panelStart.hidden = true;
      els.workspace.hidden = false;
      resetTranscript();
      resetRecordCard();
    } catch (err) {
      els.startSessionError.textContent = err.message;
    } finally {
      els.btnStartSession.disabled = false;
    }
  });

  els.btnEndSession.addEventListener("click", async () => {
    if (!state.sessionId) return;
    try {
      await fetch(API.session(state.sessionId), { method: "DELETE" });
    } catch (_err) {
      /* best-effort */
    }
    state.sessionId = null;
    state.technician = null;
    state.recordId = null;
    els.workspace.hidden = true;
    els.panelStart.hidden = false;
    els.inputTechnicianName.value = "";
  });

  // ------------------------------------------------------------
  // Technician: transcript rendering
  // ------------------------------------------------------------

  function resetTranscript() {
    els.transcript.innerHTML = '<p class="transcript__empty">Your conversation with the copilot will appear here.</p>';
  }

  function appendTurn(role, text) {
    const empty = els.transcript.querySelector(".transcript__empty");
    if (empty) empty.remove();

    const bubble = document.createElement("div");
    bubble.className = `transcript-turn transcript-turn--${role}`;

    const label = document.createElement("span");
    label.className = "transcript-turn__label";
    label.textContent = role === "technician" ? "You" : "AI Copilot";

    const body = document.createElement("span");
    body.textContent = text;

    bubble.appendChild(label);
    bubble.appendChild(body);
    els.transcript.appendChild(bubble);
    els.transcript.scrollTop = els.transcript.scrollHeight;
  }

  // ------------------------------------------------------------
  // Technician: record card rendering
  // ------------------------------------------------------------

  function resetRecordCard() {
    els.recordStatusPill.textContent = "Not started";
    els.recordStatusPill.className = "status-pill";
    els.btnDownloadReport.disabled = true;
    els.recordFields.querySelectorAll("dd").forEach((dd) => {
      dd.textContent = "—";
      dd.className = "";
    });
  }

  function renderRecord(record, isComplete) {
    if (!record) {
      resetRecordCard();
      return;
    }

    els.recordStatusPill.textContent = isComplete ? "Complete" : "In progress";
    els.recordStatusPill.className = `status-pill ${isComplete ? "status-pill--complete" : "status-pill--open"}`;

    els.recordFields.querySelectorAll("dd").forEach((dd) => {
      const field = dd.dataset.field;
      const value = record[field];
      dd.textContent = value || "Not recorded yet";
      dd.className = value ? "" : "is-empty";
      if (field === "SEVERITY" && value) {
        dd.className = severityClass("severity", value);
      }
    });

    els.btnDownloadReport.disabled = !isComplete;
  }

  async function refreshSessionStatus() {
    if (!state.sessionId) return;
    try {
      const data = await requestJSON(API.session(state.sessionId));
      state.recordId = data.record_id;
      renderRecord(data.record, data.record_complete);
    } catch (err) {
      console.error("Failed to refresh session status:", err);
    }
  }

  els.btnDownloadReport.addEventListener("click", () => {
    if (!state.recordId) return;
    window.open(API.report(state.recordId), "_blank");
  });

  // ------------------------------------------------------------
  // Technician: text fallback
  // ------------------------------------------------------------

  els.formTextFallback.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = els.inputTextMessage.value.trim();
    if (!text || !state.sessionId) return;

    els.inputTextMessage.value = "";
    appendTurn("technician", text);
    setVoiceStatus("Thinking…");

    try {
      const data = await requestJSON(API.sendMessage(state.sessionId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      appendTurn("assistant", data.reply);
      setVoiceStatus("Tap to speak a finding");
      await refreshSessionStatus();
    } catch (err) {
      appendTurn("assistant", `Something went wrong: ${err.message}`);
      setVoiceStatus("Tap to speak a finding");
    }
  });

  // ------------------------------------------------------------
  // Technician: voice recording
  // ------------------------------------------------------------

  function setVoiceStatus(text) {
    els.voiceStatus.textContent = text;
  }

  function setMicState(mode) {
    els.micBtn.classList.remove("is-recording", "is-busy");
    if (mode === "recording") els.micBtn.classList.add("is-recording");
    if (mode === "busy") els.micBtn.classList.add("is-busy");
    els.micBtn.disabled = mode === "busy";
  }

  els.micBtn.addEventListener("click", async () => {
    if (!state.sessionId) return;

    if (state.isRecording) {
      stopRecording();
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);

      state.mediaRecorder = recorder;
      state.audioChunks = [];

      recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size > 0) state.audioChunks.push(event.data);
      });

      recorder.addEventListener("stop", () => {
        stream.getTracks().forEach((track) => track.stop());
        handleRecordingComplete();
      });

      recorder.start();
      state.isRecording = true;
      setMicState("recording");
      setVoiceStatus("Listening… tap again to stop");
    } catch (err) {
      setVoiceStatus("Microphone access was denied or unavailable.");
      console.error(err);
    }
  });

  function stopRecording() {
    if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
      state.mediaRecorder.stop();
    }
    state.isRecording = false;
  }

  async function handleRecordingComplete() {
    setMicState("busy");
    setVoiceStatus("Transcribing and thinking…");

    const blob = new Blob(state.audioChunks, { type: state.audioChunks[0]?.type || "audio/webm" });
    const formData = new FormData();
    formData.append("audio", blob, "recording.webm");

    try {
      const data = await requestJSON(API.sendVoice(state.sessionId), {
        method: "POST",
        body: formData,
      });

      appendTurn("technician", data.transcript);
      appendTurn("assistant", data.reply);

      if (data.reply_audio_url) {
        els.replyAudio.src = data.reply_audio_url;
        els.replyAudio.hidden = false;
        els.replyAudio.play().catch(() => {
          /* autoplay may be blocked - user can press play manually */
        });
      }

      await refreshSessionStatus();
      setVoiceStatus("Tap to speak a finding");
    } catch (err) {
      appendTurn("assistant", `Something went wrong: ${err.message}`);
      setVoiceStatus("Tap to speak a finding");
    } finally {
      setMicState("idle");
    }
  }

  // ------------------------------------------------------------
  // Supervisor: records table
  // ------------------------------------------------------------

  async function loadRecords() {
    const aircraftReg = els.inputFilterAircraft.value.trim();
    const params = aircraftReg ? `aircraft_reg=${encodeURIComponent(aircraftReg)}` : "";

    els.recordsTableBody.innerHTML = '<tr><td colspan="6" class="records-table__empty">Loading records…</td></tr>';

    try {
      const data = await requestJSON(API.records(params));
      renderRecordsTable(data.records || []);
    } catch (err) {
      els.recordsTableBody.innerHTML = `<tr><td colspan="6" class="records-table__empty">Failed to load records: ${err.message}</td></tr>`;
    }
  }

  function renderRecordsTable(records) {
    if (!records.length) {
      els.recordsTableBody.innerHTML = '<tr><td colspan="6" class="records-table__empty">No maintenance records yet.</td></tr>';
      return;
    }

    els.recordsTableBody.innerHTML = "";

    records.forEach((record) => {
      const row = document.createElement("tr");
      row.dataset.recordId = record.RECORD_ID;

      row.innerHTML = `
        <td>${escapeHTML(record.AIRCRAFT_REG || "—")}</td>
        <td>${escapeHTML(record.COMPONENT || "—")}</td>
        <td><span class="badge ${severityClass("badge", record.SEVERITY)}">${escapeHTML(record.SEVERITY || "—")}</span></td>
        <td>${escapeHTML(record.STATUS || "—")}</td>
        <td>${escapeHTML(record.TECHNICIAN || "—")}</td>
        <td>${formatDate(record.CREATED_AT)}</td>
      `;

      row.addEventListener("click", () => selectRecord(record.RECORD_ID, row));
      els.recordsTableBody.appendChild(row);
    });
  }

  function escapeHTML(value) {
    const div = document.createElement("div");
    div.textContent = value;
    return div.innerHTML;
  }

  async function selectRecord(recordId, rowEl) {
    els.recordsTableBody.querySelectorAll("tr").forEach((tr) => tr.classList.remove("is-selected"));
    if (rowEl) rowEl.classList.add("is-selected");

    els.recordDetail.innerHTML = '<p class="record-detail__placeholder">Loading…</p>';

    try {
      const data = await requestJSON(API.record(recordId));
      renderRecordDetail(data.record, data.conversation || []);
    } catch (err) {
      els.recordDetail.innerHTML = `<p class="record-detail__placeholder">Failed to load record: ${err.message}</p>`;
    }
  }

  function renderRecordDetail(record, conversation) {
    const severityBadge = `<span class="badge ${severityClass("badge", record.SEVERITY)}">${escapeHTML(record.SEVERITY || "—")}</span>`;

    let html = `
      <div class="record-detail__header">
        <div>
          <p class="record-detail__title">${escapeHTML(record.AIRCRAFT_REG || "Unknown aircraft")} — ${escapeHTML(record.COMPONENT || "Unknown component")}</p>
          <p class="record-detail__subtitle">Reported by ${escapeHTML(record.TECHNICIAN || "—")} · ${formatDate(record.CREATED_AT)}</p>
        </div>
        ${severityBadge}
      </div>

      <p class="record-detail__section-title">Finding</p>
      <p>${escapeHTML(record.FINDING || "Not recorded")}</p>

      <p class="record-detail__section-title">Location</p>
      <p>${escapeHTML(record.LOCATION || "Not recorded")}</p>

      <p class="record-detail__section-title">Recommended action</p>
      <p>${escapeHTML(record.RECOMMENDED_ACTION || "Not recorded")}</p>

      <button class="btn btn--primary btn--full" id="btn-detail-download-report">Download PDF report</button>

      <p class="record-detail__section-title">Conversation transcript</p>
      <div class="record-detail__conversation">
        ${
          conversation.length
            ? conversation
                .map(
                  (turn) => `
              <div class="transcript-turn transcript-turn--${turn.ROLE === "technician" ? "technician" : "assistant"}">
                <span class="transcript-turn__label">${escapeHTML(turn.ROLE)}</span>
                <span>${escapeHTML(turn.MESSAGE)}</span>
              </div>`
                )
                .join("")
            : '<p class="transcript__empty">No conversation recorded.</p>'
        }
      </div>
    `;

    els.recordDetail.innerHTML = html;

    document.getElementById("btn-detail-download-report").addEventListener("click", () => {
      window.open(API.report(record.RECORD_ID), "_blank");
    });
  }

  els.btnRefreshRecords.addEventListener("click", loadRecords);
  els.inputFilterAircraft.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadRecords();
  });

  // ------------------------------------------------------------
  // Init
  // ------------------------------------------------------------

  showView("technician");
})();
