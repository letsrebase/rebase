"""REB-842: the cards' automatic evaluation: the fixed sample, a run that writes a card and
cross-checks it against its sources (the rules, then a judging call), what is stored and
never stored, what is spent and where it is recorded, and how a run stands against its
baseline."""

import base64
import json
import os
from collections.abc import Callable, Iterator
from contextlib import closing
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes_cards import (
    CARD,
    FONTI,
    card_response,
    draft_body,
    judge_response,
    judgement_body,
    text_pdf,
)
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from test_llm import Stub
from test_warehouse_contract import WAREHOUSE_TABLES

from rebase_core.ai_config import CAP_REACHED, AiConfigService
from rebase_core.card_eval import (
    INTERRUPTED,
    OUTSIDE_SAMPLE,
    Target,
    aggregate,
    compare,
    format_report,
    named,
    results_of,
    run_eval,
    sample,
)
from rebase_core.cards import PROMPT_VERSION
from rebase_core.config import Settings
from rebase_core.db import Base
from rebase_core.errors import InvalidState, LlmUnavailable, NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import AiCall, LlmResponse, RecordingCall, TaskOverride
from rebase_core.models import (
    AiTask,
    AiUsage,
    CardEvalProfile,
    CardEvalResult,
    CardEvalRun,
    Freelancer,
    FreelancerCard,
    User,
)
from rebase_core.schemas import FreelancerCreate
from rebase_core.team_schemas import Card

# A CV that dates its one role: nine years to this autumn at 2017 - 2025 (the card's 9).
CV = text_pdf("Backend developer, Acme, 2017 - 2025. Python, FastAPI, AWS, a Torino.")
# One that dates nothing, as the card of nine years would then be a guess.
UNDATED_CV = text_pdf("Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI.")
MODEL = "claude-opus-5"
OTHER_MODEL = "claude-opus-5-5"
API_KEY = "sk-ant-test-not-a-real-key"
SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for statement in (
        "DELETE FROM card_eval_runs",
        "DELETE FROM ai_usage",
        "DELETE FROM admin_actions",
        "DELETE FROM freelancers",
        "DELETE FROM users",
    ):
        hub_session.execute(text(statement))
    hub_session.commit()


def _person(
    session: Session,
    email: str = "ada@studio.it",
    cognome: str = "Lovelace",
    cv: bytes = CV,
    label: str | None = None,
) -> UUID:
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome=cognome,
            email=email,
            tariffa_giornaliera=Decimal("450.00"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        cv,
        "cv.pdf",
        "",
    )
    if label is not None:
        session.add(CardEvalProfile(freelancer_id=read.id, label=label))
        session.commit()
    return read.id


def _card(**changes: Any) -> dict[str, Any]:
    return {**CARD, **changes}


def _bare_card_response(card: dict[str, Any] | None = None) -> LlmResponse:
    """An answer under prompt version 1: the card alone, no `fonti`, no employers."""
    body = json.dumps(card or CARD)
    return LlmResponse(
        text=body,
        parsed=Card.model_validate_json(body),
        stop_reason="end_turn",
        refusal_category=None,
        model=MODEL,
        input_tokens=900,
        output_tokens=150,
        cache_read_tokens=0,
    )


def _run(
    session: Session,
    writer: Any,
    judging: Any,
    targets: list[Target] | None = None,
    *,
    provider: str = "anthropic",
    model: str = MODEL,
    prompt_version: int = PROMPT_VERSION,
    baseline_run_id: UUID | None = None,
    judge_by: tuple[str, str] = ("anthropic", MODEL),
    effort: str | None = "medium",  # the card task's, as migration 0036 seeds it
) -> CardEvalRun:
    return run_eval(
        session,
        lambda _person: writer,
        lambda _person: judging,
        task="card",
        provider=provider,
        model=model,
        effort=effort,
        targets=targets if targets is not None else sample(session),
        prompt_version=prompt_version,
        judge_by=judge_by,
        baseline_run_id=baseline_run_id,
    )


