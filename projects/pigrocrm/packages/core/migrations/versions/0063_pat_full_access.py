"""personal_access_tokens.full_access: the privileged surface is chosen per token

Revision ID: 0063
Revises: 0062

REB-810. `mcp_full_access` was one switch for the whole space: every token of every
member got the invoice and period-lock tools, a token pasted into an assistant used for
casual chat included. Each token now carries its own `full_access`, chosen by its owner
when it is made and closed unless asked for. The space switch stays, as the ceiling: what
a token may do is its own flag and the space's switch.

**What an existing token becomes.** Nothing an agent could do yesterday is taken away. A
token of a database whose switch is on gets `full_access = true`; a token of a database
whose switch is off gets `false`, and it could not use the privileged surface before
either. «The switch» is the row `space_settings.mcp_full_access` of this database when
there is one, and otherwise the environment's `PIGROCRM_MCP_FULL_ACCESS`, which is what a
space with no row of its own inherits (`space_base_settings`). The value is read with
`Settings`' own boolean rules, so `1`, `true`, `yes`, `si` and `sì` open it as the
settings page reads them. Revoked tokens are set the same way: they are dead either way,
and leaving them at the column default would make the list read differently for no reason.

The downgrade drops the column; the switch then decides for every token again, which is
exactly what the code before this revision did.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pigrocrm.core.config import get_settings

revision: str = "0063"
down_revision: str | Sequence[str] | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The spellings `space_settings.service._coerce` reads as true. Written out here rather
# than imported: a migration describes the world at its own revision, and a later change
# to that function must not change what this one did.
_TRUE = ("1", "true", "yes", "si", "sì")


def _switch_is_on(connection: sa.Connection) -> bool:
    row = connection.execute(
        sa.text("SELECT value FROM space_settings WHERE key = 'mcp_full_access'")
    ).first()
    if row is None:
        return bool(get_settings().mcp_full_access)
    return str(row[0]).strip().lower() in _TRUE


def upgrade() -> None:
    op.add_column(
        "personal_access_tokens",
        sa.Column("full_access", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    if _switch_is_on(op.get_bind()):
        op.execute(sa.text("UPDATE personal_access_tokens SET full_access = true"))


def downgrade() -> None:
    op.drop_column("personal_access_tokens", "full_access")
