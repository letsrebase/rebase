"""Amministratori: search and cursor pagination on `UserService.list_admins` (REB-313)."""

from uuid import UUID

import pytest
from admins import an_admin
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.errors import ValidationFailed
from rebase_core.models import User
from rebase_core.users import UserService


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def test_search_hits_a_partial_name_and_an_email_domain(clean: Session) -> None:
    service = UserService(clean)
    an_admin(clean, "ivan@rebase.it", "Ivan", "Fiore")
    an_admin(clean, "lorenzo@otherco.it", "Lorenzo", "Rossi")

    by_name = service.list_admins(q="Ros")
    assert [a.email for a in by_name.items] == ["lorenzo@otherco.it"]

    by_domain = service.list_admins(q="otherco.it")
    assert [a.email for a in by_domain.items] == ["lorenzo@otherco.it"]


def test_the_cursor_walks_every_admin_once_with_no_dupes_or_gaps(clean: Session) -> None:
    service = UserService(clean)
    for i in range(7):
        an_admin(clean, f"admin{i}@rebase.it", f"Admin{i}")

    full = service.list_admins(limit=100)
    assert len(full.items) == 7

    seen: list[UUID] = []
    cursor: str | None = None
    for _ in range(20):  # generous upper bound: 7 rows over a page size of 2
        page = service.list_admins(limit=2, cursor=cursor)
        seen.extend(item.id for item in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    else:
        pytest.fail("the cursor never reached its last page")

    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == {item.id for item in full.items}


def test_with_no_term_the_list_stays_oldest_first(clean: Session) -> None:
    """ORB-123: unlike Talents and Companies, a bare (no `q`) admins list reads oldest
    first, so the page still reads as a history."""
    service = UserService(clean)
    first = an_admin(clean, "first@rebase.it", "First")
    second = an_admin(clean, "second@rebase.it", "Second")

    page = service.list_admins()
    assert [a.id for a in page.items] == [first, second]


def test_a_malformed_cursor_is_refused(clean: Session) -> None:
    with pytest.raises(ValidationFailed):
        UserService(clean).list_admins(cursor="not-a-valid-cursor")


def test_a_non_admin_never_appears_in_the_list(clean: Session) -> None:
    clean.add(
        User(email="membro@rebase.it", first_name="Membro", last_name="Utente", role="member")
    )
    clean.commit()
    admin = an_admin(clean, "ivan@rebase.it", "Ivan")

    page = UserService(clean).list_admins()
    assert [a.id for a in page.items] == [admin]
