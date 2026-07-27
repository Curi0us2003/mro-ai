"""
==============================================================
AI Maintenance Voice Copilot
Backend Package
--------------------------------------------------------------

This package contains the full backend implementation of the
AI Maintenance Voice Copilot:

    config.py           Central configuration (env vars, folders, constants)
    database.py         SAP HANA Cloud connection + schema + queries
    agent.py            Azure OpenAI powered conversational agent
    speech_to_text.py   Speech recognition
    text_to_speech.py   Speech synthesis
    pdf_service.py      Maintenance report (PDF) generation
    scripts/            One-off / maintenance scripts (e.g. manual ingestion)

Only config.py is allowed to read environment variables directly
(via os.getenv). Every other module - including this one - should
import already-resolved settings from backend.config.
==============================================================
"""

from backend.config import APP_NAME, APP_VERSION

__all__ = ["APP_NAME", "APP_VERSION"]

__version__ = APP_VERSION