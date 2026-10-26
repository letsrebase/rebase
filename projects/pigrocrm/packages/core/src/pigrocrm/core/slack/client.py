"""Slack's Web API over the stdlib, and the one seam the tests replace.

No Slack SDK: `urllib.request` and `json` are enough for the six methods this uses, and a
client library in `packages/core` would drag a transitive tree into the one package the
architecture test keeps small. The seam is the one `gmail/transport.py` made, a call that
takes `(method, url, headers, body)` and answers `(status, body, response headers)`, so a
`Retry-After` on a 429 is readable; what a test replaces is the network
(`tests/fakes/fake_slack.py`), and the URLs, the form bodies, the Basic header, the
pagination and the error classification all run for real.

**Slack answers 200 for most failures.** `{"ok": false, "error": "channel_not_found"}` is
the usual shape, so a status check alone reads a refused post as accepted. `_call` reads
`ok`: a code that says the token is dead (`DEAD_TOKEN_CODES`) is `SlackTokenRevoked`, any
other is `SlackRefused` carrying the code, and a 429, a 5xx or no response at all is
retried and then `slack_unavailable`. `users.lookupByEmail`'s `users_not_found` is an
answer and not a failure, so it is `None`.

Nothing here logs. The bot token, the client secret and the authorization code pass
through, and none of them may reach an exception, a `repr` or a traceback: the dataclass
that holds a token marks it `repr=False`, and an error carries Slack's fixed error word and
the method name, never the request.
"""

import base64
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from pigrocrm.core.config import Settings
from pigrocrm.core.gmail.transport import (
    MAX_HTTP_ATTEMPTS,
    GmailCall,
    SleepFn,
    _parse_retry_after,
    _retry_delay_seconds,
    _urllib_call,
)
from pigrocrm.core.slack.errors import (
    DEAD_TOKEN_CODES,
    SlackRefused,
    SlackTokenRevoked,
    slack_unavailable,
)

# The seam is Gmail's own call type: the same three-tuple, for the same reason.
SlackCall = GmailCall
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504, 599})
_PAGE_SIZE = 200
# Two thousand channels is more than a space that wants a picker has, and a workspace of
# a hundred thousand must not turn the picker into a hundred requests.
_MAX_PAGES = 10


@dataclass(frozen=True, kw_only=True)
class SlackInstall:
    """What `oauth.v2.access` hands back for a bot install."""

    access_token: str = field(repr=False)
    bot_user_id: str
    team_id: str
    team_name: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class SlackMember:
    id: str
    email: str
    name: str


@dataclass(frozen=True)
class SlackChannel:
    id: str
    name: str
    is_private: bool
    is_member: bool


