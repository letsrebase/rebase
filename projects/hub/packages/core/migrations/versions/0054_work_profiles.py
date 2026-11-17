"""cli_authorizations and work_profiles: the CLI's sign-in and the profile it sends

Revision ID: 0054
Revises: 0053

REB-1133 and REB-1124, §§ 2 and 3 of the work-profile design (2026-11-17).

`cli_authorizations` is one device authorization: the sha256 of the device code, the `XXXX-XXXX`
code the person types or follows, the scope, the person who decided (`NULL` until then, `ON DELETE
CASCADE` with them), the state, the sha256 of the access token once it is handed out, and the
polling interval (`poll_interval`, 5 seconds and 5 more at each `slow_down`). `user_code` is
unique only while the row is `pending`. `expires_at` is the code's deadline and, once the token is
handed out, the token's.

`work_profiles` is the profile a freelancer sent, one row per freelancer (`ON DELETE CASCADE`): the
payload as received minus `contact`, the hub's own groundedness score and unsupported anchors, the
review state (`draft`, `approved`, `rejected`) and the 12-month `expires_at` the sweep deletes by.

Neither table is read by PostHog's warehouse: `posthog_ro` is granted table by table and these are
not in the grant. Conditional like every migration of this package: `IF NOT EXISTS`, `IF EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0054"
down_revision: str | Sequence[str] | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = "TIMESTAMP WITH TIME ZONE"

_CREATE = (
    "CREATE TABLE IF NOT EXISTS cli_authorizations ("
    "id UUID PRIMARY KEY, "
    "device_code_hash VARCHAR(64) NOT NULL, "
    "user_code VARCHAR(9) NOT NULL, "
    "scope VARCHAR(20) NOT NULL, "
    "user_id UUID REFERENCES users (id) ON DELETE CASCADE, "
    "state VARCHAR(10) NOT NULL, "
    "token_hash VARCHAR(64), "
    "poll_interval SMALLINT NOT NULL DEFAULT 5, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    f"expires_at {_TS} NOT NULL, "
    f"decided_at {_TS}, "
    f"last_polled_at {_TS}, "
    f"spent_at {_TS}, "
    "CONSTRAINT ck_cli_authorizations_scope CHECK (scope IN ('work_profile')), "
    "CONSTRAINT ck_cli_authorizations_state CHECK (state IN ('pending', 'approved', 'denied', "
    "'spent', 'expired')))",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_cli_authorizations_device_code_hash "
    "ON cli_authorizations (device_code_hash)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_cli_authorizations_user_code_pending "
    "ON cli_authorizations (user_code) WHERE state = 'pending'",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_cli_authorizations_token_hash "
    "ON cli_authorizations (token_hash)",
    "CREATE INDEX IF NOT EXISTS ix_cli_authorizations_user_id ON cli_authorizations (user_id)",
    "CREATE TABLE IF NOT EXISTS work_profiles ("
    "id UUID PRIMARY KEY, "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id) ON DELETE CASCADE, "
    "payload JSONB NOT NULL, "
    "tool_version VARCHAR(40), "
    "groundedness INTEGER NOT NULL, "
    "unsupported_anchors JSONB NOT NULL, "
    "state VARCHAR(10) NOT NULL, "
    f"submitted_at {_TS} NOT NULL DEFAULT now(), "
    f"expires_at {_TS} NOT NULL, "
    f"decided_at {_TS}, "
    "decided_by UUID REFERENCES users (id), "
    "decision_note TEXT, "
    "CONSTRAINT ck_work_profiles_state CHECK (state IN ('draft', 'approved', 'rejected')), "
    "CONSTRAINT ck_work_profiles_groundedness_range CHECK (groundedness BETWEEN 0 AND 100))",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_work_profiles_freelancer_id "
    "ON work_profiles (freelancer_id)",
    "CREATE INDEX IF NOT EXISTS ix_work_profiles_expires_at ON work_profiles (expires_at)",
)

_DROP = (
    "DROP TABLE IF EXISTS work_profiles",
    "DROP TABLE IF EXISTS cli_authorizations",
)


def upgrade() -> None:
    for statement in _CREATE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DROP:
        op.execute(statement)