# ---- the sample -----------------------------------------------------------------------------


def test_the_sample_is_the_people_in_it_by_label_and_a_smaller_one_spans_the_labels(
    clean: Session,
) -> None:
    people = {
        "senza date": [
            _person(clean, f"a{n}@studio.it", f"Uno{n}", label="senza date") for n in (1, 2, 3)
        ],
        "GitHub": [_person(clean, f"b{n}@studio.it", f"Due{n}", label="GitHub") for n in (1, 2)],
        "lead": [_person(clean, "c1@studio.it", "Tre1", label="lead")],
    }

    everyone = sample(clean)
    three = sample(clean, 3)
    four = sample(clean, 4)

    assert len(everyone) == 6
    assert [t.label for t in everyone] == sorted(t.label for t in everyone)
    # Three people are one of each failure, not three of the first.
    assert sorted(t.label for t in three) == ["GitHub", "lead", "senza date"]
    assert sorted(t.label for t in four) == ["GitHub", "GitHub", "lead", "senza date"]
    # The same N is always the same people, in the same order.
    assert sample(clean, 4) == four
    assert {t.freelancer_id for t in four} <= {p for group in people.values() for p in group}
    assert sample(clean, 99) == everyone


def test_the_sample_leaves_out_a_deleted_person_and_is_refused_when_empty_or_zero(
    clean: Session,
) -> None:
    with pytest.raises(InvalidState):
        sample(clean)
    ada = _person(clean, label="lead")
    grace = _person(clean, "grace@studio.it", "Hopper", label="duplicato")
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    clean.add(admin)
    clean.commit()
    FreelancerService(clean).soft_delete(grace, admin.id)

    assert [t.freelancer_id for t in sample(clean)] == [ada]
    with pytest.raises(ValidationFailed):
        sample(clean, 0)


def test_named_people_keep_their_label_or_are_outside_the_sample(clean: Session) -> None:
    ada = _person(clean, label="lead")
    grace = _person(clean, "grace@studio.it", "Hopper")

    assert named(clean, [ada, grace, ada]) == [Target(ada, "lead"), Target(grace, OUTSIDE_SAMPLE)]
    with pytest.raises(NotFound):
        named(clean, [uuid4()])
    with pytest.raises(InvalidState):
        named(clean, [])


def test_the_sample_and_its_results_go_with_the_person_and_hold_no_expected_card(
    clean: Session,
) -> None:
    person = _person(clean, label="lead")
    run = _run(clean, RecordingCall([card_response()]), RecordingCall([judge_response()]))
    assert clean.get(CardEvalProfile, person) is not None
    assert len(results_of(clean, run.id)) == 1

    clean.execute(text("DELETE FROM freelancers WHERE id = :f"), {"f": person})
    clean.commit()

    assert clean.scalars(select(CardEvalProfile)).all() == []
    assert clean.scalars(select(CardEvalResult)).all() == []
    # The run itself, which holds only totals, stays.
    assert clean.scalars(select(CardEvalRun)).one().id == run.id
    assert {c.name for c in CardEvalProfile.__table__.columns} == {
        "freelancer_id",
        "label",
        "created_at",
    }


def test_eval_tables_are_not_in_warehouse_tables() -> None:
    ours = {name for name in Base.metadata.tables if name.startswith("card_eval_")}
    assert ours == {"card_eval_profiles", "card_eval_runs", "card_eval_results"}
    assert not ours & WAREHOUSE_TABLES


# ---- one run --------------------------------------------------------------------------------


