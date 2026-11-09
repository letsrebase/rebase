"""Renewing a contract into its successor (REB-763)."""

from copy import deepcopy
from uuid import UUID

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.renewal import RenewalRefused, carried_cards, renewed_term
from pigrocrm.core.contracts.repository import ContractRepository, RateCardRepository
from pigrocrm.core.contracts.rules import ENTITY
from pigrocrm.core.contracts.schemas import ContractRead, ContractRenew
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.repository import OrganizationRepository

# The columns a renewal does not copy from the contract it renews: the row's own identity
# and bookkeeping, the term (which the renewal decides), the state (the successor is
# `active`, whatever the contract it renews was), the link back and the deal it was converted
# from (a deal becomes one contract; the renewal chain is `previous_contract_id`).
_NOT_RENEWED = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
        "start",
        "end",
        "status",
        "previous_contract_id",
        "deal_id",
    }
)


class ContractRenewalService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ContractRepository(session)
        self.organizations = OrganizationRepository(session)
        self.activities = ActivityService(session)
        self.legal_entity = LegalEntityService(session)

    def renew(self, contract_id: UUID, data: ContractRenew, actor: Actor) -> ContractRead:
        """REB-763: the contract that renews `contract_id`. The successor carries the
        contract's terms as they stand (every column but the ones named in
        `_NOT_RENEWED`, so a column added later travels by default instead of silently
        resetting on every renewal) on the renewed term (`renewal.renewed_term`), is
        `active`, and is linked back through `previous_contract_id`. The rate cards
        that reach the new term go across with the validity they have on it
        (`renewal.carried_cards`); the contract's own cards are not touched, so the history
        of what was charged stays on the contract it was charged under.

        Not carried, because each is a fact about the term that is ending: the renewal
        assumption (a belief about revenue beyond *that* term), the expenses and the days,
        and the clause notes, which read the words of a signed document that the renewed
        term may have replaced. The contract it renews keeps its `status`: nothing here
        decides that a term is over.

        A contract renews once (the unique key on the link is the database's own word, the
        check below the sentence the person reads) and only when it has an end to renew
        from.
        """
        actor.require_write("renew_contract")
        contract = self.repo.get_locked(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        existing = self.session.scalar(
            select(Contract.id).where(Contract.previous_contract_id == contract_id)
        )
        if existing is not None:
            raise Conflict(
                ENTITY,
                "il contratto è già stato rinnovato",
                contract_id=str(contract_id),
                successor_id=str(existing),
            )
        try:
            start, end = renewed_term(
                start=contract.start,
                end=contract.end,
                new_start=data.start,
                new_end=data.end,
                duration=None
                if data.duration is None
                else (data.duration.count, data.duration.unit),
            )
        except RenewalRefused as refused:
            raise ValidationFailed(
                ENTITY, refused.field, str(refused), expected=refused.expected
            ) from refused
        # The legal entity of the customer as it is now, as `create` takes it, and never a
        # deactivated one: a customer moved since the contract began renews on the legal entity
        # it has moved to, and an archived customer has nothing to renew for.
        customer = self.organizations.get(contract.customer_id)
        if customer is None:
            raise NotFound("organization", contract.customer_id)
        legal_entity_id = self.legal_entity.inherited(customer.legal_entity_id, ENTITY)

        carried = {
            column.key: deepcopy(getattr(contract, column.key))
            for column in sa_inspect(Contract).column_attrs
            if column.key not in _NOT_RENEWED
        }
        if data.title is not None:
            carried["title"] = data.title
        carried["legal_entity_id"] = legal_entity_id
        successor = Contract(
            **carried,
            start=start,
            end=end,
            status="active",
            previous_contract_id=contract.id,
        )
        # Two renewals racing: the lock above makes the second read the first's row, so
        # the unique key on the link is the last resort.
        with savepoint(
            self.session,
            lambda _: Conflict(
                ENTITY, "il contratto è già stato rinnovato", contract_id=str(contract_id)
            ),
        ):
            self.session.add(successor)
            self.session.flush()
        for card, valid_from, valid_to in carried_cards(
            RateCardRepository(self.session).list_for_contract(contract.id), start
        ):
            self.session.add(
                RateCard(
                    contract_id=successor.id,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    type=card.type,
                    amount=card.amount,
                    unit=card.unit,
                    allowed_fractions=list(card.allowed_fractions),
                    minimum_hours=card.minimum_hours,
                    delivery_period=card.delivery_period,
                    expected_quantity=card.expected_quantity,
                )
            )
        self.session.flush()
        self.activities.record(
            ENTITY,
            successor.id,
            "created",
            actor,
            {"title": successor.title, "renew": str(contract.id)},
        )
        self.activities.record(
            ENTITY,
            contract.id,
            "renewed",
            actor,
            {
                "successor": str(successor.id),
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        self.session.commit()
        return ContractRead.model_validate(successor)
