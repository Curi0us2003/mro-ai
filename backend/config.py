"""
==============================================================

AI Maintenance Voice Copilot

Configuration Module

--------------------------------------------------------------

Purpose

This file loads all application configuration from the .env file.

No other file should directly access environment variables.

Every module should simply import the values from config.py.

Example

from config import HANA_HOST

==============================================================
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ------------------------------------------------------------
# Load .env
# ------------------------------------------------------------

load_dotenv()

# ============================================================
# Project Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

UPLOAD_FOLDER = BASE_DIR / "uploads"
REPORT_FOLDER = BASE_DIR / "generated_reports"
MANUAL_FOLDER = BASE_DIR / "manuals"
LOG_FOLDER = BASE_DIR / "logs"

UPLOAD_FOLDER.mkdir(exist_ok=True)
REPORT_FOLDER.mkdir(exist_ok=True)
MANUAL_FOLDER.mkdir(exist_ok=True)
LOG_FOLDER.mkdir(exist_ok=True)

# ============================================================
# Application
# ============================================================

APP_NAME = "AI Maintenance Voice Copilot"

DEBUG = True

# ============================================================
# Security
# ============================================================

SECRET_KEY = os.getenv("SECRET_KEY")

# ============================================================
# SAP HANA Cloud
# ============================================================

HANA_HOST = os.getenv("HANA_HOST")

HANA_PORT = int(os.getenv("HANA_PORT", 443))

HANA_USER = os.getenv("HANA_USER")

HANA_PASSWORD = os.getenv("HANA_PASSWORD")

HANA_SCHEMA = os.getenv("HANA_SCHEMA")

HANA_ENCRYPT = os.getenv("HANA_ENCRYPT", "true")

# Optional JDBC

HANA_DRIVER = os.getenv("HANA_DRIVER")

HANA_URL = os.getenv("HANA_URL")

# Optional HDI

HANA_HDI_USER = os.getenv("HANA_HDI_USER")

HANA_HDI_PASSWORD = os.getenv("HANA_HDI_PASSWORD")

# ============================================================
# Azure OpenAI
# ============================================================

AZURE_OPENAI_URL = os.getenv("AZURE_OPENAI_URL")

AZURE_API_KEY = os.getenv("AZURE_API_KEY")