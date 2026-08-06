"""
==============================================================
AI Maintenance Voice Assistant
Manual Ingestion Script
--------------------------------------------------------------

Purpose
-------
Read every PDF aircraft manual in the `manuals/` folder, split
it into overlapping text chunks, generate embeddings for each
chunk using the text-embedding model deployed in SAP AI Core,
and store everything in SAP HANA Cloud so the agent can retrieve
relevant passages via semantic search
(backend.database.semantic_search).

Usage
-----
    # Ingest every new manual found in MANUALS_FOLDER
    python -m backend.scripts.ingest_manuals

    # Force re-ingestion of a manual that was already processed
    python -m backend.scripts.ingest_manuals --force

    # Ingest a single file only
    python -m backend.scripts.ingest_manuals --file B737_AMM.pdf

    # Chunk without embedding or writing anything, to sanity-check
    python -m backend.scripts.ingest_manuals --dry-run

Notes
-----
• Chunking happens page by page, so every stored chunk keeps the
  page it came from. That is what lets the assistant cite
  "[B737_AMM.pdf, p.147]" accurately instead of approximately.
• Manuals are skipped if their SHA-256 matches what is already
  stored, so you can re-run this over the folder safely.
• pdfplumber is used rather than pypdf: maintenance manuals are
  multi-column with tables, and pdfplumber keeps words in reading
  order instead of interleaving the columns.
• Pages with no text layer are reported, not silently dropped -
  a scanned manual needs OCR before it can be searched.
==============================================================
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import List

import pdfplumber

from backend.config import (
    MANUALS_FOLDER,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    AICORE_EMBEDDING_MODEL,
    EMBEDDING_DIM,
    LOG_LEVEL,
)
from backend.embeddings import embed_texts, get_embedding_dimension
from backend.database import (
    init_db,
    get_manual_by_file_name,
    delete_manual_and_chunks,
    insert_manual,
    insert_chunks_bulk,
)

logger = logging.getLogger("mro_copilot.ingest_manuals")

SUPPORTED_SUFFIXES = {".pdf"}


# ==========================================================
# Text Extraction
# ==========================================================

def extract_pages(path: Path) -> list[tuple[int, str]]:
    """Return [(page_number, text), ...] for pages that have a text layer."""
    pages: list[tuple[int, str]] = []
    empty: list[int] = []

    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append((page_number, text))
            else:
                empty.append(page_number)

    if empty:
        logger.warning(
            "%s: %d page(s) have no text layer (scanned - would need OCR): %s",
            path.name,
            len(empty),
            ", ".join(map(str, empty[:10])) + ("..." if len(empty) > 10 else ""),
        )

    return pages


def normalise(text: str) -> str:
    """Collapse the whitespace noise typical of PDF extraction."""
    text = text.replace("\u00ad", "")          # soft hyphens
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ==========================================================
# Chunking
# ==========================================================

def split_page(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List[str]:
    """
    Sliding window over one page, preferring a paragraph break and
    then a sentence end, so a numbered procedure step is not cut
    in half mid-instruction.
    """
    if chunk_overlap >= chunk_size:
        raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")

    text = normalise(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    pieces: List[str] = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        if end < len(text):
            window = text[start:end]
            cut = window.rfind("\n\n")
            if cut < chunk_size // 2:
                cut = max(window.rfind(". "), window.rfind(".\n"))
            if cut > chunk_size // 2:
                end = start + cut + 1

        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)

        if end >= len(text):
            break
        start = max(end - chunk_overlap, start + 1)

    return pieces


def chunk_pdf(path: Path) -> tuple[list[dict], int]:
    """
    Chunk a PDF page by page.

    Returns ([{"page_number": int, "text": str}, ...], page_count).
    """
    pages = extract_pages(path)
    chunks = [
        {"page_number": page_number, "text": piece}
        for page_number, page_text in pages
        for piece in split_page(page_text)
    ]
    return chunks, len(pages)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ==========================================================
# Ingestion of a single manual
# ==========================================================

def ingest_manual_file(path: Path, force: bool = False, dry_run: bool = False) -> bool:
    """
    Ingest a single manual file. Returns True if it was (re)ingested,
    False if it was skipped.
    """
    digest = file_hash(path)
    existing = None if dry_run else get_manual_by_file_name(path.name)

    if existing and not force:
        if existing.get("FILE_HASH") == digest:
            logger.info("Skipping '%s' - unchanged since last ingest", path.name)
            return False
        logger.info("'%s' has changed on disk - re-ingesting", path.name)
        force = True

    logger.info("Extracting text from '%s'", path.name)
    chunks, page_count = chunk_pdf(path)

    if not chunks:
        logger.warning(
            "No extractable text in '%s' (it may be a scanned/image-only PDF). Skipping.",
            path.name,
        )
        return False

    logger.info("Split '%s' into %d chunks across %d pages", path.name, len(chunks), page_count)

    if dry_run:
        preview = chunks[0]["text"][:200].replace("\n", " ")
        logger.info("First chunk (p.%s): %s...", chunks[0]["page_number"], preview)
        return True

    if existing:
        logger.info("Deleting previous chunks for '%s'", path.name)
        delete_manual_and_chunks(existing["MANUAL_ID"])

    embeddings = embed_texts([c["text"] for c in chunks])

    manual_id = insert_manual(
        file_name=path.name,
        title=path.stem.replace("_", " "),
        file_hash=digest,
        page_count=page_count,
        chunk_count=len(chunks),
        embed_model=AICORE_EMBEDDING_MODEL,
    )
    insert_chunks_bulk(manual_id, path.name, chunks, embeddings)

    logger.info(
        "Ingested '%s' (manual_id=%s, %d chunks)", path.name, manual_id, len(chunks)
    )
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


def ingest_all(
    folder: Path = MANUALS_FOLDER,
    force: bool = False,
    single_file: str | None = None,
    dry_run: bool = False,
) -> None:
    if not dry_run:
        init_db()

        actual_dim = get_embedding_dimension()
        logger.info(
            "Embedding model %s producing %d dimensions", AICORE_EMBEDDING_MODEL, actual_dim
        )
        if actual_dim != EMBEDDING_DIM:
            sys.exit(
                f"Embedding model returned {actual_dim} dimensions but EMBEDDING_DIM "
                f"is {EMBEDDING_DIM}, which is the width of MANUAL_CHUNKS.EMBEDDING. "
                f"Inserts would fail. Either set EMBEDDING_DIM={actual_dim} in .env "
                f"and recreate the table at that width, or check the model name."
            )

    files = find_manual_files(folder, single_file)
    if not files:
        logger.warning("No manual files (%s) found in %s", ", ".join(SUPPORTED_SUFFIXES), folder)
        return

    ingested_count = 0
    for path in files:
        try:
            if ingest_manual_file(path, force=force, dry_run=dry_run):
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chunk only - no embeddings, no database writes.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)-7s %(message)s")

    folder = Path(args.folder) if args.folder else MANUALS_FOLDER

    try:
        ingest_all(
            folder=folder,
            force=args.force,
            single_file=args.file,
            dry_run=args.dry_run,
        )
    except Exception:
        logger.exception("Manual ingestion failed")
        sys.exit(1)


if __name__ == "__main__":
    main()