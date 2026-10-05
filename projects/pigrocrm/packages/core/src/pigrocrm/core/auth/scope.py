"""A member's azienda scope: read off the row, checked on the way in, written as the
flag and the rows (REB-633, spec 2026-10-03 §1.11)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, ActorType, Role
from pigrocrm.core.auth.models import User, UserLegalEntity
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.errors import ValidationFailed


def scope_of(user: User) -> tuple[UUID, ...] | None:
    """What `Actor.aziende` is for `user`: `None` while `ambito_limitato` is false, the
    rows otherwise, an empty tuple included."""
    aziende = user.aziende
    return None if aziende is None else tuple(aziende)


def actor_for(user: User, kind: Literal["user", "mcp", "system"] = "user") -> Actor:
    """The actor a signed-in person, their agent's token or a job acting on their
    behalf runs as: role and scope both read off the row, so no caller builds an
    `Actor` for a user by hand and forgets the second."""
    actor_type: ActorType = kind
    role: Role = user.ruolo  # type: ignore[assignment]
    return Actor(id=user.id, type=actor_type, role=role, aziende=scope_of(user))


def check_scope(session: Session, entity: str, aziende: list[UUID] | None) -> list[UUID] | None:
    """The scope a request names, as the row will hold it: `None` stays `None`; a list
    must not be empty («nessuna azienda» is a deactivation, not a scope) and must name
    active aziende of this space, each once. Refuses by field, so the Team panel puts
    the sentence under the right control."""
    if aziende is None:
        return None
    if not aziende:
        raise ValidationFailed(
            entity,
            "aziende",
            "una lista vuota non e' un ambito: per togliere una persona dallo spazio, disattivala",
            expected="almeno un'azienda, oppure null per tutte",
        )
    unique = list(dict.fromkeys(aziende))
    active = set(
        session.scalars(
            select(LegalEntity.id).where(LegalEntity.id.in_(unique), LegalEntity.attiva.is_(True))
        ).all()
    )
    missing = [str(a) for a in unique if a not in active]
    if missing:
        raise ValidationFailed(
            entity,
            "aziende",
            f"azienda non attiva o inesistente: {', '.join(missing)}",
            expected="id di aziende attive dello spazio",
        )
    return unique


def apply_scope(session: Session, user: User, aziende: list[UUID] | None) -> None:
    """Write the scope on the row: the flag and the rows together, so a reader of either
    sees the same truth. `None` clears both; a list sets the flag and replaces the rows."""
    if aziende is None:
        user.ambito_limitato = False
        user.scopes = []
    else:
        user.ambito_limitato = True
        user.scopes = [UserLegalEntity(user_id=user.id, azienda_id=azienda) for azienda in aziende]
    session.flush()


def active_only(session: Session, aziende: list[UUID]) -> list[UUID]:
    """The ids among `aziende` that still name an active azienda: what an invitation
    accepted after a deactivation keeps (spec §1.11), possibly nothing."""
    if not aziende:
        return []
    return list(
        session.scalars(
            select(LegalEntity.id).where(LegalEntity.id.in_(aziende), LegalEntity.attiva.is_(True))
        ).all()
    )


__all__ = ["active_only", "actor_for", "apply_scope", "check_scope", "scope_of"]
