"""
==============================================================
AI Maintenance Voice Copilot
Voice Processing Layer - Speech-to-Text
--------------------------------------------------------------

Purpose
-------
Convert technician speech (recorded audio) into text so it can
be handed to backend.agent.MaintenanceAgent for understanding.

Responsibilities
----------------
• Validate incoming audio (format, duration where determinable)
• Transcribe audio files/bytes via Azure OpenAI's Whisper
  deployment (same Azure OpenAI resource used for chat/embeddings)
• Persist uploaded recordings under UPLOADS_FOLDER

IMPORTANT
---------
This module never reads environment variables directly.
All settings come from backend.config.

Example
-------
    from backend.speech_to_text import transcribe_audio_file

    text = transcribe_audio_file("uploads/note_001.wav")
    print(text)
==============================================================
"""

from __future__ import annotations

import io
import logging
import uuid
import wave
from pathlib import Path
from typing import Optional, Union

from openai import AzureOpenAI

from backend.config import (
    AZURE_OPENAI_URL,
    AZURE_API_KEY,
    AZURE_API_VERSION,
    AZURE_STT_MODEL,
    UPLOADS_FOLDER,
    SUPPORTED_AUDIO_FORMATS,
    MAX_RECORDING_DURATION,
    LOG_LEVEL,
)

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("mro_copilot.speech_to_text")


class UnsupportedAudioFormatError(ValueError):
    """Raised when an audio file's extension is not in SUPPORTED_AUDIO_FORMATS."""


class RecordingTooLongError(ValueError):
    """Raised when a recording exceeds MAX_RECORDING_DURATION seconds."""


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
# Validation
# ==========================================================

def validate_audio_format(file_name: str) -> str:
    """
    Confirm the file extension is one we accept. Returns the
    normalised (lowercase, no dot) extension on success.
    """
    suffix = Path(file_name).suffix.lower().lstrip(".")
    if suffix not in SUPPORTED_AUDIO_FORMATS:
        raise UnsupportedAudioFormatError(
            f"'.{suffix}' is not a supported audio format. "
            f"Supported formats: {', '.join(SUPPORTED_AUDIO_FORMATS)}"
        )
    return suffix


def get_wav_duration_seconds(path: Path) -> Optional[float]:
    """
    Best-effort duration check. Only works for .wav files, since
    that's the one format the stdlib can inspect without extra
    dependencies. Returns None for any other format (duration is
    simply not enforced in that case).
    """
    if path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            return frames / float(rate) if rate else None
    except wave.Error:
        logger.warning("Could not read WAV header for %s to check duration", path.name)
        return None


def validate_recording_duration(path: Path) -> None:
    duration = get_wav_duration_seconds(path)
    if duration is not None and duration > MAX_RECORDING_DURATION:
        raise RecordingTooLongError(
            f"Recording is {duration:.1f}s, which exceeds the "
            f"{MAX_RECORDING_DURATION}s limit."
        )


# ==========================================================
# Persisting uploaded audio
# ==========================================================

def save_uploaded_audio(audio_bytes: bytes, original_file_name: str) -> Path:
    """
    Save raw uploaded audio bytes under UPLOADS_FOLDER with a
    unique file name, preserving the original extension.
    """
    suffix = validate_audio_format(original_file_name)
    destination = UPLOADS_FOLDER / f"{uuid.uuid4()}.{suffix}"
    destination.write_bytes(audio_bytes)
    logger.info("Saved uploaded audio to %s", destination)
    return destination


# ==========================================================
# Transcription
# ==========================================================

def transcribe_audio_file(path: Union[str, Path], language: Optional[str] = None) -> str:
    """
    Transcribe an audio file already on disk and return the
    recognised text.
    """
    path = Path(path)
    validate_audio_format(path.name)
    validate_recording_duration(path)

    client = get_azure_client()
    logger.info("Transcribing %s via Azure OpenAI (%s)", path.name, AZURE_STT_MODEL)

    with open(path, "rb") as audio_file:
        kwargs = {"model": AZURE_STT_MODEL, "file": audio_file}
        if language:
            kwargs["language"] = language
        response = client.audio.transcriptions.create(**kwargs)

    text = (response.text or "").strip()
    logger.info("Transcription complete (%d characters)", len(text))
    return text


def transcribe_audio_bytes(
    audio_bytes: bytes,
    original_file_name: str,
    persist: bool = True,
    language: Optional[str] = None,
) -> str:
    """
    Transcribe raw audio bytes (e.g. straight from an HTTP upload
    or a browser microphone capture) without requiring the caller
    to manage a temp file themselves.

    If `persist` is True (default), the audio is also saved under
    UPLOADS_FOLDER for auditing/replay purposes.
    """
    suffix = validate_audio_format(original_file_name)

    if persist:
        path = save_uploaded_audio(audio_bytes, original_file_name)
        return transcribe_audio_file(path, language=language)

    # Transient transcription without saving to disk.
    client = get_azure_client()
    buffer = io.BytesIO(audio_bytes)
    buffer.name = f"audio.{suffix}"  # the SDK reads this for content-type hints

    kwargs = {"model": AZURE_STT_MODEL, "file": buffer}
    if language:
        kwargs["language"] = language

    response = client.audio.transcriptions.create(**kwargs)
    return (response.text or "").strip()
