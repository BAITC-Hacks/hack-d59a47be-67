"""Server-controlled demo identities; passwords and session tokens are never logged."""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone

from fastapi import Request

from .errors import APIError

COOKIE = "cq_session"
ITERATIONS = 600_000


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${value}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(iterations) <= 1_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def require_origin(request: Request) -> None:
    if request.headers.get("origin") not in request.app.state.settings.allowed_origins:
        raise APIError(403, "ORIGIN_FORBIDDEN", "An allowed Origin header is required.")


def identity(row) -> dict:
    return {key: row[key] for key in ("id", "username", "role", "employee_id")}


def require_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 256:
        raise APIError(401, "UNAUTHENTICATED", "Sign in to continue.")
    with request.app.state.database.connect() as conn:
        row = conn.execute(
            "SELECT a.*, s.csrf_hash FROM sessions s JOIN accounts a ON a.id=s.account_id "
            "WHERE s.token_hash=? AND s.expires_at>?",
            (digest(token), int(time.time())),
        ).fetchone()
    if row is None:
        raise APIError(401, "UNAUTHENTICATED", "Session is missing or expired.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_origin(request)
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or len(csrf) > 256 or not hmac.compare_digest(digest(csrf), row["csrf_hash"]):
            raise APIError(403, "CSRF_FAILED", "A valid X-CSRF-Token is required.")
    return identity(row)


def require_hr(request: Request) -> dict:
    user = require_user(request)
    if user["role"] != "hr":
        raise APIError(403, "FORBIDDEN", "HR access is required.")
    return user


def require_employee(request: Request, employee_id: str) -> dict:
    user = require_user(request)
    if user["role"] != "hr" and user["employee_id"] != employee_id:
        raise APIError(403, "FORBIDDEN", "This employee is outside your access scope.")
    with request.app.state.database.connect() as conn:
        found = conn.execute("SELECT 1 FROM employees WHERE id=?", (employee_id,)).fetchone()
    if found is None:
        raise APIError(404, "EMPLOYEE_NOT_FOUND", "Employee not found.")
    return user


def login(request: Request, username: str, password: str) -> tuple[dict, str]:
    require_origin(request)
    db = request.app.state.database
    now = int(time.time())
    # Never trust forwarded headers. Bound both account guessing and IP spraying.
    peer = request.client.host if request.client else "unknown"
    buckets = [("user:" + digest(username), 5), ("ip:" + digest(peer), 20)]
    blocked = False
    valid = False
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM login_attempts WHERE window_start < ? AND blocked_until < ?", (now - 900, now)
        )
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        for bucket, limit in buckets:
            row = conn.execute("SELECT * FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()
            if row is not None and row["blocked_until"] > now:
                blocked = True
        if not blocked:
            account = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
            valid = verify_password(password, account["password_hash"] if account else _DUMMY_HASH)
            if not valid:
                for bucket, limit in buckets:
                    conn.execute(
                        "INSERT INTO login_attempts(bucket,failures,window_start,blocked_until) VALUES (?,1,?,0) "
                        "ON CONFLICT(bucket) DO UPDATE SET failures=failures+1",
                        (bucket, now),
                    )
                    conn.execute(
                        "UPDATE login_attempts SET blocked_until=? WHERE bucket=? AND failures>=?",
                        (now + 900, bucket, limit),
                    )
            else:
                conn.execute("DELETE FROM login_attempts WHERE bucket=?", (buckets[0][0],))
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                expires = now + request.app.state.settings.session_ttl_seconds
                conn.execute(
                    "INSERT INTO sessions(token_hash,account_id,csrf_hash,expires_at) VALUES (?,?,?,?)",
                    (digest(token), account["id"], digest(csrf), expires),
                )
                body = {
                    "user": identity(account),
                    "csrf_token": csrf,
                    "expires_at": datetime.fromtimestamp(expires, timezone.utc),
                }
    if blocked:
        raise APIError(429, "LOGIN_RATE_LIMITED", "Too many login attempts; retry in 15 minutes.")
    if not valid:
        raise APIError(401, "INVALID_CREDENTIALS", "Invalid username or password.")
    return body, token
