"""The leads route of the engagements door (spec 2026-10-30 § 2, REB-866): what the hub
sends when a company completes a request, filed in rebase's own space as a customer, its
contact and a deal.

One space only, named by `PIGROCRM_LEADS_SPACE`: the route never takes a slug from the
caller, so the engagements token reaches through it no space it could not already reach.
Everything written is written as `Actor.rebase()`, so the space's timeline says the hub
did it. Imported by its full path, like `engagements.service` (see the package docstring).

Three rules decide what a call writes:

- A request is filed once. The deal carries `rebase:request=<request_id>` among its
  `rebase_refs` and a retry finds it first (`DealRepository.find_by_rebase_ref`), so a
  second `PUT`, or one after a crash, writes nothing.
- A customer is reused only through the referente's email: the oldest live person with
  that address whose customer is live, one who has it as their primary email before one
  who keeps it as another point. Never by name: «Rossi» and «Rossi S.r.l.» are the
  same company to a person and two strings to a query, and a wrong merge is worse than a
  duplicate a person archives.
- The contact's extra points (`contact.contact_points`, REB-945) go to the person the call
  creates, after the address, phone and profile it was keyed on, and to a person it reuses
  only when that person does not have them yet, after theirs: nothing of theirs changes.
  A point the point rules refuse is left out, never a refusal.
- Every request gets a deal of its own, even when the customer has an open one, except
  when the body names a `deal_id` (an admin linking the request to a deal the CRM already
  has): then that deal gets the marker, an activity and the request's note, and no
  customer, person or deal is created.

A session advisory lock on the lowercased email serialises the calls of one person, so two
requests from them, or the wizard's job racing the hub's sweep, cannot both create a
customer. A lock held past `LOCK_TIMEOUT` is `LeadBusy`, a «not now» the hub retries.
"""

import logging
from uuid import UUID

from psycopg.errors import LockNotAvailable
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.contact_points.schemas import MAX_CONTACT_POINTS, ContactPointIn
from pigrocrm.core.contact_points.service import (
    add_points,
    email_key,
    lock_owner,
    prepare,
)
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.schemas import LeadRead, LeadUpsert
from pigrocrm.core.engagements.service import (
    LOCK,
    EngagementService,
    deal_url,
)
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.notes.writer import append_first_note
from pigrocrm.core.organizations.repository import OrganizationRepository
from pigrocrm.core.organizations.schemas import OrganizationCreate
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.schemas import PersonCreate
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.tenants.models import Tenant
from pigrocrm.core.tenants.service import TenantService

logger = logging.getLogger(__name__)

ENTITY = "lead"
# How long a call waits for another call of the same person before answering that it is
# busy: a filing is a handful of rows, so far shorter than a provisioning's wait.
LOCK_TIMEOUT = "30s"
# The lock lives in the same advisory keyspace as the engagements door's, by address; the
# prefix keeps a freelancer's letter from queueing behind a company's request and back.
LOCK_PREFIX = "lead:"
NOT_CONFIGURED = "Spazio dei lead non configurato"
BUSY = "Un'altra richiesta della stessa persona è in corso: riprova tra poco."
NO_SUCH_DEAL = "il deal non esiste in questo spazio"
# `activities.kind` of the two events this route adds to a deal's timeline.
KIND_FILED = "hub.request_received"
KIND_LINKED = "hub.request_linked"


class LeadsSpaceNotConfigured(Exception):
    """`PIGROCRM_LEADS_SPACE` is empty, or names a space the registry does not have: the
    route is not set up, which is the installation's fault and not the caller's, so a 503
    (the hub keeps the request waiting and retries when it is)."""

    def __init__(self) -> None:
        super().__init__(NOT_CONFIGURED)


class LeadBusy(Exception):
    """Another call of the same person has held their address for longer than
    `LOCK_TIMEOUT`: a «not now», answered 503 so the hub retries, never a refusal."""

    def __init__(self) -> None:
        super().__init__(BUSY)


def lead_marker(request_id: UUID) -> str:
    """`rebase:request=<request_id>`, written to the deal's `rebase_refs` and the key a
    retry recovers the deal by."""
    return f"rebase:request={request_id}"


