"""Builders every Slack test needs: settings with the app configured, a client wired to a
`FakeSlack` through the transport seam, users, and a workspace connected through the real
OAuth flow.

A module under `fakes/` for the reason `gmail_fixtures.py` gives: three test roots, no
`__init__.py` among them, so a `conftest` import is ambiguous and `fakes` is a real package.
"""

import base64
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from sqlalchemy.orm import Session

from fakes.fake_slack import CLIENT_ID, CLIENT_SECRET, SLACK_URL, FakeSlack
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.slack.client import SlackClient
from pigrocrm.core.slack.models import SlackWorkspace
from pigrocrm.core.slack.oauth import SlackOAuthService

SLACK_KEY = b"s" * 32


def slack_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "jwt_secret": "x" * 32,
        "slack_client_id": CLIENT_ID,
        "slack_client_secret": CLIENT_SECRET,
        "slack_token_key": base64.b64encode(SLACK_KEY).decode(),
        "slack_url": SLACK_URL,
        "public_url": "https://crm.example.it",
        # No `.env` of a developer's may change what these tests assert.
        "_env_file": None,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def slack_client(fake: FakeSlack) -> SlackClient:
    return SlackClient(SLACK_URL, http=fake.http, sleep=lambda _: None)


def make_user(
    session: Session, *, email: str | None = None, nome: str = "Mario Rossi", ruolo: str = "admin"
) -> User:
    user = User(
        email=email or f"user-{uuid4().hex[:8]}@example.it",
        nome=nome,
        password_hash="x",
        ruolo=ruolo,
        attivo=True,
    )
    session.add(user)
    session.flush()
    return user


def actor_of(user: User) -> Actor:
    return Actor(id=user.id, type="user", role=user.ruolo)  # type: ignore[arg-type]


def connect(
    session: Session, fake: FakeSlack, admin: User, settings: Settings | None = None
) -> SlackWorkspace:
    """Connects the workspace the way an admin does: `start`, Slack's redirect, `complete`."""
    settings = settings or slack_settings()
    service = SlackOAuthService(session, settings=settings, client=slack_client(fake))
    url = service.start(actor_of(admin))
    state = parse_qs(urlparse(url).query)["state"][0]
    service.complete(code="good-code", state=state, actor=actor_of(admin))
    workspace = service.repo.workspace()
    assert workspace is not None
    return workspace