def test_a_run_writes_checks_and_judges_each_card_and_stores_no_live_card(clean: Session) -> None:
    person = _person(clean, label="senza date")
    writer = RecordingCall([card_response()])
    judging = RecordingCall(
        [
            judge_response(
                {"competenze": ("unsupported", "no source says AWS")},
                invented=["AWS"],
                identifying=["Acme"],
            )
        ]
    )

    run = _run(clean, writer, judging)

    [result] = results_of(clean, run.id)
    assert result.freelancer_id == person and result.label == "senza date"
    assert result.card == _card() and result.failure is None
    # The rules: the years are the CV's 2017 - 2025 (8 to 9 years), no identifier is left.
    assert result.checks == {
        "years": {"mode": "recomputed", "ok": True, "low": 8, "high": 9},
        "anonymity": [],
    }
    assert (result.anni_ok, result.guard_clean) == (True, True)
    # The judge's verdicts, and a rule's over the judge's on the years.
    assert result.judgement is not None
    fields = result.judgement["fields"]
    assert fields["competenze"] == {
        "verdict": "unsupported",
        "reason": "no source says AWS",
        "by": "judge",
    }
    assert fields["anni"]["by"] == "rule" and fields["anni"]["verdict"] == "supported"
    assert fields["ruolo"]["by"] == "judge"
    assert result.judgement["invented"] == ["AWS"]
    assert result.judgement["identifying"] == ["Acme"]
    # One call to write, one to judge, and nothing written to the live card.
    assert len(writer.requests) == 1 and len(judging.requests) == 1
    assert clean.scalars(select(FreelancerCard)).all() == []
    totals = run.summary
    assert (totals["format"], totals["ran"], totals["judged"], totals["complete"]) == (
        2,
        1,
        1,
        True,
    )
    assert (totals["fields"], totals["supported"], totals["unsupported"]) == (8, 7, 1)
    assert (totals["contradicted"], totals["invented"], totals["leaks_judge"]) == (0, 1, 1)
    assert totals["judge"] == {"provider": "anthropic", "model": MODEL}
    assert (run.prompt_version, run.provider, run.model) == (PROMPT_VERSION, "anthropic", MODEL)


def test_the_judge_is_given_the_card_and_what_the_card_was_written_from(clean: Session) -> None:
    _person(clean, label="lead")
    judging = RecordingCall([judge_response()])

    _run(clean, RecordingCall([card_response()]), judging)

    [request] = judging.requests
    message = request.messages[0].text
    assert "<card>" in message and '"ruolo": "Backend developer"' in message
    assert "Backend developer, Acme, 2017 - 2025" in message  # the CV, as data


def test_a_rule_settles_the_years_over_a_judge_who_says_supported(clean: Session) -> None:
    """The CV dates nothing, so nine years are a guess whatever the judge says (REB-799)."""
    _person(clean, cv=UNDATED_CV, label="senza date")

    run = _run(clean, RecordingCall([card_response()]), RecordingCall([judge_response()]))

    [result] = results_of(clean, run.id)
    assert result.anni_ok is False
    assert result.checks is not None and result.checks["years"]["mode"] == "undated"
    assert result.judgement is not None
    assert result.judgement["fields"]["anni"]["verdict"] == "contradicted"
    assert result.judgement["fields"]["anni"]["by"] == "rule"
    assert run.summary["contradicted"] == 1 and run.summary["contradicted_profiles"] == 1
    assert (run.summary["anni_checked"], run.summary["anni_ok"]) == (1, 0)


def test_an_identifier_of_the_person_left_in_a_card_is_found_by_the_rules(clean: Session) -> None:
    """Prompt version 1 never read the person's links, so its card may name them: the rules
    read every prompt against everything the person's data holds."""
    person = _person(clean, label="GitHub")
    row = clean.get(Freelancer, person)
    assert row is not None
    row.links = ["https://ada-engine.dev/me"]
    clean.commit()
    leaky = _card(sintesi="Ha fondato ada-engine, una libreria per i calcoli, nove anni di Python.")

    run = _run(
        clean,
        RecordingCall([_bare_card_response(leaky)]),
        RecordingCall([judge_response()]),
        prompt_version=1,
    )

    [result] = results_of(clean, run.id)
    assert result.checks is not None and result.checks["anonymity"] == ["sintesi"]
    assert (run.summary["leaks_rule"], run.summary["leaks"]) == (1, 1)
    assert "anonima" in format_report(run, [result]) and "no" in format_report(run, [result])


