from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.schemas import CONTRACT_SORTS, ContractListQuery
from pigrocrm.core.db import decode_cursor, keyset_predicate, order_by


class ContractRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, contract_id: UUID, *, include_deleted: bool = False) -> Contract | None:
        contract = self.session.get(Contract, contract_id)
        if contract is None:
            return None
        if contract.deleted_at is not None and not include_deleted:
            return None
        return contract

    def add(self, contract: Contract) -> Contract:
        self.session.add(contract)
        self.session.flush()
        return contract

    # `list` is defined LAST in this class on purpose: `def list(...)` rebinds `list`
    # in the class namespace, and Python 3.13 evaluates annotations eagerly, so a
    # later `-> list[...]` would raise `TypeError` at import time.
    def list(self, query: ContractListQuery) -> list[Contract]:
        stmt = select(Contract).where(Contract.deleted_at.is_(None))
        if query.customer_id:
            stmt = stmt.where(Contract.customer_id == query.customer_id)
        if query.stato:
            stmt = stmt.where(Contract.stato == query.stato)

        # Residuo R9: keyset pagination over a whitelisted column, ordered
        # `col <dir>, id <dir>`.
        spec = CONTRACT_SORTS.resolve(query.sort)
        if query.cursor:
            value, row_id = decode_cursor(spec, query.cursor)
            stmt = stmt.where(keyset_predicate(spec, query.dir, value, row_id))

        return list(
            self.session.execute(
                stmt.order_by(*order_by(spec, query.dir)).limit(query.limit + 1)
            ).scalars()
        )


class RateCardRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, rate_card_id: UUID) -> RateCard | None:
        return self.session.get(RateCard, rate_card_id)

    def add(self, rate_card: RateCard) -> RateCard:
        self.session.add(rate_card)
        self.session.flush()
        return rate_card

    def list_for_contract(self, contract_id: UUID) -> list[RateCard]:
        """Every rate card of one contract, oldest term first -- a contract holds a
        handful of these over its life, never enough to need keyset pagination."""
        stmt = (
            select(RateCard)
            .where(RateCard.contract_id == contract_id)
            .order_by(RateCard.valido_da.asc(), RateCard.id.asc())
        )
        return list(self.session.execute(stmt).scalars())
