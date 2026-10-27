"""The cards' evaluation set (REB-835, design 2026-10-27, «Evaluation before a change
goes live»): about fifteen real profiles, each with the card an admin wrote by hand as
the expected answer, and the runs that measure a provider, a model or a prompt against
them before it goes live.

The expected card and the card a run wrote are derived from a CV, so they are personal
data: they live in this database only (never the repository, a log, Linear or a PR), and
every row that holds one is deleted with the person (`ON DELETE CASCADE`, migration
0037). Nothing is echoed to a log or to stdout beyond a label, a name and an admin link.
An `AdminAction` of this module names the fields that changed, never their values.

A profile is `bozza` while it is the builder's draft and `accettata` once an admin
corrected it and accepted it; any later edit makes it a `bozza` again, since the
reference changed. Only accepted profiles take part in a run.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from rebase_core.audit import AdminActionService
from rebase_core.card_eval_schemas import (
    CardEvalRead,
    EvalProfileRead,
    EvalResultRead,
    EvalRunRead,
)
from rebase_core.cards import PROMPT_VERSION, CardWriter, failure_sentence
from rebase_core.errors import InvalidState, LlmUnavailable, NotFound, ValidationFailed
from rebase_core.freelancers import ENTITY
from rebase_core.llm import LlmCall
from rebase_core.models import (
    CARD_EVAL_LABEL_MAX_LENGTH,
    AiTask,
    AiUsage,
    CardEvalProfile,
    CardEvalResult,
    CardEvalRun,
    Freelancer,
    User,
)
from rebase_core.team_schemas import Card

logger = logging.getLogger(__name__)

EVAL_ENTITY = "card_eval"
# What an admin rates on a generated card, each from 1 (wrong) to 3 (as good as the
# expected one): the three fields no rule can compare.
RATED_FIELDS = ("ruolo", "competenze", "sintesi")
RATING_MIN, RATING_MAX = 1, 3
# Runs `list` returns: the newest, enough to compare a change with the last few.
RUNS_SHOWN = 20


def _changed_fields(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    return sorted(key for key in after if before is None or before.get(key) != after[key])


class CardEvalService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- reads -------------------------------------------------------------------------

    def list(self) -> CardEvalRead:
        """Every profile of a live person, and the newest runs with their results."""
        corrector = aliased(User)
        profiles = [
            self._profile_read(profile, user, fixer)
            for profile, user, fixer in self.session.execute(
                select(CardEvalProfile, User, corrector)
                .join(Freelancer, Freelancer.id == CardEvalProfile.freelancer_id)
                .join(User, User.id == Freelancer.user_id)
                .outerjoin(corrector, corrector.id == CardEvalProfile.corrected_by)
                .where(Freelancer.deleted_at.is_(None))
                .order_by(CardEvalProfile.created_at, CardEvalProfile.freelancer_id)
            )
        ]
        runs = self.session.scalars(
            select(CardEvalRun).order_by(CardEvalRun.created_at.desc()).limit(RUNS_SHOWN)
        ).all()
        return CardEvalRead(profiles=profiles, runs=[self.run_read(run) for run in runs])

    def run_read(self, run: CardEvalRun) -> EvalRunRead:
        author = self.session.get(User, run.created_by) if run.created_by is not None else None
        results = self.session.scalars(
            select(CardEvalResult)
            .join(Freelancer, Freelancer.id == CardEvalResult.freelancer_id)
            .where(CardEvalResult.run_id == run.id, Freelancer.deleted_at.is_(None))
            .order_by(CardEvalResult.label, CardEvalResult.freelancer_id)
        ).all()
        return EvalRunRead(
            id=run.id,
            task=run.task,
            provider=run.provider,
            model=run.model,
            effort=run.effort,
            prompt_version=run.prompt_version,
            created_at=run.created_at,
            created_by_nome=_full_name(author),
            summary=run.summary,
            results=[_result_read(result) for result in results],
        )

    # ---- writes ------------------------------------------------------------------------

    def ensure(self, freelancer_id: UUID, label: str) -> CardEvalProfile:
        """The person's profile, made with `label` if there is none yet; the label of an
        existing one is left alone. The command line's way in, which has no admin to
        sign: `add` is the admin's, and writes the trail."""
        cleaned = _label(label)
        self._require_live(freelancer_id)
        profile = self.session.get(CardEvalProfile, freelancer_id)
        if profile is None:
            profile = CardEvalProfile(freelancer_id=freelancer_id, label=cleaned)
            self.session.add(profile)
            self.session.commit()
        return profile

    def add(self, admin_id: UUID, freelancer_id: UUID, label: str) -> EvalProfileRead:
        if self.session.get(CardEvalProfile, freelancer_id) is not None:
            raise InvalidState("Questa persona è già nel set di valutazione")
        self.ensure(freelancer_id, label)
        AdminActionService(self.session).record(
            EVAL_ENTITY, freelancer_id, "eval_add", admin_id, {"label": _label(label)}
        )
        return self.profile_read(freelancer_id)

    def remove(self, admin_id: UUID, freelancer_id: UUID) -> None:
        profile = self._profile(freelancer_id)
        self.session.delete(profile)
        self.session.commit()
        AdminActionService(self.session).record(EVAL_ENTITY, freelancer_id, "eval_remove", admin_id)

    def require_draftable(self, freelancer_id: UUID) -> None:
        """`NotFound` without a profile, `InvalidState` for one an admin has put a hand on:
        asked before a model is called, so a draft that cannot be saved spends nothing."""
        self._draftable(freelancer_id)

    def _draftable(self, freelancer_id: UUID) -> CardEvalProfile:
        profile = self._profile(freelancer_id)
        if profile.status == "accettata" or profile.corrected_by is not None:
            raise InvalidState(
                "Un admin ha già messo mano a questa scheda: un modello non la riscrive"
            )
        return profile

    def save_draft(self, freelancer_id: UUID, card: Card) -> EvalProfileRead:
        """The builder's draft of the expected card (`draft_expected`): written as a
        `bozza`, signed by nobody. A profile an admin has put a hand on, corrected or
        accepted, is their word and is never overwritten by a model."""
        profile = self._draftable(freelancer_id)
        profile.expected_card = card.model_dump(mode="json")
        self.session.commit()
        return self.profile_read(freelancer_id)

    def set_expected(self, admin_id: UUID, freelancer_id: UUID, card: Card) -> EvalProfileRead:
        """An admin's correction of the expected card. The card carries the expected
        `seniority` and `anni`. The profile goes back to `bozza`: the reference changed,
        and an admin accepts it again. The trail names the fields that changed. A save
        that changes nothing is a no-op, so pressing it on an accepted card neither demotes
        it nor leaves a demotion the trail does not show."""
        profile = self._profile(freelancer_id)
        after = card.model_dump(mode="json")
        changed = _changed_fields(profile.expected_card, after)
        if not changed and profile.expected_card is not None:
            return self.profile_read(freelancer_id)
        profile.expected_card = after
        profile.status = "bozza"
        profile.corrected_by = admin_id
        self.session.commit()
        AdminActionService(self.session).record(
            EVAL_ENTITY, freelancer_id, "eval_expected", admin_id, {"changed": changed}
        )
        return self.profile_read(freelancer_id)

    def accept(self, admin_id: UUID, freelancer_id: UUID) -> EvalProfileRead:
        profile = self._profile(freelancer_id)
        if profile.expected_card is None:
            raise InvalidState("Scrivi prima la scheda attesa: poi si può accettare")
        if profile.status != "accettata":
            profile.status = "accettata"
            profile.corrected_by = admin_id
            self.session.commit()
            AdminActionService(self.session).record(
                EVAL_ENTITY, freelancer_id, "eval_accept", admin_id
            )
        return self.profile_read(freelancer_id)

    def rate(
        self, admin_id: UUID, run_id: UUID, ratings: dict[UUID, dict[str, int]]
    ) -> EvalRunRead:
        """The admin's ratings of a run's role, skills and summary, per person: only
        `RATED_FIELDS`, each a whole number from 1 to 3. All or nothing: one bad value
        stores none."""
        run = self.session.get(CardEvalRun, run_id)
        if run is None:
            raise NotFound("card_eval_run", run_id)
        results = {
            result.freelancer_id: result
            for result in self.session.scalars(
                select(CardEvalResult).where(CardEvalResult.run_id == run_id)
            )
        }
        for freelancer_id, given in ratings.items():
            if freelancer_id not in results:
                raise NotFound("card_eval_result", freelancer_id)
            for field, value in given.items():
                if field not in RATED_FIELDS:
                    raise ValidationFailed("card_eval_run", field, "campo non valutabile")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not RATING_MIN <= value <= RATING_MAX
                ):
                    raise ValidationFailed(
                        "card_eval_run", field, f"il voto va da {RATING_MIN} a {RATING_MAX}"
                    )
        for freelancer_id, given in ratings.items():
            result = results[freelancer_id]
            result.ratings = {**(result.ratings or {}), **given}
        self.session.commit()
        AdminActionService(self.session).record(
            EVAL_ENTITY, run_id, "eval_rate", admin_id, {"persone": len(ratings)}
        )
        return self.run_read(run)

    # ---- helpers -----------------------------------------------------------------------

    def profile_read(self, freelancer_id: UUID) -> EvalProfileRead:
        profile = self._profile(freelancer_id)
        row = self.session.get(Freelancer, freelancer_id)
        assert row is not None
        user = self.session.get(User, row.user_id)
        assert user is not None
        corrector = self.session.get(User, profile.corrected_by) if profile.corrected_by else None
        return self._profile_read(profile, user, corrector)

    def _profile_read(
        self, profile: CardEvalProfile, user: User, corrector: User | None
    ) -> EvalProfileRead:
        return EvalProfileRead(
            freelancer_id=profile.freelancer_id,
            nome=user.nome,
            cognome=user.cognome,
            label=profile.label,
            status=profile.status,  # type: ignore[arg-type]
            expected=(
                Card.model_validate(profile.expected_card)
                if profile.expected_card is not None
                else None
            ),
            corrected_by_nome=_full_name(corrector),
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    def _profile(self, freelancer_id: UUID) -> CardEvalProfile:
        profile = self.session.get(CardEvalProfile, freelancer_id)
        if profile is None:
            raise NotFound(EVAL_ENTITY, freelancer_id)
        return profile

    def _require_live(self, freelancer_id: UUID) -> Freelancer:
        row = self.session.get(Freelancer, freelancer_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(ENTITY, freelancer_id)
        return row


def _label(label: str) -> str:
    cleaned = label.strip()
    if not cleaned or len(cleaned) > CARD_EVAL_LABEL_MAX_LENGTH:
        raise ValidationFailed(
            EVAL_ENTITY, "label", f"da 1 a {CARD_EVAL_LABEL_MAX_LENGTH} caratteri"
        )
    return cleaned


def _full_name(user: User | None) -> str | None:
    return f"{user.nome} {user.cognome}".strip() if user is not None else None


def _result_read(result: CardEvalResult) -> EvalResultRead:
    return EvalResultRead(
        freelancer_id=result.freelancer_id,
        label=result.label,
        anni_ok=result.anni_ok,
        seniority_ok=result.seniority_ok,
        guard_clean=result.guard_clean,
        failure=result.failure,
        latency_ms=result.latency_ms,
        cost_eur=result.cost_eur,
        card=Card.model_validate(result.card) if result.card is not None else None,
        ratings=result.ratings,
    )


def draft_expected(session: Session, freelancer_id: UUID, llm: LlmCall) -> EvalProfileRead:
    """The builder's first draft of a profile's expected card (design 2026-10-27, «Lorenzo's
    answers»): the card the production writer makes from the person's CV, stored as a
    `bozza` for an admin to correct and accept. Nothing is written to the live card. The
    profile has to exist (`CardEvalService.ensure`) and not be one an admin touched: both are
    refused before the model is called. A CV with no text, a refusal or a
    card the guards refuse is an `InvalidState` with our sentence, since there is nothing
    to draft from; a provider that cannot answer raises `LlmUnavailable` as everywhere
    else."""
    service = CardEvalService(session)
    service.require_draftable(freelancer_id)  # refused before anything is spent
    composed = CardWriter(session, llm).compose(freelancer_id)
    if composed.card is None:
        assert composed.failure is not None
        raise InvalidState(failure_sentence(composed.failure))
    return service.save_draft(freelancer_id, composed.card)


# ---- measuring a provider, a model or a prompt against the accepted profiles ---------------

# What is a failure of the guards, not of the model or the CV: a card that names the person
# or carries an address, that still names an account, a repository, a site or an employer, or
# that still gives the gender away after its one rewrite.
GUARD_FAILURES = ("identifying", "source_identifying", "gendered")
# The three measures a rule can take, and the order the report shows them in.
MEASURES = ("anni_ok", "seniority_ok", "guard_clean")
ANNI_TOLERANCE = 1


@dataclass(frozen=True)
class AgreementRow:
    """How one card measures against the expected one. `None` is «not measured»: the model
    gave no card, so years and seniority have nothing to compare, and the guard measure
    is `False` only when a guard refused it."""

    anni_ok: bool | None
    seniority_ok: bool | None
    guard_clean: bool | None


def agreement(expected: Card, got: Card | None, failure: str | None = None) -> AgreementRow:
    """`anni` within `ANNI_TOLERANCE` year of the expected value, or both `None`;
    seniority equal; guard clean unless `failure` is a guard's. Role, skills and summary
    are not compared: an admin rates them."""
    guard = False if failure in GUARD_FAILURES else (True if got is not None else None)
    if got is None:
        return AgreementRow(None, None, guard)
    if expected.anni is None or got.anni is None:
        anni_ok = expected.anni is None and got.anni is None
    else:
        anni_ok = abs(expected.anni - got.anni) <= ANNI_TOLERANCE
    return AgreementRow(anni_ok, expected.seniority == got.seniority, guard)


def _delta(this: dict[str, Any], baseline: dict[str, Any]) -> dict[str, str]:
    """«meglio», «uguale» or «peggio» per measure, by the share of profiles that agree (a
    cross-multiplication, so the two runs may have had a different number of profiles)."""
    delta: dict[str, str] = {}
    for measure in MEASURES:
        now, before = this[measure] * baseline["ran"], baseline[measure] * this["ran"]
        delta[measure] = "meglio" if now > before else "peggio" if now < before else "uguale"
    return delta


def _baseline(session: Session, task: str, run_id: UUID | None) -> CardEvalRun | None:
    """What a run is compared with. With `run_id`, that run: it has to exist, be of the
    task and be finished. Without, the newest finished run of the live configuration: the
    provider and model the task is set to now and the current `PROMPT_VERSION`, so a
    candidate is measured against production and not against the last candidate tried.
    `None` when there is none: the report then says so. A run cut short by an outage or the
    cap is never a reference."""
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
            CardEvalRun.provider == live.provider,
            CardEvalRun.model == live.model,
            CardEvalRun.prompt_version == PROMPT_VERSION,
        )
        .order_by(CardEvalRun.created_at.desc())
        .limit(1)
    ).first()


