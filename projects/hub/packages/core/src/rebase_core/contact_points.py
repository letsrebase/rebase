"""Several ways to reach a person (REB-943, design 2026-11-04).

A contact point is a `kind` (`email`, `phone`, `linkedin`), a `value`, a `label` from a fixed
list and an optional free note, owned by a `users` row (`UserContactPoint`). One list is
written whole: `ContactPointService.replace` takes the person's entire list and replaces it,
the way a deal's value lines are in the CRM.

**The login address is not in the list.** `users.email` stays the identity: it receives the
magic link, it is what campaigns write to, and it is unique. The API shows it as the primary
email, read-only (`ContactPointService.present`: the first entry, without an `id`), and a
stored `email` point is always an extra address: never primary, never a login, never a
campaign recipient. An extra address that is somebody else's login address, or somebody
else's extra address, is refused with a sentence that names it; `UserService.find_person`
reads the extra addresses too, so a sign-up with one is recognised as the person who holds it.

**Compared, not stored, in one shape.** The value is kept as it was typed, trimmed (a
LinkedIn profile in the stored form `rebase_core.linkedin` gives it); two values of one kind
on one person are the same when `point_key` says so: an address by its lower case, a number
by its digits, a profile by its stored form. The unique indexes of the table state the same
rules in SQL.
"""

import re
from collections.abc import Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.errors import ValidationFailed
from rebase_core.linkedin import normalise_linkedin
from rebase_core.models import (
    CONTACT_NOTE_MAX_LENGTH,
    CONTACT_VALUE_MAX_LENGTH,
    PHONE_MAX_LENGTH,
    User,
    UserContactPoint,
)
from rebase_core.schema_common import ResponseModel, clean_text
from rebase_core.validation import SafeStr
from rebase_core.vocabulary import ContactKind, ContactLabel

ENTITY = "contact_points"
# More than any person has; a list that long is a paste gone wrong.
CONTACT_POINTS_MAX = 20
PHONE_MIN_DIGITS = 6
_EMAIL: TypeAdapter[str] = TypeAdapter(EmailStr)
# A phone as it is written: an optional plus, digits and the separators.
_PHONE = re.compile(r"\+?[\d\s().\-/]+")

LOGIN_IS_NOT_A_POINT = (
    "l'indirizzo con cui la persona accede è già mostrato come principale: non va nell'elenco"
)
EMAIL_NEVER_PRIMARY = (
    "l'indirizzo principale è quello con cui la persona accede: gli altri sono recapiti in più"
)


def email_key(value: str) -> str:
    """What two addresses are compared by: trimmed and lower case."""
    return value.strip().lower()


def phone_key(value: str) -> str:
    """What two numbers are compared by: their ASCII digits and nothing else."""
    return re.sub(r"[^0-9]", "", value)


def point_key(kind: str, value: str) -> str:
    """The value of a point as `uq_user_contact_points_value` compares it."""
    return phone_key(value) if kind == "phone" else value.strip().lower()


