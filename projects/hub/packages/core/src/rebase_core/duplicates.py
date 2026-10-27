"""Finding the same person twice (REB-800).

**What identifies a person.** Two things, either one: the address (lower case, already a
unique index) and the LinkedIn profile (`linkedin_key`, since REB-800 a unique index
too). Two live cards whose people share a profile are the same person, and an admin or
`rebase talent-duplicates --apply` may merge them (`rebase_core.merge`).

**What only flags.** The same name, or the same telephone number, is a reason to look,
never to merge: two people are called «Marco Rossi», a number is shared by a studio, and
a merge moves contracts. Those pairs are listed for review (`da_rivedere`), carry
`possibile_duplicato_di` on a Talenti row, and are merged only by an admin who has read
both cards, through the same merge.

**Sign-ups.** A sign-up has no card, so there is nothing to merge: one whose profile is
on a card is hidden from the lead lists (`rebase_core.leads`) and only reported here;
two sign-ups with one profile are reported too, and the form no longer makes a second.
"""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.linkedin import PROFILE_SQL_PREDICATE, linkedin_key
from rebase_core.models import Freelancer, Signup, User

LINKEDIN = "linkedin"
NOME = "nome"
TELEFONO = "telefono"
# Cards a Talenti row names as «may be this person»: a common name is shared by many, and a
# row is read by an agent that pays for every id.
FLAGS_PER_ROW = 5
# A number shorter than this is not one a second person could not have typed.
_PHONE_MIN_DIGITS = 6
_MIN_DATE = datetime.min

# The two indexes migration 0035 creates only when nothing shares a profile yet, as the
# same statements `rebase talent-duplicates --apply` runs once the merge has made it true.
UNIQUE_INDEXES = (
    ("uq_users_linkedin_lower", "users"),
    ("uq_signups_linkedin_lower", "signups"),
)


@dataclass(frozen=True)
class Person:
    """One live card with the few columns that decide who survives a merge. Never the
    CV's bytes: `cv_uploaded_at` and `ha_cv` say what there is to know."""

    user_id: UUID
    freelancer_id: UUID
    email: str
    nome: str
    cognome: str
    admin: bool
    created_at: datetime
    cv_uploaded_at: datetime | None
    ha_cv: bool

    @property
    def rank(self) -> tuple[bool, datetime, datetime]:
        """What makes a card the survivor: an admin's own card first (an admin is never
        merged away), then the newest CV, then the newest card."""
        stamp = (
            self.cv_uploaded_at.replace(tzinfo=None)
            if self.ha_cv and self.cv_uploaded_at
            else _MIN_DATE
        )
        return (self.admin, stamp, self.created_at.replace(tzinfo=None))


@dataclass(frozen=True)
class DuplicatePair:
    """Two live cards that look like one person. `survivor` and `assorbito` are the
    default of the merge: the newest CV wins, unless an admin chooses otherwise by
    merging the other way round. `unibile` is true only when the LinkedIn profile is the
    same, and neither card is an admin's, since an admin is never the one merged away."""

    survivor: Person
    assorbito: Person
    segnali: tuple[str, ...]

    @property
    def unibile(self) -> bool:
        return LINKEDIN in self.segnali and not self.assorbito.admin


@dataclass(frozen=True)
class LeadDuplicate:
    """A sign-up that is a person already on the list under another address: `card_user_id`
    when the profile is on a card, `other_signup_id` when it is on another sign-up. No
    merge: a sign-up has no card to merge, and the lead lists already leave it out."""

    signup_id: UUID
    card_user_id: UUID | None = None
    other_signup_id: UUID | None = None


@dataclass(frozen=True)
class DuplicateReport:
    unibili: list[DuplicatePair] = field(default_factory=list)
    da_rivedere: list[DuplicatePair] = field(default_factory=list)
    iscrizioni: list[LeadDuplicate] = field(default_factory=list)


def _name_key(nome: str, cognome: str) -> str | None:
    """The name as a person would be recognised by it: accents, case, spaces and the
    order of the two words left out. `None` when either is empty."""
    parts = []
    for word in (nome, cognome):
        folded = unicodedata.normalize("NFKD", word)
        text_ = "".join(ch for ch in folded if not unicodedata.combining(ch))
        text_ = re.sub(r"[\W_]+", "", text_.lower())
        if not text_:
            return None
        parts.append(text_)
    return "|".join(sorted(parts))


def _phone_key(value: str | None) -> str | None:
    digits = re.sub(r"\D+", "", value or "")
    digits = digits.removeprefix("0039").removeprefix("39") if len(digits) > 10 else digits
    return digits if len(digits) >= _PHONE_MIN_DIGITS else None


