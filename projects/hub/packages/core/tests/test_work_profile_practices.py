"""The practices block of a received work profile (REB-1144): the recheck of its structure, the
vendored list it is held to and the intake that refuses a bad one."""

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session
from work_profile_helpers import Clock, person, sample, settings, sign_in

from rebase_core.card_guard import KnownIdentifiers
from rebase_core.members import MemberService
from rebase_core.models import WorkProfile
from rebase_core.team_schemas import CloudTalentRead, CloudWorkProfile
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
from rebase_core.work_profiles.source import source_text
from rebase_core.work_profiles.structure import structure
from rebase_core.work_profiles.view import practice_rows, practices, render_profile


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
        ({"seen": 26}, "seen è 26"),  # of 25
        ({"seen": -1}, "numeri interi"),
        ({"seen": True}, "numeri interi"),
        ({"seen": 1.5}, "numeri interi"),
        ({"of": 101, "seen": 25}, "of è 101"),  # volume.sessions 100
        ({"seen": 31, "of": 40, "products": 31}, "31 prodotti ma il profilo"),  # volume.products 30
        ({"products": 19}, "19 prodotti ma visto"),  # seen 18
        ({"seen": 4, "products": 1, "months": 5}, "5 mesi ma visto"),  # window spans 12
        ({"months": 0}, "mesi sono 0"),
        ({"months": 14, "seen": 20}, "finestra"),  # window spans 12
        ({"method": "model"}, "metodo"),
    ],
)
def test_each_rule_alone_names_the_practice_and_the_rule_in_italian(
    change: dict[str, Any], rule: str
) -> None:
    profile = _profile(_item("checks_before_commit", **change))
    profile["window"] = {"from": "2026-01", "to": "2026-12"}
    found = issues(profile)
    assert len(found) == 1, found
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
    profile = _profile(_item("checks_before_commit", seen=18, months=5))
    profile["window"] = {"from": "2026-01", "to": "2026-03"}
    found = issues(profile)
    assert len(found) == 1
    assert "mesi" in found[0]
    assert "3" in found[0]


def test_months_may_exceed_the_utc_window_by_one_for_the_profile_zone() -> None:
    # Rome 2026-01-01T00:30Z and 2026-01-31T23:30Z fall in two Rome months, one UTC month.
    profile = _profile(_item("checks_before_commit", seen=18, months=2), timezone="Europe/Rome")
    profile["window"] = {"from": "2026-01", "to": "2026-01"}
    assert issues(profile) == []
    profile = _profile(_item("checks_before_commit", seen=18, months=3), timezone="Europe/Rome")
    profile["window"] = {"from": "2026-01", "to": "2026-01"}
    assert len(issues(profile)) == 1