class ContactPointInput(BaseModel):
    """One point of the list a write sends. `id` and `position` are what a read answers and
    are accepted only so a list read can be sent back as it is: the id is not used (the
    list is replaced) and the position is the order of the list."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = Field(default=None, description="Read only: ignored on write.")
    kind: Literal["email", "phone", "linkedin"]
    value: SafeStr = Field(min_length=1, max_length=CONTACT_VALUE_MAX_LENGTH)
    label: Literal["work", "personal", "billing", "other"]
    note: SafeStr | None = Field(default=None, max_length=CONTACT_NOTE_MAX_LENGTH)
    is_primary: bool = False
    position: int | None = Field(default=None, description="Read only: the list's own order.")

    @model_validator(mode="after")
    def _shaped_for_its_kind(self) -> "ContactPointInput":
        self.value = _clean_value(self.kind, self.value)
        if self.note is not None:
            trimmed = self.note.strip()
            self.note = clean_text(trimmed, what="una nota") if trimmed else None
        return self


def _clean_value(kind: str, value: str) -> str:
    """The value as it is stored, or the `ValueError` a form shows."""
    if kind == "email":
        trimmed = clean_text(value, what="un indirizzo")
        try:
            _EMAIL.validate_python(trimmed)
        except ValueError as exc:
            raise ValueError("serve un indirizzo email valido") from exc
        return trimmed
    if kind == "phone":
        trimmed = clean_text(value, what="un numero")
        if (
            len(trimmed) > PHONE_MAX_LENGTH
            or not _PHONE.fullmatch(trimmed)
            or len(phone_key(trimmed)) < PHONE_MIN_DIGITS
        ):
            raise ValueError(
                f"serve un numero di telefono, di almeno {PHONE_MIN_DIGITS} cifre "
                f"e al massimo {PHONE_MAX_LENGTH} caratteri"
            )
        return trimmed
    stored = normalise_linkedin(value)
    if stored is None:
        raise ValueError("serve l'indirizzo di un profilo LinkedIn")
    return stored


class ContactPointRead(ResponseModel):
    """One point as a page reads it. The first entry of a person's list is the login
    address, without an `id`: it is the primary email and cannot be edited from the list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None
    kind: ContactKind
    value: str
    label: ContactLabel
    note: str | None
    is_primary: bool
    position: int


def snapshot(points: Sequence[UserContactPoint]) -> list[dict[str, Any]]:
    """The stored points as an audit entry keeps them: what a write could send back."""
    return [
        {
            "kind": point.kind,
            "value": point.value,
            "label": point.label,
            "note": point.note,
            "is_primary": point.is_primary,
        }
        for point in points
    ]


