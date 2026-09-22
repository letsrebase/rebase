"""users can be seen actually running the space: last_login_at

Revision ID: 0038
Revises: 0037

REB-297. `#229`/`#234` already shipped the members table, the pending-invites table
and the invite dialog; the one thing the original ask still lacked was a way to tell
an account that has never been used from one that has. `users.last_login_at` is
nullable and written by `UserService.authenticate` on every successful password login
and by `InvitationService.accept` the moment an invitation opens its first session --
the two and only ways an account starts being used (see `auth/models.py`'s own
comment). Never touched anywhere else: a deactivated/reactivated account keeps
whenever it was last actually entered, not when an admin last flipped a switch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
