"""
==============================================================
AI Maintenance Voice Copilot
Manual Ingestion Script
--------------------------------------------------------------

Purpose
-------
Read every PDF aircraft manual in the `manuals/` folder, split
it into overlapping text chunks, generate embeddings for each
chunk using Azure OpenAI, and store everything in SAP HANA
Cloud so the AI agent can retrieve relevant passages via
semantic search (backend.database.semantic_search).

Usage
-----
    # Ingest every new manual found in MANUALS_FOLDER
    python -m backend.scripts.ingest_manuals

    # Force re-ingestion of a manual that was already processed
    python -m backend.scripts.ingest_manuals --force

    # Ingest a single file only
    python -m backend.scripts.ingest_manuals --file B737_AMM.pdf

Notes
-----
• Manuals already present in the MANUALS table (matched by file
  name) are skipped unless --force is passed.
• Chunking is character-based using CHUNK_SIZE / CHUNK_OVERLAP
  from backend.config, which keeps things simple and predictable
  regardless of PDF layout quirks.
• Embeddings are requested in batches to reduce the number of
  Azure OpenAI API calls.
==============================================================
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List

from openai import AzureOpenAI
from pypdf import PdfReader

from backend.config import (
    MANUALS_FOLDER,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    AZURE_OPENAI_URL,
    AZURE_API_KEY,
    AZURE_API_VERSION,
    AZURE_EMBEDDING_MODEL,
    LOG_LEVEL,
)
from backend.database import (
    init_db,
    get_manual_by_file_name,
    delete_manual_and_chunks,
    insert_manual,
    insert_chunks_bulk,
)

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("mro_copilot.ingest_manuals")

# Azure OpenAI embeddings are sent in batches to stay well within
# request size / rate limits.
EMBEDDING_BATCH_SIZE = 32

SUPPORTED_SUFFIXES = {".pdf"}


# ==========================================================
# Text Extraction
# ==========================================================

def extract_text_from_pdf(path: Path) -> str:
    """Extract and concatenate text from every page of a PDF."""
    reader = PdfReader(str(path))
    pages_text = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages_text.append(text)
        else:
            logger.debug("Page %d of %s produced no text (likely scanned)", page_number, path.name)
    return "\n\n".join(pages_text)


# ==========================================================
# Chunking
# ==========================================================

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Split `text` into overlapping chunks of roughly `chunk_size`
    characters, stepping forward by (chunk_size - chunk_overlap)
    each time.
    """
    text = text.strip()
    if not text:
        return []

    if chunk_overlap >= chunk_size:
        raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")

    step = chunk_size - chunk_overlap
    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == text_length:
            break
        start += step

    return chunks


# ==========================================================
# Embeddings
# ==========================================================

def get_azure_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_URL,
        api_key=AZURE_API_KEY,
        api_version=AZURE_API_VERSION,
    )


def embed_chunks(client: AzureOpenAI, chunks: List[str]) -> List[List[float]]:
    """Generate embeddings for a list of text chunks, batching requests."""
    embeddings: List[List[float]] = []

    for batch_start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[batch_start: batch_start + EMBEDDING_BATCH_SIZE]
        response = client.embeddings.create(
            model=AZURE_EMBEDDING_MODEL,
            input=batch,
        )
        # response.data is returned in the same order as the input list
        embeddings.extend(item.embedding for item in response.data)
        logger.info(
            "Embedded chunks %d-%d of %d",
            batch_start + 1,
            batch_start + len(batch),
            len(chunks),
        )

    return embeddings


# ==========================================================
# Ingestion of a single manual
# ==========================================================

def ingest_manual_file(path: Path, client: AzureOpenAI, force: bool = False) -> bool:
    """
    Ingest a single manual file. Returns True if it was (re)ingested,
    False if it was skipped.
    """
    existing = get_manual_by_file_name(path.name)
    if existing and not force:
        logger.info("Skipping '%s' - already ingested (use --force to re-ingest)", path.name)
        return False

    if existing and force:
        logger.info("Re-ingesting '%s' - deleting previous chunks first", path.name)
        delete_manual_and_chunks(existing["MANUAL_ID"])

    logger.info("Extracting text from '%s'", path.name)
    text = extract_text_from_pdf(path)

    if not text.strip():
        logger.warning(
            "No extractable text found in '%s' (it may be a scanned/image-only PDF). Skipping.",
            path.name,
        )
        return False

    chunks = chunk_text(text)
    logger.info("Split '%s' into %d chunks", path.name, len(chunks))

    if not chunks:
        logger.warning("No chunks produced for '%s'. Skipping.", path.name)
        return False

    embeddings = embed_chunks(client, chunks)

    manual_id = insert_manual(file_name=path.name, title=path.stem)
    insert_chunks_bulk(manual_id, chunks, embeddings)

    logger.info("Successfully ingested '%s' (manual_id=%s, %d chunks)", path.name, manual_id, len(chunks))
    return True


# ==========================================================
# Folder-level ingestion
# ==========================================================

def find_manual_files(folder: Path, single_file: str | None = None) -> List[Path]:
    if single_file:
        candidate = folder / single_file
        if not candidate.exists():
            raise FileNotFoundError(f"'{single_file}' not found in {folder}")
        return [candidate]

    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )


def ingest_all(folder: Path = MANUALS_FOLDER, force: bool = False, single_file: str | None = None) -> None:
    init_db()

    files = find_manual_files(folder, single_file)
    if not files:
        logger.warning("No manual files (%s) found in %s", ", ".join(SUPPORTED_SUFFIXES), folder)
        return

    client = get_azure_client()

    ingested_count = 0
    for path in files:
        try:
            if ingest_manual_file(path, client, force=force):
                ingested_count += 1
        except Exception:
            logger.exception("Failed to ingest '%s'", path.name)

    logger.info("Ingestion complete: %d/%d manuals processed", ingested_count, len(files))


# ==========================================================
# CLI
# ==========================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest aircraft maintenance manuals into SAP HANA Cloud for semantic search."
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Ingest only this file (must exist inside the manuals folder), e.g. B737_AMM.pdf",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest manuals even if they were already processed (deletes old chunks first).",
    )
    parser.add_argument(
        "--folder",
        type=str,
        default=None,
        help="Override the manuals folder (defaults to MANUALS_FOLDER from config.py).",
    )
    args = parser.parse_args()

    folder = Path(args.folder) if args.folder else MANUALS_FOLDER

    try:
        ingest_all(folder=folder, force=args.force, single_file=args.file)
    except Exception:
        logger.exception("Manual ingestion failed")
        sys.exit(1)


if __name__ == "__main__":
    main()