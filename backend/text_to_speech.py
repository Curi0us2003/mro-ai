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

Provider
--------
Piper, running locally inside this process. The SAP generative AI
hub has no text-to-speech model, so this is the one layer that is
not a BTP model call - but it is also not a cloud call at all.
Nothing leaves the runtime, there is no per-character billing,
and synthesis is faster than real time on ordinary CPU.

Voice models
------------
Download a voice (two files) and drop them in voices/:

    en_US-lessac-medium.onnx
    en_US-lessac-medium.onnx.json

Set PIPER_VOICE_NAME in .env to switch voices.

The normalisation pass
----------------------
Read verbatim, Piper says "M S two one zero four two L three"
as something unintelligible, and "ATA 32-41" as "ata thirty-two
forty-one". normalise_for_speech() rewrites maintenance vocabulary
into a form that survives synthesis. This is not cosmetic - it is
the difference between a copilot a technician uses and one they
switch off.

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

import io
import logging
import re
import threading
import uuid
import wave
from pathlib import Path
from typing import Optional

from backend.config import (
    AUDIO_OUTPUT_FOLDER,
    PIPER_MODEL_PATH,
    PIPER_CONFIG_PATH,
    PIPER_VOICE_NAME,
    PIPER_LENGTH_SCALE,
    PIPER_SENTENCE_SILENCE,
    TTS_OUTPUT_FORMAT,
    LOG_LEVEL,
)

logger = logging.getLogger("mro_copilot.text_to_speech")
logger.setLevel(LOG_LEVEL)


class VoiceModelNotFoundError(FileNotFoundError):
    """Raised when the configured Piper voice files are not on disk."""


class SynthesisError(RuntimeError):
    """Raised when Piper fails to produce audio."""


# ==========================================================
# Voice loading (once per process)
# ==========================================================

_voice = None
_voice_lock = threading.Lock()


def get_voice():
    """
    Load the Piper voice once and reuse it.

    Loading parses a ~50 MB ONNX graph, so doing it per request
    would dominate response time. The lock keeps two concurrent
    Flask threads from loading it twice on a cold start.
    """
    global _voice

    if _voice is not None:
        return _voice

    with _voice_lock:
        if _voice is not None:
            return _voice

        if not PIPER_MODEL_PATH.exists():
            raise VoiceModelNotFoundError(
                f"Piper voice model not found at {PIPER_MODEL_PATH}.\n"
                f"Download '{PIPER_VOICE_NAME}.onnx' and "
                f"'{PIPER_VOICE_NAME}.onnx.json' from the Piper voices release "
                f"and place both in {PIPER_MODEL_PATH.parent}."
            )

        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise SynthesisError(
                "piper-tts is not installed. Run: pip install piper-tts"
            ) from exc

        logger.info("Loading Piper voice '%s'", PIPER_VOICE_NAME)

        if PIPER_CONFIG_PATH.exists():
            _voice = PiperVoice.load(str(PIPER_MODEL_PATH), config_path=str(PIPER_CONFIG_PATH))
        else:
            _voice = PiperVoice.load(str(PIPER_MODEL_PATH))

        return _voice


# ==========================================================
# Speech normalisation for maintenance vocabulary
# ==========================================================

# Spoken forms for units that appear in torque and pressure specs.
_UNIT_EXPANSIONS = [
    (r"\bN\.?m\b", "newton metres"),
    (r"\bNm\b", "newton metres"),
    (r"\bin[- ]?lbs?\b", "inch pounds"),
    (r"\bft[- ]?lbs?\b", "foot pounds"),
    (r"\bpsi\b", "P S I"),
    (r"\bkPa\b", "kilopascals"),
    (r"\bbar\b", "bar"),
    (r"\bmm\b", "millimetres"),
    (r"\bcm\b", "centimetres"),
    (r"\bkg\b", "kilograms"),
    (r"\bhrs?\b", "hours"),
]

# Abbreviations technicians read as letters, not words.
_SPELL_OUT_TERMS = {
    "AMM": "A M M",
    "IPC": "I P C",
    "CMM": "C M M",
    "SRM": "S R M",
    "MEL": "M E L",
    "AD": "A D",
    "SB": "S B",
    "AOG": "A O G",
    "APU": "A P U",
    "NDT": "N D T",
    "FOD": "F O D",
}

_ATA_PATTERN = re.compile(r"\bATA\s*(\d{2})\s*[-–]\s*(\d{2})\b", re.IGNORECASE)
_REGISTRATION_PATTERN = re.compile(r"\b([A-Z]{1,2})-([A-Z]{2,4})\b")
_PART_NUMBER_PATTERN = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{4,}\b")
_CITATION_PATTERN = re.compile(r"\[([^\]]+?),\s*p\.\s*(\d+)\]")


def _spell(token: str) -> str:
    """Turn 'MS21042L3' into 'M S 2 1 0 4 2 L 3' so Piper reads it out."""
    return " ".join(ch for ch in token if ch != "-")


