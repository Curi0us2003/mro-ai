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
• Verify the schema is present
• Provide CRUD helpers for:
    - users                 (login accounts, admin-created only)
    - manuals               (ingested source documents)
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
    EMBEDDING_DIM,
    LOG_LEVEL,
)

logger = logging.getLogger("mro_copilot.database")
logger.setLevel(LOG_LEVEL)


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


def _rows_as_dicts(cursor) -> list[dict]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _row_as_dict(cursor) -> Optional[dict]:
    row = cursor.fetchone()
    if not row:
        return None
    cols = [c[0] for c in cursor.description]
    return dict(zip(cols, row))


def _as_text(value) -> str:
    """NCLOB columns come back as str, bytes, or a LOB handle."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "read"):
        return value.read()
    return str(value)


# ==========================================================
# Schema Verification
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
# are present and gives a clear, actionable error if they are not.

_TABLE_NAMES = [
    "USERS",
    "MANUALS",
    "MANUAL_CHUNKS",
    "MAINTENANCE_RECORDS",
    "CONVERSATIONS",
]


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
    Verify that every required table already exists in HANA_SCHEMA,
    and that the vector column width matches EMBEDDING_DIM.

    This does NOT create tables - see the module note above.
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

        actual_dim = get_embedding_column_dimension(conn)

    if actual_dim and actual_dim != EMBEDDING_DIM:
        logger.warning(
            "MANUAL_CHUNKS.EMBEDDING is REAL_VECTOR(%d) but EMBEDDING_DIM is %d. "
            "Inserts will fail until these agree. Either set EMBEDDING_DIM=%d in "
            ".env, or drop MANUAL_CHUNKS, recreate it at width %d, and re-ingest.",
            actual_dim, EMBEDDING_DIM, actual_dim, EMBEDDING_DIM,
        )

    logger.info("Schema verified: all required tables present in '%s'", HANA_SCHEMA)


def get_embedding_column_dimension(conn) -> Optional[int]:
    """Read the declared width of MANUAL_CHUNKS.EMBEDDING, if discoverable."""
    try:
        cursor = _execute(
            conn,
            """
            SELECT LENGTH FROM TABLE_COLUMNS
            WHERE SCHEMA_NAME = ? AND TABLE_NAME = 'MANUAL_CHUNKS'
              AND COLUMN_NAME = 'EMBEDDING'
            """,
            (HANA_SCHEMA,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] else None
    except Exception:
        # Older HANA versions expose vector width differently.
        # Not being able to check is not fatal.
        return None


# ==========================================================
# Helpers
# ==========================================================

def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _vector_literal(embedding: Iterable[float]) -> str:
    """Format a Python list of floats as a HANA TO_REAL_VECTOR literal."""
    return "[" + ",".join(f"{float(x):.7f}" for x in embedding) + "]"


# ==========================================================
# Users
# ==========================================================
#
# Accounts are created ONLY by an administrator through
# backend/scripts/manage_users.py. Nothing in the HTTP API
# creates, registers or self-provisions a user.
# ----------------------------------------------------------

def create_user(
    username: str,
    password_hash: str,
    role: str,
    full_name: Optional[str] = None,
) -> str:
    """Insert a new login account. Returns its generated USER_ID."""
    user_id = _new_id()
    with get_connection() as conn:
        _execute(
            conn,
            """
            INSERT INTO USERS
                (USER_ID, USERNAME, PASSWORD_HASH, FULL_NAME, ROLE,
                 IS_ACTIVE, CREATED_AT)
            VALUES (?, ?, ?, ?, ?, TRUE, ?)
            """,
            (user_id, username.lower(), password_hash, full_name, role.upper(), _now()),
        )
        conn.commit()
    logger.info("Created user '%s' with role %s", username, role.upper())
    return user_id


def get_user_by_username(username: str) -> Optional[dict]:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            """
            SELECT USER_ID, USERNAME, PASSWORD_HASH, FULL_NAME, ROLE,
                   IS_ACTIVE, CREATED_AT, LAST_LOGIN_AT
            FROM USERS WHERE USERNAME = ?
            """,
            (username.lower(),),
        )
        return _row_as_dict(cursor)


def get_user_by_id(user_id: str) -> Optional[dict]:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            """
            SELECT USER_ID, USERNAME, PASSWORD_HASH, FULL_NAME, ROLE,
                   IS_ACTIVE, CREATED_AT, LAST_LOGIN_AT
            FROM USERS WHERE USER_ID = ?
            """,
            (user_id,),
        )
        return _row_as_dict(cursor)


def list_users() -> list[dict]:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            """
            SELECT USER_ID, USERNAME, FULL_NAME, ROLE, IS_ACTIVE,
                   CREATED_AT, LAST_LOGIN_AT
            FROM USERS ORDER BY ROLE, USERNAME
            """,
        )
        return _rows_as_dicts(cursor)


def update_last_login(user_id: str) -> None:
    with get_connection() as conn:
        _execute(
            conn,
            "UPDATE USERS SET LAST_LOGIN_AT = ? WHERE USER_ID = ?",
            (_now(), user_id),
        )
        conn.commit()


def set_user_password(username: str, password_hash: str) -> bool:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            "UPDATE USERS SET PASSWORD_HASH = ? WHERE USERNAME = ?",
            (password_hash, username.lower()),
        )
        changed = cursor.rowcount
        conn.commit()
    return changed > 0


def set_user_active(username: str, is_active: bool) -> bool:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            "UPDATE USERS SET IS_ACTIVE = ? WHERE USERNAME = ?",
            (is_active, username.lower()),
        )
        changed = cursor.rowcount
        conn.commit()
    return changed > 0


def count_users() -> int:
    with get_connection() as conn:
        cursor = _execute(conn, "SELECT COUNT(*) FROM USERS")
        (count,) = cursor.fetchone()
    return count


# ==========================================================
# Manuals
# ==========================================================

def insert_manual(
    file_name: str,
    title: Optional[str] = None,
    aircraft_type: Optional[str] = None,
    file_hash: Optional[str] = None,
    page_count: Optional[int] = None,
    chunk_count: Optional[int] = None,
    embed_model: Optional[str] = None,
) -> str:
    """Insert a manual record and return its generated MANUAL_ID."""
    manual_id = _new_id()
    with get_connection() as conn:
        _execute(
            conn,
            """
            INSERT INTO MANUALS
                (MANUAL_ID, FILE_NAME, TITLE, AIRCRAFT_TYPE, FILE_HASH,
                 PAGE_COUNT, CHUNK_COUNT, EMBED_MODEL, UPLOADED_AT)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manual_id, file_name, title, aircraft_type, file_hash,
                page_count, chunk_count, embed_model, _now(),
            ),
        )
        conn.commit()
    logger.info("Inserted manual '%s' (id=%s)", file_name, manual_id)
    return manual_id


