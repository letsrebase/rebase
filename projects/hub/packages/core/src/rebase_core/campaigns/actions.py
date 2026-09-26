"""What an action is measured against, and whether a person has done it since (spec
§ 6.2). Phase 1 uses `done_at` to skip a mail that would ask for something already done
(spec § 5.3); phase 2 stamps its answer on the row."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from rebase_core.campaigns.states import Candidate, card_state
from rebase_core.models import CampaignRecipient, Comment, Company, Freelancer, Login, User

# What `MemberService.replace_cv` writes on a card's first CV (`members.py:303`).
CV_COMMENT_PREFIX = "CV caricato dalla persona"


def _card(session: Session, email: str) -> Freelancer | None:
    """The live card of `email`, without its CV's bytes: `cv_size` says whether one is
    there, and a snapshot or a check runs once per recipient."""
    return session.scalar(
        select(Freelancer)
        .join(User, User.id == Freelancer.user_id)
        .where(func.lower(User.email) == email.lower(), Freelancer.deleted_at.is_(None))
        .options(defer(Freelancer.cv_bytes))
    )


def _complete(card: Freelancer) -> bool:
    return (
        card_state(card.cv_size, card.tariffa_giornaliera, card.posizione, card.remoto)
        == "completo"
    )


def snapshot(session: Session, candidate: Candidate, now: datetime) -> dict[str, Any]:
    card = _card(session, candidate.email)
    richieste: dict[str, str] = {}
    if candidate.company_ids:
        rows = session.execute(
            select(Company.id, Company.updated_at).where(Company.id.in_(candidate.company_ids))
        ).all()
        richieste = {str(company_id): updated.isoformat() for company_id, updated in rows}
    return {
        "t": now.isoformat(),
        "ha_scheda": card is not None,
        "ha_cv": card is not None and card.cv_size is not None,
        "completa": card is not None and _complete(card),
        "richieste": richieste,
    }


def done_at(session: Session, recipient: CampaignRecipient, azione: str) -> datetime | None:
    prima = recipient.prima or {}
    since = datetime.fromisoformat(prima["t"])
    if azione == "entrato":
        user_id = recipient.user_id or session.scalar(
            select(User.id).where(func.lower(User.email) == recipient.email)
        )
        if user_id is None:
            return None
        return session.scalar(
            select(func.min(Login.logged_at)).where(
                Login.user_id == user_id, Login.logged_at > since
            )
        )
    if azione in ("cv", "scheda_completa", "profilo_creato"):
        card = _card(session, recipient.email)
        if card is None:
            return None
        if azione == "profilo_creato":
            created = not prima.get("ha_scheda") and card.created_at > since
            return card.created_at if created else None
        if azione == "cv":
            if prima.get("ha_cv") or card.cv_size is None:
                return None
            commented = session.scalar(
                select(func.min(Comment.created_at)).where(
                    Comment.entity_type == "freelancer",
                    Comment.entity_id == card.id,
                    Comment.testo.startswith(CV_COMMENT_PREFIX),
                    Comment.created_at > since,
                )
            )
            return commented or card.updated_at
        return card.updated_at if not prima.get("completa") and _complete(card) else None
    if azione == "richiesta_aggiornata":
        before = prima.get("richieste") or {}
        moments: list[datetime] = []
        for company_id, was in before.items():
            updated = session.scalar(select(Company.updated_at).where(Company.id == company_id))
            if updated is not None and updated > max(datetime.fromisoformat(was), since):
                moments.append(updated)
        return min(moments) if moments else None
    return None  # `pigro_cliente`: phase 3 (spec § 6.3)
