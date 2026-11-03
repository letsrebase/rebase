from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts import projection
from pigrocrm.core.contracts.repository import (
    ContractRepository,
    RateCardRepository,
    RenewalAssumptionRepository,
)
from pigrocrm.core.contracts.rules import ENTITY
from pigrocrm.core.contracts.schemas import ContractProjectionQuery, ContractProjectionRead
from pigrocrm.core.db import today_local
from pigrocrm.core.errors import NotFound


class ContractProjectionService:
    """The genuine "projected" figure a contract's own recurring-fee schedule and
    renewal assumption produce (REB-375) -- entirely apart from
    `AnalyticsService.cash_overview`'s draft-based `projected`. Reads only: every
    number here is derived, nothing is written."""

    def __init__(self, session: Session) -> None:
        self.contracts = ContractRepository(session)
        self.rate_cards = RateCardRepository(session)
        self.renewal_assumptions = RenewalAssumptionRepository(session)

    def project(
        self, contract_id: UUID, query: ContractProjectionQuery, actor: Actor
    ) -> ContractProjectionRead:
        contract = self.contracts.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)

        come_di: date = query.come_di if query.come_di is not None else today_local()
        rate_cards = self.rate_cards.list_for_contract(contract_id)
        assumption = self.renewal_assumptions.get_for_contract(contract_id)
        result = projection.project(
            contract, rate_cards, assumption, come_di, query.date_from, query.date_to
        )
        return ContractProjectionRead(
            contract_id=result.contract_id,
            come_di=result.come_di,
            date_from=result.date_from,
            date_to=result.date_to,
            irrevocability_window_until_to=result.irrevocability_window_until_to,
            scheduled=result.scheduled,
            from_renewal=result.from_renewal,
            total=result.total,
        )