class ContactPointService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- reading ----------------------------------------------------------------------

    @staticmethod
    def present(user: User) -> list[ContactPointRead]:
        """The person's list as a page shows it: the login address first, as the primary
        email and without an `id`, then the stored points in their order."""
        login = ContactPointRead(
            id=None,
            kind="email",
            value=user.email,
            label="work",
            note=None,
            is_primary=True,
            position=0,
        )
        stored = [
            ContactPointRead(
                id=point.id,
                kind=point.kind,
                value=point.value,
                label=point.label,
                note=point.note,
                is_primary=point.is_primary,
                position=index,
            )
            for index, point in enumerate(user.contact_points, start=1)
        ]
        return [login, *stored]

    def owner_of_email(self, email: str) -> User | None:
        """The person who holds this address as an extra one, `None` for nobody. The row
        itself, merged or not: `UserService` follows a merge."""
        return self.session.scalar(
            select(User)
            .join(UserContactPoint, UserContactPoint.user_id == User.id)
            .where(
                UserContactPoint.kind == "email",
                func.lower(UserContactPoint.value) == email_key(email),
            )
        )

    # ---- writing ----------------------------------------------------------------------

    def replace(self, user: User, points: Sequence[ContactPointInput]) -> bool:
        """Makes `points` the whole list of `user`, flushed and not committed (the caller
        commits it with whatever else it writes), and says whether the stored list changed.

        A point that is the login address with `is_primary` set is the list's own first
        entry sent back, and is left out; any other point equal to the login address is
        refused. Of a kind with points and none primary, the first becomes primary; an
        address is never primary (the login is). Refused with a sentence that names the
        value: two primaries of one kind, the same value twice, an address that is another
        person's login or extra address."""
        wanted = self._checked(user, points)
        stored = list(user.contact_points)
        if wanted == snapshot(stored):
            return False
        reuse = {(point.kind, point_key(point.kind, point.value)): point.id for point in stored}
        for point in stored:
            self.session.delete(point)
        self.session.flush()
        for position, entry in enumerate(wanted):
            kept_id = reuse.get((entry["kind"], point_key(entry["kind"], entry["value"])))
            self.session.add(
                UserContactPoint(
                    **({"id": kept_id} if kept_id is not None else {}),
                    user_id=user.id,
                    position=position,
                    **entry,
                )
            )
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise ValidationFailed(
                ENTITY,
                "contact_points",
                "un indirizzo è stato appena preso da un'altra persona: riprova",
            ) from exc
        self.session.expire(user, ["contact_points"])
        return True

    def write_override(
        self, user: User, points: Sequence[ContactPointInput] | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        """An admin's override of the whole list: `None` when the stored list did not change,
        else the list before and after, as the trail keeps them (and a revert sends them
        back). `null` is refused: an empty list takes every point off."""
        if points is None:
            raise ValidationFailed(
                ENTITY, "contact_points", "il campo non può essere svuotato: manda un elenco vuoto"
            )
        before = snapshot(user.contact_points)
        if not self.replace(user, points):
            return None
        return before, snapshot(user.contact_points)

    def add_primary_phone_if_missing(self, user: User, value: str) -> None:
        """A phone a request collected, kept as the person's primary one when they have
        none: what `UserService.get_or_create` always did with `users.phone`. The value was
        validated by the form that took it and is stored as it came."""
        if user.phone is not None:
            return
        self.session.add(
            UserContactPoint(
                user_id=user.id,
                kind="phone",
                value=value.strip(),
                label="work",
                is_primary=not any(
                    point.kind == "phone" and point.is_primary for point in user.contact_points
                ),
                position=len(user.contact_points),
            )
        )
        self.session.flush()
        self.session.expire(user, ["contact_points"])

    def absorb(self, survivor: User, absorbed: User) -> dict[str, Any]:
        """A merge's step (`merge_steps.merge_identity`): the absorbed person's points move
        to the survivor, and so does their login address, as an extra one. A value the
        survivor already has stays theirs and the absorbed copy is dropped; a primary the
        survivor already has is kept, so the absorbed phone arrives as a secondary one.
        Answers what the undo needs: `moved` (each point's id, whether it was primary and its
        position), `login` (the id of the extra address made from the login, or `None` when
        the survivor had it) and `dropped` (the copies not carried over)."""
        have = {(p.kind, point_key(p.kind, p.value)) for p in survivor.contact_points}
        moved: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        position = len(survivor.contact_points)
        primary_kinds = {p.kind for p in survivor.contact_points if p.is_primary}
        for point in list(absorbed.contact_points):
            key = (point.kind, point_key(point.kind, point.value))
            if key in have:
                dropped.append(snapshot([point])[0])
                self.session.delete(point)
                continue
            have.add(key)
            moved.append(
                {"id": str(point.id), "is_primary": point.is_primary, "position": point.position}
            )
            point.user_id = survivor.id
            point.position = position
            position += 1
            if point.is_primary and point.kind in primary_kinds:
                point.is_primary = False
            elif point.is_primary:
                primary_kinds.add(point.kind)
        self.session.flush()
        login_id: str | None = None
        if ("email", email_key(absorbed.email)) not in have:
            login = UserContactPoint(
                user_id=survivor.id,
                kind="email",
                value=absorbed.email,
                label="other",
                is_primary=False,
                position=position,
            )
            self.session.add(login)
            self.session.flush()
            login_id = str(login.id)
        self.session.expire(survivor, ["contact_points"])
        self.session.expire(absorbed, ["contact_points"])
        return {"moved": moved, "login": login_id, "dropped": dropped}

    def put_back(self, survivor: User, absorbed: User, undo: dict[str, Any]) -> int:
        """The undo of `absorb`: each moved point that still sits with the survivor goes
        back to the absorbed person as it was, the address made from their login is taken
        away, and the copies that were dropped are written again. Answers how many points
        the survivor has changed or removed since (they stay as they are)."""
        left = 0
        for entry in undo["moved"]:
            point = self.session.get(UserContactPoint, UUID(entry["id"]))
            if point is None or point.user_id != survivor.id:
                left += 1
                continue
            point.user_id = absorbed.id
            point.is_primary = entry["is_primary"]
            point.position = entry["position"]
        if undo["login"] is not None:
            login = self.session.get(UserContactPoint, UUID(undo["login"]))
            if login is not None and login.user_id == survivor.id:
                self.session.delete(login)
        self.session.flush()
        self.session.expire(absorbed, ["contact_points"])
        position = len(absorbed.contact_points)
        for entry in undo["dropped"]:
            self.session.add(UserContactPoint(user_id=absorbed.id, position=position, **entry))
            position += 1
        self.session.flush()
        self.session.expire(survivor, ["contact_points"])
        self.session.expire(absorbed, ["contact_points"])
        return left

    # ---- checks -----------------------------------------------------------------------

    def _checked(self, user: User, points: Sequence[ContactPointInput]) -> list[dict[str, Any]]:
        if len(points) > CONTACT_POINTS_MAX:
            raise ValidationFailed(
                ENTITY, "contact_points", f"al massimo {CONTACT_POINTS_MAX} recapiti"
            )
        login = email_key(user.email)
        kept: list[ContactPointInput] = []
        for point in points:
            if point.kind == "email" and email_key(point.value) == login:
                if point.is_primary:
                    continue  # the list's own first entry, sent back
                raise ValidationFailed(ENTITY, "contact_points", LOGIN_IS_NOT_A_POINT)
            kept.append(point)
        seen: set[tuple[str, str]] = set()
        primaries: dict[str, int] = {}
        for point in kept:
            if point.kind == "email" and point.is_primary:
                raise ValidationFailed(ENTITY, "contact_points", EMAIL_NEVER_PRIMARY)
            key = (point.kind, point_key(point.kind, point.value))
            if key in seen:
                raise ValidationFailed(
                    ENTITY,
                    "contact_points",
                    f"«{point.value}» è scritto due volte: tienine uno",
                )
            seen.add(key)
            if point.is_primary:
                primaries[point.kind] = primaries.get(point.kind, 0) + 1
        for kind, count in primaries.items():
            if count > 1:
                raise ValidationFailed(
                    ENTITY, "contact_points", f"più di un recapito principale di tipo {kind}"
                )
        self._refuse_taken_addresses(user, [p.value for p in kept if p.kind == "email"])
        wanted: list[dict[str, Any]] = []
        firsts: set[str] = set()
        for point in kept:
            primary = point.is_primary
            if point.kind != "email" and point.kind not in primaries and point.kind not in firsts:
                primary = True
            firsts.add(point.kind)
            wanted.append(
                {
                    "kind": point.kind,
                    "value": point.value,
                    "label": point.label,
                    "note": point.note,
                    "is_primary": primary,
                }
            )
        return wanted

    def _refuse_taken_addresses(self, user: User, addresses: Sequence[str]) -> None:
        """An extra address is never another person's login address or extra address.
        Whoever was merged into this person (their login addresses still answer for them)
        is this person for the rule, so a merge can keep the addresses it absorbed."""
        keys = [email_key(address) for address in addresses]
        if not keys:
            return
        login_holder = self.session.scalar(
            select(func.lower(User.email)).where(
                func.lower(User.email).in_(keys),
                User.id != user.id,
                or_(User.merged_into_id.is_(None), User.merged_into_id != user.id),
            )
        )
        if login_holder is not None:
            raise ValidationFailed(
                ENTITY,
                "contact_points",
                f"«{login_holder}» è già l'indirizzo con cui un'altra persona accede",
            )
        extra_holder = self.session.scalar(
            select(func.lower(UserContactPoint.value)).where(
                UserContactPoint.kind == "email",
                func.lower(UserContactPoint.value).in_(keys),
                UserContactPoint.user_id != user.id,
            )
        )
        if extra_holder is not None:
            raise ValidationFailed(
                ENTITY,
                "contact_points",
                f"«{extra_holder}» è già tra i recapiti di un'altra persona",
            )
