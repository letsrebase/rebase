"""REB-842: how a run stands against its baseline: the totals, the people both wrote, the default
reference, and what the report carries.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from card_eval_fixtures import MODEL, _person, _run
from fakes_cards import (
    CARD,
    SOURCES,
    card_response,
    judge_response,
)
from sqlalchemy.orm import Session

from rebase_core.card_eval import (
    Target,
    aggregate,
    compare,
    format_report,
    results_of,
    sample,
)
from rebase_core.card_prompt import PROMPT_VERSION
from rebase_core.errors import InvalidState, NotFound
from rebase_core.llm import RecordingCall
from rebase_core.models import (
    CardEvalResult,
    CardEvalRun,
)

# ---- the totals and the baseline -------------------------------------------------------------


def _result(**changes: Any) -> CardEvalResult:
    fields = {name: {"verdict": "supported", "reason": "r", "by": "judge"} for name in SOURCES}
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
        "experience_years_ok": True,
    }
    return CardEvalResult(**{**base, **changes})


def _worse(freelancer_id: UUID) -> CardEvalResult:
    fields = {name: {"verdict": "supported", "reason": "r", "by": "judge"} for name in SOURCES}
    fields["experience_years"] = {"verdict": "contradicted", "reason": "r", "by": "rule"}
    fields["skills"] = {"verdict": "unsupported", "reason": "r", "by": "judge"}
    return _result(
        freelancer_id=freelancer_id,
        experience_years_ok=False,
        judgement={"failure": None, "fields": fields, "invented": ["x", "y"], "identifying": []},
        checks={"anonymity": ["profile_summary"], "years": {"ok": False}},
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
    assert (totals["anni_checked"], totals["experience_years_ok"]) == (2, 1)
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
        run_id=uuid4(), freelancer_id=person, label="x", experience_years_ok=True, guard_clean=True
    )

    measures = compare([_result(freelancer_id=person)], [manual])["measures"]

    assert measures["supported_rate"]["verdict"] == "n/d"
    assert measures["guard_failures"]["verdict"] == "uguale"


def test_the_default_baseline_is_the_live_configuration_and_an_explicit_one_replaces_it(
    clean_hub: Session,
) -> None:
    _person(clean_hub, label="lead")
    bad = judge_response({"role": ("contradicted", "the CV says otherwise")})

    first = _run(clean_hub, RecordingCall([card_response()]), RecordingCall([bad]))
    assert first.summary["baseline"] is None
    assert "Nessun riferimento" in format_report(first, results_of(clean_hub, first.id))

    better = _run(clean_hub, RecordingCall([card_response()]), RecordingCall([judge_response()]))
    other = _run(
        clean_hub,
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
    after = _run(clean_hub, RecordingCall([card_response()]), RecordingCall([judge_response()]))
    assert after.summary["baseline"]["run_id"] == str(better.id)
    assert other.summary["baseline"]["run_id"] == str(first.id)
    report = format_report(better, results_of(clean_hub, better.id))
    assert f"run di riferimento {first.id}" in report
    assert "campi contraddetti 0% contro 12% (meglio)" in report


def test_a_run_of_another_effort_or_of_a_few_people_is_no_default_reference(
    clean_hub: Session,
) -> None:
    _person(clean_hub, label="a")
    _person(clean_hub, "grace@studio.it", "Hopper", label="b")

    def run(targets: list[Target] | None = None, **options: Any) -> CardEvalRun:
        count = len(targets) if targets is not None else 2
        return _run(
            clean_hub,
            RecordingCall([card_response()] * count),
            RecordingCall([judge_response()] * count),
            targets,
            **options,
        )

    run(effort="high")  # a probe at another effort
    run(sample(clean_hub, 1))  # a run of one person of two
    assert run().summary["baseline"] is None

    full = run()
    assert full.summary["baseline"] is not None
    assert full.summary["baseline"]["profiles"] == 2


def test_a_baseline_must_exist_be_finished_and_of_the_task(clean_hub: Session) -> None:
    _person(clean_hub, label="lead")
    cut = CardEvalRun(
        task="card",
        provider="anthropic",
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        summary={"complete": False},
    )
    clean_hub.add(cut)
    clean_hub.commit()
    writer = RecordingCall([])

    with pytest.raises(NotFound):
        _run(clean_hub, writer, RecordingCall([]), baseline_run_id=uuid4())
    with pytest.raises(InvalidState):
        _run(clean_hub, writer, RecordingCall([]), baseline_run_id=cut.id)

    assert writer.requests == []


def test_a_run_of_the_manual_evaluation_is_never_the_default_reference(clean_hub: Session) -> None:
    _person(clean_hub, label="lead")
    manual = CardEvalRun(
        task="card",
        provider="anthropic",
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        summary={"complete": True, "ran": 15, "experience_years_ok": 15},
    )
    clean_hub.add(manual)
    clean_hub.commit()

    run = _run(clean_hub, RecordingCall([card_response()]), RecordingCall([judge_response()]))

    assert run.summary["baseline"] is None


# ---- the report -----------------------------------------------------------------------------


def test_the_report_carries_no_name_card_text_or_reason_of_the_judge(clean_hub: Session) -> None:
    person = _person(clean_hub, label="senza date")
    run = _run(
        clean_hub,
        RecordingCall([card_response()]),
        RecordingCall(
            [
                judge_response(
                    {"location": ("contradicted", "The CV places her in Napoli, not Torino")},
                    invented=["Kubernetes"],
                )
            ]
        ),
    )

    report = format_report(run, results_of(clean_hub, run.id))

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


def test_another_judge_has_no_note_and_a_disabled_provider_is_flagged(clean_hub: Session) -> None:
    _person(clean_hub, label="lead")
    run = _run(
        clean_hub,
        RecordingCall([card_response()]),
        RecordingCall([judge_response()]),
        judge_by=("openai", "gpt-5"),
    )
    run.summary = {**run.summary, "allow_disabled": True}

    report = format_report(run, results_of(clean_hub, run.id))

    assert "giudice openai/gpt-5" in report and "stesso modello" not in report
    assert "ATTENZIONE: --allow-disabled" in report
