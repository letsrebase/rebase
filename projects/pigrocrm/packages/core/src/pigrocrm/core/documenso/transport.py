"""The one network call the Documenso client makes, and why it is not `http.urllib_call`.

The client sends the installation's API token in `Authorization`. `urllib` follows a redirect and
sends the request again, token included, to whatever host the `Location` names; Documenso never
redirects an API call, so a redirect is not Documenso and the token must not travel to it. A 3xx
therefore stays a 3xx here and the client reports it as a refusal. Same shape as the hub's seam
this one replaces, and as `tenants.hub` for the registry's bearer.

Failure to get an answer at all (DNS, a refused connection, a timeout) is the synthetic
`NETWORK_ERROR_STATUS`, the channel every other status travels through (`pigrocrm.core.http`).
"""

import http.client
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import IO

from pigrocrm.core.http import NETWORK_ERROR_STATUS, Response

# Documenso answers an API call in well under a second. The webhook route is held to the
# ten seconds Documenso gives a delivery, and a send runs this call under a row lock.
DOCUMENSO_TIMEOUT_SECONDS = 15
USER_AGENT = "pigrocrm/0.1 (+https://pigro.letsrebase.com)"
# A sealed letter runs to a few hundred kilobytes, and one with Documenso's certificate page
# is still far under this.
SIGNED_COPY_MAX_BYTES = 16 * 1_048_576
# What an API answer other than the sealed file may weigh: a few fields of JSON, or the page of
# envelopes `ping` reads.
JSON_MAX_BYTES = 1_048_576


class _NoRedirect(urllib.request.HTTPRedirectHandler):
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


def documenso_call(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    max_bytes: int | None = None,
) -> Response:
    """`urllib` with no redirect. An HTTP error is a status, not an exception; the body is read up
    to one byte past `max_bytes` (default `JSON_MAX_BYTES`), so a caller can tell «too long» from
    «exactly the cap»."""
    limit = (JSON_MAX_BYTES if max_bytes is None else max_bytes) + 1
    # `None` for a bodiless request: `data=b""` would write `Content-Length: 0` and a form
    # content type on a GET, which some proxies refuse.
    request = urllib.request.Request(
        url, data=body or None, headers={"User-Agent": USER_AGENT, **headers}, method=method
    )
    try:
        with _OPENER.open(request, timeout=DOCUMENSO_TIMEOUT_SECONDS) as response:
            return int(response.status), response.read(limit), dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(limit), dict(exc.headers.items()) if exc.headers else {}
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        # The reason names the failure and possibly the host, never the request's headers.
        return (
            NETWORK_ERROR_STATUS,
            json.dumps({"error": {"message": str(exc)}}).encode(),
            {},
        )
