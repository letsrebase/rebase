"""The engagements door (spec 2026-09-25 § 2.3): what a signed letter sets up in the
freelancer's CRM -- the space, the customer «rebase» in it, and the deal of the letter --
once per hub match, whatever the number of calls.

On the registry's engine, never on a space's: the registry says which space, and each
space is opened for the length of one step. Everything written in a space is written as
`Actor.rebase()` (§ 2.5), so the space's own history says who did it.

Imported by its full path, never through `pigrocrm.core.engagements` (see that
package's docstring: this module imports `tenants.service`, which imports
`tenants.database`, which imports this package's `models`).
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from uuid import UUID

from psycopg.errors import LockNotAvailable
from sqlalchemy import Connection, Engine, create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db import session_factory
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.engagements.schemas import (
    COMPANY_IN_DEAL_NAME,
    HOURS_PER_DAY,
    ROLE_IN_DEAL_NAME,
    SPACE_NAME_MAX_LENGTH,
    EngagementFreelancer,
    EngagementRead,
    EngagementUpsert,
)
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.mail import EmailSender
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.models import Tenant
from pigrocrm.core.tenants.schemas import SLUG_MAX, TenantSignup, slugify
from pigrocrm.core.tenants.service import TenantService
from pigrocrm.core.tenants.welcome import welcome

logger = logging.getLogger(__name__)

ENTITY = "engagement"
# How long a call waits for another call on the same address before answering that it
# is busy: long enough for a provisioning, short enough that a call which hung while
# holding the address does not pin every later one forever.
LOCK_TIMEOUT = "120s"
LOCK = text("SELECT pg_advisory_lock(hashtext(:email))")
UNLOCK = text("SELECT pg_advisory_unlock(hashtext(:email))")
BUSY = "Un'altra chiamata sta preparando lo spazio di questo indirizzo: riprova tra poco."
# `deals.tariffa_oraria` is Numeric(12, 6).
RATE_PLACES = Decimal("0.000001")
# What `slugify` of a name made only of characters it drops (a name in another script)
# stands in with: an empty base would never become a valid slug, whatever its suffix.
FALLBACK_SLUG = "spazio"
# `-2` ... `-50`: past this many namesakes the name is not what is wrong.
SLUG_SUFFIX_MAX = 50


def deal_name(numero: str, ruolo: str, azienda: str) -> str:
    """`Lettera n. 3/2026 · Backend developer per Acme S.r.l.`, the role cut to 80 and
    the company to 100 characters so the longest inputs stay under deals.nome's 255.
    A label: the marker below is what finds a deal again."""
    return f"Lettera n. {numero} · {ruolo[:ROLE_IN_DEAL_NAME]} per {azienda[:COMPANY_IN_DEAL_NAME]}"


def deal_marker(match_id: UUID) -> str:
    """`rebase:match=<match_id>`, the last line of the deal's note and the key a retry
    recovers the deal by (`DealRepository.find_by_marker`). The name is not: a
    freelancer may have a deal of their own with the same label, or rename ours."""
    return f"rebase:match={match_id}"


def _origin(settings: Settings) -> str:
    return settings.public_url.strip().rstrip("/")


def space_url(settings: Settings, slug: str) -> str:
    """The space's home, as the hub links it."""
    return f"{_origin(settings)}/{slug}/app/"


def deal_url(settings: Settings, slug: str, deal_id: UUID) -> str:
    """The deal's own page, the SPA's `/<slug>/app/deal/<id>` route."""
    return f"{space_url(settings, slug)}deal/{deal_id}"


def _customer_note(numero: str) -> str:
    return (
        f"Creato da rebase per la lettera n. {numero}. rebase legge le ore dei deal di "
        "questo cliente per rendicontare gli incarichi."
    )


def _deal_note(numero: str, azienda: str, match_id: UUID) -> str:
    # «con Acme S.r.l. rebase legge»: a company whose name ends in a full stop ends the
    # sentence with it, and a second one would read as a typo.
    sentence = azienda if azienda.endswith(".") else f"{azienda}."
    return (
        f"Creato da rebase per la lettera n. {numero} con {sentence} rebase legge le ore "
        f"di questo deal per la rendicontazione al cliente.\n{deal_marker(match_id)}"
    )


