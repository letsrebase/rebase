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
from rebase_core.models import CARD_SENIORITIES
from rebase_core.team_words import (
    TALENT_ANSWER_LABELS,
    TALENT_WAITING_LABEL,
    TEAM_BUILDER_ORIGIN,
    TEAM_BUILDER_ORIGIN_LABEL,
    TEAM_ORIGIN_LABELS,
    TEAM_REQUEST_STATE_LABELS,
    VETTED_LABEL,
)

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
        # REB-514: «Richieste team» and the talents' answers (P-REB-43).
        ("TEAM_REQUEST_STATE_LABELS", TEAM_REQUEST_STATE_LABELS),
        ("TEAM_ORIGIN_LABELS", TEAM_ORIGIN_LABELS),
        ("TALENT_ANSWER_LABELS", TALENT_ANSWER_LABELS),
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


def test_the_web_says_in_attesa_as_the_core_does() -> None:
    """REB-517: the word for a talent mailed and silent is one string, not a map."""
    source = FORMAT_TS.read_text(encoding="utf-8")
    found = re.search(r"^export const TALENT_WAITING_LABEL = '([^'\\]*)'$", source, re.MULTILINE)
    assert found is not None, f"TALENT_WAITING_LABEL is not a string constant in {FORMAT_TS.name}"
    assert found.group(1) == TALENT_WAITING_LABEL


@pytest.mark.parametrize(
    ("name", "core"),
    [
        # REB-518: the talent's «Verificato» pill, and a company request that came from
        # the team builder's beta box (`?da=team-builder`), with the word its row shows.
        ("VETTED_LABEL", VETTED_LABEL),
        ("TEAM_BUILDER_ORIGIN", TEAM_BUILDER_ORIGIN),
        ("TEAM_BUILDER_ORIGIN_LABEL", TEAM_BUILDER_ORIGIN_LABEL),
    ],
)
def test_the_web_says_one_word_as_the_core_does(name: str, core: str) -> None:
    source = FORMAT_TS.read_text(encoding="utf-8")
    found = re.search(rf"^export const {name} = '([^'\\]*)'$", source, re.MULTILINE)
    assert found is not None, f"{name} is not a string constant in {FORMAT_TS.name}"
    assert found.group(1) == core


def test_the_web_names_every_seniority_a_card_can_carry_and_no_other() -> None:
    """REB-514: the web words a card's `seniority`; the core keeps the values, not the
    words, since only a page says them."""
    web, core = set(_web_map("SENIORITY_LABELS")), set(CARD_SENIORITIES)
    assert web == core, (
        f"SENIORITY_LABELS: only on the web {sorted(web - core)}, "
        f"only in the core {sorted(core - web)}"
    )


def test_a_drifted_label_is_named_with_both_words() -> None:
    web = {**MATCH_STATE_LABELS, "bozza": "Bozza"}
    assert _drift("MATCH_STATE_LABELS", web, MATCH_STATE_LABELS) == [
        "MATCH_STATE_LABELS.bozza: web 'Bozza', core 'Da inviare'"
    ]
