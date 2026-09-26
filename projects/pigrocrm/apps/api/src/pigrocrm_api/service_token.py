"""The one bearer check every service-to-service door in this API shares: `GET
/api/tenants/` (the Orbiters hub reading the list of spaces, ORB-142) and the two
engagements routes (`routers/engagements.py`, spec 2026-09-25 § 2.3/§ 2.4). Lifted out
of `routers/tenants.py`'s own inline check so a second door does not have to copy it
byte for byte.
"""

import secrets

from fastapi import HTTPException, status


def require_service_token(configured: str, authorization: str | None) -> None:
    """Without `configured` the door does not exist: 404, so nothing says there is one
    to knock on. With it, a missing or wrong bearer is a 401. Compared in constant time
    on bytes, not `str`: Starlette decodes headers as latin-1, and `compare_digest`
    refuses a `str` with a non-ASCII character, which would turn a stray byte into a
    500 instead of the 401 every other wrong token gets."""
    if not configured:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    presented = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token non valido")
