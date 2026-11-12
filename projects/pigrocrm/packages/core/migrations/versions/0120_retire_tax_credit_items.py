"""a withholding suffered and an IVA credit are no longer credit items of an F24

Revision ID: 0120
Revises: 0119

REB-1100. A withholding suffered is netted inside the income-tax return (RN33 col. 4 into RN34) and
the IVA credit of a period is taken in by the next periodic settlement: neither is a line of an
F24 (research note 2026-11-12, section 6; D.Lgs. 241/1997 art. 17 c. 1; D.P.R. 100/1998 art. 1).
`TaxCreditService` therefore stopped writing the two origins of `tax_credits`
(`withholding_suffered`, `iva_carry_forward`), and this removes what it had written.

For each credit of those origins that is still open, which means its due item is not settled, no
payment is recorded on it and it is not a line of a settled F24 group:

* the open F24 group that holds it is dissolved, every live line released, as `ungroup_f24` does
  (its other items are free again; a grouped forecast was frozen as `expected`, and the next refresh
  of the tax items restates it);
* its due item is dropped, the model's own way to say an item is no longer owed, with the reason;
* its `tax_credits` row is deleted.

A credit an F24 already used, in part or whole, or one that sits in a settled group is left as it
is: the group keeps its history and the migration logs each such credit by id. Nothing here touches
a figure of the income tax or the IVA, which never read the credit items.

Going down restores nothing: the dropped items and the deleted rows were derived data that the
writer is gone for.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0120"
down_revision: str | Sequence[str] | None = "0119"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DROPPED_REASON = "credito non piu' scritto: non e' una riga dell'F24"
log = logging.getLogger("alembic.runtime.migration")

# A legacy credit with its item, when it has one, and whether it must stay: its item is settled, a
# payment is recorded on it, or it is a line (live or released) of a settled group.
_LEGACY = sa.text(
    """
    SELECT c.id AS credit_id,
           i.id AS item_id,
           COALESCE(
               i.state = 'settled'
               OR EXISTS (
                   SELECT 1 FROM due_item_settlements s
                   WHERE s.due_item_id = i.id AND s.reversed_at IS NULL
               )
               OR EXISTS (
                   SELECT 1 FROM f24_group_items l
                   JOIN f24_groups g ON g.id = l.group_id
                   WHERE l.due_item_id = i.id AND g.state = 'settled'
               ),
               false
           ) AS must_stay
    FROM tax_credits c
    LEFT JOIN due_items i ON i.source_kind = 'tax_credit' AND i.source_id = c.id
    WHERE c.origin IN ('iva_carry_forward', 'withholding_suffered')
    """
)


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(_LEGACY).all()
    # A credit stays when any item that points at it must.
    kept_credits = {row.credit_id for row in rows if row.must_stay}
    removable = [row for row in rows if row.credit_id not in kept_credits]
    credit_ids = sorted({row.credit_id for row in removable})
    item_ids = [row.item_id for row in removable if row.item_id is not None]
    if kept_credits:
        log.warning(
            "REB-1100: %d legacy tax credit(s) used by or in a settled F24 stay as they are: %s",
            len(kept_credits),
            ", ".join(sorted(str(credit_id) for credit_id in kept_credits)),
        )
    if not credit_ids:
        return
    if item_ids:
        # The open groups that hold a removable item: dissolved, as `ungroup_f24` does.
        group_ids = [
            row.group_id
            for row in connection.execute(
                sa.text(
                    "SELECT DISTINCT l.group_id FROM f24_group_items l "
                    "JOIN f24_groups g ON g.id = l.group_id "
                    "WHERE l.due_item_id = ANY(:items) AND l.released_at IS NULL "
                    "AND g.state = 'open'"
                ),
                {"items": item_ids},
            )
        ]
        if group_ids:
            connection.execute(
                sa.text(
                    "UPDATE f24_group_items SET released_at = now(), updated_at = now() "
                    "WHERE group_id = ANY(:groups) AND released_at IS NULL"
                ),
                {"groups": group_ids},
            )
            connection.execute(
                sa.text(
                    "UPDATE f24_groups SET state = 'dissolved', updated_at = now() "
                    "WHERE id = ANY(:groups)"
                ),
                {"groups": group_ids},
            )
            log.info("REB-1100: dissolved %d open F24 group(s) with a credit", len(group_ids))
        connection.execute(
            sa.text(
                "UPDATE due_items SET state = 'dropped', dropped_reason = :reason, "
                "updated_at = now() WHERE id = ANY(:items) AND state <> 'dropped'"
            ),
            {"items": item_ids, "reason": DROPPED_REASON},
        )
    connection.execute(
        sa.text("DELETE FROM tax_credits WHERE id = ANY(:credits)"), {"credits": credit_ids}
    )
    log.info("REB-1100: removed %d legacy tax credit(s)", len(credit_ids))


def downgrade() -> None:
    """Nothing to restore: see the module docstring."""
