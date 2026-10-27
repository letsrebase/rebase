"""REB-835: the cards' evaluation set: its profiles and their lifecycle (`bozza` to
`accettata`), the trail that names fields and never values, the ratings, and that every
row holding a card goes with the person."""

import base64
import json
import os
from collections.abc import Callable, Iterator
from contextlib import closing
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes_cards import CARD, card_response, text_pdf
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from test_llm import Stub
from test_warehouse_contract import WAREHOUSE_TABLES

from rebase_core.ai_config import CAP_REACHED, AiConfigService
from rebase_core.card_eval import (
    AgreementRow,
    CardEvalService,
    agreement,
    draft_expected,
    format_report,
    run_eval,
)
from rebase_core.card_eval_schemas import EvalRunRead
from rebase_core.cards import NO_TEXT, PROMPT_VERSION
from rebase_core.config import Settings
from rebase_core.db import Base
from rebase_core.errors import InvalidState, LlmCapReached, NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import AiCall, RecordingCall, TaskOverride
from rebase_core.models import (
    AdminAction,
    AiTask,
    AiUsage,
    CardEvalProfile,
    CardEvalResult,
    CardEvalRun,
    FreelancerCard,
    User,
)
from rebase_core.schemas import FreelancerCreate
from rebase_core.team_schemas import Card

