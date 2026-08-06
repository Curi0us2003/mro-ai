"""
==============================================================
AI Maintenance Voice Assistant
SAP Posting - Azure Blob Storage Upload
--------------------------------------------------------------

Purpose
-------
Uploads a COMPLETE record's PDF report to the Azure Blob Storage
container SAP picks up from, once a supervisor posts it. The record
becomes CLOSED (see backend.database.update_maintenance_record) only
after this upload actually succeeds.

Follows the same BlobServiceClient + SAS token pattern as the
OnM-MRP-Data-Loading loader (github.com/Curi0us2003/OnM-MRP-Data-Loading)
- that repo names its connection module "sftp.py" even though the
transport is the Azure Blob SDK, not the SFTP protocol. Kept the same
naming here for consistency with that project.
==============================================================
"""

import logging
from pathlib import Path

from backend.config import (
    AZURE_STORAGE_ACCOUNT_NAME,
    AZURE_STORAGE_CONTAINER,
    AZURE_STORAGE_SAS_TOKEN,
    AZURE_STORAGE_SAP_PREFIX,
    LOG_LEVEL,
)

logger = logging.getLogger("mro_copilot.sftp")
logger.setLevel(LOG_LEVEL)


class SapPostingNotConfiguredError(Exception):
    """AZURE_STORAGE_* settings are missing - see .env.example."""


class SapPostingError(Exception):
    """The upload itself failed (network, auth, expired SAS token, ...)."""


def upload_report_to_sap(record_id: str, pdf_path: Path) -> str:
    """
    Upload a maintenance report PDF to the SAP-inbound Azure Blob Storage
    container and return the blob name it was stored under.
    """
    if not (AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_CONTAINER and AZURE_STORAGE_SAS_TOKEN):
        raise SapPostingNotConfiguredError(
            "SAP posting isn't configured yet - set AZURE_STORAGE_ACCOUNT_NAME, "
            "AZURE_STORAGE_CONTAINER and AZURE_STORAGE_SAS_TOKEN."
        )

    from azure.storage.blob import BlobServiceClient

    account_url = f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
    blob_name = f"{AZURE_STORAGE_SAP_PREFIX}{record_id}.pdf"

    try:
        service = BlobServiceClient(account_url=account_url, credential=AZURE_STORAGE_SAS_TOKEN)
        container = service.get_container_client(AZURE_STORAGE_CONTAINER)
        with open(pdf_path, "rb") as fh:
            container.upload_blob(name=blob_name, data=fh, overwrite=True)
    except SapPostingNotConfiguredError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to upload report for record %s to Azure Blob Storage", record_id)
        raise SapPostingError(f"Could not upload the report to SAP: {exc}") from exc

    logger.info("Uploaded report for record %s to blob '%s'", record_id, blob_name)
    return blob_name
