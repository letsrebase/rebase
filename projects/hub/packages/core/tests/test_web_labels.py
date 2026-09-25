"""REB-477: the web's admin labels are copies of the core's, which the MCP tools answer,
so an admin reads the same word on «Match e contratti» as an agent does. The web cannot
import Python, so this reads `format.ts` from the repository and compares the maps key
by key. The parser is strict on purpose: each map is simple `key: 'value',` lines, and a
line of any other shape fails here rather than being skipped."""

import re
from pathlib import Path
from typing import get_args

import pytest

from rebase_core.match_words import DOCUMENT_STATE_LABELS, MATCH_STATE_LABELS, Action

REPO = Path(__file__).resolve().parents[5]
FORMAT_TS = REPO / "projects" / "hub" / "apps" / "web" / "src" / "lib" / "format.ts"
ENTRY = re.compile(r"(\w+): '([^'\\]*)',")


def _web_map(name: str) -> dict[str, str]:
    """The `export const <name>: Record<…> = { … }` map of `format.ts`, one entry per
    line."""
    source = FORMAT_TS.read_text(encoding="utf-8")
    block = re.search(
        rf"^export const {name}: Record<\w+, string> = \{{\n(.*?)^\}}$",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert block is not None, f"{name} is not a `Record<…, string>` map in {FORMAT_TS.name}"
    entries: dict[str, str] = {}
    for line in block.group(1).splitlines():
        entry = ENTRY.fullmatch(line.strip())
        assert entry is not None, f"{name}: a line that is not `key: 'value',`: {line!r}"
        key, value = entry.groups()
        assert key not in entries, f"{name}: {key} twice"
        entries[key] = value
    return entries


def _drift(name: str, web: dict[str, str], core: dict[str, str]) -> list[str]:
    return [
        f"{name}.{key}: web {web.get(key)!r}, core {core.get(key)!r}"
        for key in sorted(web.keys() | core.keys())
        if web.get(key) != core.get(key)
    ]


@pytest.mark.parametrize(
    ("name", "core"),
    [
        ("MATCH_STATE_LABELS", MATCH_STATE_LABELS),
        ("DOCUMENT_STATE_LABELS", DOCUMENT_STATE_LABELS),
    ],
)
def test_the_web_labels_a_state_as_the_core_does(name: str, core: dict[str, str]) -> None:
    drifted = _drift(name, _web_map(name), core)
    assert not drifted, "\n".join(drifted)


@pytest.mark.parametrize("name", ["ACTION_LABELS", "ACTION_PENDING_LABELS"])
def test_the_web_labels_every_action_the_core_names_and_no_other(name: str) -> None:
    web, core = set(_web_map(name)), set(get_args(Action))
    assert web == core, (
        f"{name}: only on the web {sorted(web - core)}, only in the core {sorted(core - web)}"
    )


def test_a_drifted_label_is_named_with_both_words() -> None:
    web = {**MATCH_STATE_LABELS, "bozza": "Bozza"}
    assert _drift("MATCH_STATE_LABELS", web, MATCH_STATE_LABELS) == [
        "MATCH_STATE_LABELS.bozza: web 'Bozza', core 'Da inviare'"
    ]
