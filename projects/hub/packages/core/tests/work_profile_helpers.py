"""What the CLI sign-in and work-profile tests share: a person with a card, a clock to move, a
whole sign-in, and the upstream sample as a profile that passes the intake."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rebase_core.cli_auth import CliAuthService
from rebase_core.config import Settings
from rebase_core.models import Freelancer, User

SAMPLE = Path(__file__).parent / "fixtures" / "work_profile_candidate.sample.json"
START = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.at = START

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> None:
        self.at += timedelta(**delta)


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def person(
    session: Session, email: str = "ada@studio.it", *, card: bool = True
) -> tuple[UUID, UUID | None]:
    user = User(email=email, first_name="Ada", last_name="Lovelace")
    session.add(user)
    session.flush()
    freelancer_id = None
    if card:
        row = Freelancer(user_id=user.id)
        session.add(row)
        session.flush()
        freelancer_id = row.id
    session.commit()
    return user.id, freelancer_id


def sample() -> dict[str, Any]:
    profile: dict[str, Any] = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return profile


def sign_in(session: Session, clock: Clock, user_id: UUID) -> str:
    """A whole sign-in, up to the raw access token the CLI would hold."""
    service = CliAuthService(session, settings(), clock)
    start = service.start("work_profile")
    service.approve(start.user_code, user_id)
    clock.advance(seconds=6)
    polled = service.poll(start.device_code)
    assert polled.access_token is not None
    return polled.access_token