def _pairs(
    groups: dict[str, list[Person]], signal: str, found: dict[frozenset[UUID], set[str]]
) -> None:
    for members in groups.values():
        for first, second in combinations(members, 2):
            found[frozenset((first.freelancer_id, second.freelancer_id))].add(signal)


def find_duplicates(session: Session) -> DuplicateReport:
    """Every pair of live cards that share a LinkedIn profile, a name or a telephone
    number, and every sign-up that repeats a profile. One read of the identity columns
    of the live cards, grouped in memory: a hundred and fifty rows, no per-pair query."""
    rows = session.execute(
        select(
            Freelancer.id,
            Freelancer.user_id,
            Freelancer.created_at,
            Freelancer.cv_uploaded_at,
            Freelancer.cv_size,
            User.email,
            User.nome,
            User.cognome,
            User.role,
            User.linkedin_url,
            User.telefono,
        )
        .join(User, User.id == Freelancer.user_id)
        .where(Freelancer.deleted_at.is_(None), User.merged_into_id.is_(None))
        .order_by(Freelancer.created_at, Freelancer.id)
    ).all()
    people: dict[UUID, Person] = {}
    by_linkedin: dict[str, list[Person]] = defaultdict(list)
    by_name: dict[str, list[Person]] = defaultdict(list)
    by_phone: dict[str, list[Person]] = defaultdict(list)
    for row in rows:
        person = Person(
            user_id=row.user_id,
            freelancer_id=row.id,
            email=row.email,
            nome=row.nome,
            cognome=row.cognome,
            admin=row.role == "admin",
            created_at=row.created_at,
            cv_uploaded_at=row.cv_uploaded_at,
            ha_cv=row.cv_size is not None,
        )
        people[row.id] = person
        if (key := linkedin_key(row.linkedin_url)) is not None:
            by_linkedin[key].append(person)
        if (name := _name_key(row.nome, row.cognome)) is not None:
            by_name[name].append(person)
        if (phone := _phone_key(row.telefono)) is not None:
            by_phone[phone].append(person)
    found: dict[frozenset[UUID], set[str]] = defaultdict(set)
    _pairs(by_linkedin, LINKEDIN, found)
    _pairs(by_name, NOME, found)
    _pairs(by_phone, TELEFONO, found)
    order = (LINKEDIN, NOME, TELEFONO)
    pairs: list[DuplicatePair] = []
    for ids, signals in found.items():
        first, second = (people[i] for i in ids)
        best, other = (first, second) if first.rank >= second.rank else (second, first)
        pairs.append(DuplicatePair(best, other, tuple(s for s in order if s in signals)))
    # Survivors first and oldest ids last, so a group of three is merged into its best
    # card by the pairs that name it, and the pair between the other two is already moot.
    pairs.sort(
        key=lambda p: (p.survivor.rank, p.survivor.freelancer_id, p.assorbito.rank), reverse=True
    )
    return DuplicateReport(
        unibili=[p for p in pairs if p.unibile],
        da_rivedere=[p for p in pairs if not p.unibile],
        iscrizioni=_lead_duplicates(session),
    )


def _lead_duplicates(session: Session) -> list[LeadDuplicate]:
    """Sign-ups that repeat a profile: on a card under another address (one card per
    profile, by the unique index or the finder above), or on an earlier sign-up."""
    signups = session.execute(
        select(Signup.id, Signup.email, Signup.linkedin_url, Signup.created_at)
        .where(Signup.linkedin_url.is_not(None))
        .order_by(Signup.created_at, Signup.id)
    ).all()
    cards = {
        linkedin_key(row.linkedin_url): (row.id, row.email.lower())
        for row in session.execute(
            select(User.id, User.email, User.linkedin_url)
            .join(Freelancer, Freelancer.user_id == User.id)
            .where(User.linkedin_url.is_not(None), User.merged_into_id.is_(None))
        ).all()
    }
    found: list[LeadDuplicate] = []
    first_by_key: dict[str, UUID] = {}
    for signup in signups:
        key = linkedin_key(signup.linkedin_url)
        if key is None:
            continue
        card = cards.get(key)
        if card is not None and card[1] != signup.email.lower():
            found.append(LeadDuplicate(signup.id, card_user_id=card[0]))
        elif key in first_by_key:
            found.append(LeadDuplicate(signup.id, other_signup_id=first_by_key[key]))
        first_by_key.setdefault(key, signup.id)
    return found


@dataclass(frozen=True)
class IndexesEnsured:
    """`created`: the indexes this call made. `blocked`: index name to how many profiles
    still have more than one row, for a table the merge does not settle (two sign-ups, or
    two users that are not cards) and an admin has to."""

    created: list[str] = field(default_factory=list)
    blocked: dict[str, int] = field(default_factory=dict)


