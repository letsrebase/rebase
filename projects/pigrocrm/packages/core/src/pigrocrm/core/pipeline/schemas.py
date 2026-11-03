from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.validation import SafeStr

StageKind = Literal["open", "won", "lost"]

# Both mirror PipelineStage's column widths (models.py). Without these, an over-length
# value sails past Pydantic, reaches flush(), and comes back as a raw
# sqlalchemy.exc.DataError (StringDataRightTruncation) instead of an ordinary
# ValidationError -- the same class of gap fields/schemas.py's KEY_MAX_LENGTH closes.
# `nome` is the fourth time this project has hit an unbounded string column that can
# poison the session this way; `code` was closed in fix round 1, `nome` predates that
# round and is closed here.
NAME_MAX_LENGTH = 60
CODE_MAX_LENGTH = 30

# `pipeline_stages.position` is `Integer`, and -- unlike `default_probability` below
# -- nothing anywhere checks its range: `2**40` reaches Postgres raw as
# `IntegerOutOfRange`. Bounded, not left to the column's own +/-2.1 billion range, the
# same reasoning as `FieldDefinitionCreate.position` (fields/schemas.py). Symmetric
# around zero, not `ge=0` like `position`: `test_default_stage_returns_the_lowest_
# position_open_stage` legitimately creates a stage at `posizione=-1` to sit before
# the first real stage, so a negative value is a real, exercised use (inserting
# before position 0), not just a theoretical one -- ruling it out would be a
# regression this project's own test suite already catches.
POSITION_MIN = -100_000
POSITION_MAX = 100_000

# No Pydantic bound on `default_probability`, mirroring `deals.probability`'s identical
# exclusion (deals/schemas.py) for the identical reason: the service's own
# `_check_probability` already requires `0 <= probabilita_default <= 100` on every
# create and update, which structurally rejects `2**40` before it ever reaches
# `flush()`. Adding a schema bound here would not close a gap; it would just risk
# swapping `ValidationFailed` for pydantic's `ValidationError` on an in-range-looking
# caller value, exactly the regression documented on `vat_number`.


class PipelineStageCreate(BaseModel):
    nome: SafeStr = Field(max_length=NAME_MAX_LENGTH)
    position: int = Field(ge=POSITION_MIN, le=POSITION_MAX)
    default_probability: int = 0
    type: StageKind = "open"
    code: SafeStr | None = Field(default=None, max_length=CODE_MAX_LENGTH)


class PipelineStageUpdate(BaseModel):
    """`code` is deliberately absent, the same way `field_type` is absent from
    `FieldDefinitionUpdate`: it is identity, not a label, and changing it after
    creation has no correct meaning. `seed_defaults`'s idempotency across a rename
    depends on `code` never moving once set."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    position: int | None = Field(default=None, ge=POSITION_MIN, le=POSITION_MAX)
    default_probability: int | None = None
    type: StageKind | None = None


class PipelineStageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    position: int
    default_probability: int
    type: StageKind
    code: str | None
