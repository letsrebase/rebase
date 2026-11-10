"""Thin calls into `DueItemService`, like every other tool module.

An agent reads the due items and records or undoes a payment. What it may do depends on the
item, and the service decides: an incoming item (a client paying) is open to any token that
may write, an outgoing or fiscal one only to an administrator's token with `full_access`
(`settle_outgoing_due_item`, `read_fiscal_due_items`). Nothing here checks a role.
"""

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pigrocrm.core.due_items.schemas import (
    Direction,
    DueItemListQuery,
    PayrollTotalsQuery,
    SettlementCreate,
    SettlementReversal,
    State,
)
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.params import (
    iso_date,
    literal_arg,
    parse_legal_entity_id,
    required_iso_date,
)


def _decimal(name: str, value: float | str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationFailed(
            "input", name, "is not an amount", expected="a number with two decimals"
        ) from exc


def _required_decimal(name: str, value: float | str) -> Decimal:
    found = _decimal(name, value)
    assert found is not None
    return found


def _uuid(name: str, value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationFailed("input", name, "is not an id", expected="a UUID") from exc


def search(
    context: McpContext,
    *,
    direction: str | None,
    state: str | None,
    source_kind: str | None,
    legal_entity_id: str | None,
    organization_id: str | None,
    due_from: str | None,
    due_to: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    query = DueItemListQuery(
        direction=literal_arg("direction", direction, Direction),
        state=literal_arg("state", state, State),
        source_kind=source_kind,
        legal_entity_id=parse_legal_entity_id(legal_entity_id),
        organization_id=_uuid("organization_id", organization_id),
        due_from=iso_date(due_from, "due_from"),
        due_to=iso_date(due_to, "due_to"),
        limit=limit,
        cursor=_uuid("cursor", cursor),
    )
    return DueItemService(context.session).list(query, context.actor).model_dump(mode="json")


def payroll_totals(
    context: McpContext, legal_entity_id: str | None, due_from: str | None, due_to: str | None
) -> dict[str, Any]:
    query = PayrollTotalsQuery(
        legal_entity_id=parse_legal_entity_id(legal_entity_id),
        due_from=iso_date(due_from, "due_from"),
        due_to=iso_date(due_to, "due_to"),
    )
    return (
        DueItemService(context.session)
        .list_payroll_totals(query, context.actor)
        .model_dump(mode="json")
    )


def get(context: McpContext, due_item_id: str) -> dict[str, Any]:
    item = DueItemService(context.session).get(
        _required_uuid("due_item_id", due_item_id), context.actor
    )
    return item.model_dump(mode="json")


def settle(
    context: McpContext,
    due_item_id: str,
    amount: float | str,
    settled_on: str,
    value_date: str | None,
    account_amount: float | str | None,
    account_currency: str | None,
    note: str | None,
) -> dict[str, Any]:
    data = SettlementCreate(
        amount=_required_decimal("amount", amount),
        settled_on=required_iso_date(settled_on, "settled_on"),
        value_date=iso_date(value_date, "value_date"),
        account_amount=_decimal("account_amount", account_amount),
        account_currency=account_currency,
        note=note,
    )
    item = DueItemService(context.session).settle(
        _required_uuid("due_item_id", due_item_id), data, context.actor
    )
    return item.model_dump(mode="json")


def reverse_settlement(
    context: McpContext, settlement_id: str, reason: str | None
) -> dict[str, Any]:
    item = DueItemService(context.session).reverse_settlement(
        _required_uuid("settlement_id", settlement_id),
        SettlementReversal(reason=reason).reason,
        context.actor,
    )
    return item.model_dump(mode="json")


def _required_uuid(name: str, value: str) -> UUID:
    found = _uuid(name, value)
    assert found is not None
    return found
