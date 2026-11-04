"""The shopper's session: an opaque id in an httpOnly cookie. There are no accounts."""

import uuid

from fastapi import Request, Response

COOKIE = "acme_sid"
MAX_AGE = 60 * 60 * 24 * 30


def session_id(request: Request, response: Response) -> str:
    """FastAPI dependency: the caller's session id, issuing a cookie on the first visit."""
    sid = request.cookies.get(COOKIE)
    if not sid:
        sid = uuid.uuid4().hex
        response.set_cookie(COOKIE, sid, max_age=MAX_AGE, httponly=True, samesite="lax")
    return sid
