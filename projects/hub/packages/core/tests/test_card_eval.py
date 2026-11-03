"""REB-842: the cards' automatic evaluation: the fixed sample, a run that writes a card and
cross-checks it against its sources (the rules, then a judging call), and what is stored and
never stored. The baseline is in `test_card_eval_baseline.py`, the spend in
`test_card_eval_spend.py`.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from admins import an_admin
from card_eval_fixtures import MODEL, UNDATED_CV, _bare_card_response, _card, _person, _run
from fakes_cards import (
    card_response,
    judge_response,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_warehouse_contract import WAREHOUSE_TABLES

from rebase_core.ai_config import CAP_REACHED
from rebase_core.card_eval import (
    INTERRUPTED,
    OUTSIDE_SAMPLE,
    Target,
    format_report,
    named,
    results_of,
    run_eval,
    sample,
)
from rebase_core.card_prompt import PROMPT_VERSION
from rebase_core.db import Base
from rebase_core.errors import InvalidState, LlmUnavailable, NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import LlmResponse, RecordingCall
from rebase_core.models import (
    CardEvalProfile,
    CardEvalResult,
    CardEvalRun,
    Freelancer,
    FreelancerCard,
)
from rebase_core.team_schemas import Card

# ---- the sample -----------------------------------------------------------------------------


def test_the_sample_is_the_people_in_it_by_label_and_a_smaller_one_spans_the_labels(
    clean_hub: Session,
) -> None:
    people = {
        "senza date": [
            _person(clean_hub, f"a{n}@studio.it", f"Uno{n}", label="senza date") for n in (1, 2, 3)
        ],
        "GitHub": [
            _person(clean_hub, f"b{n}@studio.it", f"Due{n}", label="GitHub") for n in (1, 2)
        ],
        "lead": [_person(clean_hub, "c1@studio.it", "Tre1", label="lead")],
    }

    everyone = sample(clean_hub)
    three = sample(clean_hub, 3)
    four = sample(clean_hub, 4)

    assert len(everyone) == 6
    assert [t.label for t in everyone] == sorted(t.label for t in everyone)
    # Three people are one of each failure, not three of the first.
    assert sorted(t.label for t in three) == ["GitHub", "lead", "senza date"]
    assert sorted(t.label for t in four) == ["GitHub", "GitHub", "lead", "senza date"]
    # The same N is always the same people, in the same order.
    assert sample(clean_hub, 4) == four
    assert {t.freelancer_id for t in four} <= {p for group in people.values() for p in group}
    assert sample(clean_hub, 99) == everyone


def test_the_sample_leaves_out_a_deleted_person_and_is_refused_when_empty_or_zero(
    clean_hub: Session,
) -> None:
    with pytest.raises(InvalidState):
        sample(clean_hub)
    ada = _person(clean_hub, label="lead")
    grace = _person(clean_hub, "grace@studio.it", "Hopper", label="duplicato")
    FreelancerService(clean_hub).soft_delete(grace, an_admin(clean_hub))

    assert [t.freelancer_id for t in sample(clean_hub)] == [ada]
    with pytest.raises(ValidationFailed):
        sample(clean_hub, 0)


def test_named_people_keep_their_label_or_are_outside_the_sample(clean_hub: Session) -> None:
    ada = _person(clean_hub, label="lead")
    grace = _person(clean_hub, "grace@studio.it", "Hopper")

    assert named(clean_hub, [ada, grace, ada]) == [
        Target(ada, "lead"),
        Target(grace, OUTSIDE_SAMPLE),
    ]
    with pytest.raises(NotFound):
        named(clean_hub, [uuid4()])
    with pytest.raises(InvalidState):
        named(clean_hub, [])


def test_the_sample_and_its_results_go_with_the_person_and_hold_no_expected_card(
    clean_hub: Session,
) -> None:
    person = _person(clean_hub, label="lead")
    run = _run(clean_hub, RecordingCall([card_response()]), RecordingCall([judge_response()]))
    assert clean_hub.get(CardEvalProfile, person) is not None
    assert len(results_of(clean_hub, run.id)) == 1

    clean_hub.execute(text("DELETE FROM freelancers WHERE id = :f"), {"f": person})
    clean_hub.commit()

    assert clean_hub.scalars(select(CardEvalProfile)).all() == []
    assert clean_hub.scalars(select(CardEvalResult)).all() == []
    # The run itself, which holds only totals, stays.
    assert clean_hub.scalars(select(CardEvalRun)).one().id == run.id
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


def test_a_run_writes_checks_and_judges_each_card_and_stores_no_live_card(
    clean_hub: Session,
) -> None:
    person = _person(clean_hub, label="senza date")
    writer = RecordingCall([card_response()])
    judging = RecordingCall(
        [
            judge_response(
                {"skills": ("unsupported", "no source says AWS")},
                invented=["AWS"],
                identifying=["Acme"],
            )
        ]
    )

    run = _run(clean_hub, writer, judging)

    [result] = results_of(clean_hub, run.id)
    assert result.freelancer_id == person and result.label == "senza date"
    assert result.card == _card() and result.failure is None
    # The rules: the years are the CV's 2017 - 2025 (8 to 9 years), no identifier is left.
    assert result.checks == {
        "years": {"mode": "recomputed", "ok": True, "low": 8, "high": 9},
        "anonymity": [],
    }
    assert (result.experience_years_ok, result.guard_clean) == (True, True)
    # The judge's verdicts, and a rule's over the judge's on the years.
    assert result.judgement is not None
    fields = result.judgement["fields"]
    assert fields["skills"] == {
        "verdict": "unsupported",
        "reason": "no source says AWS",
        "by": "judge",
    }
    assert (
        fields["experience_years"]["by"] == "rule"
        and fields["experience_years"]["verdict"] == "supported"
    )
    assert fields["role"]["by"] == "judge"
    assert result.judgement["invented"] == ["AWS"]
    assert result.judgement["identifying"] == ["Acme"]
    # One call to write, one to judge, and nothing written to the live card.
    assert len(writer.requests) == 1 and len(judging.requests) == 1
    assert clean_hub.scalars(select(FreelancerCard)).all() == []
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


def test_the_judge_is_given_the_card_and_what_the_card_was_written_from(clean_hub: Session) -> None:
    _person(clean_hub, label="lead")
    judging = RecordingCall([judge_response()])

    _run(clean_hub, RecordingCall([card_response()]), judging)

    [request] = judging.requests
    message = request.messages[0].text
    assert "<card>" in message and '"role": "Backend developer"' in message
    assert "Backend developer, Acme, 2017 - 2025" in message  # the CV, as data


def test_a_rule_settles_the_years_over_a_judge_who_says_supported(clean_hub: Session) -> None:
    """The CV dates nothing, so nine years are a guess whatever the judge says (REB-799)."""
    _person(clean_hub, cv=UNDATED_CV, label="senza date")

    run = _run(clean_hub, RecordingCall([card_response()]), RecordingCall([judge_response()]))

    [result] = results_of(clean_hub, run.id)
    assert result.experience_years_ok is False
    assert result.checks is not None and result.checks["years"]["mode"] == "undated"
    assert result.judgement is not None
    assert result.judgement["fields"]["experience_years"]["verdict"] == "contradicted"
    assert result.judgement["fields"]["experience_years"]["by"] == "rule"
    assert run.summary["contradicted"] == 1 and run.summary["contradicted_profiles"] == 1
    assert (run.summary["experience_years_checked"], run.summary["experience_years_ok"]) == (1, 0)


def test_an_identifier_of_the_person_left_in_a_card_is_found_by_the_rules(
    clean_hub: Session,
) -> None:
    """Prompt version 1 never read the person's links, so its card may name them: the rules
    read every prompt against everything the person's data holds."""
    person = _person(clean_hub, label="GitHub")
    row = clean_hub.get(Freelancer, person)
    assert row is not None
    row.links = ["https://ada-engine.dev/me"]
    clean_hub.commit()
    leaky = _card(
        profile_summary="Ha fondato ada-engine, una libreria per i calcoli, nove anni di Python."
    )

    run = _run(
        clean_hub,
        RecordingCall([_bare_card_response(leaky)]),
        RecordingCall([judge_response()]),
        prompt_version=1,
    )

    [result] = results_of(clean_hub, run.id)
    assert result.checks is not None and result.checks["anonymity"] == ["profile_summary"]
    assert (run.summary["leaks_rule"], run.summary["leaks"]) == (1, 1)
    assert "anonima" in format_report(run, [result]) and "no" in format_report(run, [result])


