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
    TalentiFiltri,
)
from rebase_core.campaigns.sender import RecordingCampaignSender, SendOutcome
from rebase_core.campaigns.service import NOT_A_DRAFT, ONLY_A_DRAFT_IS_DELETED, CampaignService
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
    assert first.stato == "bozza" and first.pronta is False


def test_phase_one_refuses_the_pigro_parts(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    bad_drafts = (
        draft(stato_percorso="pigro_vuoto"),
        draft(bottone_meta="pigro"),
        draft(azione="pigro_cliente"),
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
    assert (preview.incluse, preview.escluse) == (1, 1)


def test_an_edit_moves_contenuto_at_and_a_non_draft_cannot_be_edited(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    created = service.create(admin(clean).id, draft())
    clock.at = NOW + timedelta(minutes=5)
    edited = service.update(created.id, CampaignPatch(oggetto="Nuovo oggetto"))
    assert edited.contenuto_at == NOW + timedelta(minutes=5)

    row = clean.get(Campaign, created.id)
    assert row is not None
    row.stato = "programmata"
    clean.commit()
    with pytest.raises(InvalidState, match=NOT_A_DRAFT):
        service.update(created.id, CampaignPatch(oggetto="Un altro oggetto"))


def test_switching_fonte_clears_the_other_sources_column(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)

    with_filters = service.create(
        who.id,
        draft(fonte="filtri", stato_percorso=None, filtri=TalentiFiltri(lista="talenti")),
    )
    service.update(with_filters.id, CampaignPatch(fonte="stato", stato_percorso="manca_cv"))
    filtri_is_null = clean.execute(
        text("select 1 from campaigns where id = :id and filtri is null"),
        {"id": with_filters.id},
    ).scalar()
    assert filtri_is_null == 1

    with_state = service.create(who.id, draft())
    service.update(
        with_state.id, CampaignPatch(fonte="filtri", filtri=TalentiFiltri(lista="talenti"))
    )
    stato_percorso_is_null = clean.execute(
        text("select 1 from campaigns where id = :id and stato_percorso is null"),
        {"id": with_state.id},
    ).scalar()
    assert stato_percorso_is_null == 1


def ready(service: CampaignService, session: Session, clock: Clock) -> tuple[CampaignRead, User]:
    who = admin(session)
    person(session, "nocv@studio.it", cv=False)
    person(session, "other@studio.it", cv=False)
    created = service.create(who.id, draft())
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    return service.detail(created.id).campagna, who


def test_the_test_goes_to_the_admin_and_enables_sending(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    created = service.create(who.id, draft())
    recording = RecordingCampaignSender()
    tested = service.send_test(created.id, as_admin(who), recording)
    assert tested.pronta is True
    rendered = recording.sent[0]
    assert rendered.mail.to == "ivan@rebase.it"
    assert rendered.mail.subject.startswith("[prova] ")
    assert rendered.tags["kind"] == "test" and "r" not in rendered.tags


def test_an_edit_after_the_test_blocks_scheduling_until_a_new_test(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Review Focus 1: `updated_at` moves on the test's own write; `contenuto_at` does
    not, so it is what the rule compares."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, who = ready(service, clean, clock)
    clock.at += timedelta(minutes=1)
    service.update(campaign.id, CampaignPatch(testo="Ciao {nome},\n\naltro testo."))
    with pytest.raises(InvalidState, match="Manda una prova"):
        service.schedule(campaign.id, ScheduleRequest())
    clock.at += timedelta(minutes=1)
    service.send_test(campaign.id, as_admin(who), RecordingCampaignSender())
    assert service.schedule(campaign.id, ScheduleRequest()).stato == "programmata"


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
            nome=same.nome,
            fonte=same.fonte,
            stato_percorso=same.stato_percorso,
            oggetto=same.oggetto,
            testo=same.testo,
            bottone_testo=same.bottone_testo,
            bottone_meta=same.bottone_meta,
            azione=same.azione,
        ),
    )
    assert service.schedule(campaign.id, ScheduleRequest()).stato == "programmata"


def test_a_rename_after_the_test_asks_for_a_new_one(clean: Session) -> None:  # noqa: F811  (fixture)
    """A new name is a new slug, and the slug is the button's `utm_campaign` and the
    mail's Resend tag: the mail the test showed is not the one that would leave."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _who = ready(service, clean, clock)
    clock.at += timedelta(minutes=1)
    renamed = service.update(campaign.id, CampaignPatch(nome="Manca il CV, secondo giro"))
    assert renamed.slug != campaign.slug
    assert renamed.pronta is False
    with pytest.raises(InvalidState, match="Manda una prova"):
        service.schedule(campaign.id, ScheduleRequest())


def test_a_filter_save_that_changes_nothing_keeps_the_test(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    person(clean, "ada@studio.it")
    filtri = TalentiFiltri(lista="talenti", posizione="Backend", tariffa_min=Decimal("300"))
    created = service.create(who.id, draft(fonte="filtri", stato_percorso=None, filtri=filtri))
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    clock.at += timedelta(minutes=1)
    unchanged = service.update(
        created.id,
        CampaignPatch(
            fonte="filtri",
            filtri=TalentiFiltri(lista="talenti", posizione="Backend", tariffa_min=Decimal("300")),
        ),
    )
    assert unchanged.pronta is True
    clock.at += timedelta(minutes=1)
    changed = service.update(
        created.id,
        CampaignPatch(filtri=TalentiFiltri(lista="talenti", posizione="Frontend")),
    )
    assert changed.pronta is False


def test_a_refused_test_does_not_enable_sending(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    created = service.create(who.id, draft())
    with pytest.raises(InvalidState, match="Resend"):
        service.send_test(
            created.id,
            as_admin(who),
            RecordingCampaignSender([SendOutcome("rifiutata", dettaglio="Resend 422")]),
        )
    assert service.detail(created.id).campagna.pronta is False


def test_scheduling_freezes_the_list_minus_the_unticked_and_the_excluded(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    person(clean, "boss@rebase.it", cv=False, role="admin")
    scheduled = service.schedule(campaign.id, ScheduleRequest(esclusi=["Other@Studio.it"]))
    assert scheduled.stato == "programmata" and scheduled.programmata_per == clock.at
    rows = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).all()
    assert [r.email for r in rows] == ["nocv@studio.it"]
    assert rows[0].stato == "in_coda" and len(rows[0].codice) == 8
    assert rows[0].prima["ha_cv"] is False and len(rows[0].disiscrizione_token) >= 40


def test_an_odd_unticked_address_does_not_stop_the_send(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    everyone = ScheduleRequest.model_validate(
        {"esclusi": [" NoCV@Studio.it ", "OTHER@studio.it", "odd..legacy@studio.it"]}
    )
    with pytest.raises(ValidationFailed, match="nessuno riceverebbe"):
        service.schedule(campaign.id, everyone)
    odd_only = ScheduleRequest.model_validate({"esclusi": ["odd..legacy@studio.it"]})
    assert service.schedule(campaign.id, odd_only).stato == "programmata"
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
    filtri = TalentiFiltri(lista="talenti")
    created = service.create(
        who.id, draft(nome="Campagna da filtri", fonte="filtri", stato_percorso=None, filtri=filtri)
    )
    day = f"{created.created_at.astimezone(UTC):%Y-%m-%d}"
    renamed = service.update(created.id, CampaignPatch(nome="Richiamo di ottobre"))
    assert renamed.slug == f"c-{day}-richiamo-di-ottobre"
    again = service.update(created.id, CampaignPatch(nome="Richiamo di ottobre", oggetto="x"))
    assert again.slug == renamed.slug
    second = service.create(who.id, draft(nome="Altra"))
    clash = service.update(second.id, CampaignPatch(nome="Richiamo di ottobre"))
    assert clash.slug == f"c-{day}-richiamo-di-ottobre-2"
    untouched = service.update(created.id, CampaignPatch(oggetto="y"))
    assert untouched.slug == renamed.slug


@pytest.mark.parametrize(
    ("giorno", "ora", "utc"),
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
    giorno: date,
    ora: time,
    utc: datetime,
) -> None:
    """Review Focus 2."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    assert (
        service.schedule(campaign.id, ScheduleRequest(giorno=giorno, ora=ora)).programmata_per
        == utc
    )


def test_a_time_that_does_not_exist_or_has_passed_is_refused(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    with pytest.raises(ValidationFailed, match="cambio d'ora"):
        service.schedule(campaign.id, ScheduleRequest(giorno=date(2027, 3, 28), ora=time(2, 30)))
    with pytest.raises(ValidationFailed, match="già passato"):
        service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 24), ora=time(9, 30)))
    with pytest.raises(ValidationFailed, match="giorno e ora"):
        service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 30)))


