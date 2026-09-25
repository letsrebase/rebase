"""One HTTP seam for everything the hub sends out: the conversions pixel and the mail.

`HttpCall` is `(method, url, headers, body) -> (status, body)`. Production hands
`urllib_call`; a test hands a fake and reads what would have left. No dependency, no
retry, and a network error travels as `NETWORK_ERROR_STATUS` rather than as a second
failure path, the convention PigroCRM's transports set.
"""

import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import IO

HTTP_TIMEOUT_SECONDS = 10
# The CRM's door for rebase's engagements (REB-498) provisions a whole database the first
# time a freelancer is linked, which takes longer than the ten seconds above: that one
# client reads through `urllib_engagements_call`, which waits this long.
ENGAGEMENTS_TIMEOUT_SECONDS = 90
# Both endpoints the hub calls, api.resend.com and bzr.openai.com, sit behind Cloudflare,
# which answers `403 error code: 1010` to urllib's default `Python-urllib/3.x` signature
# and never reaches the application behind it. Found on 2026-09-10 by sending a mail for
# real: curl with the same JSON went through, the seam did not. The name says who calls.
USER_AGENT = "rebase-hub/0.1 (+https://letsrebase.com)"
# "No HTTP response was ever received", travelling through the same channel as a real
# status rather than a second failure path -- the convention PigroCRM's Gmail and Drive
# transports use, and the same number.
NETWORK_ERROR_STATUS = 599
# Generous next to PigroCRM's mirror-image seam's 4096 (which answers three short
# fields): this one also carries the whole Pigro tenant registry, not just Resend's
# `{"id": ...}`. Still a cap, so a wrong or hostile endpoint cannot decide how many
# bytes this process allocates.
MAX_BODY_BYTES = 1_048_576

# The sealed contracts the Documenso client downloads (REB-387) run to a few hundred
# kilobytes, and a long framework agreement with Documenso's certificate page must never
# meet the cap above: that client reads through `urllib_download_call`, capped here.
MAX_DOWNLOAD_BYTES = 16 * 1_048_576

# (method, url, headers, body) -> (status, body). Narrower than the Gmail seam on
# purpose: there is no retry here, so a `Retry-After` would have nothing to inform.
HttpCall = Callable[[str, str, dict[str, str], bytes], tuple[int, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx stays a 3xx. `urllib` would otherwise follow it and re-send the request,
    bearer included, to whatever host the `Location` names -- Resend and the Pigro
    registry never redirect, so a redirect is not either of them, and the token must
    not travel to whoever it is. The same shape as PigroCRM's twin of this seam
    (`pigrocrm.core.tenants.hub`)."""

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


def _open(
    method: str, url: str, headers: dict[str, str], body: bytes, cap: int, timeout: float
) -> tuple[int, bytes]:
    sent = {"User-Agent": USER_AGENT, **headers}
    # `None` rather than `b""` for a bodiless request: with `data=b""` urllib writes
    # `Content-Length: 0` and a form content type on a GET, which some proxies refuse.
    request = urllib.request.Request(url, data=body or None, headers=sent, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return int(response.status), response.read(cap + 1)
    except urllib.error.HTTPError as error:
        return int(error.code), error.read(cap + 1)


def urllib_call(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
    """`urllib`, no dependency.

    An HTTP error is a *status*, not an exception: `urllib` raises `HTTPError` for a
    4xx, and unwrapping it here is what lets `send` treat "OpenAI refused the event"
    and "OpenAI accepted it" through one path. The body is read up to one byte past the
    cap, so the caller can tell "too long" from "exactly the cap".
    """
    return _open(method, url, headers, body, MAX_BODY_BYTES, HTTP_TIMEOUT_SECONDS)


def urllib_download_call(
    method: str, url: str, headers: dict[str, str], body: bytes
) -> tuple[int, bytes]:
    """`urllib_call` with room for a file: the Documenso client's seam (REB-387), whose
    downloads are sealed PDFs rather than a few fields of JSON."""
    return _open(method, url, headers, body, MAX_DOWNLOAD_BYTES, HTTP_TIMEOUT_SECONDS)


def urllib_engagements_call(
    method: str, url: str, headers: dict[str, str], body: bytes
) -> tuple[int, bytes]:
    """`urllib_call` with time for a space to be provisioned: the engagements client's
    seam (REB-498), whose first `PUT` for a freelancer creates their space's database
    before it answers. The same cap: the answer is a few fields of JSON, or a report."""
    return _open(method, url, headers, body, MAX_BODY_BYTES, ENGAGEMENTS_TIMEOUT_SECONDS)