def test_prompt_version_1_writes_from_the_cv_alone_and_takes_a_bare_card(
    clean_hub: Session,
) -> None:
    _person(clean_hub, label="lead")
    writer = RecordingCall([_bare_card_response()])

    run = _run(clean_hub, writer, RecordingCall([judge_response()]), prompt_version=1)

    [request] = writer.requests
    assert "Prompt version" not in request.system and "sources" not in request.system
    assert "Sources available" not in request.messages[0].text
    assert request.output_model is Card
    [result] = results_of(clean_hub, run.id)
    assert result.card == _card() and run.prompt_version == 1


def test_a_prompt_version_nobody_can_write_is_refused_before_any_call(clean_hub: Session) -> None:
    _person(clean_hub, label="lead")
    writer = RecordingCall([])

    with pytest.raises(ValidationFailed):
        _run(clean_hub, writer, RecordingCall([]), prompt_version=4)

    assert writer.requests == [] and clean_hub.scalars(select(CardEvalRun)).all() == []


def test_a_card_a_guard_refused_is_a_guard_failure_and_is_not_judged(clean_hub: Session) -> None:
    _person(clean_hub, label="lead")
    gendered = _card(profile_summary="Lei è una sviluppatrice con nove anni di esperienza.")
    judging = RecordingCall([])

    run = _run(
        clean_hub,
        RecordingCall([card_response(gendered), card_response(gendered)]),
        judging,
    )

    [result] = results_of(clean_hub, run.id)
    assert (result.failure, result.guard_clean, result.card) == ("gendered", False, None)
    assert result.judgement is None and judging.requests == []
    assert (run.summary["guard_failures"], run.summary["failed"], run.summary["judged"]) == (
        1,
        1,
        0,
    )


