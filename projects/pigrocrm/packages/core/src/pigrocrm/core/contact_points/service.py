"""Contact points: the rules a list obeys and the one way it is written.

A person and a customer own a list of points (`ContactPoint`). A write sends the whole list
(`write_points`), which is validated as a whole (`prepare`) and replaces the stored rows, as
a deal's value lines do. What a lookup compares is `normalized`, never `value`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, delete, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from pigrocrm.core.contact_points.linkedin import LINKEDIN_PROFILE, normalise_linkedin
from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.contact_points.schemas import MAX_CONTACT_POINTS, ContactPointIn
from pigrocrm.core.db import escape_like
from pigrocrm.core.errors import ValidationFailed

if TYPE_CHECKING:
    # Not imported at run time: the two models import this package's own, and their packages'
    # `__init__` import their services, which import this module.
    from pigrocrm.core.organizations.models import Organization
    from pigrocrm.core.people.models import Person

FIELD = "contact_points"
# `.fullmatch()`, not `.match()`: a `$`-anchored pattern accepts a trailing newline.
# A bare address: no whitespace and none of `<>,;"()`, so `Name <a@b.it>` and `a@b.it, c@d.it`
# are not one.
EMAIL_RE = re.compile(r'^[^@\s<>,;"()]+@[^@\s<>,;"()]+\.[^@\s<>,;"()]+$')
# A phone is whatever a person writes (`+39 345 1234567 (ufficio)`, `345-1234567 int. 2`): what
# it must hold is enough digits to be a number, and fit its column.
PHONE_MIN_DIGITS = 6
PHONE_MAX_LENGTH = 40
# A LinkedIn value that is only a handle, which means `linkedin.com/in/<handle>`.
LINKEDIN_HANDLE_RE = re.compile(r"[\w-]+")


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
        if digits < PHONE_MIN_DIGITS or len(value) > PHONE_MAX_LENGTH:
            raise ValidationFailed(
                entity,
                where,
                "numero di telefono non valido",
                expected=f"da {PHONE_MIN_DIGITS} cifre, {PHONE_MAX_LENGTH} caratteri al massimo",
            )
        return value, phone_key(value)
    try:
        canonical = normalise_linkedin(
            LINKEDIN_PROFILE + value.lower() if LINKEDIN_HANDLE_RE.fullmatch(value) else value
        )
    except ValueError as error:
        raise ValidationFailed(
            entity, where, str(error), expected="https://www.linkedin.com/in/nome"
        ) from error
    return canonical, canonical.lower()


def _loose_key(point: ContactPointIn) -> tuple[str, str]:
    """What a value is compared by with no rule applied: the key a stored point of this kind
    has, however its value was written before the rules existed."""
    value = point.value.strip()
    if point.kind == "phone":
        return point.kind, phone_key(value)
    return point.kind, value.lower()


def prepare(
    entity: str, points: list[ContactPointIn], stored: Sequence[ContactPoint] = ()
) -> list[PreparedPoint]:
    """Validates a whole list and answers it as it will be stored, in order.

    Refused: a value that is not what its kind says, the same value twice (same kind and
    same normalised value), two primaries of one kind. A kind that has points and no
    primary gets its first point as the primary.

    `stored` is the owner's list as it is now. A point that is the same value as one of those
    (same kind, same compared key) is accepted as it is even where today's rules would refuse
    it, so that a list read back and sent unchanged always saves: what was stored before the
    rules (or by a looser version of them) is not the caller's to fix to save something else.
    """
    if len(points) > MAX_CONTACT_POINTS:
        raise ValidationFailed(
            entity,
            FIELD,
            f"al massimo {MAX_CONTACT_POINTS} recapiti",
            expected="una lista più corta",
        )
    already = {(row.kind, row.normalized) for row in stored}
    normalised: list[tuple[str, str]] = []
    for index, point in enumerate(points):
        try:
            normalised.append(_normalise(entity, index, point))
        except ValidationFailed:
            if _loose_key(point) not in already:
                raise
            normalised.append((point.value.strip(), _loose_key(point)[1]))
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


def write_points(
    session: Session, owner: Person | Organization, points: list[ContactPointIn]
) -> bool:
    """Replaces the owner's whole list with these points, in the caller's transaction, and
    answers whether the list changed: a list sent back as it was is written nowhere.

    The old rows are deleted and flushed before the new ones go in, so the unique indexes
    (one primary per kind, one point per value) never see both lists at once. The parent row
    is locked first: two writers replacing one owner's list take turns instead of colliding.
    Expires the owner's loaded list so the derived `email`, `phone` and `linkedin` read the
    new one.
    """
    is_person = owner.__tablename__ == "people"
    entity = "person" if is_person else "organization"
    prepared = prepare(entity, points, owner.contact_points)
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


def lock_owner(session: Session, owner: Person | Organization) -> None:
    """Locks the owner's row and reloads its list, so what the caller reads next is what
    another writer left and no later writer can change it before this transaction ends."""
    parent = type(owner)
    # Read to the end: the rows (and their lists) are only refreshed as the result is consumed.
    session.scalars(
        select(parent)
        .where(parent.id == owner.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def add_points(
    session: Session, owner: Person | Organization, points: list[ContactPointIn]
) -> list[ContactPoint]:
    """Appends points to the owner's list, in the caller's transaction, and answers the rows
    added. Unlike `write_points` it touches nothing the owner has: their rows keep their ids,
    values (a legacy one included), labels, notes and primaries, and the new points go after
    them in the order given.

    The row is locked and the list reloaded first, so two callers adding to one owner take
    turns. The new points are validated as a list of their own, and refused as `prepare`
    refuses them, when one repeats a value the owner already has (same kind, same compared
    key) or the list would pass `MAX_CONTACT_POINTS`. A point is primary only when it is the
    first of its kind and the owner has none of that kind: `is_primary` on a point is ignored.
    """
    lock_owner(session, owner)
    entity = "person" if owner.__tablename__ == "people" else "organization"
    stored = owner.contact_points
    if len(stored) + len(points) > MAX_CONTACT_POINTS:
        raise ValidationFailed(
            entity,
            FIELD,
            f"al massimo {MAX_CONTACT_POINTS} recapiti",
            expected="una lista più corta",
        )
    prepared = prepare(entity, [point.model_copy(update={"is_primary": False}) for point in points])
    have = {(row.kind, row.normalized) for row in stored}
    for index, point in enumerate(prepared):
        if (point.kind, point.normalized) in have:
            raise ValidationFailed(entity, f"{FIELD}[{index}]", "questo recapito è già nell'elenco")
    has_primary = {row.kind for row in stored if row.is_primary}
    column = "person_id" if entity == "person" else "customer_id"
    position = max((row.position for row in stored), default=-1) + 1
    rows: list[ContactPoint] = []
    for point in prepared:
        primary = point.kind not in has_primary
        has_primary.add(point.kind)
        rows.append(
            ContactPoint(
                **{column: owner.id},
                kind=point.kind,
                value=point.value,
                normalized=point.normalized,
                label=point.label,
                note=point.note,
                is_primary=primary,
                position=position,
            )
        )
        position += 1
    session.add_all(rows)
    if rows:
        owner.updated_at = datetime.now(UTC)
        session.flush()
        session.expire(owner, ["contact_points"])
    return rows


def email_addresses(owner: Person | Organization) -> list[str]:
    """Every email of an owner, lowercased, the primary first: what a lookup that reads mail
    (a backfill, the search for a reply) asks, where mail sent goes to the primary alone."""
    points = sorted(
        (point for point in owner.contact_points if point.kind == "email"),
        key=lambda point: (not point.is_primary, point.position),
    )
    return [point.normalized for point in points]


# The labels whose emails say where a company is. A billing address is often an accountant's
# or a shared invoicing mailbox, a personal one is somebody's own: neither names the company.
COMPANY_EMAIL_LABELS = ("work", "other")


def company_email_addresses(owner: Person | Organization) -> list[str]:
    """The emails of an owner that may name its company's domain, lowercased: the primary
    first (whatever its label), then the others labelled `work` or `other`, in position order.
    What reads a domain off a record (`customer_domain`) asks this, not `email_addresses`."""
    points = sorted(
        (
            point
            for point in owner.contact_points
            if point.kind == "email" and (point.is_primary or point.label in COMPANY_EMAIL_LABELS)
        ),
        key=lambda point: (not point.is_primary, point.position),
    )
    return [point.normalized for point in points]


# A search term that reads as a phone number: digits with the separators people type.
_PHONE_TERM = re.compile(r"[\d\s+().\-/]+")
PHONE_SEARCH_MIN_DIGITS = 3


def owners_matching(
    owner_column: InstrumentedAttribute[UUID | None], term: str, kind: str | None = None
) -> Select[UUID | None]:
    """The ids of the owners (`ContactPoint.person_id` or `.customer_id`) that have a point
    matching a search term: what was typed in any point, case-insensitively, or, when the
    term reads as a phone number, a phone whose digits contain the term's digits (so
    `02 1234` finds `+39 02-1234567`). `term` is the caller's, already lowercased or not.
    `kind` narrows it to the points of one kind (`phone`, say) when the caller needs to say
    which kind answered."""
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
    found = or_(*conditions)
    if kind is not None:
        found = and_(ContactPoint.kind == kind, found)
    return select(owner_column).where(owner_column.is_not(None), found)
