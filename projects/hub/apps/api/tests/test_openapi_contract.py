"""The shape of the OpenAPI document the hub web client is generated from."""

from typing import Any

import pytest

from rebase_api.main import create_app
from rebase_core.duplicates import SIGNALS
from rebase_core.merge_steps import CV_OUTCOMES
from rebase_core.models import (
    ADMIN_ACTION_ENTITY_TYPES,
    ADMIN_ACTION_KINDS,
    CAMPAIGN_ACTIONS,
    CAMPAIGN_DESTINATIONS,
    CAMPAIGN_SOURCES,
    CARD_SENIORITIES,
    COMPANY_STATES,
    CONTACT_KINDS,
    CONTACT_LABELS,
    CONTRACT_KINDS,
    CONTRACT_STATES,
    FILLED_BY,
    FREELANCER_STATES,
    NOTE_SOURCES,
    PIGRO_STATES,
    RECIPIENT_KINDS,
    RECIPIENT_STATES,
    REFERRAL_KINDS,
    REFERRAL_STATES,
    REMOTE_OPTIONS,
    REWARD_STATES,
    TALENT_ANSWERS,
    TALENT_STATES,
    TEAM_PROPOSAL_ERRORS,
    TEAM_PROPOSAL_ORIGINS,
    TEAM_REQUEST_ORIGINS,
    TEAM_REQUEST_STATES,
    USER_ROLES,
)
from rebase_core.referral_schemas import VERIFIED_VIA


def _refs(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                found.add(value.rsplit("/", 1)[1])
            else:
                _refs(value, found)
    elif isinstance(node, list):
        for item in node:
            _refs(item, found)


def _response_schemas(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every schema a 2xx response of any route reaches, directly or through a field."""
    schemas = spec["components"]["schemas"]
    reached: set[str] = set()
    for operations in spec["paths"].values():
        for operation in operations.values():
            for code, response in operation.get("responses", {}).items():
                if code.startswith("2"):
                    _refs(response, reached)
    pending = list(reached)
    while pending:
        name = pending.pop()
        inner: set[str] = set()
        _refs(schemas[name], inner)
        pending.extend(inner - reached)
        reached |= inner
    return {name: schemas[name] for name in reached}


def test_every_property_of_a_response_schema_is_required() -> None:
    """The services build every response model whole, so a field with a default is still
    in the JSON. The generated client must not type it as possibly missing."""
    offenders = sorted(
        f"{name}.{prop}"
        for name, schema in _response_schemas(create_app().openapi()).items()
        for prop in set(schema.get("properties", {})) - set(schema.get("required", []))
    )

    assert offenders == [], f"optional in a response schema: {offenders}"


def _enum(prop: dict[str, Any]) -> list[str] | None:
    """The `enum` of a string property, whether it is bare, nullable or the items of a list."""
    for node in (prop, prop.get("items", {}), *prop.get("anyOf", [])):
        if "enum" in node:
            return list(node["enum"])
    return None


@pytest.mark.parametrize(
    ("schema", "prop", "values"),
    [
        ("FreelancerRead", "status", FREELANCER_STATES),
        ("FreelancerRead", "remote", REMOTE_OPTIONS),
        ("CompanyRead", "status", COMPANY_STATES),
        ("CompanyRead", "remote", REMOTE_OPTIONS),
        ("CompanyRead", "crm_status", PIGRO_STATES),
        ("MeRead", "remote", REMOTE_OPTIONS),
        ("MemberRequest", "remote", REMOTE_OPTIONS),
        ("TalentRead", "status", TALENT_STATES),
        ("TalentRead", "remote", REMOTE_OPTIONS),
        ("TeamMemberRead", "mode", REMOTE_OPTIONS),
        ("CloudTalentRead", "mode", REMOTE_OPTIONS),
        ("FreelancerCardAdminRead", "mode", REMOTE_OPTIONS),
        ("TeamRequestRead", "status", TEAM_REQUEST_STATES),
        ("TeamRequestListItem", "status", TEAM_REQUEST_STATES),
        ("MemberContract", "kind", CONTRACT_KINDS),
        ("MemberContract", "status", CONTRACT_STATES),
        ("MemberReferralItem", "kind", REFERRAL_KINDS),
        ("ReferralLedgerItem", "kind", REFERRAL_KINDS),
        ("ReferralLedgerItem", "status", REWARD_STATES),
        ("ReferralLedgerItem", "referral_status", REFERRAL_STATES),
        ("RecipientRead", "status", RECIPIENT_STATES),
        ("ReferralLedgerItem", "verified_via", VERIFIED_VIA),
        ("TeamProposalRead", "origin", TEAM_PROPOSAL_ORIGINS),
        ("TeamProposalListItem", "origin", TEAM_PROPOSAL_ORIGINS),
        ("TeamProposalListItem", "error", TEAM_PROPOSAL_ERRORS),
        ("TeamRequestRead", "origin", TEAM_REQUEST_ORIGINS),
        ("TeamRequestListItem", "origin", TEAM_REQUEST_ORIGINS),
        ("TeamRequestTalentRead", "answer", TALENT_ANSWERS),
        ("FreelancerRead", "filled_by", FILLED_BY),
        ("FreelancerRead", "seniority_admin", CARD_SENIORITIES),
        ("FreelancerCardAdminRead", "seniority_admin", CARD_SENIORITIES),
        ("TalentRead", "seniority", CARD_SENIORITIES),
        ("MeRead", "role", USER_ROLES),
        ("NoteRead", "source", NOTE_SOURCES),
        ("AdminActionRead", "kind", ADMIN_ACTION_KINDS),
        ("AdminActionRead", "entity_type", ADMIN_ACTION_ENTITY_TYPES),
        ("CampaignRead", "source", CAMPAIGN_SOURCES),
        ("CampaignRead", "button_meta", CAMPAIGN_DESTINATIONS),
        ("CampaignRead", "action", CAMPAIGN_ACTIONS),
        ("RecipientRead", "kind", RECIPIENT_KINDS),
        ("DuplicatePairRead", "signals", SIGNALS),
        ("MergeRead", "cv", CV_OUTCOMES),
        ("ContactPointRead", "kind", CONTACT_KINDS),
        ("ContactPointRead", "label", CONTACT_LABELS),
    ],
)
def test_a_closed_vocabulary_is_an_enum_of_its_constant(
    schema: str, prop: str, values: tuple[str, ...]
) -> None:
    """The enum the web client types a field with is the models' own tuple, never a copy."""
    schemas = _response_schemas(create_app().openapi())

    assert _enum(schemas[schema]["properties"][prop]) == list(values)
