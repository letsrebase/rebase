"""The cards' automatic evaluation (REB-842, design 2026-10-27, «Evaluation before a change
goes live», as Lorenzo changed it on 2026-10-27): `rebase cards-eval` writes the card of
each person of a sample with the provider, model, effort and prompt a run is asked for,
then cross-checks every card against the sources it was written from, with no admin step.

The cross-check is two things, kept apart in what is stored and what is reported:

* **rules**, where one exists (`card_eval_checks`): the card validates and no guard of
  `cards.py` refused it, nothing of the person's identifiers is left in it, the seniority
  is the years' band, and the years are recomputed from the CV's dated periods when the
  text allows a reliable parse;
* **a judging call** (`card_judge`), separate from the writing one and answered by the
  `card` task's provider and model or another provider's, that is given the card and the
  sources as data and marks each field supported, unsupported or contradicted, with a
  line of reason, and flags anything invented. A field a rule settled keeps the rule's
  verdict.

The sample is the fifteen people drafted under REB-835 (`card_eval_profiles`, who and why,
never a card), stratified by the failure each covers; `--sample N` takes N of them, a
label at a time, so a smaller run still spans the failures. Nothing here is stored as a
live card, and a run changes no setting: the calls are recorded under the `card` task with
`purpose="eval"` (the writing and the judging, one person after the other, so each cost is
read from its own window) and count against its daily cap.

A card, its checks and the judge's verdicts are personal data derived from a CV and the
sources: they live in this database only, with the person's row (`ON DELETE CASCADE`),
and nothing is echoed to a log or to stdout beyond a label, an id prefix, counts and
euro. The judge's reasons are stored, never printed.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.card_eval_checks import (
    YearsCheck,
    anonymity_leaks,
    check_years,
    seniority_in_band,
)
from rebase_core.card_guard import KnownIdentifiers
from rebase_core.card_judge import CardJudgement, JudgeFailure, judge
from rebase_core.cards import PROMPT_VERSION, PROMPT_VERSIONS, CardWriter, known_identifiers
from rebase_core.cv_text import CvText
from rebase_core.errors import InvalidState, LlmUnavailable, NotFound, ValidationFailed
from rebase_core.freelancers import ENTITY, FreelancerService
from rebase_core.hub_facts import HubFacts, hub_facts
from rebase_core.llm import LlmCall
from rebase_core.models import (
    AiTask,
    AiUsage,
    CardEvalProfile,
    CardEvalResult,
    CardEvalRun,
    Freelancer,
    User,
)
from rebase_core.sources import SourceSet, default_source_service
from rebase_core.team_schemas import Card

logger = logging.getLogger(__name__)

EVAL_ENTITY = "card_eval"
# What a label stands for on a person outside the sample, named with `--freelancer`.
OUTSIDE_SAMPLE = "fuori campione"
# The shape of a run's `summary`: a run of the manual evaluation (REB-835) has none of the
# new totals, and is never a default reference.
SUMMARY_FORMAT = 2
# What is a failure of the guards, not of the model or the CV: a card that names the person
# or carries an address, that still names an account, a repository, a site or an employer, or
# that still gives the gender away after its one rewrite.
GUARD_FAILURES = ("identifying", "source_identifying", "gendered")
BY_RULE, BY_JUDGE = "rule", "judge"
INTERRUPTED = "La run si è interrotta prima della fine"


@dataclass(frozen=True)
class Target:
    """One person a run writes a card for: their id, and the failure they cover."""

    freelancer_id: UUID
    label: str


# ---- who a run measures ----------------------------------------------------------------------


def sample(session: Session, count: int | None = None) -> list[Target]:
    """The fixed sample, live people only, in the order `--sample N` takes them: one person
    of each label in turn (labels and ids sorted, so the same N is always the same people),
    until `count` are taken, or all of them with no `count`. The whole sample is in label
    order. Raises `InvalidState` when the sample is empty or `count` is under one."""
    if count is not None and count < 1:
        raise ValidationFailed(EVAL_ENTITY, "sample", "almeno 1 profilo")
    rows = session.execute(
        select(CardEvalProfile.freelancer_id, CardEvalProfile.label)
        .join(Freelancer, Freelancer.id == CardEvalProfile.freelancer_id)
        .where(Freelancer.deleted_at.is_(None))
        .order_by(CardEvalProfile.label, CardEvalProfile.freelancer_id)
    ).all()
    if not rows:
        raise InvalidState("Il campione di valutazione è vuoto")
    everyone = [Target(freelancer_id, label) for freelancer_id, label in rows]
    if count is None or count >= len(everyone):
        return everyone
    by_label: dict[str, list[Target]] = {}
    for target in everyone:
        by_label.setdefault(target.label, []).append(target)
    taken: list[Target] = []
    depth = 0
    while len(taken) < count:
        for targets in by_label.values():
            if depth < len(targets) and len(taken) < count:
                taken.append(targets[depth])
        depth += 1
    return sorted(taken, key=lambda target: (target.label, target.freelancer_id))


def named(session: Session, freelancer_ids: Sequence[UUID]) -> list[Target]:
    """The people `--freelancer` names, each once, with their label in the sample or
    `OUTSIDE_SAMPLE`. A person who does not exist or was deleted is `NotFound`."""
    targets: list[Target] = []
    for freelancer_id in dict.fromkeys(freelancer_ids):
        row = session.get(Freelancer, freelancer_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(ENTITY, freelancer_id)
        profile = session.get(CardEvalProfile, freelancer_id)
        targets.append(Target(freelancer_id, profile.label if profile else OUTSIDE_SAMPLE))
    if not targets:
        raise InvalidState("Nessun profilo da valutare")
    return targets


# ---- what one card is checked with ----------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    """What the person's card was written from, and the identifiers that could name them,
    read now: the CV, the position, the stored sources (no network), the hub's facts."""

    cv: CvText
    posizione: str | None
    sources: SourceSet
    facts: HubFacts
    known: KnownIdentifiers


