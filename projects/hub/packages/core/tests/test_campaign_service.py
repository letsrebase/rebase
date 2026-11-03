"""CampaignService: the admin's verbs (spec § 4)."""

import threading
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from time import sleep

import pytest
from campaign_fixtures import (  # noqa: F401  (fixture)
    NOW,
    SETTINGS,
    T0,
    Clock,
    admin,
    as_admin,
    campaign_row,
    clean,
    draft,
    person,
)
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from rebase_core.campaigns.audience import REASON_CANCELLED
from rebase_core.campaigns.schemas import (
    CampaignPatch,
    CampaignRead,
    ScheduleRequest,
    TalentsFilters,
)
from rebase_core.campaigns.sender import RecordingCampaignSender, SendOutcome
from rebase_core.campaigns.service import (
    DRAFT_HAS_HISTORY,
    NOT_A_DRAFT,
    ONLY_A_DRAFT_IS_DELETED,
    CampaignService,
)
from rebase_core.db import session_factory
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.models import Campaign, CampaignRecipient, Freelancer, Login, User


def test_a_draft_gets_a_dated_unique_slug(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    first = service.create(who.id, draft())
    second = service.create(who.id, draft())
    assert first.slug == "c-2026-09-25-manca-il-cv"
    assert second.slug == "c-2026-09-25-manca-il-cv-2"
    assert first.status == "draft" and first.ready is False


def test_phase_one_refuses_the_pigro_parts(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    bad_drafts = (
        draft(journey_status="pigro_empty"),
        draft(button_meta="pigro"),
        draft(action="pigro_customer"),
    )
    for bad in bad_drafts:
        with pytest.raises(ValidationFailed, match="fase Pigro"):
            service.create(who.id, bad)


def test_the_audience_preview_counts_included_and_excluded(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    person(clean, "nocv@studio.it", cv=False)
    person(clean, "boss@rebase.it", cv=False, role="admin")
    created = service.create(who.id, draft())
    preview = service.audience(created.id)
    assert (preview.included, preview.excluded) == (1, 1)


def test_an_edit_moves_content_at_and_to_not_draft_cannot_be_edited(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    created = service.create(admin(clean).id, draft())
    clock.at = NOW + timedelta(minutes=5)
    edited = service.update(created.id, CampaignPatch(subject="Nuovo oggetto"))
    assert edited.content_at == NOW + timedelta(minutes=5)

    row = clean.get(Campaign, created.id)
    assert row is not None
    row.status = "scheduled"
    clean.commit()
    with pytest.raises(InvalidState, match=NOT_A_DRAFT):
        service.update(created.id, CampaignPatch(subject="Un altro oggetto"))


def test_switching_source_clears_the_other_sources_column(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)

    with_filters = service.create(
        who.id,
        draft(source="filters", journey_status=None, filters=TalentsFilters(list="talents")),
    )
    service.update(with_filters.id, CampaignPatch(source="status", journey_status="missing_cv"))
    filters_is_null = clean.execute(
        text("select 1 from campaigns where id = :id and filters is null"),
        {"id": with_filters.id},
    ).scalar()
    assert filters_is_null == 1

    with_state = service.create(who.id, draft())
    service.update(
        with_state.id, CampaignPatch(source="filters", filters=TalentsFilters(list="talents"))
    )
    journey_status_is_null = clean.execute(
        text("select 1 from campaigns where id = :id and journey_status is null"),
        {"id": with_state.id},
    ).scalar()
    assert journey_status_is_null == 1


def ready(service: CampaignService, session: Session, clock: Clock) -> tuple[CampaignRead, User]:
    who = admin(session)
    person(session, "nocv@studio.it", cv=False)
    person(session, "other@studio.it", cv=False)
    created = service.create(who.id, draft())
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    return service.detail(created.id).campaign, who


def test_the_test_goes_to_the_admin_and_enables_sending(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    created = service.create(who.id, draft())
    recording = RecordingCampaignSender()
    tested = service.send_test(created.id, as_admin(who), recording)
    assert tested.ready is True
    rendered = recording.sent[0]
    assert rendered.mail.to == "ivan@rebase.it"
    assert rendered.mail.subject.startswith("[prova] ")
    assert rendered.tags["kind"] == "test" and "r" not in rendered.tags


def test_an_edit_after_the_test_blocks_scheduling_until_a_new_test(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Review Focus 1: `updated_at` moves on the test's own write; `content_at` does
    not, so it is what the rule compares."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, who = ready(service, clean, clock)
    clock.at += timedelta(minutes=1)
    service.update(campaign.id, CampaignPatch(text="Ciao {nome},\n\naltro testo."))
    with pytest.raises(InvalidState, match="Manda una prova"):
        service.schedule(campaign.id, ScheduleRequest())
    clock.at += timedelta(minutes=1)
    service.send_test(campaign.id, as_admin(who), RecordingCampaignSender())
    assert service.schedule(campaign.id, ScheduleRequest()).status == "scheduled"


def test_a_save_that_changes_nothing_keeps_the_test(clean: Session) -> None:  # noqa: F811  (fixture)
    """The wizard sends every field again when the admin goes back to Chi or Cosa and
    presses «Avanti» without touching anything: the test already sent still holds."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _who = ready(service, clean, clock)
    clock.at += timedelta(minutes=1)
    same = draft()
    service.update(
        campaign.id,
        CampaignPatch(
            name=same.name,
            source=same.source,
            journey_status=same.journey_status,
            subject=same.subject,
            text=same.text,
            button_text=same.button_text,
            button_meta=same.button_meta,
            action=same.action,
        ),
    )
    assert service.schedule(campaign.id, ScheduleRequest()).status == "scheduled"


def test_a_rename_after_the_test_asks_for_a_new_one(clean: Session) -> None:  # noqa: F811  (fixture)
    """A new name is a new slug, and the slug is the button's `utm_campaign` and the
    mail's Resend tag: the mail the test showed is not the one that would leave."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _who = ready(service, clean, clock)
    clock.at += timedelta(minutes=1)
    renamed = service.update(campaign.id, CampaignPatch(name="Manca il CV, secondo giro"))
    assert renamed.slug != campaign.slug
    assert renamed.ready is False
    with pytest.raises(InvalidState, match="Manda una prova"):
        service.schedule(campaign.id, ScheduleRequest())


def test_a_filter_save_that_changes_nothing_keeps_the_test(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    person(clean, "ada@studio.it")
    filters = TalentsFilters(list="talents", job_title="Backend", min_rate=Decimal("300"))
    created = service.create(who.id, draft(source="filters", journey_status=None, filters=filters))
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    clock.at += timedelta(minutes=1)
    unchanged = service.update(
        created.id,
        CampaignPatch(
            source="filters",
            filters=TalentsFilters(list="talents", job_title="Backend", min_rate=Decimal("300")),
        ),
    )
    assert unchanged.ready is True
    clock.at += timedelta(minutes=1)
    changed = service.update(
        created.id,
        CampaignPatch(filters=TalentsFilters(list="talents", job_title="Frontend")),
    )
    assert changed.ready is False


def test_a_refused_test_does_not_enable_sending(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    created = service.create(who.id, draft())
    with pytest.raises(InvalidState, match="Resend"):
        service.send_test(
            created.id,
            as_admin(who),
            RecordingCampaignSender([SendOutcome("rejected", detail="Resend 422")]),
        )
    assert service.detail(created.id).campaign.ready is False


def test_scheduling_freezes_the_list_minus_the_unticked_and_the_excluded(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    person(clean, "boss@rebase.it", cv=False, role="admin")
    scheduled = service.schedule(campaign.id, ScheduleRequest(excluded=["Other@Studio.it"]))
    assert scheduled.status == "scheduled" and scheduled.scheduled_for == clock.at
    rows = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).all()
    assert [r.email for r in rows] == ["nocv@studio.it"]
    assert rows[0].status == "queued" and len(rows[0].code) == 8
    assert rows[0].before["has_cv"] is False and len(rows[0].unsubscribe_token) >= 40


def test_an_odd_unticked_address_does_not_stop_the_send(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    everyone = ScheduleRequest.model_validate(
        {"excluded": [" NoCV@Studio.it ", "OTHER@studio.it", "odd..legacy@studio.it"]}
    )
    with pytest.raises(ValidationFailed, match="nessuno riceverebbe"):
        service.schedule(campaign.id, everyone)
    odd_only = ScheduleRequest.model_validate({"excluded": ["odd..legacy@studio.it"]})
    assert service.schedule(campaign.id, odd_only).status == "scheduled"
    rows = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).all()
    assert sorted(r.email for r in rows) == ["nocv@studio.it", "other@studio.it"]


def test_renaming_a_draft_moves_its_slug_to_the_new_name(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """A filtered campaign is created as «Campagna da filtri» before the admin names
    it in Cosa: the slug (the button's `utm_campaign`, Resend's tag) follows the name
    it is sent with, keeping the day it was created, and never collides with itself."""
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    filters = TalentsFilters(list="talents")
    created = service.create(
        who.id,
        draft(name="Campagna da filtri", source="filters", journey_status=None, filters=filters),
    )
    day = f"{created.created_at.astimezone(UTC):%Y-%m-%d}"
    renamed = service.update(created.id, CampaignPatch(name="Richiamo di ottobre"))
    assert renamed.slug == f"c-{day}-richiamo-di-ottobre"
    again = service.update(created.id, CampaignPatch(name="Richiamo di ottobre", subject="x"))
    assert again.slug == renamed.slug
    second = service.create(who.id, draft(name="Altra"))
    clash = service.update(second.id, CampaignPatch(name="Richiamo di ottobre"))
    assert clash.slug == f"c-{day}-richiamo-di-ottobre-2"
    untouched = service.update(created.id, CampaignPatch(subject="y"))
    assert untouched.slug == renamed.slug


@pytest.mark.parametrize(
    ("day", "at_time", "utc"),
    [
        (date(2026, 10, 24), time(9, 30), datetime(2026, 10, 24, 7, 30, tzinfo=UTC)),  # CEST
        (date(2026, 10, 25), time(9, 30), datetime(2026, 10, 25, 8, 30, tzinfo=UTC)),  # CET
        (
            date(2026, 10, 25),
            time(2, 30),
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        ),  # twice: the first
    ],
)
def test_a_scheduled_time_is_rome_wall_clock_across_dst(
    clean: Session,  # noqa: F811  (fixture)
    day: date,
    at_time: time,
    utc: datetime,
) -> None:
    """Review Focus 2."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    assert (
        service.schedule(campaign.id, ScheduleRequest(day=day, at_time=at_time)).scheduled_for
        == utc
    )


def test_a_time_that_does_not_exist_or_has_passed_is_refused(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    with pytest.raises(ValidationFailed, match="cambio d'ora"):
        service.schedule(campaign.id, ScheduleRequest(day=date(2027, 3, 28), at_time=time(2, 30)))
    with pytest.raises(ValidationFailed, match="già passato"):
        service.schedule(campaign.id, ScheduleRequest(day=date(2026, 9, 24), at_time=time(9, 30)))
    with pytest.raises(ValidationFailed, match="giorno e ora"):
        service.schedule(campaign.id, ScheduleRequest(day=date(2026, 9, 30)))


def test_back_to_draft_drops_the_frozen_list_and_cancel_skips_what_is_left(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest(day=date(2026, 9, 30), at_time=time(9, 30)))
    assert service.back_to_draft(campaign.id).status == "draft"
    assert clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == 0
    service.schedule(campaign.id, ScheduleRequest())
    cancelled = service.cancel(campaign.id)
    assert cancelled.status == "cancelled"
    assert {
        r.reason for r in clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id)
    } == {"campagna annullata"}
    with pytest.raises(InvalidState):
        service.cancel(campaign.id)


def test_scheduling_an_already_scheduled_campaign_raises_invalid_state_not_integrity_error(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Fix round 1 (controller ruling R12): the lock means a second «Programma» reads
    the row's committed state and raises the ordinary sentence, never the unique
    index's `IntegrityError`."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest())
    with pytest.raises(InvalidState, match=NOT_A_DRAFT):
        service.schedule(campaign.id, ScheduleRequest())


def test_back_to_draft_refuses_to_campaign_already_sending_and_deletes_nothing(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest())
    before = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count()
    row = clean.get(Campaign, campaign.id)
    assert row is not None
    row.status = "sending"
    clean.commit()
    with pytest.raises(InvalidState):
        service.back_to_draft(campaign.id)
    assert clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == before


def test_cancel_on_an_sending_campaign_leaves_the_sent_row_and_skips_the_queued_one(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest())
    rows = (
        clean.query(CampaignRecipient)
        .filter_by(campaign_id=campaign.id)
        .order_by(CampaignRecipient.email)
        .all()
    )
    assert len(rows) == 2
    rows[0].status, rows[0].sent_at = "sent", clock.at
    row = clean.get(Campaign, campaign.id)
    assert row is not None
    row.status = "sending"
    clean.commit()

    cancelled = service.cancel(campaign.id)
    assert cancelled.status == "cancelled"
    refreshed = (
        clean.query(CampaignRecipient)
        .filter_by(campaign_id=campaign.id)
        .order_by(CampaignRecipient.email)
        .all()
    )
    assert refreshed[0].status == "sent" and refreshed[0].reason is None
    assert refreshed[1].status == "skipped" and refreshed[1].reason == REASON_CANCELLED


def test_a_second_call_blocked_on_the_lock_then_sees_the_fresh_state_not_a_stale_one(
    clean: Session,  # noqa: F811  (fixture)
    hub_engine: Engine,
) -> None:
    """A true two-session race (Fix round 1, controller ruling R12, optional):
    reproduces the finding's exact shape. `clean` stands in for a concurrent actor
    (e.g. the future send loop) that has already moved the campaign to `cancelled`,
    locked, uncommitted, exactly like the tick that moves a campaign to `sending`
    mid-way through `back_to_draft`'s read. A second `cancel()` call must block on the
    locked read and, once `clean` commits, see the FRESH state and raise `InvalidState`,
    never read the stale `scheduled` and silently overwrite `clean`'s change, which
    is what `_require` (no lock) would do: the plain read does not wait, the check
    passes on stale data, and only the final commit blocks, succeeding once the lock
    is released and clobbering the concurrent write instead of refusing."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest())

    row = clean.get(Campaign, campaign.id, with_for_update=True)
    assert row is not None
    row.status = "cancelled"

    other = session_factory(hub_engine)()
    outcome: dict[str, BaseException] = {}
    entered = threading.Event()

    def call_cancel_from_another_session() -> None:
        entered.set()
        other_service = CampaignService(other, SETTINGS, clock=Clock(clock.at))
        try:
            other_service.cancel(campaign.id)
        except InvalidState as exc:
            outcome["raised"] = exc
        finally:
            other.rollback()

    thread = threading.Thread(target=call_cancel_from_another_session)
    thread.start()
    entered.wait(timeout=5)
    sleep(0.3)  # give the thread time to reach the blocking `SELECT ... FOR UPDATE`
    clean.commit()  # releases the lock, `cancelled` now the committed state
    thread.join(timeout=5)
    other.close()

    assert isinstance(outcome.get("raised"), InvalidState)


def _recipient(
    session: Session, campaign_id: object, email: str, **fields: object
) -> CampaignRecipient:
    values: dict[str, object] = {
        "campaign_id": campaign_id,
        "email": email,
        "kind": "freelancer",
        "code": "c0de0001",
        "before": {"t": T0.isoformat()},
        "unsubscribe_token": f"tok-{email}",
        "status": "sent",
        "sent_at": T0,
    }
    values.update(fields)
    row = CampaignRecipient(**values)
    session.add(row)
    session.commit()
    return row


def test_the_counts_add_clicks_entries_and_actions(clean: Session) -> None:  # noqa: F811  (fixture)
    campaign = campaign_row(clean, status="sent", sent_at=T0)
    later = T0 + timedelta(hours=1)
    _recipient(
        clean, campaign.id, "a@studio.it", first_click_at=later, signed_in_at=later, action_at=later
    )
    _recipient(clean, campaign.id, "b@studio.it", first_click_at=later, signed_in_at=later)
    _recipient(clean, campaign.id, "c@studio.it", first_click_at=later)
    _recipient(clean, campaign.id, "d@studio.it", status="skipped", sent_at=None)
    counts = CampaignService(clean, SETTINGS).detail(campaign.id).counts
    assert (counts.sent, counts.clicked, counts.entered, counts.actions) == (3, 3, 2, 1)
    listed = CampaignService(clean, SETTINGS).list_all().items[0].counts
    assert listed == counts


def test_from_mail_needs_this_campaigns_slug_and_this_persons_code(clean: Session) -> None:  # noqa: F811  (fixture)
    """Review Focus 5: a forwarded mail carries the slug with somebody else's code."""
    campaign = campaign_row(clean, status="sent", sent_at=T0, action="signed_in")
    later = T0 + timedelta(hours=1)
    ada = User(email="ada@studio.it", first_name="Ada", last_name="L")
    bob = User(email="bob@studio.it", first_name="Bob", last_name="L")
    clean.add_all([ada, bob])
    clean.flush()
    clean.add(
        Login(user_id=ada.id, logged_at=later, utm_campaign=campaign.slug, utm_term="c0de00aa")
    )
    clean.add(
        Login(user_id=bob.id, logged_at=later, utm_campaign=campaign.slug, utm_term="c0de00aa")
    )
    clean.commit()
    _recipient(
        clean, campaign.id, "ada@studio.it", code="c0de00aa", signed_in_at=later, action_at=later
    )
    _recipient(
        clean, campaign.id, "bob@studio.it", code="c0de00bb", signed_in_at=later, action_at=later
    )
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).recipients}
    assert (rows["ada@studio.it"].signed_in_from_mail, rows["ada@studio.it"].action_from_mail) == (
        True,
        True,
    )
    assert (rows["bob@studio.it"].signed_in_from_mail, rows["bob@studio.it"].action_from_mail) == (
        False,
        False,
    )


def test_from_mail_is_the_login_that_stamped_the_entry_not_any_matching_login(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Review Focus 1: an earlier, unrelated login stamped `signed_in_at`; a later login
    happens to carry this campaign's slug and this row's code. The row did not enter
    from the mail -- the stamped moment says so."""
    campaign = campaign_row(clean, status="sent", sent_at=T0, action="signed_in")
    earlier = T0 + timedelta(hours=1)
    later = T0 + timedelta(hours=2)
    ada = User(email="ada@studio.it", first_name="Ada", last_name="L")
    clean.add(ada)
    clean.flush()
    clean.add(Login(user_id=ada.id, logged_at=earlier))
    clean.add(
        Login(user_id=ada.id, logged_at=later, utm_campaign=campaign.slug, utm_term="c0de00aa")
    )
    clean.commit()
    _recipient(clean, campaign.id, "ada@studio.it", code="c0de00aa", signed_in_at=earlier)
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).recipients}
    assert rows["ada@studio.it"].signed_in_from_mail is False
    assert rows["ada@studio.it"].action_from_mail is False


def test_a_card_that_carries_the_slug_was_created_from_the_mail(clean: Session) -> None:  # noqa: F811  (fixture)
    campaign = campaign_row(
        clean,
        status="sent",
        sent_at=T0,
        journey_status="lead",
        action="profile_created",
        button_meta="wizard",
    )
    later = T0 + timedelta(hours=1)
    giulia = User(email="giulia@studio.it", first_name="Giulia", last_name="B")
    clean.add(giulia)
    clean.flush()
    # The wizard stores the button link's `utm_term` on the card (item 2, Greptile P1):
    # the row's own `code` is `c0de0001` by `_recipient`'s default, matched here.
    clean.add(
        Freelancer(user_id=giulia.id, links=[], utm_campaign=campaign.slug, utm_term="c0de0001")
    )
    clean.commit()
    _recipient(clean, campaign.id, "giulia@studio.it", kind="lead", action_at=later)
    _recipient(clean, campaign.id, "nina@studio.it", kind="lead", action_at=later)
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).recipients}
    assert rows["giulia@studio.it"].action_from_mail is True
    assert rows["nina@studio.it"].action_from_mail is False
    assert rows["giulia@studio.it"].signed_in_from_mail is False


def test_to_card_with_the_slug_but_another_persons_code_is_not_from_mail(clean: Session) -> None:  # noqa: F811  (fixture)
    """Item 2 (Greptile P1): a forwarded mail carries the slug too, so the card's own
    `utm_term` must match the row's `code`, the same rule the login check already
    applies (Review Focus 5)."""
    campaign = campaign_row(
        clean,
        status="sent",
        sent_at=T0,
        journey_status="lead",
        action="profile_created",
        button_meta="wizard",
    )
    later = T0 + timedelta(hours=1)
    nina = User(email="nina@studio.it", first_name="Nina", last_name="B")
    clean.add(nina)
    clean.flush()
    clean.add(
        Freelancer(user_id=nina.id, links=[], utm_campaign=campaign.slug, utm_term="not-ninas-code")
    )
    clean.commit()
    _recipient(clean, campaign.id, "nina@studio.it", kind="lead", code="c0de0002", action_at=later)
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).recipients}
    assert rows["nina@studio.it"].action_from_mail is False


def test_the_test_mail_greets_the_admin_in_the_subject_too(clean: Session) -> None:  # noqa: F811  (fixture)
    """REB-524: the test shows the subject as a person will read it, `{nome}` included."""
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    created = service.create(who.id, draft(subject="{nome}, manca solo il CV"))
    recording = RecordingCampaignSender()
    service.send_test(created.id, as_admin(who), recording)
    assert recording.sent[0].mail.subject == "[prova] Ivan, manca solo il CV"


def test_a_draft_is_deleted_and_nothing_else_is(clean: Session) -> None:  # noqa: F811  (fixture)
    """REB-524: an abandoned draft can go. It never froze a list (`back_to_draft` drops
    the one it had), so the row is all there is to delete."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    kept = service.create(admin(clean).id, draft(name="Resta"))
    service.delete(campaign.id)
    clean.expire_all()
    assert clean.get(Campaign, campaign.id) is None
    assert clean.get(Campaign, kept.id) is not None
    with pytest.raises(NotFound):
        service.delete(campaign.id)


def test_a_draft_moved_back_from_scheduled_is_a_draft_again_and_can_go(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest(day=date(2026, 9, 30), at_time=time(9, 30)))
    service.back_to_draft(campaign.id)
    service.delete(campaign.id)
    clean.expire_all()
    assert clean.get(Campaign, campaign.id) is None


@pytest.mark.parametrize("status", ["scheduled", "sending", "sent", "cancelled"])
def test_a_campaign_past_its_draft_is_never_deleted(
    clean: Session,  # noqa: F811  (fixture)
    status: str,
) -> None:
    """A scheduled, sending, sent or cancelled campaign carries rows people received or
    were meant to: it stays, with every one of them."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest())
    row = clean.get(Campaign, campaign.id)
    assert row is not None
    row.status = status
    clean.commit()
    before = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count()
    with pytest.raises(InvalidState, match=ONLY_A_DRAFT_IS_DELETED):
        service.delete(campaign.id)
    clean.expire_all()
    assert clean.get(Campaign, campaign.id) is not None
    assert clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == before


def test_a_delete_from_a_session_holding_a_stale_draft_is_refused(
    clean: Session,  # noqa: F811  (fixture)
    hub_engine: Engine,
) -> None:
    """CodeRabbit on #473: session A still holds the campaign as a `draft` in its identity
    map while session B schedules it and commits. `SELECT ... FOR UPDATE` alone does not
    overwrite an object already loaded, so A's delete used to act on the stale `draft`
    and `ON DELETE CASCADE` took B's frozen list with it. The locked read repopulates the
    row: the delete is refused and the recipients stay."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    held = clean.get(Campaign, campaign.id)
    assert held is not None and held.status == "draft"

    other = session_factory(hub_engine)()
    try:
        CampaignService(other, SETTINGS, clock=Clock(clock.at)).schedule(
            campaign.id, ScheduleRequest()
        )
    finally:
        other.close()
    assert held.status == "draft"  # A's copy is stale on purpose

    with pytest.raises(InvalidState, match=ONLY_A_DRAFT_IS_DELETED):
        service.delete(campaign.id)
    clean.expire_all()
    assert clean.get(Campaign, campaign.id).status == "scheduled"  # type: ignore[union-attr]
    assert clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == 2


@pytest.mark.parametrize(
    "stamp",
    [
        "sent_at",
        "delivered_at",
        "bounced_at",
        "first_click_at",
        "complaint_at",
        "signed_in_at",
        "action_at",
    ],
)
def test_a_draft_whose_rows_carry_a_send_or_an_outcome_is_not_deleted(
    clean: Session,  # noqa: F811  (fixture)
    stamp: str,
) -> None:
    """CodeRabbit on #473, round 2: a `draft` never has such a row, but if one ever did,
    the cascade would erase the history silently. The delete refuses with a sentence."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    created = service.create(admin(clean).id, draft())
    fields: dict[str, object] = {"status": "queued", "sent_at": None, stamp: T0}
    _recipient(clean, created.id, "ada@studio.it", **fields)
    with pytest.raises(InvalidState, match=DRAFT_HAS_HISTORY):
        service.delete(created.id)
    clean.expire_all()
    assert clean.get(Campaign, created.id) is not None
    assert clean.query(CampaignRecipient).filter_by(campaign_id=created.id).count() == 1
