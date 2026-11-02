"""The closed sets of values a response model states, taken from the constants of the
models package, never copied.

A field typed with one of these is still a plain `str` for Python and for validation: a
service builds the model from a column, and a value outside the set (a row from before a
`CHECK`, an `ADMIN_ACTION_KINDS` entry another change adds) must reach the page as the
text it is, not turn into a 500 on the response. What the alias adds is the `enum` in the
OpenAPI schema, which is what the generated web client reads to type the field as a union
of its values. The enum is read when the schema is built, so a value added to the
constant is in the schema without touching this module.

`Literal[*CONSTANT]` is not an option: mypy rejects it, and spelling the values out again
would copy the tuples this module exists not to copy.
"""

from collections.abc import Callable, Sequence
from typing import Annotated, Any

from pydantic import Field

from rebase_core.models import (
    ADMIN_ACTION_ENTITY_TYPES,
    ADMIN_ACTION_KINDS,
    CAMPAIGN_ACTIONS,
    CAMPAIGN_DESTINATIONS,
    CAMPAIGN_FONTI,
    CAMPAIGN_STATES,
    CARD_SENIORITIES,
    COMPANY_STATES,
    COMPILATA_DA,
    CONTRACT_KINDS,
    CONTRACT_STATES,
    FREELANCER_STATES,
    MATCH_STATES,
    NOTE_SOURCES,
    PIGRO_STATES,
    RECIPIENT_KINDS,
    RECIPIENT_STATES,
    REFERRAL_KINDS,
    REFERRAL_STATES,
    REMOTE_OPTIONS,
    REWARD_STATES,
    TALENT_ANSWERS,
    TEAM_PROPOSAL_ERRORS,
    TEAM_PROPOSAL_ORIGINS,
    TEAM_REQUEST_ORIGINS,
    TEAM_REQUEST_STATES,
    USER_ROLES,
)


def enum_of(values: Sequence[str]) -> Callable[[dict[str, Any]], None]:
    """The `json_schema_extra` that adds `enum: values` to a string field's schema."""

    def extend(schema: dict[str, Any]) -> None:
        schema["enum"] = list(values)

    return extend


RemoteOption = Annotated[str, Field(json_schema_extra=enum_of(REMOTE_OPTIONS))]
FreelancerStatus = Annotated[str, Field(json_schema_extra=enum_of(FREELANCER_STATES))]
# A row of the Talenti list: a card's own state, or `lead` for a bare sign-up.
TalentoStatus = Annotated[str, Field(json_schema_extra=enum_of((*FREELANCER_STATES, "lead")))]
CompanyStatus = Annotated[str, Field(json_schema_extra=enum_of(COMPANY_STATES))]
# What a person or a company a member referred has reached: a freelancer's or a company's.
ReferredStatus = Annotated[
    str,
    Field(json_schema_extra=enum_of(tuple(dict.fromkeys((*FREELANCER_STATES, *COMPANY_STATES))))),
]
# The CRM link of a match and of a company request share one set (`models.profiles`).
PigroState = Annotated[str, Field(json_schema_extra=enum_of(PIGRO_STATES))]
MatchState = Annotated[str, Field(json_schema_extra=enum_of(MATCH_STATES))]
ContractKind = Annotated[str, Field(json_schema_extra=enum_of(CONTRACT_KINDS))]
ContractState = Annotated[str, Field(json_schema_extra=enum_of(CONTRACT_STATES))]
ReferralKind = Annotated[str, Field(json_schema_extra=enum_of(REFERRAL_KINDS))]
ReferralState = Annotated[str, Field(json_schema_extra=enum_of(REFERRAL_STATES))]
RewardState = Annotated[str, Field(json_schema_extra=enum_of(REWARD_STATES))]
TeamRequestState = Annotated[str, Field(json_schema_extra=enum_of(TEAM_REQUEST_STATES))]
CampaignState = Annotated[str, Field(json_schema_extra=enum_of(CAMPAIGN_STATES))]
RecipientState = Annotated[str, Field(json_schema_extra=enum_of(RECIPIENT_STATES))]
TeamRequestOrigin = Annotated[str, Field(json_schema_extra=enum_of(TEAM_REQUEST_ORIGINS))]
TeamProposalOrigin = Annotated[str, Field(json_schema_extra=enum_of(TEAM_PROPOSAL_ORIGINS))]
TeamProposalError = Annotated[str, Field(json_schema_extra=enum_of(TEAM_PROPOSAL_ERRORS))]
TalentAnswer = Annotated[str, Field(json_schema_extra=enum_of(TALENT_ANSWERS))]
Seniority = Annotated[str, Field(json_schema_extra=enum_of(CARD_SENIORITIES))]
CompilataDa = Annotated[str, Field(json_schema_extra=enum_of(COMPILATA_DA))]
UserRole = Annotated[str, Field(json_schema_extra=enum_of(USER_ROLES))]
NoteSource = Annotated[str, Field(json_schema_extra=enum_of(NOTE_SOURCES))]
AdminActionKind = Annotated[str, Field(json_schema_extra=enum_of(ADMIN_ACTION_KINDS))]
AdminActionEntity = Annotated[str, Field(json_schema_extra=enum_of(ADMIN_ACTION_ENTITY_TYPES))]
CampaignFonte = Annotated[str, Field(json_schema_extra=enum_of(CAMPAIGN_FONTI))]
CampaignAction = Annotated[str, Field(json_schema_extra=enum_of(CAMPAIGN_ACTIONS))]
CampaignDestination = Annotated[str, Field(json_schema_extra=enum_of(CAMPAIGN_DESTINATIONS))]
RecipientKind = Annotated[str, Field(json_schema_extra=enum_of(RECIPIENT_KINDS))]
