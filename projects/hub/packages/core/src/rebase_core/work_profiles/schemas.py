"""The shape of a received work profile and of what the intake answers.

Pydantic mirrors the fields the hub reads of `candidate.json` (schema `playnew-profile/v1`).
Unknown fields are kept (`extra="allow"`): upstream adds optional ones, and its CONTRIBUTING
requires them nullable. Here only the SHAPE is checked (a string is a string); the VALUES (counts,
dates, ranges) are `structure.py`'s, the step after, so a refusal names the right step.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.schema_common import ResponseModel
from rebase_core.vocabulary import WorkProfileState

SCHEMA_ID = "playnew-profile/v1"


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Span(_Open):
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class Landing(_Open):
    commits: Any = None
    reverts: Any = None


class Artifact(_Open):
    type: str | None = None
    url: str | None = None
    label: str | None = None


class Volume(_Open):
    products: Any
    sessions: Any
    instructions: Any = None


class Domain(_Open):
    label: str | None = None
    products: Any = None
    sessions: Any = None
    note: str | None = None


class Project(_Open):
    id: str
    sessions: Any = None
    span: Span | None = None
    domain: str | None = None
    did: str | None = None
    whyRepresentative: str | None = None  # noqa: N815  (upstream's field name)
    tech: list[str] | None = None
    type: list[str] | None = None
    landing: Landing | None = None
    artifact: Artifact | None = None


class OtherProject(_Open):
    sessions: Any = None
    span: Span | None = None


class CandidateProfile(_Open):
    schema_id: Literal["playnew-profile/v1"] = Field(alias="schema")
    generatedAt: str | None = None  # noqa: N815
    contact: dict[str, Any] | None = None
    window: Span
    volume: Volume
    summary: str | None = None
    domains: list[Domain] | None = None
    projects: list[Project]
    otherProjects: list[OtherProject] | None = None  # noqa: N815


class WorkProfileAccepted(ResponseModel):
    """The 201 of the intake: where the profile stands and where its owner can see it."""

    state: WorkProfileState
    review_url: str
