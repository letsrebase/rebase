"""Turning a won deal into a contract (spec 2026-11-03 §8, REB-933)."""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.from_deal import (
    card_from_term,
    proposed_end,
    proposed_start,
    terms_of,
)
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.contracts.rules import ENTITY, check_renewal_notice
from pigrocrm.core.contracts.schemas import (
    ContractFromDeal,
    ContractFromDealProposal,
    ContractRead,
    ProposedRateCard,
)
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.db import today_local
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.deals.models import Deal, DealValueLine
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.value import ValueTerm, terms_overlap
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.pipeline.service import PipelineService

DEAL = "deal"


def _already_converted(deal_id: UUID, contract: tuple[UUID, str]) -> Conflict:
    return Conflict(
        DEAL,
        f"il deal è già diventato il contratto «{contract[1]}»",
        deal_id=str(deal_id),
        contract_id=str(contract[0]),
    )


def _deal_taken(exc: IntegrityError, deal_id: UUID) -> Conflict | IntegrityError:
    """The unique key on `contracts.deal_id` is the last word when two conversions race past
    the row lock; any other violation is handed back unchanged."""
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint == "contracts_deal_id_key":
        return Conflict(DEAL, "il deal è già diventato un contratto", deal_id=str(deal_id))
    return exc


def _terms_or_refuse(lines: Sequence[DealValueLine], start: date) -> list[ValueTerm]:
    """The lines as terms starting from `start`, or a refusal naming the date when one falls
    outside the calendar (a duration that was fine on the deal's own dates may not be on these)."""
    try:
        return terms_of(lines, start)
    except (ValueError, OverflowError):
        raise ValidationFailed(
            ENTITY,
            "start",
            "con questa data di inizio una riga del deal esce dal calendario",
            expected="una data di inizio più vicina",
        ) from None


class ContractConversionService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.deals = DealRepository(session)
        self.contracts = ContractRepository(session)
        self.customers = CustomerRepository(session)
        self.pipeline = PipelineService(session)
        self.legal_entity = LegalEntityService(session)
        self.activities = ActivityService(session)

    def _convertible(self, deal: Deal) -> list[DealValueLine]:
        """The deal's lines, once it is a deal that may become a contract: won, with at least
        one line and no contract yet. Each refusal says which, so the person knows what to
        do first."""
        existing = self.deals.linked_contract(deal.id)
        if existing is not None:
            raise _already_converted(deal.id, existing)
        stage = self.pipeline.get(deal.pipeline_stage_id)
        if stage.type != "won":
            raise Conflict(
                DEAL,
                f"il deal è nella fase «{stage.name}»: solo un deal vinto diventa un contratto",
                deal_id=str(deal.id),
                stage=stage.name,
            )
        lines = self.deals.value_lines([deal.id]).get(deal.id, [])
        if not lines:
            raise Conflict(
                DEAL,
                "il deal non ha righe di valore: aggiungile prima, sono il prezzo del contratto",
                deal_id=str(deal.id),
            )
        return lines

    def propose(self, deal_id: UUID, actor: Actor) -> ContractFromDealProposal:
        """What the conversion would start from, refused for the deals it would refuse. A
        read: nothing is written."""
        del actor
        deal = self.deals.get(deal_id)
        if deal is None:
            raise NotFound(DEAL, deal_id)
        lines = self._convertible(deal)
        start = proposed_start(lines, today_local())
        terms = _terms_or_refuse(lines, start)
        return ContractFromDealProposal(
            deal_id=deal.id,
            customer_id=deal.customer_id,
            legal_entity_id=deal.legal_entity_id,
            title=deal.name,
            start=start,
            end=proposed_end(terms),
            cards=[card_from_term(term) for term in terms],
        )

    def create(self, deal_id: UUID, data: ContractFromDeal, actor: Actor) -> ContractRead:
        """The contract a won deal becomes: its customer and legal entity, the confirmed term
        and clauses, status `draft`, payment terms left to cascade from the customer, and one
        rate card per line of the deal (`from_deal.card_from_term`). From then on the deal's
        lines are locked (`DealService.update`)."""
        actor.require_write("convert_deal_to_contract")
        deal = self.deals.get_locked(deal_id)
        if deal is None:
            raise NotFound(DEAL, deal_id)
        payload = data.model_dump()
        check_renewal_notice(payload)
        lines = self._convertible(deal)
        if self.customers.get(deal.customer_id) is None:
            raise NotFound("customer", deal.customer_id)

        terms = _terms_or_refuse(lines, data.start)
        # Lines with no start follow the contract's, so two day or hour rates that never
        # met on the deal's dates may meet on these; the database would refuse them anyway,
        # and the person should read why.
        rated = [term for term in terms if term.unit != "flat"]
        for position, term in enumerate(rated):
            if any(terms_overlap(term, other) for other in rated[position + 1 :]):
                raise ValidationFailed(
                    ENTITY,
                    "start",
                    "con questa data di inizio due tariffe a giorni o a ore del deal si "
                    "sovrappongono: un contratto ne ha al più una in vigore in ogni data",
                    expected="una data di inizio che le tenga separate",
                )

        contract = Contract(
            **payload,
            customer_id=deal.customer_id,
            legal_entity_id=self.legal_entity.inherited(deal.legal_entity_id, ENTITY),
            deal_id=deal.id,
            status="draft",
        )
        cards: list[ProposedRateCard] = [card_from_term(term) for term in terms]
        with savepoint(self.session, lambda exc: _deal_taken(exc, deal.id)):
            self.session.add(contract)
            self.session.flush()
            for card in cards:
                self.session.add(RateCard(**card.model_dump(), contract_id=contract.id))
        self.activities.record(
            ENTITY,
            contract.id,
            "created",
            actor,
            {"title": contract.title, "deal": str(deal.id)},
        )
        self.activities.record(
            DEAL,
            deal.id,
            "converted_to_contract",
            actor,
            {"contract": str(contract.id), "title": contract.title},
        )
        self.session.commit()
        return ContractRead.model_validate(contract)
