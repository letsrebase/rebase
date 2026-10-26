"""An in-memory Slack that speaks the HTTP surface `SlackClient` uses.

A fake of the *network*, never of `SlackClient`: the URLs, the form bodies, the Basic header
on `oauth.v2.access`, the Bearer header, the pagination and the error classification are
the parts most likely to be wrong, so they run for real. What it replaces is Slack.

Implements `oauth.v2.access`, `users.lookupByEmail`, `conversations.open`,
`chat.postMessage`, `conversations.list` and `conversations.info`, with Slack's own shapes:
`200` with `{"ok": false, "error": "..."}` for most refusals, `429` with `Retry-After` for a
rate limit, and a bad or revoked bot token answering `invalid_auth`.

Every request is recorded, with its method, its form fields and whether it was authorised
with a Basic or a Bearer header, so a test can assert on *what was asked of Slack*. A secret
(the client secret, the bot token) is never in a recorded form body: the fake refuses an
`oauth.v2.access` that carries `client_secret` as a parameter, which is what Slack asks
callers to avoid.
"""

import base64
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

SLACK_URL = "https://slack.test"
CLIENT_ID = "1111.2222"
CLIENT_SECRET = "slack-client-secret-for-tests"
BOT_TOKEN = "xoxb-test-token-never-real"


@dataclass(frozen=True)
class SlackRequest:
    method: str
    form: dict[str, str]
    basic: tuple[str, str] | None
    bearer: str | None


