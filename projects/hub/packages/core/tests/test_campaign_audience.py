"""A campaign's list: templates, candidates, exclusions, the action already done."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from campaign_fixtures import (  # noqa: F401  (fixture)
    SETTINGS,
    T0,
    Clock,
    admin,
    as_admin,
    campaign_row,
    clean,
    company,
    lead,
    person,
)
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

import rebase_core.campaigns.audience as audience_module
from rebase_core.campaigns.actions import CV_NOTE_PREFIX, _card, done_at, done_at_many, snapshot
from rebase_core.campaigns.audience import (
    REASON_ADMIN,
    REASON_BOUNCED,
    REASON_DISCARDED,
    REASON_NEVER,
    build_audience,
    candidates,
    exclusions,
    waiting_rows,
)
from rebase_core.campaigns.schemas import ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.states import (
    JOURNEY_STATES,
    PHASE_ONE_STATES,
    Candidate,
    candidates_for_state,
)
from rebase_core.campaigns.templates import STATE_TEMPLATES
from rebase_core.models import (
    CAMPAIGN_ACTIONS,
    CAMPAIGN_DESTINATIONS,
    CampaignOptout,
    CampaignRecipient,
    Company,
    Login,
    Note,
    User,
)
from rebase_core.notes import NoteService


def test_every_phase_one_state_has_a_template_that_fits_the_columns() -> None:
    assert set(STATE_TEMPLATES) == set(PHASE_ONE_STATES)
    for key, template in STATE_TEMPLATES.items():
        assert template.label == JOURNEY_STATES[key]
        assert template.action in CAMPAIGN_ACTIONS and template.action != "pigro_customer"
        assert template.button_meta in CAMPAIGN_DESTINATIONS and template.button_meta != "pigro"
        assert template.text.startswith("Ciao {nome},")
        assert len(template.subject) <= 200 and len(template.button_text) <= 60


def recipient_for(session: Session, candidate, before: dict) -> CampaignRecipient:  # type: ignore[no-untyped-def]
    return CampaignRecipient(
        email=candidate.email,
        kind=candidate.kind,
        user_id=candidate.user_id,
        freelancer_id=candidate.freelancer_id,
        signup_id=candidate.signup_id,
        code="00000000",
        before=before,
        unsubscribe_token="t",
    )


def test_a_cv_uploaded_after_the_snapshot_counts_and_one_from_before_does_not(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    card = person(clean, "nocv@studio.it", cv=False)
    candidate = candidates_for_state(clean, "missing_cv")[0]
    before = snapshot(clean, candidate, T0)
    assert before["ha_cv"] is False and before["has_card"] is True
    row = recipient_for(clean, candidate, before)
    assert done_at(clean, row, "cv") is None
    card.cv_size, card.cv_filename, card.cv_bytes = 4, "cv.pdf", b"%PDF"
    clean.commit()
    NoteService(clean).add(
        "freelancer", card.id, "CV caricato dalla persona", author_id=card.user_id, source="person"
    )
    assert done_at(clean, row, "cv") is not None


def test_a_login_after_the_snapshot_is_entered(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "done@studio.it")
    candidate = candidates_for_state(clean, "complete")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "signed_in") is None
    clean.add(Login(user_id=candidate.user_id, logged_at=T0 + timedelta(minutes=5)))
    clean.commit()
    assert done_at(clean, row, "signed_in") == T0 + timedelta(minutes=5)


def test_a_lead_who_makes_a_card_has_created_a_profile(clean: Session) -> None:  # noqa: F811  (fixture)
    lead(clean, "giulia@studio.it")
    candidate = candidates_for_state(clean, "lead")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0 - timedelta(days=1)))
    assert done_at(clean, row, "profile_created") is None
    person(clean, "giulia@studio.it", nome="Giulia")
    assert done_at(clean, row, "profile_created") is not None


def test_any_of_a_referentes_open_requests_updated_counts(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    first = company(clean, "info@block-buy.it")
    company(clean, "info@block-buy.it", status="contacted")
    candidate = candidates_for_state(clean, "azienda_aperta")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, datetime.now(UTC)))
    assert done_at(clean, row, "request_updated") is None
    first.duration = "18 mesi"
    clean.commit()
    assert done_at(clean, row, "request_updated") is not None


def test_the_pigro_action_is_never_done_in_phase_one(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "done@studio.it")
    candidate = candidates_for_state(clean, "complete")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "pigro_customer") is None


def test_an_address_with_no_card_never_has_done_cv_or_card_complete(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    lead(clean, "nocard@studio.it")
    candidate = candidates_for_state(clean, "lead")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "cv") is None
    assert done_at(clean, row, "card_complete") is None


def test_a_card_soft_deleted_after_completing_counts_as_not_done(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    card = person(clean, "gone@studio.it", cv=False)
    candidate = candidates_for_state(clean, "missing_cv")[0]
    before = snapshot(clean, candidate, T0)
    assert before["ha_cv"] is False and before["complete"] is False
    row = recipient_for(clean, candidate, before)
    card.cv_size, card.cv_filename, card.cv_bytes = 4, "cv.pdf", b"%PDF"
    card.deleted_at = datetime.now(UTC)
    clean.commit()
    assert done_at(clean, row, "cv") is None
    assert done_at(clean, row, "card_complete") is None


def test_filters_reuse_talents_and_merge_one_person_across_cases(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    person(clean, "ada@studio.it", rate=False)
    lead(clean, "giulia@studio.it")
    campaign = campaign_row(
        clean, source="filters", journey_status=None, filters={"list": "talents", "has_cv": True}
    )
    assert [c.email for c in candidates(clean, campaign)] == ["ada@studio.it"]
    campaign.filters = {"list": "talents", "status": "lead"}
    clean.commit()
    assert [c.email for c in candidates(clean, campaign)] == ["giulia@studio.it"]


def test_to_stored_origin_filter_is_still_the_marketing_page(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """REB-798: the list's `origin` became the channel a row shows; a campaign stored
    before that keeps `origin` for the page the person started from, and the replay
    asks the list for `origin_page`."""
    ada = person(clean, "ada@studio.it", rate=False)
    ada.origin = "pigrocrm"
    person(clean, "bob@studio.it", nome="Bob")
    clean.commit()
    campaign = campaign_row(
        clean,
        source="filters",
        journey_status=None,
        filters={"list": "talents", "origin": "pigrocrm"},
    )
    assert [c.email for c in candidates(clean, campaign)] == ["ada@studio.it"]


def test_every_exclusion_names_its_reason(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "boss@rebase.it", role="admin", cv=False)
    person(clean, "gone@studio.it", cv=False)
    person(clean, "never@studio.it", cv=False)
    person(clean, "bounce@studio.it", cv=False)
    person(clean, "recent@studio.it", cv=False)
    person(clean, "ok@studio.it", cv=False)
    clean.add(CampaignOptout(email="gone@studio.it", source="link"))
    clean.add(CampaignOptout(email="never@studio.it", source="admin"))
    earlier = campaign_row(clean)
    clean.add(
        CampaignRecipient(
            campaign_id=earlier.id,
            email="bounce@studio.it",
            kind="freelancer",
            code="1",
            before={},
            unsubscribe_token="b",
            status="sent",
            sent_at=T0 - timedelta(days=30),
            bounced_at=T0 - timedelta(days=30),
        )
    )
    clean.add(
        CampaignRecipient(
            campaign_id=earlier.id,
            email="recent@studio.it",
            kind="freelancer",
            code="2",
            before={},
            unsubscribe_token="r",
            status="sent",
            sent_at=T0 - timedelta(days=1),
        )
    )
    clean.commit()
    current = campaign_row(clean)
    reasons = exclusions(
        clean,
        [
            "boss@rebase.it",
            "gone@studio.it",
            "never@studio.it",
            "bounce@studio.it",
            "recent@studio.it",
            "ok@studio.it",
        ],
        campaign_id=current.id,
        now=T0,
        gap_days=3,
    )
    assert reasons["boss@rebase.it"] == REASON_ADMIN
    assert reasons["gone@studio.it"] == "si è disiscritto"
    assert reasons["never@studio.it"] == REASON_NEVER
    assert reasons["bounce@studio.it"] == REASON_BOUNCED
    assert reasons["recent@studio.it"].startswith("ha ricevuto un'altra campagna il ")
    assert "ok@studio.it" not in reasons


def test_the_audience_lists_everyone_and_greys_out_the_excluded(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    person(clean, "boss@rebase.it", role="admin", cv=False)
    person(clean, "ok@studio.it", cv=False)
    campaign = campaign_row(clean)
    rows = build_audience(clean, campaign, now=T0, gap_days=3)
    assert [(r.candidate.email, r.excluded) for r in rows] == [
        ("boss@rebase.it", REASON_ADMIN),
        ("ok@studio.it", None),
    ]


def test_the_legal_entities_filter_merges_open_requests_and_drops_the_closed_one(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    first = company(clean, "info@block-buy.it", status="new")
    second = company(clean, "info@block-buy.it", status="new")
    company(clean, "info@block-buy.it", status="closed")
    campaign = campaign_row(
        clean, source="filters", journey_status=None, filters={"list": "companies", "status": "new"}
    )
    found = candidates(clean, campaign)
    assert [c.email for c in found] == ["info@block-buy.it"]
    assert set(found[0].company_ids) == {first.id, second.id}
    assert found[0].nome == "Ciro"


def test_a_cross_case_address_across_a_lead_and_a_card_is_one_lowercase_row(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    lead(clean, "Ada@Studio.it")
    person(clean, "ada@studio.it")
    campaign = campaign_row(
        clean, source="filters", journey_status=None, filters={"list": "talents"}
    )
    rows = build_audience(clean, campaign, now=T0, gap_days=3)
    assert [(r.candidate.email, r.excluded) for r in rows] == [("ada@studio.it", None)]


@contextmanager
def statements(session: Session) -> Iterator[list[str]]:
    """Every SQL statement `session`'s engine runs inside the block."""
    seen: list[str] = []
    engine = session.get_bind()

    def record(*args: Any) -> None:
        seen.append(args[2])  # (conn, cursor, statement, parameters, context, many)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_no_list_snapshot_or_check_reads_a_cvs_bytes(clean: Session) -> None:  # noqa: F811  (fixture)
    """A list of a few hundred cards must not pull a few hundred PDFs out of Postgres:
    completeness reads `cv_size`, and nothing a campaign does needs the bytes."""
    person(clean, "nocv@studio.it", cv=False)
    person(clean, "done@studio.it")
    person(clean, "empty@studio.it", cv=False, rate=False)
    filtered = campaign_row(
        clean, source="filters", journey_status=None, filters={"list": "talents", "has_cv": True}
    )
    with statements(clean) as seen:
        for state in ("missing_cv", "complete", "scheda_vuota_nuovi", "scheda_vuota_entrati"):
            candidates_for_state(clean, state)
        candidate = candidates_for_state(clean, "missing_cv")[0]
        row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
        for action in ("cv", "card_complete", "profile_created"):
            done_at(clean, row, action)
        assert [c.email for c in candidates(clean, filtered)] == ["done@studio.it"]
    assert seen
    assert not [sql for sql in seen if "cv_bytes" in sql]


