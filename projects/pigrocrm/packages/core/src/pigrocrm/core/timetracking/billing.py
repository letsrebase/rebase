"""Turning logged hours into the lines of a draft invoice.

The one writer that used to sit in `AnalyticsService`, a read-only service. It builds an
`InvoiceCreate` and hands it to `InvoiceService`, which stays the sole owner of numbering,
fiscal validation, rounding and freezing (slice 3 §3, §6, §9), through
`invoices.assembly.assemble_draft`: the draft, the hour-to-line links and the deal's
activity are one transaction.
"""

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import BindTimeRequest
from pigrocrm.core.config import get_settings
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.assembly import assemble_draft
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn, InvoiceRead
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.money import sum_hours
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.storage.factory import storage_from_settings
from pigrocrm.core.timetracking.locks import period_label
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.service import billed_entry_ids

type GroupKey = tuple[Decimal, tuple[int, int] | None]


def _group_order(key: GroupKey) -> tuple[int, int, Decimal]:
    """Month first, rate within it -- the order the lines are printed in.

    A `sorted(..., key=...)` on the raw key would compare `tuple[int, int] | None`
    against `tuple[int, int]` and fail; flattening it to `(0, 0)` puts the un-dated
    groups of `raggruppa_per_mese = False` first, which is where they belong when they
    are also the only ones.
    """
    tariffa, mese = key
    anno, numero_mese = mese if mese is not None else (0, 0)
    return (anno, numero_mese, tariffa)


