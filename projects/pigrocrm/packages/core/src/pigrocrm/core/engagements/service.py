"""The engagements door (spec 2026-09-25 § 2.3): what a signed letter sets up in the
freelancer's CRM -- the space, the customer «rebase» in it, and the deal of the letter --
once per hub match, whatever the number of calls; and the report of that deal's hours
with the invoices they sit on (§ 2.4), the one thing of the space the door reads back.

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
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.errors import LockNotAvailable
from sqlalchemy import Connection, Engine, create_engine, func, select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealCreate, DealRead
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.engagements.schemas import (
    COMPANY_IN_DEAL_NAME,
    HOURS_PER_DAY,
    REPORT_MAX_DAYS,
    ROLE_IN_DEAL_NAME,
    SPACE_NAME_MAX_LENGTH,
    EngagementFreelancer,
    EngagementRead,
    EngagementReport,
    EngagementUpsert,
    ReportDeal,
    ReportEntry,
    ReportInvoice,
)
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.mail import EmailSender
from pigrocrm.core.money import sum_hours
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.models import Tenant
from pigrocrm.core.tenants.schemas import SLUG_MAX, TenantSignup, slugify
from pigrocrm.core.tenants.service import TenantService
from pigrocrm.core.tenants.welcome import welcome
from pigrocrm.core.timetracking.schemas import TimeEntryListQuery, TimeEntryRead
from pigrocrm.core.timetracking.service import TimeEntryService, billed_entry_ids

logger = logging.getLogger(__name__)

ENTITY = "engagement"
# How long a call waits for another call on the same address before answering that it
# is busy: long enough for a provisioning, short enough that a call which hung while
# holding the address does not pin every later one forever.
LOCK_TIMEOUT = "120s"
LOCK = text("SELECT pg_advisory_lock(hashtext(:email))")
UNLOCK = text("SELECT pg_advisory_unlock(hashtext(:email))")
BUSY = "Un'altra chiamata sta preparando lo spazio di questo indirizzo: riprova tra poco."
GONE = "Il deal di questa lettera è stato eliminato nello spazio."
REVERSED = "La data di fine precede quella di inizio."
# The report reads the deal's hours through `TimeEntryService.list`, this many a page
# (its own cap), until the cursor runs out.
REPORT_PAGE_SIZE = 200
# `deals.tariffa_oraria` is Numeric(12, 6).
RATE_PLACES = Decimal("0.000001")
# What `slugify` of a name made only of characters it drops (a name in another script)
# stands in with: an empty base would never become a valid slug, whatever its suffix.
FALLBACK_SLUG = "spazio"
# `-2` ... `-50`: past this many namesakes the name is not what is wrong.
SLUG_SUFFIX_MAX = 50


class EngagementBusy(Exception):
    """Another call has held the freelancer's address for longer than `LOCK_TIMEOUT`.
    Not a refusal of this request but a «not now»: the router answers it `503`, which
    the hub retries, where a `409` would file the match as refused for good. It
    carries no address: the answer is `BUSY` and nothing else."""

    def __init__(self) -> None:
        super().__init__(BUSY)


class SpaceUnreachable(Exception):
    """A database error inside a space: its database missing (a provisioning a restart
    cut short after the registry row), unreachable, or not migrated yet. Carries the
    tenant's id, the one name of the space a log line may hold (never its slug, which
    is the freelancer's name, nor the address); the database error is its cause."""

    def __init__(self, tenant_id: UUID) -> None:
        super().__init__(f"space {tenant_id} unreachable")
        self.tenant_id = tenant_id


