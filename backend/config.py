"""
==============================================================

AI Maintenance Voice Copilot

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
• Store SAP HANA configuration
• Store Azure OpenAI configuration
• Store application constants

IMPORTANT
---------
No other file should directly use os.getenv().

Every module should simply import from config.py.

Example:

from backend.config import HANA_HOST

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

# Create folders automatically

for folder in (
    MANUALS_FOLDER,
    REPORTS_FOLDER,
    UPLOADS_FOLDER,
    LOG_FOLDER,
    AUDIO_OUTPUT_FOLDER,
):
    folder.mkdir(parents=True, exist_ok=True)

# ==========================================================
# Application
# ==========================================================

APP_NAME = "AI Maintenance Voice Copilot"

APP_VERSION = "1.0.0"

DEBUG = True

HOST = "0.0.0.0"

PORT = 5000

SECRET_KEY = os.getenv("SECRET_KEY", "")

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

# Optional

HANA_DRIVER = os.getenv("HANA_DRIVER", "")

HANA_URL = os.getenv("HANA_URL", "")

HANA_HDI_USER = os.getenv("HANA_HDI_USER", "")

HANA_HDI_PASSWORD = os.getenv("HANA_HDI_PASSWORD", "")

# ==========================================================
# Azure OpenAI
# ==========================================================

AZURE_OPENAI_URL = os.getenv("AZURE_OPENAI_URL", "")

AZURE_API_KEY = os.getenv("AZURE_API_KEY", "")

# If later you move to separate deployments,
# these values are already available.

AZURE_CHAT_MODEL = os.getenv(
    "AZURE_CHAT_MODEL",
    "gpt-4.1"
)

AZURE_EMBEDDING_MODEL = os.getenv(
    "AZURE_EMBEDDING_MODEL",
    "text-embedding-3-small"
)

AZURE_API_VERSION = os.getenv(
    "AZURE_API_VERSION",
    "2024-05-01-preview"
)

# Azure OpenAI audio deployments (Speech-to-Text / Text-to-Speech).
# These are separate deployment names on the same Azure OpenAI
# resource defined above (same AZURE_OPENAI_URL / AZURE_API_KEY).

AZURE_STT_MODEL = os.getenv(
    "AZURE_STT_MODEL",
    "whisper-1"
)

AZURE_TTS_MODEL = os.getenv(
    "AZURE_TTS_MODEL",
    "tts-1"
)

AZURE_TTS_VOICE = os.getenv(
    "AZURE_TTS_VOICE",
    "alloy"
)

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

# ==========================================================
# Manual Ingestion
# ==========================================================

CHUNK_SIZE = 1200

CHUNK_OVERLAP = 200

TOP_K_RESULTS = 5

# ==========================================================
# PDF Settings
# ==========================================================

PDF_FONT = "Helvetica"

PDF_TITLE = "Aircraft Maintenance Report"

# ==========================================================
# Logging
# ==========================================================

LOG_LEVEL = "INFO"

LOG_FILE = LOG_FOLDER / "application.log"

# ==========================================================
# Validate Required Environment Variables
# ==========================================================

REQUIRED_ENV_VARS = {
    "HANA_HOST": HANA_HOST,
    "HANA_USER": HANA_USER,
    "HANA_PASSWORD": HANA_PASSWORD,
    "HANA_SCHEMA": HANA_SCHEMA,
    "AZURE_OPENAI_URL": AZURE_OPENAI_URL,
    "AZURE_API_KEY": AZURE_API_KEY,
    "SECRET_KEY": SECRET_KEY,
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