"""notes: one editable thread per freelancer and company, replacing `note` and `comments`

Revision ID: 0042
Revises: 0041

REB-856 (Lorenzo's request of 2026-10-29): the row's own `note` column and the append-only
`comments` table said the same kind of thing twice. There is now one thread, `notes`:

* `comments` is renamed `notes` (index and primary key with it) and gains `autore_id`
  (who wrote it, `users`), `fonte` (`web`, `mcp`, `persona`, `sistema`; `NULL` for a row
  that predates the tracking, which reads as an admin's), `modificata_at` /
  `modificata_da_id` (the last edit) and `eliminata_at` / `eliminata_da_id` (a soft delete,
  so the trail can still say what was there).
* The person's own events already in the table (the texts `MemberService` writes) are
  backfilled `fonte = 'persona'` with the card's user as `autore_id`: they are events, and an
  admin may not edit them. Every other existing row keeps `fonte` `NULL`.
* Every non-empty `freelancers.note` and `companies.note` becomes a note of its own, text
  verbatim, dated by the row's `updated_at`, written by «Nota precedente»; then both
  columns are dropped.

Downgrade puts the columns back, best effort: the migrated notes are concatenated (oldest
first) into the column again and leave the thread; a note an admin deleted since has no
place in `comments` and goes. `fonte` and the edit trail are lost. Guarded throughout, so a
retried deploy lands on the same schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS_NOTE_AUTHOR = "Nota precedente"
# What `MemberService` writes when the person changes their profile, uploads a CV or, for a
# request, a referente edits it: events, never remarks.
FREELANCER_EVENT_PREFIXES = (
    "Profilo aggiornato dalla persona",
    "Scheda confermata dalla persona",
    "CV caricato dalla persona",
    "CV aggiornato dalla persona",
)
COMPANY_EVENT_PREFIXES = ("Richiesta aggiornata dal referente",)


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "comments" in _tables() and "notes" not in _tables():
        op.rename_table("comments", "notes")
        op.execute("ALTER INDEX IF EXISTS ix_comments_entity RENAME TO ix_notes_entity")
        op.execute("ALTER INDEX IF EXISTS comments_pkey RENAME TO notes_pkey")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS autore_id UUID REFERENCES users (id)")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS fonte VARCHAR(10)")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS modificata_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS modificata_da_id UUID REFERENCES users (id)"
    )
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS eliminata_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS eliminata_da_id UUID REFERENCES users (id)"
    )

    for entity, table, prefixes in (
        ("freelancer", "freelancers", FREELANCER_EVENT_PREFIXES),
        ("company", "companies", COMPANY_EVENT_PREFIXES),
    ):
        for prefix in prefixes:
            op.execute(
                sa.text(
                    f"UPDATE notes SET fonte = 'persona', autore_id = parent.user_id "
                    f"FROM {table} AS parent "
                    f"WHERE notes.entity_type = :entity AND notes.entity_id = parent.id "
                    f"AND notes.fonte IS NULL AND notes.testo LIKE :pattern"
                ).bindparams(entity=entity, pattern=prefix + "%")
            )

        if "note" in _columns(table):
            op.execute(
                sa.text(
                    f"INSERT INTO notes (id, entity_type, entity_id, testo, autore, created_at) "
                    f"SELECT gen_random_uuid(), :entity, id, note, :author, "
                    f"COALESCE(updated_at, now()) FROM {table} "
                    f"WHERE note IS NOT NULL AND btrim(note) <> ''"
                ).bindparams(entity=entity, author=PREVIOUS_NOTE_AUTHOR)
            )
            op.execute(f"ALTER TABLE {table} DROP COLUMN note")


def downgrade() -> None:
    for entity, table in (("freelancer", "freelancers"), ("company", "companies")):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS note TEXT")
        op.execute(
            sa.text(
                f"UPDATE {table} SET note = merged.joined FROM ("
                f"SELECT entity_id, string_agg(testo, E'\\n\\n' ORDER BY created_at) AS joined "
                f"FROM notes WHERE entity_type = :entity AND autore = :author "
                f"AND eliminata_at IS NULL GROUP BY entity_id) AS merged "
                f"WHERE {table}.id = merged.entity_id"
            ).bindparams(entity=entity, author=PREVIOUS_NOTE_AUTHOR)
        )
    op.execute(
        sa.text("DELETE FROM notes WHERE autore = :author OR eliminata_at IS NOT NULL").bindparams(
            author=PREVIOUS_NOTE_AUTHOR
        )
    )
    for column in (
        "eliminata_da_id",
        "eliminata_at",
        "modificata_da_id",
        "modificata_at",
        "fonte",
        "autore_id",
    ):
        op.execute(f"ALTER TABLE notes DROP COLUMN IF EXISTS {column}")
    op.rename_table("notes", "comments")
    op.execute("ALTER INDEX IF EXISTS ix_notes_entity RENAME TO ix_comments_entity")
    op.execute("ALTER INDEX IF EXISTS notes_pkey RENAME TO comments_pkey")