def test_prompt_version_1_writes_from_the_cv_alone_and_takes_a_bare_card(clean: Session) -> None:
    _person(clean, label="lead")
    writer = RecordingCall([_bare_card_response()])

    run = _run(clean, writer, RecordingCall([judge_response()]), prompt_version=1)

    [request] = writer.requests
    assert "Prompt version" not in request.system and "fonti" not in request.system
    assert "Sources available" not in request.messages[0].text
    assert request.output_model is Card
    [result] = results_of(clean, run.id)
    assert result.card == _card() and run.prompt_version == 1


def test_a_prompt_version_nobody_can_write_is_refused_before_any_call(clean: Session) -> None:
    _person(clean, label="lead")
    writer = RecordingCall([])

    with pytest.raises(ValidationFailed):
        _run(clean, writer, RecordingCall([]), prompt_version=3)

    assert writer.requests == [] and clean.scalars(select(CardEvalRun)).all() == []


def test_a_card_a_guard_refused_is_a_guard_failure_and_is_not_judged(clean: Session) -> None:
    _person(clean, label="lead")
    gendered = _card(sintesi="Lei è una sviluppatrice con nove anni di esperienza.")
    judging = RecordingCall([])

    run = _run(
        clean,
        RecordingCall([card_response(gendered), card_response(gendered)]),
        judging,
    )

    [result] = results_of(clean, run.id)
    assert (result.failure, result.guard_clean, result.card) == ("gendered", False, None)
    assert result.judgement is None and judging.requests == []
    assert (run.summary["guard_failures"], run.summary["failed"], run.summary["judged"]) == (
        1,
        1,
        0,
    )


def test_a_refusal_is_not_a_guard_failure(clean: Session) -> None:
    _person(clean, label="lead")

    run = _run(clean, RecordingCall([card_response(stop_reason="refusal")]), RecordingCall([]))

    [result] = results_of(clean, run.id)
    assert (result.failure, result.guard_clean) == ("refusal", None)
    assert run.summary["guard_failures"] == 0 and run.summary["failed"] == 1


@pytest.mark.parametrize(
    ("response", "failure"),
    [
        (judge_response(stop_reason="refusal"), "refusal"),
        (judge_response(stop_reason="max_tokens"), "max_tokens"),
        (judge_response(text="{}"), "shape"),
    ],
)
def test_a_judge_that_gives_no_verdict_is_recorded_and_the_run_goes_on(
    clean: Session, response: LlmResponse, failure: str
) -> None:
    _person(clean, label="a")
    _person(clean, "grace@studio.it", "Hopper", label="b")

    run = _run(
        clean,
        RecordingCall([card_response(), card_response()]),
        RecordingCall([response, judge_response()]),
    )

    first, second = results_of(clean, run.id)
    assert first.judgement is not None and first.judgement["failure"] == failure
    # The rules' verdict stands where the judge gave none.
    assert set(first.judgement["fields"]) == {"anni"}
    assert second.judgement is not None and second.judgement["failure"] is None
    assert run.summary["complete"] and run.summary["judge_failed"] == 1
    assert (run.summary["judged"], run.summary["fields"]) == (1, 8 + 1)
    assert "[giudice: " + failure + "]" in format_report(run, [first, second])


def test_an_outage_of_the_judge_keeps_the_card_and_stops_the_run(clean: Session) -> None:
    _person(clean, label="a")
    _person(clean, "grace@studio.it", "Hopper", label="b")

    class Down:
        calls = 0

        def complete(self, request: object) -> object:
            Down.calls += 1
            raise LlmUnavailable("Il giudice non risponde")

    writer = RecordingCall([card_response(), card_response()])
    run = _run(clean, writer, Down())

    assert Down.calls == 1 and len(writer.requests) == 1
    [only] = results_of(clean, run.id)
    assert only.card is not None and only.judgement is not None
    assert only.judgement["failure"] == "unavailable"
    assert run.summary["complete"] is False
    assert run.summary["stopped"] == "Il giudice non risponde"


