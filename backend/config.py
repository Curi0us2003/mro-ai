"""
==============================================================
AI Maintenance Voice Assistant
Configuration Module
--------------------------------------------------------------

Purpose
-------
Central configuration file for the entire application.

Responsibilities
----------------
• Load environment variables from .env
• Define project directories
• Create required folders if they don't exist
• Store SAP HANA Cloud configuration
• Store SAP AI Core / Generative AI Hub configuration
• Store Piper text-to-speech configuration
• Store application constants

IMPORTANT
---------
No other file should directly use os.getenv().

Every module should simply import from config.py.

Example:

    from backend.config import HANA_HOST

Model hosting
-------------
Every model now comes from SAP BTP:

    embeddings      text-embedding-3-large   (AI Core / Gen AI Hub)
    chat            gpt-4.1                  (AI Core / Gen AI Hub)
    speech-to-text  gemini-2.5-flash         (AI Core / Gen AI Hub)

Text-to-speech runs locally with Piper, because the generative
AI hub does not offer a text-to-speech model. Nothing in this
application talks to Azure OpenAI any more.
==============================================================
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ==========================================================
# Project Root
# ==========================================================

# backend/
BACKEND_DIR = Path(__file__).resolve().parent

# mro-voice-copilot/
PROJECT_ROOT = BACKEND_DIR.parent

# ==========================================================
# Load .env
# ==========================================================

ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(dotenv_path=ENV_FILE)

# ==========================================================
# Project Folders
# ==========================================================

MANUALS_FOLDER = PROJECT_ROOT / "manuals"

REPORTS_FOLDER = PROJECT_ROOT / "generated_reports"

UPLOADS_FOLDER = PROJECT_ROOT / "uploads"

LOG_FOLDER = PROJECT_ROOT / "logs"

FRONTEND_FOLDER = PROJECT_ROOT / "frontend"

# Generated voice replies (text-to-speech output) live here,
# separate from technician-uploaded recordings in UPLOADS_FOLDER.
AUDIO_OUTPUT_FOLDER = PROJECT_ROOT / "audio_output"

# Piper voice models (.onnx + .onnx.json) live here.
VOICES_FOLDER = PROJECT_ROOT / "voices"

# Self-signed dev HTTPS certificate (see HTTPS_ADHOC below) lives here,
# generated once and reused - not regenerated on every server restart.
CERTS_FOLDER = PROJECT_ROOT / "certs"

# Create folders automatically

for folder in (
    MANUALS_FOLDER,
    REPORTS_FOLDER,
    UPLOADS_FOLDER,
    LOG_FOLDER,
    AUDIO_OUTPUT_FOLDER,
    VOICES_FOLDER,
    CERTS_FOLDER,
):
    folder.mkdir(parents=True, exist_ok=True)

# ==========================================================
# Application
# ==========================================================

APP_NAME = "AI Maintenance Voice Assistant"

APP_VERSION = "2.0.0"

DEBUG = os.getenv("DEBUG", "true").lower() == "true"

HOST = "0.0.0.0"

PORT = int(os.getenv("PORT", "5000"))

SECRET_KEY = os.getenv("SECRET_KEY", "")

# ==========================================================
# Authentication / Sessions
# ==========================================================
# Login sessions are signed cookies (Flask's own session, signed
# with SECRET_KEY). There is no self-service registration: user
# accounts are created only by an administrator running
#     python -m backend.scripts.manage_users add ...

SESSION_LIFETIME_HOURS = int(os.getenv("SESSION_LIFETIME_HOURS", "12"))

# Set to true once you serve the app over HTTPS.
SESSION_COOKIE_SECURE = (
    os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
)

# Browsers only expose the microphone (getUserMedia/MediaRecorder) on
# a "secure context" - HTTPS, or http://localhost on the same machine.
# A technician reaching this app from a phone/tablet over the hangar
# LAN needs HTTPS, so `python -m backend.app` serves over a self-signed
# certificate by default, generated once into CERTS_FOLDER and reused.
#
# Deliberately NOT Werkzeug's ssl_context="adhoc": that regenerates a
# brand-new certificate every time the process (re)starts, and with
# DEBUG=true the reloader restarts on its own - each restart would
# invalidate the "proceed anyway" exception the browser just remembered,
# so the page would look like it never loads.
#
# Browsers still show a one-time certificate warning to click through,
# but only once per certificate (i.e. until CERTS_FOLDER is deleted).
# Set to false to fall back to plain HTTP (fine for same-machine/
# localhost use, where the browser already treats it as secure).
HTTPS_ADHOC = os.getenv("HTTPS_ADHOC", "true").lower() == "true"

# ==========================================================
# Startup warmup
# ==========================================================
# Three things are expensive exactly once per process and were
# being paid for by whoever happened to send the first voice turn:
#
#   Piper voice load          ~4.0s  (parses a ~50 MB ONNX graph)
#   Piper first synthesis     ~5.2s  (ONNX runtime warmup; ~0.1s after)
#   AI Core proxy client init ~4.2s  (OAuth token + deployment lookup)
#
# Doing them in a background thread at startup moves ~13s off the
# first reply. The thread is a daemon and swallows its own errors -
# a warmup failure must never stop the app from serving.
WARMUP_ON_STARTUP = os.getenv("WARMUP_ON_STARTUP", "true").lower() == "true"

ROLE_TECHNICIAN = "TECHNICIAN"

ROLE_SUPERVISOR = "SUPERVISOR"

VALID_ROLES = (ROLE_TECHNICIAN, ROLE_SUPERVISOR)

# ==========================================================
# SAP HANA Cloud
# ==========================================================

HANA_HOST = os.getenv("HANA_HOST", "")

HANA_PORT = int(os.getenv("HANA_PORT", "443"))

HANA_USER = os.getenv("HANA_USER", "")

HANA_PASSWORD = os.getenv("HANA_PASSWORD", "")

HANA_SCHEMA = os.getenv("HANA_SCHEMA", "")

HANA_ENCRYPT = (
    os.getenv("HANA_ENCRYPT", "true").lower() == "true"
)

# How many open connections backend.database keeps in its pool.
#
# Opening a connection to HANA Cloud costs a TCP connect plus a full
# TLS handshake - measured at ~3.7s from a developer machine, versus
# ~0.2s for a query on an already-open connection. Since a single
# voice turn hits the database several times, connections are pooled
# and reused rather than dialled per query.
#
# Size this at or above the number of concurrent request threads that
# touch the database; anything beyond that is just idle sockets.
HANA_POOL_SIZE = int(os.getenv("HANA_POOL_SIZE", "8"))

# Optional

HANA_DRIVER = os.getenv("HANA_DRIVER", "")

HANA_URL = os.getenv("HANA_URL", "")

HANA_HDI_USER = os.getenv("HANA_HDI_USER", "")

HANA_HDI_PASSWORD = os.getenv("HANA_HDI_PASSWORD", "")

# ==========================================================
# SAP AI Core / Generative AI Hub
# ==========================================================
# From the service key of your "aicore" service instance:
#     clientid                       -> AICORE_CLIENT_ID
#     clientsecret                   -> AICORE_CLIENT_SECRET
#     url                            -> AICORE_AUTH_URL
#     serviceurls.AI_API_URL + "/v2" -> AICORE_BASE_URL

AICORE_CLIENT_ID = os.getenv("AICORE_CLIENT_ID", "")

AICORE_CLIENT_SECRET = os.getenv("AICORE_CLIENT_SECRET", "")

AICORE_AUTH_URL = os.getenv("AICORE_AUTH_URL", "")

AICORE_BASE_URL = os.getenv("AICORE_BASE_URL", "")

AICORE_RESOURCE_GROUP = os.getenv("AICORE_RESOURCE_GROUP", "default")

# generative-ai-hub-sdk reads its credentials from the process
# environment when it creates a proxy client. This module is the
# only place in the project allowed to write environment variables,
# for the same reason it is the only place allowed to read them.

for _key, _value in (
    ("AICORE_CLIENT_ID", AICORE_CLIENT_ID),
    ("AICORE_CLIENT_SECRET", AICORE_CLIENT_SECRET),
    ("AICORE_AUTH_URL", AICORE_AUTH_URL),
    ("AICORE_BASE_URL", AICORE_BASE_URL),
    ("AICORE_RESOURCE_GROUP", AICORE_RESOURCE_GROUP),
):
    if _value:
        os.environ[_key] = _value

# ----------------------------------------------------------
# Models deployed in the generative AI hub
# ----------------------------------------------------------
# Each must exist as a RUNNING deployment in AI Core. Being
# listed in the model catalogue is not enough.

AICORE_CHAT_MODEL = os.getenv("AICORE_CHAT_MODEL", "gpt-4.1")

AICORE_EMBEDDING_MODEL = os.getenv(
    "AICORE_EMBEDDING_MODEL",
    "text-embedding-3-large",
)

# Gemini is the only speech-capable family in the hub. This must
# match a RUNNING deployment's model name exactly - unlike the openai
# proxy used for chat/embeddings, the google_vertexai proxy used here
# does not resolve looser aliases.
AICORE_STT_MODEL = os.getenv("AICORE_STT_MODEL", "gemini-3.5-flash")

# Optional: target a deployment directly by its 16-character ID
# (shown as the deployment title in AI Launchpad) instead of
# letting the SDK resolve the model by name.

AICORE_CHAT_DEPLOYMENT_ID = os.getenv("AICORE_CHAT_DEPLOYMENT_ID", "")

AICORE_EMBEDDING_DEPLOYMENT_ID = os.getenv("AICORE_EMBEDDING_DEPLOYMENT_ID", "")

AICORE_STT_DEPLOYMENT_ID = os.getenv("AICORE_STT_DEPLOYMENT_ID", "")

# ----------------------------------------------------------
# Embedding vector width
# ----------------------------------------------------------
# text-embedding-3-large natively returns 3072 dimensions but
# supports truncation, so 1536 halves storage at almost no
# retrieval-quality cost. This value MUST match the width of
# MANUAL_CHUNKS.EMBEDDING in schema.sql - REAL_VECTOR width
# cannot be altered after the table is created.

EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

# How many chunks to send per embedding request during ingestion.
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))

# ==========================================================
# Voice Configuration
# ==========================================================

AUDIO_SAMPLE_RATE = 16000

AUDIO_CHANNELS = 1

MAX_RECORDING_DURATION = 120  # seconds

SUPPORTED_AUDIO_FORMATS = [
    "wav",
    "mp3",
    "m4a",
    "ogg",
    "webm",
]

# ----------------------------------------------------------
# Piper text-to-speech (runs locally, no cloud call)
# ----------------------------------------------------------
# Download a voice from the Piper voices release and drop both
# files into the voices/ folder:
#     en_US-lessac-medium.onnx
#     en_US-lessac-medium.onnx.json

PIPER_VOICE_NAME = os.getenv("PIPER_VOICE_NAME", "en_US-lessac-medium")

PIPER_MODEL_PATH = Path(
    os.getenv("PIPER_MODEL_PATH", str(VOICES_FOLDER / f"{PIPER_VOICE_NAME}.onnx"))
)

PIPER_CONFIG_PATH = Path(
    os.getenv("PIPER_CONFIG_PATH", str(PIPER_MODEL_PATH) + ".json")
)

# Speaking rate. Piper's length_scale is inverse to speed:
# higher = slower. 1.0 is the voice's natural pace.
PIPER_LENGTH_SCALE = float(os.getenv("PIPER_LENGTH_SCALE", "1.0"))

# Extra silence inserted at sentence boundaries, in seconds.
PIPER_SENTENCE_SILENCE = float(os.getenv("PIPER_SENTENCE_SILENCE", "0.35"))

# Piper writes WAV. Keep it - the browser plays it natively and
# there is no transcoding dependency.
TTS_OUTPUT_FORMAT = "wav"

# ==========================================================
# Damage Photos
# ==========================================================
# Optional evidence a technician attaches to a finding, either from
# the device camera or a file. Stored in HANA (RECORD_PHOTOS - see
# schema_record_photos.sql) and reproduced in the PDF report.

SUPPORTED_PHOTO_FORMATS = {"jpg", "jpeg", "png", "webp", "heic", "heif", "bmp", "gif"}

# Cap on what a client may post, before processing. Phone cameras
# produce 3-8 MB per shot; this leaves room for a couple of those
# while still rejecting an accidental video upload.
PHOTO_MAX_UPLOAD_BYTES = int(os.getenv("PHOTO_MAX_UPLOAD_BYTES", str(12 * 1024 * 1024)))

# Long-edge limit after processing. 1600px is plenty for judging
# corrosion on screen and for a full-width PDF plate, at a fraction
# of the storage and transfer of the original.
PHOTO_MAX_DIMENSION = int(os.getenv("PHOTO_MAX_DIMENSION", "1600"))

PHOTO_JPEG_QUALITY = int(os.getenv("PHOTO_JPEG_QUALITY", "82"))

# How many photos one finding may carry.
PHOTO_MAX_PER_RECORD = int(os.getenv("PHOTO_MAX_PER_RECORD", "8"))

# ==========================================================
# Manual Ingestion
# ==========================================================

CHUNK_SIZE = 1200

CHUNK_OVERLAP = 200

TOP_K_RESULTS = 5

# Retrieved chunks scoring below this are treated as irrelevant.
# Cosine similarity always returns the top-k, even when nothing
# in the manuals is actually related to the question.
MIN_RELEVANCE_SCORE = float(os.getenv("MIN_RELEVANCE_SCORE", "0.30"))

# ==========================================================
# PDF Settings
# ==========================================================

PDF_FONT = "Helvetica"

PDF_TITLE = "Aircraft Maintenance Report"

# ==========================================================
# Azure Blob Storage (SAP posting)
# ==========================================================
# Where a COMPLETE record's PDF report is uploaded when a supervisor posts
# it to SAP - after which the record becomes CLOSED and immutable. Same
# storage account/container as the OnM-MRP-Data-Loading loader
# (github.com/Curi0us2003/OnM-MRP-Data-Loading), which calls this
# connection "SFTP" even though the transport is the Azure Blob SDK, not
# the SFTP protocol - kept consistent with that naming here too. That
# loader's SAS token is read-only; this one needs write permission.
#
# Optional at import time (unlike HANA/AI Core below): the rest of the app
# works without it, and Post to SAP simply reports it isn't configured yet.

AZURE_STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")

AZURE_STORAGE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER", "")

AZURE_STORAGE_SAS_TOKEN = os.getenv("AZURE_STORAGE_SAS_TOKEN", "")

# Keeps posted PDFs separate from the MRP loader's own files in the same
# container.
AZURE_STORAGE_SAP_PREFIX = os.getenv("AZURE_STORAGE_SAP_PREFIX", "sap-postings/")

# ==========================================================
# Logging
# ==========================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

LOG_FILE = LOG_FOLDER / "application.log"

# ==========================================================
# Validate Required Environment Variables
# ==========================================================

REQUIRED_ENV_VARS = {
    "HANA_HOST": HANA_HOST,
    "HANA_USER": HANA_USER,
    "HANA_PASSWORD": HANA_PASSWORD,
    "HANA_SCHEMA": HANA_SCHEMA,
    "SECRET_KEY": SECRET_KEY,
    "AICORE_CLIENT_ID": AICORE_CLIENT_ID,
    "AICORE_CLIENT_SECRET": AICORE_CLIENT_SECRET,
    "AICORE_AUTH_URL": AICORE_AUTH_URL,
    "AICORE_BASE_URL": AICORE_BASE_URL,
}

missing = [
    key
    for key, value in REQUIRED_ENV_VARS.items()
    if not value
]

if missing:
    raise EnvironmentError(
        f"Missing required environment variables: "
        f"{', '.join(missing)}"
    )