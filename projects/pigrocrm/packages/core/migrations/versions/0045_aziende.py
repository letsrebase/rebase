"""aziende: the emitter profile becomes one row per azienda, the fiscal profile is keyed to it

Revision ID: 0045
Revises: 0044

REB-615, milestone «Turn the emitter profile into the azienda row» of the spec
`docs/superpowers/specs/2026-10-03-several-aziende-in-one-space-design.md` (§2 steps
1, 2, 6 and 7). The table keeps its name, `emitter_profile` (§10): routes, class and
product say «azienda», the storage keeps the name every error label and timeline row
already carries.

What this revision does, in order:

1. `emitter_profile` loses `singleton` and gains `nome` (the short name the selector
   shows, backfilled with `left(ragione_sociale, 80)` because the source column is 255
   wide), `predefinita` (exactly one `true` per space, a partial unique index, set on
   the one row every space already has) and `attiva`; two partial unique indexes on
   `upper(partita_iva)` and `upper(codice_fiscale)`, the shape the import classifier
   compares, make «one azienda per fiscal id» a fact of the database.
2. `fiscal_profile` gains `azienda_id`, backfilled from the one azienda, then
   `NOT NULL` and `UNIQUE`; `singleton` goes. A space whose owner never saved a fiscal
   profile has no row today and still has none after this: nothing is invented.
3. `users.ambito_limitato`, `user_aziende` and `invitations.aziende` exist and stay
   empty: the scope they will carry is milestone 6's, the columns are here so every
   later migration of this project finds them.

No child table gets an `azienda_id` yet (invoices, customers, counters: milestones 2
and 3), and nothing can create a second azienda before milestone 5, so a space
migrated by this revision is exactly the space it was, with one azienda that is the
default.

The two `singleton` unique constraints were created unnamed by 0003 and 0004, so
Postgres named them `<table>_singleton_key`; those are the names dropped here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045"
down_revision: str | Sequence[str] | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. emitter_profile: from one row ever to one row per azienda.
    op.drop_constraint("emitter_profile_singleton_key", "emitter_profile", type_="unique")
    op.drop_column("emitter_profile", "singleton")
    op.add_column("emitter_profile", sa.Column("nome", sa.String(length=80), nullable=True))
    op.add_column(
        "emitter_profile",
        sa.Column("predefinita", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "emitter_profile",
        sa.Column("attiva", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.execute("UPDATE emitter_profile SET nome = left(ragione_sociale, 80)")  # noqa: S608
    op.alter_column("emitter_profile", "nome", nullable=False)
    # At most one row exists here (the constraint just dropped guaranteed it), so this
    # makes it the default without a tie to break.
    op.execute("UPDATE emitter_profile SET predefinita = true")  # noqa: S608
    op.create_index(
        "uq_emitter_profile_predefinita",
        "emitter_profile",
        ["predefinita"],
        unique=True,
        postgresql_where=sa.text("predefinita"),
    )
    op.create_index(
        "uq_emitter_profile_partita_iva",
        "emitter_profile",
        [sa.text("upper(partita_iva)")],
        unique=True,
        postgresql_where=sa.text("partita_iva IS NOT NULL"),
    )
    op.create_index(
        "uq_emitter_profile_codice_fiscale",
        "emitter_profile",
        [sa.text("upper(codice_fiscale)")],
        unique=True,
        postgresql_where=sa.text("codice_fiscale IS NOT NULL"),
    )

    # 2. fiscal_profile: keyed to its azienda.
    op.drop_constraint("fiscal_profile_singleton_key", "fiscal_profile", type_="unique")
    op.drop_column("fiscal_profile", "singleton")
    op.add_column("fiscal_profile", sa.Column("azienda_id", sa.Uuid(), nullable=True))
    # The one fiscal row, if any, belongs to the one azienda, if any. A fiscal row on a
    # space with no emitter row cannot happen through the product (provisioning writes
    # the emitter before anything else, and the fiscal profile is saved from a screen
    # that needs a space) and would fail the NOT NULL below, loudly, which is the right
    # outcome for a database this code has never seen.
    op.execute(  # noqa: S608
        "UPDATE fiscal_profile SET azienda_id = (SELECT id FROM emitter_profile LIMIT 1)"
    )
    op.alter_column("fiscal_profile", "azienda_id", nullable=False)
    op.create_foreign_key(
        "fk_fiscal_profile_azienda_id_emitter_profile",
        "fiscal_profile",
        "emitter_profile",
        ["azienda_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_fiscal_profile_azienda_id", "fiscal_profile", ["azienda_id"])

    # 3. The scope's own columns, empty until milestone 6.
    op.add_column(
        "users",
        sa.Column("ambito_limitato", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "user_aziende",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("azienda_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["azienda_id"], ["emitter_profile.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "azienda_id"),
    )
    op.add_column(
        "invitations",
        sa.Column("aziende", postgresql.ARRAY(sa.Uuid()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invitations", "aziende")
    op.drop_table("user_aziende")
    op.drop_column("users", "ambito_limitato")

    op.drop_constraint("uq_fiscal_profile_azienda_id", "fiscal_profile", type_="unique")
    op.drop_constraint(
        "fk_fiscal_profile_azienda_id_emitter_profile", "fiscal_profile", type_="foreignkey"
    )
    op.drop_column("fiscal_profile", "azienda_id")
    op.add_column(
        "fiscal_profile",
        sa.Column("singleton", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_unique_constraint("fiscal_profile_singleton_key", "fiscal_profile", ["singleton"])

    # A second azienda's rows cannot go back into a single-row table: refuse rather than
    # pick one, the way the spec says (§2).
    op.execute(
        sa.text(
            "DO $$ BEGIN IF (SELECT count(*) FROM emitter_profile) > 1 THEN "
            "RAISE EXCEPTION 'emitter_profile holds more than one azienda: 0045 cannot be undone'; "
            "END IF; END $$;"
        )
    )
    op.drop_index("uq_emitter_profile_codice_fiscale", table_name="emitter_profile")
    op.drop_index("uq_emitter_profile_partita_iva", table_name="emitter_profile")
    op.drop_index("uq_emitter_profile_predefinita", table_name="emitter_profile")
    op.drop_column("emitter_profile", "attiva")
    op.drop_column("emitter_profile", "predefinita")
    op.drop_column("emitter_profile", "nome")
    op.add_column(
        "emitter_profile",
        sa.Column("singleton", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_unique_constraint("emitter_profile_singleton_key", "emitter_profile", ["singleton"])