def test_an_outage_of_the_writer_stops_the_run_and_it_is_never_a_baseline(clean: Session) -> None:
    _person(clean, label="a")
    _person(clean, "grace@studio.it", "Hopper", label="b")

    class Spent:
        calls = 0

        def complete(self, request: object) -> object:
            Spent.calls += 1
            raise LlmUnavailable(CAP_REACHED)

    judging = RecordingCall([])
    run = _run(clean, Spent(), judging)

    assert Spent.calls == 1 and judging.requests == []
    assert run.summary["complete"] is False and run.summary["stopped"] == CAP_REACHED
    assert [result.failure for result in results_of(clean, run.id)] == ["unavailable"]
    # The next finished run has no baseline to be compared with: this one is not one.
    again = _run(clean, RecordingCall([card_response()] * 2), RecordingCall([judge_response()] * 2))
    assert again.summary["baseline"] is None and again.summary["complete"]
    assert "Nessun riferimento" in format_report(again, results_of(clean, again.id))


def test_what_ran_is_kept_when_the_run_ends_early_for_any_reason(clean: Session) -> None:
    _person(clean, label="a")
    _person(clean, "grace@studio.it", "Hopper", label="b")
    calls: list[UUID] = []
    first = RecordingCall([card_response()])

    def llm_for(person: UUID) -> Any:
        calls.append(person)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return first

    with pytest.raises(KeyboardInterrupt):
        run_eval(
            clean,
            llm_for,
            lambda _person: RecordingCall([judge_response()]),
            task="card",
            provider="anthropic",
            model=MODEL,
            effort=None,
            targets=sample(clean),
            judge_by=("anthropic", MODEL),
        )

    [run] = clean.scalars(select(CardEvalRun)).all()
    assert run.summary["complete"] is False and run.summary["stopped"] == INTERRUPTED
    assert (run.summary["ran"], run.summary["profiles"]) == (1, 2)
    assert len(clean.scalars(select(CardEvalResult)).all()) == 1


def test_a_person_with_no_cv_is_a_failure_of_their_own(clean: Session) -> None:
    person = _person(clean, label="lead")
    clean.execute(text("UPDATE freelancers SET cv_bytes = NULL WHERE id = :f"), {"f": person})
    clean.commit()

    run = _run(clean, RecordingCall([]), RecordingCall([]))

    [result] = results_of(clean, run.id)
    assert result.failure == "no_text" and result.card is None


def test_a_run_changes_no_task_and_writes_no_live_card(clean: Session) -> None:
    _person(clean, label="lead")
    tasks = clean.execute(text("SELECT * FROM ai_tasks ORDER BY task")).all()

    _run(
        clean,
        RecordingCall([card_response()]),
        RecordingCall([judge_response()]),
        provider="openai",
        model="gpt-5",
    )

    assert clean.execute(text("SELECT * FROM ai_tasks ORDER BY task")).all() == tasks
    assert clean.scalars(select(FreelancerCard)).all() == []


# ---- the totals and the baseline -------------------------------------------------------------


def _result(**changes: Any) -> CardEvalResult:
    fields = {name: {"verdict": "supported", "reason": "r", "by": "judge"} for name in FONTI}
    base: dict[str, Any] = {
        "run_id": uuid4(),
        "freelancer_id": uuid4(),
        "label": "x",
        "guard_clean": True,
        "card": CARD,
        "judgement": {"failure": None, "fields": fields, "invented": [], "identifying": []},
        "checks": {"anonymity": [], "years": {"ok": True}},
        "latency_ms": 1000,
        "cost_eur": Decimal("0.01"),
        "judge_latency_ms": 2000,
        "judge_cost_eur": Decimal("0.02"),
        "anni_ok": True,
    }
    return CardEvalResult(**{**base, **changes})


