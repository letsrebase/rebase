"""The CLI's sign-in from the browser: a device authorization, RFC 8628 kept as small as the one
use needs (REB-1133, spec 2026-11-17 § 2).

`rebase-verify submit` asks for a code (`start`), the signed-in person approves it on `/hub/cli`
(`approve`, `deny`), and the CLI polls (`poll`) until it holds an access token. The token can send
one work profile (`WorkProfileService.submit` spends it), is never written to disk by the CLI, and
lives 15 minutes, so there is nothing to revoke. Only the sha256 of the device code and of the
token is stored, and the token is handed out once: by the first poll after the approval, which is
the only moment the raw value exists.
"""

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.config import Settings
from rebase_core.errors import CliCodeNotFound, CliNotAllowed, InvalidState
from rebase_core.members import MemberService
from rebase_core.models import CLI_SCOPES, CliAuthorization
from rebase_core.schema_common import ResponseModel
from rebase_core.vocabulary import CliAuthorizationState, CliScope

# Eight characters from an alphabet without the ones that read as another (`0 O 1 I L`).
USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_SECONDS = 600
TOKEN_SECONDS = 900
INTERVAL_SECONDS = 5

NO_CARD = (
    "Per inviare il tuo profilo di lavoro serve una scheda rebase. Candidati prima dal sito, "
    "poi torna qui."
)


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalise_code(typed: str) -> str | None:
    """`abcd efgh`, `ABCD-EFGH` and `abcdefgh` are one code; anything else is none."""
    letters = "".join(ch for ch in typed.upper() if ch.isalnum())
    if len(letters) != 8 or any(ch not in USER_CODE_ALPHABET for ch in letters):
        return None
    return f"{letters[:4]}-{letters[4:]}"


class DeviceStart(ResponseModel):
    """The answer of `POST /api/hub/cli/device`, RFC 8628's names."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


TokenError = Literal["authorization_pending", "slow_down", "access_denied", "expired_token"]


@dataclass(frozen=True)
class TokenPoll:
    """Either `error` (an RFC 8628 name) or the token; never both."""

    error: TokenError | None = None
    access_token: str | None = None
    expires_in: int | None = None


class TokenGranted(ResponseModel):
    """The 200 of `POST /api/hub/cli/token`: the access token, shown once."""

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int


class TokenRefused(ResponseModel):
    """The 400 of `POST /api/hub/cli/token`: RFC 8628's `error`, whatever the status."""

    error: TokenError


class CliAuthorizationRead(ResponseModel):
    """What `/hub/cli` shows of a code: the request, never a device name or an address (the CLI
    sends none), and whether this person may say yes."""

    user_code: str
    scope: CliScope
    state: CliAuthorizationState
    can_authorize: bool
    blocked_reason: str | None
    expires_at: datetime