class EngagementService:
    """On the registry's engine: `ensure` opens its own sessions on it (one for the
    rows, one connection for the lock), so a caller hands over the engine and nothing
    that is already in a transaction."""

    def __init__(
        self, registry_engine: Engine, settings: Settings, sender: EmailSender | None = None
    ) -> None:
        self.registry_engine = registry_engine
        self.settings = settings
        self.sender = sender

    def ensure(self, match_id: UUID, data: EngagementUpsert) -> EngagementRead:
        """The space, the customer «rebase» and the letter's deal for one match (spec
        § 2.3 steps 1 to 6), each found before it is created, so any number of calls
        with the same `match_id` sets them up once and answers the same ids.

        Under a session-level advisory lock on the freelancer's address, taken on a
        connection of its own and held for the whole call. Not `pg_advisory_xact_lock`:
        the registry session below commits more than once (`TenantService.provision`
        commits on its own, the row is committed before the space is touched), and a
        transaction-level lock would go at the first of those commits. So two letters of
        one freelancer activating together open one space, and two retries of one match
        wait for each other from the first step to the last."""
        if not _origin(self.settings):
            raise ValidationFailed(ENTITY, "public_url", "PIGROCRM_PUBLIC_URL non configurato")
        email = str(data.freelancer.email).strip().lower()
        with self.registry_engine.connect() as lock:
            try:
                # `is_local`: the timeout lives as long as this transaction, and the
                # pooled connection goes back without it.
                lock.execute(
                    text("SELECT set_config('lock_timeout', :timeout, true)"),
                    {"timeout": LOCK_TIMEOUT},
                )
                lock.execute(LOCK, {"email": email})
            except OperationalError as exc:
                if not isinstance(exc.orig, LockNotAvailable):
                    raise
                raise Conflict(ENTITY, BUSY, email=email) from exc
            try:
                # Out of the transaction the lock was taken in: the lock is the
                # session's, and the connection waits idle, not idle in a transaction.
                lock.commit()
                return self._ensure(match_id, data, email)
            finally:
                self._unlock(lock, email)

    @staticmethod
    def _unlock(lock: Connection, email: str) -> None:
        """Releases the address. If the unlock itself fails, the connection is
        invalidated instead of going back to the pool holding the lock: closing it is
        what releases a session lock. Not re-raised, so the call's own answer or error
        is the one that surfaces; logged by type only, since SQLAlchemy's message
        carries the statement's parameters, and the address is one of them."""
        try:
            lock.execute(UNLOCK, {"email": email})
            lock.commit()
        except Exception as exc:
            lock.invalidate()
            logger.warning(
                "engagements: pg_advisory_unlock failed (%s); connection dropped",
                type(exc).__name__,
            )

    def _ensure(self, match_id: UUID, data: EngagementUpsert, email: str) -> EngagementRead:
        with session_factory(self.registry_engine)() as registry:
            row = registry.scalar(
                select(RebaseEngagement).where(RebaseEngagement.match_id == match_id)
            )
            spazio_creato = False
            if row is not None:
                tenant = registry.get(Tenant, row.tenant_id)
                if tenant is None:  # pragma: no cover - a foreign key holds it
                    raise NotFound("tenant", row.tenant_id)
                if row.deal_id is not None:
                    return self._recorded(tenant, row.customer_id, row.deal_id, match_id)
                # A previous call stopped between steps 3 and 6: resume at step 4, in the
                # space it had already chosen.
            else:
                owned = self._owned_space(registry, email)
                if owned is None:
                    tenant = self._provision(registry, data.freelancer, email)
                    spazio_creato = True
                else:
                    tenant = owned
                # Committed before the space is touched: a failure from here on leaves a
                # row that says «space found, deal missing», which step 2 resumes.
                row = RebaseEngagement(match_id=match_id, tenant_id=tenant.id)
                registry.add(row)
                registry.commit()

            with self._space_session(tenant) as space:
                customer_id = self._customer(space, data)
                deal_id = self._deal(space, match_id, data, customer_id, email)

            row.customer_id = customer_id
            row.deal_id = deal_id
            registry.commit()
            return self._answer(tenant.slug, customer_id, deal_id, spazio_creato, creato=True)

    def _recorded(
        self, tenant: Tenant, customer_id: UUID | None, deal_id: UUID, match_id: UUID
    ) -> EngagementRead:
        """Step 2 with a deal on the row: alive, the row's ids (spec § 2.3); gone, a
        409, and nothing is recreated behind the freelancer's back."""
        with self._space_session(tenant) as space:
            try:
                deal = DealService(space).get(deal_id, Actor.rebase())
            except NotFound as exc:
                raise Conflict(
                    ENTITY,
                    "Il deal di questa lettera è stato eliminato nello spazio.",
                    match_id=str(match_id),
                ) from exc
        # Step 6 writes both ids together, so a row with a deal has its customer; the
        # deal's own stands in only for a row somebody edited by hand.
        answered = customer_id if customer_id is not None else deal.customer_id
        return self._answer(tenant.slug, answered, deal_id, False, creato=False)

    def _answer(
        self, slug: str, customer_id: UUID, deal_id: UUID, spazio_creato: bool, *, creato: bool
    ) -> EngagementRead:
        return EngagementRead(
            slug=slug,
            url=space_url(self.settings, slug),
            customer_id=customer_id,
            deal_id=deal_id,
            deal_url=deal_url(self.settings, slug, deal_id),
            spazio_creato=spazio_creato,
            creato=creato,
        )

    def _owned_space(self, registry: Session, email: str) -> Tenant | None:
        """The oldest space the address owns, case-insensitively on both sides: the
        freelancer who already signed up keeps working where they already are."""
        return registry.scalars(
            select(Tenant)
            .where(func.lower(Tenant.owner_email) == email)
            .order_by(Tenant.created_at, Tenant.id)
            .limit(1)
        ).first()

    def _provision(self, registry: Session, freelancer: EngagementFreelancer, email: str) -> Tenant:
        """A space in the freelancer's name, then the welcome the signup sends (spec
        § 2.3 step 3): the slug from the name, `-2`, `-3`... in place of its tail while
        the name is taken or reserved, up to `SLUG_SUFFIX_MAX`. A name taken between the
        question and the insert (a namesake's signup at that moment) is `provision`'s
        `Conflict` on that very slug, and moves on to the next candidate; any other
        `Conflict`, or the last candidate's, is the caller's."""
        tenants = TenantService(registry, self.settings)
        nome = f"{freelancer.nome} {freelancer.cognome}"
        base = slugify(nome) or FALLBACK_SLUG
        for n in range(1, SLUG_SUFFIX_MAX + 1):
            suffix = "" if n == 1 else f"-{n}"
            candidate = base if n == 1 else f"{base[: SLUG_MAX - len(suffix)]}{suffix}"
            if not tenants.availability(candidate).disponibile:
                continue
            try:
                created = tenants.provision(
                    TenantSignup(
                        slug=candidate,
                        nome=nome[:SPACE_NAME_MAX_LENGTH],
                        email=email,
                        membro=True,
                    )
                )
            except Conflict as exc:
                if exc.details.get("slug") != candidate or n == SLUG_SUFFIX_MAX:
                    raise
                continue
            tenant = tenants.get(created.slug)
            self._welcome(tenant, email)
            return tenant
        raise Conflict("tenant", "questo nome è già in uso", slug=base)

    def _welcome(self, tenant: Tenant, email: str) -> None:
        """What the signup sends after provisioning, sent at once: nothing here is a
        request waiting on it."""
        if self.sender is None:
            return
        with self._space_session(tenant) as space:
            mail = welcome(space, self.settings, self.sender, email, tenant.slug, membro=True)
        if mail is not None:
            self.sender.send(mail)

    @contextmanager
    def _space_session(self, tenant: Tenant) -> Iterator[Session]:
        engine = create_engine(
            tenant_database_url(self.settings, tenant_database_name(tenant.slug)), future=True
        )
        try:
            with session_factory(engine)() as space:
                yield space
        finally:
            engine.dispose()

    def _customer(self, space: Session, data: EngagementUpsert) -> UUID:
        """Step 4: the customer «rebase», by VAT number when the body carries one, else
        by its exact name among the live customers; created when missing. An existing
        one is left as it is: its fields are the freelancer's to edit."""
        rebase = data.rebase
        customers = CustomerRepository(space)
        found = (
            customers.match_by_fiscal_id(partita_iva=rebase.partita_iva, codice_fiscale=None)
            if rebase.partita_iva is not None
            else customers.find_by_name(rebase.ragione_sociale)
        )
        if found is not None:
            return found.id
        created = CustomerService(space).create(
            CustomerCreate(
                ragione_sociale=rebase.ragione_sociale,
                partita_iva=rebase.partita_iva,
                codice_fiscale=rebase.codice_fiscale,
                indirizzo=rebase.indirizzo,
                pec=str(rebase.pec) if rebase.pec is not None else None,
                codice_sdi=rebase.codice_sdi,
                note=_customer_note(data.lettera.numero),
            ),
            Actor.rebase(),
        )
        return created.id

    def _deal(
        self,
        space: Session,
        match_id: UUID,
        data: EngagementUpsert,
        customer_id: UUID,
        email: str,
    ) -> UUID:
        """Step 5: the letter's deal under that customer. The live deal carrying this
        match's marker is the one a previous call created and failed to record, and is
        reused; a deal with the same name and no marker is somebody else's, and ours is
        created beside it, in the default open stage, owned by the space's admin whose
        address is the freelancer's."""
        marker = deal_marker(match_id)
        recorded = DealRepository(space).find_by_marker(customer_id, marker)
        if recorded is not None:
            return recorded.id
        lettera = data.lettera
        owner = UserRepository(space).get_by_email(email)
        created = DealService(space).create(
            DealCreate(
                nome=deal_name(lettera.numero, lettera.ruolo, lettera.azienda),
                customer_id=customer_id,
                tariffa_oraria=(lettera.compenso / HOURS_PER_DAY).quantize(RATE_PLACES),
                ore_preventivate=(
                    lettera.giorni_previsti * HOURS_PER_DAY
                    if lettera.giorni_previsti is not None
                    else None
                ),
                data_chiusura_prevista=lettera.data_fine,
                owner_id=owner.id if owner is not None else None,
                note=_deal_note(lettera.numero, lettera.azienda, match_id),
            ),
            Actor.rebase(),
        )
        return created.id
