"""What the campaign API tests share: a clean slate after each test, the admin, and a
campaign with one recipient whose unsubscribe token the test chooses."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from hub_tables import wipe_hub
from sqlalchemy.orm import Session

from rebase_core.models import Campaign, CampaignRecipient, User

ADMIN_EMAIL = "ivan@rebase.it"


@pytest.fixture
def tidy(api_session: Session) -> Iterator[Session]:
    yield api_session
    api_session.rollback()
    wipe_hub(api_session)


def admin_user(session: Session) -> User:
    user = session.query(User).filter(User.email == ADMIN_EMAIL).one_or_none()
    if user is None:
        user = User(email=ADMIN_EMAIL, nome="Ivan", cognome="Sala", role="admin")
        session.add(user)
        session.commit()
    return user


def recipient_row(session: Session, token: str) -> None:
    recipient_rows(session, {token: "ada@studio.it"})


def recipient_rows(session: Session, addresses: dict[str, str]) -> None:
    """One campaign with one recipient per `token: address` pair."""
    admin = admin_user(session)
    campaign = Campaign(
        created_by=admin.id,
        nome="n",
        slug="c-n",
        fonte="stato",
        stato_percorso="manca_cv",
        oggetto="o",
        testo="t",
        bottone_testo="b",
        bottone_meta="area",
        azione="cv",
        contenuto_at=datetime.now(UTC),
    )
    session.add(campaign)
    session.flush()
    for token, email in addresses.items():
        session.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                email=email,
                tipo="freelancer",
                codice="1",
                prima={},
                disiscrizione_token=token,
            )
        )
    session.commit()
