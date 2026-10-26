"""Composition, and the three week helpers. Nothing here invents a figure.

Spec 2026-09-16 §3.2: `build` calls services and repositories that already own their
numbers and copies the answers into `WeeklyDigest`. The only sums in the whole report are
`InvoiceRepository`'s own -- `sum_emesse_in_periodo` twice with two windows for the two
months -- and they are that repository's, not this module's. The one arithmetic left here
is `giorni_di_ritardo`, `(today - scadenza).days`, which is not a figure but the same
subtraction `SollecitiService` prints on the same invoices. The discipline is the one
`core/dashboard/` is held to by an AST scan, and it matters more here than there: a mail
is read once, quickly, by somebody who will not go and check, so a figure that disagrees
with the screen is a figure that costs trust and is never noticed again.

**The order of `build` is load-bearing at exactly one point.**
`DashboardService.get_operational_dashboard` opens a read-only `REPEATABLE READ` snapshot
and *refuses* a session that already has a transaction in progress, so it has to be the
first read this method makes. Everything after it then runs inside that snapshot, which is
the property the report wants anyway: seven sections read at seven instants is how a mail
comes to say «2 fatture emesse» above a list of three.

The rest of the order is only the order of §3.1, so that reading this method and reading
the mail are the same walk.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.attivita.repository import AttivitaRepository
from pigrocrm.core.attivita.schemas import GIORNI_PROSSIMI
from pigrocrm.core.config import Settings
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import current_week, month_bounds, today_local
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.digest.schemas import (
    DigestAttivita,
    DigestAttivitaRiga,
    DigestDealMove,
    DigestInvoice,
    DigestOffer,
    DigestSignal,
    DigestStage,
    WeeklyDigest,
)
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import numero_completo
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.timetracking.repository import TimeEntryRepository

# §3.1: «quelle in scadenza nei prossimi sette giorni», counted from the day *after* the
# closed week -- this constant is only the width of that window.
#
# It does not, on its own, keep the two halves of «Da incassare» apart. `scadute` is
# «due on or before today» and this window opens on `a + 1`, which for the Monday the
# cron runs *is* today: an invoice due that day belongs to both, and for a week
# recomputed with `--data` the whole window has since gone by and every invoice in it is
# overdue. So `build` subtracts the `scadute` by id below, and the overdue half wins --
# it carries the days of delay, which is the fact somebody acts on.
IN_SCADENZA_GIORNI = 7

# How many commitment lines each list of «Da fare» carries; the rest is a count.
_ATTIVITA_MOSTRATE = 10

# How many stage changes are read *inside* the week's window. Two hundred is far more than
# a freelancer's week and still one bounded query; a space that moved more than that in a
# week has a report whose «deal mossi» list is truncated, which is better than a mail that
# takes a table scan to build. Since the window is in the query, the rows dropped by this
# limit are the week's own oldest ones, never a newer week's.
_MOVIMENTI_LETTI = 200

# The same ceiling the commercial dashboard puts on the same list.
_OFFERTE_MOSTRATE = 20

# `activities.stage_changed` writes `{"from": <nome>, "to": <nome>}` -- the stage *names*,
# not their ids (`deals/service.py::move_stage`). So the destination needs no second query
# to resolve, and a stage renamed after the fact keeps the name it had when the deal moved,
# which is the honest thing for a record of what happened.
_STAGE_CHANGED = "stage_changed"
_DESTINAZIONE = "to"
_ENTITA_DEAL = "deal"


def iso_week(day: date) -> str:
    """`2026-W38`: the ISO week the day belongs to.

    The report's identity (§3.3): `digests.settimana` is unique on this string, so it is
    what makes a cron that runs twice send once.

    The year comes from `isocalendar()` and never from `day.year`. 31 December 2026 is in
    ISO week 53 *of 2026* and 1 January 2027 is in the same one -- `f"{day.year}-W..."`
    would file those two days under two different identities and send the same week twice
    across every new year.

    Two digits on the week, always: `2026-W7` and `2026-W38` do not sort against each
    other as strings, and this value is read back out of a database column.
    """
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_containing(day: date) -> tuple[date, date]:
    """Monday to Sunday, both ends included, containing `day`.

    `current_week`'s rule applied to a day the caller names rather than to today: the
    command's `--data YYYY-MM-DD` (§3.4), which is how a week is recomputed for a test or
    a resend. Monday-first and inclusive at both ends for the reasons `db/clock.py` writes
    down once; two week conventions in one product is how a day gets counted twice.
    """
    monday = day - timedelta(days=day.isoweekday() - 1)
    return monday, monday + timedelta(days=6)


def previous_week(settings: Settings) -> tuple[date, date]:
    """The week that has just closed, in the emitter's zone.

    `current_week(settings)` shifted back seven days, and not `week_containing(today - 7)`:
    they agree, and going through the clock's own function means there is one definition of
    "which week is it" and this is a shift of it.

    `settings` is required rather than optional, unlike on `current_week`: the digest runs
    per space from a cron, and each space's settings are resolved by the caller
    (`space_base_settings`). A default that quietly read the platform's `get_settings()`
    would be right in every test and wrong for exactly the installation whose timezone
    differs.
    """
    monday, sunday = current_week(settings)
    return monday - timedelta(days=7), sunday - timedelta(days=7)


class DigestService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def build(self, actor: Actor, settimana: tuple[date, date]) -> WeeklyDigest:
        """The week as one object (§3.2).

        `settimana` is passed in and never computed here, so the command, a resend and a
        test all build the same report for the same seven days. `actor` is the space's
        owner, resolved by the caller (§3.3), and the operational dashboard is the one read
        that asks for it; nothing below needs more than a read right.

        The dashboard call is first because it opens the snapshot the rest of the method
        reads inside -- see the module docstring. Everything after it is a copy.
        """
        da, a = settimana

        # 1. The snapshot, and with it the backlog and the signals (§3.1 sections 2, 7).
        operativa = DashboardService(self.session).get_operational_dashboard(actor)

        # 2. The register: the four lists and the two month totals.
        #
        #    `scadute` is «every unpaid, issued invoice due on or before today», read from
        #    the register itself. It was `SollecitiService.candidates` until the branch
        #    review: that list answers «which invoices may I legitimately chase», so it
        #    drops anything inside the seven-day grace period, anything chased in the last
        #    few days and anything already at the reminder ceiling -- and the invoices it
        #    dropped are the ones that fell due *inside the reported week*, which is the
        #    money the report exists to name.
        oggi = today_local(self.settings)
        invoices = InvoiceRepository(self.session)
        scadute_rows = invoices.list_scadute(oggi)
        emesse_rows = invoices.list_emesse_in_periodo(da, a)
        incassate_rows = invoices.list_incassate_in_periodo(da, a)
        in_scadenza_rows = invoices.list_in_scadenza(
            a + timedelta(days=1), a + timedelta(days=IN_SCADENZA_GIORNI)
        )
        # One invoice, one line. The two windows meet on the day the cron runs -- `a + 1`
        # is today -- and overlap entirely for a week recomputed with `--data`, whose
        # «in scadenza» window has since gone by. The overdue half wins: it is the same
        # row, and it carries the days of delay instead of «in scadenza».
        scadute_ids = {row.id for row in scadute_rows}
        in_scadenza_rows = [row for row in in_scadenza_rows if row.id not in scadute_ids]
        # One lookup for all four lists, after the rows are chosen: a label query per row
        # is the N+1 `customer_names` exists to prevent.
        nomi = invoices.customer_names(
            {
                row.customer_id
                for row in (*scadute_rows, *emesse_rows, *incassate_rows, *in_scadenza_rows)
            }
        )
        # The lateness expression is `SollecitiService.candidates`' own, copied rather than
        # imported so that a list of debts and a list of chaseable invoices stay free to
        # diverge: `(today - scadenza).days`, over the clock's day in the emitter's zone.
        # The `is not None` guard is `list_scadute`'s own `WHERE` restated -- the column is
        # nullable and `mypy` reads it as such.
        scadute = [
            self._riga(row, nomi, scadenza, giorni_di_ritardo=(oggi - scadenza).days)
            for row in scadute_rows
            if (scadenza := row.data_scadenza) is not None
        ]
        mese_da, mese_a = month_bounds(a.year, a.month)
        # The last day of the previous month is the day before this one's first, which is
        # the only way to name it without a calendar branch on January.
        precedente = mese_da - timedelta(days=1)
        precedente_da, precedente_a = month_bounds(precedente.year, precedente.month)

        # 4. Le ore, for the closed week -- not the dashboard's `settimana`, which is the
        #    *current* one and would report the days since the report's own subject ended.
        entries = TimeEntryRepository(self.session)
        settimana_ore = entries.week_hours(da, a)
        # Absent, not zero: §3.1 drops the section entirely rather than printing a flat
        # line, because "no hours" and "seven days of nothing" read differently.
        ore = settimana_ore if settimana_ore.ore_totali > 0 else None

        # 5. «Da emettere»: the count of won deals with unbilled hours, and the backlog.
        #    The same `COUNT` the dashboard's `vinto_da_fatturare` signal ran above, asked
        #    again rather than fished out of `operativa.segnali` by its code: the report
        #    would then depend on a *label* staying spelled that way, and the two cannot
        #    disagree anyway -- both statements read the same `REPEATABLE READ` snapshot.
        vinti_da_fatturare = entries.count_won_deals_to_invoice()

        # 6. In pipeline: the stages, the movements, the offers.
        deals = DealRepository(self.session)
        pipeline = [
            DigestStage(
                stage_nome=row.stage_nome, numero=row.numero, valore_totale=row.valore_totale
            )
            for row in deals.pipeline_summary()
            if row.numero > 0
        ]
        deal_mossi = self._movimenti(deals, da, a)
        offerte = DocumentRepository(self.session).pending_offers(_OFFERTE_MOSTRATE)

        arretrato = operativa.arretrato
        return WeeklyDigest(
            settimana=iso_week(da),
            da=da,
            a=a,
            scadute=scadute,
            in_scadenza=[self._riga(row, nomi, row.data_scadenza) for row in in_scadenza_rows],
            vinti_da_fatturare=vinti_da_fatturare,
            ore_non_fatturate=arretrato.ore_fatturabili_non_fatturate,
            valore_maturato=arretrato.valore_maturato,
            emesse=[self._riga(row, nomi, row.data_emissione) for row in emesse_rows],
            totale_mese_corrente=invoices.sum_emesse_in_periodo(mese_da, mese_a),
            totale_mese_precedente=invoices.sum_emesse_in_periodo(precedente_da, precedente_a),
            incassate=[self._riga(row, nomi, row.data_incasso) for row in incassate_rows],
            ore=ore,
            pipeline=pipeline,
            deal_mossi=deal_mossi,
            offerte_in_attesa=[
                DigestOffer(
                    # `PendingOffer` carries its ids as strings, because the commercial
                    # dashboard serialises straight to JSON; this model keeps the `UUID`
                    # every other id in the report is, so the mail's link is built the
                    # same way for an offer as for an invoice.
                    document_id=UUID(offer.document_id),
                    titolo=offer.titolo,
                    # `PendingOffer.giorni` is `None` when the offer has no `stato_dal`,
                    # which for a *sent* offer only happens on a row migrated before that
                    # column existed. The mail has one column for the age, so it reads 0
                    # there; the honest `None` stays on the dashboard, which has room to
                    # render it as a dash.
                    giorni=offer.giorni if offer.giorni is not None else 0,
                )
                for offer in offerte
            ],
            segnali=[
                DigestSignal(
                    codice=signal.codice,
                    etichetta=signal.etichetta,
                    conteggio=signal.conteggio,
                    # Every signal has a drill-through today and
                    # `test_dashboard_operational.py` is what keeps it that way; a mail
                    # cannot print a link it does not have, so an absent one is an empty
                    # string the renderer skips rather than a crash on Monday morning.
                    collegamento=signal.collegamento or "",
                )
                for signal in operativa.segnali
                # §3.1 section 7: the section exists only when at least one fired. A mail
                # that reports three zeroes every week is a mail nobody opens.
                if signal.conteggio > 0
            ],
        )

    def attivita_di(self, user_id: UUID) -> DigestAttivita:
        """One person's week of commitments: their overdue ones and those due from today to
        seven days on (REB-726).

        Read from the same repository the Oggi panel reads, with the same windows, so the
        mail and the page cannot disagree about what is late. `oggi` is the emitter's
        day, like `scadute`'s, and the window closes at `oggi + 7` as the page's «prossimi
        sette giorni» does. Assigned to `user_id` only: a commitment nobody has taken is
        nobody's mail.
        """
        oggi = today_local(self.settings)
        repo = AttivitaRepository(self.session)

        def righe(rows: list[Attivita], *, ritardo: bool) -> list[DigestAttivitaRiga]:
            # `scadenza` is never `None` here: both windows are date predicates.
            return [
                DigestAttivitaRiga(
                    attivita_id=row.id,
                    titolo=row.titolo,
                    scadenza=row.scadenza,
                    giorni_di_ritardo=(oggi - row.scadenza).days if ritardo else None,
                )
                for row in rows
                if row.scadenza is not None
            ]

        totale_ritardo, in_ritardo = repo.aperte_nella_finestra(
            assegnata_a=user_id, limit=_ATTIVITA_MOSTRATE, prima_del=oggi
        )
        totale_settimana, in_settimana = repo.aperte_nella_finestra(
            assegnata_a=user_id,
            limit=_ATTIVITA_MOSTRATE,
            dal=oggi,
            al=oggi + timedelta(days=GIORNI_PROSSIMI),
        )
        return DigestAttivita(
            in_ritardo=righe(in_ritardo, ritardo=True),
            in_ritardo_totale=totale_ritardo,
            in_settimana=righe(in_settimana, ritardo=False),
            in_settimana_totale=totale_settimana,
        )

    def _riga(
        self,
        invoice: Invoice,
        nomi: dict[UUID, str],
        data: date | None,
        *,
        giorni_di_ritardo: int | None = None,
    ) -> DigestInvoice:
        """One register row as a mail line.

        `data` is passed in rather than picked here: which of three dates a section is
        about is the section's business, and a method that decided for itself would need to
        know which list it was building -- exactly the branch `DigestInvoice`'s single
        shape exists to avoid. `giorni_di_ritardo` is passed the same way and for the same
        reason, and only «Da incassare» passes it: zero days late is a thing an invoice can
        be, so `None` and `0` are two different statements about a row.

        The fallbacks are the columns' own nullability and not defensive padding:
        `anno`/`numero` are `NULL` on a draft and `data_emissione` on anything unissued,
        and none of the three predicates above can return such a row. They are here because
        the model's types are not `| None` and a `mypy` cast would hide the same fact
        without stating it.

        A row with no date at all is the one case that gets no fallback. `date.min` would
        put «lun 1 gen» of the year 1 in a mail and go on being sent every Monday; the
        three predicates cannot produce it, so if it ever arrives the register holds
        something this report has no honest line for, and the space is better skipped with
        the invoice's id in the log (`cli.digest` prints `saltato` and moves on).
        """
        giorno = data or invoice.data_emissione or invoice.data_scadenza
        if giorno is None:
            raise ValueError(f"invoice {invoice.id} has no date to print")
        return DigestInvoice(
            invoice_id=invoice.id,
            numero=numero_completo(invoice.anno or 0, invoice.numero or 0),
            cliente=nomi.get(invoice.customer_id, ""),
            importo=invoice.totale,
            data=giorno,
            stato=invoice.stato,
            stato_pagamento=invoice.stato_pagamento,
            # `trasmessa_esternamente_il` is the day the document was deposited with the
            # user's own intermediary: the register's whole notion of «trasmessa», and a
            # date rather than a state because `stato` already carries two state machines.
            # The mail prints the word, so it needs the fact and not the day.
            trasmessa=invoice.trasmessa_esternamente_il is not None,
            giorni_di_ritardo=giorni_di_ritardo,
        )

    def _movimenti(self, deals: DealRepository, da: date, a: date) -> list[DigestDealMove]:
        """The deals that changed stage inside the week (§3.1, section 6).

        Read by kind **and by window**, never by kind alone: `by_kind` answers the newest
        `limit` rows of the whole table, so filtering them here in Python would hand this
        week whatever is left after everything written since. The cron never notices --
        it reports the week that has just closed -- and `pigrocrm digest --data` for an
        older week silently loses `deal_mossi` as soon as two hundred stage changes have
        happened since. The period belongs in the query (`by_kind_between`).

        `occurred_at` is an *instant*, and the week is seven **days in the emitter's zone**
        (`db/clock.py`). The bounds are converted here exactly as the rows used to be: the
        week is «from midnight on the Monday to midnight on the next one, in the emitter's
        zone», which `by_kind_between` reads half-open. A deal moved at 00:30 on that
        Monday is still Sunday in UTC, and a window written in UTC days would file the
        movement in the week before the one it belongs to -- which, on a Monday-first
        calendar, is the week that has already been reported.

        One query for every title, never one per row.
        """
        zona = ZoneInfo(self.settings.timezone)
        rows = ActivityRepository(self.session).by_kind_between(
            [_STAGE_CHANGED],
            datetime.combine(da, time.min, tzinfo=zona),
            datetime.combine(a + timedelta(days=1), time.min, tzinfo=zona),
            limit=_MOVIMENTI_LETTI,
        )
        righe = [
            (row, row.occurred_at.astimezone(zona).date())
            for row in rows
            if row.entity_type == _ENTITA_DEAL
        ]
        titoli = deals.names({row.entity_id for row, _ in righe})
        return [
            DigestDealMove(
                deal_id=row.entity_id,
                titolo=titoli[row.entity_id],
                stage_nome=str(row.payload[_DESTINAZIONE]),
                quando=giorno,
            )
            for row, giorno in righe
            # A deal that no longer exists at all: the activity outlived its row, so there
            # is no title to print and the line would say nothing. Soft-deleted deals are
            # still found by `names` and still appear -- an archived deal did move.
            if row.entity_id in titoli and row.payload.get(_DESTINAZIONE)
        ]
