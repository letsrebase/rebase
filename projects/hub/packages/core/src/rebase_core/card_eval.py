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
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from rebase_core.audit import AdminActionService
from rebase_core.card_eval_schemas import (
    CardEvalRead,
    EvalProfileRead,
    EvalResultRead,
    EvalRunRead,
)
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.freelancers import ENTITY
from rebase_core.models import (
    CARD_EVAL_LABEL_MAX_LENGTH,
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

    def save_draft(self, freelancer_id: UUID, card: Card) -> EvalProfileRead:
        """The builder's draft of the expected card (`draft_expected`): written as a
        `bozza`, signed by nobody, over a profile that is not accepted yet. An accepted
        profile is an admin's word and is never overwritten by a model."""
        profile = self._profile(freelancer_id)
        if profile.status == "accettata":
            raise InvalidState("Il profilo è già accettato: lo cambia solo un admin")
        profile.expected_card = card.model_dump(mode="json")
        self.session.commit()
        return self.profile_read(freelancer_id)

    def set_expected(self, admin_id: UUID, freelancer_id: UUID, card: Card) -> EvalProfileRead:
        """An admin's correction of the expected card. The card carries the expected
        `seniority` and `anni`. The profile goes back to `bozza`: the reference changed,
        and an admin accepts it again. The trail names the fields that changed."""
        profile = self._profile(freelancer_id)
        after = card.model_dump(mode="json")
        changed = _changed_fields(profile.expected_card, after)
        profile.expected_card = after
        profile.status = "bozza"
        profile.corrected_by = admin_id
        self.session.commit()
        if changed:
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
