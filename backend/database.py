"""
==============================================================
AI Maintenance Voice Copilot
Database Module
--------------------------------------------------------------

Purpose
-------
Single point of access to SAP HANA Cloud for the whole application.

Responsibilities
----------------
• Open / close connections to SAP HANA Cloud
• Create the database schema (tables) if it does not exist
• Provide CRUD helpers for:
    - manuals              (ingested source documents)
    - manual_chunks         (chunked text + vector embeddings)
    - maintenance_records   (structured findings, SAP-ready)
    - conversations         (raw technician / AI dialogue turns)
• Provide semantic search over manual_chunks using the
  SAP HANA Cloud Vector Engine (COSINE_SIMILARITY)

IMPORTANT
---------
This module never reads environment variables directly.
All connection settings come from backend.config.

Every other module in the project should talk to the database
exclusively through the functions defined here - never open a
raw hdbcli connection elsewhere.

Example
-------
    from backend.database import get_connection, init_db, insert_manual

    init_db()
    with get_connection() as conn:
        ...
==============================================================
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from hdbcli import dbapi

from backend.config import (
    HANA_HOST,
    HANA_PORT,
    HANA_USER,
    HANA_PASSWORD,
    HANA_SCHEMA,
    HANA_ENCRYPT,
    AZURE_EMBEDDING_MODEL,
    LOG_LEVEL,
)

logger = logging.getLogger("mro_copilot.database")
logger.setLevel(LOG_LEVEL)

# ==========================================================
# Embedding dimensions per known Azure embedding model
# (needed to size the HANA REAL_VECTOR columns)
# ==========================================================

EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

EMBEDDING_DIM = EMBEDDING_DIMENSIONS.get(AZURE_EMBEDDING_MODEL, 1536)


# ==========================================================
# Connection Handling
# ==========================================================

@contextmanager
def get_connection():
    """
    Context manager that yields a live SAP HANA Cloud connection.

    Usage:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ...")

    The connection is always closed on exit, even if an
    exception is raised inside the `with` block.
    """
    conn = dbapi.connect(
        address=HANA_HOST,
        port=HANA_PORT,
        user=HANA_USER,
        password=HANA_PASSWORD,
        encrypt=HANA_ENCRYPT,
        sslValidateCertificate=False,
        currentSchema=HANA_SCHEMA,
    )
    try:
        yield conn
    finally:
        conn.close()


def _execute(conn, sql: str, params: Optional[tuple] = None):
    """Small helper: run a statement and return the cursor."""
    cursor = conn.cursor()
    if params is not None:
        cursor.execute(sql, params)
    else:
        cursor.execute(sql)
    return cursor


# ==========================================================
# Schema Definition / Initialisation
# ==========================================================

# NOTE: This module deliberately contains no CREATE TABLE / DDL.
#
# On HDI-container-backed HANA Cloud instances, the bound runtime
# user (typically named "<container>_<token>_RT") only ever has
# DML rights (SELECT/INSERT/UPDATE/DELETE) on its own schema - it
# cannot run DDL under any circumstances, even with a GRANT from
# an admin. Tables must instead be created once, out of band, by
# running the project's schema.sql (see project root) through a
# privileged connection such as SAP HANA Database Explorer opened
# from BTP Cockpit.
#
# init_db() below therefore only *verifies* the expected tables
# are present and gives a clear, actionable error if they are not,
# rather than attempting to create them itself.

_TABLE_NAMES = ["MANUALS", "MANUAL_CHUNKS", "MAINTENANCE_RECORDS", "CONVERSATIONS"]


class SchemaNotReadyError(RuntimeError):
    """Raised when required tables are missing and must be created manually."""


def _table_exists(conn, table_name: str) -> bool:
    cursor = _execute(
        conn,
        """
        SELECT COUNT(*) FROM TABLES
        WHERE SCHEMA_NAME = ? AND TABLE_NAME = ?
        """,
        (HANA_SCHEMA, table_name),
    )
    (count,) = cursor.fetchone()
    return count > 0


def init_db() -> None:
    """
    Verify that every required table already exists in HANA_SCHEMA.

    This does NOT create tables - see the module note above. If any
    table is missing, raises SchemaNotReadyError with instructions
    for creating it manually via schema.sql.
    """
    with get_connection() as conn:
        missing = [name for name in _TABLE_NAMES if not _table_exists(conn, name)]

    if missing:
        raise SchemaNotReadyError(
            f"Missing table(s) in schema '{HANA_SCHEMA}': {', '.join(missing)}. "
            "This app's database user only has DML rights and cannot create "
            "tables itself. Run the project's schema.sql once via a privileged "
            "connection (e.g. SAP HANA Database Explorer opened from BTP "
            "Cockpit), then re-run this."
        )

    logger.info("Schema verified: all required tables present in '%s'", HANA_SCHEMA)


# ==========================================================
# Helpers
# ==========================================================

def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _vector_literal(embedding: Iterable[float]) -> str:
    """Format a Python list of floats as a HANA TO_REAL_VECTOR literal."""
    return "[" + ",".join(f"{float(x):.8f}" for x in embedding) + "]"


# ==========================================================
# Manuals
# ==========================================================

def insert_manual(
    file_name: str,
    title: Optional[str] = None,
    aircraft_type: Optional[str] = None,
) -> str:
    """Insert a manual record and return its generated MANUAL_ID."""
    manual_id = _new_id()
    with get_connection() as conn:
        _execute(
            conn,
            """
            INSERT INTO MANUALS (MANUAL_ID, FILE_NAME, TITLE, AIRCRAFT_TYPE, UPLOADED_AT)
            VALUES (?, ?, ?, ?, ?)
            """,
            (manual_id, file_name, title, aircraft_type, _now()),
        )
        conn.commit()
    logger.info("Inserted manual '%s' (id=%s)", file_name, manual_id)
    return manual_id


def get_manual_by_file_name(file_name: str) -> Optional[dict]:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            "SELECT MANUAL_ID, FILE_NAME, TITLE, AIRCRAFT_TYPE, UPLOADED_AT "
            "FROM MANUALS WHERE FILE_NAME = ?",
            (file_name,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cursor.description]
        return dict(zip(cols, row))


def delete_manual_and_chunks(manual_id: str) -> None:
    """Remove a manual and all of its chunks (used for re-ingestion)."""
    with get_connection() as conn:
        _execute(conn, "DELETE FROM MANUAL_CHUNKS WHERE MANUAL_ID = ?", (manual_id,))
        _execute(conn, "DELETE FROM MANUALS WHERE MANUAL_ID = ?", (manual_id,))
        conn.commit()


# ==========================================================
# Manual Chunks + Semantic Search
# ==========================================================

def insert_chunk(
    manual_id: str,
    chunk_index: int,
    content: str,
    embedding: Iterable[float],
) -> str:
    """Insert a single text chunk with its embedding vector."""
    chunk_id = _new_id()
    vector_sql = f"TO_REAL_VECTOR('{_vector_literal(embedding)}')"
    with get_connection() as conn:
        _execute(
            conn,
            f"""
            INSERT INTO MANUAL_CHUNKS
                (CHUNK_ID, MANUAL_ID, CHUNK_INDEX, CONTENT, EMBEDDING, CREATED_AT)
            VALUES (?, ?, ?, ?, {vector_sql}, ?)
            """,
            (chunk_id, manual_id, chunk_index, content, _now()),
        )
        conn.commit()
    return chunk_id


def insert_chunks_bulk(manual_id: str, chunks: list[str], embeddings: list[list[float]]) -> int:
    """Insert many chunks at once. Returns the number of rows inserted."""
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must be the same length")

    with get_connection() as conn:
        cursor = conn.cursor()
        for index, (content, embedding) in enumerate(zip(chunks, embeddings)):
            vector_sql = f"TO_REAL_VECTOR('{_vector_literal(embedding)}')"
            cursor.execute(
                f"""
                INSERT INTO MANUAL_CHUNKS
                    (CHUNK_ID, MANUAL_ID, CHUNK_INDEX, CONTENT, EMBEDDING, CREATED_AT)
                VALUES (?, ?, ?, ?, {vector_sql}, ?)
                """,
                (_new_id(), manual_id, index, content, _now()),
            )
        conn.commit()
    logger.info("Inserted %d chunks for manual %s", len(chunks), manual_id)
    return len(chunks)


def semantic_search(query_embedding: Iterable[float], top_k: int = 5) -> list[dict]:
    """
    Return the `top_k` manual chunks most similar to `query_embedding`,
    using SAP HANA Cloud's native COSINE_SIMILARITY vector function.

    Each result dict contains: CHUNK_ID, MANUAL_ID, FILE_NAME, CONTENT, SCORE
    """
    vector_sql = f"TO_REAL_VECTOR('{_vector_literal(query_embedding)}')"
    with get_connection() as conn:
        cursor = _execute(
            conn,
            f"""
            SELECT TOP {int(top_k)}
                   c.CHUNK_ID,
                   c.MANUAL_ID,
                   m.FILE_NAME,
                   c.CONTENT,
                   COSINE_SIMILARITY(c.EMBEDDING, {vector_sql}) AS SCORE
            FROM MANUAL_CHUNKS c
            JOIN MANUALS m ON m.MANUAL_ID = c.MANUAL_ID
            ORDER BY SCORE DESC
            """,
        )
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in rows]


# ==========================================================
# Maintenance Records
# ==========================================================

def insert_maintenance_record(
    aircraft_reg: Optional[str] = None,
    component: Optional[str] = None,
    finding: Optional[str] = None,
    severity: Optional[str] = None,
    location: Optional[str] = None,
    recommended_action: Optional[str] = None,
    technician: Optional[str] = None,
    inspection_ts: Optional[datetime] = None,
    status: str = "OPEN",
) -> str:
    """Create a new structured maintenance record and return its RECORD_ID."""
    record_id = _new_id()
    with get_connection() as conn:
        _execute(
            conn,
            """
            INSERT INTO MAINTENANCE_RECORDS (
                RECORD_ID, AIRCRAFT_REG, COMPONENT, FINDING, SEVERITY,
                LOCATION, RECOMMENDED_ACTION, TECHNICIAN, INSPECTION_TS,
                STATUS, CREATED_AT
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                aircraft_reg,
                component,
                finding,
                severity,
                location,
                recommended_action,
                technician,
                inspection_ts or _now(),
                status,
                _now(),
            ),
        )
        conn.commit()
    logger.info("Created maintenance record %s (aircraft=%s)", record_id, aircraft_reg)
    return record_id


