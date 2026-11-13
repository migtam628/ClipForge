"""Session auth for sensitive ClipForge administrator configuration routes.

Existing clip-rendering routes remain unchanged during Phase 1 rollout.
The host configuration agent never accepts a browser session directly.
"""
from __future__ import annotations

import getpass
import os
import secrets
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request, Response

COOKIE = "clipforge_admin"
SESSION_TTL = 12 * 3600
LOCK = threading.RLock()
HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
SESSIONS: dict[str, "Session"] = {}
ATTEMPTS: dict[str, list[float]] = defaultdict(list)


@dataclass
class Session:
    csrf: str
    expires_at: float


def configured() -> bool:
    value = os.environ.get("CLIPFORGE_ADMIN_PASSWORD_HASH", "")
    return value.startswith("$argon2id$")


def has_session(request: Request) -> bool:
    key = request.cookies.get(COOKIE, "")
    with LOCK:
        entry = SESSIONS.get(key)
        return bool(entry and entry.expires_at > time.time())


def require_session(request: Request) -> Session:
    if not configured():
        raise HTTPException(503, "Administrator login has not been configured")
    key = request.cookies.get(COOKIE, "")
    with LOCK:
        entry = SESSIONS.get(key)
        if not entry or entry.expires_at <= time.time():
            raise HTTPException(401, "Administrator login required")
        return entry


def require_csrf(request: Request) -> Session:
    session = require_session(request)
    if not secrets.compare_digest(request.headers.get("x-clipforge-csrf", ""), session.csrf):
        raise HTTPException(403, "CSRF token required")
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit
        request_origin = urlsplit(origin)
        expected_host = request.headers.get("host", "")
        if request_origin.netloc != expected_host or request_origin.scheme != "https":
            raise HTTPException(403, "Invalid request origin")
    return session


def sign_in(request: Request, password: str, response: Response) -> str:
    if not configured():
        raise HTTPException(503, "Set CLIPFORGE_ADMIN_PASSWORD_HASH before admin login")
    addr = request.client.host if request.client else "unknown"
    now = time.time()
    with LOCK:
        attempts = [t for t in ATTEMPTS[addr] if now - t < 300]
        ATTEMPTS[addr] = attempts
        if len(attempts) >= 5:
            raise HTTPException(429, "Too many login attempts; retry later")
    try:
        HASHER.verify(os.environ["CLIPFORGE_ADMIN_PASSWORD_HASH"], password)
    except (InvalidHashError, VerificationError, VerifyMismatchError, KeyError):
        with LOCK:
            ATTEMPTS[addr].append(now)
        raise HTTPException(401, "Invalid administrator credentials") from None
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with LOCK:
        ATTEMPTS.pop(addr, None)
        for key in [k for k, v in SESSIONS.items() if v.expires_at <= now]:
            del SESSIONS[key]
        SESSIONS[token] = Session(csrf=csrf, expires_at=now + SESSION_TTL)
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL, path="/api",
                        secure=True, httponly=True, samesite="strict")
    return csrf


def sign_out(request: Request, response: Response) -> None:
    key = request.cookies.get(COOKIE, "")
    with LOCK:
        SESSIONS.pop(key, None)
    response.delete_cookie(COOKIE, path="/api", secure=True, httponly=True, samesite="strict")


if __name__ == "__main__":
    password = getpass.getpass("Create ClipForge admin password: ")
    check = getpass.getpass("Confirm password: ")
    if len(password) < 14 or password != check:
        raise SystemExit("Choose a matching password of at least 14 characters.")
    print("Set this in the server .env (do not commit it):")
    print("CLIPFORGE_ADMIN_PASSWORD_HASH='" + HASHER.hash(password) + "'")
