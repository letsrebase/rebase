"""A member's azienda scope: read off the row, checked on the way in, written as the
flag and the rows (REB-633, spec 2026-10-03 §1.11)."""

from __future__ import annotations

from typing import Literal, cast, get_args
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, ActorType, Role
from pigrocrm.core.auth.models import User, UserLegalEntity
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.legal_entities.models import LegalEntity


def as_role(value: str) -> Role:
    """The `Role` a `role` column holds: the column is a plain string, and a value outside
    the literal means a row nobody should have written, which is refused here rather than
    carried into an `Actor` that would refuse it later with a pydantic error."""
    for role in get_args(Role):
        if value == role:
            return cast(Role, role)
    raise ValueError(f"unknown role {value!r} on a user row")


def scope_of(user: User) -> tuple[UUID, ...] | None:
    """What `Actor.legal_entities` is for `user`: `None` while `limited_scope` is false, the
    rows otherwise, an empty tuple included."""
    legal_entities = user.legal_entities
    return None if legal_entities is None else tuple(legal_entities)


def actor_for(user: User, kind: Literal["user", "mcp", "system"] = "user") -> Actor:
    """The actor a signed-in person, their agent's token or a job acting on their
    behalf runs as: role and scope both read off the row, so no caller builds an
    `Actor` for a user by hand and forgets the second."""
    actor_type: ActorType = kind
    return Actor(
        id=user.id, type=actor_type, role=as_role(user.role), legal_entities=scope_of(user)
    )


def check_scope(
    session: Session, entity: str, legal_entities: list[UUID] | None
) -> list[UUID] | None:
    """The scope a request names, as the row will hold it: `None` stays `None`; a list
    must not be empty («nessuna azienda» is a deactivation, not a scope) and must name
    active aziende of this space, each once. Refuses by field, so the Team panel puts
    the sentence under the right control."""
    if legal_entities is None:
        return None
    if not legal_entities:
        raise ValidationFailed(
            entity,
            "legal_entities",
            "una lista vuota non e' un ambito: per togliere una persona dallo spazio, disattivala",
            expected="almeno un'azienda, oppure null per tutte",
        )
    unique = list(dict.fromkeys(legal_entities))
    active = set(
        session.scalars(
            select(LegalEntity.id).where(LegalEntity.id.in_(unique), LegalEntity.active.is_(True))
        ).all()
    )
    missing = [str(a) for a in unique if a not in active]
    if missing:
        raise ValidationFailed(
            entity,
            "legal_entities",
            f"azienda non attiva o inesistente: {', '.join(missing)}",
            expected="id di aziende attive dello spazio",
        )
    return unique


def apply_scope(session: Session, user: User, legal_entities: list[UUID] | None) -> None:
    """Write the scope on the row: the flag and the rows together, so a reader of either
    sees the same truth. `None` clears both; a list sets the flag and replaces the rows."""
    if legal_entities is None:
        user.limited_scope = False
        user.scopes = []
    else:
        user.limited_scope = True
        user.scopes = [
            UserLegalEntity(user_id=user.id, legal_entity_id=legal_entity)
            for legal_entity in legal_entities
        ]
    session.flush()


def active_only(session: Session, legal_entities: list[UUID]) -> list[UUID]:
    """The ids among `legal_entities` that still name an active azienda: what an invitation
    accepted after a deactivation keeps (spec §1.11), possibly nothing."""
    if not legal_entities:
        return []
    return list(
        session.scalars(
            select(LegalEntity.id).where(
                LegalEntity.id.in_(legal_entities), LegalEntity.active.is_(True)
            )
        ).all()
    )


__all__ = ["active_only", "actor_for", "apply_scope", "check_scope", "scope_of"]
