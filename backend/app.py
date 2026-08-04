"""
==============================================================
AI Maintenance Voice Copilot
Flask Application Entry Point
--------------------------------------------------------------

Purpose
-------
Wire together the Voice Processing Layer (speech_to_text /
text_to_speech), the AI Reasoning Layer (agent), the Knowledge
Layer (database), and the Reporting Layer (pdf_service) behind
a small Flask HTTP API, and serve the static frontend.

Responsibilities
----------------
• Serve the frontend (frontend/index.html, style.css, app.js)
• Authenticate users and gate every endpoint by role
• Manage one MaintenanceAgent per active inspection session
  (in-memory session store, keyed by session_id)
• Expose endpoints for: signing in, starting a session, sending a
  voice or text turn, listing/retrieving maintenance records,
  generating and downloading PDF reports, serving synthesized audio
• Verify the SAP HANA schema on startup

Access model
------------
There is no sign-up endpoint. Accounts are created out of band:

    python -m backend.scripts.manage_users add \\
        --username jsmith --role technician --full-name "J. Smith"

    TECHNICIAN  can run inspection sessions and see their own records
    SUPERVISOR  can see and report on every record, but does not
                run inspection sessions

IMPORTANT
---------
This module never reads environment variables directly.
All settings come from backend.config.

Run
---
    python -m backend.app

    # or, from the project root:
    flask --app backend.app run --host 0.0.0.0 --port 5000
==============================================================
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Optional

from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from backend.config import (
    APP_NAME,
    APP_VERSION,
    DEBUG,
    HOST,
    PORT,
    SECRET_KEY,
    SESSION_LIFETIME_HOURS,
    SESSION_COOKIE_SECURE,
    HTTPS_ADHOC,
    WARMUP_ON_STARTUP,
    CERTS_FOLDER,
    ROLE_SUPERVISOR,
    ROLE_TECHNICIAN,
    FRONTEND_FOLDER,
    AUDIO_OUTPUT_FOLDER,
    PHOTO_MAX_PER_RECORD,
    LOG_LEVEL,
)
from backend.auth import (
    AccountDisabledError,
    InvalidCredentialsError,
    authenticate,
    current_user,
    login_required,
    login_session,
    logout_session,
    public_user,
    role_required,
)
from backend.agent import MaintenanceAgent
from backend.assistant import SupervisorAssistant
from backend.database import (
    init_db,
    list_maintenance_records,
    get_maintenance_record,
    get_record_filter_options,
    get_conversation,
    list_manuals,
    count_chunks,
    record_photos_available,
    insert_record_photo,
    list_record_photos,
    get_record_photo,
    delete_record_photo,
    count_record_photos_by_record,
)
from backend.photo_service import (
    process_upload,
    UnsupportedImageError,
    ImageTooLargeError,
)
from backend.speech_to_text import (
    transcribe_audio_bytes,
    UnsupportedAudioFormatError,
    RecordingTooLongError,
    TranscriptionError,
)
from backend.text_to_speech import (
    synthesize_speech,
    synthesize_speech_bytes,
    VoiceModelNotFoundError,
)
from backend.pdf_service import generate_report_for_record, MaintenanceRecordNotFoundError

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("mro_copilot.app")

# The SAP AI Core SDKs log every request and the entire response body at
# DEBUG, on loggers they configure themselves - so a deployment listing
# or a full chat completion lands in the application log regardless of
# LOG_LEVEL. Keep them at WARNING; set LOG_LEVEL=DEBUG and raise these
# by hand if a provider call actually needs tracing.
for _noisy in ("ai_core_sdk", "ai-api-client-sdk", "httpx", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ==========================================================
# In-memory session store
# --------------------------------------------------------
# Maps session_id -> MaintenanceAgent. This is fine for a
# single-process dev/demo deployment; swap for a shared store
# (Redis, etc.) if you scale to multiple workers/processes.
# ==========================================================

SESSIONS: dict[str, MaintenanceAgent] = {}

# One supervisor assistant per signed-in supervisor, keyed by USER_ID so
# the chat survives clicking between records (and a page refresh) rather
# than restarting from nothing each question. Same single-process caveat
# as SESSIONS above.
ASSISTANTS: dict[str, "SupervisorAssistant"] = {}
_assistants_lock = threading.Lock()


def _get_assistant(user: dict) -> "SupervisorAssistant":
    """Fetch this supervisor's assistant, creating it on first question."""
    user_id = user["USER_ID"]

    assistant = ASSISTANTS.get(user_id)
    if assistant is not None:
        return assistant

    with _assistants_lock:
        # Double-checked: two tabs asking at once must not get two histories.
        assistant = ASSISTANTS.get(user_id)
        if assistant is None:
            assistant = SupervisorAssistant(
                supervisor_name=user.get("FULL_NAME") or user["USERNAME"]
            )
            ASSISTANTS[user_id] = assistant

    return assistant


