"""campaigns.bottone_url: a button that leads out of the hub, and the click it measures

Revision ID: 0027
Revises: 0026

REB-530. A campaign's button could lead only to the hub's own pages (`area`, `wizard`,
`richiesta`); an invitation to an event on Luma had to leave its address in the text as
a bare URL. `bottone_meta = 'link'` sends the button to `bottone_url`, an https address
of at most 500 characters, present with «Un link» and never without it. A link measures
the only thing the hub sees of a page it does not own, the first click Resend reports,
so `azione = 'clic'` goes with `link` and only with it (DECISIONS.md, 2026-09-28).

One nullable column and four `CHECK` constraints: the two enums gain a value, and two
new ones pair the address and the click with «Un link». Every existing row has neither,
so each constraint holds on the rows already there. Conditional like every migration of
this package: the column is `IF NOT EXISTS`, and each constraint is dropped `IF EXISTS`
before it is added, so a retried deploy adds the same four again. `campaigns` is not
read by PostHog's warehouse (`test_warehouse_contract.py`). It follows 0025 (#434, the
talent cloud) and 0026 (#473, the stalled send), which merge first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIONS = (
    "'entrato', 'cv', 'scheda_completa', 'profilo_creato', 'richiesta_aggiornata', 'pigro_cliente'"
)
_DESTINATIONS = "'area', 'wizard', 'pigro', 'richiesta'"

_UPGRADE = (
    ("ck_campaigns_azione", f"azione IN ({_ACTIONS}, 'clic')"),
    ("ck_campaigns_bottone_meta", f"bottone_meta IN ({_DESTINATIONS}, 'link')"),
    ("ck_campaigns_bottone_url", "(bottone_meta = 'link') = (bottone_url IS NOT NULL)"),
    ("ck_campaigns_link_clic", "(bottone_meta = 'link') = (azione = 'clic')"),
)
_DOWNGRADE = (
    ("ck_campaigns_azione", f"azione IN ({_ACTIONS})"),
    ("ck_campaigns_bottone_meta", f"bottone_meta IN ({_DESTINATIONS})"),
)


def _replace(name: str, condition: str) -> None:
    op.execute(f"ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS {name}")
    op.execute(f"ALTER TABLE campaigns ADD CONSTRAINT {name} CHECK ({condition})")


def upgrade() -> None:
    op.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS bottone_url VARCHAR(500)")
    for name, condition in _UPGRADE:
        _replace(name, condition)


def downgrade() -> None:
    """Back to 0026's enums. A campaign that already uses «Un link» cannot be described
    by them: Postgres checks the rows already there when a constraint is added, so the
    downgrade fails on that row and the whole migration rolls back, leaving the table as
    it was, rather than deleting a campaign somebody sent."""
    op.execute("ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS ck_campaigns_link_clic")
    op.execute("ALTER TABLE campaigns DROP CONSTRAINT IF EXISTS ck_campaigns_bottone_url")
    op.execute("ALTER TABLE campaigns DROP COLUMN IF EXISTS bottone_url")
    for name, condition in _DOWNGRADE:
        _replace(name, condition)
