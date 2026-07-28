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
• Manage one MaintenanceAgent per active inspection session
  (in-memory session store, keyed by session_id)
• Expose endpoints for: starting a session, sending a voice or
  text turn, listing/retrieving maintenance records, generating
  and downloading PDF reports, and serving synthesized audio
• Initialise the SAP HANA schema on startup

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

import logging
import uuid
from typing import Optional

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from backend.config import (
    APP_NAME,
    APP_VERSION,
    DEBUG,
    HOST,
    PORT,
    FRONTEND_FOLDER,
    AUDIO_OUTPUT_FOLDER,
    LOG_LEVEL,
)
from backend.agent import MaintenanceAgent
from backend.database import (
    init_db,
    list_maintenance_records,
    get_maintenance_record,
    get_conversation,
)
from backend.speech_to_text import (
    transcribe_audio_bytes,
    UnsupportedAudioFormatError,
    RecordingTooLongError,
)
from backend.text_to_speech import synthesize_speech
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


def _get_session(session_id: str) -> Optional[MaintenanceAgent]:
    return SESSIONS.get(session_id)


def _require_session(session_id: str):
    agent = _get_session(session_id)
    if not agent:
        return None, (jsonify({"error": f"Unknown session_id '{session_id}'"}), 404)
    return agent, None


# ==========================================================
# App Factory
# ==========================================================

def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(FRONTEND_FOLDER),
        static_url_path="",
    )

    logger.info("Initialising database schema...")
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
    # Health
    # ------------------------------------------------------

    @app.route("/api/health")
    def health():
        return jsonify({"app": APP_NAME, "version": APP_VERSION, "status": "ok"})

    # ------------------------------------------------------
    # Sessions
    # ------------------------------------------------------

    @app.route("/api/sessions", methods=["POST"])
    def create_session():
        """Start a new inspection session for a technician."""
        body = request.get_json(silent=True) or {}
        technician = body.get("technician")

        session_id = str(uuid.uuid4())
        SESSIONS[session_id] = MaintenanceAgent(technician=technician, session_id=session_id)

        logger.info("Started session %s for technician=%s", session_id, technician)
        return jsonify({"session_id": session_id, "technician": technician}), 201

    @app.route("/api/sessions/<session_id>", methods=["GET"])
    def get_session_status(session_id):
        agent, error = _require_session(session_id)
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
    def end_session(session_id):
        agent = SESSIONS.pop(session_id, None)
        if not agent:
            return jsonify({"error": f"Unknown session_id '{session_id}'"}), 404
        return jsonify({"session_id": session_id, "ended": True})

    # ------------------------------------------------------
    # Conversation turns
    # ------------------------------------------------------

    @app.route("/api/sessions/<session_id>/message", methods=["POST"])
    def send_text_message(session_id):
        """
        Text-only turn (fallback / testing path when no audio is
        available). Body: {"text": "..."}
        """
        agent, error = _require_session(session_id)
        if error:
            return error

        body = request.get_json(silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Field 'text' is required"}), 400

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
    def send_voice_message(session_id):
        """
        Voice turn: technician uploads an audio recording, it's
        transcribed, sent to the agent, and the reply is both
        returned as text and synthesized back to speech.

        Expects a multipart/form-data body with an 'audio' file field.
        """
        agent, error = _require_session(session_id)
        if error:
            return error

        if "audio" not in request.files:
            return jsonify({"error": "No 'audio' file part in the request"}), 400

        audio_file = request.files["audio"]
        original_name = secure_filename(audio_file.filename or "recording.wav")

        try:
            transcript = transcribe_audio_bytes(audio_file.read(), original_name)
        except UnsupportedAudioFormatError as exc:
            return jsonify({"error": str(exc)}), 400
        except RecordingTooLongError as exc:
            return jsonify({"error": str(exc)}), 400

        if not transcript:
            return jsonify({"error": "Could not transcribe any speech from the recording"}), 422

        reply = agent.send(transcript)

        try:
            audio_reply_path = synthesize_speech(reply)
            audio_reply_url = f"/api/audio/{audio_reply_path.name}"
        except Exception:
            logger.exception("Text-to-speech synthesis failed; returning text-only reply")
            audio_reply_url = None

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

    # ------------------------------------------------------
    # Synthesized audio playback
    # ------------------------------------------------------

    @app.route("/api/audio/<file_name>", methods=["GET"])
    def get_audio(file_name):
        safe_name = secure_filename(file_name)
        path = AUDIO_OUTPUT_FOLDER / safe_name
        if not path.exists():
            return jsonify({"error": "Audio file not found"}), 404
        return send_file(path)

    # ------------------------------------------------------
    # Maintenance records
    # ------------------------------------------------------

    @app.route("/api/records", methods=["GET"])
    def get_records():
        aircraft_reg = request.args.get("aircraft_reg")
        limit = int(request.args.get("limit", 50))
        records = list_maintenance_records(aircraft_reg=aircraft_reg, limit=limit)
        return jsonify({"records": records, "count": len(records)})

    @app.route("/api/records/<record_id>", methods=["GET"])
    def get_record(record_id):
        record = get_maintenance_record(record_id)
        if not record:
            return jsonify({"error": f"No maintenance record found with id '{record_id}'"}), 404

        include_conversation = request.args.get("include_conversation", "true").lower() == "true"
        payload = {"record": record}
        if include_conversation:
            payload["conversation"] = get_conversation(record_id)

        return jsonify(payload)

    # ------------------------------------------------------
    # PDF reports
    # ------------------------------------------------------

    @app.route("/api/records/<record_id>/report", methods=["POST", "GET"])
    def get_report(record_id):
        """
        Generate (or regenerate) the PDF report for a maintenance
        record and stream it back to the caller.
        """
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


# ==========================================================
# Error Handlers
# ==========================================================

def register_error_handlers(app: Flask) -> None:

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def server_error(error):
        logger.exception("Unhandled server error")
        return jsonify({"error": "Internal server error"}), 500


# ==========================================================
# Entry Point
# ==========================================================

app = create_app()

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=DEBUG)