def _require_own_session(session_id: str):
    """
    Fetch an inspection session, but only if it belongs to the
    signed-in user. Returns (agent, None) or (None, error_response).
    """
    agent = SESSIONS.get(session_id)
    if not agent:
        return None, (jsonify({"error": f"Unknown session_id '{session_id}'"}), 404)

    user = current_user()
    if agent.user_id != user["USER_ID"]:
        return None, (jsonify({"error": "That session belongs to someone else."}), 403)

    return agent, None


# ==========================================================
# Streaming replies (text + speech as the model generates them)
# --------------------------------------------------------
# Instead of waiting for the whole reply then synthesizing the
# whole thing as one WAV, the reply is streamed token-by-token
# from the model, and each completed *sentence* is synthesized
# and sent to the browser as soon as it's ready. The browser
# plays clips back to back as they arrive, so audio starts after
# the first sentence instead of after the entire reply - both
# faster and closer to "the copilot is speaking as it thinks".
# ==========================================================

_SENTENCE_BOUNDARY = re.compile(r"[.!?]\s+")
_BARE_LIST_MARKER = re.compile(r"^\d{1,3}\.?$")


def _next_sentence_end(buffer: str) -> Optional[int]:
    """
    Find where the first real sentence in `buffer` ends, or None if
    there isn't a complete one yet.

    A numbered step like "2. Remove the engine cowlings." has a
    period-space right after the "2" too, which looks exactly like a
    sentence end - split there and the reply is read aloud as a run
    of isolated digits ("Two. Three. Four."). Skip any boundary whose
    candidate sentence is nothing but a bare list marker.
    """
    search_from = 0
    while True:
        match = _SENTENCE_BOUNDARY.search(buffer, search_from)
        if not match:
            return None
        candidate = buffer[:match.start()].strip()
        if not candidate or _BARE_LIST_MARKER.match(candidate):
            search_from = match.end()
            continue
        return match.end()


def _ndjson_line(payload: dict) -> bytes:
    return (json.dumps(payload, default=str) + "\n").encode("utf-8")


def _sentence_audio_event(sentence: str) -> dict:
    try:
        audio_bytes = synthesize_speech_bytes(sentence)
        return {
            "type": "audio",
            "text": sentence,
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
        }
    except VoiceModelNotFoundError as exc:
        logger.warning("Piper voice missing, skipping audio for a sentence: %s", exc)
        return {"type": "audio_unavailable", "text": sentence}
    except Exception:  # noqa: BLE001
        logger.exception("Sentence synthesis failed")
        return {"type": "audio_unavailable", "text": sentence}


# Synthesis runs off the request thread so it never stalls the token
# stream. One worker, not a pool: a PiperVoice wraps a single ONNX
# session, and serialising synthesis both keeps that safe and keeps
# the audio clips in sentence order. It keeps up comfortably - a warm
# sentence synthesises in ~0.1s while the model needs ~1s to produce
# the next one.
_tts_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")


