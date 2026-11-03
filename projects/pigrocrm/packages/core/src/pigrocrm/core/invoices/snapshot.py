"""The identities frozen into an invoice when it is issued."""

from typing import Protocol
from uuid import UUID

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.schemas import SNAPSHOT_VERSION, InvoiceSnapshot, PartySnapshot


class _Party(Protocol):
    """What a customer row and an emitter profile have in common: the fields a document
    prints about one of its two parties."""

    @property
    def legal_name(self) -> str: ...
    @property
    def vat_number(self) -> str | None: ...
    @property
    def tax_code(self) -> str | None: ...
    @property
    def sdi_code(self) -> str | None: ...
    @property
    def certified_email(self) -> str | None: ...
    @property
    def address(self) -> str | None: ...
    @property
    def cap(self) -> str | None: ...
    @property
    def city(self) -> str | None: ...
    @property
    def province(self) -> str | None: ...
    @property
    def country(self) -> str: ...
    @property
    def email(self) -> str | None: ...
    @property
    def phone(self) -> str | None: ...
    @property
    def website(self) -> str | None: ...


def party_snapshot(party: _Party) -> PartySnapshot:
    """One party as the document freezes it. A missing address part is the empty string,
    the shape `PartySnapshot` stores."""
    return PartySnapshot(
        legal_name=party.legal_name,
        vat_number=party.vat_number,
        tax_code=party.tax_code,
        sdi_code=party.sdi_code,
        certified_email=party.certified_email,
        address=party.address or "",
        cap=party.cap or "",
        city=party.city or "",
        province=party.province or "",
        country=party.country,
        email=party.email,
        phone=party.phone,
        website=party.website,
    )


def build_snapshot(
    ctx: InvoiceContext,
    customer_id: UUID,
    profile: FiscalSnapshot,
    actor: Actor,
    legal_entity_id: UUID,
) -> InvoiceSnapshot:
    """The frozen identities, from an id rather than from a row, for one azienda.

    `customer_id` and not an `Invoice` on purpose: reading the issuer raises
    `NotFound("legal_entities")` whenever the issuer's own profile is missing -- the
    exact state a fresh installation is in before §2.3 is done -- and `import_issued` has
    to be able to hit that refusal *before* it flushes anything. `issue` and the proforma
    PDF pass `invoice.customer_id`.

    The issuer is the azienda the document belongs to (REB-619).
    `legal_entities.tax_regime` is deliberately not read: it is a human-readable
    caption for the PDF header, free text, and the machine value the SdI validates is
    `fiscal_profile.regime_code`. Two columns, two jobs.
    """
    customer = ctx.session.get(Customer, customer_id)
    if customer is None:  # pragma: no cover - the FK makes this unreachable
        raise NotFound("customer", customer_id)
    return InvoiceSnapshot(
        version=SNAPSHOT_VERSION,
        legal_entity=party_snapshot(ctx.legal_entity.get(actor, legal_entity_id)),
        customer=party_snapshot(customer),
        tax=profile,
    )
