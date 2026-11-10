"""Rows the campaign tests build on: people in every state, leads, company requests,
and a clean table after each test. Imported by name, like `fakes_contracts.py`."""

import base64
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from admins import an_admin
from hub_tables import wipe_hub
from sqlalchemy.orm import Session

from rebase_core.admin_tokens import AdminRead
from rebase_core.campaigns.schemas import CampaignDraft
from rebase_core.campaigns.states import Candidate
from rebase_core.config import Settings
from rebase_core.models import Campaign, Company, Freelancer, Login, Signup, User

# The Svix secret every Resend webhook test signs with and every settings object carries:
# `whsec_` and 24 bytes of base64, which is what Resend shows. Not a real secret.
RESEND_TEST_SECRET = "whsec_" + base64.b64encode(b"s" * 24).decode()

T0 = datetime(2026, 9, 25, 7, 30, tzinfo=UTC)


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    wipe_hub(hub_session)


def person(
    session: Session,
    email: str,
    *,
    first_name: str = "Ada",
    cv: bool = True,
    rate: bool = True,
    job_title: bool = True,
    remote: bool = True,
    deleted: bool = False,
    role: str = "member",
    logins: int = 0,
    status: str = "new",
    confirmed: bool = True,
) -> Freelancer:
    """A card. Its address is confirmed unless `confirmed=False` (REB-911): the rule that
    leaves an unproved address out of campaigns has its own tests, and every other test
    here is about something else."""
    user = User(
        email=email,
        first_name=first_name,
        last_name="Lovelace",
        role=role,
        email_confirmed_at=T0 if confirmed else None,
    )
    session.add(user)
    session.flush()
    card = Freelancer(
        user_id=user.id,
        cv_bytes=b"%PDF" if cv else None,
        cv_filename="cv.pdf" if cv else None,
        cv_mime="application/pdf" if cv else None,
        cv_size=4 if cv else None,
        daily_rate=Decimal("450") if rate else None,
        job_title="Backend developer" if job_title else None,
        remote="remote" if remote else None,
        links=[],
        status=status,
    )
    if deleted:
        card.deleted_at = datetime.now(UTC)
    session.add(card)
    session.flush()
    for _ in range(logins):
        session.add(Login(user_id=user.id))
    session.commit()
    return card


def lead(session: Session, email: str, first_name: str | None = "giulia") -> Signup:
    row = Signup(email=email, first_name=first_name, last_name="Branda")
    session.add(row)
    session.commit()
    return row


def company(
    session: Session,
    email: str,
    *,
    status: str = "new",
    deleted: bool = False,
    confirmed: bool = True,
) -> Company:
    user = session.query(User).filter(User.email == email).one_or_none()
    if user is None:
        user = User(
            email=email,
            first_name="Ciro",
            last_name="Aurelio",
            email_confirmed_at=T0 if confirmed else None,
        )
        session.add(user)
        session.flush()
    row = Company(
        user_id=user.id,
        company_name="Block Buy SRL",
        requested_role="Developer",
        project="ASP.NET Core e React",
        period_from=date(2026, 10, 1),
        duration="12 mesi",
        daily_budget=Decimal("320"),
        remote="remote",
        headcount=1,
        status=status,
    )
    if deleted:
        row.deleted_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    return row


def emails(candidates: list[Candidate]) -> list[str]:
    return [c.email for c in candidates]


def admin(session: Session) -> User:
    """The admin every campaign test acts as: one row, whoever asks first makes it."""
    user = session.get(User, an_admin(session, last_name="Sala"))
    assert user is not None
    return user


def campaign_row(session: Session, **fields: object) -> Campaign:
    """A campaign row written straight to the table, for the tests below the service."""
    values: dict[str, object] = {
        "created_by": admin(session).id,
        "name": "Prova",
        "slug": f"c-prova-{session.query(Campaign).count()}",
        "source": "status",
        "journey_status": "missing_cv",
        "subject": "o",
        "text": "Ciao {nome},",
        "button_text": "Vai",
        "button_meta": "area",
        "action": "cv",
        "content_at": T0,
    }
    values.update(fields)
    campaign = Campaign(**values)
    session.add(campaign)
    session.commit()
    return campaign


SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
NOW = datetime(2026, 9, 25, 7, 0, tzinfo=UTC)


class Clock:
    """A clock a test moves by hand: the service and the tick take one."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def as_admin(user: User) -> AdminRead:
    return AdminRead.model_validate(user)


def draft(**fields: object) -> CampaignDraft:
    values: dict[str, object] = {
        "name": "Manca il CV",
        "source": "status",
        "journey_status": "missing_cv",
        "subject": "Manca solo il CV",
        "text": "Ciao {nome},\n\ntesto.",
        "button_text": "Carica il CV",
        "button_meta": "area",
        "action": "cv",
    }
    values.update(fields)
    return CampaignDraft(**values)  # type: ignore[arg-type]
