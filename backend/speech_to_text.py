"""
==============================================================
AI Maintenance Voice Copilot
Voice Processing Layer - Speech-to-Text
--------------------------------------------------------------

Purpose
-------
Convert technician speech (recorded audio) into text so it can
be handed to backend.agent.MaintenanceAgent for understanding.

Provider
--------
Gemini, served through SAP AI Core / Generative AI Hub. Gemini
is the only speech-capable model family in the hub - there is no
Whisper deployment available there - so transcription is done by
a multimodal model taking the audio as inline data.

The installed generative-ai-hub-sdk build exposes Gemini only
through its `google_vertexai` native client (a drop-in for
`vertexai.generative_models.GenerativeModel`) - it has no
`google_genai` client at all, so that's the API used below.

Because we lose Whisper's `prompt` parameter (which biased the
decoder toward supplied vocabulary), the domain priming happens
in the system instruction instead: we tell the model it is
transcribing aviation maintenance speech and show it the shape of
the identifiers it should expect. That recovers most of the
accuracy on strings like "MS21042L3" or "ATA 32-41", which is
exactly where generic ASR falls over.

Responsibilities
----------------
• Validate incoming audio (format, duration where determinable)
• Transcribe audio files/bytes via the deployed Gemini model
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

import logging
import mimetypes
import threading
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional, Union

from backend.config import (
    AICORE_STT_MODEL,
    AICORE_STT_DEPLOYMENT_ID,
    UPLOADS_FOLDER,
    SUPPORTED_AUDIO_FORMATS,
    MAX_RECORDING_DURATION,
    LOG_LEVEL,
)

logger = logging.getLogger("mro_copilot.speech_to_text")
logger.setLevel(LOG_LEVEL)


class UnsupportedAudioFormatError(ValueError):
    """Raised when an audio file's extension is not in SUPPORTED_AUDIO_FORMATS."""


class RecordingTooLongError(ValueError):
    """Raised when a recording exceeds MAX_RECORDING_DURATION seconds."""


class TranscriptionError(RuntimeError):
    """Raised when the speech-to-text provider fails."""


# ==========================================================
# Domain priming
# ==========================================================

TRANSCRIPTION_INSTRUCTION = """\
You are transcribing speech recorded by an aircraft maintenance technician,
often in a noisy hangar.

Transcribe exactly what was said. Output ONLY the transcript - no preamble,
no commentary, no speaker labels, no timestamps, no quotation marks.

Expect and preserve this vocabulary precisely:
- Aircraft registrations: two to three letters, hyphen, letters, e.g. VT-ABC, N737QA
- ATA chapter references spoken as digits, e.g. "ATA 32-41", "chapter 27 dash 51"
- Part numbers mixing letters and digits with no spaces, e.g. MS21042L3, NAS1149F0332P
- Torque values with units, e.g. "45 newton metres", "120 inch pounds"
- Component names: actuator, bushing, fairing, spar, stringer, longeron, nacelle,
  pylon, empennage, aileron, elevator, rudder, slat, flap, strut, bogie
- Defect terms: corrosion, delamination, fretting, chafing, crazing, spalling,
  scoring, pitting, exfoliation, hydraulic seepage, fuel weep

Write alphanumeric part numbers with no internal spaces. Write registrations
with a hyphen. Keep numbers as digits. If a passage is genuinely inaudible,
write [inaudible] rather than guessing.
"""


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


def guess_mime_type(file_name: str) -> str:
    """Map an audio file name to the MIME type Gemini expects."""
    suffix = Path(file_name).suffix.lower().lstrip(".")
    explicit = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "ogg": "audio/ogg",
        "webm": "audio/webm",
    }
    if suffix in explicit:
        return explicit[suffix]
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"


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


# Archiving the upload is for auditing and replay - the transcript does
# not depend on it, so it must not sit in front of the technician's reply.
_archive_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audioarchive")


def _persist_audio_async(audio_bytes: bytes, original_file_name: str) -> None:
    """Archive an upload under UPLOADS_FOLDER without blocking the caller."""

    def _report(future):
        exc = future.exception()
        if exc:
            logger.warning("Could not archive an uploaded recording: %s", exc)

    _archive_writer.submit(
        save_uploaded_audio, audio_bytes, original_file_name
    ).add_done_callback(_report)


