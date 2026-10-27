"""`GET /api/hub/referral-rates` (REB-709): the two live referral rates the public site
quotes, read from the admin's settings row, to anybody, and nothing else."""

import re
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rebase_api.main import create_app
from rebase_api.ratelimit import SIGNUPS_PER_MINUTE
from rebase_api.routers.referral_rates import UNREADABLE
from rebase_core.mail import RecordingSender
from rebase_core.models import Login, ReferralSettings, User

ADMIN_EMAIL = "ivan@rebase.it"
PATH = "/api/hub/referral-rates"


@pytest.fixture
def settings_row(api_session: Session) -> Iterator[Callable[[str, str], None]]:
    """Gives the test the one settings row it asks for, whatever an earlier test left, and
    takes it away afterwards: no test here leans on the row migration 0024 seeds (REB-647)."""
    before = set(api_session.scalars(select(User.id)))
    logins_before = set(api_session.scalars(select(Login.id)))

    def put(rate_freelancer: str, rate_company: str) -> None:
        api_session.execute(text("DELETE FROM referral_settings"))
        api_session.add(
            ReferralSettings(
                rate_freelancer=Decimal(rate_freelancer), rate_company=Decimal(rate_company)
            )
        )
        api_session.commit()

    yield put
    # Only what this test wrote, the users included: a blanket `DELETE FROM users` here
    # failed whenever an earlier file on the same worker had left a card behind
    # (`freelancers.user_id`), and the admin it then could not remove collided with the
    # next test's own (hub-v0.66.0's tag run).
    api_session.rollback()
    for table in ("sessions", "magic_link_tokens", "referral_settings"):
        api_session.execute(text(f"DELETE FROM {table}"))
    for login_id in set(api_session.scalars(select(Login.id))) - logins_before:
        api_session.execute(text("DELETE FROM logins WHERE id = :id"), {"id": login_id})
    for user_id in set(api_session.scalars(select(User.id))) - before:
        api_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    api_session.commit()


def _admin(api_session: Session) -> None:
    """The admin who edits the rates, reused, as an admin, when an earlier file left one."""
    admin = api_session.scalar(select(User).where(func.lower(User.email) == ADMIN_EMAIL))
    if admin is None:
        api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    else:
        admin.role = "admin"
    api_session.commit()


def _login(client: TestClient, sender: RecordingSender) -> None:
    assert client.post("/api/hub/auth/link", json={"email": ADMIN_EMAIL}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def test_both_rates_come_from_the_settings_row(
    client: TestClient, settings_row: Callable[[str, str], None]
) -> None:
    settings_row("0.10", "0.30")

    answer = client.get(PATH)

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"rate_freelancer": "0.1000", "rate_company": "0.3000"}


def test_an_admin_edit_is_the_next_anonymous_read(
    client: TestClient,
    sender: RecordingSender,
    api_session: Session,
    settings_row: Callable[[str, str], None],
) -> None:
    """The admin saves on the ledger, with their cookie; the site, which has none, reads
    the new pair on its very next call. Nothing is cached and nothing is typed."""
    settings_row("0.10", "0.30")
    _admin(api_session)
    _login(client, sender)
    saved = client.put(
        "/api/hub/referral-settings", json={"rate_freelancer": "0.125", "rate_company": "0.35"}
    )
    assert saved.status_code == 200, saved.text

    client.cookies.clear()
    answer = client.get(PATH)

    assert answer.json() == {"rate_freelancer": "0.1250", "rate_company": "0.3500"}


def test_it_needs_no_cookie_and_sets_none(
    client: TestClient, settings_row: Callable[[str, str], None]
) -> None:
    settings_row("0.10", "0.30")
    assert not client.cookies

    answer = client.get(PATH)

    assert answer.status_code == 200
    assert "set-cookie" not in answer.headers
    assert "cache-control" not in answer.headers
    # The admin's twin of this route answers 401 to the same caller: the gate is on that
    # one and this one is open on purpose.
    assert client.get("/api/hub/referral-settings").status_code == 401


def test_nothing_but_the_two_rates_is_in_the_answer(
    client: TestClient,
    sender: RecordingSender,
    api_session: Session,
    settings_row: Callable[[str, str], None],
) -> None:
    """The row an admin edited also holds who edited it and when; the member area's panel
    holds a code and who it brought in. None of that is on this route, in the body or in
    the contract the OpenAPI document publishes."""
    settings_row("0.10", "0.30")
    _admin(api_session)
    _login(client, sender)
    saved = client.put(
        "/api/hub/referral-settings", json={"rate_freelancer": "0.12", "rate_company": "0.35"}
    )
    assert saved.status_code == 200, saved.text
    assert api_session.scalar(select(ReferralSettings.updated_by)) is not None
    client.cookies.clear()

    body = client.get(PATH).json()

    assert set(body) == {"rate_freelancer", "rate_company"}
    schemas = create_app().openapi()["components"]["schemas"]
    assert set(schemas["PublicReferralRates"]["properties"]) == {
        "rate_freelancer",
        "rate_company",
    }


def test_a_missing_row_is_a_503_that_is_never_cached_and_never_invented(
    client: TestClient, api_session: Session, settings_row: Callable[[str, str], None]
) -> None:
    """No row, no figure: the page drops it. The route does not seed the defaults the way
    the member area's `get_settings` does, since a public GET must not write."""
    settings_row("0.10", "0.30")  # for the cleanup; the row itself goes at once
    api_session.execute(text("DELETE FROM referral_settings"))
    api_session.commit()

    answer = client.get(PATH)

    assert answer.status_code == 503
    assert answer.json() == {"detail": UNREADABLE}
    assert answer.headers["cache-control"] == "no-store"
    assert api_session.scalar(select(func.count()).select_from(ReferralSettings)) == 0


def test_reading_the_rates_does_not_spend_the_signups_budget(
    client: TestClient, settings_row: Callable[[str, str], None]
) -> None:
    """The limiter's bucket is the wizards' and the signup form's. A landing that reads the
    rates on every view must not leave its visitor with a 429 on the form."""
    settings_row("0.10", "0.30")

    codes = {client.get(PATH).status_code for _ in range(SIGNUPS_PER_MINUTE + 3)}

    assert codes == {200}
