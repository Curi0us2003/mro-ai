"""
==============================================================
AI Maintenance Voice Assistant
User Administration Script
--------------------------------------------------------------

Purpose
-------
Create and manage the login accounts for the app. This is the
ONLY way an account comes into existence - the HTTP API has no
sign-up endpoint, by design. Technicians and supervisors can
sign in; nobody can provision themselves.

Usage
-----
    # Create a technician (prompts for the password, hidden)
    python -m backend.scripts.manage_users add \\
        --username jsmith --role technician --full-name "J. Smith"

    # Create a supervisor, password on stdin (for scripting)
    python -m backend.scripts.manage_users add \\
        --username rkhan --role supervisor --full-name "R. Khan" \\
        --password-stdin < secret.txt

    # See who exists
    python -m backend.scripts.manage_users list

    # Reset someone's password
    python -m backend.scripts.manage_users passwd --username jsmith

    # Revoke access without deleting the audit trail
    python -m backend.scripts.manage_users disable --username jsmith
    python -m backend.scripts.manage_users enable  --username jsmith

Notes
-----
• Passwords are never stored or logged - only a salted hash.
• Deactivating beats deleting: maintenance records reference the
  user id, and a disabled account is rejected on the very next
  request, not whenever its cookie happens to expire.
==============================================================
"""

from __future__ import annotations

import argparse
import getpass
import logging
import sys

from backend.config import LOG_LEVEL, ROLE_SUPERVISOR, ROLE_TECHNICIAN
from backend.auth import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    validate_role,
    validate_username,
)
from backend.database import (
    create_user,
    get_user_by_username,
    init_db,
    list_users,
    set_user_active,
    set_user_password,
)

logger = logging.getLogger("mro_copilot.manage_users")


# ==========================================================
# Password input
# ==========================================================

def read_password(from_stdin: bool, confirm: bool = True) -> str:
    if from_stdin:
        password = sys.stdin.read().strip()
        if not password:
            sys.exit("No password received on stdin.")
        return password

    password = getpass.getpass("Password: ")
    if confirm:
        again = getpass.getpass("Confirm password: ")
        if password != again:
            sys.exit("Passwords do not match.")

    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    return password


# ==========================================================
# Commands
# ==========================================================

def cmd_add(args) -> None:
    try:
        username = validate_username(args.username)
        role = validate_role(args.role)
    except ValueError as exc:
        sys.exit(str(exc))

    if get_user_by_username(username):
        sys.exit(f"A user named '{username}' already exists.")

    password = read_password(args.password_stdin)

    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        sys.exit(str(exc))

    user_id = create_user(
        username=username,
        password_hash=password_hash,
        role=role,
        full_name=args.full_name or username,
    )

    print(f"Created {role.lower()} '{username}' ({user_id}).")
    print("They can sign in at the app's login screen now.")


def cmd_list(args) -> None:
    users = list_users()
    if not users:
        print("No accounts yet. Create one with `manage_users add`.")
        return

    print(f"{'USERNAME':<20} {'ROLE':<12} {'ACTIVE':<7} {'NAME':<24} LAST SIGN-IN")
    for user in users:
        last = user.get("LAST_LOGIN_AT")
        last_text = last.strftime("%Y-%m-%d %H:%M") if last else "never"
        print(
            f"{user['USERNAME']:<20} "
            f"{user['ROLE']:<12} "
            f"{'yes' if user['IS_ACTIVE'] else 'no':<7} "
            f"{(user.get('FULL_NAME') or ''):<24} "
            f"{last_text}"
        )
    print(f"\n{len(users)} account(s).")


def cmd_passwd(args) -> None:
    username = args.username.strip().lower()
    if not get_user_by_username(username):
        sys.exit(f"No user named '{username}'.")

    password = read_password(args.password_stdin)

    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        sys.exit(str(exc))

    set_user_password(username, password_hash)
    print(f"Password updated for '{username}'.")


def cmd_disable(args) -> None:
    username = args.username.strip().lower()
    if not set_user_active(username, False):
        sys.exit(f"No user named '{username}'.")
    print(f"Disabled '{username}'. Their next request will be rejected.")


def cmd_enable(args) -> None:
    username = args.username.strip().lower()
    if not set_user_active(username, True):
        sys.exit(f"No user named '{username}'.")
    print(f"Enabled '{username}'.")


# ==========================================================
# CLI
# ==========================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create and manage AI Maintenance Voice Assistant accounts."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="create a new account")
    p_add.add_argument("--username", required=True)
    p_add.add_argument(
        "--role",
        required=True,
        choices=[ROLE_TECHNICIAN.lower(), ROLE_SUPERVISOR.lower()],
    )
    p_add.add_argument("--full-name", help="display name shown in the app and on reports")
    p_add.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin instead of prompting",
    )
    p_add.set_defaults(func=cmd_add)

    sub.add_parser("list", help="list all accounts").set_defaults(func=cmd_list)

    p_passwd = sub.add_parser("passwd", help="reset a password")
    p_passwd.add_argument("--username", required=True)
    p_passwd.add_argument("--password-stdin", action="store_true")
    p_passwd.set_defaults(func=cmd_passwd)

    p_disable = sub.add_parser("disable", help="revoke access")
    p_disable.add_argument("--username", required=True)
    p_disable.set_defaults(func=cmd_disable)

    p_enable = sub.add_parser("enable", help="restore access")
    p_enable.add_argument("--username", required=True)
    p_enable.set_defaults(func=cmd_enable)

    args = parser.parse_args()

    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)-7s %(message)s")

    init_db()
    args.func(args)


if __name__ == "__main__":
    main()