@dataclass
class FakeSlack:
    team_id: str = "T0TEST"
    team_name: str = "Acme Test"
    bot_user_id: str = "UBOT"
    bot_token: str = BOT_TOKEN
    scope: str = "chat:write,chat:write.public,im:write,users:read,users:read.email,channels:read,groups:read"
    # email (lower case) -> (slack user id, extra profile fields)
    members: dict[str, tuple[str, dict[str, object]]] = field(default_factory=dict)
    # channel id -> channel fields
    channels: dict[str, dict[str, object]] = field(default_factory=dict)
    requests: list[SlackRequest] = field(default_factory=list)
    # What `chat.postMessage` was asked to post: (channel, text), in order.
    posts: list[tuple[str, str]] = field(default_factory=list)
    # Codes the next calls answer instead of working, consumed one per call by method:
    # `fail["chat.postMessage"] = ["channel_not_found"]`.
    fail: dict[str, list[str]] = field(default_factory=dict)
    # HTTP statuses the next calls answer (any method), consumed first: `[429, 500]`.
    fail_status: list[int] = field(default_factory=list)
    # Codes issued by `oauth.v2.access`, consumed on use.
    codes: dict[str, str] = field(default_factory=lambda: {"good-code": "T0TEST"})
    revoked: bool = False

    # -- seeding ----------------------------------------------------------------

    def add_member(
        self, email: str, user_id: str, *, deleted: bool = False, is_bot: bool = False
    ) -> None:
        self.members[email.lower()] = (
            user_id,
            {
                "id": user_id,
                "name": email.split("@")[0],
                "real_name": email.split("@")[0].title(),
                "deleted": deleted,
                "is_bot": is_bot,
                "profile": {"email": email},
            },
        )

    def add_channel(
        self, channel_id: str, name: str, *, is_private: bool = False, is_member: bool = False
    ) -> None:
        self.channels[channel_id] = {
            "id": channel_id,
            "name": name,
            "is_private": is_private,
            "is_member": is_member,
        }

    def calls(self, method: str) -> list[SlackRequest]:
        return [request for request in self.requests if request.method == method]

    # -- the seam `SlackClient` takes -------------------------------------------

    def http(
        self, verb: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes, dict[str, str]]:
        assert verb == "POST", "every Slack Web API call here is a POST"
        path = urlparse(url).path
        assert path.startswith("/api/"), path
        method = path.removeprefix("/api/")
        form = {k: v[0] for k, v in parse_qs((body or b"").decode(), keep_blank_values=True).items()}
        auth = headers.get("Authorization", "")
        basic = None
        bearer = None
        if auth.startswith("Basic "):
            client, _, secret = base64.b64decode(auth.removeprefix("Basic ")).decode().partition(":")
            basic = (client, secret)
        elif auth.startswith("Bearer "):
            bearer = auth.removeprefix("Bearer ")
        self.requests.append(SlackRequest(method, form, basic, bearer))

        if self.fail_status:
            status = self.fail_status.pop(0)
            return status, json.dumps({"ok": False, "error": "ratelimited"}).encode(), (
                {"Retry-After": "1"} if status == 429 else {}
            )
        queued = self.fail.get(method)
        if queued:
            return self._reply({"ok": False, "error": queued.pop(0)})
        if method == "oauth.v2.access":
            return self._oauth(form, basic)
        if bearer != self.bot_token or self.revoked:
            return self._reply({"ok": False, "error": "invalid_auth"})
        handler = {
            "users.lookupByEmail": self._lookup,
            "conversations.open": self._open,
            "chat.postMessage": self._post,
            "conversations.list": self._list,
            "conversations.info": self._info,
        }.get(method)
        if handler is None:
            return self._reply({"ok": False, "error": "unknown_method"})
        return self._reply(handler(form))

    @staticmethod
    def _reply(payload: dict[str, object]) -> tuple[int, bytes, dict[str, str]]:
        return 200, json.dumps(payload).encode(), {}

    def _oauth(
        self, form: dict[str, str], basic: tuple[str, str] | None
    ) -> tuple[int, bytes, dict[str, str]]:
        assert "client_secret" not in form, "the client secret travels in the Basic header"
        if basic != (CLIENT_ID, CLIENT_SECRET):
            return self._reply({"ok": False, "error": "invalid_client_id"})
        code = form.get("code", "")
        if code not in self.codes:
            return self._reply({"ok": False, "error": "invalid_code"})
        del self.codes[code]
        return self._reply(
            {
                "ok": True,
                "access_token": self.bot_token,
                "token_type": "bot",
                "scope": self.scope,
                "bot_user_id": self.bot_user_id,
                "app_id": "A0TEST",
                "team": {"id": self.team_id, "name": self.team_name},
                "enterprise": None,
                "authed_user": {"id": "UADMIN"},
            }
        )

    def _lookup(self, form: dict[str, str]) -> dict[str, object]:
        found = self.members.get(form.get("email", "").lower())
        if found is None:
            return {"ok": False, "error": "users_not_found"}
        return {"ok": True, "user": found[1]}

    def _open(self, form: dict[str, str]) -> dict[str, object]:
        user = form.get("users", "")
        if user not in {member[0] for member in self.members.values()}:
            return {"ok": False, "error": "user_not_found"}
        return {"ok": True, "channel": {"id": f"D{user}"}}

    def _post(self, form: dict[str, str]) -> dict[str, object]:
        channel = form.get("channel", "")
        known = {f"D{member[0]}" for member in self.members.values()} | set(self.channels)
        if channel not in known:
            return {"ok": False, "error": "channel_not_found"}
        self.posts.append((channel, form.get("text", "")))
        return {"ok": True, "channel": channel, "ts": f"{len(self.posts)}.000100"}

    def _list(self, form: dict[str, str]) -> dict[str, object]:
        ordered = sorted(self.channels.values(), key=lambda c: str(c["id"]))
        start = int(form.get("cursor") or 0)
        size = int(form.get("limit") or 100)
        page = ordered[start : start + size]
        more = start + size < len(ordered)
        return {
            "ok": True,
            "channels": page,
            "response_metadata": {"next_cursor": str(start + size) if more else ""},
        }

    def _info(self, form: dict[str, str]) -> dict[str, object]:
        channel = self.channels.get(form.get("channel", ""))
        if channel is None:
            return {"ok": False, "error": "channel_not_found"}
        return {"ok": True, "channel": channel}