def ensure_unique_indexes(session: Session) -> IndexesEnsured:
    """Creates the LinkedIn unique indexes migration 0035 left out because a pair still
    shared a profile. Each index on its own: a table where profiles are still shared (two
    sign-ups with one profile are never merged, and neither are two users that are not
    cards) is reported in `blocked` with how many, and its index is left for later, while
    the other is created. A no-op once they exist. The same condition as the migration's
    check and `linkedin_key`: a personal profile, in lower case."""
    done = IndexesEnsured()
    for name, table in UNIQUE_INDEXES:
        present = session.scalar(
            text("SELECT 1 FROM pg_indexes WHERE indexname = :name"), {"name": name}
        )
        if present is not None:
            continue
        shared = session.scalar(
            text(
                f"SELECT count(*) FROM (SELECT 1 FROM {table} WHERE {PROFILE_SQL_PREDICATE} "
                "GROUP BY lower(linkedin_url) HAVING count(*) > 1) AS shared"
            )
        )
        if shared:
            done.blocked[name] = int(shared)
            continue
        session.execute(
            text(
                f"CREATE UNIQUE INDEX {name} ON {table} (lower(linkedin_url)) "
                f"WHERE {PROFILE_SQL_PREDICATE}"
            )
        )
        done.created.append(name)
    session.commit()
    return done


def duplicates_of(session: Session, freelancer_ids: list[UUID]) -> dict[UUID, list[UUID]]:
    """For a page of Talenti rows: the other live cards each might be, by LinkedIn, name
    or telephone (`possibile_duplicato_di`). Reads the whole report once; the list is a
    page of a hundred, the table a few hundred rows, and a card with none has no key."""
    wanted = set(freelancer_ids)
    answer: dict[UUID, list[UUID]] = {}
    report = find_duplicates(session)
    for pair in (*report.unibili, *report.da_rivedere):
        for mine, other in (
            (pair.survivor, pair.assorbito),
            (pair.assorbito, pair.survivor),
        ):
            if mine.freelancer_id in wanted:
                flagged = answer.setdefault(mine.freelancer_id, [])
                if len(flagged) < FLAGS_PER_ROW:
                    flagged.append(other.freelancer_id)
    return answer


class PersonRead(BaseModel):
    """A card of a pair as the admin reads it: who, and what decides the survivor."""

    freelancer_id: UUID
    user_id: UUID
    nome: str
    cognome: str
    email: str
    created_at: datetime
    cv_uploaded_at: datetime | None
    ha_cv: bool


class DuplicatePairRead(BaseModel):
    """`survivor` is the default of the merge (the newest CV); `segnali` says why the two
    look alike (`linkedin`, `nome`, `telefono`), and `unibile` that the profile is the same,
    the only signal that is a reason to merge rather than to look."""

    survivor: PersonRead
    assorbito: PersonRead
    segnali: list[str]
    unibile: bool


class DuplicateListRead(BaseModel):
    unibili: list[DuplicatePairRead]
    da_rivedere: list[DuplicatePairRead]
    iscrizioni: int


def _person_read(person: Person) -> PersonRead:
    return PersonRead(
        freelancer_id=person.freelancer_id,
        user_id=person.user_id,
        nome=person.nome,
        cognome=person.cognome,
        email=person.email,
        created_at=person.created_at,
        cv_uploaded_at=person.cv_uploaded_at,
        ha_cv=person.ha_cv,
    )


def duplicate_list(session: Session) -> DuplicateListRead:
    """`find_duplicates` as the API and the MCP server answer it."""
    report = find_duplicates(session)

    def read(pair: DuplicatePair) -> DuplicatePairRead:
        return DuplicatePairRead(
            survivor=_person_read(pair.survivor),
            assorbito=_person_read(pair.assorbito),
            segnali=list(pair.segnali),
            unibile=pair.unibile,
        )

    return DuplicateListRead(
        unibili=[read(p) for p in report.unibili],
        da_rivedere=[read(p) for p in report.da_rivedere],
        iscrizioni=len(report.iscrizioni),
    )


__all__ = [
    "LINKEDIN",
    "NOME",
    "TELEFONO",
    "UNIQUE_INDEXES",
    "DuplicateListRead",
    "DuplicatePair",
    "DuplicatePairRead",
    "DuplicateReport",
    "IndexesEnsured",
    "LeadDuplicate",
    "Person",
    "PersonRead",
    "duplicate_list",
    "duplicates_of",
    "ensure_unique_indexes",
    "find_duplicates",
]
