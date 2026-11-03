"""Spec 9 §5.6: the session lasts six months and every refresh renews it."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.refresh_models import RefreshToken
from pigrocrm.core.auth.refresh_service import RefreshTokenService
from pigrocrm.core.auth.tokens import decode_token
from pigrocrm.core.config import Settings


def _settings() -> Settings:
    return Settings(_env_file=None, jwt_secret="x" * 32)  # type: ignore[call-arg]


@pytest.fixture
def admin_user(db_session: Session) -> User:
    # No `admin_user` fixture exists in conftest.py; built inline the same way
    # `fakes/gmail_fixtures.py::connected_account` does, since all this test needs is a
    # persisted user row for `RefreshTokenService.issue`'s foreign key.
    user = User(
        email=f"session-ttl-{uuid4().hex[:8]}@example.it",
        nome="Owner",
        password_hash="x",
        role="admin",
        active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_the_default_refresh_lifetime_is_one_hundred_and_eighty_days() -> None:
    assert _settings().refresh_token_days == 180
    assert _settings().access_token_minutes == 15


def test_a_refresh_issued_today_expires_in_six_months(
    db_session: Session, admin_user: User
) -> None:
    settings = _settings()
    token = RefreshTokenService(db_session).issue(admin_user.id, settings)
    payload = decode_token(token, settings, expected_type="refresh")
    row = db_session.execute(
        select(RefreshToken).where(RefreshToken.jti == payload.jti)
    ).scalar_one()
    assert timedelta(days=179, hours=23) < row.expires_at - datetime.now(UTC) <= timedelta(days=180)


def test_rotation_renews_the_window(db_session: Session, admin_user: User) -> None:
    """Consuming the old jti and issuing a new one is what `/api/auth/refresh` does:
    the new row's expiry is measured from now, so a user who refreshes on day 179
    gets another 180 days. Sliding, not fixed."""
    settings = _settings()
    service = RefreshTokenService(db_session)
    first = decode_token(service.issue(admin_user.id, settings), settings, expected_type="refresh")
    service.consume(first.jti, admin_user.id)
    second = decode_token(service.issue(admin_user.id, settings), settings, expected_type="refresh")
    rows = {
        r.jti: r
        for r in db_session.execute(
            select(RefreshToken).where(RefreshToken.user_id == admin_user.id)
        ).scalars()
    }
    assert rows[first.jti].consumed_at is not None
    assert rows[second.jti].expires_at >= rows[first.jti].expires_at