CV = text_pdf("Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS.")


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM card_eval_runs"))
    hub_session.execute(text("DELETE FROM admin_actions"))
    hub_session.execute(text("DELETE FROM freelancers"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def _person(session: Session, email: str = "ada@studio.it", cognome: str = "Lovelace") -> UUID:
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome=cognome,
            email=email,
            tariffa_giornaliera=Decimal("450.00"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        CV,
        "cv.pdf",
        "",
    )
    return read.id


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _card(**changes: Any) -> Card:
    return Card.model_validate({**CARD, **changes})


def _actions(session: Session) -> list[AdminAction]:
    return list(session.scalars(select(AdminAction).order_by(AdminAction.created_at)))


def _run_with_result(session: Session, freelancer_id: UUID) -> CardEvalRun:
    run = CardEvalRun(
        task="card", provider="anthropic", model="claude-opus-5", prompt_version=1, summary={}
    )
    session.add(run)
    session.flush()
    session.add(
        CardEvalResult(
            run_id=run.id,
            freelancer_id=freelancer_id,
            label="lead",
            anni_ok=True,
            seniority_ok=True,
            guard_clean=True,
            latency_ms=1200,
            cost_eur=Decimal("0.05"),
            card=CARD,
        )
    )
    session.commit()
    return run


def test_add_makes_a_bozza_and_signs_it(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)

    read = CardEvalService(clean).add(admin, person, "senza date")

    assert (read.label, read.status, read.expected, read.nome) == (
        "senza date",
        "bozza",
        None,
        "Ada",
    )
    assert [(a.kind, a.entity_type, a.entity_id) for a in _actions(clean)] == [
        ("eval_add", "card_eval", person)
    ]


def test_a_person_is_in_the_set_once_and_a_label_is_bounded(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")

    with pytest.raises(InvalidState):
        service.add(admin, person, "lead")
    with pytest.raises(ValidationFailed):
        service.ensure(person, "x" * 41)
    with pytest.raises(ValidationFailed):
        service.ensure(person, "  ")
    with pytest.raises(NotFound):
        service.add(admin, uuid4(), "lead")


def test_accept_requires_expected_card(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")

    with pytest.raises(InvalidState):
        service.accept(admin, person)

    service.set_expected(admin, person, _card())
    accepted = service.accept(admin, person)
    assert accepted.status == "accettata"
    assert accepted.corrected_by_nome == "Ivan Sala"


def test_any_edit_of_an_accepted_card_makes_it_a_bozza_again(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    service.set_expected(admin, person, _card())
    service.accept(admin, person)

    edited = service.set_expected(admin, person, _card(anni=10, seniority="lead"))

    assert edited.status == "bozza"
    assert edited.expected is not None and edited.expected.anni == 10


def test_a_draft_never_overwrites_an_accepted_profile(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    service.save_draft(person, _card())
    service.accept(admin, person)

    with pytest.raises(InvalidState):
        service.save_draft(person, _card(anni=1))


def test_the_trail_names_fields_and_never_values(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    service.set_expected(admin, person, _card())
    service.set_expected(admin, person, _card(ruolo="Data engineer", anni=3))
    service.accept(admin, person)

    trail = _actions(clean)
    assert [a.kind for a in trail] == ["eval_add", "eval_expected", "eval_expected", "eval_accept"]
    assert trail[2].payload == {"changed": ["anni", "ruolo"]}
    dump = json.dumps([a.payload for a in trail])
    for secret in ("Data engineer", "Backend developer", "Python", "Torino", "Lovelace"):
        assert secret not in dump


def test_rate_stores_one_to_three_and_nothing_else(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    run = _run_with_result(clean, person)
    service = CardEvalService(clean)

    read = service.rate(admin, run.id, {person: {"ruolo": 3, "competenze": 2}})
    assert read.results[0].ratings == {"ruolo": 3, "competenze": 2}
    # A later rating adds to what is there.
    read = service.rate(admin, run.id, {person: {"sintesi": 1}})
    assert read.results[0].ratings == {"ruolo": 3, "competenze": 2, "sintesi": 1}


@pytest.mark.parametrize("value", [0, 4, -1, True, 2.5])
def test_rate_rejects_values_outside_1_to_3(clean: Session, value: Any) -> None:
    person, admin = _person(clean), _admin(clean)
    run = _run_with_result(clean, person)

    with pytest.raises(ValidationFailed):
        CardEvalService(clean).rate(admin, run.id, {person: {"ruolo": 3, "sintesi": value}})
    clean.expire_all()
    assert clean.get(CardEvalResult, (run.id, person)).ratings is None  # type: ignore[union-attr]


def test_rate_rejects_a_field_nobody_rates_and_an_unknown_run_or_person(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    run = _run_with_result(clean, person)
    service = CardEvalService(clean)

    with pytest.raises(ValidationFailed):
        service.rate(admin, run.id, {person: {"anni": 2}})
    with pytest.raises(NotFound):
        service.rate(admin, uuid4(), {person: {"ruolo": 2}})
    with pytest.raises(NotFound):
        service.rate(admin, run.id, {uuid4(): {"ruolo": 2}})


def test_profile_deleted_with_freelancer(clean: Session) -> None:
    """The person's removal, from the database as an erasure does it, takes the expected
    card and every result written for them; the run, which holds totals only, stays."""
    person, admin = _person(clean), _admin(clean)
    other = _person(clean, "grace@studio.it", "Hopper")
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    service.set_expected(admin, person, _card())
    service.add(admin, other, "designer")
    run = _run_with_result(clean, person)

    clean.execute(text("DELETE FROM freelancers WHERE id = :id"), {"id": person})
    clean.commit()
    clean.expire_all()

    assert clean.get(CardEvalProfile, person) is None
    assert clean.get(CardEvalProfile, other) is not None
    assert clean.get(CardEvalResult, (run.id, person)) is None
    assert clean.get(CardEvalRun, run.id) is not None


def test_list_leaves_out_a_soft_deleted_person(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    _run_with_result(clean, person)
    FreelancerService(clean).soft_delete(person, admin)

    read = service.list()

    assert read.profiles == []
    assert [run.results for run in read.runs] == [[]]


def test_remove_deletes_the_profile_and_signs_it(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")

    service.remove(admin, person)

    assert service.list().profiles == []
    assert _actions(clean)[-1].kind == "eval_remove"
    with pytest.raises(NotFound):
        service.remove(admin, person)


def test_eval_table_not_in_warehouse_tables() -> None:

    ours = {name for name in Base.metadata.tables if name.startswith("card_eval_")}
    assert ours == {"card_eval_profiles", "card_eval_runs", "card_eval_results"}
    assert not ours & WAREHOUSE_TABLES


# ---- drafting the expected cards (REB-835, Task 11) ---------------------------------------

SCAN = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


def test_draft_stores_bozza_and_leaves_live_card_alone(clean: Session) -> None:

    person = _person(clean)
    CardEvalService(clean).ensure(person, "senza date")
    llm = RecordingCall([card_response()])

    read = draft_expected(clean, person, llm)

    assert (read.status, read.label, read.expected) == ("bozza", "senza date", _card())
    assert read.corrected_by_nome is None
    assert clean.get(FreelancerCard, person) is None
    assert _actions(clean) == []  # a model's draft is signed by nobody


def test_a_draft_needs_the_profile_before_it_spends_anything(clean: Session) -> None:

    person = _person(clean)
    llm = RecordingCall([card_response()])

    with pytest.raises(NotFound):
        draft_expected(clean, person, llm)
    assert llm.requests == []


def test_draft_with_cv_that_has_no_text_reports_it(clean: Session) -> None:

    read, _ = FreelancerService(clean).apply(
        FreelancerCreate(
            nome="Mario",
            cognome="Rossi",
            email="scan@studio.it",
            tariffa_giornaliera=Decimal("300.00"),
            posizione="Designer",
            remoto="remoto",
        ),
        SCAN,
        "cv.pdf",
        "",
    )
    CardEvalService(clean).ensure(read.id, "designer")

    with pytest.raises(InvalidState, match=NO_TEXT):
        draft_expected(clean, read.id, RecordingCall([]))
    assert clean.get(CardEvalProfile, read.id).expected_card is None  # type: ignore[union-attr]


def test_a_draft_does_not_overwrite_what_an_admin_corrected(clean: Session) -> None:

    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    service.set_expected(admin, person, _card(anni=12, seniority="lead"))

    with pytest.raises(InvalidState):
        draft_expected(clean, person, RecordingCall([card_response()]))
    expected = service.profile_read(person).expected
    assert expected is not None and expected.anni == 12


# ---- `rebase cards-eval` (REB-835, Task 13) -----------------------------------------------

API_KEY = "sk-ant-test-not-a-real-key"
SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()
MODEL = "claude-opus-5"
OTHER_MODEL = "claude-opus-5-5"


def _accepted(session: Session, admin: UUID, person: UUID, label: str, **changes: Any) -> None:
    service = CardEvalService(session)
    service.add(admin, person, label)
    service.set_expected(admin, person, _card(**changes))
    service.accept(admin, person)


def _run(
    session: Session, llm: Any, *, provider: str = "anthropic", model: str = MODEL
) -> EvalRunRead:
    return run_eval(
        session, lambda _person: llm, task="card", provider=provider, model=model, effort=None
    )


@pytest.mark.parametrize(
    ("expected", "got", "ok"),
    [(5, 6, True), (5, 4, True), (5, 5, True), (5, 7, False), (None, None, True), (None, 3, False)]
    + [(3, None, False)],
)
def test_anni_within_one_year_or_both_null(expected: int | None, got: int | None, ok: bool) -> None:
    row = agreement(_card(anni=expected), _card(anni=got))

    assert row.anni_ok is ok


def test_seniority_must_equal() -> None:
    assert agreement(_card(seniority="senior"), _card(seniority="senior")).seniority_ok is True
    assert agreement(_card(seniority="senior"), _card(seniority="mid")).seniority_ok is False


def test_no_card_measures_nothing_but_a_guard_refusal_is_a_failure() -> None:
    assert agreement(_card(), None, "refusal") == AgreementRow(None, None, None)
    assert agreement(_card(), None, "gendered") == AgreementRow(None, None, False)
    assert agreement(_card(), None, "identifying").guard_clean is False


def test_a_run_measures_each_accepted_profile_and_stores_the_card_it_wrote(
    clean: Session,
) -> None:
    ada, grace, admin = _person(clean), _person(clean, "grace@studio.it", "Hopper"), _admin(clean)
    _accepted(clean, admin, ada, "senza date")
    _accepted(clean, admin, grace, "lead", anni=2, seniority="junior")
    llm = RecordingCall([card_response(), card_response()])

    run = _run(clean, llm)

    by_label = {result.label: result for result in run.results}
    assert (by_label["senza date"].anni_ok, by_label["senza date"].seniority_ok) == (True, True)
    assert (by_label["lead"].anni_ok, by_label["lead"].seniority_ok) == (False, False)
    assert all(result.guard_clean for result in run.results)
    assert by_label["lead"].card == Card.model_validate(CARD)
    assert run.summary["anni_ok"] == 1 and run.summary["ran"] == 2 and run.summary["complete"]
    assert (run.prompt_version, run.provider, run.model) == (PROMPT_VERSION, "anthropic", MODEL)


def test_run_skips_draft_profiles(clean: Session) -> None:
    ada, grace, admin = _person(clean), _person(clean, "grace@studio.it", "Hopper"), _admin(clean)
    _accepted(clean, admin, ada, "lead")
    CardEvalService(clean).add(admin, grace, "designer")
    CardEvalService(clean).set_expected(admin, grace, _card())  # corrected, not accepted
    llm = RecordingCall([card_response()])

    run = _run(clean, llm)

    assert [result.freelancer_id for result in run.results] == [ada]
    assert len(llm.requests) == 1


def test_run_skips_a_soft_deleted_person(clean: Session) -> None:
    ada, grace, admin = _person(clean), _person(clean, "grace@studio.it", "Hopper"), _admin(clean)
    _accepted(clean, admin, ada, "lead")
    _accepted(clean, admin, grace, "duplicato")
    FreelancerService(clean).soft_delete(grace, admin)

    run = _run(clean, RecordingCall([card_response()]))

    assert [result.freelancer_id for result in run.results] == [ada]


def test_guard_failure_counted(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    _accepted(clean, admin, person, "lead")
    gendered = {**CARD, "sintesi": "Lei è una sviluppatrice con nove anni di esperienza."}

    run = _run(clean, RecordingCall([card_response(gendered), card_response(gendered)]))

    [result] = run.results
    assert (result.failure, result.guard_clean, result.card) == ("gendered", False, None)
    assert (run.summary["guard_failures"], run.summary["failed"]) == (1, 1)
    assert run.summary["guard_clean"] == 0


def test_run_without_an_accepted_profile_is_refused_before_any_call(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    CardEvalService(clean).add(admin, person, "lead")
    llm = RecordingCall([])

    with pytest.raises(InvalidState):
        _run(clean, llm)
    assert clean.scalars(select(CardEvalRun)).all() == []


def test_run_does_not_change_ai_tasks_or_live_cards(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    _accepted(clean, admin, person, "lead")
    tasks = clean.execute(text("SELECT * FROM ai_tasks ORDER BY task")).all()

    _run(clean, RecordingCall([card_response()]), provider="openai", model="gpt-5")

    assert clean.execute(text("SELECT * FROM ai_tasks ORDER BY task")).all() == tasks
    assert clean.scalars(select(FreelancerCard)).all() == []


def test_a_cap_or_an_outage_stops_the_run_and_it_is_never_a_baseline(clean: Session) -> None:
    ada, grace, admin = _person(clean), _person(clean, "grace@studio.it", "Hopper"), _admin(clean)
    _accepted(clean, admin, ada, "a")
    _accepted(clean, admin, grace, "b")

    class Spent:
        calls = 0

        def complete(self, request: object) -> object:
            Spent.calls += 1
            raise LlmCapReached(CAP_REACHED)

    run = _run(clean, Spent())

    assert Spent.calls == 1
    assert run.summary["complete"] is False and run.summary["stopped"] == CAP_REACHED
    assert [result.failure for result in run.results] == ["unavailable"]
    # The next finished run has no baseline to be compared with: this one is not one.
    again = _run(clean, RecordingCall([card_response(), card_response()]))
    assert again.summary["baseline"] is None and again.summary["complete"]
    assert "base" in format_report(again)


def test_report_marks_regression_against_baseline(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    _accepted(clean, admin, person, "lead")
    first = _run(clean, RecordingCall([card_response()]))
    assert first.summary["baseline"] is None

    worse = _run(
        clean,
        RecordingCall([card_response({**CARD, "anni": 3, "seniority": "mid"})]),
        provider="openai",
        model="gpt-5",
    )
    same = _run(clean, RecordingCall([card_response({**CARD, "anni": 3, "seniority": "mid"})]))

    baseline = worse.summary["baseline"]
    assert baseline["run_id"] == str(first.id)
    assert baseline["delta"] == {
        "anni_ok": "peggio",
        "seniority_ok": "peggio",
        "guard_clean": "uguale",
    }
    assert same.summary["baseline"]["delta"] == {
        "anni_ok": "uguale",
        "seniority_ok": "uguale",
        "guard_clean": "uguale",
    }
    report = format_report(worse)
    assert "anni peggio, seniority peggio, guardia uguale" in report
    assert "openai/gpt-5" in report


def test_the_report_waits_for_ratings_and_carries_no_name_or_card_text(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    _accepted(clean, admin, person, "senza date")
    run = _run(clean, RecordingCall([card_response()]))

    before = format_report(run)
    assert before.count("da valutare") == 3
    CardEvalService(clean).rate(admin, run.id, {person: {"ruolo": 3, "competenze": 2}})
    after = format_report(CardEvalService(clean).run_read(clean.get(CardEvalRun, run.id)))  # type: ignore[arg-type]

    assert after.count("da valutare") == 1
    for private in ("Lovelace", "Ada", "Backend developer", "Python", "Torino", "fintech"):
        assert private not in before + after
    assert "senza date" in after and str(person)[:8] in after


@pytest.fixture
def ai_ready(hub_engine: Engine) -> Iterator[Callable[[], Any]]:
    """The card task callable: key saved, Anthropic on, a price for two models. Wiped back
    to what migration 0036 seeded."""

    def open_session() -> Any:
        return closing(Session(hub_engine))

    settings = Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]
    with open_session() as session:
        admin = User(email="root@rebase.it", nome="Root", cognome="Admin", role="admin")
        session.add(admin)
        session.commit()
        service = AiConfigService(session, settings)
        service.save_key(admin.id, "anthropic", API_KEY)
        service.set_provider_enabled(admin.id, "anthropic", True)
        service.upsert_price(admin.id, "anthropic", MODEL, Decimal("5"), Decimal("25"))
        service.upsert_price(admin.id, "anthropic", OTHER_MODEL, Decimal("2"), Decimal("10"))
    yield open_session
    with open_session() as session:
        for statement in (
            "DELETE FROM ai_usage",
            "DELETE FROM ai_prices",
            "UPDATE ai_providers SET key_ciphertext = NULL, key_nonce = NULL, "
            "key_last4 = NULL, saved_by = NULL, saved_at = NULL, enabled = false",
            "UPDATE ai_tasks SET provider = 'anthropic', model = 'claude-opus-5', "
            "effort = 'medium', daily_cap_eur = NULL, enabled = true",
        ):
            session.execute(text(statement))
        session.commit()


def test_eval_cost_recorded_as_card_task_with_eval_purpose_and_counts_in_spent_today(
    clean: Session, ai_ready: Callable[[], Any]
) -> None:
    person, admin = _person(clean), _admin(clean)
    _accepted(clean, admin, person, "lead")
    settings = Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]
    stub = Stub(Stub.reply(json.dumps(CARD), input_tokens=1000, output_tokens=200))

    def llm_for(freelancer_id: UUID) -> AiCall:
        return AiCall(
            ai_ready,
            settings,
            "card",
            override=TaskOverride("anthropic", OTHER_MODEL, "high"),
            purpose="eval",
            freelancer_id=freelancer_id,
            model_factory=stub.factory,
        )

    run = run_eval(
        clean, llm_for, task="card", provider="anthropic", model=OTHER_MODEL, effort="high"
    )

    [usage] = clean.scalars(select(AiUsage)).all()
    assert (usage.task, usage.purpose, usage.model, usage.freelancer_id) == (
        "card",
        "eval",
        OTHER_MODEL,
        person,
    )
    # 1000 input at 2 and 200 output at 10 euro per million tokens.
    assert usage.cost_eur == Decimal("0.004000")
    [result] = run.results
    assert result.cost_eur == Decimal("0.004000") and result.latency_ms is not None
    assert Decimal(run.summary["cost_eur"]) == Decimal("0.004")
    assert (run.provider, run.model, run.effort) == ("anthropic", OTHER_MODEL, "high")
    with ai_ready() as session:
        assert AiConfigService(session, settings).spent_today("card") == Decimal("0.004")
        # The task's own settings stay what an admin chose.
        task = session.get(AiTask, "card")
        assert task is not None and (task.model, task.effort) == (MODEL, "medium")


def test_eval_spend_stops_at_the_tasks_daily_cap_and_says_why(
    clean: Session, ai_ready: Callable[[], Any]
) -> None:
    person, admin = _person(clean), _admin(clean)
    _accepted(clean, admin, person, "lead")
    settings = Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]
    with ai_ready() as session:
        task = session.get(AiTask, "card")
        assert task is not None
        task.daily_cap_eur = Decimal("0")
        session.commit()
    stub = Stub()

    run = run_eval(
        clean,
        lambda fid: AiCall(
            ai_ready,
            settings,
            "card",
            override=TaskOverride("anthropic", OTHER_MODEL, None),
            purpose="eval",
            freelancer_id=fid,
            model_factory=stub.factory,
        ),
        task="card",
        provider="anthropic",
        model=OTHER_MODEL,
        effort=None,
    )

    assert stub.built == []
    assert run.summary["complete"] is False and run.summary["stopped"] == CAP_REACHED


def test_fifteen_profiles_are_drafted_corrected_accepted_and_measured(clean: Session) -> None:
    """The whole path on a test database (the card's «Done when»): fifteen people drafted
    as `bozza`, an admin accepts them, and one run reports the agreement and the cost."""
    admin = _admin(clean)
    people = [_person(clean, f"persona{index}@studio.it", f"Cognome{index}") for index in range(15)]
    service = CardEvalService(clean)
    drafts = RecordingCall([card_response() for _ in people])
    for person in people:
        service.ensure(person, "senza date")
        draft_expected(clean, person, drafts)

    assert [profile.status for profile in service.list().profiles] == ["bozza"] * 15
    for person in people:
        service.accept(admin, person)
    run = _run(clean, RecordingCall([card_response() for _ in people]))

    assert (run.summary["ran"], run.summary["anni_ok"], run.summary["guard_clean"]) == (15, 15, 15)
    report = format_report(run)
    assert report.count("senza date") == 15
