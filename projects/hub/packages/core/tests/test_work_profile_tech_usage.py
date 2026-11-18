"""The usage bands of a work profile (REB-1130): the intake re-derives each band from its own
counts and refuses a claim that does not match, the review view and the member area carry them,
the talent screens carry an approved profile's and never a draft's, and the talent cloud carries
none."""

import json
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD
from sqlalchemy import update
from sqlalchemy.orm import Session
from work_profile_helpers import Clock, person, sample, seen, settings, sign_in

from rebase_core.audit import utcnow
from rebase_core.cloud import CloudTalentService
from rebase_core.members import MemberService
from rebase_core.models import FreelancerCard, User, WorkProfile
from rebase_core.talents import TalentsService
from rebase_core.team_schemas import CloudTalentQuery
from rebase_core.work_profiles.groundedness import split_tech, tech_lexicon
from rebase_core.work_profiles.intake import WorkProfileRefused, WorkProfileService
from rebase_core.work_profiles.structure import structure
from rebase_core.work_profiles.tech_usage import band_for, month_span, technology_labels
from rebase_core.work_profiles.view import render_profile

# The tokens the lexicon drops from a label (`LEXICON_STOP` in the fork's groundedness.mjs).
LEXICON_STOP = {
    "event-driven",
    "jobs",
    "rich",
    "text",
    "analytics",
    "email",
    "headless",
    "cms",
    "pdf",
    "monorepo",
    "google",
    "motion",
    "hook",
    "form",
    "shell",
}


def _row(
    name: str = "Python",
    months: int = 6,
    first: str = "2026-01",
    last: str = "2026-06",
    **more: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name,
        "sessions": 12,
        "products": 3,
        "months": months,
        "firstMonth": first,
        "lastMonth": last,
        "band": band_for(months, first, last),
    }
    return row | more


def _with(*rows: dict[str, Any], timezone: str = "Europe/Rome") -> dict[str, Any]:
    profile = sample()
    profile["techUsage"] = {"timezone": timezone, "technologies": list(rows)}
    return profile


@pytest.mark.parametrize(
    ("months", "first", "last", "band"),
    [
        (1, "2026-03", "2026-03", "occasional"),
        (2, "2026-03", "2026-10", "occasional"),
        (3, "2026-03", "2026-05", "recurring"),
        (3, "2026-01", "2026-11", "recurring"),
        # Six months is the least for «continuous», and they must fill 75% of their span.
        (5, "2026-01", "2026-05", "recurring"),
        (6, "2026-01", "2026-06", "continuous"),
        (6, "2026-01", "2026-08", "continuous"),  # 6 of 8 is exactly 75%
        (6, "2026-01", "2026-09", "recurring"),  # 6 of 9 is 66%
        (9, "2026-01", "2026-12", "continuous"),  # 9 of 12 is exactly 75%
        (8, "2026-01", "2026-12", "recurring"),
        (12, "2026-01", "2026-12", "continuous"),
        # The span counts across a year's end.
        (3, "2025-11", "2026-02", "recurring"),
        (6, "2025-09", "2026-04", "continuous"),
    ],
)
def test_a_band_derives_from_the_months_and_their_span_alone(
    months: int, first: str, last: str, band: str
) -> None:
    assert band_for(months, first, last) == band


def test_the_span_is_inclusive_and_crosses_the_year() -> None:
    assert month_span("2026-03", "2026-03") == 1
    assert month_span("2025-11", "2026-02") == 4
    assert month_span("2024-01", "2026-12") == 36


def test_a_matching_block_has_nothing_to_report() -> None:
    profile = _with(
        _row(), _row("Go", 2, "2026-02", "2026-04"), _row("Shell", 4, "2026-01", "2026-04")
    )

    assert structure(profile) == []


def test_a_profile_without_the_block_is_fine() -> None:
    assert "techUsage" not in sample()
    assert structure(sample()) == []


