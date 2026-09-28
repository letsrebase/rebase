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
from sqlalchemy import event
from sqlalchemy.orm import Session

import rebase_core.campaigns.audience as audience_module
from rebase_core.campaigns.actions import done_at, snapshot
from rebase_core.campaigns.audience import (
    REASON_ADMIN,
    REASON_BOUNCED,
    REASON_NEVER,
    build_audience,
    candidates,
    exclusions,
    waiting_rows,
)
from rebase_core.campaigns.schemas import ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.states import JOURNEY_STATES, PHASE_ONE_STATES, candidates_for_state
from rebase_core.campaigns.templates import STATE_TEMPLATES
from rebase_core.comments import CommentService
from rebase_core.models import (
    CAMPAIGN_ACTIONS,
    CAMPAIGN_DESTINATIONS,
    CampaignOptout,
    CampaignRecipient,
    Login,
)


def test_every_phase_one_state_has_a_template_that_fits_the_columns() -> None:
    assert set(STATE_TEMPLATES) == set(PHASE_ONE_STATES)
    for key, template in STATE_TEMPLATES.items():
        assert template.etichetta == JOURNEY_STATES[key]
        assert template.azione in CAMPAIGN_ACTIONS and template.azione != "pigro_cliente"
        assert template.bottone_meta in CAMPAIGN_DESTINATIONS and template.bottone_meta != "pigro"
        assert template.testo.startswith("Ciao {nome},")
        assert len(template.oggetto) <= 200 and len(template.bottone_testo) <= 60


def recipient_for(session: Session, candidate, prima: dict) -> CampaignRecipient:  # type: ignore[no-untyped-def]
    return CampaignRecipient(
        email=candidate.email,
        tipo=candidate.tipo,
        user_id=candidate.user_id,
        freelancer_id=candidate.freelancer_id,
        signup_id=candidate.signup_id,
        codice="00000000",
        prima=prima,
        disiscrizione_token="t",
    )


def test_a_cv_uploaded_after_the_snapshot_counts_and_one_from_before_does_not(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    card = person(clean, "nocv@studio.it", cv=False)
    candidate = candidates_for_state(clean, "manca_cv")[0]
    prima = snapshot(clean, candidate, T0)
    assert prima["ha_cv"] is False and prima["ha_scheda"] is True
    row = recipient_for(clean, candidate, prima)
    assert done_at(clean, row, "cv") is None
    card.cv_size, card.cv_filename, card.cv_bytes = 4, "cv.pdf", b"%PDF"
    clean.commit()
    CommentService(clean).add("freelancer", card.id, "CV caricato dalla persona", "Ada Lovelace")
    assert done_at(clean, row, "cv") is not None


def test_a_login_after_the_snapshot_is_entered(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "done@studio.it")
    candidate = candidates_for_state(clean, "completo")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "entrato") is None
    clean.add(Login(user_id=candidate.user_id, logged_at=T0 + timedelta(minutes=5)))
    clean.commit()
    assert done_at(clean, row, "entrato") == T0 + timedelta(minutes=5)


def test_a_lead_who_makes_a_card_has_created_a_profile(clean: Session) -> None:  # noqa: F811  (fixture)
    lead(clean, "giulia@studio.it")
    candidate = candidates_for_state(clean, "lead")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0 - timedelta(days=1)))
    assert done_at(clean, row, "profilo_creato") is None
    person(clean, "giulia@studio.it", nome="Giulia")
    assert done_at(clean, row, "profilo_creato") is not None


def test_any_of_a_referentes_open_requests_updated_counts(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    first = company(clean, "info@block-buy.it")
    company(clean, "info@block-buy.it", stato="contattato")
    candidate = candidates_for_state(clean, "azienda_aperta")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, datetime.now(UTC)))
    assert done_at(clean, row, "richiesta_aggiornata") is None
    first.durata = "18 mesi"
    clean.commit()
    assert done_at(clean, row, "richiesta_aggiornata") is not None