class SlackClient:
    def __init__(
        self, base_url: str, *, http: SlackCall | None = None, sleep: SleepFn | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http: SlackCall = http or _urllib_call
        self._sleep: SleepFn = sleep or time.sleep

    # -- the install ------------------------------------------------------------

    def oauth_access(
        self, *, client_id: str, client_secret: str, code: str, redirect_uri: str
    ) -> SlackInstall:
        """`oauth.v2.access`: the authorization code for the workspace's bot token. The
        client credentials go in an HTTP Basic header, which Slack asks for over
        parameters, so they are in no query string and no body."""
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        reply = self._call(
            "oauth.v2.access",
            {"code": code, "redirect_uri": redirect_uri},
            authorization=f"Basic {basic}",
            token_errors=False,
        )
        team = reply.get("team") or {}
        token = reply.get("access_token")
        bot = reply.get("bot_user_id")
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(bot, str)
            or not isinstance(team, dict)
            or not isinstance(team.get("id"), str)
        ):
            raise SlackRefused("oauth.v2.access", "invalid_response")
        return SlackInstall(
            access_token=token,
            bot_user_id=bot,
            team_id=team["id"],
            team_name=str(team.get("name") or team["id"]),
            scopes=tuple(s for s in str(reply.get("scope") or "").split(",") if s),
        )

    # -- people -----------------------------------------------------------------

    def lookup_user_by_email(self, token: str, email: str) -> SlackMember | None:
        """`users.lookupByEmail`, or `None` when nobody in the workspace has the address, or
        the member is a deactivated account or a bot: neither can be written to, so for
        a reminder they are not a match."""
        try:
            reply = self._call("users.lookupByEmail", {"email": email}, token=token)
        except SlackRefused as refused:
            if refused.code == "users_not_found":
                return None
            raise
        user = reply.get("user")
        if not isinstance(user, dict) or user.get("deleted") or user.get("is_bot"):
            return None
        profile = user.get("profile") or {}
        user_id = user.get("id")
        if not isinstance(user_id, str):
            return None
        return SlackMember(
            id=user_id,
            email=str(profile.get("email") or email),
            name=str(user.get("real_name") or user.get("name") or ""),
        )

    # -- messages ---------------------------------------------------------------

    def open_dm(self, token: str, user_id: str) -> str:
        """`conversations.open`: the id of the direct message channel with a member."""
        reply = self._call("conversations.open", {"users": user_id}, token=token)
        channel = reply.get("channel")
        channel_id = channel.get("id") if isinstance(channel, dict) else None
        if not isinstance(channel_id, str) or not channel_id:
            raise SlackRefused("conversations.open", "invalid_response")
        return channel_id

    def post_message(self, token: str, channel: str, text: str) -> None:
        """`chat.postMessage` with Slack's mrkdwn and no link previews. `text` is already
        escaped by the caller (`delivery.escape`)."""
        self._call(
            "chat.postMessage",
            {"channel": channel, "text": text, "unfurl_links": "false", "unfurl_media": "false"},
            token=token,
        )

    # -- channels ---------------------------------------------------------------

    def list_channels(self, token: str) -> list[SlackChannel]:
        """`conversations.list`: every channel the bot can see that is not archived, public
        ones and the private ones the bot was invited to, by name."""
        channels: list[SlackChannel] = []
        cursor = ""
        for _ in range(_MAX_PAGES):
            params = {
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
                "limit": str(_PAGE_SIZE),
            }
            if cursor:
                params["cursor"] = cursor
            reply = self._call("conversations.list", params, token=token)
            for raw in reply.get("channels") or []:
                channel = _channel(raw)
                if channel is not None:
                    channels.append(channel)
            meta = reply.get("response_metadata")
            cursor = str(meta.get("next_cursor") or "") if isinstance(meta, dict) else ""
            if not cursor:
                break
        return sorted(channels, key=lambda channel: channel.name)

    def channel(self, token: str, channel_id: str) -> SlackChannel | None:
        """`conversations.info`, or `None` when the channel does not exist or the bot may
        not see it."""
        try:
            reply = self._call("conversations.info", {"channel": channel_id}, token=token)
        except SlackRefused as refused:
            if refused.code == "channel_not_found":
                return None
            raise
        return _channel(reply.get("channel"))

    # -- the call ---------------------------------------------------------------

    def _call(
        self,
        method: str,
        params: dict[str, str],
        *,
        token: str | None = None,
        authorization: str | None = None,
        token_errors: bool = True,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        elif authorization is not None:
            headers["Authorization"] = authorization
        url = f"{self._base}/api/{method}"
        body = urlencode(params).encode()
        status, payload, response_headers = self._http("POST", url, headers, body)
        for attempt in range(MAX_HTTP_ATTEMPTS - 1):
            if status not in _RETRYABLE_STATUSES:
                break
            # A server-supplied Retry-After outranks the guessed schedule, clamped
            # (`gmail/transport.py`).
            self._sleep(_parse_retry_after(response_headers) or _retry_delay_seconds(attempt))
            status, payload, response_headers = self._http("POST", url, headers, body)
        if status in _RETRYABLE_STATUSES:
            raise slack_unavailable(status)
        try:
            reply = json.loads(payload.decode())
        except (ValueError, UnicodeDecodeError):
            reply = None
        if not isinstance(reply, dict):
            raise slack_unavailable(status)
        if reply.get("ok") is True:
            return reply
        code = str(reply.get("error") or "unknown_error")
        if code == "ratelimited":
            raise slack_unavailable(429)
        if token_errors and code in DEAD_TOKEN_CODES:
            raise SlackTokenRevoked(code)
        raise SlackRefused(method, code)


def _channel(raw: object) -> SlackChannel | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        return None
    return SlackChannel(
        id=raw["id"],
        name=str(raw.get("name") or raw["id"]),
        is_private=bool(raw.get("is_private")),
        is_member=bool(raw.get("is_member")),
    )


SlackClientFactory = Callable[[Settings], SlackClient]


def slack_client_for(settings: Settings, *, http: SlackCall | None = None) -> SlackClient:
    """The client a service builds: Slack's address from the settings, the network unless a
    test hands in its own."""
    return SlackClient(settings.slack_url, http=http)
