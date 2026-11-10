"""An admin's personal tokens: minted once, resolved to the admin, refused uniformly."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from rebase_core.admin_tokens import INVALID_TOKEN, TOKEN_PREFIX, AdminTokenService
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.models import User


@pytest.fixture
def ivan(hub_engine: Engine, hub_session: Session) -> Iterator[User]:
    row = User(email="ivan@rebase.it", first_name="Ivan", last_name="Fiore", role="admin")
    hub_session.add(row)
    hub_session.commit()
    yield row
    hub_session.rollback()
    for table in ("admin_tokens", "users"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def test_a_token_is_minted_once_and_resolves_to_its_admin(ivan: User, hub_session: Session) -> None:
    tokens = AdminTokenService(hub_session)
    read, raw = tokens.create(ivan.id, "  Claude Code ")
    assert raw.startswith(TOKEN_PREFIX) and len(raw) > 30
    assert read.name == "Claude Code"
    assert read.prefix == raw[:12] and read.last_used_at is None and read.revoked_at is None
    stored = hub_session.execute(text("SELECT token_hash, prefix FROM admin_tokens")).one()
    assert raw not in stored[0] and stored[1] == read.prefix

    resolved = tokens.resolve(raw)
    assert resolved.id == ivan.id and resolved.email == "ivan@rebase.it"
    [listed] = tokens.list(ivan.id)
    assert listed.id == read.id and listed.last_used_at is not None


def test_every_failure_of_resolve_is_the_same_sentence(ivan: User, hub_session: Session) -> None:
    tokens = AdminTokenService(hub_session)
    _, raw = tokens.create(ivan.id, "Claude Code")
    for bad in (None, "", "Bearer x", "pgc_" + "a" * 40, TOKEN_PREFIX + "sconosciuto"):
        with pytest.raises(ValidationFailed) as refused:
            tokens.resolve(bad)
        assert refused.value.details["reason"] == INVALID_TOKEN

    revoked = tokens.revoke(ivan.id, tokens.list(ivan.id)[0].id)
    assert revoked.revoked_at is not None
    with pytest.raises(ValidationFailed) as refused:
        tokens.resolve(raw)
    assert refused.value.details["reason"] == INVALID_TOKEN
    # Revoking again keeps the first date, and the list still shows the row.
    assert tokens.revoke(ivan.id, revoked.id).revoked_at == revoked.revoked_at
    assert [row.id for row in tokens.list(ivan.id)] == [revoked.id]

    _, again = tokens.create(ivan.id, "Cursor")
    hub_session.execute(
        text("UPDATE users SET active = false WHERE id = :id"), {"id": str(ivan.id)}
    )
    hub_session.commit()
    with pytest.raises(ValidationFailed) as refused:
        tokens.resolve(again)
    assert refused.value.details["reason"] == INVALID_TOKEN


def test_a_demoted_owner_fails_a_resolve_the_same_way(ivan: User, hub_session: Session) -> None:
    """Since REB-278 the owner's role, not only `active`, decides: a token minted while
    the row was an admin must stop resolving the moment it is demoted."""
    tokens = AdminTokenService(hub_session)
    _, raw = tokens.create(ivan.id, "Claude Code")
    hub_session.execute(
        text("UPDATE users SET role = 'member' WHERE id = :id"), {"id": str(ivan.id)}
    )
    hub_session.commit()
    with pytest.raises(ValidationFailed) as refused:
        tokens.resolve(raw)
    assert refused.value.details["reason"] == INVALID_TOKEN


def test_a_token_belongs_to_its_admin_alone(ivan: User, hub_session: Session) -> None:
    tokens = AdminTokenService(hub_session)
    read, _ = tokens.create(ivan.id, "Claude Code")
    with pytest.raises(NotFound):
        tokens.revoke(uuid4(), read.id)
    assert tokens.list(uuid4()) == []
    with pytest.raises(NotFound):
        tokens.create(uuid4(), "Nessuno")
    with pytest.raises(ValidationFailed):
        tokens.create(ivan.id, "   ")


# ---- search and cursor pagination (REB-313) ----------------------------------------------


def test_search_hits_a_partial_token_name(ivan: User, hub_session: Session) -> None:
    tokens = AdminTokenService(hub_session)
    tokens.create(ivan.id, "Claude Code laptop")
    tokens.create(ivan.id, "Cursor desktop")

    by_name = tokens.list_page(ivan.id, q="Claude")
    assert [item.name for item in by_name.items] == ["Claude Code laptop"]


def test_the_cursor_walks_every_token_once_with_no_dupes_or_gaps(
    ivan: User, hub_session: Session
) -> None:
    tokens = AdminTokenService(hub_session)
    for i in range(7):
        tokens.create(ivan.id, f"Token {i}")

    full = tokens.list_page(ivan.id, limit=100)
    assert len(full.items) == 7

    seen: list[UUID] = []
    cursor: str | None = None
    for _ in range(20):  # generous upper bound: 7 rows over a page size of 2
        page = tokens.list_page(ivan.id, limit=2, cursor=cursor)
        seen.extend(item.id for item in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    else:
        pytest.fail("the cursor never reached its last page")

    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == {item.id for item in full.items}


def test_list_page_stays_scoped_to_the_caller_and_keeps_revoked_ones(
    ivan: User, hub_session: Session
) -> None:
    tokens = AdminTokenService(hub_session)
    read, _ = tokens.create(ivan.id, "Claude Code")
    tokens.revoke(ivan.id, read.id)

    assert [item.id for item in tokens.list_page(ivan.id).items] == [read.id]
    assert tokens.list_page(uuid4()).items == []


def test_a_malformed_cursor_is_refused(ivan: User, hub_session: Session) -> None:
    with pytest.raises(ValidationFailed):
        AdminTokenService(hub_session).list_page(ivan.id, cursor="not-a-valid-cursor")


def test_a_token_is_stamped_at_most_once_a_minute(ivan: User, hub_session: Session) -> None:
    """REB-803: a busy agent resolves on every request; `last_used_at` is the list's
    «ultimo uso», not an access log, and costs one write a minute."""
    tokens = AdminTokenService(hub_session)
    read, raw = tokens.create(ivan.id, "Claude Code")
    tokens.resolve(raw)
    [first] = tokens.list(ivan.id)
    assert first.last_used_at is not None
    tokens.resolve(raw)
    tokens.resolve(raw)
    hub_session.expire_all()
    [again] = tokens.list(ivan.id)
    assert again.last_used_at == first.last_used_at

    old = datetime.now(UTC) - timedelta(minutes=2)
    hub_session.execute(
        text("UPDATE admin_tokens SET last_used_at = :old WHERE id = :id"),
        {"old": old, "id": str(read.id)},
    )
    hub_session.commit()
    tokens.resolve(raw)
    hub_session.expire_all()
    [after] = tokens.list(ivan.id)
    assert after.last_used_at is not None and after.last_used_at > old + timedelta(minutes=1)


def test_an_expired_token_fails_like_a_revoked_one(ivan: User, hub_session: Session) -> None:
    tokens = AdminTokenService(hub_session)
    soon = datetime.now(UTC) + timedelta(days=30)
    read, raw = tokens.create(ivan.id, "Per un mese", expires_at=soon)
    assert read.expires_at == soon
    assert tokens.resolve(raw).id == ivan.id
    hub_session.execute(
        text("UPDATE admin_tokens SET expires_at = :past WHERE id = :id"),
        {"past": datetime.now(UTC) - timedelta(seconds=1), "id": str(read.id)},
    )
    hub_session.commit()
    with pytest.raises(ValidationFailed) as refused:
        tokens.resolve(raw)
    assert refused.value.details["reason"] == INVALID_TOKEN
    [listed] = tokens.list(ivan.id)
    assert listed.expires_at is not None


def test_an_expiry_is_a_future_instant_with_a_timezone(ivan: User, hub_session: Session) -> None:
    tokens = AdminTokenService(hub_session)
    for bad in (
        datetime.now(UTC) - timedelta(minutes=1),
        (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None),
    ):
        with pytest.raises(ValidationFailed) as refused:
            tokens.create(ivan.id, "Mai", expires_at=bad)
        assert refused.value.details["field"] == "expires_at"
    assert tokens.list(ivan.id) == []
    _, raw = tokens.create(ivan.id, "Senza scadenza")
    assert tokens.list(ivan.id)[0].expires_at is None
    assert tokens.resolve(raw).id == ivan.id
