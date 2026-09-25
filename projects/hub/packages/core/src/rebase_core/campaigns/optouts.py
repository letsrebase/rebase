"""An address no campaign may reach (spec § 7): its own link, a complaint Resend reports,
an admin's «Non scrivere mai». The first reason recorded stays; recording it again is a
no-op, so a double click or a webhook retry changes nothing."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rebase_core.models import CampaignOptout, CampaignRecipient

TOKEN_MAX_LENGTH = 64


class OptoutService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def unsubscribe(self, token: str) -> None:
        if not token or len(token) > TOKEN_MAX_LENGTH:
            return
        row = self.session.scalar(
            select(CampaignRecipient).where(CampaignRecipient.disiscrizione_token == token)
        )
        if row is not None:
            self.record(row.email, "link", row.campaign_id)

    def never_write(self, email: str) -> None:
        self.record(email, "admin", None)

    def record(self, email: str, fonte: str, campaign_id: UUID | None) -> None:
        self.stage(email, fonte, campaign_id)
        self.session.commit()

    def stage(self, email: str, fonte: str, campaign_id: UUID | None) -> None:
        """`record` without the commit, for a caller whose own write must land in the
        same transaction as the opt-out (a complaint and its `reclamo_at`)."""
        statement = (
            insert(CampaignOptout)
            .values(email=email.strip().lower(), fonte=fonte, campaign_id=campaign_id)
            .on_conflict_do_nothing(index_elements=["email"])
        )
        self.session.execute(statement)