@pytest.mark.parametrize("claimed", ["recurring", "occasional", "gold", None, 3])
def test_a_band_that_does_not_match_its_counts_is_refused(claimed: Any) -> None:
    # Six months of six derive «continuous», whatever else the row says.
    issues = structure(_with(_row(band=claimed)))

    assert len(issues) == 1
    assert "fascia" in issues[0] and "Python" in issues[0] and "«continuous»" in issues[0]


def test_every_band_is_re_derived_not_just_the_top_one() -> None:
    inflated = _with(_row("Python", 2, "2026-01", "2026-02", band="recurring"))
    deflated = _with(_row("Python", 12, "2026-01", "2026-12", band="occasional"))

    assert "fascia" in " ".join(structure(inflated))
    assert "fascia" in " ".join(structure(deflated))


@pytest.mark.parametrize(
    ("change", "sentence"),
    [
        (lambda r: r.update(name="Acme Billing"), "non è una tecnologia"),
        (lambda r: r.update(name=None), "non è una tecnologia"),
        (lambda r: r.update(name="python"), "non è una tecnologia"),  # the label, not a token
        (lambda r: r.update(firstMonth="2026-13"), "intervallo valido"),
        (lambda r: r.update(firstMonth="2026-07"), "intervallo valido"),  # after the last
        (lambda r: r.update(months=7), "non stanno fra"),  # more than the span holds
        (lambda r: r.update(months=0, band="occasional"), "non stanno fra"),
        (lambda r: r.update(months=True), "non stanno fra"),
        (lambda r: r.update(sessions=-1), "numeri interi"),
        (lambda r: r.update(products=1.5), "numeri interi"),
        (lambda r: r.update(sessions=45), "ma il profilo ne ha 44"),  # the profile has 44
        (lambda r: r.update(products=24), "ma il profilo ne ha 23"),
        (lambda r: r.update(products=13), "ma solo 12 sessioni"),
    ],
)
def test_a_row_that_cannot_be_true_is_refused(change: Any, sentence: str) -> None:
    row = _row()
    change(row)

    assert sentence in " ".join(structure(_with(row)))


def test_a_technology_listed_twice_is_refused() -> None:
    assert "più di una volta" in " ".join(structure(_with(_row(), _row())))


@pytest.mark.parametrize(
    ("block", "sentence"),
    [
        ("nope", "serve un oggetto"),
        ({"timezone": "Europe/Rome", "technologies": "nope"}, "serve una lista"),
        ({"timezone": "Mars/Olympus", "technologies": []}, "fuso orario"),
        ({"technologies": []}, "fuso orario"),
        ({"timezone": "../etc/passwd", "technologies": []}, "fuso orario"),
    ],
)
def test_a_block_that_is_not_one_is_refused(block: Any, sentence: str) -> None:
    profile = sample()
    profile["techUsage"] = block

    assert sentence in " ".join(structure(profile))


def test_one_generation_uses_one_zone() -> None:
    profile = _with(_row(), timezone="Europe/Rome")
    profile["intensity"] = {**profile["intensity"], "timezone": "America/New_York"}

    assert "un solo fuso" in " ".join(structure(profile))
    profile["intensity"]["timezone"] = "Europe/Rome"
    assert structure(profile) == []


def test_every_label_the_detector_can_emit_is_a_name_and_its_words_are_in_the_lexicon() -> None:
    labels = technology_labels()
    assert len(labels) == 52 and {"Go", "Shell", "Next.js/React", "docx/mammoth"} <= labels

    lexicon = set(tech_lexicon())
    for label in labels:
        assert structure(_with(_row(label))) == [], label
        # The two vendored files describe one vocabulary: a label the lexicon cannot tokenise is
        # a refresh that missed one of them.
        assert {t for t in split_tech(label) if t not in LEXICON_STOP} <= lexicon, label


# ---- the intake ---------------------------------------------------------------------------------


