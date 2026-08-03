"""
==============================================================
AI Maintenance Voice Copilot
Authentication Layer
--------------------------------------------------------------

Purpose
-------
Log people in, keep them logged in, and gate every endpoint by
role.

Design decision: there is NO self-service registration anywhere
in this application. Accounts exist only because an administrator
created them with:

    python -m backend.scripts.manage_users add \\
        --username jsmith --role technician --full-name "J. Smith"

The HTTP API can authenticate a user and nothing else. It cannot
create, invite, or elevate one.

How sessions work
-----------------
Flask's own signed-cookie session, signed with SECRET_KEY. The
cookie holds only the user id and role; every request re-reads
the account from the database, so deactivating someone takes
effect on their next request rather than whenever their cookie
happens to expire.

IMPORTANT
---------
This module never reads environment variables directly.
All settings come from backend.config.
==============================================================
"""

from __future__ import annotations

import logging
import re
from functools import wraps
from typing import Callable, Optional

from flask import g, jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

from backend.config import (
    ROLE_SUPERVISOR,
    ROLE_TECHNICIAN,
    VALID_ROLES,
    LOG_LEVEL,
)
from backend.database import (
    get_user_by_id,
    get_user_by_username,
    update_last_login,
)

logger = logging.getLogger("mro_copilot.auth")
logger.setLevel(LOG_LEVEL)

SESSION_USER_KEY = "user_id"

USERNAME_PATTERN = re.compile(r"^[a-z0-9._-]{3,64}$")

MIN_PASSWORD_LENGTH = 8

# A real hash of a value nobody will ever submit. Used to burn the
# same CPU time on an unknown username as on a wrong password, so
# the endpoint's response time does not reveal which accounts exist.
_DUMMY_HASH = generate_password_hash("2f2fbe4c-no-such-account")


class InvalidCredentialsError(ValueError):
    """Raised when a login attempt fails for any reason."""


class AccountDisabledError(ValueError):
    """Raised when a valid account has been deactivated."""


# ==========================================================
# Password handling
# ==========================================================

def hash_password(plain_password: str) -> str:
    """
    Hash a password for storage. Uses Werkzeug's default
    (scrypt on modern versions, pbkdf2 otherwise) - salted,
    slow, and versioned in the stored string itself.
    """
    if len(plain_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    return generate_password_hash(plain_password)


def verify_password(stored_hash: str, plain_password: str) -> bool:
    return check_password_hash(stored_hash, plain_password)


def validate_username(username: str) -> str:
    """Normalise and check a username. Returns the normalised form."""
    normalised = (username or "").strip().lower()
    if not USERNAME_PATTERN.match(normalised):
        raise ValueError(
            "Username must be 3-64 characters, using only lowercase letters, "
            "digits, dot, underscore or hyphen."
        )
    return normalised


def validate_role(role: str) -> str:
    normalised = (role or "").strip().upper()
    if normalised not in VALID_ROLES:
        raise ValueError(f"Role must be one of: {', '.join(VALID_ROLES)}")
    return normalised


# ==========================================================
# Login / logout
# ==========================================================

def authenticate(username: str, password: str) -> dict:
    """
    Check credentials and return the user record on success.

    Raises InvalidCredentialsError for both an unknown username and
    a wrong password - deliberately the same error, so the response
    cannot be used to discover which accounts exist.
    """
    user = get_user_by_username((username or "").strip().lower())

    if not user:
        # Still spend the time a real check would, so response
        # timing does not leak whether the account exists.
        check_password_hash(_DUMMY_HASH, password or "")
        raise InvalidCredentialsError("Incorrect username or password.")

    if not verify_password(user["PASSWORD_HASH"], password or ""):
        raise InvalidCredentialsError("Incorrect username or password.")

    if not user.get("IS_ACTIVE"):
        raise AccountDisabledError(
            "This account has been deactivated. Contact your administrator."
        )

    return user


def login_session(user: dict) -> None:
    """Attach an authenticated user to the current browser session."""
    session.clear()
    session[SESSION_USER_KEY] = user["USER_ID"]
    session.permanent = True
    update_last_login(user["USER_ID"])
    logger.info("User '%s' signed in (%s)", user["USERNAME"], user["ROLE"])


def logout_session() -> None:
    session.clear()


def current_user() -> Optional[dict]:
    """
    Return the logged-in user for this request, or None.

    Cached on `g` so a request that checks the role and then reads
    the user does not hit the database twice.
    """
    if "current_user" in g:
        return g.current_user

    user_id = session.get(SESSION_USER_KEY)
    if not user_id:
        g.current_user = None
        return None

    user = get_user_by_id(user_id)
    if not user or not user.get("IS_ACTIVE"):
        # Account deleted or deactivated since the cookie was issued.
        session.clear()
        g.current_user = None
        return None

    g.current_user = user
    return user


def public_user(user: dict) -> dict:
    """The subset of a user record that is safe to send to the browser."""
    return {
        "user_id": user["USER_ID"],
        "username": user["USERNAME"],
        "full_name": user.get("FULL_NAME") or user["USERNAME"],
        "role": user["ROLE"],
    }


# ==========================================================
# Endpoint decorators
# ==========================================================

def login_required(view: Callable) -> Callable:
    """Reject anonymous requests with 401."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return jsonify({"error": "Sign in to continue."}), 401
        return view(*args, **kwargs)

    return wrapper


def role_required(*roles: str) -> Callable:
    """Reject requests from a signed-in user whose role is not listed."""
    allowed = {r.upper() for r in roles}

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return jsonify({"error": "Sign in to continue."}), 401
            if user["ROLE"] not in allowed:
                return jsonify(
                    {"error": "Your role does not have access to this."}
                ), 403
            return view(*args, **kwargs)

        return wrapper

    return decorator


def is_supervisor(user: Optional[dict] = None) -> bool:
    user = user or current_user()
    return bool(user) and user["ROLE"] == ROLE_SUPERVISOR


def is_technician(user: Optional[dict] = None) -> bool:
    user = user or current_user()
    return bool(user) and user["ROLE"] == ROLE_TECHNICIAN