def test_the_pigro_action_is_never_done_in_phase_one(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "done@studio.it")
    candidate = candidates_for_state(clean, "completo")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "pigro_cliente") is None


def test_an_address_with_no_card_never_has_done_cv_or_scheda_completa(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    lead(clean, "nocard@studio.it")
    candidate = candidates_for_state(clean, "lead")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "cv") is None
    assert done_at(clean, row, "scheda_completa") is None


def test_a_card_soft_deleted_after_completing_counts_as_not_done(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    card = person(clean, "gone@studio.it", cv=False)
    candidate = candidates_for_state(clean, "manca_cv")[0]
    prima = snapshot(clean, candidate, T0)
    assert prima["ha_cv"] is False and prima["completa"] is False
    row = recipient_for(clean, candidate, prima)
    card.cv_size, card.cv_filename, card.cv_bytes = 4, "cv.pdf", b"%PDF"
    card.deleted_at = datetime.now(UTC)
    clean.commit()
    assert done_at(clean, row, "cv") is None
    assert done_at(clean, row, "scheda_completa") is None


def test_filters_reuse_talenti_and_merge_one_person_across_cases(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    person(clean, "ada@studio.it", tariffa=False)
    lead(clean, "giulia@studio.it")
    campaign = campaign_row(
        clean, fonte="filtri", stato_percorso=None, filtri={"lista": "talenti", "has_cv": True}
    )
    assert [c.email for c in candidates(clean, campaign)] == ["ada@studio.it"]
    campaign.filtri = {"lista": "talenti", "stato": "lead"}
    clean.commit()
    assert [c.email for c in candidates(clean, campaign)] == ["giulia@studio.it"]


def test_every_exclusion_names_its_reason(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "boss@rebase.it", role="admin", cv=False)
    person(clean, "gone@studio.it", cv=False)
    person(clean, "never@studio.it", cv=False)
    person(clean, "bounce@studio.it", cv=False)
    person(clean, "recent@studio.it", cv=False)
    person(clean, "ok@studio.it", cv=False)
    clean.add(CampaignOptout(email="gone@studio.it", fonte="link"))
    clean.add(CampaignOptout(email="never@studio.it", fonte="admin"))
    earlier = campaign_row(clean)
    clean.add(
        CampaignRecipient(
            campaign_id=earlier.id,
            email="bounce@studio.it",
            tipo="freelancer",
            codice="1",
            prima={},
            disiscrizione_token="b",
            stato="inviata",
            inviata_at=T0 - timedelta(days=30),
            rimbalzata_at=T0 - timedelta(days=30),
        )
    )
    clean.add(
        CampaignRecipient(
            campaign_id=earlier.id,
            email="recent@studio.it",
            tipo="freelancer",
            codice="2",
            prima={},
            disiscrizione_token="r",
            stato="inviata",
            inviata_at=T0 - timedelta(days=1),
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
    assert [(r.candidate.email, r.escluso) for r in rows] == [
        ("boss@rebase.it", REASON_ADMIN),
        ("ok@studio.it", None),
    ]


def test_the_aziende_filter_merges_open_requests_and_drops_the_closed_one(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    first = company(clean, "info@block-buy.it", stato="nuovo")
    second = company(clean, "info@block-buy.it", stato="nuovo")
    company(clean, "info@block-buy.it", stato="chiuso")
    campaign = campaign_row(
        clean, fonte="filtri", stato_percorso=None, filtri={"lista": "aziende", "stato": "nuovo"}
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
    campaign = campaign_row(clean, fonte="filtri", stato_percorso=None, filtri={"lista": "talenti"})
    rows = build_audience(clean, campaign, now=T0, gap_days=3)
    assert [(r.candidate.email, r.escluso) for r in rows] == [("ada@studio.it", None)]


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
    person(clean, "empty@studio.it", cv=False, tariffa=False)
    filtered = campaign_row(
        clean, fonte="filtri", stato_percorso=None, filtri={"lista": "talenti", "has_cv": True}
    )
    with statements(clean) as seen:
        for state in ("manca_cv", "completo", "scheda_vuota_nuovi", "scheda_vuota_entrati"):
            candidates_for_state(clean, state)
        candidate = candidates_for_state(clean, "manca_cv")[0]
        row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
        for azione in ("cv", "scheda_completa", "profilo_creato"):
            done_at(clean, row, azione)
        assert [c.email for c in candidates(clean, filtered)] == ["done@studio.it"]
    assert seen
    assert not [sql for sql in seen if "cv_bytes" in sql]


def test_a_row_with_a_broken_richieste_snapshot_is_left_out_and_logged(
    clean: Session,  # noqa: F811  (fixture)
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Part 2 (REB-550): a malformed `prima["richieste"]` timestamp makes `done_at`'s own
    `datetime.fromisoformat` raise for that one row. Stamping already isolates a row like
    it per savepoint (REB-533, `outcome.py`); `waiting_rows` does the same, leaving the
    row out -- the safe side, since it never lets a mail reach someone who may already
    have acted -- and logging it by id, rather than 500ing the whole «Riscrivi» list."""
    # `hub_engine`'s Alembic `env.py` calls `fileConfig`, which disables every logger
    # that already existed (the trap `test_campaign_outcome.py`'s
    # `test_a_row_whose_stamping_raises_is_skipped_the_rest_still_stamped` documents):
    # undo it so `caplog` sees this module's line.
    logging.getLogger("rebase_core.campaigns.audience").disabled = False
    parent = campaign_row(clean, azione="richiesta_aggiornata")
    fine = company(clean, "fine@studio.it")
    good = CampaignRecipient(
        campaign_id=parent.id,
        email="fine@studio.it",
        tipo="azienda",
        codice="1",
        prima={"richieste": {str(fine.id): fine.updated_at.isoformat()}},
        disiscrizione_token="t-good",
        stato="inviata",
        inviata_at=T0,
    )
    rotta = company(clean, "rotta@studio.it")
    broken = CampaignRecipient(
        campaign_id=parent.id,
        email="rotta@studio.it",
        tipo="azienda",
        codice="2",
        prima={"richieste": {str(rotta.id): "not-a-date"}},
        disiscrizione_token="t-broken",
        stato="inviata",
        inviata_at=T0,
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
    parent = campaign_row(clean, azione="richiesta_aggiornata", stato="inviata")
    fine = company(clean, "fine2@studio.it")
    clean.add(
        CampaignRecipient(
            campaign_id=parent.id,
            email="fine2@studio.it",
            tipo="azienda",
            codice="1",
            prima={"richieste": {str(fine.id): fine.updated_at.isoformat()}},
            disiscrizione_token="t-good2",
            stato="inviata",
            inviata_at=T0,
        )
    )
    rotta = company(clean, "rotta2@studio.it")
    clean.add(
        CampaignRecipient(
            campaign_id=parent.id,
            email="rotta2@studio.it",
            tipo="azienda",
            codice="2",
            prima={"richieste": {str(rotta.id): "not-a-date"}},
            disiscrizione_token="t-broken2",
            stato="inviata",
            inviata_at=T0,
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
    assert [r.email for r in preview.righe] == ["fine2@studio.it"]
    clock.at += timedelta(minutes=1)
    service.send_test(follow.id, as_admin(who), RecordingCampaignSender())
    scheduled = service.schedule(follow.id, ScheduleRequest())
    assert scheduled.stato == "programmata"


def test_a_non_parse_error_from_done_at_propagates_instead_of_dropping_the_row(
    clean: Session,  # noqa: F811  (fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Greptile P1 on PR #444: only a malformed snapshot's own parse errors are caught
    (`ValueError`, `TypeError`, `KeyError`); a transient database error or an unrelated
    bug must propagate rather than silently drop an eligible person from the list."""
    parent = campaign_row(clean, azione="richiesta_aggiornata")
    fine = company(clean, "fine3@studio.it")
    clean.add(
        CampaignRecipient(
            campaign_id=parent.id,
            email="fine3@studio.it",
            tipo="azienda",
            codice="1",
            prima={"richieste": {str(fine.id): fine.updated_at.isoformat()}},
            disiscrizione_token="t-good3",
            stato="inviata",
            inviata_at=T0,
        )
    )
    clean.commit()

    def raising(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(audience_module, "done_at", raising)
    with pytest.raises(RuntimeError, match="boom"):
        waiting_rows(clean, parent)


def test_a_row_whose_richieste_is_not_a_string_keyed_dict_is_left_out_and_logged(
    clean: Session,  # noqa: F811  (fixture)
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CodeRabbit Major on PR #444: `{"richieste": [123]}` made `UUID(123)` raise
    `AttributeError`, breaking the whole list before the row could be left out. The
    shape is checked explicitly instead of widening the `except`, so a real bug
    elsewhere is not swallowed along with it."""
    logging.getLogger("rebase_core.campaigns.audience").disabled = False
    parent = campaign_row(clean, azione="richiesta_aggiornata")
    fine = company(clean, "fine4@studio.it")
    good = CampaignRecipient(
        campaign_id=parent.id,
        email="fine4@studio.it",
        tipo="azienda",
        codice="1",
        prima={"richieste": {str(fine.id): fine.updated_at.isoformat()}},
        disiscrizione_token="t-good4",
        stato="inviata",
        inviata_at=T0,
    )
    broken = CampaignRecipient(
        campaign_id=parent.id,
        email="rotta4@studio.it",
        tipo="azienda",
        codice="2",
        prima={"richieste": [123]},
        disiscrizione_token="t-broken4",
        stato="inviata",
        inviata_at=T0,
    )
    clean.add_all([good, broken])
    clean.commit()
    with caplog.at_level(logging.ERROR, logger="rebase_core.campaigns.audience"):
        rows = waiting_rows(clean, parent)
    assert [r.email for r in rows] == ["fine4@studio.it"]
    assert str(broken.id) in caplog.text


@pytest.mark.parametrize("bad_prima", [["not", "a", "dict"], "not-a-dict"])
def test_a_row_whose_prima_is_not_a_dict_is_left_out_and_logged(
    clean: Session,  # noqa: F811  (fixture)
    caplog: pytest.LogCaptureFixture,
    bad_prima: object,
) -> None:
    """The same shape check applies to `prima` itself, not only `richieste`: a row
    whose snapshot is a list or a string, not a dict, must not raise inside `.get`."""
    logging.getLogger("rebase_core.campaigns.audience").disabled = False
    parent = campaign_row(clean, azione="richiesta_aggiornata")
    fine = company(clean, "fine5@studio.it")
    good = CampaignRecipient(
        campaign_id=parent.id,
        email="fine5@studio.it",
        tipo="azienda",
        codice="1",
        prima={"richieste": {str(fine.id): fine.updated_at.isoformat()}},
        disiscrizione_token="t-good5",
        stato="inviata",
        inviata_at=T0,
    )
    broken = CampaignRecipient(
        campaign_id=parent.id,
        email="rotta5@studio.it",
        tipo="azienda",
        codice="2",
        prima=bad_prima,  # type: ignore[arg-type]
        disiscrizione_token="t-broken5",
        stato="inviata",
        inviata_at=T0,
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
    parent = campaign_row(clean, azione="richiesta_aggiornata")
    fine = company(clean, "fine6@studio.it")
    clean.add(
        CampaignRecipient(
            campaign_id=parent.id,
            email="fine6@studio.it",
            tipo="azienda",
            codice="1",
            prima={"richieste": {str(fine.id): fine.updated_at.isoformat()}},
            disiscrizione_token="t-good6",
            stato="inviata",
            inviata_at=T0,
        )
    )
    clean.commit()

    def raising(*args: object, **kwargs: object) -> None:
        raise ValueError("not this row's own snapshot")

    monkeypatch.setattr(audience_module, "done_at", raising)
    with pytest.raises(ValueError, match="not this row's own snapshot"):
        waiting_rows(clean, parent)