def _worse(freelancer_id: UUID) -> CardEvalResult:
    fields = {name: {"verdict": "supported", "reason": "r", "by": "judge"} for name in FONTI}
    fields["anni"] = {"verdict": "contradicted", "reason": "r", "by": "rule"}
    fields["competenze"] = {"verdict": "unsupported", "reason": "r", "by": "judge"}
    return _result(
        freelancer_id=freelancer_id,
        anni_ok=False,
        judgement={"failure": None, "fields": fields, "invented": ["x", "y"], "identifying": []},
        checks={"anonymity": ["sintesi"], "years": {"ok": False}},
    )


def test_the_totals_count_verdicts_costs_and_what_went_wrong() -> None:
    results = [
        _result(),
        _worse(uuid4()),
        _result(card=None, judgement=None, checks=None, guard_clean=False),
    ]

    totals = aggregate(results)

    assert (totals["fields"], totals["supported"], totals["unsupported"]) == (16, 14, 1)
    assert (totals["contradicted"], totals["contradicted_profiles"]) == (1, 1)
    assert (totals["invented"], totals["leaks_rule"], totals["leaks"]) == (2, 1, 1)
    assert (totals["failed"], totals["judged"], totals["guard_failures"]) == (1, 2, 1)
    assert (totals["anni_checked"], totals["anni_ok"]) == (2, 1)
    assert (totals["cost_eur"], totals["judge_cost_eur"]) == ("0.03", "0.06")
    assert (totals["latency_ms_avg"], totals["judge_latency_ms_avg"]) == (1000, 2000)


def test_a_run_is_compared_with_its_baseline_on_the_people_both_wrote() -> None:
    shared = uuid4()
    baseline = [_worse(shared), _result(freelancer_id=uuid4())]
    candidate = [_result(freelancer_id=shared), _result(freelancer_id=uuid4())]

    table = compare(candidate, baseline)

    assert table["profiles"] == 1
    measures = table["measures"]
    assert measures["supported_rate"] == {"this": 1.0, "baseline": 0.75, "verdict": "meglio"}
    assert measures["contradicted_rate"]["verdict"] == "meglio"
    assert measures["anni_ok_rate"] == {"this": 1.0, "baseline": 0.0, "verdict": "meglio"}
    assert measures["leaks"] == {"this": 0.0, "baseline": 1.0, "verdict": "meglio"}
    assert measures["guard_failures"]["verdict"] == "uguale"
    assert compare(baseline, candidate)["measures"]["supported_rate"]["verdict"] == "peggio"
    # Nobody in common: nothing to compare.
    assert compare(candidate, [_result()])["profiles"] == 0


def test_a_baseline_with_no_verdicts_is_compared_on_what_it_has() -> None:
    person = uuid4()
    manual = CardEvalResult(
        run_id=uuid4(), freelancer_id=person, label="x", anni_ok=True, guard_clean=True
    )

    measures = compare([_result(freelancer_id=person)], [manual])["measures"]

    assert measures["supported_rate"]["verdict"] == "n/d"
    assert measures["guard_failures"]["verdict"] == "uguale"


