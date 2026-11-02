"""notes: a signed, dated thread on deals, customers and people; the engagements door's
references move to a column

Revision ID: 0064
Revises: 0063

REB-879. The single `note` text column of `deals`, `customers` and `people`, overwritten
by every edit, becomes a thread: one `notes` table keyed by `(entity_type, entity_id)`,
each row with who wrote it (`autore_id`, `autore`), through which door (`fonte`) and when,
the last edit, and a soft delete. The table carries the row-level policy every table that
reaches an azienda carries (spec 2026-10-03 §4): `scope_tutte()` first, then the parent's
own visibility through `entita_visibile`, forced like the rest. Grants need nothing: the
application role has default privileges on what the owner creates, and the boot's
`ensure-space-defaults` re-grants on every table of every space. A second table,
`note_revisions`, keeps the whole text each edit replaced (a timeline payload is cut at 2000
characters); its policy is its note's own.

What is moved, in this order, inside the one transaction:

1. `deals.rebase_refs` (a `text[]`, GIN-indexed) is created. The `rebase:request=<uuid>`
   and `rebase:match=<uuid>` references that `engagements/leads.py` and
   `engagements/service.py` wrote as the last line of `deals.note` are read out of every
   deal's note (anywhere in it, as the old `strpos` lookup found them) into that column,
   and a line that was nothing but a reference is removed from the text.
2. Every non-blank `note` of the three tables becomes the first note of its row: no
   author, no source (`fonte` NULL, shown as «Nota precedente»), `created_at` the row's
   `updated_at`, which is when that text was last written. A text that is only a
   reference line leaves no note.
3. The three `note` columns are dropped, indexes with them.

Safe on a database in use: nothing is deleted before it has a copy, and a note's text is
kept as it was (trimmed only when a reference line was cut out of it). The downgrade
rebuilds the columns, joining each row's live notes oldest first with a blank line and
putting the references back as the last lines of a deal's note, which is the shape the old
code read.
"""

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064"
down_revision: str | Sequence[str] | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "notes"
REVISIONS = "note_revisions"
PARENTS = (("deals", "deal"), ("customers", "customer"), ("people", "person"))
POLICY_EXPRESSION = "scope_tutte() OR entita_visibile(entity_type, entity_id, false)"
# A revision is as visible as its note, which is as visible as its record.
REVISIONS_POLICY_EXPRESSION = (
    "scope_tutte() OR EXISTS (SELECT 1 FROM notes n WHERE n.id = note_id "
    "AND entita_visibile(n.entity_type, n.entity_id, false))"
)
# The two references the engagements door writes, a UUID each.
REFERENCE = re.compile(r"rebase:(?:request|match)=[0-9a-fA-F-]{36}")


def statements() -> list[str]:
    """The row-level security of the table, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY ambito_azienda ON {TABLE} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {REVISIONS} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {REVISIONS} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY ambito_azienda ON {REVISIONS} FOR ALL "
        f"USING ({REVISIONS_POLICY_EXPRESSION}) WITH CHECK ({REVISIONS_POLICY_EXPRESSION})",
    ]


def split_references(note: str) -> tuple[list[str], str]:
    """The references a deal's note carries, in order and once each, and the note without
    the lines that were only a reference. A reference inside a longer line is registered
    and its line stays: the old lookup found it there too, and the person's words are
    not ours to cut."""
    references: list[str] = []
    for found in REFERENCE.findall(note):
        if found not in references:
            references.append(found)
    if not references:
        return [], note
    kept = [line for line in note.splitlines() if not REFERENCE.fullmatch(line.strip())]
    return references, "\n".join(kept).strip()


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("testo", sa.Text(), nullable=False),
        sa.Column("autore_id", sa.Uuid(), nullable=True),
        sa.Column("autore", sa.String(length=200), nullable=True),
        sa.Column("fonte", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("modificata_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modificata_da_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["autore_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["modificata_da_id"], ["users.id"]),
        sa.CheckConstraint(
            "entity_type IN ('deal', 'customer', 'person')", name="ck_notes_entity_type"
        ),
        sa.CheckConstraint(
            "fonte IS NULL OR fonte IN ('web', 'mcp', 'sistema', 'rebase')", name="ck_notes_fonte"
        ),
    )
    op.create_index("ix_notes_entity", TABLE, ["entity_type", "entity_id", "created_at"])
    op.create_table(
        REVISIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("testo", sa.Text(), nullable=False),
        sa.Column(
            "edited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("edited_by_id", sa.Uuid(), nullable=True),
        sa.Column("edited_by", sa.String(length=200), nullable=True),
        sa.Column("fonte", sa.String(length=10), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"]),
        sa.ForeignKeyConstraint(["edited_by_id"], ["users.id"]),
    )
    op.create_index("ix_note_revisions_note", REVISIONS, ["note_id", "edited_at"])
    for statement in statements():
        op.execute(statement)

    op.add_column(
        "deals",
        sa.Column(
            "rebase_refs",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )
    op.create_index("ix_deals_rebase_refs", "deals", ["rebase_refs"], postgresql_using="gin")

    connection = op.get_bind()
    notes = sa.table(
        TABLE,
        sa.column("id", sa.Uuid()),
        sa.column("entity_type", sa.String()),
        sa.column("entity_id", sa.Uuid()),
        sa.column("testo", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    for table, entity_type in PARENTS:
        rows = connection.execute(
            sa.text(f"SELECT id, note, updated_at FROM {table} WHERE note IS NOT NULL")  # noqa: S608
        ).all()
        moved = []
        for row_id, note, updated_at in rows:
            if table == "deals":
                references, note = split_references(note)
                if references:
                    connection.execute(
                        sa.text("UPDATE deals SET rebase_refs = :refs WHERE id = :id"),
                        {"refs": references, "id": row_id},
                    )
            if note.strip():
                moved.append(
                    {
                        "id": uuid.uuid4(),
                        "entity_type": entity_type,
                        "entity_id": row_id,
                        "testo": note,
                        "created_at": updated_at,
                    }
                )
        if moved:
            connection.execute(sa.insert(notes), moved)
        op.drop_column(table, "note")


def downgrade() -> None:
    connection = op.get_bind()
    for table, entity_type in PARENTS:
        op.add_column(table, sa.Column("note", sa.Text(), nullable=True))
        threads = connection.execute(
            sa.text(
                "SELECT entity_id, testo FROM notes "
                "WHERE entity_type = :type AND deleted_at IS NULL "
                "ORDER BY created_at, id"
            ),
            {"type": entity_type},
        ).all()
        texts: dict[uuid.UUID, list[str]] = {}
        for entity_id, testo in threads:
            texts.setdefault(entity_id, []).append(testo)
        references: dict[uuid.UUID, list[str]] = {}
        if table == "deals":
            references = {
                deal_id: refs
                for deal_id, refs in connection.execute(
                    sa.text("SELECT id, rebase_refs FROM deals WHERE cardinality(rebase_refs) > 0")
                )
            }
        for row_id in texts.keys() | references.keys():
            body = "\n\n".join(texts.get(row_id, []))
            # A reference is a line of its own at the end, as the old writers left it.
            lines = ([body] if body else []) + references.get(row_id, [])
            connection.execute(
                sa.text(f"UPDATE {table} SET note = :note WHERE id = :id"),  # noqa: S608
                {"note": "\n".join(lines), "id": row_id},
            )
    op.drop_index("ix_deals_rebase_refs", table_name="deals")
    op.drop_column("deals", "rebase_refs")
    op.drop_table(REVISIONS)
    op.drop_table(TABLE)
