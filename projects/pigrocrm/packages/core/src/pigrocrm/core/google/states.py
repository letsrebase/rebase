"""The in-flight authorisations of the three connect flows (Gmail, Drive, Slack).

`google_oauth_states` is the one registry of consents that were started and not yet
redeemed; `GoogleOAuthState.purpose` says which flow a row belongs to. It is a repository
of its own, and not a corner of `GmailRepository`, because none of the three flows owns it
and each one used to reach into the Gmail repository for it.

Queries only. Never commits -- the service owns the transaction (and commits right after
`issue`, since the person is about to leave for the provider).
"""

import base64
import os
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from pigrocrm.core.gmail.models import GoogleOAuthState

STATE_TTL_MINUTES = 5
# 32 base64url characters. The `state` is the only thing standing between a forged
# callback and a redeemable authorisation, so it is sized as a secret and not as an
# identifier.
_JTI_BYTES = 24


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class OAuthStateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def issue(self, *, user_id: UUID, purpose: str, code_verifier: str, now: datetime) -> str:
        """Registers a fresh single-use state for `user_id` and returns its jti.

        Expired rows are pruned here, by whichever flow starts next: the table only ever
        grows when somebody starts a consent, so that is exactly when it is tidied. (It
        used to be pruned by the Gmail sync cycle, which a space with no mailbox never
        runs, so Drive's and Slack's rows were never removed there.)
        """
        self.prune_expired(now)
        jti = b64url(os.urandom(_JTI_BYTES))
        self.session.add(
            GoogleOAuthState(
                jti=jti,
                code_verifier=code_verifier,
                user_id=user_id,
                expires_at=now + timedelta(minutes=STATE_TTL_MINUTES),
                purpose=purpose,
            )
        )
        self.session.flush()
        return jti

    def consume(self, jti: str, now: datetime, purpose: str) -> GoogleOAuthState | None:
        """Marks the row consumed and returns it, or returns `None` if it does not
        exist, has expired, was already consumed, or was minted for a different flow.

        One statement, deliberately: a conditional `UPDATE ... WHERE consumed_at IS
        NULL ... RETURNING`. Postgres serialises two concurrent callbacks carrying the
        same jti on the row lock, and the loser re-evaluates its `WHERE` against the
        winner's committed row, finds `consumed_at` no longer NULL and updates nothing.
        `test_gmail_models.py` proves that with two real connections and a barrier --
        drop the predicate and it reports `[1, 1]` instead of `[0, 1]`.

        A read-then-write pair over the same column would pass every single-threaded
        test and hand two callbacks the same authorisation code.

        `purpose` is part of the `WHERE` and not a check on the row after the fact, so a
        state minted for another flow simply does not match this statement at all: it is
        left exactly as it was, still single-use and still redeemable by its own flow's
        callback. Answering `None` here must not spend it.
        """
        return self.session.execute(
            update(GoogleOAuthState)
            .where(
                GoogleOAuthState.jti == jti,
                GoogleOAuthState.consumed_at.is_(None),
                GoogleOAuthState.expires_at > now,
                GoogleOAuthState.purpose == purpose,
            )
            .values(consumed_at=now)
            .returning(GoogleOAuthState)
        ).scalar_one_or_none()

    def prune_expired(self, now: datetime) -> int:
        """Expiry, not consumption, is the criterion: a consumed row is still evidence
        that a jti was used, and it has to outlive its own TTL or the single-use check
        above would start answering "unknown" instead of "already redeemed" for a
        replay that arrives seconds later.

        `RETURNING` rather than `rowcount`: the DBAPI's row count is typed as `Any`-free
        only on `CursorResult`, and this table is small and short-lived by construction,
        so counting the returned ids costs nothing worth naming.
        """
        deleted = self.session.execute(
            delete(GoogleOAuthState)
            .where(GoogleOAuthState.expires_at < now)
            .returning(GoogleOAuthState.id)
        )
        return len(deleted.scalars().all())
