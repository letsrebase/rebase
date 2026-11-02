"""The timesheet: one set of figures, two formats.

Two formats for the same data is not redundancy -- they are two recipients with two
needs. The PDF is attached to the invoice; the XLSX gets filtered by whoever has to
check it (§2.1). Both read the same `variables_for`, so the two documents can never
disagree about a total.
"""

import re
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.customers.schemas import CustomerRead
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.documents.schemas import DocumentFromTemplate, DocumentRead
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.money import sum_hours
from pigrocrm.core.storage import DocumentStorage
from pigrocrm.core.templates.service import TemplateService
from pigrocrm.core.timetracking.locks import period_label
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.repository import TimeEntryRepository
from pigrocrm.core.timetracking.xlsx import build_time_report_xlsx

ENTITY = "time_report"

TIME_REPORT_TEMPLATE_NOME = "Rapporto ore"

# Declared for the compilation form (slice 2 §4.3). `voci` is deliberately not
# declared: it is supplied by the service on every render, never typed by a human, and
# `render_template` accepts undeclared supplied keys -- `{{#each voci}}` resolves from
# `values`. Declaring it would put a list-typed variable on a form nobody fills in.
TIME_REPORT_TEMPLATE_VARIABLES: tuple[dict[str, Any], ...] = (
    {"nome": "periodo", "etichetta": "Periodo", "tipo": "text", "obbligatoria": True},
    {"nome": "totale_ore", "etichetta": "Totale ore", "tipo": "number", "obbligatoria": True},
    {"nome": "numero_voci", "etichetta": "Numero voci", "tipo": "number", "obbligatoria": True},
)

# `re.fullmatch` is what this is used with -- never `re.match` with `$`, because `$`
# matches before a trailing newline and `"2026-03\n"` would slip through. The month
# alternation, rather than `\d{2}`, is what makes `2026-13` and `2026-00` unrepresentable
# instead of merely rejected later by `date()`.
PERIOD_KEY_RE = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")


def parse_period(mese: str) -> tuple[int, int]:
    """`"2026-03"` -> `(2026, 3)`. The `AAAA-MM` key carried over from the previous system: the
    monthly cut is what a client expects next to an invoice, and it is what gets agreed
    on."""
    match = PERIOD_KEY_RE.fullmatch(mese)
    if match is None:
        raise ValidationFailed(
            ENTITY, "mese", "periodo non valido", expected="un periodo nella forma AAAA-MM"
        )
    return int(match.group(1)), int(match.group(2))


def _italian_date(giorno: date) -> str:
    """`dd/mm/yyyy`, formatted from the `Date`'s own parts.

    Never through an instant and never through `toISOString()`-shaped arithmetic:
    The previous system's `formatIsoDate` projected a timestamp to UTC, so an hour logged at 23:30
    CEST on 31 March was stored -- and printed -- as 1 April, landing in the wrong
    monthly export, which is the file attached to an invoice.
    """
    return f"{giorno.day:02d}/{giorno.month:02d}/{giorno.year}"


def report_variables(
    entries: list[TimeEntry], *, anno: int, mese: int, deal: Deal, customer: Customer | None
) -> dict[str, Any]:
    """Everything both formats need, computed once from already-fetched rows.

    Every figure arrives finished: `totale_ore` is already summed with `sum_hours`, so
    neither the template nor the workbook adds anything. The previous system accumulated hours as
    binary floats (`sum + entry.hours`) and printed a total that was a binary sum
    rounded at the end.

    `descrizione` is handed over **raw** -- multi-line, unescaped. `escape_for` (slice
    2) prepares it at render, once, for the context it lands in; the XLSX passes it
    through no escaper at all, because a cell value is not markup.

    A pure function of already-fetched rows -- no session, no repository -- so
    `TimeReportService.variables_for` is the only place that does I/O and this can be
    exercised (and, in Task 4A-15, reused for the XLSX) with plain objects.
    """
    return {
        "periodo": period_label(anno, mese),
        # `today_local()`, never a bare `date.today()`: see `db/clock.py`. This is the
        # compilation date printed at the head of the timesheet that goes to the client
        # next to the invoice. On the API image, which runs in UTC, every report
        # produced between midnight and 01:00 CET carries the previous day -- and one
        # produced just after midnight on 1 January carries the previous *year*, on the
        # document that justifies the hours billed for the year that just closed.
        "oggi": _italian_date(today_local()),
        "deal": {"nome": deal.nome},
        "cliente": (
            CustomerRead.model_validate(customer).model_dump(mode="json") if customer else {}
        ),
        "voci": [
            {
                "data": _italian_date(entry.data),
                # `str(Decimal)` keeps the stored scale exactly ("2.50", not "2.5"):
                # the column the client reads must show two places, and formatting a
                # float here would be the drift `Numeric` exists to avoid.
                "ore": str(entry.ore),
                "descrizione": entry.descrizione,
            }
            for entry in entries
        ],
        "totale_ore": str(sum_hours([entry.ore for entry in entries])),
        "numero_voci": str(len(entries)),
        # Kept as native types for the workbook, which needs real dates and real
        # numbers rather than the display strings above (Task 4A-15).
        "_rows": [(entry.data, entry.ore, entry.descrizione) for entry in entries],
        "_anno": anno,
        "_mese": mese,
    }


