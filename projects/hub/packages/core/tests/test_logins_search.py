"""Accessi: search and cursor pagination on `LoginService.stats`'s `recent` (REB-313)."""

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.errors import ValidationFailed
from rebase_core.logins import LoginService
from rebase_core.models import Login, User


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM logins"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def _login(session: Session, email: str, first_name: str, last_name: str = "Utente") -> Login:
    user = User(email=email, first_name=first_name, last_name=last_name)
    session.add(user)
    session.flush()
    row = Login(user_id=user.id)
    session.add(row)
    session.commit()
    return row


def test_search_hits_a_partial_name_and_an_email_domain(clean: Session) -> None:
    service = LoginService(clean)
    _login(clean, "ada@studio.it", "Ada", "Lovelace")
    _login(clean, "bob@otherco.it", "Bob", "Smith")

    by_name = service.stats(q="Love").recent
    assert [row.email for row in by_name] == ["ada@studio.it"]

    by_domain = service.stats(q="otherco.it").recent
    assert [row.email for row in by_domain] == ["bob@otherco.it"]


def test_search_leaves_the_aggregate_counters_unaffected(clean: Session) -> None:
    """The four counters (REB-313) always read the whole table, not the filtered page."""
    service = LoginService(clean)
    _login(clean, "ada@studio.it", "Ada", "Lovelace")
    _login(clean, "bob@otherco.it", "Bob", "Smith")

    stats = service.stats(q="Love")
    assert (stats.total, stats.members) == (2, 2)
    assert len(stats.recent) == 1


def test_the_cursor_walks_every_login_once_with_no_dupes_or_gaps(clean: Session) -> None:
    service = LoginService(clean)
    for i in range(7):
        _login(clean, f"user{i}@studio.it", f"User{i}")

    full = service.stats(limit=100)
    assert len(full.recent) == 7

    seen: list[UUID] = []
    cursor: str | None = None
    for _ in range(20):  # generous upper bound: 7 rows over a page size of 2
        page = service.stats(limit=2, cursor=cursor)
        seen.extend(row.id for row in page.recent)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    else:
        pytest.fail("the cursor never reached its last page")

    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == {row.id for row in full.recent}


def test_a_malformed_cursor_is_refused(clean: Session) -> None:
    with pytest.raises(ValidationFailed):
        LoginService(clean).stats(cursor="not-a-valid-cursor")
