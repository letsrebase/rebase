"""Signing in with GitHub or Google (REB-1137, spec 2026-11-18).

The provider is asked one question: which addresses does this person hold that you have
verified? Everything after that is the hub's own rule (`UserService.enter_with_provider`):
an address that is a login address opens that person, and nothing is ever created here.

Two providers, one shape. The authorization code flow with PKCE (S256), `redirect_uri` sent
explicitly (one app serves the production and the preview host) and built from `hub_url`,
never from the request's `Host`. The hub is a confidential client, so the code is exchanged
with the secret as well. The access token is used once or twice, in this call, and dropped:
it is never stored and never logged.

- **GitHub** (scope `user:email`): the token endpoint, then `GET /user/emails`, keeping the
  `verified: true` entries. A GitHub account may hold several, and any of them may match.
- **Google** (scopes `openid email profile`): the token endpoint, then the userinfo endpoint,
  requiring `email_verified`. The ID token is not read. It would be safe to read without a
  signature check, since it comes straight from Google over TLS (OpenID Connect Core 3.1.3.7),
  but then `iss`, `aud` and `exp` are ours to check by hand; userinfo, called with the access
  token this client has just obtained, answers the one thing wanted (`email`,
  `email_verified`) from the same host and needs no parsing of a JWT.

All HTTP goes through the hub's `HttpCall` seam, as every other outbound call does: a test
hands a fake and reads what would have left.
"""

import base64
import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from rebase_core.config import Settings
from rebase_core.http import HttpCall
from rebase_core.models import ORIGIN_MAX_LENGTH, UTM_MAX_LENGTH
from rebase_core.schema_common import ResponseModel
from rebase_core.signup_schemas import SignupUtm

logger = logging.getLogger(__name__)

PROVIDERS = ("github", "google")
CALLBACK_PATH = "/api/hub/auth/oauth/{provider}/callback"
# How long the cookie that carries `state` and the PKCE verifier lives: long enough for a
# person to pick an account and type a password, short enough that a forgotten tab is dead.
ATTEMPT_MINUTES = 10

GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_EMAILS = "https://api.github.com/user/emails"
GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
# GitHub hands every account a verified address that nobody can write to and no hub user
# holds. It never decides anything, and it is not an address to show a person either.
_GITHUB_NOREPLY = "@users.noreply.github.com"

# The return path after a sign-in: a page of the hub's own SPA, and nothing else. A path of
# letters, digits, `-`, `_` and `/`, never `//`, never `..`; `/cli` alone may carry a query,
# the device code. It is appended to `hub_url`, so no value of it can leave the hub's origin,
# and this shape is the second guard.
_NEXT_PATH_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/")
# The server's alphabet (`cli_auth.USER_CODE_ALPHABET`): no `0 O 1 I L`.
_CODE_ALPHABET = frozenset("ABCDEFGHJKMNPQRSTUVWXYZ23456789")


class OAuthUnavailable(Exception):
    """The provider did not give an answer this flow can use: a network failure, a refusal
    of the code or an answer of the wrong shape. The message names the step, never a value."""


@dataclass(frozen=True)
class Attempt:
    """What the browser holds between the redirect to the provider and its callback."""

    provider: str
    state: str
    verifier: str
    next_path: str | None
    utm: SignupUtm


@dataclass(frozen=True)
class Begun:
    """The provider's URL to send the browser to, and the attempt to seal into its cookie."""

    url: str
    attempt: Attempt


class AuthProviders(ResponseModel):
    """Which providers the login page offers a button for: the ones whose OAuth app is
    configured on this deployment."""

    github: bool
    google: bool


class MissedAccount(ResponseModel):
    """What the no-account page shows: which provider spoke and the address it vouched for,
    read from a ten-minute cookie of the person's own browser. It is stored nowhere else."""

    provider: str
    email: str


