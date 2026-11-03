"""The identities frozen into an invoice when it is issued."""

from typing import Protocol
from uuid import UUID

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.schemas import SNAPSHOT_VERSIONE, InvoiceSnapshot, PartySnapshot


class _Party(Protocol):
    """What a customer row and an emitter profile have in common: the fields a document
    prints about one of its two parties."""

    @property
    def ragione_sociale(self) -> str: ...
    @property
    def partita_iva(self) -> str | None: ...
    @property
    def codice_fiscale(self) -> str | None: ...
    @property
    def codice_sdi(self) -> str | None: ...
    @property
    def pec(self) -> str | None: ...
    @property
    def indirizzo(self) -> str | None: ...
    @property
    def cap(self) -> str | None: ...
    @property
    def comune(self) -> str | None: ...
    @property
    def provincia(self) -> str | None: ...
    @property
    def nazione(self) -> str: ...
    @property
    def email(self) -> str | None: ...
    @property
    def telefono(self) -> str | None: ...
    @property
    def sito_web(self) -> str | None: ...


def party_snapshot(party: _Party) -> PartySnapshot:
    """One party as the document freezes it. A missing address part is the empty string,
    the shape `PartySnapshot` stores."""
    return PartySnapshot(
        ragione_sociale=party.ragione_sociale,
        partita_iva=party.partita_iva,
        codice_fiscale=party.codice_fiscale,
        codice_sdi=party.codice_sdi,
        pec=party.pec,
        indirizzo=party.indirizzo or "",
        cap=party.cap or "",
        comune=party.comune or "",
        provincia=party.provincia or "",
        nazione=party.nazione,
        email=party.email,
        telefono=party.telefono,
        sito_web=party.sito_web,
    )


def build_snapshot(
    ctx: InvoiceContext,
    customer_id: UUID,
    profile: FiscalSnapshot,
    actor: Actor,
    azienda_id: UUID,
) -> InvoiceSnapshot:
    """The frozen identities, from an id rather than from a row, for one azienda.

    `customer_id` and not an `Invoice` on purpose: reading the issuer raises
    `NotFound("emitter_profile")` whenever the issuer's own profile is missing -- the
    exact state a fresh installation is in before §2.3 is done -- and `import_issued` has
    to be able to hit that refusal *before* it flushes anything. `issue` and the proforma
    PDF pass `invoice.customer_id`.

    The issuer is the azienda the document belongs to (REB-619).
    `emitter_profile.regime_fiscale` is deliberately not read: it is a human-readable
    caption for the PDF header, free text, and the machine value the SdI validates is
    `fiscal_profile.codice_regime`. Two columns, two jobs.
    """
    customer = ctx.session.get(Customer, customer_id)
    if customer is None:  # pragma: no cover - the FK makes this unreachable
        raise NotFound("customer", customer_id)
    return InvoiceSnapshot(
        versione=SNAPSHOT_VERSIONE,
        emittente=party_snapshot(ctx.emitter.get(actor, azienda_id)),
        cliente=party_snapshot(customer),
        fiscale=profile,
    )
