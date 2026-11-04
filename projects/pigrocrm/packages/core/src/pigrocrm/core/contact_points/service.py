"""Contact points: the rules a list obeys and the one way it is written.

A person and a customer own a list of points (`ContactPoint`). A write sends the whole list
(`write_points`), which is validated as a whole (`prepare`) and replaces the stored rows, as
a deal's value lines do. What a lookup compares is `normalized`, never `value`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, delete, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from pigrocrm.core.contact_points.linkedin import normalise_linkedin
from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.contact_points.schemas import MAX_CONTACT_POINTS, ContactPointIn
from pigrocrm.core.db import escape_like
from pigrocrm.core.errors import ValidationFailed

if TYPE_CHECKING:
    # Not imported at run time: the two models import this package's own, and their packages'
    # `__init__` import their services, which import this module.
    from pigrocrm.core.customers.models import Customer
    from pigrocrm.core.people.models import Person

FIELD = "contact_points"
# `.fullmatch()`, not `.match()`: a `$`-anchored pattern accepts a trailing newline.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_MIN_DIGITS = 3
PHONE_MAX_DIGITS = 20


@dataclass(frozen=True)
class PreparedPoint:
    kind: str
    value: str
    normalized: str
    label: str
    note: str | None
    is_primary: bool


def phone_key(value: str) -> str:
    """The digits of a phone, with a leading `+` kept: what two numbers are compared by."""
    digits = re.sub(r"\D", "", value)
    return f"+{digits}" if value.strip().startswith("+") else digits


def email_key(value: str) -> str:
    return value.strip().lower()


def _normalise(entity: str, index: int, point: ContactPointIn) -> tuple[str, str]:
    """The stored value and the comparison key of one point, or a refusal naming it."""
    where = f"{FIELD}[{index}]"
    value = point.value.strip()
    if not value:
        raise ValidationFailed(entity, where, "manca il valore")
    if point.kind == "email":
        if not EMAIL_RE.fullmatch(value):
            raise ValidationFailed(
                entity, where, "indirizzo non valido", expected="nome@dominio.it"
            )
        return value, email_key(value)
    if point.kind == "phone":
        digits = len(re.sub(r"\D", "", value))
        if not PHONE_MIN_DIGITS <= digits <= PHONE_MAX_DIGITS:
            raise ValidationFailed(
                entity, where, "numero di telefono non valido", expected="+39 02 1234567"
            )
        return value, phone_key(value)
    try:
        canonical = normalise_linkedin(value)
    except ValueError as error:
        raise ValidationFailed(
            entity, where, str(error), expected="https://www.linkedin.com/in/nome"
        ) from error
    return canonical, canonical.lower()


def prepare(entity: str, points: list[ContactPointIn]) -> list[PreparedPoint]:
    """Validates a whole list and answers it as it will be stored, in order.

    Refused: a value that is not what its kind says, the same value twice (same kind and
    same normalised value), two primaries of one kind. A kind that has points and no
    primary gets its first point as the primary.
    """
    if len(points) > MAX_CONTACT_POINTS:
        raise ValidationFailed(
            entity,
            FIELD,
            f"al massimo {MAX_CONTACT_POINTS} recapiti",
            expected="una lista più corta",
        )
    normalised = [_normalise(entity, index, point) for index, point in enumerate(points)]
    seen: set[tuple[str, str]] = set()
    primaries: dict[str, int] = {}
    for index, (point, (_, key)) in enumerate(zip(points, normalised, strict=True)):
        if (point.kind, key) in seen:
            raise ValidationFailed(entity, f"{FIELD}[{index}]", "questo recapito è già nell'elenco")
        seen.add((point.kind, key))
        if point.is_primary:
            if point.kind in primaries:
                raise ValidationFailed(
                    entity,
                    f"{FIELD}[{index}]",
                    "c'è già un recapito principale di questo tipo",
                    expected="un solo principale per tipo",
                )
            primaries[point.kind] = index
    first_of_kind: dict[str, int] = {}
    for index, point in enumerate(points):
        first_of_kind.setdefault(point.kind, index)
    for kind, index in first_of_kind.items():
        primaries.setdefault(kind, index)
    flagged = set(primaries.values())
    return [
        PreparedPoint(
            kind=point.kind,
            value=value,
            normalized=key,
            label=point.label,
            note=(point.note or "").strip() or None,
            is_primary=index in flagged,
        )
        for index, (point, (value, key)) in enumerate(zip(points, normalised, strict=True))
    ]


def write_points(session: Session, owner: Person | Customer, points: list[ContactPointIn]) -> bool:
    """Replaces the owner's whole list with these points, in the caller's transaction, and
    answers whether the list changed: a list sent back as it was is written nowhere.

    The old rows are deleted and flushed before the new ones go in, so the unique indexes
    (one primary per kind, one point per value) never see both lists at once. The parent row
    is locked first: two writers replacing one owner's list take turns instead of colliding.
    Expires the owner's loaded list so the derived `email`, `phone` and `linkedin` read the
    new one.
    """
    is_person = owner.__tablename__ == "people"
    entity = "person" if is_person else "customer"
    prepared = prepare(entity, points)
    stored = [
        (row.kind, row.value, row.label, row.note, row.is_primary) for row in owner.contact_points
    ]
    if stored == [(p.kind, p.value, p.label, p.note, p.is_primary) for p in prepared]:
        return False
    kind: Literal["person_id", "customer_id"] = "person_id" if is_person else "customer_id"
    parent = type(owner)
    session.execute(select(parent.id).where(parent.id == owner.id).with_for_update())
    column = ContactPoint.person_id if kind == "person_id" else ContactPoint.customer_id
    session.execute(delete(ContactPoint).where(column == owner.id))
    session.flush()
    session.add_all(
        ContactPoint(
            **{kind: owner.id},
            kind=point.kind,
            value=point.value,
            normalized=point.normalized,
            label=point.label,
            note=point.note,
            is_primary=point.is_primary,
            position=position,
        )
        for position, point in enumerate(prepared)
    )
    # The owner's own row did not change, but the owner did: the lists that sort by what was
    # touched last must see it.
    owner.updated_at = datetime.now(UTC)
    session.flush()
    session.expire(owner, ["contact_points"])
    return True


def email_addresses(owner: Person | Customer) -> list[str]:
    """Every email of an owner, lowercased, the primary first: what a lookup that reads mail
    (a backfill, the search for a reply) asks, where mail sent goes to the primary alone."""
    points = sorted(
        (point for point in owner.contact_points if point.kind == "email"),
        key=lambda point: (not point.is_primary, point.position),
    )
    return [point.normalized for point in points]


# A search term that reads as a phone number: digits with the separators people type.
_PHONE_TERM = re.compile(r"[\d\s+().\-/]+")
PHONE_SEARCH_MIN_DIGITS = 3


def owners_matching(
    owner_column: InstrumentedAttribute[UUID | None], term: str
) -> Select[UUID | None]:
    """The ids of the owners (`ContactPoint.person_id` or `.customer_id`) that have a point
    matching a search term: what was typed in any point, case-insensitively, or, when the
    term reads as a phone number, a phone whose digits contain the term's digits (so
    `02 1234` finds `+39 02-1234567`). `term` is the caller's, already lowercased or not."""
    like = f"%{escape_like(term.lower())}%"
    conditions: list[ColumnElement[bool]] = [
        ContactPoint.value.ilike(like, escape="\\"),
        ContactPoint.normalized.ilike(like, escape="\\"),
    ]
    digits = re.sub(r"\D", "", term)
    if _PHONE_TERM.fullmatch(term.strip()) and len(digits) >= PHONE_SEARCH_MIN_DIGITS:
        conditions.append(
            and_(ContactPoint.kind == "phone", ContactPoint.normalized.like(f"%{digits}%"))
        )
    return select(owner_column).where(or_(*conditions))
