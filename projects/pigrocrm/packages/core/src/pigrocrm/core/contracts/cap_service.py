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
# estimated in euros (`Deal.valore_preventivato`).
SIMULATION_DIVISA = "EUR"


class ContractCapService:
    def __init__(self, session: Session) -> None:
        self.repo = ContractRepository(session)
        self.analytics = AnalyticsRepository(session)

    def concentration_cap(
        self,
        contract_id: UUID,
        actor: Actor,
        as_of: date | None = None,
        soglia: float | None = None,
    ) -> ContractConcentrationCap:
        """REB-352 §1.5: one engagement's own share of total invoiced revenue over
        the anniversary year containing `as_of` (today, in the emitter's own zone,
        when not given). Read-only, like every other figure `AnalyticsService`
        exposes -- no `require_write`. `soglia` is a caller-supplied share in [0, 1]
        and is never stored; without one the share is compared with the contract's own
        cap when it has one (REB-761), and `superata` stays `None` when it has neither.
        """
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        periodo_da, periodo_a = self._anniversary_window(contract, as_of)
        soglia_da_contratto = soglia is None and contract.tetto_quota_cliente is not None
        if soglia is None and contract.tetto_quota_cliente is not None:
            soglia = percent_to_share(contract.tetto_quota_cliente)
        # Within the contract's own azienda (REB-630, spec 2026-10-03 §1.9): a share of
        # revenue is a share of one azienda's revenue, and the contract names which.
        # Per currency, never converted (REB-776): the contract's own `divisa` says what
        # its fees are in, not what the customer's invoices were issued in.
        revenue = self.analytics.revenue_for_customer_in_window(
            contract.customer_id, periodo_da, periodo_a, azienda_id=contract.azienda_id
        )
        if not revenue:
            revenue = [(contract.divisa, Decimal("0.00"), Decimal("0.00"))]
        valute = []
        for divisa, ricavi_cliente, ricavi_totali in revenue:
            quota = share(ricavi_cliente, ricavi_totali)
            superata = (quota >= soglia) if soglia is not None else None
            valute.append(
                ConcentrationFigures(
                    divisa=divisa,
                    ricavi_cliente=ricavi_cliente,
                    ricavi_totali=ricavi_totali,
                    quota=quota,
                    superata=superata,
                )
            )
        return ContractConcentrationCap(
            contract_id=contract.id,
            customer_id=contract.customer_id,
            periodo_da=periodo_da,
            periodo_a=periodo_a,
            soglia=soglia,
            soglia_da_contratto=soglia_da_contratto,
            valute=valute,
        )

    def simulate_cap(
        self, contract_id: UUID, aggiunta: Decimal, data_prevista: date | None, actor: Actor
    ) -> ContractCapSimulation:
        """REB-761: the contract's cap against its customer's share after a synthetic
        addition, the «ci sta?» simulator's own reading (`AnalyticsService.simulate_ceiling`
        calls this when it is given a contract). The addition is revenue of this customer
        on the anniversary year containing `data_prevista` (today when not given), so it
        raises the customer's figure and the total together. Nothing is written.

        A contract with no cap has nothing to be checked against, and says so naming the
        field instead of answering that everything fits.
        """
        contract = self.repo.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        if contract.tetto_quota_cliente is None:
            raise ValidationFailed(
                ENTITY,
                "tetto_quota_cliente",
                "il contratto non ha un tetto sulla quota del cliente",
                expected="un contratto con un tetto, da impostare sul contratto",
            )
        if contract.divisa != SIMULATION_DIVISA:
            # The addition is a deal's estimate, in the currency deals are estimated in; added
            # to the revenue of a contract in another one it would be a sum of two currencies.
            raise ValidationFailed(
                ENTITY,
                "divisa",
                f"il simulatore ragiona in {SIMULATION_DIVISA}: il contratto è in "
                f"{contract.divisa}",
                expected=f"un contratto in {SIMULATION_DIVISA}",
            )
        # A contract that has not started (a fresh renewal) is asked about its first year:
        # the cap is read where the clause begins, and the simulation still answers.
        reference = data_prevista if data_prevista is not None else today_local()
        periodo_da, periodo_a = self._anniversary_window(contract, max(reference, contract.inizio))
        revenue = self.analytics.revenue_for_customer_in_window(
            contract.customer_id, periodo_da, periodo_a, azienda_id=contract.azienda_id
        )
        cliente, totale = next(
            ((c, t) for divisa, c, t in revenue if divisa == contract.divisa),
            (Decimal("0.00"), Decimal("0.00")),
        )
        tetto = percent_to_share(contract.tetto_quota_cliente)
        quota_simulata = share(cliente + aggiunta, totale + aggiunta)
        return ContractCapSimulation(
            contract_id=contract.id,
            titolo=contract.titolo,
            customer_id=contract.customer_id,
            divisa=contract.divisa,
            periodo_da=periodo_da,
            periodo_a=periodo_a,
            tetto=tetto,
            ricavi_cliente_attuali=cliente,
            ricavi_totali_attuali=totale,
            quota_attuale=share(cliente, totale),
            ricavi_cliente_simulati=round_money(cliente + aggiunta),
            ricavi_totali_simulati=round_money(totale + aggiunta),
            quota_simulata=quota_simulata,
            rientra=quota_simulata < tetto,
        )

    @staticmethod
    def _anniversary_window(
        contract: Contract, as_of: date | None, field: str = "as_of"
    ) -> tuple[date, date]:
        """The anniversary year of `contract` that contains `as_of` (today when not given):
        the window a cap resets on, never the calendar year."""
        reference = as_of if as_of is not None else today_local()
        if reference < contract.inizio:
            raise ValidationFailed(
                ENTITY,
                field,
                "precede l'inizio del contratto",
                expected=f">= {contract.inizio.isoformat()}",
            )
        return anniversary_year_bounds(contract.inizio, reference)
