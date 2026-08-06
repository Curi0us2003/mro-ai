"""
==============================================================
AI Maintenance Voice Assistant
Data Cleanup Script
--------------------------------------------------------------

Purpose
-------
Remove rows that should not be in the schema: findings filed by
test traffic, child rows left dangling by a record that was
deleted outside the app, and the throwaway accounts that filed
them.

Why this exists
---------------
A maintenance record is INSERTed the moment the technician first
names an aircraft and a component, so anything that drives the
technician flow - including an end-to-end test run - leaves real
rows behind. Deleting them straight from MAINTENANCE_RECORDS in
HANA Database Explorer removes the finding but silently leaves its
CONVERSATIONS turns and RECORD_PHOTOS blobs pointing at a record
id that no longer resolves; there is no ON DELETE CASCADE. This
script (and delete_maintenance_record, which the supervisor's
"Discard record" button calls) is the supported way to do it.

Usage
-----
    # What is in there? Changes nothing.
    python -m backend.scripts.cleanup_data report

    # Delete one finding and its transcript + photos
    python -m backend.scripts.cleanup_data record --record-id <uuid> --yes

    # Delete every finding filed by an account, then the account
    python -m backend.scripts.cleanup_data user --username verify.tech1 --yes

    # Sweep up child rows whose record is already gone
    python -m backend.scripts.cleanup_data orphans --yes

Nothing is deleted without --yes: every command prints what it
would do and stops there otherwise. A CLOSED record has been
posted to SAP and is refused everywhere - clean those up in HANA
directly if you truly must.
==============================================================
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.config import LOG_LEVEL
from backend.database import (
    count_orphan_rows,
    count_records_for_user,
    delete_maintenance_record,
    delete_user,
    get_maintenance_record,
    get_user_by_username,
    init_db,
    list_maintenance_records,
    purge_orphan_rows,
)

logger = logging.getLogger("mro_copilot.cleanup_data")


def _describe(record: dict) -> str:
    return (
        f"{record['RECORD_ID']}  "
        f"{(record.get('AIRCRAFT_REG') or '—'):<10} "
        f"{(record.get('COMPONENT') or '—'):<28} "
        f"{(record.get('STATUS') or '—'):<9} "
        f"{(record.get('TECHNICIAN') or '—'):<20} "
        f"{record.get('CREATED_AT')}"
    )


# ==========================================================
# Commands
# ==========================================================

def cmd_report(_args) -> None:
    counts = count_orphan_rows()

    print("Child rows whose maintenance record no longer exists")
    print(f"  conversation turns : {counts['dangling_conversations']}")
    print(f"  photos             : {counts['dangling_photos']}")
    print("\nConversation turns never linked to a record (normal - the assistant")
    print("logs manual questions that never became a finding)")
    print(f"  turns              : {counts['unlinked_conversations']}")

    if counts["dangling_conversations"] or counts["dangling_photos"]:
        print(
            "\nThose dangling rows mean a record was deleted outside the app. "
            "Clear them with:\n  python -m backend.scripts.cleanup_data orphans --yes"
        )
    else:
        print("\nNo dangling rows - every child row resolves to a real record.")


def cmd_orphans(args) -> None:
    counts = count_orphan_rows()
    doomed = counts["dangling_conversations"] + counts["dangling_photos"]
    if args.include_unlinked:
        doomed += counts["unlinked_conversations"]

    if not doomed:
        print("Nothing to purge.")
        return

    print(f"Would delete {counts['dangling_conversations']} dangling conversation "
          f"turn(s) and {counts['dangling_photos']} dangling photo(s).")
    if args.include_unlinked:
        print(f"Would also delete {counts['unlinked_conversations']} unlinked "
              f"conversation turn(s) (--include-unlinked).")

    if not args.yes:
        print("\nDry run. Re-run with --yes to actually delete.")
        return

    removed = purge_orphan_rows(include_unlinked_conversations=args.include_unlinked)
    print(f"Deleted {removed['dangling_conversations']} dangling conversation turn(s), "
          f"{removed['dangling_photos']} dangling photo(s), "
          f"{removed['unlinked_conversations']} unlinked turn(s).")


def cmd_record(args) -> None:
    record = get_maintenance_record(args.record_id)
    if not record:
        sys.exit(f"No record with id '{args.record_id}'.")

    print("Would delete this finding, with its transcript and photos:")
    print(" ", _describe(record))

    if not args.yes:
        print("\nDry run. Re-run with --yes to actually delete.")
        return

    try:
        delete_maintenance_record(args.record_id)
    except ValueError as exc:
        sys.exit(str(exc))
    print("Deleted.")


def cmd_user(args) -> None:
    username = args.username.strip().lower()
    user = get_user_by_username(username)
    if not user:
        sys.exit(f"No user named '{username}'.")

    records = list_maintenance_records(
        technician_user_id=user["USER_ID"], limit=200
    )

    print(f"Account '{username}' ({user['ROLE']}, "
          f"{'active' if user['IS_ACTIVE'] else 'disabled'}) "
          f"is credited on {count_records_for_user(user['USER_ID'])} record(s).")

    closed = [r for r in records if r.get("STATUS") == "CLOSED"]
    if closed:
        print(f"\n{len(closed)} of them are CLOSED (posted to SAP) and cannot be "
              f"deleted. Nothing will be removed - this account has a real audit "
              f"trail. Disable it instead:\n"
              f"  python -m backend.scripts.manage_users disable --username {username}")
        return

    if records:
        print("\nWould delete these findings, with their transcripts and photos:")
        for record in records:
            print(" ", _describe(record))

    print(f"\nWould then delete the account '{username}' itself.")

    if not args.yes:
        print("\nDry run. Re-run with --yes to actually delete.")
        return

    for record in records:
        delete_maintenance_record(record["RECORD_ID"])
    print(f"Deleted {len(records)} record(s).")

    try:
        delete_user(username)
    except ValueError as exc:
        sys.exit(str(exc))
    print(f"Deleted account '{username}'.")


# ==========================================================
# CLI
# ==========================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove test findings, dangling child rows and throwaway accounts."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "report", help="show what test/orphan data is in the schema (read-only)"
    ).set_defaults(func=cmd_report)

    p_orphans = sub.add_parser(
        "orphans", help="delete child rows whose record no longer exists"
    )
    p_orphans.add_argument(
        "--include-unlinked",
        action="store_true",
        help="also delete conversation turns that were never linked to a record",
    )
    p_orphans.add_argument("--yes", action="store_true", help="actually delete")
    p_orphans.set_defaults(func=cmd_orphans)

    p_record = sub.add_parser(
        "record", help="delete one finding, with its transcript and photos"
    )
    p_record.add_argument("--record-id", required=True)
    p_record.add_argument("--yes", action="store_true", help="actually delete")
    p_record.set_defaults(func=cmd_record)

    p_user = sub.add_parser(
        "user", help="delete an account's findings and then the account"
    )
    p_user.add_argument("--username", required=True)
    p_user.add_argument("--yes", action="store_true", help="actually delete")
    p_user.set_defaults(func=cmd_user)

    args = parser.parse_args()

    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)-7s %(message)s")

    init_db()
    args.func(args)


if __name__ == "__main__":
    main()
