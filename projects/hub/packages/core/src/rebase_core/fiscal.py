"""A freelancer's tax data, as the two contracts print them (REB-387).

A table of its own rather than columns on `freelancers`, because PostHog's warehouse
syncs `freelancers` whole (spec § 2). Filled by an admin in the match flow the first
time, reused afterwards, editable from «Match e contratti». The audit entry names the
fields that changed and never their values: a codice fiscale copied into
`admin_actions` would be one more place to delete it from.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import FiscalData, FiscalRead
from rebase_core.errors import NotFound
from rebase_core.models import Freelancer, FreelancerFiscal

ENTITY = "freelancer_fiscal"
FISCAL_FIELDS = ("codice_fiscale", "partita_iva", "domicilio", "pec")


class FiscalService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, freelancer_id: UUID) -> FiscalRead | None:
        self._require_card(freelancer_id)
        row = self._row(freelancer_id)
        return FiscalRead.model_validate(row) if row is not None else None

    def save(self, freelancer_id: UUID, data: FiscalData, admin_id: UUID) -> FiscalRead:
        """Writes or rewrites the one row of this card. Two first saves racing on one
        card: the unique index decides, and the loser writes again over the winner."""
        self._require_card(freelancer_id)
        try:
            return self._write(freelancer_id, data, admin_id)
        except IntegrityError:
            self.session.rollback()
            return self._write(freelancer_id, data, admin_id)

    def _write(self, freelancer_id: UUID, data: FiscalData, admin_id: UUID) -> FiscalRead:
        row = self._row(freelancer_id)
        before = {field: getattr(row, field) for field in FISCAL_FIELDS} if row is not None else {}
        if row is None:
            row = FreelancerFiscal(freelancer_id=freelancer_id)
            self.session.add(row)
        for field in FISCAL_FIELDS:
            setattr(row, field, getattr(data, field))
        row.updated_by = admin_id
        self.session.commit()
        changed = [field for field in FISCAL_FIELDS if before.get(field) != getattr(row, field)]
        if changed:
            AdminActionService(self.session).record(
                ENTITY, freelancer_id, "fiscal_updated", admin_id, {"changed": changed}
            )
        return FiscalRead.model_validate(row)

    def _row(self, freelancer_id: UUID) -> FreelancerFiscal | None:
        return self.session.scalar(
            select(FreelancerFiscal).where(FreelancerFiscal.freelancer_id == freelancer_id)
        )

    def _require_card(self, freelancer_id: UUID) -> None:
        card = self.session.get(Freelancer, freelancer_id)
        if card is None or card.deleted_at is not None:
            raise NotFound("freelancer", freelancer_id)