def normalise_for_speech(text: str) -> str:
    """
    Rewrite a copilot reply into something Piper can read aloud
    correctly. Applied automatically by synthesize_speech().

    The written reply the technician sees on screen is untouched -
    only the audio version is rewritten.
    """
    if not text:
        return text

    spoken = text

    # "[A320-AMM.pdf, p.147]" -> dropped entirely. Citations are for the
    # written record and the PDF report - not useful read aloud, and a
    # technician mid-task doesn't need to hear a file name and page number.
    spoken = _CITATION_PATTERN.sub("", spoken)

    # "ATA 32-41" -> "A T A chapter 32, section 41"
    spoken = _ATA_PATTERN.sub(
        lambda m: f"A T A chapter {m.group(1)}, section {m.group(2)}", spoken
    )

    # Registrations: "VT-ABC" -> "V T - A B C"
    spoken = _REGISTRATION_PATTERN.sub(
        lambda m: f"{_spell(m.group(1))} {_spell(m.group(2))}", spoken
    )

    # Part numbers: any 5+ char token mixing letters and digits.
    spoken = _PART_NUMBER_PATTERN.sub(lambda m: _spell(m.group(0)), spoken)

    # Units.
    for pattern, replacement in _UNIT_EXPANSIONS:
        spoken = re.sub(pattern, replacement, spoken)

    # Manual abbreviations read as letters.
    for term, replacement in _SPELL_OUT_TERMS.items():
        spoken = re.sub(rf"\b{term}\b", replacement, spoken)

    # Markdown artefacts and list bullets have no spoken equivalent.
    spoken = re.sub(r"[*_`#]+", "", spoken)
    spoken = re.sub(r"^\s*[-•]\s*", "", spoken, flags=re.MULTILINE)

    # Removing a citation can leave "engine , torque" or "engine ." behind.
    spoken = re.sub(r"\s+([.,!?;:])", r"\1", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()

    return spoken


# ==========================================================
# Synthesis
# ==========================================================

def synthesize_speech_bytes(
    text: str,
    length_scale: Optional[float] = None,
    normalise: bool = True,
) -> bytes:
    """
    Synthesize `text` and return WAV bytes without writing to disk.
    Useful when the caller wants to stream audio straight back over
    HTTP rather than persisting it.
    """
    if not text or not text.strip():
        raise ValueError("Cannot synthesize speech for empty text")

    spoken = normalise_for_speech(text) if normalise else text
    voice = get_voice()

    logger.info(
        "Synthesizing %d chars with '%s'", len(spoken), PIPER_VOICE_NAME
    )

    buffer = io.BytesIO()
    try:
        with wave.open(buffer, "wb") as wav_file:
            _synthesize_into_wav(voice, spoken, wav_file, length_scale)
    except Exception as exc:  # noqa: BLE001 - piper raises various types
        raise SynthesisError(f"Piper synthesis failed: {exc}") from exc

    return buffer.getvalue()


def _synthesize_into_wav(voice, text: str, wav_file, length_scale: Optional[float]) -> None:
    """
    Call Piper, tolerating the API differences between releases.

    Piper 1.3+ exposes synthesize_wav(); earlier builds expose
    synthesize(). Both write a complete WAV into the open handle.
    """
    scale = PIPER_LENGTH_SCALE if length_scale is None else length_scale

    kwargs = {
        "length_scale": scale,
        "sentence_silence": PIPER_SENTENCE_SILENCE,
    }

    if hasattr(voice, "synthesize_wav"):
        try:
            voice.synthesize_wav(text, wav_file, **kwargs)
            return
        except TypeError:
            voice.synthesize_wav(text, wav_file)
            return

    try:
        voice.synthesize(text, wav_file, **kwargs)
    except TypeError:
        voice.synthesize(text, wav_file)


def synthesize_speech(
    text: str,
    file_name: Optional[str] = None,
    length_scale: Optional[float] = None,
) -> Path:
    """
    Synthesize `text` and save it under AUDIO_OUTPUT_FOLDER.
    Returns the path to the saved file.

    Pass `file_name` to control the output name; otherwise a unique
    one is generated.
    """
    audio_bytes = synthesize_speech_bytes(text, length_scale=length_scale)

    stem = Path(file_name).stem if file_name else str(uuid.uuid4())
    destination = AUDIO_OUTPUT_FOLDER / f"{stem}.{TTS_OUTPUT_FORMAT}"
    destination.write_bytes(audio_bytes)

    logger.info("Saved synthesized speech to %s", destination)
    return destination


def voice_is_available() -> bool:
    """True if the configured Piper voice can be loaded right now."""
    try:
        get_voice()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Piper voice unavailable: %s", exc)
        return False


# ==========================================================
# CLI - `python -m backend.text_to_speech "some text"`
# ==========================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=LOG_LEVEL)
    sample = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "Corrosion on VT-ABC, ATA 32-41. Torque the MS21042L3 nut to 45 Nm."
    )
    print("Written :", sample)
    print("Spoken  :", normalise_for_speech(sample))
    print("Saved to:", synthesize_speech(sample))