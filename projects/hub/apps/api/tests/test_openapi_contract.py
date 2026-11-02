"""The shape of the OpenAPI document the hub web client is generated from."""

from typing import Any

import pytest

from rebase_api.main import create_app
from rebase_core.models import (
    COMPANY_STATES,
    CONTRACT_KINDS,
    CONTRACT_STATES,
    FREELANCER_STATES,
    MATCH_STATES,
    PIGRO_STATES,
    RECIPIENT_STATES,
    REFERRAL_KINDS,
    REFERRAL_STATES,
    REMOTE_OPTIONS,
    REWARD_STATES,
    TEAM_REQUEST_STATES,
)


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
        ("FreelancerRead", "stato", FREELANCER_STATES),
        ("FreelancerRead", "remoto", REMOTE_OPTIONS),
        ("CompanyRead", "stato", COMPANY_STATES),
        ("CompanyRead", "remoto", REMOTE_OPTIONS),
        ("CompanyRead", "crm_stato", PIGRO_STATES),
        ("MeRead", "remoto", REMOTE_OPTIONS),
        ("MemberRequest", "remoto", REMOTE_OPTIONS),
        ("TalentoRead", "stato", FREELANCER_STATES),
        ("TalentoRead", "remoto", REMOTE_OPTIONS),
        ("TeamMemberRead", "modalita", REMOTE_OPTIONS),
        ("CloudTalentRead", "modalita", REMOTE_OPTIONS),
        ("FreelancerCardAdminRead", "modalita", REMOTE_OPTIONS),
        ("TeamRequestRead", "stato", TEAM_REQUEST_STATES),
        ("TeamRequestListItem", "stato", TEAM_REQUEST_STATES),
        ("MatchRead", "stato", MATCH_STATES),
        ("MatchRead", "pigro_stato", PIGRO_STATES),
        ("MatchListItem", "lettera_stato", CONTRACT_STATES),
        ("MatchReport", "pigro_stato", PIGRO_STATES),
        ("ContractDocumentRead", "kind", CONTRACT_KINDS),
        ("ContractDocumentRead", "stato", CONTRACT_STATES),
        ("MemberContract", "kind", CONTRACT_KINDS),
        ("SendReport", "inviato", CONTRACT_KINDS),
        ("MemberReferralItem", "kind", REFERRAL_KINDS),
        ("ReferralLedgerItem", "kind", REFERRAL_KINDS),
        ("ReferralLedgerItem", "stato", REWARD_STATES),
        ("ReferralLedgerItem", "referral_stato", REFERRAL_STATES),
        ("RecipientRead", "stato", RECIPIENT_STATES),
    ],
)
def test_a_closed_vocabulary_is_an_enum_of_its_constant(
    schema: str, prop: str, values: tuple[str, ...]
) -> None:
    """The enum the web client types a field with is the models' own tuple, never a copy."""
    schemas = _response_schemas(create_app().openapi())

    assert _enum(schemas[schema]["properties"][prop]) == list(values)
