"""Every kind and payload key the core writes to the timeline has an Italian label (REB-901).

The web prints an event's `kind` and the keys of its payload (and the names in `changed`)
through `components/activityLabels.ts` and `payloadLabels.ts`, and a word those do not name
falls to `humanize`, which spells the English identifier: «Owner id», «Created from template».
This reads the core for what it can write and the two files for what they name, so a new
kind, key or column without a word fails here rather than on a person's screen.

What it reads from the core:

- the kind and the payload dict of every `<service>.record(entity, id, kind, actor, payload)`;
- the `_IDENTITY_FIELDS` and `_AUDITED_FIELDS` constants that build the payloads of the
  pipeline and the field definitions;
- every column of the tables whose records have a timeline, which is the superset of what a
  `changed` list can name.

A kind that is a variable (`gmail.message_sent`, the OAuth `connected_event`, the reminder
defaults) is not read; those are listed in `KIND_FALLBACK_LABELS` by hand.
"""

import ast
import importlib
import re
from pathlib import Path

import pytest

import pigrocrm.core as core_package
from pigrocrm.core import models_registry  # noqa: F401  (registers every table)
from pigrocrm.core.db.base import Base

CORE_ROOT = Path(core_package.__file__).parent
WEB = Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "components"
ACTIVITY_LABELS = WEB / "activityLabels.ts"
PAYLOAD_LABELS = WEB / "payloadLabels.ts"

# `changed` names the keys of `field_changes`, the attributes an update touches; the columns
# of these tables are what it can say. The system columns are never an edit.
TIMELINE_TABLES = (
    "organizations",
    "people",
    "deals",
    "documents",
    "invoices",
    "contracts",
    "tasks",
    "costs",
    "time_entries",
    "templates",
    "users",
    "legal_entities",
    "fiscal_profile",
    "pipeline_stages",
    "cost_categories",
    "field_definitions",
    "proposals",
    "period_locks",
    "rate_cards",
    "contract_expenses",
    "clause_notes",
    "renewal_assumptions",
    "automation_config",
    "reminder_preference",
    "invitations",
)
SYSTEM_COLUMNS = {"id", "created_at", "updated_at", "deleted_at", "archived_at", "space_id"}


def _block(source: str, name: str) -> set[str]:
    """The keys of `const <name>: Record<…> = { … }`, one per line, bare or quoted."""
    found = re.search(rf"const {name}[^=]*= \{{\n(.*?)^\}}", source, re.MULTILINE | re.DOTALL)
    assert found is not None, f"{name} is not an object literal"
    return set(re.findall(r"^\s+'?([\w.]+)'?: ", found.group(1), re.MULTILINE))


def _kind_labels() -> set[str]:
    return _block(ACTIVITY_LABELS.read_text(encoding="utf-8"), "KIND_LABELS") | _block(
        PAYLOAD_LABELS.read_text(encoding="utf-8"), "KIND_FALLBACK_LABELS"
    )


def _key_labels() -> set[str]:
    return _block(PAYLOAD_LABELS.read_text(encoding="utf-8"), "PAYLOAD_KEY_LABELS")


def _written() -> tuple[set[str], set[str]]:
    """`(kinds, payload keys)` the core's `record(...)` calls can write."""
    kinds: set[str] = set()
    keys: set[str] = set()
    for path in sorted(CORE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if ".record(" not in source:
            continue
        name = ".".join(("pigrocrm.core", *path.relative_to(CORE_ROOT).with_suffix("").parts))
        module = importlib.import_module(name.removesuffix(".__init__"))
        for node in ast.walk(ast.parse(source)):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record"
                and len(node.args) >= 3
            ):
                continue
            kind = node.args[2]
            if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
                kinds.add(kind.value)
            elif isinstance(kind, ast.Name) and kind.id.isupper():
                kinds.add(getattr(module, kind.id))
            if len(node.args) >= 5:
                for inner in ast.walk(node.args[4]):
                    if isinstance(inner, ast.Dict):
                        keys.update(
                            key.value
                            for key in inner.keys
                            if isinstance(key, ast.Constant) and isinstance(key.value, str)
                        )
        for constant in ("_IDENTITY_FIELDS", "_AUDITED_FIELDS"):
            keys.update(getattr(module, constant, ()))
    return kinds, keys


def test_the_scan_reads_the_core() -> None:
    kinds, keys = _written()
    assert {"created", "state_changed", "pat_created", "version_added"} <= kinds
    assert "legal_name" in keys
    assert len(kinds) > 60
    assert len(_kind_labels()) > 60 and len(_key_labels()) > 150


def test_every_kind_the_core_records_has_an_italian_label() -> None:
    kinds, _ = _written()
    assert sorted(kinds - _kind_labels()) == []


def test_every_payload_key_the_core_records_has_an_italian_label() -> None:
    _, keys = _written()
    assert sorted(keys - _key_labels() - {"changed"}) == []


@pytest.mark.parametrize("table", TIMELINE_TABLES)
def test_every_column_a_changed_list_can_name_has_an_italian_label(table: str) -> None:
    columns = {column.name for column in Base.metadata.tables[table].columns}
    assert sorted(columns - SYSTEM_COLUMNS - _key_labels()) == []


def test_the_owner_and_the_brand_read_as_the_forms_do() -> None:
    labels = PAYLOAD_LABELS.read_text(encoding="utf-8")
    assert "owner_id: 'Responsabile'" in labels
    assert "linkedin: 'LinkedIn'" in labels
