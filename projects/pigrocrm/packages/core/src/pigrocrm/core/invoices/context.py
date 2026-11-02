"""What every collaborator of `InvoiceService` shares: one session, the services an invoice
write talks to, and the reads and lookups several of them need.

`InvoiceService` builds one `InvoiceContext` and hands it to `InvoiceDrafting`,
`InvoiceEmission`, `RegisterGaps`, `InvoiceImporter`, `InvoiceArtifacts` and the rest, so
they all work on the same session and the same transaction.
"""

from collections.abc import Callable, Sequence
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.drive.reader import DriveReader
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fiscal.pack import PACK_NON_IT
from pigrocrm.core.fiscal.regime import RegimeStrategy, resolve_regime
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.checks import is_editable
from pigrocrm.core.invoices.constants import ENTITY
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.scadenza import scadenza_da_termini
from pigrocrm.core.invoices.schemas import InvoiceRead
from pigrocrm.core.storage.base import DocumentStorage


class InvoiceContext:
    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        settings: Settings | None = None,
        *,
        drive_reader_factory: Callable[[Actor], DriveReader] | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings or get_settings()
        self.repo = InvoiceRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)
        self.fiscal = FiscalProfileService(session)
        self.emitter = LegalEntityService(session)
        self.documents = DocumentService(session, storage, self.settings)
        # The same session as everything else here (REB-367, design §7 item 5): a
        # customer `confirm_import` creates for a brand-new counterparty lands in the
        # exact transaction the invoice write is about to commit, never a customer
        # committed on its own moments before a register failure.
        self.customers = CustomerService(session)
        # The one seam of §3.5: `None` in production, where the reader is composed from
        # the actor's own stored Drive account. A test that wants an in-memory Drive
        # injects the factory instead of seeding an account row and a sealed token.
        self.drive_reader_factory = drive_reader_factory

    # ---- rows -----------------------------------------------------------------

    def require(self, invoice_id: UUID) -> Invoice:
        invoice = self.repo.get(invoice_id)
        if invoice is None:
            raise NotFound(ENTITY, invoice_id)
        return invoice

    def require_locked(self, invoice_id: UUID) -> Invoice:
        """`require`, with the row locked for the rest of the transaction and re-read, so
        the state check that follows sees what a concurrent writer committed.

        Every writer that decides on the row's state takes this before the check
        (REB-885, PCORE-A-03): without it `update` could write a frozen document that
        `issue` committed a moment before, and `annul` and `mark_transmitted_externally`
        could both pass their checks. Lock order is counter, then row: only `issue` takes
        both, in that order, so no two transactions wait on each other.
        """
        invoice = self.repo.get_for_update(invoice_id)
        if invoice is None:
            raise NotFound(ENTITY, invoice_id)
        return invoice

    def check_owner(self, customer_id: UUID, deal_id: UUID | None) -> Customer:
        """A syntactically valid but unknown UUID becomes this project's own `NotFound`
        instead of a raw `ForeignKeyViolation` reaching the caller from `flush()`. The
        nullable `deal_id` is checked too whenever a value is supplied -- skipping a
        nullable FK is the defect `deals.owner_id` shipped with.

        Returns the customer, because the regime needs its country to pick the `Natura`
        of every line (ORB-32) and the row has just been read anyway."""
        customer = self.session.get(Customer, customer_id)
        if customer is None:
            raise NotFound("customer", customer_id)
        if deal_id is None:
            return customer
        deal = self.session.get(Deal, deal_id)
        if deal is None:
            raise NotFound("deal", deal_id)
        if deal.customer_id != customer_id:
            raise ValidationFailed(
                ENTITY,
                "deal_id",
                "il deal appartiene a un altro cliente",
                expected=f"un deal del cliente {customer_id}",
            )
        return customer

    # ---- fiscal and azienda ---------------------------------------------------

    def regime(self, azienda_id: UUID | None = None) -> tuple[RegimeStrategy, FiscalSnapshot]:
        """The strategy and the parameters of one azienda, read together so a caller
        cannot pair a profile with the wrong strategy; `None` is the default azienda
        (REB-619). Raises `NotFound("fiscal_profile", ...)` when nothing is configured,
        which tells the user which screen to go to."""
        profile = self.fiscal.snapshot(azienda_id)
        return resolve_regime(profile.codice_regime, profile.pack_id), profile

    def azienda_of_parent(self, customer: Customer, deal_id: UUID | None) -> UUID:
        """The azienda a new document is born under (REB-623, spec §1.7): the deal's
        when it hangs on one, the customer's otherwise. Copied now and kept, so a
        customer moved later leaves this document where it was issued. Refused when
        that azienda is deactivated (spec §3): a proforma born on it could never issue."""
        if deal_id is not None:
            deal = self.session.get(Deal, deal_id)
            if deal is None:  # pragma: no cover - `check_owner` has already answered NotFound
                raise NotFound("deal", deal_id)
            return self.emitter.inherited(deal.azienda_id, ENTITY)
        return self.emitter.inherited(customer.azienda_id, ENTITY)

    def writing_azienda(self, azienda_id: UUID | None) -> LegalEntity:
        """The azienda a register write goes on: the one named, the default when none,
        and never an inactive one (REB-619). A read may still answer for a deactivated
        azienda, since its history stays readable; a number consumed or a gap declared
        on it would be a register nobody can see moving."""
        azienda = self.emitter.resolve(azienda_id)
        if not azienda.attiva:
            raise Conflict(
                ENTITY,
                "l'azienda non e' attiva: riattivala prima di scrivere sul suo registro",
                azienda_id=str(azienda.id),
            )
        return azienda

    @staticmethod
    def issues_abroad(profile: FiscalSnapshot) -> bool:
        """A foreign azienda (REB-619, spec §1.3): its documents are never FatturaPA,
        so the SdI's own checks on parties and text do not apply to them and no XML is
        ever produced; the PDF is the document."""
        return profile.pack_id == PACK_NON_IT

    # ---- due dates ------------------------------------------------------------

    def due_date_from_terms(
        self, data_emissione: date, customer_id: UUID, profile_days: int
    ) -> date:
        """The due date the customer's terms give from `data_emissione` (REB-326). The
        customer's days when it has them, the profile's otherwise; the end-of-month slide
        is the customer's alone. One arithmetic, `scadenza_da_termini`, for every caller."""
        days, month_end = self.repo.payment_terms({customer_id}).get(customer_id, (None, False))
        return scadenza_da_termini(
            data_emissione, profile_days if days is None else days, fine_mese=month_end
        )

    def scadenza_prevista(self, invoice: Invoice) -> date | None:
        """`scadenza_prevista` of an editable document (REB-326): the date `issue` would
        print if pressed today, so the person sees a wrong term before the XML carries
        it. The row's own `data_scadenza` when written by hand, the customer's terms
        otherwise. An issued or consumed row gets nothing: its date is a fact, not a
        forecast. A space with no fiscal profile yet has no default days, and only a
        customer with days of its own gets a forecast there -- the profile's absence is
        reported by `issue`, not here.
        """
        if not is_editable(invoice):
            return None
        if invoice.data_scadenza is not None:
            return invoice.data_scadenza
        days, month_end = self.repo.payment_terms({invoice.customer_id}).get(
            invoice.customer_id, (None, False)
        )
        if days is None:
            try:
                days = self.fiscal.snapshot().giorni_scadenza
            except NotFound:
                return None
        return scadenza_da_termini(oggi_in_italia(), days, fine_mese=month_end)

    # ---- reads ----------------------------------------------------------------

    def read(self, invoice: Invoice) -> InvoiceRead:
        """The one place an `Invoice` becomes an `InvoiceRead`, so every path -- a
        mutation's answer, `get`, `list` -- hands back the same shape, the customer's name
        included. Mirrors `DealService._read`."""
        return self.reads([invoice])[0]

    def reads(self, invoices: Sequence[Invoice]) -> list[InvoiceRead]:
        """The batched form, and the reason `customer_ragione_sociale` is resolved here
        and not inside `InvoiceRead` itself: one lookup for the whole page
        (`InvoiceRepository.customer_names`), so a page of invoices costs two queries
        rather than one per row.

        `model_copy` and not a second `model_validate`: the name is not an attribute of
        `Invoice` at all, so there is nothing on the ORM object for `from_attributes` to
        read -- and the value comes from a `String` column, already the right type.
        """
        names = self.repo.customer_names({invoice.customer_id for invoice in invoices})
        return [
            InvoiceRead.model_validate(invoice).model_copy(
                update={"customer_ragione_sociale": names.get(invoice.customer_id)}
            )
            for invoice in invoices
        ]

    def get(self, invoice_id: UUID) -> InvoiceRead:
        """One row, with its forecast: `scadenza_prevista` is filled here and not in
        `reads`, because only the detail page shows it and a list page's query budget
        (two statements, `test_the_customer_name_costs_one_query_for_the_whole_page`) is
        not spent on a date nothing on that page prints."""
        invoice = self.require(invoice_id)
        return self.read(invoice).model_copy(
            update={"scadenza_prevista": self.scadenza_prevista(invoice)}
        )
