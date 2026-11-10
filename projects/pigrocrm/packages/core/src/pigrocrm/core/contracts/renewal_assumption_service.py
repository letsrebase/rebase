from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import RenewalAssumption
from pigrocrm.core.contracts.repository import ContractRepository, RenewalAssumptionRepository
from pigrocrm.core.contracts.schemas import RenewalAssumptionRead, RenewalAssumptionUpsert
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.errors import Conflict, NotFound


class RenewalAssumptionService:
    """A contract's own recorded belief about revenue beyond its known term
    (REB-375). One row per contract, revised in place -- see `RenewalAssumption`
    (contracts/models.py) for why this is a singleton-per-parent, not a history."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = RenewalAssumptionRepository(session)
        self.contracts = ContractRepository(session)
        self.activities = ActivityService(session)

    def upsert(
        self, contract_id: UUID, data: RenewalAssumptionUpsert, actor: Actor
    ) -> RenewalAssumptionRead:
        """Create-or-update the one row for `contract_id`.

        The write sits inside the savepoint: it is the only statement that can violate
        `uq_renewal_assumptions_contract_id`, and two concurrent first-time saves must
        surface a clean `Conflict` rather than a raw `IntegrityError` -- the same shape
        `FiscalProfileService.upsert` uses for its own singleton race.
        """
        actor.require_write("set_renewal_assumption")
        if self.contracts.get(contract_id) is None:
            raise NotFound("contract", contract_id)

        payload = data.model_dump()
        assumption = self.repo.get_for_contract(contract_id)
        with savepoint(
            self.session,
            lambda _: Conflict(
                "renewal_assumption",
                "esiste già un'assunzione di rinnovo per questo contratto",
                contract_id=str(contract_id),
            ),
        ):
            if assumption is None:
                assumption = self.repo.add(RenewalAssumption(contract_id=contract_id, **payload))
            else:
                for key, value in payload.items():
                    setattr(assumption, key, value)
        self.activities.record(
            "contract",
            contract_id,
            "renewal_assumption_set",
            actor,
            {
                "probability": assumption.probability,
                "horizon_until": str(assumption.horizon_until),
            },
        )
        self.session.commit()
        return RenewalAssumptionRead.model_validate(assumption)

    def get(self, contract_id: UUID, actor: Actor) -> RenewalAssumptionRead:
        if self.contracts.get(contract_id) is None:
            raise NotFound("contract", contract_id)
        assumption = self.repo.get_for_contract(contract_id)
        if assumption is None:
            raise NotFound("renewal_assumption", contract_id)
        return RenewalAssumptionRead.model_validate(assumption)
