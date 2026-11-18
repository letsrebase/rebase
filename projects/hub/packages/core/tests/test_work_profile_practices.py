"""The practices block of a received work profile (REB-1144): the recheck of its structure, the
vendored list it is held to and the intake that refuses a bad one."""

import json
from typing import Any

import pytest
from sqlalchemy.orm import Session
from work_profile_helpers import Clock, person, sample, settings, sign_in

from rebase_core.models import WorkProfile
from rebase_core.work_profiles import practices as practices_module
from rebase_core.work_profiles.intake import WorkProfileRefused, WorkProfileService
from rebase_core.work_profiles.practices import (
    PRACTICE_LABELS,
    RUBRIC,
    drop_unknown,
    issues,
    practice_methods,
    reads_true,
)
from rebase_core.work_profiles.structure import structure


def _item(ident: str, **more: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": ident,
        "method": "code",
        "seen": 18,
        "of": 25,
        "products": 3,
        "months": 4,
    }
    return item | more


def _profile(*items: dict[str, Any], **block: Any) -> dict[str, Any]:
    profile = sample()
    profile["window"] = {"from": "2026-01", "to": "2026-05"}
    profile["volume"] = profile["volume"] | {"products": 30, "sessions": 100}
    profile["practices"] = {
        "rubric": RUBRIC,
        "timezone": "Europe/Rome",
        "items": list(items),
    } | block
    return profile


def test_a_sound_block_has_no_issue() -> None:
    assert issues(_profile(_item("checks_before_commit"))) == []


def test_absent_or_null_block_is_no_issue() -> None:
    assert issues({}) == []
    assert issues({"practices": None}) == []


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"seen": 26}, "seen"),
        ({"seen": -1}, "seen"),
        ({"seen": True}, "seen"),
        ({"seen": 1.5}, "seen"),
        ({"of": 237}, "of"),
        ({"products": 31}, "prodotti"),
        ({"products": 19}, "prodotti"),
        ({"months": 19}, "mesi"),
        ({"months": 0}, "mesi"),
        ({"months": 6}, "mesi"),
        ({"method": "model"}, "metodo"),
    ],
)
def test_each_rule_names_the_practice_and_the_rule_in_italian(
    change: dict[str, Any], rule: str
) -> None:
    item = _item("checks_before_commit", seen=18, of=25, products=18, months=4) | change
    found = issues(_profile(item))
    assert found
    assert "checks_before_commit" in found[0]
    assert rule in found[0]


@pytest.mark.parametrize(
    ("block", "rule"),
    [
        ({"rubric": "x/v1"}, "rubric"),
        ({"timezone": "Not/AZone"}, "fuso"),
        ({"timezone": "UTC"}, "intensity.timezone"),
        ({"items": {}}, "lista"),
    ],
)
def test_the_block_itself_is_held_to_the_rubric_the_zone_and_a_list(
    block: dict[str, Any], rule: str
) -> None:
    profile = _profile(_item("checks_before_commit"), **block)
    profile["intensity"] = (profile.get("intensity") or {}) | {"timezone": "Europe/Rome"}
    found = issues(profile)
    assert len(found) == 1
    assert rule in found[0]


def test_a_block_that_is_not_an_object_is_refused() -> None:
    assert issues({"practices": []}) == ["practices: serve un oggetto"]


def test_an_id_twice_is_an_issue() -> None:
    found = issues(_profile(_item("checks_before_commit"), _item("checks_before_commit")))
    assert found == ["practices.checks_before_commit: compare più di una volta"]


def test_months_cannot_exceed_the_windows_span() -> None:
    profile = _profile(_item("checks_before_commit", seen=18, months=4))
    profile["window"] = {"from": "2026-01", "to": "2026-03"}
    found = issues(profile)
    assert len(found) == 1
    assert "mesi" in found[0]
    assert "3" in found[0]


def test_a_model_practice_is_capped_at_60(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(practices_module, "practice_methods", lambda: {"read_by_model": "model"})
    sound = _item("read_by_model", method="model", seen=40, of=60, products=3, months=4)
    assert issues(_profile(sound)) == []
    found = issues(_profile(sound | {"of": 61}))
    assert len(found) == 1
    assert "60" in found[0]


def test_an_unknown_id_is_no_issue_and_drop_unknown_removes_it_and_counts() -> None:
    profile = _profile(
        _item("checks_before_commit"), _item("acme_secret"), _item("Tests_With_Changes")
    )
    assert issues(profile) == []
    assert drop_unknown(profile) == 2
    assert [i["id"] for i in profile["practices"]["items"]] == ["checks_before_commit"]
    assert drop_unknown(profile) == 0
    assert drop_unknown({}) == 0
    assert drop_unknown({"practices": {"items": 3}}) == 0


def test_a_stored_item_reads_true_only_when_the_rules_accept_it() -> None:
    assert reads_true(_item("checks_before_commit"))
    assert not reads_true(_item("acme_secret"))
    assert not reads_true(_item("checks_before_commit", seen=26))
    assert not reads_true(_item("checks_before_commit", method="model"))
    assert not reads_true("checks_before_commit")


def test_vendored_file_matches_the_fork_list_and_every_id_has_an_italian_label() -> None:
    assert set(practice_methods()) == set(PRACTICE_LABELS)
    assert set(practice_methods().values()) <= {"code", "model"}


def test_structure_refuses_a_bad_block(hub_session: Session) -> None:
    assert structure(_profile(_item("checks_before_commit"))) == []
    clock = Clock()
    user_id, freelancer_id = person(hub_session)
    bad = _profile(_item("checks_before_commit", seen=26))
    with pytest.raises(WorkProfileRefused) as refused:
        WorkProfileService(hub_session, settings(), clock).submit(
            sign_in(hub_session, clock, user_id), json.dumps(bad).encode()
        )
    assert "checks_before_commit" in refused.value.message
    assert "seen" in refused.value.message
    assert hub_session.query(WorkProfile).filter_by(freelancer_id=freelancer_id).count() == 0
