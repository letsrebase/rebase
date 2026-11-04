"""user_contact_points: several ways to reach a person, and `users.phone` moves into them

Revision ID: 0050
Revises: 0049

REB-943 (design 2026-11-04: `projects/pigrocrm/docs/superpowers/specs/
2026-11-04-contact-points-design.md`).

`user_contact_points` is owned by `users` (a freelancer's and a company contact's one
record): `kind` (`email`, `phone`, `linkedin`), `value`, `label` (`work`, `personal`,
`billing`, `other`), an optional `note` of at most 200 characters, `is_primary` and the
display `position`. Three unique indexes hold the rules in the database: one primary per
person and kind, one value per person and kind (an address by its lower case, a number by its
ASCII digits) and one owner per address across all people. That an extra address is not
another person's *login* address lives in another table and is the service's to check.

`users.phone` becomes the person's primary `work` phone point, and the column is dropped. The
login address `users.email` is not copied: it stays the identity and is shown as the primary
email by the API.

The release is breaking for the old code, which reads `users.phone`: the api, the mcp and the
sweeps are stopped before `alembic upgrade` and started on the new image. The downgrade gives
the column back with the primary phone of each person (cut to the column's 40 characters), and
the other points are dropped with the table.

Conditional like every migration of this package: `IF NOT EXISTS`, `IF EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0050"
down_revision: str | Sequence[str] | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALUE_KEY = (
    "CASE WHEN kind = 'phone' THEN regexp_replace(value, '[^0-9]', '', 'g') ELSE lower(value) END"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_contact_points (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            kind VARCHAR(10) NOT NULL,
            value VARCHAR(320) NOT NULL,
            label VARCHAR(10) NOT NULL,
            note VARCHAR(200),
            is_primary BOOLEAN NOT NULL DEFAULT false,
            position INTEGER NOT NULL DEFAULT 0,
            value_key VARCHAR(320) GENERATED ALWAYS AS (
                CASE WHEN kind = 'phone' THEN regexp_replace(value, '[^0-9]', '', 'g')
                ELSE lower(value) END
            ) STORED,
            CONSTRAINT ck_user_contact_points_kind
                CHECK (kind IN ('email', 'phone', 'linkedin')),
            CONSTRAINT ck_user_contact_points_label
                CHECK (label IN ('work', 'personal', 'billing', 'other'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_contact_points_user_id ON user_contact_points (user_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_contact_points_primary "
        "ON user_contact_points (user_id, kind) WHERE is_primary"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_contact_points_value "
        "ON user_contact_points (user_id, kind, value_key)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_contact_points_email "
        "ON user_contact_points (lower(value)) WHERE kind = 'email'"
    )
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(40)")
    op.execute(
        """
        INSERT INTO user_contact_points (id, user_id, kind, value, label, is_primary, position)
        SELECT gen_random_uuid(), id, 'phone', btrim(phone), 'work', true, 0
        FROM users
        WHERE phone IS NOT NULL AND btrim(phone) <> ''
        ON CONFLICT DO NOTHING
        """
    )
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS phone")


def downgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(40)")
    op.execute(
        """
        UPDATE users SET phone = left(point.value, 40)
        FROM user_contact_points point
        WHERE point.user_id = users.id AND point.kind = 'phone' AND point.is_primary
        """
    )
    op.execute("DROP TABLE IF EXISTS user_contact_points")