def test_a_model_practice_is_capped_at_60(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(practices_module, "practice_methods", lambda: {"read_by_model": "model"})
    sound = _item("read_by_model", method="model", seen=40, of=60, products=3, months=4)
    assert issues(_profile(sound)) == []
    found = issues(_profile(sound | {"of": 61}))
    assert len(found) == 1
    assert "60" in found[0]


def test_the_method_is_the_one_the_list_gives_in_both_directions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(practices_module, "practice_methods", lambda: {"read_by_model": "model"})
    found = issues(_profile(_item("read_by_model", method="code")))
    assert len(found) == 1
    assert "metodo" in found[0]
    assert "model" in found[0]


@pytest.mark.parametrize(
    "bad",
    [
        None,
        3,
        "checks_before_commit",
        [],
        {"id": None},
        {"id": ""},
        {"id": 7},
        {"id": []},
        {"id": {}},
    ],
)
def test_a_malformed_item_is_refused_by_position(bad: Any) -> None:
    found = issues(_profile(_item("checks_before_commit"), bad))
    assert len(found) == 1
    assert "practices.items[2]" in found[0]


def test_drop_unknown_survives_hostile_items() -> None:
    profile = _profile(_item("checks_before_commit"), {"id": []}, {"id": {}}, None, 3, {"id": 7})
    assert drop_unknown(profile) == 5
    assert [i["id"] for i in profile["practices"]["items"]] == ["checks_before_commit"]


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


def _submit(session: Session, clock: Clock, profile: dict[str, Any], email: str) -> Any:
    user_id, freelancer_id = person(session, email)
    assert freelancer_id is not None
    WorkProfileService(session, settings(), clock).submit(
        sign_in(session, clock, user_id), json.dumps(profile).encode()
    )
    return freelancer_id


def test_a_profile_with_an_unknown_practice_is_accepted_and_the_id_is_never_stored(
    hub_session: Session,
) -> None:
    clock = Clock()
    profile = _profile(_item("checks_before_commit"), _item("acme_secret"))
    freelancer_id = _submit(hub_session, clock, profile, "ada@studio.it")

    row = hub_session.query(WorkProfile).filter_by(freelancer_id=freelancer_id).one()
    assert [i["id"] for i in row.payload["practices"]["items"]] == ["checks_before_commit"]
    assert "acme_secret" not in json.dumps(row.payload)
    assert row.unknown_practices == 1
    read = WorkProfileService(hub_session, settings(), clock).admin_read(freelancer_id)
    assert read.unknown_practices == 1


def test_a_resubmission_without_unknowns_resets_the_count(hub_session: Session) -> None:
    clock = Clock()
    user_id, freelancer_id = person(hub_session, "grace@studio.it")
    service = WorkProfileService(hub_session, settings(), clock)
    first = _profile(_item("checks_before_commit"), _item("acme_secret"))
    service.submit(sign_in(hub_session, clock, user_id), json.dumps(first).encode())
    second = _profile(_item("checks_before_commit"))
    service.submit(sign_in(hub_session, clock, user_id), json.dumps(second).encode())

    row = hub_session.query(WorkProfile).filter_by(freelancer_id=freelancer_id).one()
    assert row.unknown_practices == 0


def test_rows_follow_the_list_order_not_the_counts() -> None:
    rows = practice_rows(
        [
            _item("security_checks", seen=20, of=20),
            _item("checks_before_commit", seen=1, of=5, products=1, months=1),
        ]
    )
    assert [r.id for r in rows] == ["checks_before_commit", "security_checks"]
    assert rows[0].label == "Verifica prima del commit" and rows[0].method == "code"
    assert (rows[0].seen, rows[0].of, rows[0].products, rows[0].months) == (1, 5, 1, 1)


def test_a_refused_row_is_left_out_and_a_repeated_id_keeps_its_first_valid_row() -> None:
    assert (
        practice_rows([_item("checks_before_commit", seen=9, of=5), _item("nope"), "x", None]) == []
    )
    assert practices({"practices": "x"}) == [] and practices({}) == []
    assert practices({"practices": None}) == []
    assert practices({"practices": {"items": "x"}}) == []
    twice = practice_rows([_item("checks_before_commit", seen=5), _item("checks_before_commit")])
    assert [(r.id, r.seen) for r in twice] == [("checks_before_commit", 5)]


def _submit_two(session: Session, clock: Clock, email: str) -> tuple[Any, Any]:
    user_id, freelancer_id = person(session, email)
    assert freelancer_id is not None
    token = sign_in(session, clock, user_id)
    payload = _profile(_item("security_checks"), _item("checks_before_commit"))
    WorkProfileService(session, settings(), clock).submit(token, json.dumps(payload).encode())
    return user_id, freelancer_id


def test_admin_read_and_member_read_carry_the_rows(hub_session: Session) -> None:
    clock = Clock()
    user_id, freelancer_id = _submit_two(hub_session, clock, "ada@studio.it")
    expected = ["checks_before_commit", "security_checks"]

    admin = WorkProfileService(hub_session, settings(), clock).admin_read(freelancer_id)
    assert [r.id for r in admin.profile.practices] == expected
    member = MemberService(hub_session).me_read(user_id).work_profile
    assert member is not None and [r.id for r in member.practices] == expected


def test_member_read_has_no_rows_when_expired_or_rejected(hub_session: Session) -> None:
    clock = Clock()
    user_id, freelancer_id = _submit_two(hub_session, clock, "ada@studio.it")
    row = hub_session.query(WorkProfile).filter_by(freelancer_id=freelancer_id).one()

    row.expires_at = row.submitted_at - timedelta(days=1)
    hub_session.commit()
    member = MemberService(hub_session).me_read(user_id).work_profile
    assert member is not None and member.expired and member.practices == []

    row.expires_at = row.submitted_at + timedelta(days=100)
    row.state = "rejected"
    hub_session.commit()
    member = MemberService(hub_session).me_read(user_id).work_profile
    assert member is not None and member.practices == []


def test_practices_are_nowhere_a_company_or_the_card_reads() -> None:
    assert "practices" not in CloudTalentRead.model_fields
    assert "practices" not in CloudWorkProfile.model_fields
    with_block = _profile(_item("checks_before_commit"))
    plain = {k: v for k, v in with_block.items() if k != "practices"}
    known = KnownIdentifiers.empty("Rossi")
    assert source_text(with_block, known) == source_text(plain, known)
    assert render_profile(with_block).practices and not render_profile(plain).practices


NEVER_READ_PRACTICES = (
    "talents.py",
    "talent_schemas.py",
    "talent_availability.py",
    "team_schemas.py",
    "cloud.py",
    "hub_facts.py",
    "card_prompt.py",
    "card_draft.py",
    "work_profiles/source.py",
)


def _offending(root: Path, names: tuple[str, ...]) -> list[str]:
    return [n for n in names if "practices" in (root / n).read_text(encoding="utf-8")]


def test_no_query_orders_or_filters_by_a_practice(tmp_path: Path) -> None:
    root = Path(practices_module.__file__).parents[1]
    # The word is absent from every module that builds the talent list, the cloud, the card's
    # source or the narrative input, so no `ORDER BY`/`WHERE` (one line, several, or raw SQL
    # such as `payload -> 'practices'`) can read a practice there.
    assert _offending(root, NEVER_READ_PRACTICES) == []
    # The intake only drops what the list does not know and imports the module.
    lines = [
        line.strip()
        for line in (root / "work_profiles" / "intake.py").read_text(encoding="utf-8").splitlines()
        if re.search(r"\bpractices\b", line)
    ]
    assert lines == [
        "from rebase_core.work_profiles import practices",
        "unknown_practices = practices.drop_unknown(stored)",
    ]
    # The check itself catches a multi-line `.where(` and raw SQL.
    (tmp_path / "a.py").write_text("select(X).where(\n    X.payload['practices'] > 1\n)\n")
    (tmp_path / "b.py").write_text("text(\"payload -> 'practices' -> 'items'\")\n")
    assert _offending(tmp_path, ("a.py", "b.py")) == ["a.py", "b.py"]


def test_a_model_row_has_its_shape_in_the_view(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(practices_module, "practice_methods", lambda: {"read_by_model": "model"})
    monkeypatch.setattr(practices_module, "PRACTICE_LABELS", {"read_by_model": "Letta dal modello"})
    [row] = practice_rows([_item("read_by_model", method="model", seen=10, of=40)])
    assert row.model_dump() == {
        "id": "read_by_model",
        "label": "Letta dal modello",
        "method": "model",
        "seen": 10,
        "of": 40,
        "products": 3,
        "months": 4,
    }