class TimeBilling:
    def __init__(self, session: Session, storage: DocumentStorage | None = None) -> None:
        """`storage` is optional and resolved from settings only when a draft is bound:
        `InvoiceService`'s constructor requires a backend because the artefacts a *later*
        emission produces are written through it, not because creating a draft writes
        anything. Callers that hold one pass it; nothing else constructs a backend."""
        self.session = session
        self.storage = storage
        self.deals = DealRepository(session)

    def bind_to_invoice(self, deal_id: UUID, data: BindTimeRequest, actor: Actor) -> InvoiceRead:
        """Turns selected hours into the lines of a **draft** invoice, and writes the
        link back onto each of them.

        **Issues nothing and computes no total.** `InvoiceService` stays the sole owner
        of numbering, fiscal validation, rounding and freezing. `admin`, and **no MCP
        tool** (§11's exclusion list): choosing *which* hours to invoice is a commercial
        decision, and binding is the step that determines their freezing at issue.

        Which reading of "already invoiced" applies, since task 4B-3 deliberately left
        two that differ: the double-invoicing guard below uses the **invoice-state** one,
        through `billed_entry_ids`, which asks whether the line belongs to an *issued*
        document. The link-based reading (`invoice_line_id IS NOT NULL`) would refuse to
        rebind an hour sitting on a draft nobody ever issued, and that draft may be a
        mistake somebody is trying to redo.

        The link is written **when the draft line is born**, not at issue: the FK is
        `ON DELETE SET NULL`, so while the invoice is a draft the hours stay modifiable
        and slice 3's wholesale line replacement unbinds and rebinds them without
        orphans; the moment `issue()` commits, the bound hours are frozen without
        `issue()` having had to know they exist.

        **The rounding disagreement, and which figure wins.** A line's `prezzo_totale`
        is `ROUND(Σ ore × tariffa, 2)`, while the timesheet prints
        `Σ ROUND(ore × tariffa, 2)`. The two differ by cents and §6.2 states this as its
        one exception rather than reconciling it: hours × rate is an *estimate*
        (`valore_maturato`), and it stops being consulted the moment an invoice exists.
        From then on the revenue is the invoice's own `imponibile`.
        """
        actor.require_admin("bind_time_to_invoice")
        deal = self.deals.get(deal_id)
        if deal is None:
            raise NotFound("deal", deal_id)

        rows = self._load_entries(deal_id, data)
        priced = self._priced(rows)
        self._refuse_billed(rows)

        # One line per `(tariffa, mese)`. Not one per entry: a forty-line invoice is
        # unreadable for the client, and the detail already has its place -- the
        # timesheet attached to it (§10.2). `raggruppa_per_mese = False` collapses to one
        # line per rate, which is a presentation choice the caller makes and not a rule.
        groups: dict[GroupKey, list[TimeEntry]] = defaultdict(list)
        for tariffa, entry in priced:
            mese = (entry.data.year, entry.data.month) if data.raggruppa_per_mese else None
            groups[(tariffa, mese)].append(entry)
        ordered = sorted(groups.items(), key=lambda item: _group_order(item[0]))

        righe: list[InvoiceLineIn] = []
        for (tariffa, mese), members in ordered:
            ore = sum_hours([m.ore for m in members])
            etichetta = f" {period_label(*mese)}" if mese is not None else ""
            righe.append(
                InvoiceLineIn(
                    # Names the month and the hours, which is what makes a six-line
                    # invoice legible next to a timesheet the client can check it
                    # against. A plain hyphen, never an em dash: `Descrizione` goes into
                    # the FatturaPA XML, whose FPR12 profile admits only basic-latin and
                    # Latin-1 code points, and U+2014 is in neither.
                    descrizione=f"Attività{etichetta} - {ore} ore",
                    quantita=ore,
                    unita_misura="ore",
                    prezzo_unitario=tariffa,
                    # `aliquota_iva` deliberately omitted: `None` means "the regime's
                    # answer", and `natura`/`riferimento_normativo` are not on this
                    # schema at all, so a caller cannot put the table constraint
                    # `(aliquota_iva = 0) = (natura IS NOT NULL)` within reach of a
                    # request body. The fiscal profile is `InvoiceService`'s to read.
                )
            )

        def bind(line_ids: list[UUID]) -> None:
            # The line ids arrive in `numero_linea` order, which `_computed_lines`
            # assigns from the order `righe` was built in -- so the group-to-line mapping
            # is positional and deterministic, rather than matched on a description
            # string that two groups could share.
            for line_id, (_, members) in zip(line_ids, ordered, strict=True):
                for entry in members:
                    entry.invoice_line_id = line_id
            invoice_id = self.session.execute(
                select(InvoiceLine.invoice_id).where(InvoiceLine.id == line_ids[0])
            ).scalar_one()
            # One activity on the deal, not one per entry: binding is a single
            # commercial act over a selection, unlike `recalculate_rates`, which changes
            # each row's meaning individually and therefore records per row.
            ActivityService(self.session).record(
                "deal",
                deal_id,
                "time_bound_to_invoice",
                actor,
                {"invoice_id": str(invoice_id), "voci": len(rows)},
            )

        storage = (
            self.storage if self.storage is not None else storage_from_settings(get_settings())
        )
        return assemble_draft(
            InvoiceService(self.session, storage),
            InvoiceCreate(
                customer_id=deal.customer_id,
                deal_id=deal_id,
                tipo="fattura",
                righe=righe,
            ),
            actor,
            bind=bind,
        )

    def _load_entries(self, deal_id: UUID, data: BindTimeRequest) -> list[TimeEntry]:
        """The selected entries, each checked to be this deal's, live and billable."""
        # De-duplicated, order preserved. The same id twice in one request would land in
        # one group twice and bill those hours twice on a single line -- silently, since
        # every count downstream would still agree with itself.
        rows: list[TimeEntry] = []
        for entry_id in dict.fromkeys(data.entry_ids):
            entry = self.session.get(TimeEntry, entry_id)
            # Never a silent skip: a dropped id produces a draft missing work the user
            # believed they had selected, and they find out from the client.
            if entry is None:
                raise NotFound("time_entry", entry_id)
            rows.append(entry)

        stray = [str(e.id) for e in rows if e.deal_id != deal_id or e.deleted_at is not None]
        if stray:
            raise ValidationFailed(
                "time_entry",
                "entry_ids",
                f"{len(stray)} voci non appartengono a questo deal o sono archiviate",
                expected=f"solo voci del deal {deal_id}: {', '.join(stray)}",
            )

        # A property of the work itself, decided when it was logged -- an internal
        # meeting, a rewrite nobody agreed to pay for -- so it never belongs on a draft,
        # whatever rate it happens to carry.
        internal = [str(e.id) for e in rows if not e.fatturabile]
        if internal:
            raise ValidationFailed(
                "time_entry",
                "entry_ids",
                f"{len(internal)} voci non sono fatturabili",
                expected=f"solo voci fatturabili: {', '.join(internal)}",
            )
        return rows

    @staticmethod
    def _priced(rows: list[TimeEntry]) -> list[tuple[Decimal, TimeEntry]]:
        """`(rate, entry)` for every entry, refusing the unpriced ones together."""
        priced: list[tuple[Decimal, TimeEntry]] = []
        unpriced: list[TimeEntry] = []
        for entry in rows:
            tariffa = entry.tariffa_applicata
            if tariffa is None:
                unpriced.append(entry)
            else:
                priced.append((tariffa, entry))
        if unpriced:
            # Counted in the reason and listed in `expected`, so the refusal is an
            # instruction -- "give these two entries a rate" -- and not an obstacle. An
            # invoice line with no unit price is not issuable, and inventing one here
            # would decide on the user's behalf what their work is worth.
            raise ValidationFailed(
                "time_entry",
                "entry_ids",
                f"{len(unpriced)} voci non hanno una tariffa: assegnane una prima di "
                "generare la bozza",
                expected=(
                    "una tariffa su ogni voce selezionata: "
                    f"{', '.join(str(e.id) for e in unpriced)}"
                ),
            )
        return priced

    def _refuse_billed(self, rows: list[TimeEntry]) -> None:
        """Refuse hours already on an issued invoice, naming the document when it is
        visible: "already invoiced" is only actionable if the user can go and look."""
        already = billed_entry_ids(self.session, rows)
        if not already:
            return
        frozen = [e.invoice_line_id for e in rows if e.id in already]
        named = self.session.execute(
            select(Invoice.numero, Invoice.anno)
            .join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id)
            .where(InvoiceLine.id == frozen[0])
        ).one_or_none()
        if named is None:
            # Frozen by a line this session cannot see (an invoice of another azienda,
            # REB-634): the refusal stands, and it names no document, since the
            # document is not this member's to be pointed at.
            raise Conflict(
                "time_entry",
                f"{len(already)} voci sono già su una fattura emessa di un'azienda "
                "che non vedi: non si fattura due volte lo stesso lavoro",
                voci=len(already),
            )
        numero, anno = named
        raise Conflict(
            "time_entry",
            f"{len(already)} voci sono già su una fattura emessa: non si fattura "
            "due volte lo stesso lavoro",
            voci=len(already),
            numero=numero,
            anno=anno,
        )
