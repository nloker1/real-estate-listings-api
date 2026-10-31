"""Single-owner admin authentication; no provider calls or database access."""
import base64
from collections import OrderedDict
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request, Response

COOKIE = "realty_admin"
COOKIE_PATH = "/api/admin"
SESSION_SECONDS = 8 * 60 * 60
_attempts = OrderedDict()


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def auth_config():
    username = os.getenv("ADMIN_USERNAME", "")
    password_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
    secret = os.getenv("ADMIN_SESSION_SECRET", "")
    try:
        scheme, salt, digest = password_hash.split("$")
        valid_hash = scheme == "scrypt" and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32
    except ValueError:
        valid_hash = False
    if not username or not valid_hash or len(secret) < 32:
        raise HTTPException(503, "Admin sign-in is not configured", headers={"Cache-Control": "no-store"})
    return username, password_hash, secret


def secure_cookie():
    return os.getenv("APP_ENV", "").lower() not in {"test", "development", "preview"}


def verify_password(password, encoded):
    _, salt, _ = encoded.split("$")
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), encoded)


def origin_check(request):
    origin = request.headers.get("origin", "").rstrip("/")
    allowed = {value.strip().rstrip("/") for value in os.getenv("ADMIN_ALLOWED_ORIGINS", "").split(",") if value.strip()}
    allowed.add(str(request.base_url).rstrip("/"))
    if os.getenv("APP_ENV", "").lower() in {"test", "development", "preview"}:
        allowed.update({"http://localhost:3000", "http://127.0.0.1:3000"})
    if os.getenv("APP_ENV", "").lower() == "preview":
        allowed.update({"http://localhost:3001", "http://127.0.0.1:3001"})
    if not origin or origin not in allowed:
        raise HTTPException(403, "Request origin is not allowed", headers={"Cache-Control": "no-store"})


def rate_limit(request):
    # Bounded, per-process defense. Configure a shared proxy limit for multiple workers.
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = [at for at in _attempts.pop(key, []) if at > now - 600]
    if len(attempts) >= 10:
        _attempts[key] = attempts
        raise HTTPException(429, "Too many sign-in attempts. Try again later.",
                            headers={"Retry-After": "600", "Cache-Control": "no-store"})
    _attempts[key] = attempts + [now]
    while len(_attempts) > 1024:
        _attempts.popitem(last=False)


def credential_version(username, password_hash):
    return hashlib.sha256((username + "\0" + password_hash).encode()).hexdigest()


def issue_session(username, password_hash, secret):
    payload = {"username": username, "expires": int(time.time()) + SESSION_SECONDS,
               "csrf": secrets.token_urlsafe(24), "version": credential_version(username, password_hash)}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + signature, payload


def require_admin(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie"
    username, password_hash, secret = auth_config()
    token = request.cookies.get(COOKIE, "")
    try:
        if len(token) > 2048:
            raise ValueError()
        body, signature = token.split(".")
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(body))
        if (payload["username"] != username or payload["expires"] <= time.time()
                or payload["expires"] > time.time() + SESSION_SECONDS + 60
                or payload["version"] != credential_version(username, password_hash)
                or not isinstance(payload["csrf"], str)):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "Sign in to continue", headers={"Cache-Control": "no-store"}) from None
    return payload
