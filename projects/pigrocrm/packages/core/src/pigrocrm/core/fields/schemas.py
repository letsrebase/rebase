import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pigrocrm.core.fields.types import FieldType
from pigrocrm.core.validation import SafeStr

# Closed by design: EntityType is a Literal, not an open set. The database column is
# `String(30)` with no constraint, so **the database** is open; the code is not.
# Adding an entity is an edit in exactly four places -- here, ENTITY_TYPES and
# CREATE_MODELS in schema_registry.py, and EntityType in apps/web/src/lib/schema.ts
# -- and no migration. That is R13 stated correctly; the earlier phrasing ("no
# changes required") has now been checked and disproved three times, once per slice.
EntityType = Literal[
    "customer",
    "person",
    "deal",
    "document",
    "invoice",
    "time_entry",
    "cost",
    # Slice 10. A commitment carries custom fields for the same reason everything
    # else does: whoever runs the installation knows what they need to record about
    # one, and the alternative is a `note` field holding a form.
    "attivita",
    # REB-358. A contract engagement carries custom fields for the identical
    # reason: whoever runs the installation knows what they need to record about
    # one engagement that the schema's own columns do not already carry.
    "contract",
]

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

# Mirrors the width of the `key` / `label` columns in fields/models.py. Without this,
# an over-length value sails past Pydantic, reaches `flush()`, and comes back as a raw
# sqlalchemy.exc.DataError (StringDataRightTruncation) -- which is not a subclass of
# IntegrityError, so the service's `except IntegrityError` around the commit does not
# catch it. Bounding it here turns that into an ordinary pydantic ValidationError
# before the request ever reaches the service.
KEY_MAX_LENGTH = 60
LABEL_MAX_LENGTH = 120

# `field_definitions.position` is `Integer`, unguarded by any service-level range
# check (unlike `deals.probabilita`/`pipeline_stages.probabilita_default`, which stay
# unbounded here on purpose -- see deals/schemas.py's comment on `probabilita`).
# `2**40` reaches Postgres raw as `IntegerOutOfRange` with nothing to stop it. `position`
# is a display-order index -- an entity realistically has, at most, a few dozen custom
# fields -- so 100000 is generous headroom, comfortably inside the actual `Integer`
# column's +/-2.1 billion range, while still catching the kind of wrong-by-orders-of-
# -magnitude value that is never a legitimate position and never merely a large one.
POSITION_MIN = 0
POSITION_MAX = 100_000


def slugify_key(raw: str) -> str:
    """Lossless-where-possible: an accented letter becomes its plain-ASCII base
    letter ("città" -> "citta"), not silence. NFKD decomposes each accented character
    into a base letter plus a separate combining mark; dropping only the combining
    marks keeps the letter. A character with no ASCII base at all (e.g. CJK) is still
    discarded -- there is no letter to fall back to -- and input that is only
    punctuation or only such characters slugifies to "", which the service rejects."""
    decomposed = unicodedata.normalize("NFKD", raw)
    transliterated = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SLUG_STRIP.sub("_", transliterated.strip().lower()).strip("_")


class FieldDefinitionCreate(BaseModel):
    entity_type: EntityType
    # `key` does not need `SafeStr`: `_slugify` below runs first (a `mode="before"`
    # field_validator wraps *outside* an Annotated type's own BeforeValidator,
    # verified against the installed pydantic) and `_SLUG_STRIP` already replaces
    # every character outside `[a-z0-9]` -- including a NUL byte -- with "_", so no
    # NUL byte can survive to be stored. Adding `SafeStr` here would never fire.
    key: str = Field(max_length=KEY_MAX_LENGTH)
    label: SafeStr = Field(max_length=LABEL_MAX_LENGTH)
    field_type: FieldType
    options: list[SafeStr] = []
    required: bool = False
    position: int = Field(default=0, ge=POSITION_MIN, le=POSITION_MAX)

    @field_validator("key", mode="before")
    @classmethod
    def _slugify(cls, value: str) -> str:
        # A `mode="before"` validator runs before Pydantic's own field constraints
        # (here, `max_length`), so the bound below applies to the slugified key, not
        # to whatever raw string the caller sent.
        return slugify_key(value)


class FieldDefinitionUpdate(BaseModel):
    """`field_type` is deliberately absent. Changing it with existing values has no
    correct answer, so the operation does not exist at any layer."""

    model_config = ConfigDict(extra="forbid")

    label: SafeStr | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    options: list[SafeStr] | None = None
    required: bool | None = None
    position: int | None = Field(default=None, ge=POSITION_MIN, le=POSITION_MAX)


class FieldDefinitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: EntityType
    key: str
    label: str
    field_type: FieldType
    options: list[str]
    required: bool
    position: int
    archived: bool