def get_manual_by_file_name(file_name: str) -> Optional[dict]:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            """
            SELECT MANUAL_ID, FILE_NAME, TITLE, AIRCRAFT_TYPE, FILE_HASH,
                   PAGE_COUNT, CHUNK_COUNT, EMBED_MODEL, UPLOADED_AT
            FROM MANUALS WHERE FILE_NAME = ?
            """,
            (file_name,),
        )
        return _row_as_dict(cursor)


def list_manuals() -> list[dict]:
    with get_connection() as conn:
        cursor = _execute(
            conn,
            """
            SELECT MANUAL_ID, FILE_NAME, TITLE, AIRCRAFT_TYPE,
                   PAGE_COUNT, CHUNK_COUNT, EMBED_MODEL, UPLOADED_AT
            FROM MANUALS ORDER BY UPLOADED_AT DESC
            """,
        )
        return _rows_as_dicts(cursor)


def delete_manual_and_chunks(manual_id: str) -> None:
    """Remove a manual and all of its chunks (used for re-ingestion)."""
    with get_connection() as conn:
        _execute(conn, "DELETE FROM MANUAL_CHUNKS WHERE MANUAL_ID = ?", (manual_id,))
        _execute(conn, "DELETE FROM MANUALS WHERE MANUAL_ID = ?", (manual_id,))
        conn.commit()


# ==========================================================
# Manual Chunks + Semantic Search
# ==========================================================

