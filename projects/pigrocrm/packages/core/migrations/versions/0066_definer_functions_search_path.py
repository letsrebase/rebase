"""the four SECURITY DEFINER helpers: a closed search_path and no PUBLIC execute

Revision ID: 0066
Revises: 0065

PCORE-B-09. `sollecito_di_messaggio`, `tipo_fattura_del_pdf`, `deal_attivi_del_cliente`
and `cliente_archiviato` (migration 0048) run as the table owner with
`pigrocrm.aziende = '*'` and answer for any UUID, past the row-level policies. They are the
only unscoped read path of the schema and the row policies need them, so they stay; what
changes is how they are hardened:

- `search_path = pg_catalog, public, pg_temp`: the system catalog first and the session's
  temporary schema last, so nothing a session creates can stand in for a catalog object
  the body names. It was `public` alone, which searches `pg_temp` first.
- `EXECUTE` is taken away from PUBLIC, and given to the application role in the same
  transaction: the role is the user of `PIGROCRM_DATABASE_URL`, read the way the application
  reads it, so an API still serving from the previous image while this revision runs never
  meets «permission denied for function». `ensure_application_role` (`db/role.py`) grants
  the same at every boot, which covers a role created later.

The SQL bodies are not touched. The downgrade restores the previous search_path and the
default PUBLIC execute.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

from pigrocrm.core.config import get_settings

revision: str = "0066"
down_revision: str | Sequence[str] | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINER_FUNCTIONS = (
    "sollecito_di_messaggio(uuid, text)",
    "tipo_fattura_del_pdf(uuid)",
    "deal_attivi_del_cliente(uuid)",
    "cliente_archiviato(uuid)",
)


def statements() -> list[str]:
    """The DDL of `upgrade`, for the test template, which applies it after `create_all`
    like the policies of 0048 (`conftest.py`)."""
    out: list[str] = []
    for function in DEFINER_FUNCTIONS:
        out.append(f"ALTER FUNCTION {function} SET search_path = pg_catalog, public, pg_temp")
        out.append(f"REVOKE EXECUTE ON FUNCTION {function} FROM PUBLIC")
    return out


def upgrade() -> None:
    for statement in statements():
        op.execute(statement)
    connection = op.get_bind()
    role = make_url(get_settings().database_url).username
    exists = (
        role
        and connection.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
        ).first()
    )
    if role and exists:
        quoted = connection.dialect.identifier_preparer.quote(role)
        for function in DEFINER_FUNCTIONS:
            op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {quoted}")


def downgrade() -> None:
    for function in DEFINER_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC")
        op.execute(f"ALTER FUNCTION {function} SET search_path = public")