def inputs_of(session: Session, freelancer_id: UUID) -> Inputs:
    row = session.get(Freelancer, freelancer_id)
    if row is None:
        raise NotFound(ENTITY, freelancer_id)
    owner = session.get(User, row.user_id)
    cv = FreelancerService(session).cv_text(freelancer_id)
    sources = default_source_service(session).current(freelancer_id)
    facts = hub_facts(session, freelancer_id)
    known = known_identifiers(
        owner.cognome if owner is not None else "",
        owner.nome if owner is not None else "",
        row.links,
        sources,
        facts,
    )
    return Inputs(cv, row.posizione, sources, facts, known)


@dataclass(frozen=True)
class Checked:
    """The rules' findings on one card."""

    years: YearsCheck
    band_ok: bool
    leaks: list[str]

    def as_json(self) -> dict[str, Any]:
        return {
            "years": {
                "mode": self.years.mode,
                "ok": self.years.ok,
                "low": self.years.low,
                "high": self.years.high,
            },
            "seniority_in_band": self.band_ok,
            "anonymity": self.leaks,
        }


def check(card: Card, inputs: Inputs, today: date) -> Checked:
    """Every rule on `card` against what it was written from. The anonymity is read
    against everything the person's data holds, the sources included, for every prompt
    version: a card of the CV alone is held to the same rule as one of all the sources."""
    return Checked(
        years=check_years(
            card,
            inputs.cv.testo,
            hub_completed_months=inputs.facts.completed_months,
            anni_admin=inputs.facts.anni_admin,
            today=today,
        ),
        band_ok=seniority_in_band(card),
        leaks=anonymity_leaks(card, inputs.known),
    )


def verdicts(checked: Checked, judgement: CardJudgement | None) -> dict[str, dict[str, str]]:
    """The verdict of each field of the card, `by` whoever settled it. A field a rule
    settled keeps the rule's verdict whatever the judge said: the years when the CV allowed
    a reliable parse (`YearsCheck.ok`), and a seniority outside its band. Without a
    judgement (the judge's own failure), only those fields have one."""
    result: dict[str, dict[str, str]] = {}
    if judgement is not None:
        result = {
            name: {"verdict": item.verdict, "reason": item.reason, "by": BY_JUDGE}
            for name, item in judgement.fields().items()
        }
    if checked.years.ok is not None:
        result["anni"] = {
            "verdict": "supported" if checked.years.ok else "contradicted",
            "reason": f"years recomputed from the dated periods ({checked.years.mode})",
            "by": BY_RULE,
        }
    if not checked.band_ok:
        result["seniority"] = {
            "verdict": "contradicted",
            "reason": "seniority outside the band the years fall in",
            "by": BY_RULE,
        }
    return result


