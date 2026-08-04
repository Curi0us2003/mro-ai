-- ==============================================================
-- AI Maintenance Voice Copilot
-- Migration: damage-inspection photos
-- --------------------------------------------------------------
--
-- WHY A SEPARATE TABLE RATHER THAN COLUMNS ON MAINTENANCE_RECORDS
--
--   1. MAINTENANCE_RECORDS is read with SELECT * on *every*
--      conversation turn (backend.database.get_maintenance_record,
--      called from the streaming turn handler to refresh the finding
--      card). A BLOB living on that row would drag every photo's
--      bytes across the wire on every single turn.
--   2. A technician photographs damage from several angles, so the
--      relationship is genuinely one-to-many.
--   3. Photos can be deleted (a bad shot) without touching the
--      audit-relevant finding row.
--
-- WHY A BLOB RATHER THAN A FILE PATH
--
--   These images are evidence attached to a maintenance record and
--   reproduced in the PDF report. On SAP BTP / Cloud Foundry the
--   container filesystem is ephemeral - a restart or rescale would
--   silently orphan every path. Keeping the bytes in HANA Cloud means
--   the evidence survives with the record it belongs to.
--
-- HOW TO RUN
--
--   The application's HANA user has DML rights only - it cannot
--   create tables (verified: SQL error 258, insufficient privilege).
--   Run this once through a privileged connection:
--
--     BTP Cockpit -> your HANA Cloud instance -> "SAP HANA Database
--     Explorer" -> open a SQL console on the schema below -> paste
--     this file -> Run.
--
--   Substitute your schema name if it differs from .env HANA_SCHEMA.
--
--   The application detects whether this table exists and simply
--   hides the photo feature until it does, so running this is safe
--   at any time and nothing breaks before you do.
-- ==============================================================

SET SCHEMA C811C24A3FD74FD3BE60512DADA49257;

CREATE TABLE RECORD_PHOTOS (
    PHOTO_ID     NVARCHAR(36)  PRIMARY KEY,
    RECORD_ID    NVARCHAR(36)  NOT NULL,

    -- Original upload name, kept for the audit trail only; never
    -- used to build a path.
    FILE_NAME    NVARCHAR(255),

    -- Always image/jpeg or image/png after server-side processing.
    MIME_TYPE    NVARCHAR(100),

    BYTE_SIZE    INTEGER,
    WIDTH        INTEGER,
    HEIGHT       INTEGER,

    -- Optional technician note: "looking outboard from the wheel well".
    CAPTION      NVARCHAR(500),

    -- The processed image itself (re-encoded, EXIF stripped, capped
    -- to a sane resolution before it ever reaches here).
    IMAGE_DATA   BLOB,

    -- USERS.USER_ID of whoever attached it.
    UPLOADED_BY  NVARCHAR(36),

    CREATED_AT   TIMESTAMP
);

-- Every read is "all photos for this record, oldest first".
CREATE INDEX IDX_RECORD_PHOTOS_RECORD
    ON RECORD_PHOTOS (RECORD_ID, CREATED_AT);

-- ==============================================================
-- Verify
-- ==============================================================
-- SELECT COUNT(*) FROM RECORD_PHOTOS;
--
-- To roll back (drops all attached photos):
-- DROP TABLE RECORD_PHOTOS;