def test_a_refusal_is_not_a_guard_failure(clean_hub: Session) -> None:
    _person(clean_hub, label="lead")

    run = _run(clean_hub, RecordingCall([card_response(stop_reason="refusal")]), RecordingCall([]))

    [result] = results_of(clean_hub, run.id)
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
    clean_hub: Session, response: LlmResponse, failure: str
) -> None:
    _person(clean_hub, label="a")
    _person(clean_hub, "grace@studio.it", "Hopper", label="b")

    run = _run(
        clean_hub,
        RecordingCall([card_response(), card_response()]),
        RecordingCall([response, judge_response()]),
    )

    first, second = results_of(clean_hub, run.id)
    assert first.judgement is not None and first.judgement["failure"] == failure
    # The rules' verdict stands where the judge gave none.
    assert set(first.judgement["fields"]) == {"experience_years"}
    assert second.judgement is not None and second.judgement["failure"] is None
    assert run.summary["complete"] and run.summary["judge_failed"] == 1
    assert (run.summary["judged"], run.summary["fields"]) == (1, 8 + 1)
    assert "[giudice: " + failure + "]" in format_report(run, [first, second])


def test_an_outage_of_the_judge_keeps_the_card_and_stops_the_run(clean_hub: Session) -> None:
    _person(clean_hub, label="a")
    _person(clean_hub, "grace@studio.it", "Hopper", label="b")

    class Down:
        calls = 0

        def complete(self, request: object) -> object:
            Down.calls += 1
            raise LlmUnavailable("Il giudice non risponde")

    writer = RecordingCall([card_response(), card_response()])
    run = _run(clean_hub, writer, Down())

    assert Down.calls == 1 and len(writer.requests) == 1
    [only] = results_of(clean_hub, run.id)
    assert only.card is not None and only.judgement is not None
    assert only.judgement["failure"] == "unavailable"
    assert run.summary["complete"] is False
    assert run.summary["stopped"] == "Il giudice non risponde"


