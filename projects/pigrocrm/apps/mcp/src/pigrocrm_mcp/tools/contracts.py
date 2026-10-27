from datetime import date
from typing import Any
from uuid import UUID

from pigrocrm.core.contract_emails.service import ContractEmailService
from pigrocrm.core.contracts.schemas import (
    ClauseNoteCreate,
    ClauseNoteUpdate,
    ContractCreate,
    ContractListQuery,
    ContractProjectionQuery,
    ContractRenew,
    ContractStateChange,
    ContractUpdate,
    RateCardCreate,
    RateCardUpdate,
    RenewalAssumptionUpsert,
)
from pigrocrm.core.contracts.service import (
    ClauseNoteService,
    ContractProjectionService,
    ContractService,
    RateCardService,
    RenewalAssumptionService,
)
from pigrocrm.core.work_units.register import ContractRegisterService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.changes import pick, refuse_empty, report


def create(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return (
        ContractService(context.session)
        .create(ContractCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def get(context: McpContext, contract_id: str) -> dict[str, Any]:
    return (
        ContractService(context.session)
        .detail(UUID(contract_id), context.actor)
        .model_dump(mode="json")
    )


def update(context: McpContext, contract_id: str, data: dict[str, Any]) -> dict[str, Any]:
    service = ContractService(context.session)
    identifier = UUID(contract_id)
    changes = ContractUpdate(**data)
    refuse_empty(data)
    before = service.get(identifier, context.actor)
    return report(before, service.update(identifier, changes, context.actor))


def set_state(context: McpContext, contract_id: str, stato: str) -> dict[str, Any]:
    return (
        ContractService(context.session)
        .set_state(
            UUID(contract_id), ContractStateChange.model_validate({"stato": stato}), context.actor
        )
        .model_dump(mode="json")
    )


def concentration_cap(
    context: McpContext, contract_id: str, as_of: date | None, soglia: float | None
) -> dict[str, Any]:
    return (
        ContractService(context.session)
        .concentration_cap(UUID(contract_id), context.actor, as_of, soglia)
        .model_dump(mode="json")
    )


def register(context: McpContext, contract_id: str, da: date, a: date) -> dict[str, Any]:
    return (
        ContractRegisterService(context.session)
        .build(UUID(contract_id), da, a, context.actor)
        .model_dump(mode="json")
    )


def email_templates(context: McpContext, contract_id: str) -> list[dict[str, Any]]:
    return [
        t.model_dump(mode="json")
        for t in ContractEmailService(context.session).list_for_contract(
            UUID(contract_id), context.actor
        )
    ]


def email_preview(
    context: McpContext, contract_id: str, template_id: str, invoice_id: str
) -> dict[str, Any]:
    return (
        ContractEmailService(context.session)
        .preview(UUID(contract_id), UUID(template_id), UUID(invoice_id), context.actor)
        .model_dump(mode="json")
    )


def search(context: McpContext, query: ContractListQuery) -> dict[str, Any]:
    page = ContractService(context.session).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        "next_cursor": page.next_cursor,
    }


def create_rate_card(
    context: McpContext, contract_id: str, data: dict[str, Any], close_previous: bool
) -> dict[str, Any]:
    return (
        RateCardService(context.session)
        .create(
            UUID(contract_id), RateCardCreate(**data), context.actor, close_previous=close_previous
        )
        .model_dump(mode="json")
    )


def update_rate_card(
    context: McpContext, contract_id: str, rate_card_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    service = RateCardService(context.session)
    contract, card = UUID(contract_id), UUID(rate_card_id)
    changes = RateCardUpdate(**data)
    refuse_empty(data)
    before = pick(service.list_for_contract(contract, context.actor), card, "rate_card")
    return report(before, service.update(contract, card, changes, context.actor))


def list_rate_cards(context: McpContext, contract_id: str) -> list[dict[str, Any]]:
    cards = RateCardService(context.session).list_for_contract(UUID(contract_id), context.actor)
    return [card.model_dump(mode="json") for card in cards]


def set_renewal_assumption(
    context: McpContext, contract_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    return (
        RenewalAssumptionService(context.session)
        .upsert(UUID(contract_id), RenewalAssumptionUpsert(**data), context.actor)
        .model_dump(mode="json")
    )


def get_renewal_assumption(context: McpContext, contract_id: str) -> dict[str, Any]:
    return (
        RenewalAssumptionService(context.session)
        .get(UUID(contract_id), context.actor)
        .model_dump(mode="json")
    )


def project_revenue(
    context: McpContext, contract_id: str, query: ContractProjectionQuery
) -> dict[str, Any]:
    return (
        ContractProjectionService(context.session)
        .project(UUID(contract_id), query, context.actor)
        .model_dump(mode="json")
    )


def create_clause_note(
    context: McpContext, contract_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    return (
        ClauseNoteService(context.session)
        .create(UUID(contract_id), ClauseNoteCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def update_clause_note(
    context: McpContext, contract_id: str, note_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    service = ClauseNoteService(context.session)
    contract, note = UUID(contract_id), UUID(note_id)
    changes = ClauseNoteUpdate(**data)
    refuse_empty(data)
    before = pick(service.list_for_contract(contract, context.actor), note, "clause_note")
    return report(before, service.update(contract, note, changes, context.actor))


def list_clause_notes(context: McpContext, contract_id: str) -> list[dict[str, Any]]:
    notes = ClauseNoteService(context.session).list_for_contract(UUID(contract_id), context.actor)
    return [note.model_dump(mode="json") for note in notes]


def renew(context: McpContext, contract_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return (
        ContractService(context.session)
        .renew(UUID(contract_id), ContractRenew(**data), context.actor)
        .model_dump(mode="json")
    )