def test_back_to_draft_drops_the_frozen_list_and_cancel_skips_what_is_left(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 30), ora=time(9, 30)))
    assert service.back_to_draft(campaign.id).stato == "bozza"
    assert clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == 0
    service.schedule(campaign.id, ScheduleRequest())
    cancelled = service.cancel(campaign.id)
    assert cancelled.stato == "annullata"
    assert {
        r.motivo for r in clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id)
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


def test_back_to_draft_refuses_a_campaign_already_in_invio_and_deletes_nothing(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest())
    before = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count()
    row = clean.get(Campaign, campaign.id)
    assert row is not None
    row.stato = "in_invio"
    clean.commit()
    with pytest.raises(InvalidState):
        service.back_to_draft(campaign.id)
    assert clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == before


def test_cancel_on_an_in_invio_campaign_leaves_the_sent_row_and_skips_the_queued_one(
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
    rows[0].stato, rows[0].inviata_at = "inviata", clock.at
    row = clean.get(Campaign, campaign.id)
    assert row is not None
    row.stato = "in_invio"
    clean.commit()

    cancelled = service.cancel(campaign.id)
    assert cancelled.stato == "annullata"
    refreshed = (
        clean.query(CampaignRecipient)
        .filter_by(campaign_id=campaign.id)
        .order_by(CampaignRecipient.email)
        .all()
    )
    assert refreshed[0].stato == "inviata" and refreshed[0].motivo is None
    assert refreshed[1].stato == "saltata" and refreshed[1].motivo == REASON_CANCELLED


def test_a_second_call_blocked_on_the_lock_then_sees_the_fresh_state_not_a_stale_one(
    clean: Session,  # noqa: F811  (fixture)
    hub_engine: Engine,
) -> None:
    """A true two-session race (Fix round 1, controller ruling R12, optional):
    reproduces the finding's exact shape. `clean` stands in for a concurrent actor
    (e.g. the future send loop) that has already moved the campaign to `annullata`,
    locked, uncommitted, exactly like the tick that moves a campaign to `in_invio`
    mid-way through `back_to_draft`'s read. A second `cancel()` call must block on the
    locked read and, once `clean` commits, see the FRESH state and raise `InvalidState`,
    never read the stale `programmata` and silently overwrite `clean`'s change, which
    is what `_require` (no lock) would do: the plain read does not wait, the check
    passes on stale data, and only the final commit blocks, succeeding once the lock
    is released and clobbering the concurrent write instead of refusing."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest())

    row = clean.get(Campaign, campaign.id, with_for_update=True)
    assert row is not None
    row.stato = "annullata"

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
    clean.commit()  # releases the lock, `annullata` now the committed state
    thread.join(timeout=5)
    other.close()

    assert isinstance(outcome.get("raised"), InvalidState)


def _recipient(
    session: Session, campaign_id: object, email: str, **fields: object
) -> CampaignRecipient:
    values: dict[str, object] = {
        "campaign_id": campaign_id,
        "email": email,
        "tipo": "freelancer",
        "codice": "c0de0001",
        "prima": {"t": T0.isoformat()},
        "disiscrizione_token": f"tok-{email}",
        "stato": "inviata",
        "inviata_at": T0,
    }
    values.update(fields)
    row = CampaignRecipient(**values)
    session.add(row)
    session.commit()
    return row


def test_the_counts_add_clicks_entries_and_actions(clean: Session) -> None:  # noqa: F811  (fixture)
    campaign = campaign_row(clean, stato="inviata", inviata_at=T0)
    later = T0 + timedelta(hours=1)
    _recipient(
        clean, campaign.id, "a@studio.it", primo_clic_at=later, entrato_at=later, azione_at=later
    )
    _recipient(clean, campaign.id, "b@studio.it", primo_clic_at=later, entrato_at=later)
    _recipient(clean, campaign.id, "c@studio.it", primo_clic_at=later)
    _recipient(clean, campaign.id, "d@studio.it", stato="saltata", inviata_at=None)
    counts = CampaignService(clean, SETTINGS).detail(campaign.id).conteggi
    assert (counts.inviate, counts.cliccate, counts.entrate, counts.azioni) == (3, 3, 2, 1)
    listed = CampaignService(clean, SETTINGS).list_all().items[0].conteggi
    assert listed == counts


def test_dalla_mail_needs_this_campaigns_slug_and_this_persons_code(clean: Session) -> None:  # noqa: F811  (fixture)
    """Review Focus 5: a forwarded mail carries the slug with somebody else's code."""
    campaign = campaign_row(clean, stato="inviata", inviata_at=T0, azione="entrato")
    later = T0 + timedelta(hours=1)
    ada = User(email="ada@studio.it", nome="Ada", cognome="L")
    bob = User(email="bob@studio.it", nome="Bob", cognome="L")
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
        clean, campaign.id, "ada@studio.it", codice="c0de00aa", entrato_at=later, azione_at=later
    )
    _recipient(
        clean, campaign.id, "bob@studio.it", codice="c0de00bb", entrato_at=later, azione_at=later
    )
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).destinatari}
    assert (rows["ada@studio.it"].entrato_dalla_mail, rows["ada@studio.it"].azione_dalla_mail) == (
        True,
        True,
    )
    assert (rows["bob@studio.it"].entrato_dalla_mail, rows["bob@studio.it"].azione_dalla_mail) == (
        False,
        False,
    )


