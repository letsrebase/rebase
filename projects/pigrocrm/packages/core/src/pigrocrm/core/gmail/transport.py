"""The only module in this slice that knows a socket exists.

Shaped after `storage/gdrive.py`: `urllib.request`, no Google client library, and an
injectable seam that the fake transport replaces (`core/http.py`, shared with Drive and
Slack). What is faked is the *network*, so URL construction, `q` construction, RFC822
assembly and error classification all run for real in the tests -- which matters because
those are precisely the parts the previous system got wrong, in code no test ever executed.

Nothing in this module logs, and nothing that leaves it carries a request. The one
value that travels out of a failure is an `UpstreamFailure`: a status, a code from
Google's published vocabulary, and a number. The bearer token, the refresh token and
the client secret all pass through here, and none of them may reach an exception, a
`repr`, or a traceback.
"""

import json
import time
from typing import Any
from urllib.parse import urlencode

from pigrocrm.core.gmail.errors import GoogleCallFailed, UpstreamFailure
from pigrocrm.core.http import (
    NEVER_RETRY,
    RETRYABLE_STATUSES,
    HttpCall,
    SleepFn,
    call_with_retry,
    parse_retry_after,
    urllib_call,
)


def _error_code(payload: bytes) -> str:
    """Google speaks two dialects and this slice touches both. The OAuth token
    endpoint answers `{"error": "invalid_grant"}` -- a bare string. The Gmail API
    answers `{"error": {"status": "UNAUTHENTICATED", ...}}` -- an object. Reading only
    one of them is how `invalid_grant` gets lost, which is the previous system's defect.

    The `errors[0].reason` fallback is the older v1 shape, which the Gmail API still
    returns for some quota failures and which carries no `status` at all.
    """
    try:
        parsed = json.loads(payload.decode())
    except (ValueError, UnicodeDecodeError):
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        status = error.get("status")
        if isinstance(status, str):
            return status
        errors = error.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            reason = errors[0].get("reason")
            if isinstance(reason, str):
                return reason
    return ""


def _decode_success(payload: bytes) -> dict[str, Any]:
    """An empty body is an empty object, not a parse error: `messages.trash` answers
    204 with nothing at all, and `json.loads(b"")` raises."""
    if not payload:
        return {}
    parsed = json.loads(payload.decode())
    if not isinstance(parsed, dict):
        # Google returns an object from every endpoint this slice calls. A bare list
        # or scalar means we are talking to something else -- a captive portal, a
        # proxy's error page that happens to be valid JSON -- and calling that a
        # success would push the surprise into whichever caller indexes the result.
        raise GoogleCallFailed(UpstreamFailure(200, "unexpected_payload", None), "risposta Google")
    return parsed


class GmailTransport:
    def __init__(self, *, http: HttpCall | None = None, sleep: SleepFn | None = None) -> None:
        self._http: HttpCall = http or urllib_call
        # Injectable so the retry tests do not actually block for seconds.
        self._sleep: SleepFn = sleep or time.sleep

    def _call(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        what: str,
        *,
        retry: bool = True,
    ) -> dict[str, Any]:
        """Every call goes through `call_with_retry`, so that backoff applies uniformly
        rather than being reimplemented -- or forgotten -- at each call site.

        **`retry=False` exists for one caller and it is not a tuning knob.** A retry is
        safe only when a failed attempt is known to have had no effect, and that is true
        of every endpoint here except `users.messages.send`: a 599 or a 502 on a send
        means *no answer arrived*, not *nothing was sent*, and Gmail offers no idempotency
        key to make the second attempt the same operation as the first. Retrying it is
        therefore a mechanism for delivering a second copy of somebody's email to their
        client -- three of them, at this loop's four attempts -- and
        `test_gmail_reconcile.py` reproduced exactly that before this parameter existed.
        A send whose outcome is unknown is `incerto` and is resolved by asking, which is
        what `EmailSendService.reconcile` is for.
        """
        status, payload, response_headers = call_with_retry(
            self._http,
            self._sleep,
            method,
            url,
            headers,
            body,
            retryable=RETRYABLE_STATUSES if retry else NEVER_RETRY,
        )
        if status < 400:
            return _decode_success(payload)
        raise GoogleCallFailed(
            UpstreamFailure(
                status=status,
                error_code=_error_code(payload),
                retry_after=parse_retry_after(response_headers),
            ),
            what,
        )

    def json(
        self,
        method: str,
        url: str,
        *,
        token: str,
        body: dict[str, Any] | None = None,
        what: str,
        retry: bool = True,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"}
        encoded: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            encoded = json.dumps(body).encode()
        return self._call(method, url, headers, encoded, what, retry=retry)

    def form(self, url: str, fields: dict[str, str], *, what: str) -> dict[str, Any]:
        """POST `application/x-www-form-urlencoded`, for the OAuth token endpoint and
        nothing else. No `Authorization` header: the credentials are in the body, and
        this is the one call that carries the client secret. Nothing here logs."""
        return self._call(
            "POST",
            url,
            {"Content-Type": "application/x-www-form-urlencoded"},
            urlencode(fields).encode(),
            what,
        )
