"""What a route answers when the CRM's door cannot be asked (REB-1107)."""

from fastapi import HTTPException, status

from rebase_core.pigro_door import PigroMisconfigured, PigroUnavailable


def door_unavailable(exc: PigroUnavailable, consequence: str = "") -> HTTPException:
    """The sentence `PigroUnavailable` carries, then `consequence` (what was therefore not done),
    as a 503 when this environment cannot reach the door at all (no token, a CRM not on HTTPS)
    and a 502 when the CRM did not answer as it should: a gateway's failure, never the
    caller's."""
    code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if isinstance(exc, PigroMisconfigured)
        else status.HTTP_502_BAD_GATEWAY
    )
    return HTTPException(code, f"{exc} {consequence}".strip())
