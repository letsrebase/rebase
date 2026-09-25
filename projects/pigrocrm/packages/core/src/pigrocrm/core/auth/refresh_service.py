from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.auth.refresh_models import RefreshToken
from pigrocrm.core.auth.tokens import issue_refresh_token
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ValidationFailed

# Every failure this service reports -- an unknown jti, an expired row, one already
# consumed -- reads identically. Same anti-enumeration discipline as
# UserService.authenticate's dummy hash and PatService.resolve's INVALID_TOKEN:
# whoever is holding a stolen or replayed refresh token must not be able to tell "this
# exact token is dead" apart from "this user's whole session family was just revoked."
INVALID_REFRESH_TOKEN = "sessione non valida o scaduta"

# How long after a rotation the token just rotated away is still answered with the pair
# that rotation produced, instead of being treated as a replay.
#
# The case this exists for is not exotic: the refresh cookie is one cookie per browser,
# not per tab, so two tabs left open past the fifteen-minute access cookie
# (`access_token_minutes`) both meet a 401 on their first request and both POST
# /api/auth/refresh with the *same* token. The web client already shares one refresh
# across a tab's own concurrent requests and now takes a `navigator.locks` lock across
# tabs (`apps/web/src/lib/api.ts`), but a lock in the browser cannot cover a second
# browser window restored from a session, a client with locks unavailable, or a request
# already in flight when the lock was taken -- and the cost of getting it wrong is the
# owner logged out of everything at once, which is precisely what was reported.
#
# Ten seconds is the width of that race and not much more: it is longer than any
# plausible round trip plus retry, and short enough that a token resurfacing later --
# the actual signal of theft -- still burns the family. It is deliberately not a
# setting: a knob here is a knob that turns rotation off.
REFRESH_GRACE_SECONDS = 10


@dataclass(frozen=True)
class Rotation:
    """What a successful rotation hands its caller.

    `issued_at` travels with the refresh token because the *access* token has to be
    signed at the same instant to come out identical on a second presentation (see
    `issue_access_token`'s `issued_at`): the router signs it from this value, so the
    grace path and the ordinary path are the same code and produce the same two
    cookies. Nothing in the response says which of the two happened -- a client that
    could tell them apart could probe how long ago somebody else's tab refreshed."""

    refresh_token: str
    issued_at: datetime