def _same_name(typed: str, known: str) -> bool:
    return " ".join(typed.split()).casefold() == " ".join(known.split()).casefold()


def _acceptable(point: ContactPointIn) -> bool:
    """Whether the CRM's point rules take this point as a new value."""
    try:
        prepare("person", [point])
    except ValidationFailed:
        return False
    return True


def _contact_points(email: str, data: LeadUpsert) -> list[ContactPointIn]:
    """The points of the contact a new person is born with: the address the request came
    with, which the door is keyed on, and the phone and the LinkedIn profile it carried, all
    `work` and primary (an extra point that is the same value as one of them gives it its label
    and note), then every extra point of `contact.contact_points` after them. The
    flat phone and profile were free text before points had a shape, and a request is not
    refused for a number or a profile the CRM cannot read: such a value is left out, as a
    blank one is, and so is an extra point the rules refuse, one already in the list (same
    kind, same compared key) or one past the list's bound. An extra point is never primary:
    the primary of a kind is the flat value, and when the flat value is absent or was left
    out the first point of that kind takes the place by the list's own rule."""
    points = [ContactPointIn(kind="email", value=email)]
    optional: list[ContactPointIn] = []
    if data.contact.phone and data.contact.phone.strip():
        optional.append(ContactPointIn(kind="phone", value=data.contact.phone))
    if data.contact.linkedin_url and data.contact.linkedin_url.strip():
        optional.append(ContactPointIn(kind="linkedin", value=data.contact.linkedin_url))
    points.extend(point for point in optional if _acceptable(point))
    rows = prepare("person", points)
    # The hub sends its primary phone both flat and in the list, with the label and note it has
    # there: the flat point takes them rather than the default.
    described = {
        (row.kind, row.normalized): point
        for point in data.contact.contact_points or []
        if _acceptable(point)
        for row in prepare("person", [point])
    }
    points = [
        point.model_copy(update={"label": twin.label, "note": twin.note})
        if (twin := described.get((row.kind, row.normalized))) is not None
        else point
        for point, row in zip(points, rows, strict=True)
    ]
    keys = {(row.kind, row.normalized) for row in rows}
    return points + _new_extras(data, keys, MAX_CONTACT_POINTS - len(points))


def _new_extras(data: LeadUpsert, known: set[tuple[str, str]], room: int) -> list[ContactPointIn]:
    """The extra points of the request that the CRM takes and `known` (kind and compared
    key) does not have, as non-primary points, at most `room` of them, in the order sent."""
    extras: list[ContactPointIn] = []
    for point in data.contact.contact_points or []:
        if len(extras) >= room:
            break
        point = point.model_copy(update={"is_primary": False})
        if not _acceptable(point):
            continue
        row = prepare("person", [point])[0]
        if (row.kind, row.normalized) in known:
            continue
        known.add((row.kind, row.normalized))
        extras.append(point)
    return extras