def test_the_default_baseline_is_the_live_configuration_and_an_explicit_one_replaces_it(
    clean: Session,
) -> None:
    _person(clean, label="lead")
    bad = judge_response({"ruolo": ("contradicted", "the CV says otherwise")})

    first = _run(clean, RecordingCall([card_response()]), RecordingCall([bad]))
    assert first.summary["baseline"] is None
    assert "Nessun riferimento" in format_report(first, results_of(clean, first.id))

    better = _run(clean, RecordingCall([card_response()]), RecordingCall([judge_response()]))
    other = _run(
        clean,
        RecordingCall([card_response()]),
        RecordingCall([judge_response()]),
        provider="openai",
        model="gpt-5",
        baseline_run_id=first.id,
    )

    baseline = better.summary["baseline"]
    assert baseline["run_id"] == str(first.id) and baseline["profiles"] == 1
    assert baseline["measures"]["contradicted_rate"]["verdict"] == "meglio"
    # A run of another model is a candidate, not the reference of the next.
    after = _run(clean, RecordingCall([card_response()]), RecordingCall([judge_response()]))
    assert after.summary["baseline"]["run_id"] == str(better.id)
    assert other.summary["baseline"]["run_id"] == str(first.id)
    report = format_report(better, results_of(clean, better.id))
    assert f"run di riferimento {first.id}" in report
    assert "campi contraddetti 0% contro 12% (meglio)" in report


def test_a_run_of_another_effort_or_of_a_few_people_is_no_default_reference(
    clean: Session,
) -> None:
    _person(clean, label="a")
    _person(clean, "grace@studio.it", "Hopper", label="b")

    def run(targets: list[Target] | None = None, **options: Any) -> CardEvalRun:
        count = len(targets) if targets is not None else 2
        return _run(
            clean,
            RecordingCall([card_response()] * count),
            RecordingCall([judge_response()] * count),
            targets,
            **options,
        )

    run(effort="high")  # a probe at another effort
    run(sample(clean, 1))  # a run of one person of two
    assert run().summary["baseline"] is None

    full = run()
    assert full.summary["baseline"] is not None
    assert full.summary["baseline"]["profiles"] == 2


def test_a_baseline_must_exist_be_finished_and_of_the_task(clean: Session) -> None:
    _person(clean, label="lead")
    cut = CardEvalRun(
        task="card",
        provider="anthropic",
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        summary={"complete": False},
    )
    clean.add(cut)
    clean.commit()
    writer = RecordingCall([])

    with pytest.raises(NotFound):
        _run(clean, writer, RecordingCall([]), baseline_run_id=uuid4())
    with pytest.raises(InvalidState):
        _run(clean, writer, RecordingCall([]), baseline_run_id=cut.id)

    assert writer.requests == []


def test_a_run_of_the_manual_evaluation_is_never_the_default_reference(clean: Session) -> None:
    _person(clean, label="lead")
    manual = CardEvalRun(
        task="card",
        provider="anthropic",
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        summary={"complete": True, "ran": 15, "anni_ok": 15},
    )
    clean.add(manual)
    clean.commit()

    run = _run(clean, RecordingCall([card_response()]), RecordingCall([judge_response()]))

    assert run.summary["baseline"] is None


# ---- the report -----------------------------------------------------------------------------


def test_the_report_carries_no_name_card_text_or_reason_of_the_judge(clean: Session) -> None:
    person = _person(clean, label="senza date")
    run = _run(
        clean,
        RecordingCall([card_response()]),
        RecordingCall(
            [
                judge_response(
                    {"luogo": ("contradicted", "The CV places her in Napoli, not Torino")},
                    invented=["Kubernetes"],
                )
            ]
        ),
    )

    report = format_report(run, results_of(clean, run.id))

    for private in (
        "Lovelace",
        "Ada",
        "Backend developer",
        "Python",
        "Torino",
        "Napoli",
        "Kubernetes",
    ):
        assert private not in report
    assert "senza date" in report and str(person)[:8] in report
    assert "giudice anthropic/claude-opus-5" in report
    # The judge is the model that wrote the cards: the report says what that is worth.
    assert "il giudice è lo stesso modello" in report
    assert "campi supportati 7/8 (88%)" in report and "contraddetti 1 in 1 schede" in report
    assert "costo scheda" in report and "giudizio" in report


