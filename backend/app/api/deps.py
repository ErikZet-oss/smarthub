from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from app.services.sections_unlock import verify_sections_unlock_token


@dataclass(frozen=True)
class AuthUserContext:
    id: int
    username: str
    is_admin: bool


def get_current_user(request: Request) -> AuthUserContext:
    u = getattr(request.state, "smarthub_user", None)
    if not isinstance(u, AuthUserContext):
        raise HTTPException(status_code=401, detail="Vyžaduje sa prihlásenie.")
    return u


def require_admin(
    user: AuthUserContext = Depends(get_current_user),
) -> AuthUserContext:
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Túto akciu môže vykonať len administrátor.",
        )
    return user


def require_sections_unlock(
    request: Request,
    user: AuthUserContext = Depends(get_current_user),
) -> AuthUserContext:
    """Admin alebo používateľ s platným tokenom odomknutia (hlavička X-Sections-Unlock)."""
    if user.is_admin:
        return user
    raw = (request.headers.get("x-sections-unlock") or "").strip()
    token = raw[7:].strip() if raw.lower().startswith("bearer ") else raw
    if verify_sections_unlock_token(token, user.id):
        return user
    raise HTTPException(
        status_code=403,
        detail="Pre prístup k tejto sekcii zadaj heslo odomknutia.",
    )
