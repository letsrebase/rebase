"""R13, written in its correct form instead of the promise that has now been
disproved three times: **the database is open** (`field_definitions.entity_type` is
`String(30)` with no constraint), **the type is extended in four places** --
`fields/schemas.py:EntityType`, `schema_registry.py:ENTITY_TYPES` and
`CREATE_MODELS`, and `apps/web/src/lib/schema.ts:EntityType` -- **and no migration
is needed.** This test is the four places, asserted.

`EXPECTED` includes `invoice`: the plan's own brief for this task listed only the
four pre-invoicing entity types plus the two new ones, but `invoice` is already a
real, shipped `EntityType` member (the invoicing slice added it) -- dropping it here
would have silently regressed `EntityType`, `ENTITY_TYPES` and `CREATE_MODELS` back
to five members instead of widening them to seven.
"""

import re
from pathlib import Path

from pigrocrm.core.documents.schemas import DocumentTipo
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.schema_registry import CREATE_MODELS, ENTITY_TYPES, native_fields

WEB_SCHEMA = Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "lib" / "schema.ts"

EXPECTED = (
    "customer",
    "person",
    "deal",
    "document",
    "invoice",
    "time_entry",
    "cost",
    # Slice 10 (2026-09-09). A commitment carries custom fields for the reason
    # everything else does: whoever runs the installation knows what they need to record
    # about one, and the alternative is a `note` field holding a form.
    "attivita",
    # REB-358. A contract engagement carries custom fields for the identical reason.
    "contract",
)


def test_entity_type_literal_covers_this_slice() -> None:
    assert set(EntityType.__args__) == set(EXPECTED)


def test_registry_agrees_with_the_literal() -> None:
    assert set(ENTITY_TYPES) == set(EXPECTED)
    assert set(CREATE_MODELS) == set(EXPECTED)


def test_native_fields_names_this_slice_columns() -> None:
    """The names A13 (Task 4A-2) has to defend. Asserted here so that renaming a
    column without revisiting the guard's own test still trips something."""
    assert {"ore", "data", "descrizione"} <= set(native_fields("time_entry"))
    assert {"importo", "data", "descrizione"} <= set(native_fields("cost"))


def test_document_tipo_gained_the_time_report() -> None:
    assert "rapporto_ore" in DocumentTipo.__args__


def test_the_frontend_literal_agrees() -> None:
    """The fifth place, and the one no Python import can reach.

    The union is read up to the blank line that ends the declaration, not to the end of
    the *line*: with eight members Prettier wraps it one member per line, and a
    single-line pattern found nothing at all -- a test that passed by asserting the
    absence of what it was looking for, until `assert match is not None` caught it. The
    non-greedy `.+?` with `re.DOTALL` stops at the first blank line, which is where a TS
    type declaration ends.
    """
    source = WEB_SCHEMA.read_text(encoding="utf-8")
    match = re.search(r"export type EntityType =(.+?)\n\n", source, re.DOTALL)
    assert match is not None, "EntityType not found in apps/web/src/lib/schema.ts"
    declared = {part.strip().strip("'") for part in match.group(1).split("|") if part.strip() != ""}
    assert declared == set(EXPECTED)
