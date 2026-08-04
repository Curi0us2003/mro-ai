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
import uuid
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
    ROLE_SUPERVISOR,
    ROLE_TECHNICIAN,
    FRONTEND_FOLDER,
    AUDIO_OUTPUT_FOLDER,
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
from backend.database import (
    init_db,
    list_maintenance_records,
    get_maintenance_record,
    get_conversation,
    list_manuals,
    count_chunks,
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

# ==========================================================
# In-memory session store
# --------------------------------------------------------
# Maps session_id -> MaintenanceAgent. This is fine for a
# single-process dev/demo deployment; swap for a shared store
# (Redis, etc.) if you scale to multiple workers/processes.
# ==========================================================

SESSIONS: dict[str, MaintenanceAgent] = {}


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


def _stream_turn(agent: MaintenanceAgent, technician_utterance: str, transcript: Optional[str] = None):
    """
    Generator of NDJSON-encoded lines for a Flask streaming Response.
    One JSON object per line:
        {"type": "transcript", "text": ...}          voice turns only, first
        {"type": "text", "delta": ...}                 as the reply streams in
        {"type": "audio", "text": ..., "audio_base64": ...}   one per sentence
        {"type": "audio_unavailable", "text": ...}     Piper voice not installed
        {"type": "done", "reply": ..., "record_id": ..., "record_complete": ...}
    """
    if transcript is not None:
        yield _ndjson_line({"type": "transcript", "text": transcript})

    buffer = ""
    full_reply = ""

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
                sentence, buffer = buffer[:end], buffer[end:]
                sentence = sentence.strip()
                if sentence:
                    yield _ndjson_line(_sentence_audio_event(sentence))
        else:
            full_reply = event["reply"]

    remaining = buffer.strip()
    if remaining:
        yield _ndjson_line(_sentence_audio_event(remaining))

    yield _ndjson_line(
        {
            "type": "done",
            "reply": full_reply,
            "record_id": agent.record_id,
            "record_complete": agent.is_record_complete(),
        }
    )


# ==========================================================
# App Factory
# ==========================================================

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

        return jsonify(
            {
                "session_id": session_id,
                "technician": agent.technician,
                "record_id": agent.record_id,
                "record_complete": agent.is_record_complete(),
                "record": get_maintenance_record(agent.record_id) if agent.record_id else None,
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
        aircraft_reg = request.args.get("aircraft_reg")
        limit = min(int(request.args.get("limit", 50)), 200)

        technician_user_id = (
            None if user["ROLE"] == ROLE_SUPERVISOR else user["USER_ID"]
        )

        records = list_maintenance_records(
            aircraft_reg=aircraft_reg,
            technician_user_id=technician_user_id,
            limit=limit,
        )
        return jsonify({"records": records, "count": len(records)})

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

app = create_app()

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=DEBUG)