def full_name(freelancer: EngagementFreelancer) -> str:
    """`Ada Lovelace`, cut to the signup's 200 characters: the space's name and its
    admin's, the same on a space the door opens and on an admin it completes."""
    return f"{freelancer.nome} {freelancer.cognome}"[:SPACE_NAME_MAX_LENGTH]


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
    """On the registry's engine: `ensure` and `report` open their own sessions on it
    (`ensure` one for the rows and one connection for the lock), so a caller hands over
    the engine and nothing that is already in a transaction."""

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
        wait for each other from the first step to the last. A wait longer than
        `LOCK_TIMEOUT` is `EngagementBusy`, and a database error inside the space is
        `SpaceUnreachable`: both a «not now», never a refusal."""
        self._require_public_url()
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
                raise EngagementBusy() from exc
            try:
                # Out of the transaction the lock was taken in: the lock is the
                # session's, and the connection waits idle, not idle in a transaction.
                lock.commit()
                return self._ensure(match_id, data, email)
            finally:
                self._unlock(lock, email)

    def report(
        self, match_id: UUID, da: date | None = None, a: date | None = None
    ) -> EngagementReport:
        """The hours on the match's deal from `da` to `a`, both included (spec § 2.4):
        one row per time entry with the invoice its line belongs to, the CRM's own split
        of billed hours, and one row per invoice with the hours of this report on it.
        Nothing else of the space: not its customers, not its other deals, not an
        invoice these hours do not sit on.

        `da` defaults to the day the deal was created and `a` to today, both in the
        CRM's zone; a period that ends before it starts, or spans over
        `REPORT_MAX_DAYS`, is refused, each with its own sentence. No row for the
        match, or a row a call left before its deal existed, is `NotFound`; a deal
        deleted in the space is `ensure`'s `Conflict`."""
        self._require_public_url()
        with session_factory(self.registry_engine)() as registry:
            row = self._row(registry, match_id)
            if row is None or row.deal_id is None:
                raise NotFound(ENTITY, str(match_id))
            tenant = self._tenant(registry, row)
            deal_id = row.deal_id

        with self._space_session(tenant) as space:
            deal = self._live_deal(space, deal_id, match_id)
            first, last = self._period(deal, da, a)
            times = TimeEntryService(space)
            entries = self._entries(times, deal_id, first, last)
            stato = times.deal_summary(deal_id, Actor.rebase()).stato
            billed = billed_entry_ids(space, entries)
            invoice_of_line, invoices = self._invoices(space, entries)

        on_invoice: dict[UUID, list[Decimal]] = {}
        giorni = []
        for entry in entries:
            invoice_id = (
                invoice_of_line.get(entry.invoice_line_id)
                if entry.invoice_line_id is not None
                else None
            )
            if invoice_id is not None:
                on_invoice.setdefault(invoice_id, []).append(entry.ore)
            giorni.append(
                ReportEntry(
                    data=entry.data,
                    ore=entry.ore,
                    descrizione=entry.descrizione,
                    fatturabile=entry.fatturabile,
                    fattura=invoices[invoice_id][0] if invoice_id is not None else None,
                )
            )
        totale = sum_hours(entry.ore for entry in entries)
        fatturate = sum_hours(entry.ore for entry in entries if entry.id in billed)
        # Newest first by the date it was issued, the undated (a draft, a proforma) last;
        # among equals the one created last first, then by id, so the order is stable.
        newest_first = sorted(
            invoices.values(),
            key=lambda pair: (
                pair[0].data is not None,
                pair[0].data or date.min,
                pair[1],
                pair[0].id,
            ),
            reverse=True,
        )
        return EngagementReport(
            slug=tenant.slug,
            deal_url=deal_url(self.settings, tenant.slug, deal_id),
            deal=ReportDeal(
                id=deal.id,
                nome=deal.nome,
                tariffa_oraria=deal.tariffa_oraria,
                ore_preventivate=deal.ore_preventivate,
                stato=stato,
            ),
            giorni=giorni,
            totale_ore=totale,
            ore_fatturate=fatturate,
            ore_non_fatturate=totale - fatturate,
            fatture=[
                invoice.model_copy(update={"ore": sum_hours(on_invoice[invoice.id])})
                for invoice, _ in newest_first
            ],
        )

    def _require_public_url(self) -> None:
        """Both answers carry links built from `PIGROCRM_PUBLIC_URL`: without it the
        door is not configured, whichever route is asked."""
        if not _origin(self.settings):
            raise ValidationFailed(ENTITY, "public_url", "PIGROCRM_PUBLIC_URL non configurato")

    def _period(self, deal: DealRead, da: date | None, a: date | None) -> tuple[date, date]:
        """`da` or the day the deal was created, `a` or today, both in the CRM's zone:
        `created_at` is an instant, and 22:30 UTC on 1 October is the 2nd in Rome. At
        most `REPORT_MAX_DAYS` from one to the other, so the hub's windows of exactly
        that many days, bounds touching, all pass."""
        first = (
            da
            if da is not None
            else deal.created_at.astimezone(ZoneInfo(self.settings.timezone)).date()
        )
        last = a if a is not None else today_local(self.settings)
        if last < first:
            raise ValidationFailed(ENTITY, "periodo", REVERSED)
        if (last - first).days > REPORT_MAX_DAYS:
            raise ValidationFailed(ENTITY, "periodo", f"al massimo {REPORT_MAX_DAYS} giorni")
        return first, last

    @staticmethod
    def _entries(times: TimeEntryService, deal_id: UUID, da: date, a: date) -> list[TimeEntryRead]:
        """Every live entry of the deal in `[da, a]`, through the service's own list and
        its cursor to the end, sorted by day and then by when it was logged (the list
        itself runs newest first)."""
        entries: list[TimeEntryRead] = []
        cursor: UUID | None = None
        while True:
            page = times.list(
                TimeEntryListQuery(
                    deal_id=deal_id, da=da, a=a, limit=REPORT_PAGE_SIZE, cursor=cursor
                ),
                Actor.rebase(),
            )
            entries.extend(page.items)
            if page.next_cursor is None:
                return sorted(entries, key=lambda e: (e.data, e.created_at, e.id))
            cursor = page.next_cursor

    @staticmethod
    def _invoices(
        space: Session, entries: list[TimeEntryRead]
    ) -> tuple[dict[UUID, UUID], dict[UUID, tuple[ReportInvoice, datetime]]]:
        """The invoice of each entry's line, in one query for the whole report: which
        invoice each line is on, and each invoice once, with its `created_at` for the
        order. Any state (a draft, a proforma, an annulled invoice all still show), but
        not an invoice deleted since: the CRM no longer shows it, and neither does the
        door."""
        line_ids = {e.invoice_line_id for e in entries if e.invoice_line_id is not None}
        if not line_ids:
            return {}, {}
        rows = space.execute(
            select(
                InvoiceLine.id,
                Invoice.id,
                Invoice.tipo,
                Invoice.anno,
                Invoice.numero,
                Invoice.stato,
                Invoice.stato_pagamento,
                Invoice.data_emissione,
                Invoice.created_at,
            )
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(InvoiceLine.id.in_(line_ids), Invoice.deleted_at.is_(None))
        ).all()
        invoice_of_line: dict[UUID, UUID] = {}
        invoices: dict[UUID, tuple[ReportInvoice, datetime]] = {}
        for line_id, invoice_id, tipo, anno, numero, stato, pagamento, emissione, created in rows:
            invoice_of_line[line_id] = invoice_id
            invoices[invoice_id] = (
                ReportInvoice(
                    id=invoice_id,
                    tipo=tipo,
                    anno=anno,
                    numero=numero,
                    stato=stato,
                    stato_pagamento=pagamento,
                    data=emissione,
                ),
                created,
            )
        return invoice_of_line, invoices

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

    @staticmethod
    def _row(registry: Session, match_id: UUID) -> RebaseEngagement | None:
        return registry.scalar(
            select(RebaseEngagement).where(RebaseEngagement.match_id == match_id)
        )

    @staticmethod
    def _tenant(registry: Session, row: RebaseEngagement) -> Tenant:
        tenant = registry.get(Tenant, row.tenant_id)
        if tenant is None:  # pragma: no cover - a foreign key holds it
            raise NotFound("tenant", row.tenant_id)
        return tenant

    def _ensure(self, match_id: UUID, data: EngagementUpsert, email: str) -> EngagementRead:
        with session_factory(self.registry_engine)() as registry:
            row = self._row(registry, match_id)
            spazio_creato = False
            if row is not None:
                tenant = self._tenant(registry, row)
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
                if self._ensure_admin(space, data.freelancer, email):
                    # A space whose provisioning a restart cut short after its registry
                    # row, whose database the next boot then created: nobody in it, and
                    # the welcome never sent. It is opened now, and said so.
                    self._welcome(space, tenant.slug, email)
                    spazio_creato = True
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
            deal = self._live_deal(space, deal_id, match_id)
        # Step 6 writes both ids together, so a row with a deal has its customer; the
        # deal's own stands in only for a row somebody edited by hand.
        answered = customer_id if customer_id is not None else deal.customer_id
        return self._answer(tenant.slug, answered, deal_id, False, creato=False)

    @staticmethod
    def _live_deal(space: Session, deal_id: UUID, match_id: UUID) -> DealRead:
        """The row's deal in its space; soft-deleted or missing, the `409` both routes
        answer (spec § 2.3 step 2, § 2.4)."""
        try:
            return DealService(space).get(deal_id, Actor.rebase())
        except NotFound as exc:
            raise Conflict(ENTITY, GONE, match_id=str(match_id)) from exc

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
        nome = full_name(freelancer)
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
                        nome=nome,
                        email=email,
                        membro=True,
                    )
                )
            except Conflict as exc:
                if exc.details.get("slug") != candidate or n == SLUG_SUFFIX_MAX:
                    raise
                continue
            tenant = tenants.get(created.slug)
            with self._space_session(tenant) as space:
                self._welcome(space, tenant.slug, email)
            return tenant
        raise Conflict("tenant", "questo nome è già in uso", slug=base)

    def _welcome(self, space: Session, slug: str, email: str) -> None:
        """What the signup sends after provisioning, sent at once: nothing here is a
        request waiting on it."""
        if self.sender is None:
            return
        mail = welcome(space, self.settings, self.sender, email, slug, membro=True)
        if mail is not None:
            self.sender.send(mail)

    @staticmethod
    def _ensure_admin(space: Session, freelancer: EngagementFreelancer, email: str) -> bool:
        """The space's admin with the freelancer's address, created when the space has
        no user with it, the way `TenantService.provision` creates it: as
        `Actor.system()`, with no password, entering by the welcome's link. `True`
        when it was created now. A space `provision` finished always has it; one whose
        provisioning stopped after the registry row does not, and without it the deal
        would have no owner and the hub would mail a link nobody can enter."""
        if UserRepository(space).get_by_email(email) is not None:
            return False
        UserService(space).create(
            UserCreate(email=email, password=None, nome=full_name(freelancer), ruolo="admin"),
            Actor.system(),
        )
        return True

    @contextmanager
    def _space_session(self, tenant: Tenant) -> Iterator[Session]:
        """A session on the space's own database for one step. A database error in it
        leaves as `SpaceUnreachable`, so the router's `503` can say which space."""
        tenant_id = tenant.id
        engine = create_engine(
            tenant_database_url(self.settings, tenant_database_name(tenant.slug)), future=True
        )
        try:
            with session_factory(engine)() as space:
                yield space
        except SQLAlchemyError as exc:
            raise SpaceUnreachable(tenant_id) from exc
        finally:
            engine.dispose()

    def _customer(self, space: Session, data: EngagementUpsert) -> UUID:
        """Step 4: the customer «rebase», by VAT number when the body carries one, then
        by its exact name among the live customers; created when neither finds it. The
        name is tried after a VAT number that finds nothing too: a space whose customer
        was created before the signer data had a VAT number keeps that one customer,
        rather than gain a second the day the number arrives. An existing one is left as
        it is: its fields are the freelancer's to edit."""
        rebase = data.rebase
        customers = CustomerRepository(space)
        found = (
            customers.match_by_fiscal_id(partita_iva=rebase.partita_iva, codice_fiscale=None)
            if rebase.partita_iva is not None
            else None
        )
        if found is None:
            found = customers.find_by_name(rebase.ragione_sociale)
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