def safe_next(raw: str | None) -> str | None:
    """The return path if it is one the hub may send a person to, else `None`."""
    if not raw or len(raw) > 64:
        return None
    path, _, query = raw.partition("?")
    if not path.startswith("/") or "//" in path or ".." in path:
        return None
    if not set(path) <= _NEXT_PATH_CHARS:
        return None
    if not query:
        return path
    if path != "/cli":
        return None
    key, _, code = query.partition("=")
    if key != "code" or len(code) != 9 or code[4] != "-":
        return None
    if not (set(code[:4]) <= _CODE_ALPHABET and set(code[5:]) <= _CODE_ALPHABET):
        return None
    return raw


def utm_of(params: dict[str, str]) -> SignupUtm:
    """The attribution a start URL carried, cut to the stored bounds. A navigation is never
    answered with a 422: what does not fit is dropped or shortened."""
    fields: dict[str, str] = {}
    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "utm_id"):
        value = params.get(key, "").strip()[:UTM_MAX_LENGTH]
        if value:
            fields[key] = value
    origin = params.get("origin", "").strip()
    if len(origin) <= ORIGIN_MAX_LENGTH and re.fullmatch(r"[a-z0-9-]+", origin):
        fields["origin"] = origin
    return SignupUtm(**fields)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def seal_attempt(attempt: Attempt) -> str:
    """The cookie's value. Not signed: nobody but the browser holds it, and what it protects
    (`state`, which must equal the one the callback carries) is checked against it, so a
    forged value only ever fails its own owner's sign-in."""
    body = {
        "p": attempt.provider,
        "s": attempt.state,
        "v": attempt.verifier,
        "n": attempt.next_path,
        "u": attempt.utm.model_dump(exclude_none=True),
    }
    return _b64(json.dumps(body, separators=(",", ":")).encode())


def open_attempt(value: str | None) -> Attempt | None:
    """The attempt a cookie holds, or `None` for a missing, malformed or altered one."""
    if not value:
        return None
    try:
        body = json.loads(_unb64(value))
        provider, state, verifier = body["p"], body["s"], body["v"]
        if provider not in PROVIDERS or not isinstance(state, str) or not isinstance(verifier, str):
            return None
        next_path = safe_next(body.get("n"))
        utm = SignupUtm(**{k: v for k, v in body.get("u", {}).items() if isinstance(v, str)})
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return Attempt(provider, state, verifier, next_path, utm)


def seal_miss(miss: MissedAccount) -> str:
    return _b64(json.dumps({"p": miss.provider, "e": miss.email}, separators=(",", ":")).encode())


def open_miss(value: str | None) -> MissedAccount | None:
    if not value:
        return None
    try:
        body = json.loads(_unb64(value))
        provider, email = body["p"], body["e"]
    except (ValueError, KeyError, TypeError):
        return None
    if provider not in PROVIDERS or not isinstance(email, str) or len(email) > 320:
        return None
    return MissedAccount(provider=provider, email=email)


def same_state(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode(), right.encode())