class TimeReportService:
    def __init__(self, session: Session, storage: DocumentStorage | None) -> None:
        self.session = session
        self.storage = storage
        self.entries = TimeEntryRepository(session)
        self.deals = DealRepository(session)
        self.customers = CustomerRepository(session)
        self.templates = TemplateService(session)

    def variables_for(self, deal_id: UUID, mese: str, actor: Actor) -> dict[str, Any]:
        """Fetches the deal, the customer and the month's entries, then delegates the
        actual figure-building to `report_variables` -- see that function's docstring
        for why the two are split."""
        anno, numero_mese = parse_period(mese)
        deal = self.deals.get(deal_id)
        if deal is None:
            raise NotFound("deal", deal_id)
        customer = self.customers.get(deal.customer_id)
        rows = self.entries.for_month(deal_id, anno, numero_mese)
        return report_variables(rows, anno=anno, mese=numero_mese, deal=deal, customer=customer)

    def render_pdf(self, deal_id: UUID, mese: str, actor: Actor) -> DocumentRead:
        """Archived as a `document` of type `rapporto_ore` on the deal, through the
        slice 2 pipeline unchanged -- so it gets the versioning, the hash, the pluggable
        storage and the timeline that already work, and a regenerated report is a new
        version rather than a silent overwrite."""
        variables = self.variables_for(deal_id, mese, actor)
        template = self.templates.repo.get_by_nome(TIME_REPORT_TEMPLATE_NOME)
        if template is None:
            raise ValidationFailed(
                ENTITY,
                "template",
                f"il template «{TIME_REPORT_TEMPLATE_NOME}» non esiste",
                expected="esegui `pigrocrm seed-templates`",
            )
        if self.storage is None:
            raise ValidationFailed(
                ENTITY, "storage", "nessun backend di storage configurato", expected="uno storage"
            )
        public = {k: v for k, v in variables.items() if not k.startswith("_")}
        return DocumentService(self.session, self.storage).create_from_template(
            DocumentFromTemplate(
                template_id=template.id,
                titolo=f"Rapporto ore {variables['periodo']}",
                customer_id=None,
                deal_id=deal_id,
                variabili=public,
            ),
            actor,
        )

    def build_xlsx(self, deal_id: UUID, mese: str, actor: Actor) -> tuple[str, bytes]:
        """Returns `(filename, bytes)`.

        Not archived as a `document`, unlike the PDF, and that asymmetry is deliberate:
        the PDF is the artefact attached to an invoice and therefore has to be
        reproducible byte for byte a year later, which is what `document_versions` and
        its hash exist for. The XLSX is a working copy somebody filters -- it is
        regenerated from the same `variables_for` on every request, so it can never
        drift from the PDF, and storing versions of it would archive scratch paper.
        """
        variables = self.variables_for(deal_id, mese, actor)
        content = build_time_report_xlsx(
            cliente=str(variables["cliente"].get("ragione_sociale", "")),
            offerta=str(variables["deal"]["nome"]),
            periodo=str(variables["periodo"]),
            rows=variables["_rows"],
        )
        return f"rapporto-ore-{variables['_anno']}-{variables['_mese']:02d}.xlsx", content