def _submit(session: Session, clock: Clock, profile: dict[str, Any], email: str) -> UUID:
    user_id, freelancer_id = person(session, email)
    assert freelancer_id is not None
    token = sign_in(session, clock, user_id)
    WorkProfileService(session, settings(), clock).submit(token, json.dumps(profile).encode())
    return freelancer_id


def test_the_intake_refuses_a_mismatched_band_and_keeps_a_matching_block(
    hub_session: Session,
) -> None:
    clock = Clock()
    user_id, freelancer_id = person(hub_session)
    bad = _with(_row(band="occasional"))
    with pytest.raises(WorkProfileRefused) as refused:
        WorkProfileService(hub_session, settings(), clock).submit(
            sign_in(hub_session, clock, user_id), json.dumps(bad).encode()
        )
    assert "fascia" in refused.value.message
    assert freelancer_id is not None
    assert hub_session.query(WorkProfile).filter_by(freelancer_id=freelancer_id).count() == 0

    good = _with(_row(), _row("Go", 1, "2026-02", "2026-02"))
    kept = _submit(hub_session, clock, good, "grace@studio.it")
    row = hub_session.query(WorkProfile).filter_by(freelancer_id=kept).one()
    assert row.payload["techUsage"] == good["techUsage"]


# ---- what reads them ----------------------------------------------------------------------------

ROWS = (
    _row("Playwright (E2E)", 2, "2026-04", "2026-05"),
    _row("Python", 6, "2026-01", "2026-06"),
    _row("TypeScript", 12, "2025-07", "2026-06"),
    _row("Zod", 3, "2026-01", "2026-03"),
    _row("Go", 6, "2026-01", "2026-06"),
)


def _admin(session: Session) -> UUID:
    user = User(email="ivan@rebase.it", first_name="Ivan", last_name="Rossi", role="admin")
    session.add(user)
    session.commit()
    return user.id


def _names(read: Any) -> list[tuple[str, str]]:
    return [(t.name, t.band) for t in read.technologies]


STRONGEST_FIRST = [
    ("TypeScript", "continuous"),
    ("Go", "continuous"),
    ("Python", "continuous"),
    ("Zod", "recurring"),
    ("Playwright (E2E)", "occasional"),
]


def test_the_review_view_lists_strongest_band_first_then_months_then_name() -> None:
    view = render_profile(_with(*ROWS))

    assert _names(view) == STRONGEST_FIRST
    first = view.technologies[0]
    assert (first.sessions, first.products, first.months) == (12, 3, 12)
    assert (first.first_month, first.last_month) == ("2025-07", "2026-06")


def test_a_profile_with_no_block_reads_no_technologies() -> None:
    assert render_profile(sample()).technologies == []


def test_a_stored_row_that_does_not_read_is_left_out_never_a_500() -> None:
    payload = _with(*ROWS, {"name": "Python"}, {"band": "gold"}, "x")  # type: ignore[arg-type]
    payload["techUsage"]["technologies"].append(_row("Vue", band="gold"))
    # Rows the intake's own rules would refuse, stored before a rule changed, are not shown.
    payload["techUsage"]["technologies"].append(_row("Acme Billing"))  # not a detector label
    payload["techUsage"]["technologies"].append(
        _row("Rust", 2, "2026-01", "2026-02", band="continuous")
    )
    payload["techUsage"]["technologies"].append(_row("Python"))  # listed twice

    assert _names(render_profile(payload)) == STRONGEST_FIRST


def _with_card(session: Session, freelancer_id: UUID) -> None:
    session.add(FreelancerCard(freelancer_id=freelancer_id, cv_sha256="0" * 64, card=dict(CARD)))
    session.commit()


