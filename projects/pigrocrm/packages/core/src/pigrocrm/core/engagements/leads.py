"""The leads route of the engagements door (spec 2026-10-30 § 2, REB-866): what the hub
sends when a company completes a request, filed in rebase's own space as a customer, its
contact and a deal.

One space only, named by `PIGROCRM_LEADS_SPACE`: the route never takes a slug from the
caller, so the engagements token reaches through it no space it could not already reach.
Everything written is written as `Actor.rebase()`, so the space's timeline says the hub
did it. Imported by its full path, like `engagements.service` (see the package docstring).

Three rules decide what a call writes:

- A request is filed once. The deal carries `rebase:request=<request_id>` in its note and
  a retry finds it first (`DealRepository.find_by_marker`), so a second `PUT`, or one after
  a crash, writes nothing.
- A customer is reused only through the referente's email: the oldest live person with
  that address whose customer is live. Never by name: «Rossi» and «Rossi S.r.l.» are the
  same company to a person and two strings to a query, and a wrong merge is worse than a
  duplicate a person archives.
- Every request gets a deal of its own, even when the customer has an open one, except
  when the body names a `deal_id` (an admin linking the request to a deal the CRM already
  has): then that deal gets the marker and an activity, and nothing else is created.

A session advisory lock on the lowercased email serialises the calls of one person, so two
requests from them, or the wizard's job racing the hub's sweep, cannot both create a
customer. A lock held past `LOCK_TIMEOUT` is `LeadBusy`, a «not now» the hub retries.
"""

import logging
from uuid import UUID

from psycopg.errors import LockNotAvailable
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealCreate, DealUpdate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.schemas import LeadRead, LeadUpsert
from pigrocrm.core.engagements.service import (
    LOCK,
    EngagementService,
    deal_url,
)
from pigrocrm.core.errors import NotFound, ValidationFailed
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
KIND_FILED = "hub.richiesta_ricevuta"
KIND_LINKED = "hub.richiesta_collegata"


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
    """`rebase:request=<request_id>`, the last line of the deal's note and the key a
    retry recovers the deal by."""
    return f"rebase:request={request_id}"


def _same_name(typed: str, known: str) -> bool:
    return " ".join(typed.split()).casefold() == " ".join(known.split()).casefold()


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
        email = str(data.referente.email).strip().lower()
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
            recorded = DealRepository(space).find_by_marker(lead_marker(request_id))
            if recorded is not None:
                return self._answer(
                    tenant.slug,
                    recorded.customer_id,
                    self._person_id(space, email, recorded.customer_id),
                    recorded.id,
                    creato=False,
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
        """§ 2 case 1: the request joins a deal the CRM already has, and nothing else is
        created. A deal that already carries this request's marker is the answer of a
        retry and is left as it is."""
        deal = DealRepository(space).get(deal_id)
        if deal is None:
            raise ValidationFailed(ENTITY, "deal_id", NO_SUCH_DEAL)
        marker = lead_marker(request_id)
        person_id = self._person_id(space, email, deal.customer_id)
        if deal.note is not None and marker in deal.note:
            return self._answer(slug, deal.customer_id, person_id, deal.id, creato=False)
        note = f"{deal.note.rstrip()}\n{marker}" if deal.note else marker
        # Recorded first and left to the update's commit: the entry survives only with
        # the marker it is about (`ActivityService.record` joins the caller's transaction).
        ActivityService(space).record(
            "deal",
            deal.id,
            KIND_LINKED,
            Actor.rebase(),
            {"richiesta": str(request_id), "azienda": data.azienda, "nota": data.deal.note},
        )
        DealService(space).update(deal.id, DealUpdate(note=note), Actor.rebase())
        return self._answer(slug, deal.customer_id, person_id, deal.id, creato=False)

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
        customers = CustomerRepository(space)
        people = self._people_with_email(space, email)
        reused = next(
            (
                person
                for person in people
                if person.customer_id is not None and customers.get(person.customer_id)
            ),
            None,
        )
        known_name: str | None = None
        if reused is not None and reused.customer_id is not None:
            customer_id = reused.customer_id
            person_id: UUID = reused.id
            customer = customers.get(customer_id)
            known_name = customer.ragione_sociale if customer is not None else None
        else:
            customer_id = (
                CustomerService(space)
                ._insert(
                    CustomerCreate(
                        ragione_sociale=data.azienda,
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
            else:
                person_id = (
                    PersonService(space)
                    ._insert(
                        PersonCreate(
                            nome=data.referente.nome,
                            cognome=data.referente.cognome,
                            email=email,
                            telefono=data.referente.telefono,
                            linkedin=data.referente.linkedin_url,
                            customer_id=customer_id,
                        ),
                        actor,
                    )
                    .id
                )

        notes = [data.deal.note] if data.deal.note else []
        if known_name is not None and not _same_name(data.azienda, known_name):
            notes.append(
                f"Nome indicato nella richiesta: {data.azienda}. "
                f"Cliente già presente nel CRM: {known_name}."
            )
        notes.append(lead_marker(request_id))
        deal = DealService(space).create(
            DealCreate(
                nome=data.deal.nome,
                customer_id=customer_id,
                valore_previsto=data.deal.valore_previsto,
                data_chiusura_prevista=data.deal.data_chiusura_prevista,
                note="\n".join(notes),
            ),
            actor,
        )
        ActivityService(space).record(
            "deal", deal.id, KIND_FILED, actor, {"richiesta": str(request_id)}
        )
        space.commit()
        return self._answer(slug, customer_id, person_id, deal.id, creato=True)

    @staticmethod
    def _people_with_email(space: Session, email: str) -> list[Person]:
        """The live people with this address, case-insensitively (`people.email` is not
        unique), oldest first."""
        return list(
            space.scalars(
                select(Person)
                .where(Person.deleted_at.is_(None), func.lower(Person.email) == email)
                .order_by(Person.created_at, Person.id)
            )
        )

    def _person_id(self, space: Session, email: str, customer_id: UUID) -> UUID | None:
        """The contact an answer names when the deal already exists: the oldest live
        person with the address at the deal's customer, or `None`, never somebody of
        another customer."""
        people = self._people_with_email(space, email)
        found = next((p for p in people if p.customer_id == customer_id), None)
        return found.id if found is not None else None

    def _answer(
        self, slug: str, customer_id: UUID, person_id: UUID | None, deal_id: UUID, *, creato: bool
    ) -> LeadRead:
        return LeadRead(
            customer_id=customer_id,
            person_id=person_id,
            deal_id=deal_id,
            deal_url=deal_url(self.settings, slug, deal_id),
            creato=creato,
        )
