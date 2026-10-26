"""Who a reminder may go to (spec 2026-10-26 §4).

A reminder is raised for a **set of users**, computed by its detector from the subject:
the subject's assignee, the owner of the customer or deal it hangs on, and for the
space-wide types every admin. Three rules then apply to every set, in this one place, so a
detector only names who it would like to tell:

- **A recipient is shown only what they may read.** A user is dropped from a subject's
  set, the owner fallback included, when their azienda scope does not cover the subject's
  azienda. A subject with no azienda at all (an attività linked to nothing) is visible
  only to an unscoped member, as the row-level policy has it (`0048`). Assigning a deal
  never widens who can read its customer's invoices.
- **A user who cannot be told is dropped.** An archived user, or one with no address,
  stays out of the set, and the reminder goes on to the others.
- **A set that ends up empty goes to the space's owner**, so a type never fires into a
  void. The owner is the registry's `owner_email` for a space and the first active admin
  for the root installation (the digest's titolare, `digest/run.py`).

The set is deduplicated: a person who is both assignee and owner gets one reminder.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.scope import scope_of


@dataclass(frozen=True)
class Person:
    """What the pass needs of a user, taken as plain values so the rows going stale
    across a commit costs nothing."""

    id: UUID
    email: str
    nome: str
    admin: bool
    aziende: tuple[UUID, ...] | None

    def sees(self, azienda_id: UUID | None) -> bool:
        """Whether the user's scope covers a subject of this azienda. `None` is a subject
        no azienda owns, which only an unscoped member may read."""
        if self.aziende is None:
            return True
        return azienda_id is not None and azienda_id in self.aziende


class Directory:
    def __init__(self, people: Iterable[Person], owner_id: UUID | None) -> None:
        self.people = {person.id: person for person in people}
        self.owner_id = owner_id

    @classmethod
    def load(cls, session: Session, owner_email: str | None = None) -> Directory:
        """Every active user with an address, and the space's owner. `owner_email` is the
        registry's for a space; `None` asks for the root installation's, the first active
        admin."""
        users = UserRepository(session)
        people = [
            _person(user)
            for user in session.scalars(select(User).where(User.attivo.is_(True)).order_by(User.id))
            if user.email
        ]
        owner = users.get_by_email(owner_email) if owner_email is not None else None
        if owner is None and owner_email is None:
            owner = users.first_active_admin()
        return cls(people, owner.id if owner is not None and owner.attivo else None)

    def recipients(
        self, candidates: Iterable[UUID | None], azienda_id: UUID | None
    ) -> frozenset[UUID]:
        """The users among `candidates` who can be told and may read the subject, or the
        space's owner when none of them can."""
        chosen = frozenset(
            candidate
            for candidate in candidates
            if candidate is not None
            and (person := self.people.get(candidate)) is not None
            and person.sees(azienda_id)
        )
        if chosen:
            return chosen
        owner = self.people.get(self.owner_id) if self.owner_id is not None else None
        if owner is not None and owner.sees(azienda_id):
            return frozenset({owner.id})
        return frozenset()

    def admins(self) -> frozenset[UUID]:
        """Every admin, for the space-wide types (spec §3.5)."""
        return frozenset(person.id for person in self.people.values() if person.admin)


def _person(user: User) -> Person:
    return Person(
        id=user.id,
        email=user.email,
        nome=user.nome,
        admin=user.ruolo == "admin",
        aziende=scope_of(user),
    )