def test_the_talent_screens_show_an_approved_profiles_bands_and_never_a_drafts(
    hub_session: Session,
) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    draft = _submit(hub_session, clock, _with(*ROWS), "draft@studio.it")
    approved = _submit(hub_session, clock, _with(*ROWS), "approved@studio.it")
    none = _submit(hub_session, clock, sample(), "none@studio.it")
    service = WorkProfileService(hub_session, settings(), clock)
    service.approve(approved, admin_id, None, seen(hub_session, approved))

    talents = TalentsService(hub_session)
    listed = {item.id: item for item in talents.list_recent().items}

    assert _names(listed[approved]) == STRONGEST_FIRST
    assert listed[draft].work_profile_state == "draft"
    assert listed[draft].technologies == []
    assert listed[none].technologies == []
    # The detail's own row is the list's row.
    assert _names(talents.row_for_freelancer(approved)) == STRONGEST_FIRST
    assert talents.row_for_freelancer(draft).technologies == []

    # Approved, then past its twelve months: it counts for nothing.
    hub_session.execute(
        update(WorkProfile)
        .where(WorkProfile.freelancer_id == approved)
        .values(expires_at=utcnow().replace(year=2000))
    )
    hub_session.commit()
    assert talents.row_for_freelancer(approved).technologies == []
    assert {i.id: i.technologies for i in talents.list_recent().items}[approved] == []


def test_an_approved_profile_without_the_block_reads_an_empty_list(hub_session: Session) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    freelancer_id = _submit(hub_session, clock, sample(), "ada@studio.it")
    WorkProfileService(hub_session, settings(), clock).approve(
        freelancer_id, admin_id, None, seen(hub_session, freelancer_id)
    )

    assert TalentsService(hub_session).row_for_freelancer(freelancer_id).technologies == []


def test_the_member_sees_their_own_bands_while_the_profile_counts(hub_session: Session) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    user_id, freelancer_id = person(hub_session)
    assert freelancer_id is not None
    token = sign_in(hub_session, clock, user_id)
    service = WorkProfileService(hub_session, settings(), clock)
    service.submit(token, json.dumps(_with(*ROWS)).encode())
    members = MemberService(hub_session)

    # A draft is theirs to read.
    mine = members.me_read(user_id).work_profile
    assert mine is not None and _names(mine) == STRONGEST_FIRST
    service.approve(freelancer_id, admin_id, None, seen(hub_session, freelancer_id))
    mine = members.me_read(user_id).work_profile
    assert mine is not None and _names(mine) == STRONGEST_FIRST

    # Past its twelve months: not shown.
    hub_session.execute(
        update(WorkProfile)
        .where(WorkProfile.freelancer_id == freelancer_id)
        .values(expires_at=utcnow().replace(year=2000))
    )
    hub_session.commit()
    mine = members.me_read(user_id).work_profile
    assert mine is not None and mine.expired and mine.technologies == []


def test_the_talent_cloud_never_carries_the_bands(hub_session: Session) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    user_id, freelancer_id = person(hub_session, "ada@studio.it")
    assert freelancer_id is not None
    _with_card(hub_session, freelancer_id)
    token = sign_in(hub_session, clock, user_id)
    service = WorkProfileService(hub_session, settings(), clock)
    service.submit(token, json.dumps(_with(*ROWS)).encode())
    service.approve(freelancer_id, admin_id, None, seen(hub_session, freelancer_id))

    [read] = CloudTalentService(hub_session).list(CloudTalentQuery()).items
    assert read.work_profile is not None  # approved: the summary is there

    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for value in node.values() for k in keys(value)}
        if isinstance(node, list):
            return {k for item in node for k in keys(item)}
        return set()

    # The shape is the privacy decision: the three fields the person approved, nothing else.
    assert set(read.work_profile.model_dump()) == {"summary", "domains", "project_domains"}
    # (`band` on the talent is the client's price band, REB-511, not a usage band.)
    assert not keys(read.model_dump()) & {"technologies", "months", "first_month", "last_month"}
    assert not keys(read.work_profile.model_dump()) & {"band", "technologies", "months"}
