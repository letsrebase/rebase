from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.emitter.models import Azienda


class AziendaRepository:
    """A repository never commits (project rule): every method here reads or flushes,
    and the surrounding service method is the one transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, *, only_active: bool = True) -> list[Azienda]:
        """The default first, then by short name, so every list and every selector
        shows the same order without sorting again."""
        query = select(Azienda)
        if only_active:
            query = query.where(Azienda.attiva.is_(True))
        query = query.order_by(Azienda.predefinita.desc(), Azienda.nome, Azienda.created_at)
        return list(self.session.execute(query).scalars().all())

    def get(self, azienda_id: UUID) -> Azienda | None:
        return self.session.get(Azienda, azienda_id)

    def default(self) -> Azienda | None:
        """The one row with `predefinita`, or `None` on a space that has no azienda
        yet (between signup and `ensure_defaults`). With one azienda this is the row
        every consumer that used to ask for «the» emitter now gets."""
        return (
            self.session.execute(select(Azienda).where(Azienda.predefinita.is_(True)))
            .scalars()
            .first()
        )

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(Azienda)) or 0

    def add(self, azienda: Azienda) -> Azienda:
        # Flushes: this is what actually sends the `INSERT` to Postgres, where the
        # partial unique indexes (one default, one P.IVA, one codice fiscale) are the
        # only things that can refuse it, and what populates `azienda.id` (a flush-time
        # column default, see `PrimaryKeyMixin`) before the service needs it for
        # `ActivityService.record`. Callers invoke this from inside their own
        # `try/except IntegrityError`: see `AziendaService.upsert_default`.
        self.session.add(azienda)
        self.session.flush()
        return azienda