# ==========================================================
# Provider call
# ==========================================================

_model_cache: dict[str, Any] = {}
_model_lock = threading.Lock()


def _build_model(system_instruction: str):
    """
    Gemini model wired through the AI Core proxy, built once per
    distinct system instruction and reused after that.

    Constructing one resolves an AI Core proxy client - an OAuth token
    fetch plus a deployment lookup, several seconds on a cold process -
    so rebuilding it per recording put that cost into every single
    voice turn. The instruction only varies by spoken language, so the
    cache stays tiny in practice.

    Unlike the openai-compatible proxy used for chat/embeddings,
    google_vertexai's GenerativeModel needs a real `model_name` to build
    its request URI even when `deployment_id` is also given to pin the
    exact AI Core deployment - passing deployment_id alone leaves the
    underlying vertexai SDK with an empty model name and every call
    fails with an "Invalid request" URI-templating error.
    """
    cached = _model_cache.get(system_instruction)
    if cached is not None:
        return cached

    with _model_lock:
        cached = _model_cache.get(system_instruction)
        if cached is not None:
            return cached

        from gen_ai_hub.proxy.native.google_vertexai.clients import GenerativeModel

        kwargs: dict = {
            "model_name": AICORE_STT_MODEL,
            "system_instruction": system_instruction,
        }
        if AICORE_STT_DEPLOYMENT_ID:
            kwargs["deployment_id"] = AICORE_STT_DEPLOYMENT_ID

        model = GenerativeModel(**kwargs)
        _model_cache[system_instruction] = model
        return model


def warm_up_transcriber() -> None:
    """
    Build (and cache) the transcription model without sending audio.

    This is where the AI Core proxy client gets resolved - an OAuth
    token fetch plus a deployment lookup that costs several seconds
    once per process. Calling this at startup means the first real
    recording only pays for the transcription itself. The same proxy
    client is shared with the chat and embedding calls, so they get
    warmed as a side effect.
    """
    _build_model(TRANSCRIPTION_INSTRUCTION)


def _transcribe_bytes(audio_bytes: bytes, mime_type: str, language: Optional[str]) -> str:
    """Send audio to the deployed Gemini model and return the transcript."""
    from vertexai.generative_models import GenerationConfig, Part

    instruction = TRANSCRIPTION_INSTRUCTION
    if language:
        instruction += f"\nThe technician is speaking {language}. Transcribe in that language."

    model_name = AICORE_STT_DEPLOYMENT_ID or AICORE_STT_MODEL
    logger.info("Transcribing %d bytes (%s) via %s", len(audio_bytes), mime_type, model_name)

    try:
        model = _build_model(instruction)
        response = model.generate_content(
            [
                Part.from_data(data=audio_bytes, mime_type=mime_type),
                "Transcribe this recording.",
            ],
            generation_config=GenerationConfig(temperature=0.0),
        )
    except Exception as exc:  # noqa: BLE001 - provider SDK exceptions vary
        raise TranscriptionError(f"Speech-to-text failed: {exc}") from exc

    text = (getattr(response, "text", "") or "").strip()

    # A multimodal model occasionally wraps its answer in quotes or
    # prefixes it despite the instruction. Strip the common cases.
    if text.startswith(("Transcript:", "TRANSCRIPT:")):
        text = text.split(":", 1)[1].strip()
    text = text.strip('"').strip()

    logger.info("Transcription complete (%d characters)", len(text))
    return text


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

    return _transcribe_bytes(
        path.read_bytes(),
        guess_mime_type(path.name),
        language,
    )


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
    UPLOADS_FOLDER for auditing/replay purposes - but on a background
    thread, and transcription starts from the bytes already in memory.
    Previously this wrote the upload to disk and then read the very
    same bytes back before sending them, so the technician waited on
    two file operations that the transcript never depended on.
    """
    validate_audio_format(original_file_name)

    if persist:
        _persist_audio_async(audio_bytes, original_file_name)

    return _transcribe_bytes(
        audio_bytes,
        guess_mime_type(original_file_name),
        language,
    )