INTERRUPTED = "La run si è interrotta prima della fine"


def run_eval(
    session: Session,
    llm_for: Callable[[UUID], LlmCall],
    *,
    task: str,
    provider: str,
    model: str,
    effort: str | None,
    created_by: UUID | None = None,
    baseline_run_id: UUID | None = None,
    allow_disabled: bool = False,
) -> EvalRunRead:
    """Runs the accepted profiles of live people through `CardWriter.compose` with
    `llm_for(person)`, the seam as `task` but answered by `provider` and `model`
    (`TaskOverride`), so the spend is recorded under that task with `purpose="eval"` and
    counts against its daily cap. Nothing else is touched: no live card, no task row.
    `allow_disabled` only records that the run was allowed to reach a provider that is
    switched off (the seam's own `TaskOverride` is what lets it): the report says so.

    One call per person, one after the other, each result committed as it comes, so what
    was paid for is never lost. A provider that cannot answer, or a cap spent, stops the
    run there; so does anything else that ends it early (the run says `INTERRUPTED`, and
    the exception goes on). The run is then incomplete, and an incomplete run is never a
    baseline. The baseline is `baseline_run_id` or the newest finished run of the live
    configuration (`_baseline`). Raises `InvalidState` when no profile is accepted yet."""
    profiles = session.scalars(
        select(CardEvalProfile)
        .join(Freelancer, Freelancer.id == CardEvalProfile.freelancer_id)
        .where(CardEvalProfile.status == "accettata", Freelancer.deleted_at.is_(None))
        .order_by(CardEvalProfile.label, CardEvalProfile.freelancer_id)
    ).all()
    if not profiles:
        raise InvalidState("Nessun profilo accettato nel set di valutazione")
    run = CardEvalRun(
        task=task,
        provider=provider,
        model=model,
        effort=effort,
        prompt_version=PROMPT_VERSION,
        summary={},
        created_by=created_by,
    )
    baseline = _baseline(session, task, baseline_run_id)
    # Until it ends, the run reads as unfinished: a ghost left by a killed process is
    # an incomplete run, never one that looks empty and complete.
    run.summary = _summary([], planned=len(profiles), stopped=INTERRUPTED)
    session.add(run)
    session.commit()
    stopped: str | None = INTERRUPTED
    try:
        for profile in profiles:
            expected = Card.model_validate(profile.expected_card)
            freelancer_id, label = profile.freelancer_id, profile.label
            started: datetime = session.execute(select(func.clock_timestamp())).scalar_one()
            clock = time.perf_counter()
            card: Card | None = None
            failure: str | None = None
            unanswered: str | None = None
            try:
                composed = CardWriter(session, llm_for(freelancer_id)).compose(freelancer_id)
                card, failure = composed.card, composed.failure
            except LlmUnavailable as exc:
                failure, unanswered = "unavailable", exc.message
            latency = round((time.perf_counter() - clock) * 1000)
            row = agreement(expected, card, failure)
            session.add(
                CardEvalResult(
                    run_id=run.id,
                    freelancer_id=freelancer_id,
                    label=label,
                    anni_ok=row.anni_ok,
                    seniority_ok=row.seniority_ok,
                    guard_clean=row.guard_clean,
                    failure=failure,
                    latency_ms=latency,
                    cost_eur=_spent(session, task, freelancer_id, started),
                    card=card.model_dump(mode="json") if card is not None else None,
                )
            )
            session.commit()
            if unanswered is not None:
                stopped = unanswered
                break
        else:
            stopped = None
    finally:
        session.rollback()
        results = list(
            session.scalars(select(CardEvalResult).where(CardEvalResult.run_id == run.id))
        )
        summary = _summary(results, planned=len(profiles), stopped=stopped)
        summary["allow_disabled"] = allow_disabled
        if baseline is not None and stopped is None:
            summary["baseline"] = {
                "run_id": str(baseline.id),
                "provider": baseline.provider,
                "model": baseline.model,
                "delta": _delta(summary, baseline.summary),
            }
        run.summary = summary
        session.commit()
    return CardEvalService(session).run_read(run)


