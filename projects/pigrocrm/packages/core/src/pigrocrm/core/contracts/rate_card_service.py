from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import RateCard
from pigrocrm.core.contracts.repository import ContractRepository, RateCardRepository
from pigrocrm.core.contracts.rules import check_rate_card_shape
from pigrocrm.core.contracts.schemas import RateCardCreate, RateCardRead, RateCardUpdate
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes


def _overlap(contract_id: UUID) -> Conflict:
    """`ck_rate_cards_no_overlap` is the real authority (a card is refused "by the
    database, not by a service-level check"); this turns its raw exclusion violation into
    this project's own clean error, the way `LegalEntityService.upsert` does for its own
    singleton race."""
    return Conflict(
        "rate_card",
        "il periodo di validità si sovrappone a un'altra scheda tariffaria di questo contratto",
        contract_id=str(contract_id),
    )


class RateCardService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = RateCardRepository(session)
        self.contracts = ContractRepository(session)
        self.activities = ActivityService(session)

    def create(
        self,
        contract_id: UUID,
        data: RateCardCreate,
        actor: Actor,
        *,
        close_previous: bool = False,
    ) -> RateCardRead:
        """`close_previous` asks for the card in force on `data.valido_da` to be closed
        the day before it, in the same transaction, instead of the overlap being
        refused. It is an argument of the call and not a field of `RateCardCreate`,
        because that schema is also what a proposal carries for its first card, which
        has nothing to replace. Off by default: an overlap stays refused unless the
        caller said it meant to replace the current rate."""
        actor.require_write("create_rate_card")
        contract = self.contracts.get(contract_id)
        if contract is None:
            raise NotFound("contract", contract_id)

        payload = data.model_dump()
        check_rate_card_shape(payload)
        payload["contract_id"] = contract_id

        rate_card = RateCard(**payload)
        closed: RateCard | None = None
        with savepoint(self.session, lambda _: _overlap(contract_id)):
            if close_previous:
                closed = self._close_card_in_force(contract_id, rate_card.valido_da)
            self.session.add(rate_card)

        self.activities.record(
            "contract",
            contract_id,
            "rate_card_added",
            actor,
            {
                "tipo": rate_card.tipo,
                "valido_da": str(rate_card.valido_da),
                # Why another card's end moved, when it did.
                **(
                    {
                        "precedente_valido_da": str(closed.valido_da),
                        "precedente_valido_a": str(closed.valido_a),
                    }
                    if closed is not None
                    else {}
                ),
            },
        )
        self.session.commit()
        return RateCardRead.model_validate(rate_card)

    def _close_card_in_force(self, contract_id: UUID, start: date) -> RateCard | None:
        """Ends, the day before `start`, the card that began earlier and is still in
        force on `start` -- the one a new rate from `start` replaces. The exclusion
        constraint allows at most one such card per contract. A card that began on or
        after `start` is not touched, so a genuine overlap is refused as ever. Returns
        the card it closed, so the timeline can say why its end moved."""
        for card in self.repo.list_for_contract(contract_id):
            if card.valido_da < start and (card.valido_a is None or card.valido_a >= start):
                card.valido_a = start - timedelta(days=1)
                self.session.flush()
                return card
        return None

    def update(
        self, contract_id: UUID, rate_card_id: UUID, data: RateCardUpdate, actor: Actor
    ) -> RateCardRead:
        actor.require_write("update_rate_card")
        card = self.repo.get(rate_card_id)
        if card is None or card.contract_id != contract_id:
            raise NotFound("rate_card", rate_card_id)

        changes = supplied_changes(data)
        reject_cleared_columns("rate_card", RateCard, changes)
        merged = {
            key: changes.get(key, getattr(card, key))
            for key in ("valido_da", "valido_a", "tipo", "ore_minime", "periodo_erogazione")
        }
        check_rate_card_shape(merged)
        if merged["valido_a"] is not None and merged["valido_a"] < merged["valido_da"]:
            raise ValidationFailed(
                "rate_card",
                "valido_a",
                "non può precedere valido_da",
                expected=f">= {merged['valido_da'].isoformat()}",
            )

        with savepoint(self.session, lambda _: _overlap(contract_id)):
            for key, value in changes.items():
                setattr(card, key, value)

        self.activities.record(
            "contract",
            contract_id,
            "rate_card_updated",
            actor,
            {"changed": sorted(changes), "valido_da": str(card.valido_da)},
        )
        self.session.commit()
        return RateCardRead.model_validate(card)

    def list_for_contract(self, contract_id: UUID, actor: Actor) -> list[RateCardRead]:
        if self.contracts.get(contract_id) is None:
            raise NotFound("contract", contract_id)
        return [
            RateCardRead.model_validate(card) for card in self.repo.list_for_contract(contract_id)
        ]