def _stream_turn(agent: MaintenanceAgent, technician_utterance: str, transcript: Optional[str] = None):
    """
    Generator of NDJSON-encoded lines for a Flask streaming Response.
    One JSON object per line:
        {"type": "transcript", "text": ...}          voice turns only, first
        {"type": "text", "delta": ...}                 as the reply streams in
        {"type": "audio", "text": ..., "audio_base64": ...}   one per sentence
        {"type": "audio_unavailable", "text": ...}     Piper voice not installed
        {"type": "done", "reply": ..., "record_id": ...,
         "record_complete": ..., "record": {...}}

    Text and speech genuinely overlap here. Each completed sentence is
    handed to a background synthesis worker and the loop goes straight
    back to reading model tokens, so words keep appearing on screen
    while earlier sentences are still being turned into audio. Doing
    the synthesis inline (as this used to) stopped the token stream
    dead for the duration of every clip, which made the text arrive in
    visible lurches and delayed the whole reply by the sum of the
    synthesis times rather than overlapping them.

    Clips are still emitted in sentence order: completed futures are
    drained from the front of the queue only, so audio never overtakes
    itself even though synthesis happens off-thread.
    """
    if transcript is not None:
        yield _ndjson_line({"type": "transcript", "text": transcript})

    buffer = ""
    full_reply = ""
    pending: deque = deque()

    def drain_finished():
        """Emit audio for sentences whose synthesis has already finished."""
        while pending and pending[0].done():
            yield _ndjson_line(pending.popleft().result())

    for event in agent.send_stream(technician_utterance):
        if event["type"] == "content":
            delta = event["text"]
            full_reply += delta
            buffer += delta
            yield _ndjson_line({"type": "text", "delta": delta})

            while True:
                end = _next_sentence_end(buffer)
                if end is None:
                    break
                sentence, buffer = buffer[:end].strip(), buffer[end:]
                if sentence:
                    pending.append(_tts_worker.submit(_sentence_audio_event, sentence))

            # Hand over anything already synthesised, but never wait here.
            yield from drain_finished()
        else:
            full_reply = event["reply"]

    remaining = buffer.strip()
    if remaining:
        pending.append(_tts_worker.submit(_sentence_audio_event, remaining))

    # The reply is fully generated; now it's fine to wait for the tail
    # of the audio, still in order.
    while pending:
        yield _ndjson_line(pending.popleft().result())

    # One read of the record, not three. The status card needs the
    # fields, and completeness is derived from the same row - so this
    # payload carries the record itself and the browser no longer
    # follows up with GET /api/sessions/<id> (which re-read the row and
    # then re-read it again to judge completeness).
    record = get_maintenance_record(agent.record_id) if agent.record_id else None

    yield _ndjson_line(
        {
            "type": "done",
            "reply": full_reply,
            "record_id": agent.record_id,
            "record_complete": MaintenanceAgent.record_is_complete(record),
            "record": record,
        }
    )


# ==========================================================
# App Factory
# ==========================================================

def _warm_up() -> None:
    """
    Pay the one-off startup costs now, in the background, instead of
    charging them to whoever sends the first voice turn.

    Measured cold costs on a developer machine:

        Piper voice load            ~4.0s   parses a ~50 MB ONNX graph
        Piper first synthesis       ~5.2s   ONNX warmup; ~0.1s thereafter
        AI Core proxy client init   ~4.2s   OAuth token + deployment lookup
        HANA connection             ~3.7s   TCP + TLS handshake

    That is well over ten seconds of dead air on the first reply. Each
    step is best-effort: a warmup failure is logged and skipped, never
    fatal, because the real request path reports these problems far
    better than a boot-time crash would.
    """
    from backend.database import warm_pool

    try:
        pooled = warm_pool(2)
        logger.info("Warmup: %d HANA connection(s) ready", pooled)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Warmup: HANA pool unavailable: %s", exc)

    # Loading the voice is not enough - the first synthesis is what
    # actually warms the ONNX runtime, so synthesize a throwaway phrase.
    try:
        synthesize_speech_bytes("Systems ready.", normalise=False)
        logger.info("Warmup: Piper voice loaded and warm")
    except VoiceModelNotFoundError as exc:
        logger.warning("Warmup: Piper voice not installed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Warmup: Piper synthesis failed: %s", exc)

    # Building the transcription model resolves the AI Core proxy
    # client, which is the slow part; the same client is then reused by
    # the chat and embedding calls too.
    try:
        from backend.speech_to_text import warm_up_transcriber

        warm_up_transcriber()
        logger.info("Warmup: AI Core proxy client ready")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Warmup: AI Core proxy client unavailable: %s", exc)

    logger.info("Warmup complete - first voice turn will not pay startup costs")


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(FRONTEND_FOLDER),
        static_url_path="",
    )

    app.config.update(
        SECRET_KEY=SECRET_KEY,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_LIFETIME_HOURS),
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,   # 32 MB cap on uploads
    )

    logger.info("Verifying database schema...")
    init_db()

    register_routes(app)
    register_error_handlers(app)

    if WARMUP_ON_STARTUP:
        # Daemon thread: the server starts serving immediately and the
        # process is never held open by a warmup still in flight.
        threading.Thread(target=_warm_up, name="warmup", daemon=True).start()

    return app


# ==========================================================
# Routes
# ==========================================================

