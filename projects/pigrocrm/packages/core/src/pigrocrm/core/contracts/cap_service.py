"""A contract's cap on its customer's share of revenue (REB-352 §1.5, REB-761): the share
over the anniversary year, and the «ci sta?» simulation of a synthetic addition."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.contracts.dates import anniversary_year_bounds
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.contracts.rules import ENTITY
from pigrocrm.core.contracts.schemas import (
    ConcentrationFigures,
    ContractCapSimulation,
    ContractConcentrationCap,
)
from pigrocrm.core.db import today_local
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.money import percent_to_share, round_money, share

# The currency a simulated addition is in: a deal's estimate carries none, and deals are
# estimated in euros (`Deal.budgeted_value`).
SIMULATION_CURRENCY = "EUR"


class ContractCapService:
    def __init__(self, session: Session) -> None:
        self.repo = ContractRepository(session)
        self.analytics = AnalyticsRepository(session)

    def concentration_cap(
        self,
        contract_id: UUID,
        actor: Actor,
        as_of: date | None = None,
        threshold: float | None = None,
    ) -> ContractConcentrationCap:
        """REB-352 §1.5: one engagement's own share of total invoiced revenue over
        the anniversary year containing `as_of` (today, in the emitter's own zone,
        when not given). Read-only, like every other figure `AnalyticsService`
        exposes -- no `require_write`. `threshold` is a caller-supplied share in [0, 1]
        and is never stored; without one the share is compared with the contract's own
        cap when it has one (REB-761), and `superata` stays `None` when it has neither.
        """
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        period_from, period_to = self._anniversary_window(contract, as_of)
        threshold_from_contract = threshold is None and contract.customer_share_cap is not None
        if threshold is None and contract.customer_share_cap is not None:
            threshold = percent_to_share(contract.customer_share_cap)
        # Within the contract's own legal entity (REB-630, spec 2026-10-03 §1.9): a share of
        # revenue is a share of one legal entity's revenue, and the contract names which.
        # Per currency, never converted (REB-776): the contract's own `currency` says what
        # its fees are in, not what the customer's invoices were issued in.
        revenue = self.analytics.revenue_for_customer_in_window(
            contract.customer_id, period_from, period_to, legal_entity_id=contract.legal_entity_id
        )
        if not revenue:
            revenue = [(contract.currency, Decimal("0.00"), Decimal("0.00"))]
        currencies = []
        for currency, customer_revenue, total_revenue in revenue:
            quota = share(customer_revenue, total_revenue)
            exceeded = (quota >= threshold) if threshold is not None else None
            currencies.append(
                ConcentrationFigures(
                    currency=currency,
                    customer_revenue=customer_revenue,
                    total_revenue=total_revenue,
                    share=quota,
                    exceeded=exceeded,
                )
            )
        return ContractConcentrationCap(
            contract_id=contract.id,
            customer_id=contract.customer_id,
            period_from=period_from,
            period_to=period_to,
            threshold=threshold,
            threshold_from_contract=threshold_from_contract,
            currencies=currencies,
        )

    def simulate_cap(
        self, contract_id: UUID, addition: Decimal, expected_date: date | None, actor: Actor
    ) -> ContractCapSimulation:
        """REB-761: the contract's cap against its customer's share after a synthetic
        addition, the «ci sta?» simulator's own reading (`AnalyticsService.simulate_ceiling`
        calls this when it is given a contract). The addition is revenue of this customer
        on the anniversary year containing `expected_date` (today when not given), so it
        raises the customer's figure and the total together. Nothing is written.

        A contract with no cap has nothing to be checked against, and says so naming the
        field instead of answering that everything fits.
        """
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        if contract.customer_share_cap is None:
            raise ValidationFailed(
                ENTITY,
                "customer_share_cap",
                "il contratto non ha un tetto sulla quota del cliente",
                expected="un contratto con un tetto, da impostare sul contratto",
            )
        if contract.currency != SIMULATION_CURRENCY:
            # The addition is a deal's estimate, in the currency deals are estimated in; added
            # to the revenue of a contract in another one it would be a sum of two currencies.
            raise ValidationFailed(
                ENTITY,
                "currency",
                f"il simulatore ragiona in {SIMULATION_CURRENCY}: il contratto è in "
                f"{contract.currency}",
                expected=f"un contratto in {SIMULATION_CURRENCY}",
            )
        # A contract that has not started (a fresh renewal) is asked about its first year:
        # the cap is read where the clause begins, and the simulation still answers.
        reference = expected_date if expected_date is not None else today_local()
        period_from, period_to = self._anniversary_window(contract, max(reference, contract.start))
        revenue = self.analytics.revenue_for_customer_in_window(
            contract.customer_id, period_from, period_to, legal_entity_id=contract.legal_entity_id
        )
        customer, total = next(
            ((c, t) for currency, c, t in revenue if currency == contract.currency),
            (Decimal("0.00"), Decimal("0.00")),
        )
        cap = percent_to_share(contract.customer_share_cap)
        simulated_share = share(customer + addition, total + addition)
        return ContractCapSimulation(
            contract_id=contract.id,
            title=contract.title,
            customer_id=contract.customer_id,
            currency=contract.currency,
            period_from=period_from,
            period_to=period_to,
            cap=cap,
            current_customer_revenue=customer,
            current_total_revenue=total,
            current_share=share(customer, total),
            simulated_customer_revenue=round_money(customer + addition),
            simulated_total_revenue=round_money(total + addition),
            simulated_share=simulated_share,
            falls_within=simulated_share < cap,
        )

    @staticmethod
    def _anniversary_window(
        contract: Contract, as_of: date | None, field: str = "as_of"
    ) -> tuple[date, date]:
        """The anniversary year of `contract` that contains `as_of` (today when not given):
        the window a cap resets on, never the calendar year."""
        reference = as_of if as_of is not None else today_local()
        if reference < contract.start:
            raise ValidationFailed(
                ENTITY,
                field,
                "precede l'inizio del contratto",
                expected=f">= {contract.start.isoformat()}",
            )
        return anniversary_year_bounds(contract.start, reference)
