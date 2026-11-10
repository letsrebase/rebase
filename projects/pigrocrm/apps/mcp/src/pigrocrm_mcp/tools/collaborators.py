"""Thin calls into the collaborators services (engagements with freelancers, availability).

Every tool is an administrator's; what a token may do is decided in the service by the action
it names (`agent_actions.py`): nothing here checks a role.
"""

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pigrocrm.core.collaborators.days_report import DaysReportService
from pigrocrm.core.collaborators.letter_service import LetterService
from pigrocrm.core.collaborators.schemas import (
    EngagementCreate,
    EngagementListQuery,
    EngagementUpdate,
    FreelancerTaxData,
)
from pigrocrm.core.collaborators.service import EngagementService, FreelancerService
from pigrocrm.core.collaborators.signing import SigningService
from pigrocrm.core.documenso.client import signing_configured
from pigrocrm.core.errors import SigningUnavailable, ValidationFailed
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.changes import refuse_empty, report
from pigrocrm_mcp.tools.params import iso_date, required_iso_date


def _uuid(name: str, value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationFailed("input", name, "is not an id", expected="a UUID") from exc


def _required_uuid(name: str, value: str) -> UUID:
    found = _uuid(name, value)
    assert found is not None
    return found


def _decimal(name: str, value: float | str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationFailed("input", name, "is not a number", expected="a number") from exc


def preview_letter(
    context: McpContext, engagement_id: str, variables: dict[str, str | None] | None
) -> dict[str, Any]:
    found = LetterService(context.session, context.storage).preview_letter(
        _required_uuid("engagement_id", engagement_id), variables or {}, context.actor
    )
    return found.model_dump(mode="json")


def preview_framework_agreement(
    context: McpContext, freelancer_id: str, letter_type: str | None
) -> dict[str, Any]:
    found = LetterService(context.session, context.storage).preview_framework_agreement(
        _required_uuid("freelancer_id", freelancer_id), letter_type, context.actor
    )
    return found.model_dump(mode="json")


def create(
    context: McpContext,
    *,
    freelancer_id: str,
    role: str,
    starts_on: str,
    fee: float | str,
    fee_unit: str,
    payment_rule: str,
    days_per_week: float | str | None,
    ends_on: str | None,
    hub_match_id: str | None,
    client_organization_id: str | None,
    deal_id: str | None,
    contract_id: str | None,
    expected_days: float | str | None,
    letter_type: str | None,
    payment_days: int | None,
    payment_end_of_month: bool,
    collection_days: int | None,
) -> dict[str, Any]:
    fee_value = _decimal("fee", fee)
    assert fee_value is not None
    data = EngagementCreate(
        freelancer_id=_required_uuid("freelancer_id", freelancer_id),
        hub_match_id=_uuid("hub_match_id", hub_match_id),
        client_organization_id=_uuid("client_organization_id", client_organization_id),
        deal_id=_uuid("deal_id", deal_id),
        contract_id=_uuid("contract_id", contract_id),
        role=role,
        starts_on=required_iso_date(starts_on, "starts_on"),
        ends_on=iso_date(ends_on, "ends_on"),
        days_per_week=_decimal("days_per_week", days_per_week),
        fee=fee_value,
        fee_unit=fee_unit,
        expected_days=_decimal("expected_days", expected_days),
        letter_type=letter_type,
        payment_rule=payment_rule,
        payment_days=payment_days,
        payment_end_of_month=payment_end_of_month,
        collection_days=collection_days,
    )
    return EngagementService(context.session).create(data, context.actor).model_dump(mode="json")


def update(context: McpContext, engagement_id: str, data: dict[str, Any]) -> dict[str, Any]:
    service = EngagementService(context.session)
    identifier = _required_uuid("engagement_id", engagement_id)
    changes = EngagementUpdate(**data)
    refuse_empty(data)
    before = service.get(identifier, context.actor)
    return report(before, service.update(identifier, changes, context.actor))


def get(context: McpContext, engagement_id: str) -> dict[str, Any]:
    found = EngagementService(context.session).get(
        _required_uuid("engagement_id", engagement_id), context.actor
    )
    return found.model_dump(mode="json")


def engagement_report(
    context: McpContext, engagement_id: str, date_from: str | None, date_to: str | None
) -> dict[str, Any]:
    settings = context.settings
    if settings is None:
        raise ValidationFailed("input", "settings", "the server has no settings to read with")
    found = DaysReportService(context.session, settings, space_slug=context.space).report(
        _required_uuid("engagement_id", engagement_id),
        context.actor,
        iso_date(date_from, "date_from"),
        iso_date(date_to, "date_to"),
    )
    return found.model_dump(mode="json")


def search(
    context: McpContext,
    *,
    freelancer_id: str | None,
    state: str | None,
    client_organization_id: str | None,
    deal_id: str | None,
    contract_id: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    query = EngagementListQuery(
        freelancer_id=_uuid("freelancer_id", freelancer_id),
        state=state,
        client_organization_id=_uuid("client_organization_id", client_organization_id),
        deal_id=_uuid("deal_id", deal_id),
        contract_id=_uuid("contract_id", contract_id),
        limit=limit,
        cursor=_uuid("cursor", cursor),
    )
    return EngagementService(context.session).list(query, context.actor).model_dump(mode="json")


def _signing(context: McpContext) -> SigningService:
    """The signing service of this server's space: Documenso and the mail provider come from its
    settings, and `tenant_id` is what a new envelope's `externalId` names."""
    return SigningService(
        context.session, context.storage, context.settings, tenant_id=context.tenant_id
    )


APP_ONLY = (
    "Questo server MCP non può parlare con la firma elettronica né mandare email: non ha il "
    "token di Documenso né la chiave della posta, per tenerli separati. Fallo dall'app: lì il "
    "documento si invia, si rimanda, si annulla e si aggiorna."
)


def _signing_here(context: McpContext, *, mail: bool = True) -> SigningService:
    """The signing service, only where this server holds what signing needs. The MCP container
    holds neither the Documenso token nor the mail key (secret separation), so a tool that sends,
    resends, cancels or refreshes a document tells the person to do it from the app instead of
    answering a bare 503."""
    settings = context.settings
    if (
        settings is None
        or not signing_configured(settings)
        or (mail and not settings.resend_api_key)
    ):
        raise SigningUnavailable(APP_ONLY)
    return _signing(context)


def cancel(context: McpContext, engagement_id: str) -> dict[str, Any]:
    # The engagement service that also cancels the letter's envelope: the plain one refuses to
    # cancel a letter that is out for signature. A letter out for signature needs Documenso,
    # which this server may not reach: that refusal says to use the app.
    try:
        done = (
            _signing(context)
            .engagement_service()
            .cancel(_required_uuid("engagement_id", engagement_id), context.actor)
        )
    except SigningUnavailable as exc:
        raise SigningUnavailable(APP_ONLY) from exc
    return done.model_dump(mode="json")


def send_for_signature(context: McpContext, engagement_id: str) -> dict[str, Any]:
    sent = _signing_here(context).send_engagement(
        _required_uuid("engagement_id", engagement_id), context.actor
    )
    return sent.model_dump(mode="json")


def refresh_signature(context: McpContext, document_id: str) -> dict[str, Any]:
    found = _signing_here(context, mail=False).refresh(
        _required_uuid("document_id", document_id), context.actor
    )
    return found.model_dump(mode="json")


def resend_signing_mail(context: McpContext, document_id: str) -> dict[str, Any]:
    found = _signing_here(context).resend_mail(
        _required_uuid("document_id", document_id), context.actor
    )
    return found.model_dump(mode="json")


def cancel_framework_agreement(context: McpContext, document_id: str) -> dict[str, Any]:
    found = _signing_here(context).cancel_framework_agreement(
        _required_uuid("document_id", document_id), context.actor
    )
    return found.model_dump(mode="json")


def record_framework_notice(context: McpContext, document_id: str) -> dict[str, Any]:
    found = _signing(context).record_framework_notice(
        _required_uuid("document_id", document_id), context.actor
    )
    return found.model_dump(mode="json")


def close(context: McpContext, engagement_id: str, ended_on: str | None) -> dict[str, Any]:
    done = EngagementService(context.session).close(
        _required_uuid("engagement_id", engagement_id),
        context.actor,
        iso_date(ended_on, "ended_on"),
    )
    return done.model_dump(mode="json")


def availability(
    context: McpContext,
    *,
    freelancer_id: str | None,
    hub_freelancer_id: str | None,
    date_from: str | None,
    date_to: str | None,
) -> dict[str, Any]:
    found = FreelancerService(context.session).availability(
        context.actor,
        freelancer_id=_uuid("freelancer_id", freelancer_id),
        hub_freelancer_id=_uuid("hub_freelancer_id", hub_freelancer_id),
        date_from=iso_date(date_from, "date_from"),
        date_to=iso_date(date_to, "date_to"),
    )
    return found.model_dump(mode="json")


def set_tax_data(context: McpContext, freelancer_id: str, data: dict[str, Any]) -> dict[str, Any]:
    done = FreelancerService(context.session).set_tax_data(
        _required_uuid("freelancer_id", freelancer_id),
        FreelancerTaxData.model_validate(data),
        context.actor,
    )
    return done.model_dump(mode="json")