@pytest.mark.parametrize(
    "bad_value",
    [
        "not-a-date",
        # `fromisoformat` parses a date-only string cleanly, as naive midnight; a
        # naive value then raises `TypeError` where `done_at` compares it in plain
        # Python against a timezone-aware database timestamp (Greptile, PR #444,
        # round 4). Both a date-only and a datetime-only string are naive this way.
        "2026-09-28",
        "2026-09-28T10:00:00",
    ],
)
def test_to_row_with_to_broken_requests_snapshot_is_left_out_and_logged(
    clean: Session,  # noqa: F811  (fixture)
    caplog: pytest.LogCaptureFixture,
    bad_value: str,
) -> None:
    """Part 2 (REB-550): a malformed `before["requests"]` timestamp makes `done_at`'s own
    `datetime.fromisoformat` raise, or a naive one raise at the comparison, for that one
    row. Stamping already isolates a row like it per savepoint (REB-533, `outcome.py`);
    `waiting_rows` does the same, leaving the row out -- the safe side, since it never
    lets a mail reach someone who may already have acted -- and logging it by id, rather
    than 500ing the whole «Riscrivi» list."""
    # `hub_engine`'s Alembic `env.py` calls `fileConfig`, which disables every logger
    # that already existed (the trap `test_campaign_outcome.py`'s
    # `test_a_row_whose_stamping_raises_is_skipped_the_rest_still_stamped` documents):
    # undo it so `caplog` sees this module's line.
    logging.getLogger("rebase_core.campaigns.audience").disabled = False
    parent = campaign_row(clean, action="request_updated")
    fine = company(clean, "fine@studio.it")
    good = CampaignRecipient(
        campaign_id=parent.id,
        email="fine@studio.it",
        kind="company",
        code="1",
        before={"requests": {str(fine.id): fine.updated_at.isoformat()}},
        unsubscribe_token="t-good",
        status="sent",
        sent_at=T0,
    )
    rotta = company(clean, "rotta@studio.it")
    broken = CampaignRecipient(
        campaign_id=parent.id,
        email="rotta@studio.it",
        kind="company",
        code="2",
        before={"requests": {str(rotta.id): bad_value}},
        unsubscribe_token="t-broken",
        status="sent",
        sent_at=T0,
    )
    clean.add_all([good, broken])
    clean.commit()
    with caplog.at_level(logging.ERROR, logger="rebase_core.campaigns.audience"):
        rows = waiting_rows(clean, parent)
    assert [r.email for r in rows] == ["fine@studio.it"]
    assert str(broken.id) in caplog.text
    # The session survives the broken row: a second call still reads live.
    assert [r.email for r in waiting_rows(clean, parent)] == ["fine@studio.it"]


