"""JWT token pre odomknutie admin sekcií (Dodávatelia, Párovanie, Dev) ne-admin účtom."""

from __future__ import annotations

import os
import time

import jwt

UNLOCK_TTL_SEC = 8 * 3600


def _auth_secret() -> str:
    return os.environ.get("SMARTHUB_AUTH_SECRET", "")


def issue_sections_unlock_token(user_id: int) -> tuple[str, int]:
    secret = _auth_secret()
    if len(secret) < 16:
        raise RuntimeError("SMARTHUB_AUTH_SECRET nie je nastavený.")
    exp = int(time.time()) + UNLOCK_TTL_SEC
    token = jwt.encode(
        {"uid": user_id, "sections_unlock": True, "exp": exp},
        secret,
        algorithm="HS256",
    )
    return token, exp


def verify_sections_unlock_token(token: str, user_id: int) -> bool:
    secret = _auth_secret()
    if len(secret) < 16 or not token.strip():
        return False
    try:
        payload = jwt.decode(token.strip(), secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return bool(payload.get("sections_unlock")) and int(payload.get("uid") or -1) == user_id
