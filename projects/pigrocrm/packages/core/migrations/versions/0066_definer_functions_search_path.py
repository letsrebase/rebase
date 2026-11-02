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
- `EXECUTE` is taken away from PUBLIC. The application role gets it back from
  `ensure_application_role` (`db/role.py`), which runs after every migration at boot and
  names the role from the application's own URL, which a migration cannot know.

The SQL bodies are not touched. The downgrade restores the previous search_path and the
default PUBLIC execute.
"""

from collections.abc import Sequence

from alembic import op

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


def downgrade() -> None:
    for function in DEFINER_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC")
        op.execute(f"ALTER FUNCTION {function} SET search_path = public")
