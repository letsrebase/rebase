"""The one question the CRM asks the rebase hub: does this address belong to a member?

The signup greets a community member by name and skips the questions the hub already
answered (spec 2026-09-12 §6.3, ORB-173). The hub is reached through its own API,
`POST /api/hub/members/lookup`, with the token the two hosts already share for the
registry of spaces in the other direction (ORB-142): `PIGROCRM_REGISTRY_TOKEN` here,
`REBASE_PIGRO_REGISTRY_TOKEN` there. Nothing from the hub is imported and no database
of it is opened.

The address travels in a JSON body and never in the URL: a query string is written by
uvicorn's access log and by every proxy on the way, on both hosts, a body is not.

Every failure is the same answer, `MemberLookup(membro=False)`: no token, no URL, a
refused connection, a timeout, a status other than 2xx, a redirect, a body that is not
the shape or is too long. The community is the fast lane of the signup, never a gate,
so a hub that is down must cost the person nothing but the greeting. Nothing here logs:
the only thing worth logging would be the address, and the address is the one thing
that must not reach a log.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message

# Under its own name: the `http` parameter of `lookup_member` would shadow the module.
from http.client import HTTPException as HttpProtocolError
from typing import IO

from pydantic import BaseModel, ConfigDict, ValidationError

from pigrocrm.core.config import Settings

LOOKUP_PATH = "/api/hub/members/lookup"
# Short on purpose: the wizard is waiting on this answer, and a hub that takes longer
# than this to say «member» has already cost more than the greeting is worth.
HTTP_TIMEOUT_SECONDS = 5
# The answer is three short fields; anything longer is not the hub talking.
MAX_BODY_BYTES = 4096
USER_AGENT = "pigrocrm/0.1 (+https://pigro.letsrebase.com)"

# (method, url, headers, body) -> (status, body). Narrower than the Gmail and Drive
# seams: no response header is read, and there is no retry to inform.
HttpCall = Callable[[str, str, dict[str, str], bytes], tuple[int, bytes]]


class MemberLookup(BaseModel):
    """What the hub says about an address: whether a freelancer with it exists, and the
    two names if so. Extra keys are ignored so the hub may grow its answer without
    breaking the signup."""

    model_config = ConfigDict(extra="ignore")

    membro: bool
    nome: str | None = None
    cognome: str | None = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx stays a 3xx. `urllib` would otherwise follow it and re-send the request,
    bearer included, to whatever host the `Location` names; the hub never redirects, so
    a redirect is not the hub, and the token must not travel to whoever it is."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urllib_call(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
    """`urllib`, no dependency: `packages/core` declares no HTTP client and
    `test_architecture.py` would refuse one. A 4xx, and a 3xx that `_NoRedirect` turned
    into one, is a status rather than an exception, so the caller sees «the hub refused»
    and «the hub answered» through one path. The body is read up to one byte past the
    cap, so the caller can tell «too long» from «exactly the cap»."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status), response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as error:
        return int(error.code), error.read(MAX_BODY_BYTES + 1)


def lookup_member(settings: Settings, email: str, http: HttpCall | None = None) -> MemberLookup:
    """Asks the hub whether `email` belongs to a member. Any failure of the call is
    `MemberLookup(membro=False)`, the same answer a non-member gets. What is caught is
    the network and the parse (`OSError`, which `URLError` is, `http.client`'s own
    `HTTPException`, `ValueError`) and nothing wider: a test's `AssertionError` from the network
    guard, or a programming error, must surface rather than read as «not a member»."""
    token = settings.registry_token
    base = settings.hub_url.strip().rstrip("/")
    address = email.strip().lower()
    if not token or not base or not address:
        return MemberLookup(membro=False)
    url = f"{base}{LOOKUP_PATH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    payload = json.dumps({"email": address}).encode("utf-8")
    call = http or urllib_call
    try:
        status, body = call("POST", url, headers, payload)
    except (OSError, HttpProtocolError, ValueError):
        return MemberLookup(membro=False)
    if not 200 <= status < 300 or len(body) > MAX_BODY_BYTES:
        return MemberLookup(membro=False)
    try:
        return MemberLookup.model_validate(json.loads(body))
    except (ValueError, ValidationError):
        return MemberLookup(membro=False)
