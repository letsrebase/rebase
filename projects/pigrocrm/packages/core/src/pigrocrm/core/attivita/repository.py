from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.attivita.schemas import ATTIVITA_SORTS, AttivitaListQuery
from pigrocrm.core.db import decode_cursor, keyset_predicate, order_by


class AttivitaRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, attivita_id: UUID, *, include_deleted: bool = False) -> Attivita | None:
        row = self.session.get(Attivita, attivita_id)
        if row is None:
            return None
        if row.deleted_at is not None and not include_deleted:
            return None
        return row

    def add(self, attivita: Attivita) -> Attivita:
        self.session.add(attivita)
        self.session.flush()
        return attivita

    def in_range(self, da: date, a: date) -> list[Attivita]:
        """Every live activity whose `scadenza` falls in `[da, a]`, earliest first.

        For the calendar (slice 10 §6), which asks by month and not by page: no cursor,
        no whitelist, and no limit -- a month of commitments is bounded by the month.
        Ordered by `(scadenza, created_at)` so a day with several is stable between two
        reads of the same month, which a grid redrawing itself needs.

        Activities with no `scadenza` are **not** here, by construction: a range cannot
        answer for a row that has no date. `senza_scadenza` below is how they are asked
        for, and the calendar shows them outside the grid.
        """
        return list(
            self.session.scalars(
                select(Attivita)
                .where(
                    Attivita.deleted_at.is_(None),
                    Attivita.scadenza.is_not(None),
                    Attivita.scadenza >= da,
                    Attivita.scadenza <= a,
                )
                .order_by(Attivita.scadenza, Attivita.created_at)
            )
        )

    def senza_scadenza(self, *, limit: int) -> list[Attivita]:
        """The open commitments with no date, newest first, capped.

        Only `aperta`: a completed activity with no date is history, and the calendar's
        «senza scadenza» section is a list of things still to do. Capped because that
        section sits under a grid and is not a page -- an installation with four hundred
        undated to-dos would otherwise send all four hundred with every month.
        """
        return list(
            self.session.scalars(
                select(Attivita)
                .where(
                    Attivita.deleted_at.is_(None),
                    Attivita.scadenza.is_(None),
                    Attivita.stato == "aperta",
                )
                .order_by(Attivita.created_at.desc(), Attivita.id.desc())
                .limit(limit)
            )
        )

    def list(self, query: AttivitaListQuery) -> list[Attivita]:
        archived = (
            Attivita.deleted_at.is_not(None) if query.archiviate else Attivita.deleted_at.is_(None)
        )
        stmt = select(Attivita).where(archived)

        if query.stato:
            stmt = stmt.where(Attivita.stato == query.stato)
        if query.assegnata_a:
            stmt = stmt.where(Attivita.assegnata_a == query.assegnata_a)
        if query.customer_id:
            stmt = stmt.where(Attivita.customer_id == query.customer_id)
        if query.person_id:
            stmt = stmt.where(Attivita.person_id == query.person_id)
        if query.deal_id:
            stmt = stmt.where(Attivita.deal_id == query.deal_id)
        if query.invoice_id:
            stmt = stmt.where(Attivita.invoice_id == query.invoice_id)
        if query.scade_entro is not None:
            # Inclusive, and it excludes the undated rows on its own -- `NULL <= date`
            # is NULL, which is not true. Stated in the schema and asserted in the
            # tests, because «a filter that silently drops rows» is the shape of defect
            # §3.2 is written to prevent.
            stmt = stmt.where(Attivita.scadenza <= query.scade_entro)
        if query.senza_scadenza is True:
            stmt = stmt.where(Attivita.scadenza.is_(None))
        elif query.senza_scadenza is False:
            stmt = stmt.where(Attivita.scadenza.is_not(None))

        spec = ATTIVITA_SORTS.resolve(query.sort)
        if query.cursor:
            value, row_id = decode_cursor(spec, query.cursor)
            stmt = stmt.where(keyset_predicate(spec, query.dir, value, row_id))

        return list(
            self.session.execute(
                stmt.order_by(*order_by(spec, query.dir)).limit(query.limit + 1)
            ).scalars()
        )
