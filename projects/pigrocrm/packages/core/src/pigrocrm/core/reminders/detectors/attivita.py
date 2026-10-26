"""`attivita_due` and `attivita_overdue` (spec 2026-10-26 §3.2).

An attività with no `scadenza` is not late and not due, ever: «a NULL is not zero days»
(`attivita/models.py`), so both detectors read only the dated ones. Only an **open** one
counts: a completed or cancelled attività is done with, and an archived one was a mistake.

Recipients are the attività' assignee, plus the owners of the deal and the customer it
hangs on (`links.py`); an attività linked to nothing and assigned to nobody goes to the
space's owner (`Directory.recipients`). The azienda it is read under is its record's, so a
scoped member is told only about what their scope covers.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Select, or_, select

from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.automations.schemas import REGOLA_COPERTA_DA_PROMEMORIA
from pigrocrm.core.mail import giorno_breve
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.links import LinkResolver

DUE = REMINDER_TYPES["attivita_due"]
OVERDUE = REMINDER_TYPES["attivita_overdue"]


def _detections(ctx: DetectorContext, rows: list[Attivita], type_key: str) -> list[Detection]:
    resolver = LinkResolver(ctx.session)
    resolver.preload(
        customers=(row.customer_id for row in rows),
        people=(row.person_id for row in rows),
        deals=(row.deal_id for row in rows),
        invoices=(row.invoice_id for row in rows),
    )
    found: list[Detection] = []
    for row in rows:
        assert row.scadenza is not None  # the query asked for dated ones only
        links = resolver.resolve(
            customer_id=row.customer_id,
            person_id=row.person_id,
            deal_id=row.deal_id,
            invoice_id=row.invoice_id,
        )
        days = (row.scadenza - ctx.today).days
        where = f" · {links.customer_name}" if links.customer_name else ""
        if type_key == "attivita_due":
            when = (
                "Scade oggi"
                if days == 0
                else "Scade domani"
                if days == 1
                else f"Scade fra {days} giorni ({giorno_breve(row.scadenza)})"
            )
            title, measure = f"Attività in scadenza: {row.titolo}", float(days)
        else:
            late = -days
            when = (
                f"Scaduta ieri ({giorno_breve(row.scadenza)})"
                if late == 1
                else f"Scaduta da {late} giorni (era per {giorno_breve(row.scadenza)})"
            )
            title, measure = f"Attività scaduta: {row.titolo}", float(late)
        found.append(
            Detection(
                type=type_key,
                subject_id=row.id,
                measure=measure,
                recipients=ctx.directory.recipients(
                    (row.assegnata_a, *links.owners), links.azienda_id
                ),
                title=title,
                detail=f"{when}{where}",
                entity="attivita",
                entity_id=row.id,
                azienda_id=links.azienda_id,
                customer_id=links.customer_id,
            )
        )
    return found


def _open_dated() -> Select[Attivita]:
    return select(Attivita).where(
        Attivita.stato == "aperta",
        Attivita.deleted_at.is_(None),
        Attivita.scadenza.is_not(None),
        # What the daily pass opened for a late invoice is the to-do of `invoice_overdue`,
        # which already raises that cause and climbs its own ladder: not announced twice.
        or_(Attivita.regola.is_(None), Attivita.regola != REGOLA_COPERTA_DA_PROMEMORIA),
    )


def detect_due(ctx: DetectorContext) -> list[Detection]:
    """Open attività falling due from today to `bound` days ahead. Today is the lowest
    measure: yesterday's is `attivita_overdue`'s."""
    horizon = ctx.today + timedelta(days=max(ctx.bound(DUE), 0))
    rows = list(
        ctx.session.scalars(
            _open_dated()
            .where(Attivita.scadenza >= ctx.today, Attivita.scadenza <= horizon)
            .order_by(Attivita.scadenza, Attivita.id)
        )
    )
    return _detections(ctx, rows, "attivita_due")


def detect_overdue(ctx: DetectorContext) -> list[Detection]:
    """Open attività past their `scadenza` by at least `bound` days, never fewer than one:
    on the day itself an attività is due, not late."""
    cutoff = ctx.today - timedelta(days=max(ctx.bound(OVERDUE), 1))
    rows = list(
        ctx.session.scalars(
            _open_dated()
            .where(Attivita.scadenza <= cutoff)
            .order_by(Attivita.scadenza, Attivita.id)
        )
    )
    return _detections(ctx, rows, "attivita_overdue")


DETECTORS = (Detector(DUE, detect_due), Detector(OVERDUE, detect_overdue))
