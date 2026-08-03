"""
==============================================================
AI Maintenance Voice Copilot
Backend Package
--------------------------------------------------------------

This package contains the full backend implementation of the
AI Maintenance Voice Copilot:

    config.py           Central configuration (env vars, folders, constants)
    database.py         SAP HANA Cloud connection + queries
    auth.py             Login, password hashing, role gating
    embeddings.py       Text -> vectors via SAP AI Core
    agent.py            Conversational agent (chat model in AI Core)
    speech_to_text.py   Speech recognition (Gemini in AI Core)
    text_to_speech.py   Speech synthesis (Piper, local)
    pdf_service.py      Maintenance report (PDF) generation
    scripts/            One-off / operational scripts

Model hosting
-------------
Embeddings, chat and speech-to-text are all served by deployments
in SAP AI Core through the Generative AI Hub. Text-to-speech runs
locally with Piper, because the hub offers no TTS model. Nothing
here talks to Azure OpenAI.

Only config.py is allowed to read environment variables directly
(via os.getenv). Every other module - including this one - should
import already-resolved settings from backend.config.
==============================================================
"""

from backend.config import APP_NAME, APP_VERSION

__all__ = ["APP_NAME", "APP_VERSION"]

__version__ = APP_VERSION