# ---- totals and the comparison --------------------------------------------------------------


def _fields(result: CardEvalResult) -> dict[str, dict[str, str]]:
    return (result.judgement or {}).get("fields", {})


def _leaks_rule(result: CardEvalResult) -> bool:
    return bool((result.checks or {}).get("anonymity"))


def _leaks_judge(result: CardEvalResult) -> bool:
    return bool((result.judgement or {}).get("identifying"))


def _sum(values: Sequence[Decimal | None]) -> Decimal:
    return sum((value for value in values if value is not None), Decimal(0))


def _average(values: Sequence[int | None]) -> int | None:
    kept = [value for value in values if value is not None]
    return round(sum(kept) / len(kept)) if kept else None


def aggregate(results: Sequence[CardEvalResult]) -> dict[str, Any]:
    """The totals of a set of results, JSON-ready. `fields` counts the verdicts of every
    field of every card, a rule's included, and `judged` the cards the judging call
    answered. The costs are the sums of what was priced: a call with no price is `None` in
    its row and counted as no spend here, never as free in the row."""
    counted = [verdict["verdict"] for result in results for verdict in _fields(result).values()]
    return {
        "ran": len(results),
        "failed": sum(result.card is None for result in results),
        "judged": sum(
            result.judgement is not None and result.judgement.get("failure") is None
            for result in results
        ),
        "judge_failed": sum(
            result.judgement is not None and result.judgement.get("failure") is not None
            for result in results
        ),
        "fields": len(counted),
        "supported": counted.count("supported"),
        "unsupported": counted.count("unsupported"),
        "contradicted": counted.count("contradicted"),
        "contradicted_profiles": sum(
            any(v["verdict"] == "contradicted" for v in _fields(result).values())
            for result in results
        ),
        "invented": sum(len((r.judgement or {}).get("invented", [])) for r in results),
        "guard_failures": sum(result.guard_clean is False for result in results),
        "leaks_rule": sum(_leaks_rule(result) for result in results),
        "leaks_judge": sum(_leaks_judge(result) for result in results),
        "leaks": sum(_leaks_rule(r) or _leaks_judge(r) for r in results),
        "anni_checked": sum(result.anni_ok is not None for result in results),
        "anni_ok": sum(result.anni_ok is True for result in results),
        "latency_ms_avg": _average([result.latency_ms for result in results]),
        "judge_latency_ms_avg": _average([result.judge_latency_ms for result in results]),
        "cost_eur": str(_sum([result.cost_eur for result in results])),
        "judge_cost_eur": str(_sum([result.judge_cost_eur for result in results])),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


# What each compared measure is, and whether more of it is better: «supportati» and
# «anni» are shares, the rest counts or shares of what is wrong.
MEASURES: tuple[tuple[str, bool], ...] = (
    ("supported_rate", True),
    ("contradicted_rate", False),
    ("invented_per_profile", False),
    ("anni_ok_rate", True),
    ("guard_failures", False),
    ("leaks", False),
)


def _measures(totals: dict[str, Any]) -> dict[str, float | None]:
    ran = totals["ran"]
    return {
        "supported_rate": _rate(totals["supported"], totals["fields"]),
        "contradicted_rate": _rate(totals["contradicted"], totals["fields"]),
        "invented_per_profile": _rate(totals["invented"], totals["judged"]),
        "anni_ok_rate": _rate(totals["anni_ok"], totals["anni_checked"]),
        "guard_failures": float(totals["guard_failures"]) if ran else None,
        "leaks": float(totals["leaks"]) if ran else None,
    }


def compare(
    results: Sequence[CardEvalResult], baseline: Sequence[CardEvalResult]
) -> dict[str, Any]:
    """This run against a baseline, on the people both wrote a card for (a smaller run or
    a person the baseline missed does not tilt it): each measure of `MEASURES` on both,
    and «meglio», «uguale» or «peggio» (`n/d` when either has nothing to measure, as a
    baseline of the manual evaluation has no verdicts). A shared set of nobody is
    `profiles: 0`."""
    shared = {r.freelancer_id for r in results} & {r.freelancer_id for r in baseline}
    mine = aggregate([r for r in results if r.freelancer_id in shared])
    theirs = aggregate([r for r in baseline if r.freelancer_id in shared])
    now, before = _measures(mine), _measures(theirs)
    table: dict[str, dict[str, Any]] = {}
    for name, higher_is_better in MEASURES:
        a, b = now[name], before[name]
        if a is None or b is None:
            verdict = "n/d"
        elif a == b:
            verdict = "uguale"
        else:
            verdict = "meglio" if (a > b) == higher_is_better else "peggio"
        table[name] = {"this": a, "baseline": b, "verdict": verdict}
    return {
        "profiles": len(shared),
        "measures": table,
        "cost_eur": {"this": mine["cost_eur"], "baseline": theirs["cost_eur"]},
        "judge_cost_eur": {"this": mine["judge_cost_eur"], "baseline": theirs["judge_cost_eur"]},
        "latency_ms_avg": {"this": mine["latency_ms_avg"], "baseline": theirs["latency_ms_avg"]},
    }


def results_of(session: Session, run_id: UUID) -> list[CardEvalResult]:
    return list(
        session.scalars(
            select(CardEvalResult)
            .join(Freelancer, Freelancer.id == CardEvalResult.freelancer_id)
            .where(CardEvalResult.run_id == run_id, Freelancer.deleted_at.is_(None))
            .order_by(CardEvalResult.label, CardEvalResult.freelancer_id)
        )
    )


def _baseline(session: Session, task: str, run_id: UUID | None) -> CardEvalRun | None:
    """What a run is compared with. With `run_id`, that run: it has to exist, be of the
    task and be finished. Without, the newest finished run of the live configuration (the
    provider and model the task is set to now and the current `PROMPT_VERSION`) that has
    the automatic evaluation's totals, so a candidate is measured against production and
    not against the last candidate tried. `None` when there is none: the report then says
    so. A run cut short by an outage or the cap is never a reference."""
    finished = CardEvalRun.summary["complete"].as_boolean().is_(True)
    if run_id is not None:
        run = session.get(CardEvalRun, run_id)
        if run is None:
            raise NotFound("card_eval_run", run_id)
        if run.task != task or run.summary.get("complete") is not True:
            raise InvalidState("La run di riferimento non è una run completa di questo compito")
        return run
    live = session.get(AiTask, task)
    if live is None:
        return None
    return session.scalars(
        select(CardEvalRun)
        .where(
            CardEvalRun.task == task,
            finished,
            CardEvalRun.summary["format"].as_integer() == SUMMARY_FORMAT,
            CardEvalRun.provider == live.provider,
            CardEvalRun.model == live.model,
            CardEvalRun.prompt_version == PROMPT_VERSION,
        )
        .order_by(CardEvalRun.created_at.desc())
        .limit(1)
    ).first()


# ---- the run --------------------------------------------------------------------------------


def _spent(
    session: Session, task: str, freelancer_id: UUID, since: datetime, until: datetime | None
) -> Decimal | None:
    """What the calls made for this person from `since` (up to `until`) cost, as recorded
    under the task with `purpose="eval"`; `None` when there was no call (a CV with no text)
    or one of them had no price, which is never counted as free."""
    query = select(AiUsage.cost_eur).where(
        AiUsage.task == task,
        AiUsage.purpose == "eval",
        AiUsage.freelancer_id == freelancer_id,
        AiUsage.at >= since,
    )
    if until is not None:
        query = query.where(AiUsage.at < until)
    rows = session.scalars(query).all()
    if not rows or any(cost is None for cost in rows):
        return None
    return sum((cost for cost in rows if cost is not None), Decimal(0))


def _now(session: Session) -> datetime:
    return session.execute(select(func.clock_timestamp())).scalar_one()


def _summary(
    results: Sequence[CardEvalResult], *, planned: int, stopped: str | None
) -> dict[str, Any]:
    return {
        "format": SUMMARY_FORMAT,
        "profiles": planned,
        **aggregate(results),
        "complete": stopped is None,
        "stopped": stopped,
        "baseline": None,
    }


def run_eval(
    session: Session,
    llm_for: Callable[[UUID], LlmCall],
    judge_for: Callable[[UUID], LlmCall],
    *,
    task: str,
    provider: str,
    model: str,
    effort: str | None,
    targets: Sequence[Target],
    prompt_version: int = PROMPT_VERSION,
    judge_by: tuple[str, str],
    created_by: UUID | None = None,
    baseline_run_id: UUID | None = None,
    allow_disabled: bool = False,
    today: date | None = None,
) -> CardEvalRun:
    """Writes the card of each target through `CardWriter.compose` with `llm_for(person)`
    (the seam as `task`, answered by `provider` and `model`) under `prompt_version`, checks
    it (`check`), and has `judge_for(person)` judge it against the person's sources
    (`judge_by` is the provider and model that answers, for the report). Nothing else is
    touched: no live card, no task row. Both calls are recorded in `ai_usage` under `task`
    with `purpose="eval"` and count against its cap; each person's two costs are read from
    their own window.

    One person after the other, each result committed as it comes, so what was paid for is
    never lost. A provider that cannot answer, on either call, or a cap spent, stops the
    run there; so does anything else that ends it early (the run says `INTERRUPTED`, and
    the exception goes on). The run is then incomplete, and an incomplete run is never a
    baseline. The baseline is `baseline_run_id` or the newest finished run of the live
    configuration (`_baseline`); a run is compared with it on the people both have.

    `allow_disabled` only records that the run was allowed to reach a provider that is
    switched off (the seam's own `TaskOverride` is what lets it): the report says so."""
    if prompt_version not in PROMPT_VERSIONS:
        raise ValidationFailed(
            EVAL_ENTITY, "prompt_version", f"una fra {', '.join(map(str, PROMPT_VERSIONS))}"
        )
    if not targets:
        raise InvalidState("Nessun profilo da valutare")
    today = today or utcnow().date()
    run = CardEvalRun(
        task=task,
        provider=provider,
        model=model,
        effort=effort,
        prompt_version=prompt_version,
        summary={},
        created_by=created_by,
    )
    baseline = _baseline(session, task, baseline_run_id)
    # Until it ends, the run reads as unfinished: a ghost left by a killed process is
    # an incomplete run, never one that looks empty and complete.
    run.summary = _summary([], planned=len(targets), stopped=INTERRUPTED)
    session.add(run)
    session.commit()
    stopped: str | None = INTERRUPTED
    try:
        for target in targets:
            unanswered = _evaluate(
                session, run, target, llm_for, judge_for, task, prompt_version, today
            )
            if unanswered is not None:
                stopped = unanswered
                break
        else:
            stopped = None
    finally:
        session.rollback()
        results = results_of(session, run.id)
        summary = _summary(results, planned=len(targets), stopped=stopped)
        summary["allow_disabled"] = allow_disabled
        summary["judge"] = {"provider": judge_by[0], "model": judge_by[1]}
        if baseline is not None and stopped is None:
            summary["baseline"] = {
                "run_id": str(baseline.id),
                "provider": baseline.provider,
                "model": baseline.model,
                "prompt_version": baseline.prompt_version,
                **compare(results, results_of(session, baseline.id)),
            }
        run.summary = summary
        session.commit()
    return run


def _evaluate(
    session: Session,
    run: CardEvalRun,
    target: Target,
    llm_for: Callable[[UUID], LlmCall],
    judge_for: Callable[[UUID], LlmCall],
    task: str,
    prompt_version: int,
    today: date,
) -> str | None:
    """One person: the card, the rules, the judge, stored. The sentence of the outage
    that must end the run, or `None` when the person was done (a card that failed a guard
    is done)."""
    freelancer_id = target.freelancer_id
    started = _now(session)
    clock = time.perf_counter()
    card: Card | None = None
    failure: str | None = None
    unanswered: str | None = None
    try:
        composed = CardWriter(session, llm_for(freelancer_id)).compose(
            freelancer_id, prompt_version
        )
        card, failure = composed.card, composed.failure
    except NotFound:  # no CV on file: nothing to write from
        failure = "no_text"
    except LlmUnavailable as exc:
        failure, unanswered = "unavailable", exc.message
    latency = round((time.perf_counter() - clock) * 1000)
    written = _now(session)
    result = CardEvalResult(
        run_id=run.id,
        freelancer_id=freelancer_id,
        label=target.label,
        guard_clean=False if failure in GUARD_FAILURES else (True if card is not None else None),
        failure=failure,
        latency_ms=latency,
        card=card.model_dump(mode="json") if card is not None else None,
    )
    if card is not None:
        found = inputs_of(session, freelancer_id)
        checked = check(card, found, today)
        result.checks = checked.as_json()
        result.anni_ok = checked.years.ok
        result.seniority_ok = checked.band_ok
        judge_clock = time.perf_counter()
        judged: CardJudgement | None = None
        judge_failure: JudgeFailure | Literal["unavailable"] | None = None
        try:
            answer = judge(
                judge_for(freelancer_id),
                card,
                found.cv,
                found.posizione,
                found.sources,
                found.facts,
                today,
            )
            if isinstance(answer, CardJudgement):
                judged = answer
            else:
                judge_failure = answer
        except LlmUnavailable as exc:
            unanswered, judge_failure = exc.message, "unavailable"
        result.judge_latency_ms = round((time.perf_counter() - judge_clock) * 1000)
        result.judgement = {
            "failure": judge_failure,
            "fields": verdicts(checked, judged),
            "invented": list(judged.invented) if judged is not None else [],
            "identifying": list(judged.identifying) if judged is not None else [],
        }
        result.judge_cost_eur = _spent(session, task, freelancer_id, written, None)
    result.cost_eur = _spent(session, task, freelancer_id, started, written)
    session.add(result)
    session.commit()
    return unanswered


# ---- the report -----------------------------------------------------------------------------


def _mark(value: bool | None) -> str:
    return "-" if value is None else "ok" if value else "no"


def _percent(numerator: int, denominator: int) -> str:
    return f"{round(100 * numerator / denominator)}%" if denominator else "-"


def _euro(value: Decimal | str | None) -> str:
    return f"{Decimal(value):.4f}" if value is not None else "-"


def _measure_line(name: str, entry: dict[str, Any]) -> str:
    this, base = entry["this"], entry["baseline"]
    if name.endswith("_rate"):
        text = f"{_percent_of(this)} contro {_percent_of(base)}"
    else:
        text = (
            f"{'-' if this is None else f'{this:g}'} contro {'-' if base is None else f'{base:g}'}"
        )
    return f"{LABELS[name]} {text} ({entry['verdict']})"


def _percent_of(value: float | None) -> str:
    return "-" if value is None else f"{round(100 * value)}%"


LABELS = {
    "supported_rate": "campi supportati",
    "contradicted_rate": "campi contraddetti",
    "invented_per_profile": "inventati per scheda",
    "anni_ok_rate": "anni ricalcolati",
    "guard_failures": "respinte dalla guardia",
    "leaks": "schede con identificativi",
}


def format_report(run: CardEvalRun, results: Sequence[CardEvalResult]) -> str:
    """The run as `rebase cards-eval` prints it: one line per person (label and the first
    characters of the id, never a name), the fields the sources support, those they
    contradict, what was invented, whether the years recomputed from the CV agree, the
    guard, the anonymity, latency and the cost of the writing and of the judging; then the
    totals, who judged, whether the run is complete and how it stands against its
    baseline. It carries no card text and no reason of the judge's."""
    summary = run.summary
    effort = f", effort {run.effort}" if run.effort else ""
    lines = [
        f"Valutazione {run.task}: {run.provider}/{run.model}{effort}, prompt v{run.prompt_version}",
        f"run {run.id}",
    ]
    judge_by = summary.get("judge")
    if judge_by is not None:
        lines.append(f"giudice {judge_by['provider']}/{judge_by['model']}")
        if (judge_by["provider"], judge_by["model"]) == (run.provider, run.model):
            lines.append(
                "Nota: il giudice è lo stesso modello che ha scritto le schede, "
                "e tende a trovarle giuste."
            )
    if summary.get("allow_disabled"):
        lines.append(
            "ATTENZIONE: --allow-disabled. I CV sono andati a un fornitore che può essere "
            "spento, solo per questa valutazione."
        )
    lines += [
        "",
        f"{'profilo':<22}{'campi':<8}{'contr':<7}{'inv':<5}{'anni':<6}{'guardia':<9}{'anonima':<9}"
        f"{'ms':>7}{'euro':>9}{'giudice':>9}",
    ]
    for result in results:
        verdict_list = [v["verdict"] for v in _fields(result).values()]
        supported = (
            f"{verdict_list.count('supported')}/{len(verdict_list)}" if verdict_list else "-"
        )
        contradicted = str(verdict_list.count("contradicted")) if verdict_list else "-"
        invented = (
            str(len((result.judgement or {}).get("invented", [])))
            if result.judgement is not None and result.judgement.get("failure") is None
            else "-"
        )
        anonymous = (
            "-" if result.card is None else _mark(not (_leaks_rule(result) or _leaks_judge(result)))
        )
        note = f"  [{result.failure}]" if result.failure else ""
        if result.judgement is not None and result.judgement.get("failure") is not None:
            note += f"  [giudice: {result.judgement['failure']}]"
        lines.append(
            f"{f'{result.label} {str(result.freelancer_id)[:8]}':<22}{supported:<8}"
            f"{contradicted:<7}{invented:<5}{_mark(result.anni_ok):<6}"
            f"{_mark(result.guard_clean):<9}{anonymous:<9}"
            f"{result.latency_ms if result.latency_ms is not None else '-':>7}"
            f"{_euro(result.cost_eur):>9}{_euro(result.judge_cost_eur):>9}" + note
        )
    if "fields" not in summary:
        lines += ["", "Run senza totali: scritta dalla valutazione manuale."]
        return "\n".join(lines)
    ran, fields = summary["ran"], summary["fields"]
    lines += [
        "",
        f"Totale su {ran} di {summary['profiles']} profili ({summary['failed']} senza scheda, "
        f"{summary['judge_failed']} senza giudizio):",
        f"  campi supportati {summary['supported']}/{fields} "
        f"({_percent(summary['supported'], fields)}), non supportati {summary['unsupported']}, "
        f"contraddetti {summary['contradicted']} in {summary['contradicted_profiles']} schede, "
        f"inventati {summary['invented']}",
        f"  anni ricalcolati dal CV {summary['anni_ok']}/{summary['anni_checked']} "
        f"(gli altri al giudice); {summary['guard_failures']} respinte dalla guardia; "
        f"schede con identificativi {summary['leaks']} "
        f"(regole {summary['leaks_rule']}, giudice {summary['leaks_judge']})",
        f"  {_ms(summary['latency_ms_avg'])} in media per scheda, "
        f"{_ms(summary['judge_latency_ms_avg'])} per il giudizio",
        f"  costo scheda {_euro(summary['cost_eur'])} euro, "
        f"giudizio {_euro(summary['judge_cost_eur'])} euro",
    ]
    if not summary["complete"]:
        lines.append(f"INCOMPLETA, fermata qui: {summary['stopped']}")
    baseline = summary.get("baseline")
    if baseline is not None:
        lines.append(
            f"Rispetto alla run di riferimento {baseline['run_id']} "
            f"({baseline['provider']}/{baseline['model']}, prompt v{baseline['prompt_version']}), "
            f"su {baseline['profiles']} profili in comune:"
        )
        lines += [f"  {_measure_line(name, baseline['measures'][name])}" for name, _ in MEASURES]
        lines.append(
            f"  euro scheda {_euro(baseline['cost_eur']['this'])} contro "
            f"{_euro(baseline['cost_eur']['baseline'])}, giudizio "
            f"{_euro(baseline['judge_cost_eur']['this'])} contro "
            f"{_euro(baseline['judge_cost_eur']['baseline'])}"
        )
    elif summary["complete"]:
        lines.append(
            "Nessun riferimento: nessuna run completa della configurazione attuale del compito "
            f"(fornitore e modello di «Modelli AI», prompt v{PROMPT_VERSION}). "
            "Questa lo diventa se è quella; altrimenti indica --baseline RUN_ID."
        )
    return "\n".join(lines)


def _ms(value: int | None) -> str:
    return f"{value} ms" if value is not None else "- ms"