def test_an_outage_of_the_writer_stops_the_run_and_it_is_never_a_baseline(
    clean_hub: Session,
) -> None:
    _person(clean_hub, label="a")
    _person(clean_hub, "grace@studio.it", "Hopper", label="b")

    class Spent:
        calls = 0

        def complete(self, request: object) -> object:
            Spent.calls += 1
            raise LlmUnavailable(CAP_REACHED)

    judging = RecordingCall([])
    run = _run(clean_hub, Spent(), judging)

    assert Spent.calls == 1 and judging.requests == []
    assert run.summary["complete"] is False and run.summary["stopped"] == CAP_REACHED
    assert [result.failure for result in results_of(clean_hub, run.id)] == ["unavailable"]
    # The next finished run has no baseline to be compared with: this one is not one.
    again = _run(
        clean_hub, RecordingCall([card_response()] * 2), RecordingCall([judge_response()] * 2)
    )
    assert again.summary["baseline"] is None and again.summary["complete"]
    assert "Nessun riferimento" in format_report(again, results_of(clean_hub, again.id))


def test_what_ran_is_kept_when_the_run_ends_early_for_any_reason(clean_hub: Session) -> None:
    _person(clean_hub, label="a")
    _person(clean_hub, "grace@studio.it", "Hopper", label="b")
    calls: list[UUID] = []
    first = RecordingCall([card_response()])

    def llm_for(person: UUID) -> Any:
        calls.append(person)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return first

    with pytest.raises(KeyboardInterrupt):
        run_eval(
            clean_hub,
            llm_for,
            lambda _person: RecordingCall([judge_response()]),
            task="card",
            provider="anthropic",
            model=MODEL,
            effort=None,
            targets=sample(clean_hub),
            judge_by=("anthropic", MODEL),
        )

    [run] = clean_hub.scalars(select(CardEvalRun)).all()
    assert run.summary["complete"] is False and run.summary["stopped"] == INTERRUPTED
    assert (run.summary["ran"], run.summary["profiles"]) == (1, 2)
    assert len(clean_hub.scalars(select(CardEvalResult)).all()) == 1


def test_a_person_with_no_cv_is_a_failure_of_their_own(clean_hub: Session) -> None:
    person = _person(clean_hub, label="lead")
    clean_hub.execute(text("UPDATE freelancers SET cv_bytes = NULL WHERE id = :f"), {"f": person})
    clean_hub.commit()

    run = _run(clean_hub, RecordingCall([]), RecordingCall([]))

    [result] = results_of(clean_hub, run.id)
    assert result.failure == "no_text" and result.card is None


def test_a_run_changes_no_task_and_writes_no_live_card(clean_hub: Session) -> None:
    _person(clean_hub, label="lead")
    tasks = clean_hub.execute(text("SELECT * FROM ai_tasks ORDER BY task")).all()

    _run(
        clean_hub,
        RecordingCall([card_response()]),
        RecordingCall([judge_response()]),
        provider="openai",
        model="gpt-5",
    )

    assert clean_hub.execute(text("SELECT * FROM ai_tasks ORDER BY task")).all() == tasks
    assert clean_hub.scalars(select(FreelancerCard)).all() == []
