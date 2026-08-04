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
    sendVoiceStream: (id) => `/api/sessions/${id}/voice/stream`,
    sendMessage: (id) => `/api/sessions/${id}/message`,
    sendMessageStream: (id) => `/api/sessions/${id}/message/stream`,
    speak: (id) => `/api/sessions/${id}/speak`,
    records: (params) => `/api/records${params ? `?${params}` : ""}`,
    record: (id) => `/api/records/${id}`,
    report: (id) => `/api/records/${id}/report`,
    filters: () => "/api/records/filters",
    recordPhotos: (id) => `/api/records/${id}/photos`,
    sessionPhotos: (id) => `/api/sessions/${id}/photos`,
    photo: (id) => `/api/photos/${id}`,
    assistantChat: () => "/api/assistant/chat",
    assistantReset: () => "/api/assistant/reset",
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

    // Technician: damage photos on the finding being recorded.
    photos: [],
    // False until the RECORD_PHOTOS table exists, which hides the feature.
    photosEnabled: false,

    // Supervisor: the loaded page of records, and which one is expanded.
    records: [],
    openRecordId: null,
    assistantBusy: false,
  };

  // ------------------------------------------------------------
  // Element refs
  // ------------------------------------------------------------

  const els = {
    // shell
    brandLogo: document.getElementById("brand-logo"),
    landingImage: document.getElementById("landing-image"),
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
    startHeroName: document.getElementById("start-hero-name"),
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
    btnJumpLatest: document.getElementById("btn-jump-latest"),
    replyAudio: document.getElementById("reply-audio"),
    toggleSpeak: document.getElementById("toggle-speak"),
    recordStatusPill: document.getElementById("record-status-pill"),
    recordFields: document.getElementById("record-fields"),
    recordProgressFill: document.getElementById("record-progress-fill"),
    recordProgressText: document.getElementById("record-progress-text"),
    btnDownloadReport: document.getElementById("btn-download-report"),

    // technician: damage photos
    cardPhotos: document.getElementById("card-photos"),
    photoCountPill: document.getElementById("photo-count-pill"),
    photoHint: document.getElementById("photo-hint"),
    btnTakePhoto: document.getElementById("btn-take-photo"),
    btnUploadPhoto: document.getElementById("btn-upload-photo"),
    inputPhotoCamera: document.getElementById("input-photo-camera"),
    inputPhotoFile: document.getElementById("input-photo-file"),
    photoGrid: document.getElementById("photo-grid"),
    photoError: document.getElementById("photo-error"),

    // supervisor
    btnRefreshRecords: document.getElementById("btn-refresh-records"),
    recordsList: document.getElementById("records-list"),
    recordsCount: document.getElementById("records-count"),
    inputFilterSearch: document.getElementById("input-filter-search"),
    btnClearFilters: document.getElementById("btn-clear-filters"),
    filterSelects: Array.from(document.querySelectorAll("[data-filter]")),

    // supervisor assistant
    chatbot: document.getElementById("chatbot"),
    chatbotPanel: document.getElementById("chatbot-panel"),
    chatbotMessages: document.getElementById("chatbot-messages"),
    chatbotContext: document.getElementById("chatbot-context"),
    btnChatbotToggle: document.getElementById("btn-chatbot-toggle"),
    btnChatbotClose: document.getElementById("btn-chatbot-close"),
    btnChatbotReset: document.getElementById("btn-chatbot-reset"),
    formChatbot: document.getElementById("form-chatbot"),
    inputChatbot: document.getElementById("input-chatbot"),
    btnChatbotSend: document.getElementById("btn-chatbot-send"),
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
  // Branding assets
  // --------------------------------------------------------
  // logo.png (shell bar) and image.png (landing panel) are dropped
  // into frontend/ by hand and are not in the repo. Both start
  // hidden and are only revealed once the browser confirms they
  // actually decoded - so a missing file leaves the built-in mark
  // and the gradient in place rather than a broken-image icon.
  // ------------------------------------------------------------

  function revealWhenLoaded(img, onLoad) {
    if (!img) return;

    const show = () => {
      // A file that exists but isn't a usable image still fires load.
      if (!img.naturalWidth) return;
      img.hidden = false;
      if (onLoad) onLoad();
    };

    if (img.complete) {
      show();
      return;
    }

    img.addEventListener("load", show);
    img.addEventListener("error", () => { img.hidden = true; });
  }

  revealWhenLoaded(els.brandLogo, () => {
    els.brandLogo.parentElement.classList.add("has-logo");
  });
  revealWhenLoaded(els.landingImage);

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

    // Blank the identity as well as hiding it. Hiding alone would leave
    // the previous user's name in the DOM, ready to flash on the next
    // sign-in before the new one is fetched.
    els.shellUser.hidden = true;
    els.shellUserName.textContent = "—";
    els.shellUserRole.textContent = "—";

    // Nothing from the last session should outlive it.
    stopPlayback();
    els.panelStart.hidden = false;
    els.workspace.hidden = true;

    // The assistant belongs to a signed-in supervisor, and its history
    // is the previous user's - close and clear it.
    closeChatbot();
    els.chatbot.hidden = true;
    els.chatbotMessages.innerHTML = "";

    state.photos = [];
    state.records = [];
    state.openRecordId = null;

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
      // The assistant is a supervisor tool; technicians have the voice
      // copilot in their workspace instead.
      els.chatbot.hidden = false;
      loadFilterOptions().then(loadRecords);
    } else {
      // First name only - the start page greets them directly.
      els.startHeroName.textContent =
        (user.full_name || "technician").trim().split(/\s+/)[0];
      showView(els.viewTechnician);
      els.panelStart.hidden = false;
      els.workspace.hidden = true;

      // Whether photo attachments are available at all depends on the
      // RECORD_PHOTOS table existing; this endpoint reports it.
      loadFilterOptions();
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
      state.photos = [];
      refreshPhotoCard();
      setVoiceStatus(IDLE_STATUS);
      els.inputTextMessage.focus();
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

  // ------------------------------------------------------------
  // Auto-scroll
  // --------------------------------------------------------
  // The transcript follows the newest turn on its own, but only
  // while the technician is already at the bottom. If they've
  // scrolled up to re-read an earlier torque figure, a reply
  // streaming in must not yank the view away from them - so the
  // pin releases on scroll-up and a "jump to latest" button
  // appears instead. Anything else is actively hostile in a panel
  // that updates token by token.
  // ------------------------------------------------------------

  const SCROLL_PIN_SLACK_PX = 48;
  let pinnedToBottom = true;

  function isAtBottom() {
    const el = els.transcript;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_PIN_SLACK_PX;
  }

  function scrollToLatest(smooth = true) {
    pinnedToBottom = true;
    els.btnJumpLatest.hidden = true;
    els.transcript.scrollTo({
      top: els.transcript.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });
  }

  function keepPinned() {
    if (!pinnedToBottom) return;
    // Jump without smoothing: during streaming this runs on every
    // token, and animating each one fights itself into a stutter.
    els.transcript.scrollTop = els.transcript.scrollHeight;
  }

  els.transcript.addEventListener("scroll", () => {
    pinnedToBottom = isAtBottom();
    els.btnJumpLatest.hidden = pinnedToBottom || !hasTurns();
  });

  els.btnJumpLatest.addEventListener("click", () => scrollToLatest());

  // ------------------------------------------------------------
  // Turns
  // ------------------------------------------------------------

  function hasTurns() {
    return els.transcript.querySelector(".transcript-turn") !== null;
  }

  function clearEmptyState() {
    const empty = els.transcript.querySelector(".chat__empty");
    if (empty) empty.remove();
  }

  /**
   * Append a turn and return its body element, so a streaming reply
   * can keep rewriting the same node as tokens arrive.
   */
  function appendTurn(role, text) {
    clearEmptyState();
    hideTyping();

    const turn = document.createElement("div");
    turn.className = `transcript-turn transcript-turn--${role}`;

    const label = document.createElement("span");
    label.className = "transcript-turn__label";
    label.textContent =
      role === "technician" ? "You" : role === "error" ? "Problem" : "Copilot";

    const body = document.createElement("span");
    body.className = "transcript-turn__body";
    body.textContent = text;

    const speaking = document.createElement("span");
    speaking.className = "transcript-turn__speaking";
    speaking.textContent = "🔊";
    speaking.setAttribute("aria-hidden", "true");

    label.appendChild(speaking);
    turn.append(label, body);
    els.transcript.appendChild(turn);
    keepPinned();
    return body;
  }

  function clearTranscript() {
    els.transcript.innerHTML = `
      <div class="chat__empty" id="chat-empty">
        <span class="chat__empty-icon" aria-hidden="true">🎙</span>
        <p class="chat__empty-title">Nothing logged yet</p>
        <p class="chat__empty-text">
          Tap the mic and describe what you're looking at — for example
          “Found corrosion on the left main gear trunnion of VT-ABC.”
        </p>
      </div>`;
    els.btnJumpLatest.hidden = true;
    pinnedToBottom = true;
  }

  // --- Typing indicator: shown while waiting for the first token ---

  let typingEl = null;

  function showTyping() {
    if (typingEl) return;
    clearEmptyState();
    typingEl = document.createElement("div");
    typingEl.className = "typing";
    typingEl.innerHTML = "<span></span><span></span><span></span>";
    els.transcript.appendChild(typingEl);
    keepPinned();
  }

  function hideTyping() {
    if (!typingEl) return;
    typingEl.remove();
    typingEl = null;
  }

  // Sentences arrive one at a time as the reply streams in, each as
  // its own short WAV clip. They're queued and played back to back
  // so the audio keeps pace with the text instead of waiting for the
  // whole reply to finish before anything is heard.
  const audioQueue = [];
  let audioPlaying = false;

  function base64ToBlob(base64, mimeType) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes], { type: mimeType });
  }

  // The turn currently being read aloud, so it can carry a speaker mark.
  let speakingTurn = null;

  function markSpeaking(turnBody) {
    const turn = turnBody ? turnBody.closest(".transcript-turn") : null;
    if (speakingTurn === turn) return;
    if (speakingTurn) speakingTurn.classList.remove("is-speaking");
    speakingTurn = turn;
    if (turn) turn.classList.add("is-speaking");
  }

  function clearSpeaking() {
    if (speakingTurn) speakingTurn.classList.remove("is-speaking");
    speakingTurn = null;
  }

  function enqueueAudio(base64) {
    if (!base64 || !els.toggleSpeak.checked) return;
    const url = URL.createObjectURL(base64ToBlob(base64, "audio/wav"));
    audioQueue.push(url);
    if (!audioPlaying) playNextAudio();
  }

  function playNextAudio() {
    const url = audioQueue.shift();
    if (!url) {
      audioPlaying = false;
      clearSpeaking();
      return;
    }
    audioPlaying = true;
    els.replyAudio.src = url;
    els.replyAudio.play().catch(() => {
      // Autoplay can be blocked until the user interacts with the
      // page. Tapping the mic counts, so this rarely fires twice.
      setVoiceStatus("Tap the mic once to enable audio replies");
    });
  }

  els.replyAudio.addEventListener("ended", () => {
    URL.revokeObjectURL(els.replyAudio.src);
    playNextAudio();
  });

  function stopPlayback() {
    audioQueue.splice(0, audioQueue.length).forEach((url) => URL.revokeObjectURL(url));
    audioPlaying = false;
    els.replyAudio.pause();
    els.replyAudio.removeAttribute("src");
    clearSpeaking();
  }

  // ------------------------------------------------------------
  // Technician: streaming replies (NDJSON: text deltas + per-sentence audio)
  // ------------------------------------------------------------

  async function postStream(url, options) {
    const response = await fetch(url, { credentials: "same-origin", ...options });

    if (response.status === 401) {
      showLogin("Your session expired. Sign in again.");
      throw new ApiError("Not signed in", 401);
    }

    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      const type = response.headers.get("content-type") || "";
      if (type.includes("application/json")) {
        const payload = await response.json().catch(() => null);
        if (payload && payload.error) message = payload.error;
      }
      throw new ApiError(message, response.status);
    }

    return response.body;
  }

  async function* readNdjson(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newlineIndex;
      while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newlineIndex).trim();
        buffer = buffer.slice(newlineIndex + 1);
        if (line) yield JSON.parse(line);
      }
    }

    const rest = buffer.trim();
    if (rest) yield JSON.parse(rest);
  }

  async function streamAgentReply(url, fetchOptions) {
    const body = await postStream(url, fetchOptions);

    let assistantBody = null;
    let assistantTurn = null;
    let fullText = "";

    /** Create the reply bubble on first use, mid-stream. */
    function ensureAssistantTurn() {
      if (assistantBody) return assistantBody;
      assistantBody = appendTurn("assistant", "");
      assistantTurn = assistantBody.closest(".transcript-turn");
      // The caret trails the text while more is still coming.
      assistantTurn.classList.add("is-streaming");
      return assistantBody;
    }

    try {
      for await (const event of readNdjson(body)) {
        switch (event.type) {
          case "transcript":
            appendTurn("technician", event.text);
            setBusy(true, "Thinking…");
            showTyping();
            break;

          case "text":
            fullText += event.delta;
            ensureAssistantTurn().textContent = fullText;
            keepPinned();
            break;

          case "audio":
            // Audio for a sentence arrives while later sentences are
            // still being generated - flag whichever reply it belongs to.
            markSpeaking(assistantBody);
            enqueueAudio(event.audio_base64);
            break;

          case "audio_unavailable":
            break; // no voice model installed yet - text-only is fine

          case "done":
            ensureAssistantTurn().textContent = event.reply;
            applyRecordState(event);
            break;

          default:
            break;
        }
      }
    } finally {
      hideTyping();
      if (assistantTurn) assistantTurn.classList.remove("is-streaming");
      keepPinned();
    }
  }

  async function sendText(event) {
    event.preventDefault();

    const text = els.inputTextMessage.value.trim();
    if (!text || !state.sessionId || state.isBusy) return;

    els.inputTextMessage.value = "";
    appendTurn("technician", text);
    setBusy(true, "Thinking…");
    showTyping();

    try {
      await streamAgentReply(API.sendMessageStream(state.sessionId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
    } catch (err) {
      appendTurn("error", `Couldn't send that: ${err.message}`);
    } finally {
      setBusy(false, IDLE_STATUS);
    }
  }

  async function sendVoice(blob) {
    if (!state.sessionId) return;

    const form = new FormData();
    form.append("audio", blob, "recording.webm");

    setBusy(true, "Transcribing…");
    showTyping();

    try {
      // The technician's own turn is appended from the "transcript"
      // event in the stream, not here - it's not known until the
      // recording has actually been transcribed server-side.
      await streamAgentReply(API.sendVoiceStream(state.sessionId), {
        method: "POST",
        body: form,
      });
    } catch (err) {
      appendTurn("error", `Couldn't process that recording: ${err.message}`);
    } finally {
      setBusy(false, IDLE_STATUS);
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
      setVoiceStatus("Listening — tap again when you're done", "listening");
    } catch (_err) {
      setVoiceStatus("Microphone access was blocked — type instead");
    }
  }

  const IDLE_STATUS = "Tap to speak a finding";

  function setVoiceStatus(text, tone) {
    els.voiceStatus.textContent = text;
    els.voiceStatus.classList.toggle("is-listening", tone === "listening");
    els.voiceStatus.classList.toggle("is-working", tone === "working");
  }

  function setBusy(busy, status) {
    state.isBusy = busy;
    els.micBtn.classList.toggle("is-busy", busy);
    els.micBtn.disabled = busy;
    if (status) setVoiceStatus(status, busy ? "working" : undefined);
  }

  // ------------------------------------------------------------
  // Technician: record card
  // ------------------------------------------------------------

  // The seven fields the backend counts as a complete record
  // (backend/agent.py REQUIRED_FIELDS), in the order they're shown.
  const REQUIRED_FIELDS = [
    "AIRCRAFT_REG",
    "COMPONENT",
    "FINDING",
    "SEVERITY",
    "LOCATION",
    "RECOMMENDED_ACTION",
    "TECHNICIAN",
  ];

  function resetRecordCard() {
    els.recordFields.querySelectorAll("dd").forEach((cell) => {
      cell.textContent = "—";
      cell.className = "is-empty";
    });
    els.recordStatusPill.textContent = "Not started";
    els.recordStatusPill.className = "status-pill";
    els.btnDownloadReport.disabled = true;
    setRecordProgress(0);
  }

  function setRecordProgress(filledCount) {
    const total = REQUIRED_FIELDS.length;
    const pct = Math.round((filledCount / total) * 100);
    els.recordProgressFill.style.width = `${pct}%`;
    els.recordProgressFill.classList.toggle("is-complete", filledCount >= total);
    els.recordProgressText.textContent = `${filledCount} of ${total} captured`;
  }

  /**
   * Update the finding card from a turn's "done" payload.
   *
   * The record now travels inside that payload, so this no longer
   * fires a follow-up GET /api/sessions/<id>. That request used to
   * cost two more HANA round trips per turn (once to read the row,
   * once more to judge completeness) purely to redraw this card.
   */
  function applyRecordState(turnData) {
    const hadRecord = Boolean(state.recordId);
    state.recordId = turnData.record_id || state.recordId;

    if (!state.recordId) {
      resetRecordCard();
      return;
    }

    // The agent creates the record on the first thing the technician
    // says; that is the moment photos become attachable.
    if (!hadRecord) refreshPhotoCard();

    els.recordStatusPill.textContent = turnData.record_complete ? "Complete" : "In progress";
    els.recordStatusPill.className =
      "status-pill " + (turnData.record_complete ? "status-pill--complete" : "status-pill--open");
    els.btnDownloadReport.disabled = false;

    if (turnData.record) fillRecordFields(turnData.record);
  }

  function fillRecordFields(record) {
    let filled = 0;

    els.recordFields.querySelectorAll("dd").forEach((cell) => {
      const field = cell.dataset.field;
      const value = record[field];
      const previous = cell.textContent;

      if (value) {
        if (REQUIRED_FIELDS.includes(field)) filled += 1;
        cell.textContent = value;
        cell.className =
          field === "SEVERITY" ? `severity--${String(value).toLowerCase()}` : "";

        // Flash whatever the last utterance actually filled in, so the
        // technician can see the record growing as they talk.
        if (previous !== value) {
          const row = cell.parentElement;
          row.classList.remove("just-filled");
          // Reading offsetWidth restarts the animation on a re-fill.
          void row.offsetWidth;
          row.classList.add("just-filled");
        }
      } else {
        cell.textContent = "—";
        cell.className = "is-empty";
      }
    });

    setRecordProgress(filled);
  }

  function downloadReport() {
    if (!state.recordId) return;
    window.open(API.report(state.recordId), "_blank");
  }

  // ------------------------------------------------------------
  // Technician: damage photos (optional)
  // --------------------------------------------------------
  // Two entry points onto one <input type="file">: the camera button
  // carries capture="environment", which makes a phone or tablet open
  // the rear camera instead of the gallery. On a laptop both open a
  // file picker, which is the sensible fallback - no getUserMedia
  // plumbing, no separate preview/shutter UI to maintain, and it
  // inherits the platform's own camera app.
  //
  // A photo hangs off the maintenance record, which the agent only
  // creates once the technician has described something. Until then
  // the buttons explain that rather than failing on upload.
  // ------------------------------------------------------------

  function refreshPhotoCard() {
    if (!state.photosEnabled) {
      els.cardPhotos.hidden = true;
      return;
    }

    els.cardPhotos.hidden = false;

    const count = state.photos.length;
    const ready = Boolean(state.recordId);

    els.btnTakePhoto.disabled = !ready;
    els.btnUploadPhoto.disabled = !ready;

    els.photoCountPill.textContent = count
      ? `${count} attached`
      : "Optional";
    els.photoCountPill.className =
      "status-pill" + (count ? " status-pill--complete" : "");

    els.photoHint.textContent = ready
      ? "Attached photos go into the PDF report and are visible to your supervisor."
      : "Describe the finding first — then you can attach a photo of it.";

    renderPhotoGrid();
  }

  function renderPhotoGrid() {
    els.photoGrid.innerHTML = "";

    state.photos.forEach((photo) => {
      const thumb = document.createElement("div");
      thumb.className = "photo-thumb";

      const img = document.createElement("img");
      img.src = photo.url;
      img.alt = photo.caption || "Damage photo";
      img.loading = "lazy";
      img.addEventListener("click", () => openPhotoViewer(photo.url, photo.caption));

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "photo-thumb__remove";
      remove.textContent = "✕";
      remove.title = "Remove this photo";
      remove.setAttribute("aria-label", "Remove this photo");
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        deletePhoto(photo.photo_id);
      });

      thumb.append(img, remove);
      els.photoGrid.appendChild(thumb);
    });
  }

  /** A placeholder tile while the upload is in flight. */
  function addUploadingTile() {
    const thumb = document.createElement("div");
    thumb.className = "photo-thumb is-uploading";
    thumb.innerHTML = '<span class="photo-thumb__spinner">…</span>';
    els.photoGrid.appendChild(thumb);
    return thumb;
  }

  async function uploadPhotos(files) {
    if (!state.sessionId || !state.recordId || !files.length) return;

    els.photoError.textContent = "";

    for (const file of files) {
      const tile = addUploadingTile();
      const form = new FormData();
      form.append("photo", file, file.name || "damage.jpg");

      try {
        const data = await api(API.sessionPhotos(state.sessionId), {
          method: "POST",
          body: form,
        });
        state.photos.push(data.photo);
      } catch (err) {
        els.photoError.textContent = err.message;
      } finally {
        tile.remove();
      }
    }

    refreshPhotoCard();
  }

  async function deletePhoto(photoId) {
    try {
      await api(API.photo(photoId), { method: "DELETE" });
      state.photos = state.photos.filter((p) => p.photo_id !== photoId);
      refreshPhotoCard();
    } catch (err) {
      els.photoError.textContent = err.message;
    }
  }

  async function loadSessionPhotos() {
    if (!state.recordId || !state.photosEnabled) return;
    try {
      const data = await api(API.recordPhotos(state.recordId));
      state.photos = data.photos || [];
      refreshPhotoCard();
    } catch (_err) {
      /* the grid is a convenience; the record is the source of truth */
    }
  }

  // ------------------------------------------------------------
  // Supervisor assistant (floating chatbot)
  // --------------------------------------------------------
  // Read-only by construction: the server exposes no mutating tool to
  // it (see backend/assistant.py), so it can discuss and look up but
  // never edit a finding.
  //
  // Whichever record is expanded in the list is sent as context with
  // each question, so "is this severity right?" resolves without the
  // supervisor restating which finding they mean.
  // ------------------------------------------------------------

  const CHATBOT_SUGGESTIONS_GENERAL = [
    "What are the common causes of hydraulic seepage at an actuator seal?",
    "How many findings are still open?",
    "What does the manual say about main landing gear inspection intervals?",
  ];

  const CHATBOT_SUGGESTIONS_RECORD = [
    "Is this severity reasonable for what was found?",
    "What does the manual say about this component?",
    "Is anything missing from this finding?",
    "Has this aircraft had related findings before?",
  ];

  function openChatbot() {
    els.chatbot.classList.add("is-open");
    els.chatbotPanel.hidden = false;
    els.btnChatbotToggle.setAttribute("aria-expanded", "true");
    updateAssistantContext();
    if (!els.chatbotMessages.querySelector(".chat-msg")) renderChatbotEmpty();
    els.inputChatbot.focus();
  }

  function closeChatbot() {
    els.chatbot.classList.remove("is-open");
    els.chatbotPanel.hidden = true;
    els.btnChatbotToggle.setAttribute("aria-expanded", "false");
  }

  function toggleChatbot() {
    if (els.chatbot.classList.contains("is-open")) closeChatbot();
    else openChatbot();
  }

  /** Reflect the open record in the header, and in the suggestions. */
  function updateAssistantContext() {
    const record = state.records.find((r) => r.RECORD_ID === state.openRecordId);

    if (record) {
      const bits = [record.AIRCRAFT_REG, record.COMPONENT].filter(Boolean);
      els.chatbotContext.textContent = `Discussing ${bits.join(" · ") || "this finding"}`;
      els.chatbotContext.classList.add("is-scoped");
      els.inputChatbot.placeholder = "Ask about this finding…";
    } else {
      els.chatbotContext.textContent = "Ask about any aircraft system";
      els.chatbotContext.classList.remove("is-scoped");
      els.inputChatbot.placeholder = "Ask about any system or finding…";
    }

    if (!els.chatbotMessages.querySelector(".chat-msg")) renderChatbotEmpty();
  }

  function renderChatbotEmpty() {
    const scoped = Boolean(state.openRecordId);
    const suggestions = scoped
      ? CHATBOT_SUGGESTIONS_RECORD
      : CHATBOT_SUGGESTIONS_GENERAL;

    els.chatbotMessages.innerHTML = `
      <div class="chatbot__empty">
        <strong>${scoped ? "Ask about the open finding" : "Ask me anything maintenance"}</strong>
        ${
          scoped
            ? "I can see the record you have expanded — its fields, its photos and its transcript."
            : "I answer from your ingested manuals and from the findings your technicians have logged."
        }
        <div class="chatbot__suggestions">
          ${suggestions
            .map(
              (text) =>
                `<button type="button" class="chatbot__suggestion">${escapeHtml(text)}</button>`
            )
            .join("")}
        </div>
      </div>
    `;

    els.chatbotMessages.querySelectorAll(".chatbot__suggestion").forEach((button) => {
      button.addEventListener("click", () => {
        els.inputChatbot.value = button.textContent;
        askAssistant();
      });
    });
  }

  function appendChatMessage(kind, text) {
    const empty = els.chatbotMessages.querySelector(".chatbot__empty");
    if (empty) empty.remove();

    const message = document.createElement("div");
    message.className = `chat-msg chat-msg--${kind}`;
    message.textContent = text;
    els.chatbotMessages.appendChild(message);
    els.chatbotMessages.scrollTop = els.chatbotMessages.scrollHeight;
    return message;
  }

  /** The "Searching the manuals…" pill shown during a tool call. */
  function showToolPill(toolName) {
    const labels = {
      search_maintenance_knowledge: "Searching the manuals…",
      search_maintenance_records: "Searching findings…",
      get_record_details: "Reading the record…",
    };

    const pill = document.createElement("div");
    pill.className = "chat-tool";
    pill.innerHTML = `<span class="chat-tool__dot"></span><span>${
      escapeHtml(labels[toolName] || "Looking that up…")
    }</span>`;
    els.chatbotMessages.appendChild(pill);
    els.chatbotMessages.scrollTop = els.chatbotMessages.scrollHeight;
    return pill;
  }

  /**
   * Render [manual.pdf, p.42] citations as inline chips.
   * Built from a text node so the reply itself is never treated as HTML.
   */
  function renderReplyWithCitations(element, text) {
    element.textContent = "";
    const pattern = /\[([^\]]+?,\s*p\.\s*\d+)\]/g;
    let cursor = 0;
    let match;

    while ((match = pattern.exec(text)) !== null) {
      if (match.index > cursor) {
        element.appendChild(document.createTextNode(text.slice(cursor, match.index)));
      }
      const chip = document.createElement("span");
      chip.className = "cite";
      chip.textContent = match[1];
      element.appendChild(chip);
      cursor = match.index + match[0].length;
    }

    if (cursor < text.length) {
      element.appendChild(document.createTextNode(text.slice(cursor)));
    }
  }

  async function askAssistant(event) {
    if (event) event.preventDefault();

    const question = els.inputChatbot.value.trim();
    if (!question || state.assistantBusy) return;

    els.inputChatbot.value = "";
    appendChatMessage("user", question);

    state.assistantBusy = true;
    els.btnChatbotSend.disabled = true;

    let bubble = null;
    let full = "";
    let toolPill = null;

    try {
      const body = await postStream(API.assistantChat(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          // Whatever is expanded in the list behind the panel.
          record_id: state.openRecordId || null,
        }),
      });

      for await (const chunk of readNdjson(body)) {
        switch (chunk.type) {
          case "tool":
            if (toolPill) toolPill.remove();
            toolPill = showToolPill(chunk.name);
            break;

          case "content":
            if (toolPill) { toolPill.remove(); toolPill = null; }
            if (!bubble) {
              bubble = appendChatMessage("bot", "");
              bubble.classList.add("is-streaming");
            }
            full += chunk.text;
            bubble.textContent = full;
            els.chatbotMessages.scrollTop = els.chatbotMessages.scrollHeight;
            break;

          case "done":
            if (toolPill) { toolPill.remove(); toolPill = null; }
            if (!bubble) bubble = appendChatMessage("bot", "");
            renderReplyWithCitations(bubble, chunk.reply || full);
            bubble.classList.remove("is-streaming");
            break;

          case "error":
            if (toolPill) { toolPill.remove(); toolPill = null; }
            if (bubble) bubble.classList.remove("is-streaming");
            appendChatMessage("error", chunk.error);
            break;

          default:
            break;
        }
      }
    } catch (err) {
      if (bubble) bubble.classList.remove("is-streaming");
      appendChatMessage("error", err.message);
    } finally {
      if (toolPill) toolPill.remove();
      state.assistantBusy = false;
      els.btnChatbotSend.disabled = false;
      els.chatbotMessages.scrollTop = els.chatbotMessages.scrollHeight;
    }
  }

  async function resetAssistant() {
    try {
      await postJson(API.assistantReset());
    } catch (_err) {
      /* clearing the screen is worth doing even if the server missed it */
    }
    els.chatbotMessages.innerHTML = "";
    renderChatbotEmpty();
  }

  // ------------------------------------------------------------
  // Supervisor: records
  // ------------------------------------------------------------

  const SEVERITY_CLASSES = ["minor", "moderate", "major", "critical", "aog"];

  function severityBadgeClass(severity) {
    const key = (severity || "").toLowerCase().trim();
    return SEVERITY_CLASSES.includes(key) ? `badge--${key}` : "badge--default";
  }

  /** Current filter state, read straight off the controls. */
  function currentFilters() {
    const filters = {};
    els.filterSelects.forEach((select) => {
      if (select.value) filters[select.dataset.filter] = select.value;
    });
    const search = els.inputFilterSearch.value.trim();
    if (search) filters.search = search;
    return filters;
  }

  function filtersAreActive() {
    return Object.keys(currentFilters()).length > 0;
  }

  /**
   * Populate the dropdowns from the values that actually exist in the
   * records, rather than a hardcoded list that would drift from reality.
   * Runs once per sign-in, then again after a refresh.
   */
  async function loadFilterOptions() {
    let data;
    try {
      data = await api(API.filters());
    } catch (_err) {
      return; // the free-text search still works without the dropdowns
    }

    state.photosEnabled = Boolean(data.photos_enabled);

    els.filterSelects.forEach((select) => {
      const values = data[select.dataset.filter] || [];
      const previous = select.value;
      // Keep the "All"/"Any" option, replace the rest.
      const placeholder = select.options[0];
      select.innerHTML = "";
      select.appendChild(placeholder);

      values.forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      });

      // A value that survived a refresh stays selected.
      if (previous && values.includes(previous)) select.value = previous;
      markSelectState(select);
    });
  }

  function markSelectState(select) {
    select.classList.toggle("is-set", Boolean(select.value));
  }

  async function loadRecords() {
    const filters = currentFilters();
    const params = new URLSearchParams(filters).toString();

    els.btnClearFilters.hidden = !filtersAreActive();
    els.filterSelects.forEach(markSelectState);

    els.recordsList.innerHTML =
      '<p class="records-list__empty">Loading records…</p>';

    try {
      const data = await api(API.records(params));
      state.records = data.records || [];
      renderRecords(state.records);
      els.recordsCount.textContent =
        `${data.count} ${data.count === 1 ? "finding" : "findings"}` +
        (filtersAreActive() ? " (filtered)" : "");
    } catch (err) {
      state.records = [];
      els.recordsList.innerHTML =
        `<p class="records-list__empty">${escapeHtml(err.message)}</p>`;
      els.recordsCount.textContent = "—";
    }
  }

  function clearFilters() {
    els.filterSelects.forEach((select) => {
      select.value = "";
      markSelectState(select);
    });
    els.inputFilterSearch.value = "";
    loadRecords();
  }

  // ------------------------------------------------------------
  // Supervisor: the records list is an accordion
  // --------------------------------------------------------
  // The list is the page. Clicking a finding expands it in place,
  // which keeps the surrounding context (what else is open on this
  // aircraft, what the severities look like) visible while reading
  // the detail - the old side panel showed the detail at the cost of
  // squeezing the list into a third of the width.
  // ------------------------------------------------------------

  function renderRecords(records) {
    if (!records.length) {
      els.recordsList.innerHTML = filtersAreActive()
        ? '<p class="records-list__empty">No findings match these filters.</p>'
        : '<p class="records-list__empty">No findings logged yet.</p>';
      return;
    }

    els.recordsList.innerHTML = "";
    records.forEach((record) => els.recordsList.appendChild(buildRecordRow(record)));
  }

  function buildRecordRow(record) {
    const row = document.createElement("div");
    row.className = "record-row";
    row.dataset.recordId = record.RECORD_ID;

    const summary = document.createElement("button");
    summary.type = "button";
    summary.className = "record-row__summary";
    summary.setAttribute("aria-expanded", "false");

    const photoBadge = record.PHOTO_COUNT
      ? `<span class="photo-badge">📷 ${record.PHOTO_COUNT}</span>`
      : "";

    summary.innerHTML = `
      <span class="record-row__aircraft">${escapeHtml(record.AIRCRAFT_REG || "—")}</span>
      <span class="record-row__component">${escapeHtml(record.COMPONENT || "No component recorded")}</span>
      <span><span class="badge ${severityBadgeClass(record.SEVERITY)}">${escapeHtml(record.SEVERITY || "—")}</span></span>
      <span class="record-row__meta record-row__status">${escapeHtml(record.STATUS || "—")}</span>
      <span class="record-row__meta">${escapeHtml(record.TECHNICIAN || "—")}</span>
      <span class="record-row__meta">${formatDate(record.CREATED_AT)}</span>
      <span class="record-row__chevron" aria-hidden="true">▾</span>
    `;

    if (photoBadge) {
      summary.querySelector(".record-row__component").insertAdjacentHTML(
        "afterend", `<span>${photoBadge}</span>`
      );
      summary.style.gridTemplateColumns =
        "96px minmax(0, 1fr) 54px 92px 96px 130px 116px 26px";
    }

    summary.addEventListener("click", () => toggleRecordRow(record.RECORD_ID));
    row.appendChild(summary);
    return row;
  }

  async function toggleRecordRow(recordId) {
    const row = els.recordsList.querySelector(`[data-record-id="${recordId}"]`);
    if (!row) return;

    // Clicking the open one closes it.
    if (state.openRecordId === recordId) {
      collapseOpenRow();
      return;
    }

    collapseOpenRow();

    state.openRecordId = recordId;
    state.selectedRecordId = recordId;
    row.classList.add("is-open");
    row.querySelector(".record-row__summary").setAttribute("aria-expanded", "true");

    const detail = document.createElement("div");
    detail.className = "record-row__detail";
    detail.innerHTML = '<p class="detail-section__body">Loading details…</p>';
    row.appendChild(detail);

    // The assistant should now be talking about this finding.
    updateAssistantContext();

    try {
      const [data, photoData] = await Promise.all([
        api(API.record(recordId)),
        api(API.recordPhotos(recordId)).catch(() => ({ photos: [] })),
      ]);
      renderRecordDetail(detail, data.record, data.conversation || [], photoData.photos || []);
    } catch (err) {
      detail.innerHTML =
        `<p class="detail-section__body">${escapeHtml(err.message)}</p>`;
    }
  }

  function collapseOpenRow() {
    if (!state.openRecordId) return;

    const open = els.recordsList.querySelector(
      `[data-record-id="${state.openRecordId}"]`
    );
    if (open) {
      open.classList.remove("is-open");
      const summary = open.querySelector(".record-row__summary");
      if (summary) summary.setAttribute("aria-expanded", "false");
      const detail = open.querySelector(".record-row__detail");
      if (detail) detail.remove();
    }

    state.openRecordId = null;
    state.selectedRecordId = null;
    updateAssistantContext();
  }

  function renderRecordDetail(container, record, conversation, photos) {
    const fields = [
      ["Aircraft", record.AIRCRAFT_REG],
      ["Component", record.COMPONENT],
      ["Location", record.LOCATION],
      ["Severity", record.SEVERITY],
      ["Status", record.STATUS],
      ["Technician", record.TECHNICIAN],
      ["Inspected", formatDate(record.INSPECTION_TS)],
      ["Record id", record.RECORD_ID],
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

    const photoPlates = photos
      .map(
        (photo) => `
        <div class="photo-thumb">
          <img src="${escapeHtml(photo.url)}" alt="${escapeHtml(photo.caption || "Damage photo")}"
               data-photo-url="${escapeHtml(photo.url)}"
               data-photo-caption="${escapeHtml(photo.caption || "")}" loading="lazy">
        </div>`
      )
      .join("");

    container.innerHTML = `
      <div class="detail-grid">
        ${fields
          .map(
            ([label, value]) => `
          <div class="detail-item">
            <span class="detail-item__label">${label}</span>
            <span class="detail-item__value ${value ? "" : "is-empty"}">${escapeHtml(value || "—")}</span>
          </div>`
          )
          .join("")}
      </div>

      <div class="detail-section">
        <p class="detail-section__title">Finding</p>
        <p class="detail-section__body">${escapeHtml(record.FINDING || "Not recorded")}</p>
      </div>

      <div class="detail-section">
        <p class="detail-section__title">Recommended action</p>
        <p class="detail-section__body">${escapeHtml(record.RECOMMENDED_ACTION || "Not recorded")}</p>
      </div>

      ${
        photos.length
          ? `<div class="detail-section">
               <p class="detail-section__title">Photographic evidence (${photos.length})</p>
               <div class="photo-grid">${photoPlates}</div>
             </div>`
          : ""
      }

      ${
        turns
          ? `<div class="detail-section">
               <p class="detail-section__title">Transcript</p>
               <div class="record-detail__conversation">${turns}</div>
             </div>`
          : ""
      }

      <div class="detail-actions">
        <button class="btn btn--primary btn--small" data-action="report">Download PDF report</button>
        <button class="btn btn--secondary btn--small" data-action="ask">Ask the assistant about this</button>
      </div>
    `;

    container
      .querySelector('[data-action="report"]')
      .addEventListener("click", () => window.open(API.report(record.RECORD_ID), "_blank"));

    container.querySelector('[data-action="ask"]').addEventListener("click", () => {
      openChatbot();
      els.inputChatbot.focus();
    });

    container.querySelectorAll("[data-photo-url]").forEach((img) => {
      img.addEventListener("click", () =>
        openPhotoViewer(img.dataset.photoUrl, img.dataset.photoCaption)
      );
    });
  }

  // ------------------------------------------------------------
  // Full-size photo viewer (both roles)
  // ------------------------------------------------------------

  function openPhotoViewer(url, caption) {
    const overlay = document.createElement("div");
    overlay.className = "photo-viewer";
    overlay.innerHTML = `
      <img src="${escapeHtml(url)}" alt="${escapeHtml(caption || "Damage photo")}">
      ${caption ? `<p class="photo-viewer__caption">${escapeHtml(caption)}</p>` : ""}
    `;

    const close = () => {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
    };
    const onKey = (event) => {
      if (event.key === "Escape") close();
    };

    overlay.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
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

  // --- Technician: damage photos ---

  els.btnTakePhoto.addEventListener("click", () => els.inputPhotoCamera.click());
  els.btnUploadPhoto.addEventListener("click", () => els.inputPhotoFile.click());

  [els.inputPhotoCamera, els.inputPhotoFile].forEach((input) => {
    input.addEventListener("change", () => {
      const files = Array.from(input.files || []);
      // Reset first: picking the same file twice must still fire change.
      input.value = "";
      uploadPhotos(files);
    });
  });

  // --- Supervisor: filters ---

  els.btnRefreshRecords.addEventListener("click", () => {
    collapseOpenRow();
    loadFilterOptions().then(loadRecords);
  });

  els.filterSelects.forEach((select) => {
    select.addEventListener("change", () => {
      collapseOpenRow();
      loadRecords();
    });
  });

  els.btnClearFilters.addEventListener("click", () => {
    collapseOpenRow();
    clearFilters();
  });

  // Debounced so typing doesn't fire a query per keystroke.
  let searchTimer = null;
  els.inputFilterSearch.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      collapseOpenRow();
      loadRecords();
    }, 300);
  });

  els.inputFilterSearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      clearTimeout(searchTimer);
      collapseOpenRow();
      loadRecords();
    }
  });

  // --- Supervisor assistant ---

  els.btnChatbotToggle.addEventListener("click", toggleChatbot);
  els.btnChatbotClose.addEventListener("click", closeChatbot);
  els.btnChatbotReset.addEventListener("click", resetAssistant);
  els.formChatbot.addEventListener("submit", askAssistant);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && els.chatbot.classList.contains("is-open")) {
      closeChatbot();
    }
  });

  restoreSession();
})();