def insert_chunks_bulk(
    manual_id: str,
    file_name: str,
    chunks: list[dict],
    embeddings: list[list[float]],
) -> int:
    """
    Insert many chunks at once. Returns the number of rows inserted.

    `chunks` is a list of dicts shaped {"page_number": int, "text": str}
    so every stored chunk keeps the page it came from - that is what
    lets the copilot cite "[A320-AMM.pdf, p.147]" accurately.

    TO_REAL_VECTOR takes a bind parameter, so this is a real
    executemany rather than thousands of interpolated statements.
    """
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must be the same length")

    now = _now()
    rows = [
        (
            _new_id(),
            manual_id,
            file_name,
            chunk.get("page_number"),
            index,
            chunk["text"],
            _vector_literal(embedding),
            now,
        )
        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO MANUAL_CHUNKS
                (CHUNK_ID, MANUAL_ID, FILE_NAME, PAGE_NUMBER, CHUNK_INDEX,
                 CONTENT, EMBEDDING, CREATED_AT)
            VALUES (?, ?, ?, ?, ?, ?, TO_REAL_VECTOR(?), ?)
            """,
            rows,
        )
        conn.commit()

    logger.info("Inserted %d chunks for manual %s", len(rows), manual_id)
    return len(rows)


def semantic_search(
    query_embedding: Iterable[float],
    top_k: int = 5,
    min_score: float = 0.0,
) -> list[dict]:
    """
    Return the `top_k` manual chunks most similar to `query_embedding`,
    using SAP HANA Cloud's native COSINE_SIMILARITY vector function.

    Chunks scoring below `min_score` are dropped. Cosine similarity
    always returns the top-k even when nothing relevant exists, so
    this floor is what lets the agent honestly say "not in the manuals"
    instead of reasoning over five unrelated passages.

    Each result dict contains:
        CHUNK_ID, MANUAL_ID, FILE_NAME, PAGE_NUMBER, CONTENT, SCORE
    """
    with get_connection() as conn:
        cursor = _execute(
            conn,
            f"""
            SELECT TOP {int(top_k)}
                   CHUNK_ID,
                   MANUAL_ID,
                   FILE_NAME,
                   PAGE_NUMBER,
                   CONTENT,
                   COSINE_SIMILARITY(EMBEDDING, TO_REAL_VECTOR(?)) AS SCORE
            FROM MANUAL_CHUNKS
            ORDER BY SCORE DESC
            """,
            (_vector_literal(query_embedding),),
        )
        results = _rows_as_dicts(cursor)

    for result in results:
        result["CONTENT"] = _as_text(result.get("CONTENT"))
        result["SCORE"] = float(result.get("SCORE") or 0.0)

    return [r for r in results if r["SCORE"] >= min_score]


def count_chunks() -> int:
    with get_connection() as conn:
        cursor = _execute(conn, "SELECT COUNT(*) FROM MANUAL_CHUNKS")
        (count,) = cursor.fetchone()
    return count


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
    technician_user_id: Optional[str] = None,
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
                LOCATION, RECOMMENDED_ACTION, TECHNICIAN, TECHNICIAN_USER_ID,
                INSPECTION_TS, STATUS, CREATED_AT
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                technician_user_id,
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
        "RECOMMENDED_ACTION", "TECHNICIAN", "TECHNICIAN_USER_ID",
        "INSPECTION_TS", "STATUS",
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
        record = _row_as_dict(cursor)

    if record:
        record["FINDING"] = _as_text(record.get("FINDING"))
        record["RECOMMENDED_ACTION"] = _as_text(record.get("RECOMMENDED_ACTION"))
    return record


def list_maintenance_records(
    aircraft_reg: Optional[str] = None,
    technician_user_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """
    List records, newest first.

    Pass `technician_user_id` to restrict the result to one person's
    own findings - that is how a technician's view is scoped, while
    a supervisor sees everything.
    """
    clauses = []
    params: list[Any] = []

    if aircraft_reg:
        clauses.append("AIRCRAFT_REG = ?")
        params.append(aircraft_reg)
    if technician_user_id:
        clauses.append("TECHNICIAN_USER_ID = ?")
        params.append(technician_user_id)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_connection() as conn:
        cursor = _execute(
            conn,
            f"SELECT TOP {int(limit)} * FROM MAINTENANCE_RECORDS "
            f"{where} ORDER BY CREATED_AT DESC",
            tuple(params) if params else None,
        )
        records = _rows_as_dicts(cursor)

    for record in records:
        record["FINDING"] = _as_text(record.get("FINDING"))
        record["RECOMMENDED_ACTION"] = _as_text(record.get("RECOMMENDED_ACTION"))
    return records


# ==========================================================
# Conversations
# ==========================================================

def insert_conversation_message(
    role: str,
    message: str,
    record_id: Optional[str] = None,
) -> str:
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
        turns = _rows_as_dicts(cursor)

    for turn in turns:
        turn["MESSAGE"] = _as_text(turn.get("MESSAGE"))
    return turns


# ==========================================================
# CLI entry point - `python -m backend.database` verifies the schema.
# ==========================================================

if __name__ == "__main__":
    logging.basicConfig(level=LOG_LEVEL)
    init_db()
    print("Database schema verified successfully.")
    print(f"  users:  {count_users()}")
    print(f"  chunks: {count_chunks()}")