def _spent(session: Session, task: str, freelancer_id: UUID, since: datetime) -> Decimal | None:
    """What the calls made for this person since `since` cost, as recorded under the task
    with `purpose="eval"`; `None` when there was no call (a CV with no text) or one of them
    had no price, which is never counted as free."""
    rows = session.scalars(
        select(AiUsage.cost_eur).where(
            AiUsage.task == task,
            AiUsage.purpose == "eval",
            AiUsage.freelancer_id == freelancer_id,
            AiUsage.at >= since,
        )
    ).all()
    if not rows or any(cost is None for cost in rows):
        return None
    return sum((cost for cost in rows if cost is not None), Decimal(0))


def _summary(results: list[CardEvalResult], *, planned: int, stopped: str | None) -> dict[str, Any]:
    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    costs = [r.cost_eur for r in results if r.cost_eur is not None]
    return {
        "profiles": planned,
        "ran": len(results),
        "anni_ok": sum(r.anni_ok is True for r in results),
        "seniority_ok": sum(r.seniority_ok is True for r in results),
        # Clean unless a guard refused the card: a refusal, a cut-off answer or an outage is
        # not the guard's, and has its own count in `failed`.
        "guard_clean": sum(r.guard_clean is not False for r in results),
        "guard_failures": sum(r.guard_clean is False for r in results),
        "failed": sum(r.card is None for r in results),
        "latency_ms_avg": round(sum(latencies) / len(latencies)) if latencies else None,
        "cost_eur": str(sum(costs, Decimal(0))),
        "complete": stopped is None,
        "stopped": stopped,
        "baseline": None,
    }