def test_dalla_mail_is_the_login_that_stamped_the_entry_not_any_matching_login(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Review Focus 1: an earlier, unrelated login stamped `entrato_at`; a later login
    happens to carry this campaign's slug and this row's code. The row did not enter
    from the mail -- the stamped moment says so."""
    campaign = campaign_row(clean, stato="inviata", inviata_at=T0, azione="entrato")
    earlier = T0 + timedelta(hours=1)
    later = T0 + timedelta(hours=2)
    ada = User(email="ada@studio.it", nome="Ada", cognome="L")
    clean.add(ada)
    clean.flush()
    clean.add(Login(user_id=ada.id, logged_at=earlier))
    clean.add(
        Login(user_id=ada.id, logged_at=later, utm_campaign=campaign.slug, utm_term="c0de00aa")
    )
    clean.commit()
    _recipient(clean, campaign.id, "ada@studio.it", codice="c0de00aa", entrato_at=earlier)
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).destinatari}
    assert rows["ada@studio.it"].entrato_dalla_mail is False
    assert rows["ada@studio.it"].azione_dalla_mail is False


def test_a_card_that_carries_the_slug_was_created_from_the_mail(clean: Session) -> None:  # noqa: F811  (fixture)
    campaign = campaign_row(
        clean,
        stato="inviata",
        inviata_at=T0,
        stato_percorso="lead",
        azione="profilo_creato",
        bottone_meta="wizard",
    )
    later = T0 + timedelta(hours=1)
    giulia = User(email="giulia@studio.it", nome="Giulia", cognome="B")
    clean.add(giulia)
    clean.flush()
    # The wizard stores the button link's `utm_term` on the card (item 2, Greptile P1):
    # the row's own `codice` is `c0de0001` by `_recipient`'s default, matched here.
    clean.add(
        Freelancer(user_id=giulia.id, links=[], utm_campaign=campaign.slug, utm_term="c0de0001")
    )
    clean.commit()
    _recipient(clean, campaign.id, "giulia@studio.it", tipo="lead", azione_at=later)
    _recipient(clean, campaign.id, "nina@studio.it", tipo="lead", azione_at=later)
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).destinatari}
    assert rows["giulia@studio.it"].azione_dalla_mail is True
    assert rows["nina@studio.it"].azione_dalla_mail is False
    assert rows["giulia@studio.it"].entrato_dalla_mail is False


def test_a_card_with_the_slug_but_another_persons_code_is_not_dalla_mail(clean: Session) -> None:  # noqa: F811  (fixture)
    """Item 2 (Greptile P1): a forwarded mail carries the slug too, so the card's own
    `utm_term` must match the row's `codice`, the same rule the login check already
    applies (Review Focus 5)."""
    campaign = campaign_row(
        clean,
        stato="inviata",
        inviata_at=T0,
        stato_percorso="lead",
        azione="profilo_creato",
        bottone_meta="wizard",
    )
    later = T0 + timedelta(hours=1)
    nina = User(email="nina@studio.it", nome="Nina", cognome="B")
    clean.add(nina)
    clean.flush()
    clean.add(
        Freelancer(user_id=nina.id, links=[], utm_campaign=campaign.slug, utm_term="not-ninas-code")
    )
    clean.commit()
    _recipient(
        clean, campaign.id, "nina@studio.it", tipo="lead", codice="c0de0002", azione_at=later
    )
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).destinatari}
    assert rows["nina@studio.it"].azione_dalla_mail is False


def test_the_test_mail_greets_the_admin_in_the_subject_too(clean: Session) -> None:  # noqa: F811  (fixture)
    """REB-524: the test shows the subject as a person will read it, `{nome}` included."""
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    created = service.create(who.id, draft(oggetto="{nome}, manca solo il CV"))
    recording = RecordingCampaignSender()
    service.send_test(created.id, as_admin(who), recording)
    assert recording.sent[0].mail.subject == "[prova] Ivan, manca solo il CV"


def test_a_draft_is_deleted_and_nothing_else_is(clean: Session) -> None:  # noqa: F811  (fixture)
    """REB-524: an abandoned draft can go. It never froze a list (`back_to_draft` drops
    the one it had), so the row is all there is to delete."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    kept = service.create(admin(clean).id, draft(nome="Resta"))
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
    service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 30), ora=time(9, 30)))
    service.back_to_draft(campaign.id)
    service.delete(campaign.id)
    clean.expire_all()
    assert clean.get(Campaign, campaign.id) is None


@pytest.mark.parametrize("stato", ["programmata", "in_invio", "inviata", "annullata"])
def test_a_campaign_past_its_draft_is_never_deleted(
    clean: Session,  # noqa: F811  (fixture)
    stato: str,
) -> None:
    """A scheduled, sending, sent or cancelled campaign carries rows people received or
    were meant to: it stays, with every one of them."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest())
    row = clean.get(Campaign, campaign.id)
    assert row is not None
    row.stato = stato
    clean.commit()
    before = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count()
    with pytest.raises(InvalidState, match=ONLY_A_DRAFT_IS_DELETED):
        service.delete(campaign.id)
    clean.expire_all()
    assert clean.get(Campaign, campaign.id) is not None
    assert clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == before
