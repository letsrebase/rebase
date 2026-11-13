"""REB-477: the web's admin labels are copies of the core's, which the MCP tools answer,
so an admin reads the same word on «Richieste team» as an agent does. The web cannot
import Python, so this reads `format.ts` from the repository and compares the maps key
by key. The parser is strict on purpose: each map is simple `key: 'value',` lines, and a
line of any other shape fails here rather than being skipped."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

from rebase_core.bands import BANDS
from rebase_core.models import CARD_SENIORITIES
from rebase_core.team_words import (
    CLOUD_VETTED_LABEL,
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
BANDS_TS = FORMAT_TS.with_name("bands.ts")
CAMPAIGNS_TS = FORMAT_TS.with_name("campaigns.ts")
MIGRATION = (
    REPO
    / "projects"
    / "hub"
    / "packages"
    / "core"
    / "migrations"
    / "versions"
    / "0049_english_names.py"
)
ENTRY = re.compile(r"(\w+): '([^'\\]*)',")


def _web_map(name: str, path: Path = FORMAT_TS) -> dict[str, str]:
    """The `export const <name>: Record<…> = { … }` map of `format.ts` (or of `path`), one
    entry per line."""
    source = path.read_text(encoding="utf-8")
    block = re.search(
        rf"^export const {name}: Record<\w+, string> = \{{\n(.*?)^\}}$",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert block is not None, f"{name} is not a `Record<…, string>` map in {path.name}"
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
        # REB-514: «Richieste team» and the talents' answers (P-REB-43).
        ("TEAM_REQUEST_STATE_LABELS", TEAM_REQUEST_STATE_LABELS),
        ("TEAM_ORIGIN_LABELS", TEAM_ORIGIN_LABELS),
        ("TALENT_ANSWER_LABELS", TALENT_ANSWER_LABELS),
    ],
)
def test_the_web_labels_a_state_as_the_core_does(name: str, core: dict[str, str]) -> None:
    drifted = _drift(name, _web_map(name), core)
    assert not drifted, "\n".join(drifted)


def test_the_web_says_pending_as_the_core_does() -> None:
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
        # REB-519: the badge on a vetted talent's card in the talent cloud.
        ("CLOUD_VETTED_LABEL", CLOUD_VETTED_LABEL),
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


def test_the_web_offers_the_bands_the_core_places_a_price_in() -> None:
    """REB-519: the talent cloud's band filter lists `DAY_BANDS`, a copy of the core's
    `BANDS`, one `{ min: N, max: N | null },` per line; a band the core moves or adds
    fails here rather than filtering on bounds no talent has."""
    source = BANDS_TS.read_text(encoding="utf-8")
    block = re.search(
        r"^export const DAY_BANDS: Band\[\] = \[\n(.*?)^\]$", source, re.MULTILINE | re.DOTALL
    )
    assert block is not None, f"DAY_BANDS is not a `Band[]` list in {BANDS_TS.name}"
    web: list[tuple[int, int | None]] = []
    for line in block.group(1).splitlines():
        entry = re.fullmatch(r"\{ min: (\d+), max: (\d+|null) \},", line.strip())
        assert entry is not None, f"DAY_BANDS: a line that is not a band: {line!r}"
        low, high = entry.groups()
        web.append((int(low), None if high == "null" else int(high)))
    assert tuple(web) == BANDS


def test_a_drifted_label_is_named_with_both_words() -> None:
    web = {**TEAM_REQUEST_STATE_LABELS, "closed": "Archiviata"}
    assert _drift("TEAM_REQUEST_STATE_LABELS", web, TEAM_REQUEST_STATE_LABELS) == [
        f"TEAM_REQUEST_STATE_LABELS.closed: web 'Archiviata', core "
        f"{TEAM_REQUEST_STATE_LABELS['closed']!r}"
    ]


def _migration_0049() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0049_english_names", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _renamed(columns: list[tuple[str, str]]) -> set[str]:
    """The English values migration 0049 gave the persisted codes of these columns."""
    values = _migration_0049().VALUES
    return {new for column in columns for _old, new in values[column]}


# Each web map that words a renamed code, with the persisted columns it words: a code the
# migration renamed and the map has no word for would reach the screen as the English code.
WORDED: list[tuple[str, Path, list[tuple[str, str]]]] = [
    (
        "STATE_LABELS",
        FORMAT_TS,
        [("freelancers", "status"), ("companies", "status")],
    ),
    (
        "REFERRAL_STATE_LABELS",
        FORMAT_TS,
        [("referral_rewards", "status")],
    ),
    ("REFERRAL_VERIFIED_VIA_LABELS", FORMAT_TS, [("referrals", "verified_via")]),
    ("TEAM_REQUEST_STATE_LABELS", FORMAT_TS, [("team_requests", "status")]),
    (
        "TEAM_ORIGIN_LABELS",
        FORMAT_TS,
        [("team_requests", "origin"), ("team_proposals", "origin")],
    ),
    ("TALENT_ANSWER_LABELS", FORMAT_TS, [("team_request_talents", "answer")]),
    ("CAMPAIGN_STATE_LABELS", CAMPAIGNS_TS, [("campaigns", "status")]),
    ("RECIPIENT_STATE_LABELS", CAMPAIGNS_TS, [("campaign_recipients", "status")]),
    ("ACTION_LABELS", CAMPAIGNS_TS, [("campaigns", "action")]),
]


@pytest.mark.parametrize(("name", "path", "columns"), WORDED, ids=[row[0] for row in WORDED])
def test_the_web_has_a_word_for_every_code_the_rename_gave_a_column(
    name: str, path: Path, columns: list[tuple[str, str]]
) -> None:
    missing = _renamed(columns) - set(_web_map(name, path))
    assert not missing, f"{name} has no word for {sorted(missing)}"


def test_every_renamed_value_is_either_worded_or_not_shown_by_the_web() -> None:
    """Columns of migration 0049 with a closed value set that the web words elsewhere or never
    prints, each with the reason: a new vocabulary has to be worded above or listed here."""
    not_shown = {
        ("campaign_optouts", "source"): "no page prints it",
        ("campaign_recipients", "kind"): "no page prints it",
        ("campaigns", "source"): "the wizard reads it; no page prints it",
        ("campaigns", "button_meta"): "worded by META_LABELS, which is partial by design",
        ("campaigns", "journey_status"): "the templates carry their own label",
        ("companies", "crm_status"): "the CRM link says it in a sentence (`crm_words.py`)",
        # The hub's match tables went with REB-1108 (migration 0053): their codes are the CRM's.
        ("matches", "status"): "the table is gone; the CRM words an engagement's state",
        ("matches", "pigro_status"): "the table is gone; the CRM words an engagement's state",
        ("contract_documents", "kind"): "the table is gone; the member's page words the CRM's",
        ("contract_documents", "status"): "the table is gone; the member's page words the CRM's",
        ("contract_documents", "regime"): "the table is gone; the member's page words the CRM's",
        ("freelancers", "filled_by"): "worded by FILLED_BY_LABELS and the detail's sentence",
        ("freelancers", "remote"): "worded by REMOTE_LABELS",
        ("companies", "remote"): "worded by REMOTE_LABELS",
        ("notes", "source"): "worded by NOTE_SOURCE_LABELS in lib/notes.ts",
        ("ai_tasks", "task"): "worded by TASK_LABELS in AiModels.tsx",
        ("ai_usage", "task"): "worded by TASK_LABELS in AiModels.tsx",
        ("ai_usage", "purpose"): "no page prints it",
        ("referrals", "status"): "the page compares it; the proof is worded by verified_via",
    }
    worded = {column for _name, _path, columns in WORDED for column in columns}
    values = _migration_0049().VALUES
    assert sorted(set(values) - worded - set(not_shown)) == []
    assert not worded & set(not_shown)