def update_maintenance_record(record_id: str, **fields: Any) -> None:
    """Update arbitrary columns on an existing maintenance record."""
    if not fields:
        return
    allowed = {
        "AIRCRAFT_REG", "COMPONENT", "FINDING", "SEVERITY", "LOCATION",
        "RECOMMENDED_ACTION", "TECHNICIAN", "INSPECTION_TS", "STATUS",
    }
    updates = {k.upper(): v for k, v in fields.items() if k.upper() in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{col} = ?" for col in updates)
    params = list(updates.values()) + [record_id]
    with get_connection() as conn:
        _execute(
            conn,
            f"UPDATE MAINTENANCE_RECORDS SET {set_clause} WHERE RECORD_ID = ?",
            tuple(params),
        )
        conn.commit()


def get_maintenance_record(record_id: str) -> Optional[dict]:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            "SELECT * FROM MAINTENANCE_RECORDS WHERE RECORD_ID = ?",
            (record_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cursor.description]
        return dict(zip(cols, row))


def list_maintenance_records(aircraft_reg: Optional[str] = None, limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        if aircraft_reg:
            cursor = _execute(
                conn,
                f"SELECT TOP {int(limit)} * FROM MAINTENANCE_RECORDS "
                "WHERE AIRCRAFT_REG = ? ORDER BY CREATED_AT DESC",
                (aircraft_reg,),
            )
        else:
            cursor = _execute(
                conn,
                f"SELECT TOP {int(limit)} * FROM MAINTENANCE_RECORDS ORDER BY CREATED_AT DESC",
            )
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in rows]


# ==========================================================
# Conversations
# ==========================================================

def insert_conversation_message(role: str, message: str, record_id: Optional[str] = None) -> str:
    """
    Store one turn of dialogue.

    `role` should be one of: "technician", "assistant", "system".
    `record_id` links the message to a maintenance record once one exists.
    """
    message_id = _new_id()
    with get_connection() as conn:
        _execute(
            conn,
            """
            INSERT INTO CONVERSATIONS (MESSAGE_ID, RECORD_ID, ROLE, MESSAGE, CREATED_AT)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, record_id, role, message, _now()),
        )
        conn.commit()
    return message_id


def get_conversation(record_id: str) -> list[dict]:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            "SELECT MESSAGE_ID, ROLE, MESSAGE, CREATED_AT FROM CONVERSATIONS "
            "WHERE RECORD_ID = ? ORDER BY CREATED_AT ASC",
            (record_id,),
        )
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in rows]


# ==========================================================
# CLI entry point - lets you run `python -m backend.database`
# to (re)initialise the schema manually.
# ==========================================================

if __name__ == "__main__":
    logging.basicConfig(level=LOG_LEVEL)
    init_db()
    print("Database schema initialised successfully.")