"""The two referral rates an admin edits (P-REB-44, REB-610): one settings row, seeded by
migration 0024, read by the member area, the contract flow, the ledger and, through
`public_rates`, the public referral page."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.models import ReferralSettings
from rebase_core.referral_schemas import PublicReferralRates


class ReferralSettingsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_settings(self) -> ReferralSettings:
        """The one settings row, seeded by migration 0024; a second call in the same
        process never inserts again, `ReferralSettings.__doc__`'s own promise. Flushed,
        never committed: `record_reward_if_signed` calls this inside the row locks
        `SigningService._confirm_completion` already holds, and a commit here would
        release them and close that transaction early, ahead of the reward and the
        signature it belongs to (CodeRabbit) -- the same reason `write_framework`
        never commits either."""
        row = self._settings_row()
        if row is not None:
            return row
        row = ReferralSettings()
        self.session.add(row)
        self.session.flush()
        self.session.refresh(row)
        return row

    def _settings_row(self) -> ReferralSettings | None:
        """The oldest settings row, the one every reader agrees is the row."""
        return self.session.scalar(
            select(ReferralSettings).order_by(ReferralSettings.created_at).limit(1)
        )

    def public_rates(self) -> PublicReferralRates | None:
        """The two rates a stranger may read (`GET /api/hub/referral-rates`), from the
        same row `get_settings` serves the member area and the ledger, live on every call.

        Unlike `get_settings` this never writes: it is a public GET, and a request nobody
        authenticated must not seed a row. With no row it answers `None`, never the
        column defaults, because a figure the admin did not set is a figure nobody chose
        and the public page drops a rate it cannot read (design record 2026-09-29).
        Migration 0024 seeds the row, so `None` is a database that lost it, not a state
        a deploy reaches."""
        row = self._settings_row()
        if row is None:
            return None
        return PublicReferralRates(
            rate_freelancer=row.rate_freelancer, rate_company=row.rate_company
        )

    def save_settings(
        self, rate_freelancer: Decimal, rate_company: Decimal, admin_id: UUID
    ) -> ReferralSettings:
        row = self.get_settings()
        row.rate_freelancer = rate_freelancer
        row.rate_company = rate_company
        row.updated_by = admin_id
        self.session.commit()
        return row