# ---- the report -----------------------------------------------------------------------------

TO_RATE = "da valutare"


def _mark(value: bool | None) -> str:
    return "-" if value is None else "ok" if value else "no"


def format_report(run: EvalRunRead) -> str:
    """The run as `rebase cards-eval` prints it: one line per person (label and the first
    characters of the id, never a name), the three automatic measures, the three ratings
    (`da valutare` until an admin saved them), latency and cost, then the totals, whether
    the run is complete and how it stands against the previous finished run of the task.
    It carries no card text."""
    summary = run.summary
    effort = f", effort {run.effort}" if run.effort else ""
    lines = [
        f"Valutazione {run.task}: {run.provider}/{run.model}{effort}, prompt v{run.prompt_version}",
        f"run {run.id}",
    ]
    if run.summary.get("allow_disabled"):
        lines.append(
            "ATTENZIONE: --allow-disabled. I CV sono andati a un fornitore che può essere "
            "spento, solo per questa valutazione."
        )
    lines += [
        "",
        f"{'profilo':<22}{'anni':<6}{'seniority':<11}{'guardia':<9}"
        + "".join(f"{field:<13}" for field in RATED_FIELDS)
        + f"{'ms':>7}{'euro':>10}",
    ]
    for result in run.results:
        ratings = result.ratings or {}
        cost = f"{result.cost_eur:.4f}" if result.cost_eur is not None else "-"
        failure = f"  [{result.failure}]" if result.failure else ""
        lines.append(
            f"{f'{result.label} {str(result.freelancer_id)[:8]}':<22}"
            f"{_mark(result.anni_ok):<6}{_mark(result.seniority_ok):<11}"
            f"{_mark(result.guard_clean):<9}"
            + "".join(f"{ratings.get(field, TO_RATE)!s:<13}" for field in RATED_FIELDS)
            + f"{result.latency_ms if result.latency_ms is not None else '-':>7}{cost:>10}"
            + failure
        )
    ran = summary["ran"]
    lines += [
        "",
        f"Totale su {ran} di {summary['profiles']} profili: "
        f"anni {summary['anni_ok']}/{ran}, seniority {summary['seniority_ok']}/{ran}, "
        f"guardia {summary['guard_clean']}/{ran} "
        f"({summary['guard_failures']} respinte dalla guardia), "
        f"{summary['failed']} senza scheda; "
        f"{summary['latency_ms_avg'] if summary['latency_ms_avg'] is not None else '-'} ms "
        f"in media; {Decimal(summary['cost_eur']):.4f} euro",
    ]
    if not summary["complete"]:
        lines.append(f"INCOMPLETA, fermata qui: {summary['stopped']}")
    baseline = summary.get("baseline")
    if baseline is not None:
        delta = baseline["delta"]
        lines.append(
            f"Rispetto alla run precedente ({baseline['provider']}/{baseline['model']}): "
            f"anni {delta['anni_ok']}, seniority {delta['seniority_ok']}, "
            f"guardia {delta['guard_clean']}"
        )
    elif summary["complete"]:
        lines.append(
            "Nessun riferimento: nessuna run completa della configurazione attuale del compito "
            f"(fornitore e modello di «Modelli AI», prompt v{PROMPT_VERSION}). "
            "Questa lo diventa se è quella; altrimenti indica --baseline RUN_ID."
        )
    return "\n".join(lines)
