"""freelancer_cards, team_proposals, team_requests, team_request_talents,
talent_cloud_grants -- and «Vetted» on freelancers

Revision ID: 0022
Revises: 0020

REB-509 (milestone C, task C2): the tables every later task of the team builder and the
talent cloud writes to. `freelancer_cards` is Claude's anonymous read of a CV (spec
§ 2.1), one row per freelancer; `team_proposals` is one row per team the engine
proposed, immutable once written; `team_requests` and `team_request_talents` are a
company's «Assumi team» and who it asked; `talent_cloud_grants` is a company's access
to the private talent cloud (spec § 4.1). `freelancers` gains `vetted_at`/`vetted_by`
for the manual «Vetted» flag, and `ADMIN_ACTION_KINDS` gains `vetted` in Python only --
`admin_actions.kind` carries no database `CHECK` today (nothing in this package has
ever added one), so there is no `pg_constraint` guard to write for it here.

Written as `0022` revising `0019`, then re-pointed at `0020` when the campaigns branch
merged into `main`; `0021` (the hours-report branch) still revises `0019`, so
three migrations would make three heads and `alembic upgrade head` refuse at the API's
boot. C9 and D5 re-point `down_revision` at whatever `main`'s head is by then and rename
this file to the next free number, after `git merge origin/main`, before `gh pr ready`.

Every statement is conditional (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT
EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`), the discipline migration 0001's
docstring states for this package and 0017's repeats for a set of brand new tables: a
retried deploy must not error on a table or a column the previous attempt already
added. The check constraints are declared inside each `CREATE TABLE`, so they arrive
with their table and need no `pg_constraint` guard of their own; the two partial unique
indexes (`uq_team_requests_proposal_id`, `uq_talent_cloud_grants_user_company_live`) are
`CREATE UNIQUE INDEX IF NOT EXISTS ... WHERE ...`, which Postgres accepts the same way
as any other index.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMPS = (
    "created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(), "
    "updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()"
)

_TABLES = (
    # `cv_sha256`, `card`, `model` and the two token counts are the last *successful*
    # generation, all nullable together (`NULL` until a freelancer's first card);
    # `error`/`error_cv_sha256` are the last *failure*, independent of the above.
    "CREATE TABLE IF NOT EXISTS freelancer_cards ("
    "freelancer_id UUID PRIMARY KEY REFERENCES freelancers (id) ON DELETE CASCADE, "
    "cv_sha256 VARCHAR(64), "
    "card JSONB, "
    "model VARCHAR(60), "
    "input_tokens INTEGER, "
    "output_tokens INTEGER, "
    "generated_at TIMESTAMP WITH TIME ZONE, "
    "error TEXT, "
    "error_cv_sha256 VARCHAR(64), "
    f"{_TIMESTAMPS})",
    "CREATE TABLE IF NOT EXISTS team_proposals ("
    "id UUID PRIMARY KEY, "
    "descrizione TEXT NOT NULL, "
    "nota TEXT, "
    "previous_id UUID REFERENCES team_proposals (id), "
    "riassunto TEXT NOT NULL, "
    "luogo JSONB NOT NULL, "
    "team JSONB NOT NULL, "
    "economia JSONB NOT NULL, "
    "model VARCHAR(60) NOT NULL, "
    "input_tokens INTEGER NOT NULL, "
    "output_tokens INTEGER NOT NULL, "
    "cache_read_tokens INTEGER NOT NULL, "
    "origine VARCHAR(10) NOT NULL, "
    "user_id UUID REFERENCES users (id), "
    "created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(), "
    "CONSTRAINT ck_team_proposals_origine CHECK (origine IN ('pubblico', 'cloud', 'admin')))",
    "CREATE TABLE IF NOT EXISTS team_requests ("
    "id UUID PRIMARY KEY, "
    "proposal_id UUID REFERENCES team_proposals (id), "
    "origine VARCHAR(10) NOT NULL, "
    "azienda VARCHAR(200) NOT NULL, "
    "email VARCHAR(320) NOT NULL, "
    "telefono VARCHAR(40), "
    "user_id UUID REFERENCES users (id), "
    "company_id UUID REFERENCES companies (id), "
    "stato VARCHAR(20) NOT NULL, "
    "note TEXT, "
    "contacted_at TIMESTAMP WITH TIME ZONE, "
    "closed_at TIMESTAMP WITH TIME ZONE, "
    f"{_TIMESTAMPS}, "
    "CONSTRAINT ck_team_requests_stato CHECK (stato IN ('nuova', 'contattata', 'chiusa')), "
    "CONSTRAINT ck_team_requests_origine CHECK (origine IN ('pubblico', 'cloud')))",
    "CREATE TABLE IF NOT EXISTS team_request_talents ("
    "id UUID PRIMARY KEY, "
    "request_id UUID NOT NULL REFERENCES team_requests (id), "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id), "
    "ruolo VARCHAR(160) NOT NULL, "
    "token_hash VARCHAR(64), "
    "mail_sent_at TIMESTAMP WITH TIME ZONE, "
    "risposta VARCHAR(10), "
    "risposta_at TIMESTAMP WITH TIME ZONE, "
    "CONSTRAINT ck_team_request_talents_risposta CHECK (risposta IN ('si', 'no')))",
    "CREATE TABLE IF NOT EXISTS talent_cloud_grants ("
    "id UUID PRIMARY KEY, "
    "user_id UUID NOT NULL REFERENCES users (id), "
    "company_id UUID NOT NULL REFERENCES companies (id), "
    "granted_by UUID NOT NULL REFERENCES users (id), "
    "granted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(), "
    "revoked_by UUID REFERENCES users (id), "
    "revoked_at TIMESTAMP WITH TIME ZONE)",
)

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_team_requests_proposal_id "
    "ON team_requests (proposal_id) WHERE proposal_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_team_requests_stato_created_at "
    "ON team_requests (stato, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_team_proposals_created_at ON team_proposals (created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_team_request_talents_request_freelancer "
    "ON team_request_talents (request_id, freelancer_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_team_request_talents_token_hash "
    "ON team_request_talents (token_hash)",
    # One live grant per person and company (REB-518, spec § 2): a person behind two
    # companies holds two, and a second «Apri» on the same request finds the first.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_talent_cloud_grants_user_company_live "
    "ON talent_cloud_grants (user_id, company_id) WHERE revoked_at IS NULL",
)

_FREELANCER_COLUMNS = (
    "ALTER TABLE freelancers ADD COLUMN IF NOT EXISTS vetted_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE freelancers ADD COLUMN IF NOT EXISTS vetted_by UUID REFERENCES users (id)",
)


def upgrade() -> None:
    for statement in (*_TABLES, *_INDEXES, *_FREELANCER_COLUMNS):
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE freelancers DROP COLUMN IF EXISTS vetted_by")
    op.execute("ALTER TABLE freelancers DROP COLUMN IF EXISTS vetted_at")
    for table in (
        "talent_cloud_grants",
        "team_request_talents",
        "team_requests",
        "team_proposals",
        "freelancer_cards",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