class CliAuthService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session = session
        self.settings = settings
        self._now = now

    # ---- the CLI's side ------------------------------------------------------------------

    def start(self, scope: str) -> DeviceStart:
        if scope not in CLI_SCOPES:
            raise InvalidState("Questo accesso non è disponibile.")
        now = self._now()
        device_code = secrets.token_urlsafe(32)
        user_code = self._free_code()
        self.session.add(
            CliAuthorization(
                device_code_hash=sha256_hex(device_code),
                user_code=user_code,
                scope=scope,
                state="pending",
                poll_interval=INTERVAL_SECONDS,
                created_at=now,
                expires_at=now + timedelta(seconds=CODE_SECONDS),
            )
        )
        self.session.commit()
        uri = f"{self.settings.hub_url.rstrip('/')}/cli"
        return DeviceStart(
            device_code=device_code,
            user_code=user_code,
            verification_uri=uri,
            verification_uri_complete=f"{uri}?code={user_code}",
            expires_in=CODE_SECONDS,
            interval=INTERVAL_SECONDS,
        )

    def _free_code(self) -> str:
        while True:
            letters = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
            code = f"{letters[:4]}-{letters[4:]}"
            taken = self.session.scalar(
                select(CliAuthorization.id).where(
                    CliAuthorization.user_code == code, CliAuthorization.state == "pending"
                )
            )
            if taken is None:
                return code

    def poll(self, device_code: str) -> TokenPoll:
        row = self.session.scalar(
            select(CliAuthorization)
            .where(CliAuthorization.device_code_hash == sha256_hex(device_code))
            .with_for_update()
        )
        if row is None:
            return TokenPoll(error="expired_token")
        now = self._now()
        if self._lapse(row, now):
            self.session.commit()
            return TokenPoll(error="expired_token")
        slow = (
            row.state in ("pending", "approved")
            and row.last_polled_at is not None
            and (now - row.last_polled_at).total_seconds() < row.poll_interval
        )
        if slow:
            row.poll_interval += INTERVAL_SECONDS
        row.last_polled_at = now
        if slow:
            self.session.commit()
            return TokenPoll(error="slow_down")
        if row.state == "pending":
            self.session.commit()
            return TokenPoll(error="authorization_pending")
        if row.state == "denied":
            self.session.commit()
            return TokenPoll(error="access_denied")
        if row.state == "approved" and row.token_hash is None:
            raw = secrets.token_urlsafe(32)
            row.token_hash = sha256_hex(raw)
            row.expires_at = now + timedelta(seconds=TOKEN_SECONDS)
            self.session.commit()
            return TokenPoll(access_token=raw, expires_in=TOKEN_SECONDS)
        # Approved and already handed out (the raw value is gone), spent, or expired.
        self.session.commit()
        return TokenPoll(error="expired_token")

    @staticmethod
    def _lapse(row: CliAuthorization, now: datetime) -> bool:
        """Marks a row whose deadline passed before its token was handed out `expired`."""
        live = row.state in ("pending", "approved") and row.token_hash is None
        if live and row.expires_at <= now:
            row.state = "expired"
            return True
        return row.state == "expired"

    # ---- the signed-in person's side -----------------------------------------------------

    def _find(self, typed: str, *, lock: bool = False) -> CliAuthorization:
        code = normalise_code(typed)
        if code is None:
            raise CliCodeNotFound
        query = (
            select(CliAuthorization)
            .where(
                CliAuthorization.user_code == code,
                CliAuthorization.state.in_(("pending", "approved", "denied")),
            )
            .order_by(CliAuthorization.created_at.desc())
            .limit(1)
        )
        row = self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise CliCodeNotFound
        if self._lapse(row, self._now()):
            self.session.commit()
            raise CliCodeNotFound
        return row

    def _blocked(self, row: CliAuthorization, user_id: UUID) -> str | None:
        if (
            row.scope == "work_profile"
            and MemberService(self.session).card_for_user(user_id) is None
        ):
            return NO_CARD
        return None

    def read(self, typed: str, user_id: UUID) -> CliAuthorizationRead:
        row = self._find(typed)
        blocked = self._blocked(row, user_id) if row.state == "pending" else None
        return CliAuthorizationRead(
            user_code=row.user_code,
            scope=row.scope,
            state=row.state,
            can_authorize=row.state == "pending" and blocked is None,
            blocked_reason=blocked,
            expires_at=row.expires_at,
        )

    def approve(self, typed: str, user_id: UUID) -> CliAuthorizationRead:
        row = self._find(typed, lock=True)
        if row.state != "pending":
            raise InvalidState("Questo codice è già stato usato.")
        blocked = self._blocked(row, user_id)
        if blocked is not None:
            raise CliNotAllowed(blocked)
        row.state = "approved"
        row.user_id = user_id
        row.decided_at = self._now()
        self.session.commit()
        return self.read(typed, user_id)

    def deny(self, typed: str, user_id: UUID) -> CliAuthorizationRead:
        row = self._find(typed, lock=True)
        if row.state != "pending":
            raise InvalidState("Questo codice è già stato usato.")
        row.state = "denied"
        row.user_id = user_id
        row.decided_at = self._now()
        self.session.commit()
        return self.read(typed, user_id)