class OAuthService:
    def __init__(self, settings: Settings, http: HttpCall) -> None:
        self.settings = settings
        self.http = http

    # ---- which providers are on ------------------------------------------------------

    def _pair(self, provider: str) -> tuple[str, str]:
        if provider == "github":
            return self.settings.github_oauth_client_id, self.settings.github_oauth_client_secret
        if provider == "google":
            return self.settings.google_oauth_client_id, self.settings.google_oauth_client_secret
        return "", ""

    def is_on(self, provider: str) -> bool:
        """On only when both the id and the secret are set: half a pair is a typo, not a
        provider."""
        client_id, secret = self._pair(provider)
        return bool(client_id and secret)

    def providers_on(self) -> AuthProviders:
        return AuthProviders(github=self.is_on("github"), google=self.is_on("google"))

    def redirect_uri(self, provider: str) -> str:
        """Where the provider sends the browser back: the origin of `hub_url`, which is a
        setting of this deployment (`https://letsrebase.com` or the preview's), and never
        what the request claims to be."""
        parts = urlsplit(self.settings.hub_url)
        return f"{parts.scheme}://{parts.netloc}{CALLBACK_PATH.format(provider=provider)}"

    def return_url(self, path: str) -> str:
        """A page of the hub's SPA, `path` appended to `hub_url`."""
        return f"{self.settings.hub_url.rstrip('/')}{path}"

    # ---- the way out ------------------------------------------------------------------

    def begin(self, provider: str, next_path: str | None, utm: SignupUtm) -> Begun:
        client_id, _ = self._pair(provider)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        query = {
            "client_id": client_id,
            "redirect_uri": self.redirect_uri(provider),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if provider == "github":
            # `allow_signup=false`: a person with no GitHub account is not offered one here.
            github = {**query, "scope": "user:email", "allow_signup": "false"}
            url = GITHUB_AUTHORIZE + "?" + urlencode(github)
        else:
            url = (
                GOOGLE_AUTHORIZE
                + "?"
                + urlencode(
                    {
                        **query,
                        "response_type": "code",
                        "scope": "openid email profile",
                        "prompt": "select_account",
                    }
                )
            )
        return Begun(url, Attempt(provider, state, verifier, next_path, utm))

    # ---- the way back -----------------------------------------------------------------

    def verified_addresses(self, attempt: Attempt, code: str) -> list[str]:
        """The addresses the provider has verified for the person who just signed in there,
        lower-cased, the primary one first. Empty when it verified none."""
        token = self._exchange(attempt, code)
        if attempt.provider == "github":
            return self._github_addresses(token)
        return self._google_addresses(token)

    def _call(self, step: str, method: str, url: str, headers: dict[str, str], body: bytes) -> Any:
        try:
            status, raw = self.http(method, url, headers, body)
        except Exception as exc:  # noqa: BLE001 -- urllib raises URLError, timeouts and resets
            logger.warning("oauth %s did not answer: %s", step, type(exc).__name__)
            raise OAuthUnavailable(step) from exc
        if status != 200:
            logger.warning("oauth %s answered %s", step, status)
            raise OAuthUnavailable(step)
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise OAuthUnavailable(step) from exc

    def _exchange(self, attempt: Attempt, code: str) -> str:
        client_id, secret = self._pair(attempt.provider)
        form = urlencode(
            {
                "client_id": client_id,
                "client_secret": secret,
                "code": code,
                "redirect_uri": self.redirect_uri(attempt.provider),
                "code_verifier": attempt.verifier,
                **({"grant_type": "authorization_code"} if attempt.provider == "google" else {}),
            }
        ).encode()
        url = GITHUB_TOKEN if attempt.provider == "github" else GOOGLE_TOKEN
        answer = self._call(
            "token",
            "POST",
            url,
            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            form,
        )
        # GitHub answers a refused code with a 200 and an `error` key.
        token = answer.get("access_token") if isinstance(answer, dict) else None
        if not isinstance(token, str) or not token:
            logger.warning("oauth token answer carried no access token")
            raise OAuthUnavailable("token")
        return token

    def _github_addresses(self, token: str) -> list[str]:
        rows = self._call(
            "emails",
            "GET",
            GITHUB_EMAILS,
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            b"",
        )
        if not isinstance(rows, list):
            raise OAuthUnavailable("emails")
        found: list[tuple[bool, str]] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("verified") is not True:
                continue
            email = row.get("email")
            if not isinstance(email, str) or not email.strip():
                continue
            lowered = email.strip().lower()
            if lowered.endswith(_GITHUB_NOREPLY):
                continue
            found.append((row.get("primary") is True, lowered))
        found.sort(key=lambda item: not item[0])
        return list(dict.fromkeys(email for _, email in found))

    def _google_addresses(self, token: str) -> list[str]:
        info = self._call(
            "userinfo", "GET", GOOGLE_USERINFO, {"Authorization": f"Bearer {token}"}, b""
        )
        if not isinstance(info, dict):
            raise OAuthUnavailable("userinfo")
        email = info.get("email")
        # Google has answered `email_verified` as a JSON boolean and, on older paths, as the
        # string "true": both mean verified, anything else does not.
        verified = info.get("email_verified") is True or info.get("email_verified") == "true"
        if not verified or not isinstance(email, str) or not email.strip():
            return []
        return [email.strip().lower()]
