"""
==============================================================
AI Maintenance Voice Copilot
Voice Processing Layer - Text-to-Speech
--------------------------------------------------------------

Purpose
-------
Convert the AI copilot's written replies (from backend.agent)
back into spoken audio, so a technician never has to look at a
screen mid-inspection.

Responsibilities
----------------
• Synthesize speech for a given piece of text via Azure OpenAI's
  TTS deployment (same Azure OpenAI resource used for chat/embeddings)
• Save the resulting audio under AUDIO_OUTPUT_FOLDER
• Optionally stream/return raw audio bytes for a caller that
  wants to play it immediately without touching disk

IMPORTANT
---------
This module never reads environment variables directly.
All settings come from backend.config.

Example
-------
    from backend.text_to_speech import synthesize_speech

    audio_path = synthesize_speech("Corrosion finding on VT-ABC has been saved.")
    print(audio_path)
==============================================================
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from openai import AzureOpenAI

from backend.config import (
    AZURE_OPENAI_URL,
    AZURE_API_KEY,
    AZURE_API_VERSION,
    AZURE_TTS_MODEL,
    AZURE_TTS_VOICE,
    AUDIO_OUTPUT_FOLDER,
    LOG_LEVEL,
)

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("mro_copilot.text_to_speech")

# Audio container format written to disk / returned as bytes.
DEFAULT_OUTPUT_FORMAT = "mp3"

SUPPORTED_OUTPUT_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}


class UnsupportedOutputFormatError(ValueError):
    """Raised when an unsupported TTS output format is requested."""


# ==========================================================
# Client
# ==========================================================

def get_azure_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_URL,
        api_key=AZURE_API_KEY,
        api_version=AZURE_API_VERSION,
    )


# ==========================================================
# Synthesis
# ==========================================================

def synthesize_speech_bytes(
    text: str,
    voice: Optional[str] = None,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
) -> bytes:
    """
    Synthesize `text` into speech and return the raw audio bytes
    without writing anything to disk. Useful when the caller
    wants to stream the response straight back over HTTP/WebSocket.
    """
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise UnsupportedOutputFormatError(
            f"'{output_format}' is not supported. "
            f"Supported formats: {', '.join(sorted(SUPPORTED_OUTPUT_FORMATS))}"
        )

    if not text or not text.strip():
        raise ValueError("Cannot synthesize speech for empty text")

    client = get_azure_client()
    logger.info(
        "Synthesizing speech (%d chars, voice=%s, format=%s)",
        len(text),
        voice or AZURE_TTS_VOICE,
        output_format,
    )

    response = client.audio.speech.create(
        model=AZURE_TTS_MODEL,
        voice=voice or AZURE_TTS_VOICE,
        input=text,
        response_format=output_format,
    )

    return response.read()


def synthesize_speech(
    text: str,
    voice: Optional[str] = None,
    output_format: str = DEFAULT_OUTPUT_FORMAT,
    file_name: Optional[str] = None,
) -> Path:
    """
    Synthesize `text` into speech and save it under
    AUDIO_OUTPUT_FOLDER. Returns the path to the saved file.

    Pass `file_name` to control the output file name (extension
    is derived from `output_format` if not already present);
    otherwise a unique name is generated.
    """
    audio_bytes = synthesize_speech_bytes(text, voice=voice, output_format=output_format)

    if file_name:
        stem = Path(file_name).stem
    else:
        stem = str(uuid.uuid4())

    destination = AUDIO_OUTPUT_FOLDER / f"{stem}.{output_format}"
    destination.write_bytes(audio_bytes)

    logger.info("Saved synthesized speech to %s", destination)
    return destination