class LeadService:
    """On the registry's engine, like `EngagementService`, whose space session and
    unlock it borrows: `file` opens its own sessions and connection."""

    def __init__(self, registry_engine: Engine, settings: Settings) -> None:
        self.registry_engine = registry_engine
        self.settings = settings
        self.door = EngagementService(registry_engine, settings)

    def file(self, request_id: UUID, data: LeadUpsert) -> LeadRead:
        """The customer, the contact and the deal of one hub request, each found before
        it is created, so any number of calls with one `request_id` files it once and
        answers the same ids. See the module docstring for what decides each one."""
        slug = self.settings.leads_space.strip()
        if not slug:
            raise LeadsSpaceNotConfigured()
        # The answer carries the deal's link, built from `PIGROCRM_PUBLIC_URL`.
        self.door._require_public_url()
        email = str(data.contact.email).strip().lower()
        key = f"{LOCK_PREFIX}{email}"
        with self.registry_engine.connect() as lock:
            try:
                # `is_local`: the timeout lives as long as this transaction, and the
                # pooled connection goes back without it.
                lock.execute(
                    text("SELECT set_config('lock_timeout', :timeout, true)"),
                    {"timeout": LOCK_TIMEOUT},
                )
                lock.execute(LOCK, {"email": key})
            except OperationalError as exc:
                if not isinstance(exc.orig, LockNotAvailable):
                    raise
                raise LeadBusy() from exc
            try:
                # Out of the transaction the lock was taken in: the lock is the
                # session's, and the connection waits idle, not idle in a transaction.
                lock.commit()
                return self._file(slug, request_id, data, email)
            finally:
                EngagementService._unlock(lock, key)

    def _tenant(self, slug: str) -> Tenant:
        with session_factory(self.registry_engine)() as registry:
            try:
                return TenantService(registry, self.settings).get(slug)
            except NotFound as exc:
                raise LeadsSpaceNotConfigured() from exc

    def _file(self, slug: str, request_id: UUID, data: LeadUpsert, email: str) -> LeadRead:
        tenant = self._tenant(slug)
        with self.door._space_session(tenant) as space:
            if data.deal_id is not None:
                return self._link(space, tenant.slug, request_id, data, email, data.deal_id)
            recorded = DealRepository(space).find_by_rebase_ref(lead_marker(request_id))
            if recorded is not None:
                return self._answer(
                    tenant.slug,
                    recorded.customer_id,
                    self._person_id(space, email, recorded.customer_id),
                    recorded.id,
                    created=False,
                )
            return self._create(space, tenant.slug, request_id, data, email)

    def _link(
        self,
        space: Session,
        slug: str,
        request_id: UUID,
        data: LeadUpsert,
        email: str,
        deal_id: UUID,
    ) -> LeadRead:
        """§ 2 case 1: the request joins a deal the CRM already has, and no customer, person
        or deal is created. A deal that already carries this request's marker is the answer
        of a retry and is left as it is."""
        deal = DealRepository(space).get(deal_id)
        if deal is None:
            raise ValidationFailed(ENTITY, "deal_id", NO_SUCH_DEAL)
        marker = lead_marker(request_id)
        person_id = self._person_id(space, email, deal.customer_id)
        if marker in deal.rebase_refs:
            return self._answer(slug, deal.customer_id, person_id, deal.id, created=False)
        # Recorded first and left to the reference's commit: the entries survive only with
        # the reference they are about (`ActivityService.record` joins the caller's
        # transaction). The prospect's sentence goes to the deal's thread, where it can be
        # edited or deleted, and not into the append-only timeline payload.
        ActivityService(space).record(
            "deal",
            deal.id,
            KIND_LINKED,
            Actor.rebase(),
            {"request": str(request_id), "company": data.company},
        )
        append_first_note(space, "deal", deal.id, data.deal.notes, Actor.rebase())
        DealService(space).attach_rebase_ref(deal.id, marker, Actor.rebase())
        return self._answer(slug, deal.customer_id, person_id, deal.id, created=False)

    def _create(
        self, space: Session, slug: str, request_id: UUID, data: LeadUpsert, email: str
    ) -> LeadRead:
        """§ 2 cases 2 and 3 and «always a new deal». The customer and the contact are
        flushed on this session and committed by the deal's own commit, so a call that
        fails before it leaves nothing behind. The deal's `KIND_FILED` entry is the one
        write after that commit (`DealService.create` commits on its own and has no
        no-commit variant): a crash between the two leaves a deal with no such entry,
        and its retry, finding the marker, does not write it."""
        actor = Actor.rebase()
        organizations = OrganizationRepository(space)
        people = self._people_with_email(space, email)
        # Only the tier the address is primary in, when there is one: a person who keeps it as
        # a second point is not the contact while somebody has it as theirs.
        people = [p for p in people if self._holds_as_primary(p, email)] or people
        reused = next(
            (
                person
                for person in people
                if person.customer_id is not None and organizations.get(person.customer_id)
            ),
            None,
        )
        known_name: str | None = None
        if reused is not None and reused.customer_id is not None:
            customer_id = reused.customer_id
            person_id: UUID = reused.id
            self._add_points(space, reused, data, actor)
            customer = organizations.get(customer_id)
            known_name = customer.legal_name if customer is not None else None
        else:
            customer_id = (
                OrganizationService(space)
                ._insert(
                    OrganizationCreate(
                        legal_name=data.company,
                        note=(
                            "Creato da rebase dalla richiesta di un'azienda sull'hub il "
                            f"{today_local(self.settings):%d/%m/%Y}."
                        ),
                    ),
                    actor,
                )
                .id
            )
            # A person with this address and no live customer is the contact still: it
            # is attached to the new customer, not duplicated.
            person = people[0] if people else None
            if person is not None:
                person.customer_id = customer_id
                ActivityService(space).record(
                    "person", person.id, "updated", actor, {"changed": ["customer_id"]}
                )
                person_id = person.id
                self._add_points(space, person, data, actor)
            else:
                person_id = (
                    PersonService(space)
                    ._insert(
                        PersonCreate(
                            first_name=data.contact.first_name,
                            last_name=data.contact.last_name,
                            contact_points=_contact_points(email, data),
                            customer_id=customer_id,
                        ),
                        actor,
                    )
                    .id
                )

        notes = [data.deal.notes] if data.deal.notes else []
        if known_name is not None and not _same_name(data.company, known_name):
            notes.append(
                f"Nome indicato nella richiesta: {data.company}. "
                f"Cliente già presente nel CRM: {known_name}."
            )
        deal = DealService(space).create(
            DealCreate(
                name=data.deal.name,
                customer_id=customer_id,
                value_lines=data.deal.value_lines,
                expected_close_date=data.deal.expected_close_date,
                note="\n".join(notes),
            ),
            actor,
            rebase_ref=lead_marker(request_id),
        )
        ActivityService(space).record(
            "deal", deal.id, KIND_FILED, actor, {"request": str(request_id)}
        )
        space.commit()
        return self._answer(slug, customer_id, person_id, deal.id, created=True)

    @staticmethod
    def _add_points(space: Session, person: Person, data: LeadUpsert, actor: Actor) -> None:
        """The extra points of the request that a person already in the CRM does not have,
        added after theirs. Nothing they have is touched (`add_points` inserts only the new
        rows, so a legacy value stays as it was and every id stays), the primary of each
        kind stays, and the flat phone and profile are not added: only `contact_points` is,
        as the door added nothing to a known person before it existed. The row is locked
        and its list reloaded first, so what is missing is judged against what another
        writer left."""
        lock_owner(space, person)
        known = {(row.kind, row.normalized) for row in person.contact_points}
        extras = _new_extras(data, known, MAX_CONTACT_POINTS - len(person.contact_points))
        if add_points(space, person, extras):
            ActivityService(space).record(
                "person", person.id, "updated", actor, {"changed": ["contact_points"]}
            )

    @staticmethod
    def _holds_as_primary(person: Person, email: str) -> bool:
        key = email_key(email)
        return any(
            point.kind == "email" and point.is_primary and point.normalized == key
            for point in person.contact_points
        )

    @classmethod
    def _people_with_email(cls, space: Session, email: str) -> list[Person]:
        """The live people with this address among any of their email points,
        case-insensitively (an address is not unique), in two tiers: those whose primary
        email it is, then those who hold it as another point, oldest first within each. The
        address a request came with is the contact's, not a stranger's extra one."""
        owned = (
            select(ContactPoint.person_id)
            .where(ContactPoint.kind == "email", ContactPoint.normalized == email_key(email))
            .scalar_subquery()
        )
        people = list(
            space.scalars(
                select(Person)
                .where(Person.deleted_at.is_(None), Person.id.in_(owned))
                .order_by(Person.created_at, Person.id)
            )
        )
        return sorted(people, key=lambda person: not cls._holds_as_primary(person, email))

    def _person_id(self, space: Session, email: str, customer_id: UUID) -> UUID | None:
        """The contact an answer names when the deal already exists: the oldest live
        person with the address at the deal's customer, or `None`, never somebody of
        another customer."""
        people = self._people_with_email(space, email)
        found = next((p for p in people if p.customer_id == customer_id), None)
        return found.id if found is not None else None

    def _answer(
        self, slug: str, customer_id: UUID, person_id: UUID | None, deal_id: UUID, *, created: bool
    ) -> LeadRead:
        return LeadRead(
            customer_id=customer_id,
            person_id=person_id,
            deal_id=deal_id,
            deal_url=deal_url(self.settings, slug, deal_id),
            created=created,
        )
