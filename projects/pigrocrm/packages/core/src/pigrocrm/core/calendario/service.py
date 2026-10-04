import calendar
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.repository import AttivitaRepository
from pigrocrm.core.attivita.schemas import AttivitaRead
from pigrocrm.core.calendario.repository import CalendarRepository
from pigrocrm.core.calendario.schemas import (
    MESE_RE,
    SENZA_SCADENZA_LIMIT,
    CalendarDay,
    CalendarMonth,
    DayDealHours,
    DueInvoice,
)
from pigrocrm.core.db import today_local
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.money import ZERO_HOURS, sum_hours

ENTITY = "calendario"


def month_bounds(mese: str) -> tuple[date, date]:
    """`AAAA-MM` into the first and last day of that month.

    Server-side, so the client never has to agree with the server about how long
    February is -- and validated before it is parsed, so `2026-13` reads as «non è un
    mese» instead of arriving as a `ValueError` from the standard library.

    `calendar.monthrange` and not `date(anno, mese + 1, 1) - timedelta(days=1)`: the
    second is correct until December, where `mese + 1` is 13.
    """
    if not MESE_RE.fullmatch(mese):
        raise ValidationFailed(
            ENTITY, "mese", "non è un mese valido", expected="AAAA-MM, per esempio 2026-09"
        )
    anno, numero = (int(part) for part in mese.split("-"))
    ultimo = calendar.monthrange(anno, numero)[1]
    return date(anno, numero, 1), date(anno, numero, ultimo)


class CalendarService:
    """One month of the calendar, assembled from three reads.

    In `packages/core` and not in the router, because it is the same reading the web app
    and the agent both want: putting it in an adapter would mean either the agent does
    not have it or it exists twice. Slice 10 §6.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CalendarRepository(session)
        self.attivita = AttivitaRepository(session)

    def month(
        self,
        mese: str,
        actor: Actor,
        *,
        user_id: UUID | None = None,
        azienda_id: UUID | None = None,
    ) -> CalendarMonth:
        """Every day of `mese` that has hours, activities or an invoice falling due.

        `user_id` scopes the hours to one person. The default is `actor.id`, which is
        what «my calendar» means and what every caller wants; passing `None` explicitly
        is how an admin asks for the whole space, and passing another id is how they ask
        about somebody else -- a decision this method does not second-guess, because
        reading hours is not gated anywhere else in this product either.
        """
        da, a = month_bounds(mese)
        oggi = today_local()

        # `azienda_id` narrows both reads to one azienda's deals and invoices (REB-623).
        ore = self.repo.hours_by_day_and_deal(da, a, user_id=user_id, azienda_id=azienda_id)
        etichette = self.repo.deal_labels({deal_id for _, deal_id, _ in ore})

        giorni: dict[date, CalendarDay] = {}

        def giorno(quando: date) -> CalendarDay:
            """The day's bucket, created on first mention. A `setdefault` over a dict
            rather than thirty-one pre-built objects: §6 says a day with nothing on it
            is not in the response, and pre-building them would make emptying one
            impossible to distinguish from never having filled it."""
            return giorni.setdefault(quando, CalendarDay(giorno=quando, ore=ZERO_HOURS))

        for quando, deal_id, ore_del_deal in ore:
            nome, cliente = etichette.get(deal_id, ("(deal archiviato)", None))
            bucket = giorno(quando)
            bucket.per_deal.append(
                DayDealHours(deal_id=deal_id, deal_nome=nome, cliente=cliente, ore=ore_del_deal)
            )

        # The day's total is the sum of its own rows, through `money.sum_hours`: adding
        # the per-deal figures here and separately in SQL would be two totals of the same
        # hours, which is how they begin to disagree.
        for bucket in giorni.values():
            bucket.ore = sum_hours(riga.ore for riga in bucket.per_deal)

        for row in self.attivita.in_range(da, a):
            # `in_range` filters on `scadenza IS NOT NULL`, so this is never None -- and
            # the guard is here rather than an `assert` because an assert that fires in
            # production is a traceback shown to somebody who can only read it as a
            # crash.
            scadenza_attivita = row.scadenza
            if scadenza_attivita is None:  # pragma: no cover - excluded by the query
                continue
            giorno(scadenza_attivita).attivita.append(AttivitaRead.model_validate(row))

        for invoice, cliente in self.repo.invoices_due(da, a, azienda_id=azienda_id):
            scadenza = invoice.data_scadenza
            if scadenza is None:  # pragma: no cover - excluded by the query itself
                continue
            giorno(scadenza).fatture.append(
                DueInvoice(
                    id=invoice.id,
                    numero=_numero(invoice.numero, invoice.anno),
                    cliente=cliente,
                    totale=invoice.totale,
                    data_scadenza=scadenza,
                    # `<`, like `_overdue_predicate`: due today is due today.
                    in_ritardo=scadenza < oggi,
                )
            )

        return CalendarMonth(
            mese=mese,
            da=da,
            a=a,
            oggi=oggi,
            ore_totali=sum_hours(bucket.ore for bucket in giorni.values()),
            giorni=[giorni[quando] for quando in sorted(giorni)],
            attivita_senza_scadenza=[
                AttivitaRead.model_validate(row)
                for row in self.attivita.senza_scadenza(limit=SENZA_SCADENZA_LIMIT)
            ],
        )


def _numero(numero: int | None, anno: int | None) -> str | None:
    """«3/2026», the way a person reads an invoice number, or `None` if it has none.

    Only an issued invoice reaches here and an issued one always has both halves, so the
    `None` is the type being honest rather than a case anybody expects.
    """
    if numero is None or anno is None:
        return None
    return f"{numero}/{anno}"