class RefreshTokenService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def issue(self, user_id: UUID, settings: Settings) -> str:
        """Creates the row this token's `jti` points at, then signs the token. Both share
        one `jti` and one instant -- the row's `expires_at` and the token's `exp` claim
        are the same arithmetic on the same `now`, which is what lets `rotate` below
        recover a successor's issuance instant from its row and re-sign it byte for
        byte."""
        token, _ = self._mint(user_id, settings, datetime.now(UTC))
        self.session.commit()
        return token

    def _mint(self, user_id: UUID, settings: Settings, now: datetime) -> tuple[str, RefreshToken]:
        """One new token and its row, uncommitted, from a single instant.

        The instant is the caller's, not this method's: `rotate` needs the *same* `now`
        it used to mark the predecessor consumed, so that the two writes describe one
        event, and needs to keep it to sign the access token with.
        """
        jti = uuid4()
        expires_at = now + timedelta(days=settings.refresh_token_days)
        record = RefreshToken(jti=jti, user_id=user_id, expires_at=expires_at)
        self.session.add(record)
        return issue_refresh_token(user_id, settings, jti=jti, issued_at=now), record

    def rotate(self, jti: UUID, user_id: UUID, settings: Settings) -> Rotation:
        """Consumes the token presented and returns its successor -- the whole of what
        `POST /api/auth/refresh` does to the session.

        This is `consume` plus one forgiveness. A refresh token presented twice is
        normally a stolen one (see `consume`'s docstring, which is still the rule), but
        there is one shape of double presentation that is not theft at all and used to
        be punished as if it were: the refresh cookie belongs to the *browser*, not to a
        tab, so two tabs past the fifteen-minute access cookie both send the same token
        within milliseconds of each other and the second one revoked the family. The
        owner's report was "it logs me out when I have two tabs open".

        So: inside `REFRESH_GRACE_SECONDS` of the consumption that rotated it away, and
        only while the successor that consumption produced is itself still live, the
        second presentation is answered with that same successor -- the same refresh
        token, the same issuance instant, therefore the same access token, therefore two
        tabs that agree on which cookie they hold. Outside the window, or once the
        successor has been consumed or revoked or has expired, nothing has changed: it
        is a replay, and the family dies.

        What the window does *not* do is let a token be spent twice: the grace path
        mints nothing, marks nothing consumed and creates no row. It is a read of a
        decision already taken.
        """
        now = datetime.now(UTC)
        record = self._locked(jti, user_id, now)
        if record.consumed_at is not None:
            already = self._successor_within_grace(record, user_id, now, settings)
            if already is not None:
                # Nothing was written, but the row lock this transaction took is still
                # held; releasing it here is what keeps a burst of tabs from queueing
                # behind each other on the database.
                self.session.commit()
                return already
            self._revoke_all_valid(user_id, now)
            raise ValidationFailed("refresh_token", "jti", INVALID_REFRESH_TOKEN)
        token, successor = self._mint(user_id, settings, now)
        record.consumed_at = now
        record.successor_jti = successor.jti
        self.session.commit()
        return Rotation(refresh_token=token, issued_at=now)

    def _successor_within_grace(
        self, record: RefreshToken, user_id: UUID, now: datetime, settings: Settings
    ) -> Rotation | None:
        """The pair `record`'s own consumption produced, if that was moments ago and the
        pair is still good -- otherwise None, which means "this is a replay".

        The successor's issuance instant is recovered from its row rather than stored
        twice: `_mint` computes `expires_at` as exactly `issued_at + refresh_token_days`
        from one `now`, and `timestamptz` round-trips a Python `datetime` to the
        microsecond, so subtracting the same offset gives the same instant back and
        re-signing gives the same token back. That identity is what
        `test_a_replay_within_the_grace_window_returns_the_very_same_successor` pins,
        so a future change to how `expires_at` is derived cannot quietly break it.
        """
        if record.successor_jti is None or record.consumed_at is None:
            return None
        if now - record.consumed_at > timedelta(seconds=REFRESH_GRACE_SECONDS):
            return None
        successor = self.session.execute(
            select(RefreshToken).where(
                RefreshToken.jti == record.successor_jti, RefreshToken.user_id == user_id
            )
        ).scalar_one_or_none()
        if successor is None or successor.consumed_at is not None or successor.expires_at < now:
            return None
        issued_at = successor.expires_at - timedelta(days=settings.refresh_token_days)
        return Rotation(
            refresh_token=issue_refresh_token(
                user_id, settings, jti=successor.jti, issued_at=issued_at
            ),
            issued_at=issued_at,
        )

    def consume(self, jti: UUID, user_id: UUID) -> None:
        """Marks a refresh token used up so it can never be presented again. Reusing an
        already-consumed token is the standard signal that it was stolen: the
        legitimate user rotated past it, so whoever is presenting it now is not them.
        Rewarding that replay with a fresh pair of tokens would leave the thief inside,
        so the response is to revoke every other still-valid token this user holds,
        not just the one being replayed.

        `with_for_update()` locks the row for the rest of this transaction: without
        it, two concurrent calls on the same jti (a genuine replay -- an attacker and
        the legitimate user racing, or even just a retried request) can both read
        `consumed_at IS NULL` before either writes, so both "succeed" and the
        revocation chain above never fires. The lock forces the second caller to wait
        for the first's commit and then see the state that commit actually produced,
        which is what makes the two branches below mutually exclusive for the same
        row. It stays held until this method's own commit or the caller's -- there is
        no commit between the SELECT and the write in either branch, on purpose.

        This is the path `logout` uses, and the path `rotate` above is the refresh
        endpoint's: a token consumed here has no recorded successor, so a later
        presentation of it is a replay even within the grace window -- which is right,
        because logging out is a decision to end the session, not a rotation."""
        now = datetime.now(UTC)
        record = self._locked(jti, user_id, now)
        if record.consumed_at is not None:
            self._revoke_all_valid(user_id, now)
            raise ValidationFailed("refresh_token", "jti", INVALID_REFRESH_TOKEN)
        record.consumed_at = now
        self.session.commit()

    def is_live(self, jti: UUID, user_id: UUID) -> bool:
        """Whether this token could still be rotated: its row exists for this user, has
        not been consumed and has not expired. A read and nothing else: no lock, no
        write, no revocation, so asking it never changes the session it asks about.

        Its one caller is the Google consent's way back (`deps.callback_actor`, REB-446),
        a top-level navigation that has to know whose browser came back after the access
        cookie ran out on Google's screens, and that must not rotate the pair the SPA is
        about to renew on its own: a rotation there would need its new cookies on every
        answer the callback can give, and one it could not carry them on would leave the
        browser holding a consumed token, which the next refresh reads as a replay. A
        consumed token answers False here without burning the family, like an unknown
        one: this read is not where a replay is judged, `rotate` is.
        """
        now = datetime.now(UTC)
        stmt = select(RefreshToken.id).where(
            RefreshToken.jti == jti,
            RefreshToken.user_id == user_id,
            RefreshToken.consumed_at.is_(None),
            RefreshToken.expires_at >= now,
        )
        return self.session.execute(stmt).first() is not None

    def _locked(self, jti: UUID, user_id: UUID, now: datetime) -> RefreshToken:
        """The row for this jti, locked for the rest of the transaction, or a rejection.

        See `consume`'s docstring for why the lock is not optional. An unknown jti and
        an expired row raise the same message as everything else here, on purpose.
        """
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.jti == jti, RefreshToken.user_id == user_id)
            .with_for_update()
        )
        record = self.session.execute(stmt).scalar_one_or_none()
        if record is None or record.expires_at < now:
            raise ValidationFailed("refresh_token", "jti", INVALID_REFRESH_TOKEN)
        return record

    def revoke_all(self, user_id: UUID, now: datetime | None = None) -> None:
        """Every still-valid refresh token of `user_id` is consumed. Public for the one
        caller outside this class that has a reason: `MagicLinkService.enter`, on the
        first link entry of a user, when whoever opened a session before the address was
        proven must lose it (spec 2026-09-12 §6.2)."""
        self._revoke_all_valid(user_id, now or datetime.now(UTC))

    def _revoke_all_valid(self, user_id: UUID, now: datetime) -> None:
        # `order_by(id)` is not decorative: without it, this UPDATEs whatever order
        # session.dirty happens to hand SQLAlchemy, and two concurrent replays that
        # both revoke an overlapping set of sibling rows can acquire those rows' locks
        # in different orders -- a real Postgres deadlock, not a hypothetical one.
        # A fixed order means every concurrent caller takes the same locks in the same
        # sequence, which is what rules that out. This runs on the exact path that
        # fires when a stolen token is being replayed -- the worst possible moment for
        # a transaction to fail.
        stmt = (
            select(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.consumed_at.is_(None),
                RefreshToken.expires_at >= now,
            )
            .order_by(RefreshToken.id)
        )
        for record in self.session.execute(stmt).scalars():
            record.consumed_at = now
        self.session.commit()
