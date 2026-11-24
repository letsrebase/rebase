"""The shape of a received work profile and of what the intake answers.

Pydantic mirrors the fields the hub reads of `candidate.json` (schema `playnew-profile/v1`).
Unknown fields are kept (`extra="allow"`): upstream adds optional ones, and its CONTRIBUTING
requires them nullable. Here only the SHAPE is checked (a string is a string); the VALUES (counts,
dates, ranges) are `structure.py`'s, the step after, so a refusal names the right step.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from rebase_core.schema_common import ResponseModel, clean_multiline
from rebase_core.vocabulary import (
    ArtifactOutcome,
    GithubScope,
    GithubTechStatus,
    WorkProfileState,
)
from rebase_core.work_profiles.view import (
    WorkProfileLanguage,
    WorkProfileView,
)

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
    language: WorkProfileLanguage | None = None
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


DECISION_NOTE_MAX_LENGTH = 2000


def _clean_note(value: str) -> str:
    return clean_multiline(value, what="la nota")


def _optional_note(value: str | None) -> str | None:
    return None if value is None or not value.strip() else _clean_note(value)


class WorkProfileApproval(BaseModel):
    """«Approva»: the note is optional and stays with the admins."""

    note: Annotated[
        str | None, Field(max_length=DECISION_NOTE_MAX_LENGTH), AfterValidator(_optional_note)
    ] = None
    # Approve although the anonymity guards stand (REB-1127): the note is then required and is
    # written into the audit entry.
    override: bool = False
    # The version the admin read (`WorkProfileAdminRead.submitted_at`): a profile sent again
    # since is another profile, and the decision is refused.
    submitted_at: datetime

    @model_validator(mode="after")
    def _override_needs_a_note(self) -> "WorkProfileApproval":
        if self.override and not self.note:
            raise ValueError("per approvare nonostante le segnalazioni serve una nota")
        return self


class WorkProfileRejection(BaseModel):
    """«Rifiuta»: the note is required, because the freelancer reads it in their area."""

    note: Annotated[
        str, Field(min_length=1, max_length=DECISION_NOTE_MAX_LENGTH), AfterValidator(_clean_note)
    ]
    submitted_at: datetime


class WorkProfileAnchor(ResponseModel):
    """A claim of the prose the recheck did not find in the data, with the field it came
    from (`where`, as `view.WorkProfileView` names them)."""

    where: str
    anchor: str
    kind: str


class WorkProfileGuardHit(ResponseModel):
    """A field a company reads that an anonymity guard stopped, and the rule: never the text."""

    field: str
    rule: str


class WorkProfileGithub(ResponseModel):
    """The GitHub account the person linked from their area (REB-1166): the login at the time
    of the link, the scope they chose and when."""

    login: str
    scope: GithubScope
    linked_at: datetime


class WorkProfileCrossCheckMonth(ResponseModel):
    """A month the profile claims, inside the 24 the snapshot read: what GitHub dated in it
    (`server_dated`: pull requests, reviews and issues) and the commits, which their author
    dated."""

    month: str
    server_dated: int
    commits: int


class WorkProfileCrossCheckTechnology(ResponseModel):
    name: str
    status: GithubTechStatus


class WorkProfileCrossCheckArtifact(ResponseModel):
    project: str
    outcome: ArtifactOutcome


class WorkProfileCrossCheck(ResponseModel):
    """The profile beside the person's verified GitHub account (REB-1169): counts and matches
    for the admin, no score, read by no decision. Everything empty and `github` null when no
    account is linked. `months_outside` counts claimed months before the snapshot's 24."""

    github: WorkProfileGithub | None
    months: list[WorkProfileCrossCheckMonth]
    months_outside: int
    with_server_activity: int
    with_commits_only: int
    without_activity: int
    technologies: list[WorkProfileCrossCheckTechnology]
    artifacts: list[WorkProfileCrossCheckArtifact]
    artifacts_checked_at: datetime | None


class WorkProfileAdminRead(ResponseModel):
    """What the admin's «Profilo di lavoro» section reads: the review state, the dates, the
    tool version, the hub's recheck (`groundedness`, `unsupported_anchors`) and the profile
    rendered from the payload. `expired` says `expires_at` has passed: nothing outside this
    page counts such a profile, and the sweep deletes it."""

    freelancer_id: UUID
    state: WorkProfileState
    submitted_at: datetime
    expires_at: datetime
    expired: bool
    tool_version: str | None
    groundedness: int
    unsupported_anchors: list[WorkProfileAnchor]
    decided_at: datetime | None
    decided_by_name: str | None
    decision_note: str | None
    # How many techUsage rows were dropped at intake because this hub did not know the name.
    unknown_technologies: int
    # The same for practices items whose id this hub did not know (REB-1144).
    unknown_practices: int
    guard_hits: list[WorkProfileGuardHit]
    profile: WorkProfileView
    # The profile beside the person's verified GitHub account (REB-1169).
    cross_check: WorkProfileCrossCheck
