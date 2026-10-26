"""automations: the three rules that open an attività, and the index that keeps it to one

Revision ID: 0055
Revises: 0054

For REB-727 and REB-728 (slice 7 §6). `automation_config` gains the switches of the three
rules the daily pass runs (`a3_offerta_inviata_richiama`, `a4_deal_fermo`,
`a5_fattura_scaduta`, default true like A1 and A2) and the one threshold,
`a4_deal_fermo_giorni` (default 30, `CHECK` 1 to 365). The columns are added with their
server defaults, so the row that exists keeps working and takes the defaults.

`uq_attivita_regola_aperta` is a unique partial index over the open, live attività an
automation wrote: `(regola, coalesce(customer_id, person_id, deal_id, invoice_id))`. An
attività carries at most one reference (`ck_attivita_un_solo_riferimento`), so the
`coalesce` is that reference whichever column holds it, and the index needs no
`NULLS NOT DISTINCT`. It is safe on the table that exists: no row has a `regola` yet (nothing
wrote one before this pass), and manual attività have a null `regola`, so none enters the
index. `IF NOT EXISTS` for a host that built it by hand. The downgrade drops the index and
the columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: str | Sequence[str] | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FLAGS = ("a3_offerta_inviata_richiama", "a4_deal_fermo", "a5_fattura_scaduta")


def upgrade() -> None:
    for flag in _FLAGS:
        op.add_column(
            "automation_config",
            sa.Column(flag, sa.Boolean(), nullable=False, server_default="true"),
        )
    op.add_column(
        "automation_config",
        sa.Column("a4_deal_fermo_giorni", sa.Integer(), nullable=False, server_default="30"),
    )
    op.create_check_constraint(
        "ck_automation_config_deal_fermo_giorni",
        "automation_config",
        "a4_deal_fermo_giorni BETWEEN 1 AND 365",
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_attivita_regola_aperta ON attivita "
        "(regola, (coalesce(customer_id, person_id, deal_id, invoice_id))) "
        "WHERE regola IS NOT NULL AND stato = 'aperta' AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_attivita_regola_aperta")
    op.drop_constraint("ck_automation_config_deal_fermo_giorni", "automation_config")
    op.drop_column("automation_config", "a4_deal_fermo_giorni")
    for flag in reversed(_FLAGS):
        op.drop_column("automation_config", flag)