def test_another_judge_has_no_note_and_a_disabled_provider_is_flagged(clean: Session) -> None:
    _person(clean, label="lead")
    run = _run(
        clean,
        RecordingCall([card_response()]),
        RecordingCall([judge_response()]),
        judge_by=("openai", "gpt-5"),
    )
    run.summary = {**run.summary, "allow_disabled": True}

    report = format_report(run, results_of(clean, run.id))

    assert "giudice openai/gpt-5" in report and "stesso modello" not in report
    assert "ATTENZIONE: --allow-disabled" in report


# ---- what it costs ----------------------------------------------------------------------------


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


def _calls(
    ai_ready: Callable[[], Any], stub: Stub, writer: tuple[str, str], judging: tuple[str, str]
) -> tuple[Callable[[UUID], AiCall], Callable[[UUID], AiCall]]:
    settings = Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]

    def call_as(provider: str, model: str) -> Callable[[UUID], AiCall]:
        return lambda freelancer_id: AiCall(
            ai_ready,
            settings,
            "card",
            override=TaskOverride(provider, model, None),
            purpose="eval",
            freelancer_id=freelancer_id,
            model_factory=stub.factory,
        )

    return call_as(*writer), call_as(*judging)


def test_writing_and_judging_are_each_spent_under_the_card_task_for_eval(
    clean: Session, ai_ready: Callable[[], Any]
) -> None:
    person = _person(clean, label="lead")
    stub = Stub(
        Stub.reply(draft_body(), input_tokens=1000, output_tokens=200),
        Stub.reply(judgement_body(), input_tokens=3000, output_tokens=400),
    )
    write_as, judge_as = _calls(ai_ready, stub, ("anthropic", MODEL), ("anthropic", OTHER_MODEL))

    run = run_eval(
        clean,
        write_as,
        judge_as,
        task="card",
        provider="anthropic",
        model=MODEL,
        effort=None,
        targets=sample(clean),
        judge_by=("anthropic", OTHER_MODEL),
    )

    usage = clean.scalars(select(AiUsage).order_by(AiUsage.at)).all()
    assert [(u.task, u.purpose, u.model, u.freelancer_id) for u in usage] == [
        ("card", "eval", MODEL, person),
        ("card", "eval", OTHER_MODEL, person),
    ]
    # 1000 in and 200 out at 5 and 25 euro per million; 3000 and 400 at 2 and 10.
    assert [u.cost_eur for u in usage] == [Decimal("0.010000"), Decimal("0.010000")]
    [result] = results_of(clean, run.id)
    assert result.cost_eur == Decimal("0.010000") and result.judge_cost_eur == Decimal("0.010000")
    assert result.latency_ms is not None and result.judge_latency_ms is not None
    assert (Decimal(run.summary["cost_eur"]), Decimal(run.summary["judge_cost_eur"])) == (
        Decimal("0.01"),
        Decimal("0.01"),
    )
    assert run.summary["judge"] == {"provider": "anthropic", "model": OTHER_MODEL}
    settings = Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]
    with ai_ready() as session:
        # Both count against the task's daily cap, and the task's own settings stay.
        assert AiConfigService(session, settings).spent_today("card") == Decimal("0.02")
        task = session.get(AiTask, "card")
        assert task is not None and (task.model, task.effort) == (MODEL, "medium")


def test_the_daily_cap_stops_a_run_before_any_call_and_says_why(
    clean: Session, ai_ready: Callable[[], Any]
) -> None:
    _person(clean, label="lead")
    with ai_ready() as session:
        task = session.get(AiTask, "card")
        assert task is not None
        task.daily_cap_eur = Decimal("0")
        session.commit()
    stub = Stub()
    write_as, judge_as = _calls(ai_ready, stub, ("anthropic", MODEL), ("anthropic", MODEL))

    run = run_eval(
        clean,
        write_as,
        judge_as,
        task="card",
        provider="anthropic",
        model=MODEL,
        effort=None,
        targets=sample(clean),
        judge_by=("anthropic", MODEL),
    )

    assert stub.built == []
    assert run.summary["complete"] is False and run.summary["stopped"] == CAP_REACHED