def register_routes(app: Flask) -> None:

    # ------------------------------------------------------
    # Frontend
    # ------------------------------------------------------

    @app.route("/")
    def index():
        return send_from_directory(FRONTEND_FOLDER, "index.html")

    # ------------------------------------------------------
    # Health (public - used by platform probes)
    # ------------------------------------------------------

    @app.route("/api/health")
    def health():
        return jsonify({"app": APP_NAME, "version": APP_VERSION, "status": "ok"})

    # ------------------------------------------------------
    # Authentication
    # ------------------------------------------------------

    @app.route("/api/auth/login", methods=["POST"])
    def login():
        """
        Sign in with a username and password.

        This is the only way to obtain a session. Accounts cannot be
        created here - an administrator provisions them with
        backend/scripts/manage_users.py.
        """
        body = request.get_json(silent=True) or {}
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""

        if not username or not password:
            return jsonify({"error": "Enter your username and password."}), 400

        try:
            user = authenticate(username, password)
        except InvalidCredentialsError as exc:
            logger.info("Failed sign-in attempt for '%s'", username)
            return jsonify({"error": str(exc)}), 401
        except AccountDisabledError as exc:
            return jsonify({"error": str(exc)}), 403

        login_session(user)
        return jsonify({"user": public_user(user)})

    @app.route("/api/auth/logout", methods=["POST"])
    def logout():
        logout_session()
        return jsonify({"signed_out": True})

    @app.route("/api/auth/me", methods=["GET"])
    def me():
        """Who am I? Used by the frontend on load to restore state."""
        user = current_user()
        if not user:
            return jsonify({"user": None}), 401
        return jsonify({"user": public_user(user)})

    # ------------------------------------------------------
    # Inspection sessions (technicians only)
    # ------------------------------------------------------

    @app.route("/api/sessions", methods=["POST"])
    @role_required(ROLE_TECHNICIAN)
    def create_session():
        """
        Start a new inspection session.

        The technician is the signed-in user - the client does not
        get to name them.
        """
        user = current_user()
        session_id = str(uuid.uuid4())

        SESSIONS[session_id] = MaintenanceAgent(
            technician=user.get("FULL_NAME") or user["USERNAME"],
            session_id=session_id,
            user_id=user["USER_ID"],
        )

        logger.info("Started session %s for %s", session_id, user["USERNAME"])
        return jsonify(
            {
                "session_id": session_id,
                "technician": user.get("FULL_NAME") or user["USERNAME"],
            }
        ), 201

    @app.route("/api/sessions/<session_id>", methods=["GET"])
    @role_required(ROLE_TECHNICIAN)
    def get_session_status(session_id):
        agent, error = _require_own_session(session_id)
        if error:
            return error

        # Read the row once and derive completeness from it, rather than
        # fetching it here and having is_record_complete() fetch it again.
        record = get_maintenance_record(agent.record_id) if agent.record_id else None

        return jsonify(
            {
                "session_id": session_id,
                "technician": agent.technician,
                "record_id": agent.record_id,
                "record_complete": MaintenanceAgent.record_is_complete(record),
                "record": record,
            }
        )

    @app.route("/api/sessions/<session_id>", methods=["DELETE"])
    @role_required(ROLE_TECHNICIAN)
    def end_session(session_id):
        agent, error = _require_own_session(session_id)
        if error:
            return error

        SESSIONS.pop(session_id, None)
        return jsonify({"session_id": session_id, "ended": True})

    # ------------------------------------------------------
    # Conversation turns
    # ------------------------------------------------------

    @app.route("/api/sessions/<session_id>/message", methods=["POST"])
    @role_required(ROLE_TECHNICIAN)
    def send_text_message(session_id):
        """
        Text-only turn (fallback / testing path when no audio is
        available). Body: {"text": "..."}
        """
        agent, error = _require_own_session(session_id)
        if error:
            return error

        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Type a message before sending."}), 400

        reply = agent.send(text)
        return jsonify(
            {
                "session_id": session_id,
                "transcript": text,
                "reply": reply,
                "record_id": agent.record_id,
                "record_complete": agent.is_record_complete(),
            }
        )

    @app.route("/api/sessions/<session_id>/voice", methods=["POST"])
    @role_required(ROLE_TECHNICIAN)
    def send_voice_message(session_id):
        """
        Voice turn: technician uploads an audio recording, it's
        transcribed by the Gemini deployment in AI Core, sent to the
        agent, and the reply is both returned as text and synthesized
        back to speech locally with Piper.

        Expects a multipart/form-data body with an 'audio' file field.
        """
        agent, error = _require_own_session(session_id)
        if error:
            return error

        if "audio" not in request.files:
            return jsonify({"error": "No recording was attached."}), 400

        audio_file = request.files["audio"]
        original_name = secure_filename(audio_file.filename or "recording.webm")

        try:
            transcript = transcribe_audio_bytes(audio_file.read(), original_name)
        except UnsupportedAudioFormatError as exc:
            return jsonify({"error": str(exc)}), 400
        except RecordingTooLongError as exc:
            return jsonify({"error": str(exc)}), 400
        except TranscriptionError as exc:
            logger.exception("Transcription failed")
            return jsonify({"error": str(exc)}), 502

        if not transcript:
            return jsonify(
                {"error": "No speech was picked up. Move closer to the mic and try again."}
            ), 422

        reply = agent.send(transcript)

        audio_reply_url = None
        try:
            audio_reply_path = synthesize_speech(reply)
            audio_reply_url = f"/api/audio/{audio_reply_path.name}"
        except VoiceModelNotFoundError as exc:
            logger.warning("Piper voice missing, returning text-only reply: %s", exc)
        except Exception:
            logger.exception("Speech synthesis failed; returning text-only reply")

        return jsonify(
            {
                "session_id": session_id,
                "transcript": transcript,
                "reply": reply,
                "reply_audio_url": audio_reply_url,
                "record_id": agent.record_id,
                "record_complete": agent.is_record_complete(),
            }
        )

    @app.route("/api/sessions/<session_id>/message/stream", methods=["POST"])
    @role_required(ROLE_TECHNICIAN)
    def send_text_message_stream(session_id):
        """
        Streaming counterpart to /message: the reply is sent back as
        newline-delimited JSON, one sentence's audio (and every text
        delta) as soon as it's ready, instead of one JSON blob after
        the whole reply and its WAV file are both fully generated.
        """
        agent, error = _require_own_session(session_id)
        if error:
            return error

        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Type a message before sending."}), 400

        return Response(_stream_turn(agent, text), mimetype="application/x-ndjson")

    @app.route("/api/sessions/<session_id>/voice/stream", methods=["POST"])
    @role_required(ROLE_TECHNICIAN)
    def send_voice_message_stream(session_id):
        """Streaming counterpart to /voice - see send_text_message_stream."""
        agent, error = _require_own_session(session_id)
        if error:
            return error

        if "audio" not in request.files:
            return jsonify({"error": "No recording was attached."}), 400

        audio_file = request.files["audio"]
        original_name = secure_filename(audio_file.filename or "recording.webm")

        try:
            transcript = transcribe_audio_bytes(audio_file.read(), original_name)
        except UnsupportedAudioFormatError as exc:
            return jsonify({"error": str(exc)}), 400
        except RecordingTooLongError as exc:
            return jsonify({"error": str(exc)}), 400
        except TranscriptionError as exc:
            logger.exception("Transcription failed")
            return jsonify({"error": str(exc)}), 502

        if not transcript:
            return jsonify(
                {"error": "No speech was picked up. Move closer to the mic and try again."}
            ), 422

        return Response(
            _stream_turn(agent, transcript, transcript=transcript),
            mimetype="application/x-ndjson",
        )

    @app.route("/api/sessions/<session_id>/speak", methods=["POST"])
    @role_required(ROLE_TECHNICIAN)
    def speak_text(session_id):
        """
        Synthesize arbitrary text for the current session - used by
        the frontend to read a typed-turn reply aloud, since the text
        path does not generate audio on its own.
        """
        _, error = _require_own_session(session_id)
        if error:
            return error

        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Nothing to read aloud."}), 400

        try:
            path = synthesize_speech(text)
        except VoiceModelNotFoundError as exc:
            return jsonify({"error": str(exc)}), 503
        except Exception as exc:  # noqa: BLE001
            logger.exception("Speech synthesis failed")
            return jsonify({"error": f"Speech synthesis failed: {exc}"}), 500

        return jsonify({"reply_audio_url": f"/api/audio/{path.name}"})

    # ------------------------------------------------------
    # Synthesized audio playback
    # ------------------------------------------------------

    @app.route("/api/audio/<file_name>", methods=["GET"])
    @login_required
    def get_audio(file_name):
        safe_name = secure_filename(file_name)
        path = AUDIO_OUTPUT_FOLDER / safe_name
        if not path.exists():
            return jsonify({"error": "That audio clip is no longer available."}), 404
        return send_file(path, mimetype="audio/wav")

    # ------------------------------------------------------
    # Damage photos (optional evidence on a finding)
    # ------------------------------------------------------

    def _photo_payload(photo: dict) -> dict:
        """Metadata only - the bytes are fetched per photo from /api/photos."""
        return {
            "photo_id": photo["PHOTO_ID"],
            "record_id": photo["RECORD_ID"],
            "file_name": photo.get("FILE_NAME"),
            "mime_type": photo.get("MIME_TYPE"),
            "byte_size": photo.get("BYTE_SIZE"),
            "width": photo.get("WIDTH"),
            "height": photo.get("HEIGHT"),
            "caption": photo.get("CAPTION"),
            "created_at": photo.get("CREATED_AT"),
            "url": f"/api/photos/{photo['PHOTO_ID']}",
        }

    def _may_see_record(record: dict, user: dict) -> bool:
        return (
            user["ROLE"] == ROLE_SUPERVISOR
            or record.get("TECHNICIAN_USER_ID") == user["USER_ID"]
        )

    @app.route("/api/sessions/<session_id>/photos", methods=["POST"])
    @role_required(ROLE_TECHNICIAN)
    def upload_session_photo(session_id):
        """
        Attach a damage photo to the finding being recorded right now.

        Optional by design: a technician may log a finding with no photo
        at all. Accepts a multipart body with a 'photo' file field and an
        optional 'caption'. Works for both a camera capture and a file
        pick - the browser sends the same thing either way.
        """
        agent, error = _require_own_session(session_id)
        if error:
            return error

        if not record_photos_available():
            return jsonify({
                "error": "Photo storage isn't set up yet. Ask an administrator to "
                         "run schema_record_photos.sql against the HANA schema."
            }), 503

        # Photos hang off a record, so there has to be one. The agent
        # creates it on the first thing the technician says.
        if not agent.record_id:
            return jsonify({
                "error": "Describe the finding first, then attach a photo to it."
            }), 409

        if "photo" not in request.files:
            return jsonify({"error": "No photo was attached."}), 400

        existing = list_record_photos(agent.record_id)
        if len(existing) >= PHOTO_MAX_PER_RECORD:
            return jsonify({
                "error": f"This finding already has {PHOTO_MAX_PER_RECORD} photos, "
                         f"which is the limit."
            }), 409

        upload = request.files["photo"]
        original_name = secure_filename(upload.filename or "damage.jpg")

        try:
            processed = process_upload(upload.read(), original_name)
        except ImageTooLargeError as exc:
            return jsonify({"error": str(exc)}), 413
        except UnsupportedImageError as exc:
            return jsonify({"error": str(exc)}), 400

        user = current_user()
        caption = (request.form.get("caption") or "").strip() or None

        photo_id = insert_record_photo(
            record_id=agent.record_id,
            image_data=processed.data,
            mime_type=processed.mime_type,
            file_name=processed.original_name,
            caption=caption,
            uploaded_by=user["USER_ID"],
            width=processed.width,
            height=processed.height,
        )

        return jsonify({
            "photo": {
                "photo_id": photo_id,
                "record_id": agent.record_id,
                "mime_type": processed.mime_type,
                "width": processed.width,
                "height": processed.height,
                "byte_size": len(processed.data),
                "caption": caption,
                "file_name": processed.original_name,
                "url": f"/api/photos/{photo_id}",
            },
            "photo_count": len(existing) + 1,
        }), 201

    @app.route("/api/records/<record_id>/photos", methods=["GET"])
    @login_required
    def get_record_photos(record_id):
        """Photo metadata for a record. Supervisors any, technicians own."""
        user = current_user()
        record = get_maintenance_record(record_id)

        if not record:
            return jsonify({"error": "That record no longer exists."}), 404
        if not _may_see_record(record, user):
            return jsonify({"error": "That record belongs to another technician."}), 403

        photos = list_record_photos(record_id)
        return jsonify({
            "photos": [_photo_payload(p) for p in photos],
            "count": len(photos),
        })

    @app.route("/api/photos/<photo_id>", methods=["GET"])
    @login_required
    def get_photo(photo_id):
        """
        Serve one image.

        Access is decided by the record the photo belongs to, not by the
        photo id - so a technician cannot pull a colleague's evidence by
        guessing an id.
        """
        photo = get_record_photo(photo_id)
        if not photo or not photo.get("IMAGE_DATA"):
            return jsonify({"error": "That photo is no longer available."}), 404

        record = get_maintenance_record(photo["RECORD_ID"])
        if not record or not _may_see_record(record, current_user()):
            return jsonify({"error": "You can't view that photo."}), 403

        response = Response(photo["IMAGE_DATA"], mimetype=photo.get("MIME_TYPE") or "image/jpeg")
        # Immutable once stored, and the id is unguessable, so let the
        # browser keep it rather than refetching megabytes per click.
        response.headers["Cache-Control"] = "private, max-age=86400"
        return response

    @app.route("/api/photos/<photo_id>", methods=["DELETE"])
    @login_required
    def remove_photo(photo_id):
        """
        Delete a photo. A technician may remove a bad shot from their own
        finding; a supervisor may remove any.
        """
        photo = get_record_photo(photo_id)
        if not photo:
            return jsonify({"error": "That photo is no longer available."}), 404

        record = get_maintenance_record(photo["RECORD_ID"])
        if not record or not _may_see_record(record, current_user()):
            return jsonify({"error": "You can't delete that photo."}), 403

        delete_record_photo(photo_id)
        return jsonify({"deleted": True, "photo_id": photo_id})

    # ------------------------------------------------------
    # Maintenance records
    # ------------------------------------------------------

    @app.route("/api/records", methods=["GET"])
    @login_required
    def get_records():
        """
        Supervisors see every record. Technicians see only their own -
        the scope is decided here from the session, never from a
        client-supplied parameter.
        """
        user = current_user()

        try:
            limit = min(int(request.args.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50

        technician_user_id = (
            None if user["ROLE"] == ROLE_SUPERVISOR else user["USER_ID"]
        )

        records = list_maintenance_records(
            aircraft_reg=request.args.get("aircraft_reg"),
            component=request.args.get("component"),
            severity=request.args.get("severity"),
            status=request.args.get("status"),
            technician=request.args.get("technician"),
            search=request.args.get("search"),
            technician_user_id=technician_user_id,
            limit=limit,
        )

        # One grouped query for the whole page, so the list can badge which
        # findings carry photo evidence without a query per row.
        photo_counts = count_record_photos_by_record(
            [r["RECORD_ID"] for r in records]
        )
        for record in records:
            record["PHOTO_COUNT"] = photo_counts.get(record["RECORD_ID"], 0)

        return jsonify({"records": records, "count": len(records)})

    @app.route("/api/records/filters", methods=["GET"])
    @login_required
    def get_record_filters():
        """
        The values actually present in the records, for the filter
        dropdowns - so they offer what exists rather than a hardcoded list.

        Scoped like the listing: a technician's dropdowns are built only
        from their own findings.
        """
        user = current_user()
        technician_user_id = (
            None if user["ROLE"] == ROLE_SUPERVISOR else user["USER_ID"]
        )

        options = get_record_filter_options(technician_user_id=technician_user_id)

        return jsonify({
            "aircraft_reg": options.get("AIRCRAFT_REG", []),
            "component": options.get("COMPONENT", []),
            "severity": options.get("SEVERITY", []),
            "status": options.get("STATUS", []),
            "technician": options.get("TECHNICIAN", []),
            "photos_enabled": record_photos_available(),
        })

    @app.route("/api/records/<record_id>", methods=["GET"])
    @login_required
    def get_record(record_id):
        user = current_user()
        record = get_maintenance_record(record_id)

        if not record:
            return jsonify({"error": "That record no longer exists."}), 404

        if (
            user["ROLE"] != ROLE_SUPERVISOR
            and record.get("TECHNICIAN_USER_ID") != user["USER_ID"]
        ):
            return jsonify({"error": "That record belongs to another technician."}), 403

        include_conversation = (
            request.args.get("include_conversation", "true").lower() == "true"
        )
        payload = {"record": record}
        if include_conversation:
            payload["conversation"] = get_conversation(record_id)

        return jsonify(payload)

    # ------------------------------------------------------
    # PDF reports
    # ------------------------------------------------------

    @app.route("/api/records/<record_id>/report", methods=["POST", "GET"])
    @login_required
    def get_report(record_id):
        """
        Generate (or regenerate) the PDF report for a maintenance
        record and stream it back to the caller.
        """
        user = current_user()
        record = get_maintenance_record(record_id)

        if not record:
            return jsonify({"error": "That record no longer exists."}), 404

        if (
            user["ROLE"] != ROLE_SUPERVISOR
            and record.get("TECHNICIAN_USER_ID") != user["USER_ID"]
        ):
            return jsonify({"error": "That record belongs to another technician."}), 403

        try:
            report_path = generate_report_for_record(record_id)
        except MaintenanceRecordNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404

        return send_file(
            report_path,
            as_attachment=True,
            download_name=report_path.name,
            mimetype="application/pdf",
        )

    # ------------------------------------------------------
    # Supervisor assistant (read-only chat)
    # ------------------------------------------------------

    @app.route("/api/assistant/chat", methods=["POST"])
    @role_required(ROLE_SUPERVISOR)
    def assistant_chat():
        """
        Ask the supervisor assistant a question, streamed back as NDJSON.

        Body: {"question": "...", "record_id": "..." | null}

        `record_id` is whichever finding the supervisor has open, so the
        assistant can answer "is this severity right?" without them
        having to restate the record. Omit it for general questions.

        The assistant is strictly read-only - see backend.assistant.
        """
        user = current_user()

        body = request.get_json(silent=True) or {}
        question = (body.get("question") or "").strip()
        record_id = (body.get("record_id") or "").strip() or None

        if not question:
            return jsonify({"error": "Ask a question first."}), 400

        # A supervisor sees every record, so no ownership check is needed -
        # but a bogus id should not silently become "no context".
        if record_id and not get_maintenance_record(record_id):
            record_id = None

        assistant = _get_assistant(user)

        def stream():
            try:
                for event in assistant.ask_stream(question, record_id=record_id):
                    yield _ndjson_line(event)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Supervisor assistant failed")
                yield _ndjson_line({
                    "type": "error",
                    "error": f"The assistant couldn't answer that: {exc}",
                })

        return Response(stream(), mimetype="application/x-ndjson")

    @app.route("/api/assistant/reset", methods=["POST"])
    @role_required(ROLE_SUPERVISOR)
    def assistant_reset():
        """Clear the chat history and start fresh."""
        assistant = ASSISTANTS.get(current_user()["USER_ID"])
        if assistant:
            assistant.reset()
        return jsonify({"reset": True})

    # ------------------------------------------------------
    # Knowledge base status (supervisors)
    # ------------------------------------------------------

    @app.route("/api/manuals", methods=["GET"])
    @role_required(ROLE_SUPERVISOR)
    def get_manuals():
        return jsonify({"manuals": list_manuals(), "chunk_count": count_chunks()})


# ==========================================================
# Error Handlers
# ==========================================================

def register_error_handlers(app: Flask) -> None:

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({"error": "That recording is too large to upload."}), 413

    @app.errorhandler(500)
    def server_error(_error):
        logger.exception("Unhandled server error")
        return jsonify({"error": "Something went wrong on our side."}), 500


# ==========================================================
# Entry Point
# ==========================================================

def _dev_ssl_context() -> Optional[tuple[str, str]]:
    """
    Return (cert_path, key_path) for a self-signed dev certificate,
    generating it once into CERTS_FOLDER if it isn't there yet.

    Deliberately not Werkzeug's ssl_context="adhoc": that regenerates a
    brand-new certificate every time the process starts, and DEBUG=true's
    reloader restarts the process on its own - each restart would
    invalidate the "proceed anyway" exception the browser just accepted,
    which looks exactly like the page never loading.
    """
    cert_path = CERTS_FOLDER / "dev.crt"
    key_path = CERTS_FOLDER / "dev.key"

    if not cert_path.exists() or not key_path.exists():
        from werkzeug.serving import make_ssl_devcert

        logger.info("Generating a self-signed dev certificate in %s", CERTS_FOLDER)
        make_ssl_devcert(str(CERTS_FOLDER / "dev"))

    return (str(cert_path), str(key_path))


app = create_app()

if __name__ == "__main__":
    # A self-signed cert is required for the microphone (getUserMedia/
    # MediaRecorder) to work from any device other than localhost - see
    # HTTPS_ADHOC in backend/config.py.
    ssl_context = _dev_ssl_context() if HTTPS_ADHOC else None
    if ssl_context:
        logger.info(
            "Serving over HTTPS with a self-signed certificate - browsers "
            "will show a one-time certificate warning to click through "
            "(only once, since the certificate is now reused across restarts)."
        )
    app.run(host=HOST, port=PORT, debug=DEBUG, ssl_context=ssl_context)