"""The default azienda a committed test world needs under its invoices (REB-619).

`invoices.azienda_id` is `NOT NULL` and defaults, at insert time, to the space's default
azienda (`emitter/models.py::default_azienda_id`). A test on the shared `db_session`
has one: the fixture seeds it on the test's own connection. A world that commits for
real (`session_factory(db_engine)()`, the dashboards, the digest, the concurrency
files) sees none, so it writes one first with `committed_default_azienda` and removes
it last with `remove_azienda`, after its invoices and before its commit.

Under `fakes/` for the reason `invoice_fixtures.py` gives: three test roots, no
`__init__.py`, so `conftest` is ambiguous and `fakes.azienda_fixtures` is not.

The row is inserted only when the database has no default yet, and the id returned is
the one *this call* inserted, or `None` when another file's world already holds one:
a teardown must never take a row it did not write, which is the same rule every
committed fixture in these files already follows for its customers and deals.

`predefinita=False` is for a world built while a `db_session`-style fixture is open on
the same database (the API's `client` seeds a default on the test's own connection,
uncommitted): a second default would wait on `uq_emitter_profile_predefinita` until
that transaction ends, which is never, so such a world writes a non-default azienda and
names it on every row it inserts, since the column default only knows the default.
"""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pigrocrm.core.emitter.models import Azienda

NOME = "Spazio di prova"


def committed_default_azienda(
    session: Session, *, nome: str = NOME, predefinita: bool = True
) -> UUID | None:
    """Insert the default azienda unless one exists; the id to remove at teardown."""
    if predefinita:
        existing = session.execute(
            select(Azienda.id).where(Azienda.predefinita.is_(True))
        ).scalar_one_or_none()
        if existing is not None:
            return None
    row = Azienda(nome=nome, ragione_sociale=nome, predefinita=predefinita)
    session.add(row)
    session.flush()
    return row.id


def remove_azienda(session: Session, azienda_id: UUID | None) -> None:
    """Undo `committed_default_azienda`: nothing when it inserted nothing."""
    if azienda_id is not None:
        session.execute(delete(Azienda).where(Azienda.id == azienda_id))


__all__ = ["NOME", "committed_default_azienda", "remove_azienda"]