def test_the_follow_ups_preview_and_schedule_survive_a_broken_parent_row(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """The same broken row, reached through the two doors that answered 500 before the
    fix: the follow-up's audience preview and its schedule."""
    clock = Clock(T0)
    parent = campaign_row(clean, action="request_updated", status="sent")
    fine = company(clean, "fine2@studio.it")
    clean.add(
        CampaignRecipient(
            campaign_id=parent.id,
            email="fine2@studio.it",
            kind="company",
            code="1",
            before={"requests": {str(fine.id): fine.updated_at.isoformat()}},
            unsubscribe_token="t-good2",
            status="sent",
            sent_at=T0,
        )
    )
    rotta = company(clean, "rotta2@studio.it")
    clean.add(
        CampaignRecipient(
            campaign_id=parent.id,
            email="rotta2@studio.it",
            kind="company",
            code="2",
            before={"requests": {str(rotta.id): "not-a-date"}},
            unsubscribe_token="t-broken2",
            status="sent",
            sent_at=T0,
        )
    )
    clean.commit()
    # Past the gap-day window a fresh send within it would otherwise be excluded for,
    # the same advance `test_campaign_follow_up.py`'s own scheduled-follow-up test makes.
    clock.at = T0 + timedelta(days=SETTINGS.campaign_gap_days, hours=1)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    follow = service.follow_up(parent.id, who.id)
    preview = service.audience(follow.id)
    assert [r.email for r in preview.lines] == ["fine2@studio.it"]
    clock.at += timedelta(minutes=1)
    service.send_test(follow.id, as_admin(who), RecordingCampaignSender())
    scheduled = service.schedule(follow.id, ScheduleRequest())
    assert scheduled.status == "scheduled"


def test_a_non_parse_error_from_the_done_check_propagates_instead_of_dropping_the_row(
    clean: Session,  # noqa: F811  (fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Greptile P1 on PR #444: only a malformed snapshot's own parse errors are caught
    (`ValueError`, `TypeError`, `KeyError`); a transient database error or an unrelated
    bug must propagate rather than silently drop an eligible person from the list."""
    parent = campaign_row(clean, action="request_updated")
    fine = company(clean, "fine3@studio.it")
    clean.add(
        CampaignRecipient(
            campaign_id=parent.id,
            email="fine3@studio.it",
            kind="company",
            code="1",
            before={"requests": {str(fine.id): fine.updated_at.isoformat()}},
            unsubscribe_token="t-good3",
            status="sent",
            sent_at=T0,
        )
    )
    clean.commit()

    def raising(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(audience_module, "done_at_many", raising)
    with pytest.raises(RuntimeError, match="boom"):
        waiting_rows(clean, parent)


def test_to_row_whose_requests_is_not_to_string_keyed_dict_is_left_out_and_logged(
    clean: Session,  # noqa: F811  (fixture)
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CodeRabbit Major on PR #444: `{"richieste": [123]}` made `UUID(123)` raise
    `AttributeError`, breaking the whole list before the row could be left out. The
    shape is checked explicitly instead of widening the `except`, so a real bug
    elsewhere is not swallowed along with it."""
    logging.getLogger("rebase_core.campaigns.audience").disabled = False
    parent = campaign_row(clean, action="request_updated")
    fine = company(clean, "fine4@studio.it")
    good = CampaignRecipient(
        campaign_id=parent.id,
        email="fine4@studio.it",
        kind="company",
        code="1",
        before={"requests": {str(fine.id): fine.updated_at.isoformat()}},
        unsubscribe_token="t-good4",
        status="sent",
        sent_at=T0,
    )
    broken = CampaignRecipient(
        campaign_id=parent.id,
        email="rotta4@studio.it",
        kind="company",
        code="2",
        before={"requests": [123]},
        unsubscribe_token="t-broken4",
        status="sent",
        sent_at=T0,
    )
    clean.add_all([good, broken])
    clean.commit()
    with caplog.at_level(logging.ERROR, logger="rebase_core.campaigns.audience"):
        rows = waiting_rows(clean, parent)
    assert [r.email for r in rows] == ["fine4@studio.it"]
    assert str(broken.id) in caplog.text


@pytest.mark.parametrize("bad_before", [["not", "a", "dict"], "not-a-dict"])
def test_to_row_whose_before_is_not_to_dict_is_left_out_and_logged(
    clean: Session,  # noqa: F811  (fixture)
    caplog: pytest.LogCaptureFixture,
    bad_before: object,
) -> None:
    """The same shape check applies to `before` itself, not only `requests`: a row
    whose snapshot is a list or a string, not a dict, must not raise inside `.get`."""
    logging.getLogger("rebase_core.campaigns.audience").disabled = False
    parent = campaign_row(clean, action="request_updated")
    fine = company(clean, "fine5@studio.it")
    good = CampaignRecipient(
        campaign_id=parent.id,
        email="fine5@studio.it",
        kind="company",
        code="1",
        before={"requests": {str(fine.id): fine.updated_at.isoformat()}},
        unsubscribe_token="t-good5",
        status="sent",
        sent_at=T0,
    )
    broken = CampaignRecipient(
        campaign_id=parent.id,
        email="rotta5@studio.it",
        kind="company",
        code="2",
        before=bad_before,  # type: ignore[arg-type]
        unsubscribe_token="t-broken5",
        status="sent",
        sent_at=T0,
    )
    clean.add_all([good, broken])
    clean.commit()
    with caplog.at_level(logging.ERROR, logger="rebase_core.campaigns.audience"):
        rows = waiting_rows(clean, parent)
    assert [r.email for r in rows] == ["fine5@studio.it"]
    assert str(broken.id) in caplog.text


def test_a_value_error_from_inside_done_at_propagates_too(
    clean: Session,  # noqa: F811  (fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CodeRabbit's adversarial pass on PR #444, round 3: once every snapshot value
    `done_at` would parse is validated up front, `done_at` itself is called with no
    `try` around it at all -- a `ValueError` it still raises, for a reason that has
    nothing to do with this row's own snapshot, must propagate rather than being
    mistaken for one of the parse errors the pre-pass already ruled out."""
    parent = campaign_row(clean, action="request_updated")
    fine = company(clean, "fine6@studio.it")
    clean.add(
        CampaignRecipient(
            campaign_id=parent.id,
            email="fine6@studio.it",
            kind="company",
            code="1",
            before={"requests": {str(fine.id): fine.updated_at.isoformat()}},
            unsubscribe_token="t-good6",
            status="sent",
            sent_at=T0,
        )
    )
    clean.commit()

    def raising(*args: object, **kwargs: object) -> None:
        raise ValueError("not this row's own snapshot")

    monkeypatch.setattr(audience_module, "done_at_many", raising)
    with pytest.raises(ValueError, match="not this row's own snapshot"):
        waiting_rows(clean, parent)


def test_to_card_marked_discarded_is_excluded_with_its_reason(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """REB-524, Ivan's decision (2026-09-28): a card an admin turned down never gets a
    campaign. The journey states ignore `Freelancer.status`, so the rule is one of the
    exclusions, shown on screen with its reason, checked again before each mail."""
    person(clean, "no@studio.it", status="discarded")
    person(clean, "si@studio.it", status="active")
    current = campaign_row(clean, journey_status="complete")
    reasons = exclusions(
        clean, ["no@studio.it", "si@studio.it"], campaign_id=current.id, now=T0, gap_days=3
    )
    assert reasons == {"no@studio.it": REASON_DISCARDED}


def test_to_state_list_shows_to_discarded_card_as_excluded(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """«Profilo completo» was the case the card named: a turned-down card is complete."""
    person(clean, "no@studio.it", status="discarded")
    person(clean, "si@studio.it")
    campaign = campaign_row(clean, journey_status="complete")
    rows = build_audience(clean, campaign, now=T0, gap_days=3)
    assert [(r.candidate.email, r.excluded) for r in rows] == [
        ("no@studio.it", REASON_DISCARDED),
        ("si@studio.it", None),
    ]


@pytest.mark.parametrize(
    "filters",
    [{"list": "talents"}, {"list": "talents", "status": "discarded"}],
)
def test_to_filtered_list_shows_to_discarded_card_as_excluded_even_when_it_asks_for_them(
    clean: Session,  # noqa: F811  (fixture)
    filters: dict[str, str],
) -> None:
    person(clean, "no@studio.it", status="discarded")
    campaign = campaign_row(clean, source="filters", journey_status=None, filters=filters)
    rows = build_audience(clean, campaign, now=T0, gap_days=3)
    assert [(r.candidate.email, r.excluded) for r in rows] == [("no@studio.it", REASON_DISCARDED)]


# ---- the done check reads once per kind of fact, not once per person (REB-891) -----------


def _reference_done_at(
    session: Session,
    recipient: CampaignRecipient,
    action: str,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """`done_at` as it was before it was batched: up to three statements per row."""
    before = recipient.before or {}
    moment = since if since is not None else datetime.fromisoformat(before["t"])
    if action == "signed_in":
        user_id = recipient.user_id or session.scalar(
            select(User.id).where(func.lower(User.email) == recipient.email)
        )
        if user_id is None:
            return None
        return session.scalar(
            select(func.min(Login.logged_at)).where(
                Login.user_id == user_id, Login.logged_at > moment
            )
        )
    if action in ("cv", "card_complete", "profile_created"):
        card = _card(session, recipient.email)
        if card is None:
            return None
        if action == "profile_created":
            created = not before.get("has_card") and card.created_at > moment
            return card.created_at if created else None
        if action == "cv":
            if before.get("ha_cv") or card.cv_size is None:
                return None
            noted = session.scalar(
                select(func.min(Note.created_at)).where(
                    Note.entity_type == "freelancer",
                    Note.entity_id == card.id,
                    Note.source == "person",
                    Note.deleted_at.is_(None),
                    Note.text.startswith(CV_NOTE_PREFIX),
                    Note.created_at > moment,
                )
            )
            return noted or card.updated_at
        if before.get("complete") or card.cv_size is None or card.daily_rate is None:
            return None
        return now or card.updated_at
    if action == "request_updated":
        moments: list[datetime] = []
        for company_id, was in (before.get("requests") or {}).items():
            updated = session.scalar(select(Company.updated_at).where(Company.id == company_id))
            if updated is not None and updated > max(datetime.fromisoformat(was), moment):
                moments.append(updated)
        return min(moments) if moments else None
    if action == "click":
        return recipient.first_click_at
    return None


def _a_waiting_crowd(session: Session) -> tuple[Any, list[CampaignRecipient]]:
    """Cards and requests that moved after the list froze, and ones that did not, with a
    recipient row for each, sent at `T0`."""
    parent = campaign_row(session, action="cv")
    candidates_: list[Candidate] = []
    for n in range(4):
        person(session, f"nocv{n}@studio.it", cv=False)
    for n in range(3):
        person(session, f"done{n}@studio.it", logins=0)
    lead(session, "lead@studio.it")
    first = company(session, "azienda@studio.it")
    company(session, "altra@studio.it")
    for state in ("missing_cv", "complete", "lead", "azienda_aperta"):
        candidates_ += candidates_for_state(session, state)
    rows = []
    for n, candidate in enumerate(candidates_):
        row = recipient_for(session, candidate, snapshot(session, candidate, T0))
        row.campaign_id, row.status, row.sent_at = parent.id, "sent", T0
        row.code, row.unsubscribe_token = f"c{n}", f"t{n}"
        session.add(row)
        rows.append(row)
    session.commit()
    # After the snapshot: two CVs (one with the person's note), two logins (one before it),
    # a card made by the lead, one request changed.
    for email in ("nocv0@studio.it", "nocv1@studio.it"):
        card = _card(session, email)
        assert card is not None
        card.cv_size, card.cv_filename, card.cv_bytes = 4, "cv.pdf", b"%PDF"
    session.commit()
    first_card = _card(session, "nocv0@studio.it")
    assert first_card is not None
    NoteService(session).add(
        "freelancer",
        first_card.id,
        "CV caricato dalla persona",
        author_id=first_card.user_id,
        source="person",
    )
    done0 = session.scalar(select(User).where(User.email == "done0@studio.it"))
    done1 = session.scalar(select(User).where(User.email == "done1@studio.it"))
    assert done0 is not None and done1 is not None
    session.add(Login(user_id=done0.id, logged_at=T0 + timedelta(hours=2)))
    session.add(Login(user_id=done1.id, logged_at=T0 - timedelta(hours=2)))
    person(session, "lead@studio.it", nome="Giulia")
    first.duration = "18 mesi"
    session.commit()
    return parent, rows


@pytest.mark.parametrize(
    "action",
    ["signed_in", "cv", "card_complete", "profile_created", "request_updated", "click"],
)
def test_the_batched_done_check_answers_what_one_row_at_a_time_answered(
    clean: Session,  # noqa: F811  (fixture)
    action: str,
) -> None:
    _, rows = _a_waiting_crowd(clean)
    rows[0].first_click_at = T0 + timedelta(minutes=3)
    clean.commit()
    batched = done_at_many(clean, rows, action, since=lambda row: row.sent_at)
    one_by_one = {row.id: _reference_done_at(clean, row, action, since=row.sent_at) for row in rows}
    assert batched == one_by_one
    if action != "click":
        assert any(moment is not None for moment in batched.values())
        assert any(moment is None for moment in batched.values())


def test_waiting_rows_reads_a_fixed_number_of_statements(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    parent, rows = _a_waiting_crowd(clean)
    by_action: dict[str, int] = {}
    for action in ("signed_in", "cv", "request_updated"):
        parent.action = action
        clean.commit()
        with statements(clean) as few:
            waiting_rows(clean, parent)
        # The same list five times as long, every row a person of its own.
        for n in range(len(rows) * 4):
            person(clean, f"extra{action}{n}@studio.it", cv=False)
        extra = [
            CampaignRecipient(
                campaign_id=parent.id,
                email=f"extra{action}{n}@studio.it",
                kind="freelancer",
                code=f"x{n}"[:8],
                before={"t": T0.isoformat(), "ha_cv": False},
                unsubscribe_token=f"x{action}{n}",
                status="sent",
                sent_at=T0,
            )
            for n in range(len(rows) * 4)
        ]
        clean.add_all(extra)
        clean.commit()
        with statements(clean) as many:
            waiting_rows(clean, parent)
        by_action[action] = len(many) - len(few)
        clean.execute(
            CampaignRecipient.__table__.delete().where(
                CampaignRecipient.email.like(f"extra{action}%")
            )
        )
        clean.commit()
    assert by_action == {action: 0 for action in by_action}
