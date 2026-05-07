from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request


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
