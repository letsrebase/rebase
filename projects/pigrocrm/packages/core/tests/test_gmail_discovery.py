"""Discovering who at a customer one has actually written to.

Spec 4.2 makes the address book the relevance rule, and it holds: nothing in this file
*stores* a message. What it adds is the step before the address book has anything in it.
A brand-new customer with a website and no people is a dead end for the backfill -- it
has no address to ask about -- and the only way out used to be guessing addresses one
at a time. Discovery asks Gmail one question scoped to the customer's own domain, taken
from the record and never typed, and answers with the addresses that turned up so a
person can add them. The stored mirror widens only when somebody does.
"""

from urllib.parse import parse_qs, urlparse

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.gmail.query import (
    build_domain_clause,
    customer_domain,
    discovery_query,
    messages_list_url,
)

# --- the query, as pure functions --------------------------------------------------


def test_a_domain_clause_asks_both_directions_with_an_at_sign() -> None:
    """`@` is load-bearing: `from:example.com` is a free-text search over display
    names, which is the broad search spec 4 forbids. `from:@example.com` is a match on
    the address itself, and it is also what lets `messages_list_url` accept it."""
    assert build_domain_clause("example.com") == "(from:@example.com OR to:@example.com)"


def test_a_domain_is_lowercased_and_stripped() -> None:
    assert build_domain_clause("  Example.COM ") == "(from:@example.com OR to:@example.com)"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "example",
        "example.com OR from:ceo@rival.com",
        "example.com)",
        "@example.com",
        "marco@example.com",
        "example labs.com",
    ],
)
def test_a_domain_that_could_break_out_of_the_query_is_refused(bad: str) -> None:
    with pytest.raises(ValidationFailed):
        build_domain_clause(bad)


def test_the_discovery_query_is_one_the_list_url_accepts() -> None:
    url = messages_list_url(discovery_query("example.com"))
    q = parse_qs(urlparse(url).query)["q"][0]
    assert q == "(from:@example.com OR to:@example.com)"


# --- where the domain comes from ---------------------------------------------------


@pytest.mark.parametrize(
    ("sito_web", "expected"),
    [
        ("https://www.example.com/", "example.com"),
        ("http://example.com", "example.com"),
        ("example.com", "example.com"),
        ("www.example.com/about-us", "example.com"),
        ("HTTPS://WWW.Example.com", "example.com"),
    ],
)
def test_the_domain_is_read_from_the_website(sito_web: str, expected: str) -> None:
    assert customer_domain(sito_web=sito_web, email=None) == expected


def test_without_a_website_the_domain_is_read_from_the_customer_email() -> None:
    assert customer_domain(sito_web=None, email="info@example.com") == "example.com"


def test_the_website_wins_over_the_email_when_both_exist() -> None:
    assert customer_domain(sito_web="https://example.com", email="x@gmail.com") == "example.com"


@pytest.mark.parametrize("email", ["ceo@gmail.com", "ceo@outlook.com", "ceo@yahoo.it"])
def test_a_webmail_domain_is_not_a_customer_domain(email: str) -> None:
    """`from:@gmail.com` is half the planet. The result would not be one customer's
    correspondents, so it is refused rather than answered."""
    assert customer_domain(sito_web=None, email=email) is None


def test_nothing_to_derive_from_is_none_not_an_error() -> None:
    assert customer_domain(sito_web=None, email=None) is None
    assert customer_domain(sito_web="", email="") is None


# --- the service ----------------------------------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from fakes.fake_gmail import FakeGmail, FakeMessage  # noqa: E402
from fakes.gmail_fixtures import MAILBOX, actor_for, connected_account, sync_service  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from pigrocrm.core.actor import Actor  # noqa: E402
from pigrocrm.core.customers.models import Customer  # noqa: E402
from pigrocrm.core.errors import AgentForbidden, Conflict, NotFound  # noqa: E402
from pigrocrm.core.gmail import sync as sync_module  # noqa: E402
from pigrocrm.core.gmail.errors import GmailUnavailable  # noqa: E402
from pigrocrm.core.gmail.models import GmailMessage  # noqa: E402

DOMAIN_CLAUSE = "(from:@example.com OR to:@example.com)"


def _mail(
    index: int, *, frm: str, to: str, thread: str, days_ago: float = 1.0, subject: str = ""
) -> FakeMessage:
    stamp = int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp() * 1000)
    return FakeMessage(
        id=f"m{index}",
        thread_id=thread,
        headers={
            "From": frm,
            "To": to,
            "Subject": subject or f"Oggetto {index}",
            "Message-ID": f"<msg{index}@example.it>",
        },
        body_text=f"corpo riservato {index}",
        internal_date_ms=stamp,
    )


def _example(session: Session, **overrides: object) -> Customer:
    fields: dict[str, object] = {
        "ragione_sociale": "Example Ltd",
        "sito_web": "https://www.example.com/",
    }
    fields.update(overrides)
    customer = Customer(**fields)
    session.add(customer)
    session.flush()
    return customer


def test_discovery_answers_who_at_the_domain_wrote_or_was_written_to(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _example(db_session)
    fake = FakeGmail()
    fake.messages["m1"] = _mail(
        1, frm="Marco Bianchi <marco@example.com>", to=MAILBOX, thread="t1", days_ago=30
    )
    fake.messages["m2"] = _mail(
        2,
        frm=MAILBOX,
        to="marco@example.com, Sarah Miller <sarah@example.com>",
        thread="t1",
        days_ago=2,
    )
    fake.messages["m3"] = _mail(3, frm="estraneo@altrove.com", to=MAILBOX, thread="t9")

    report = sync_service(db_session, fake).discover(customer.id, actor=actor_for(account))

    assert report.dominio == "example.com"
    assert report.threads_scanned == 1
    found = {row.indirizzo: row for row in report.corrispondenti}
    assert set(found) == {"marco@example.com", "sarah@example.com"}
    marco = found["marco@example.com"]
    assert marco.nome == "Marco Bianchi"
    assert marco.messaggi == 2
    assert marco.ultimo_messaggio == datetime.fromtimestamp(
        fake.messages["m2"].internal_date_ms / 1000, tz=UTC
    )
    assert found["sarah@example.com"].nome == "Sarah Miller"
    assert found["sarah@example.com"].messaggi == 1


def test_the_most_frequent_correspondent_comes_first(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _example(db_session)
    fake = FakeGmail()
    fake.messages["m1"] = _mail(1, frm="sarah@example.com", to=MAILBOX, thread="t1")
    fake.messages["m2"] = _mail(2, frm="marco@example.com", to=MAILBOX, thread="t2")
    fake.messages["m3"] = _mail(3, frm="marco@example.com", to=MAILBOX, thread="t3")

    report = sync_service(db_session, fake).discover(customer.id, actor=actor_for(account))

    assert [row.indirizzo for row in report.corrispondenti] == [
        "marco@example.com",
        "sarah@example.com",
    ]


def test_discovery_stores_nothing_and_moves_no_watermark(db_session: Session) -> None:
    """Spec 4.2 survives intact: the mirror widens when a person adds an address, not
    when discovery finds one."""
    account = connected_account(db_session)
    customer = _example(db_session)
    fake = FakeGmail()
    fake.messages["m1"] = _mail(1, frm="marco@example.com", to=MAILBOX, thread="t1")

    sync_service(db_session, fake).discover(customer.id, actor=actor_for(account))

    assert db_session.execute(select(func.count()).select_from(GmailMessage)).scalar_one() == 0
    db_session.refresh(account)
    assert account.sync_watermark is None
    assert account.last_sync_at is None


def test_every_listing_discovery_issues_carries_the_domain_clause_and_nothing_else(
    db_session: Session,
) -> None:
    """The domain came from the record. Nothing typed by the caller reaches the `q`, and
    the connected mailbox is never asked about -- the widening that looks harmless."""
    account = connected_account(db_session)
    customer = _example(db_session)
    fake = FakeGmail()
    fake.messages["m1"] = _mail(1, frm="marco@example.com", to=MAILBOX, thread="t1")

    sync_service(db_session, fake).discover(customer.id, actor=actor_for(account))

    listings = [request for request in fake.requests if request.is_messages_list]
    assert listings, "discovery issued no listing at all, so this test proves nothing"
    assert all(request.q == DOMAIN_CLAUSE for request in listings)
    assert not any(request.is_messages_send for request in fake.requests)


def test_the_connected_mailbox_is_never_reported_as_a_correspondent(db_session: Session) -> None:
    """If the owner's own mailbox is at the customer's domain -- a contractor with an
    address there -- it is still the owner, not somebody to add to the CRM."""
    account = connected_account(db_session, email_address="ivan@example.com")
    customer = _example(db_session)
    fake = FakeGmail()
    fake.messages["m1"] = _mail(1, frm="marco@example.com", to="ivan@example.com", thread="t1")

    report = sync_service(db_session, fake).discover(customer.id, actor=actor_for(account))

    assert [row.indirizzo for row in report.corrispondenti] == ["marco@example.com"]


def test_addresses_already_in_the_crm_are_marked_as_known(db_session: Session) -> None:
    """The answer is a to-do list, so it says which items are already done."""
    from pigrocrm.core.people.models import Person

    account = connected_account(db_session)
    customer = _example(db_session)
    db_session.add(Person(nome="Marco", email="Marco@Example.com", customer_id=customer.id))
    db_session.flush()
    fake = FakeGmail()
    fake.messages["m1"] = _mail(1, frm="marco@example.com", to=MAILBOX, thread="t1")
    fake.messages["m2"] = _mail(2, frm="sarah@example.com", to=MAILBOX, thread="t2")

    report = sync_service(db_session, fake).discover(customer.id, actor=actor_for(account))

    known = {row.indirizzo: row.gia_in_anagrafica for row in report.corrispondenti}
    assert known == {"marco@example.com": True, "sarah@example.com": False}


def test_a_customer_with_no_domain_is_a_conflict_not_an_empty_answer(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _example(db_session, sito_web=None, email="ceo@gmail.com")
    with pytest.raises(Conflict):
        sync_service(db_session, FakeGmail()).discover(customer.id, actor=actor_for(account))


def test_an_archived_customer_is_not_found(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _example(db_session, deleted_at=datetime.now(UTC))
    with pytest.raises(NotFound):
        sync_service(db_session, FakeGmail()).discover(customer.id, actor=actor_for(account))


def test_an_agent_without_full_access_is_refused_before_gmail_is_asked(
    db_session: Session,
) -> None:
    """Discovery spends the owner's Gmail quota under the owner's consent, exactly like
    a backfill. The `mcp_full_access` switch is what says this installation wants its
    agent to be able to do that."""
    account = connected_account(db_session)
    customer = _example(db_session)
    fake = FakeGmail()
    fake.messages["m1"] = _mail(1, frm="marco@example.com", to=MAILBOX, thread="t1")
    agent = Actor(id=account.user_id, type="mcp", role="admin")

    with pytest.raises(AgentForbidden):
        sync_service(db_session, fake).discover(customer.id, actor=agent)
    assert fake.requests == []

    trusted = Actor(id=account.user_id, type="mcp", role="admin", full_access=True)
    report = sync_service(db_session, fake).discover(customer.id, actor=trusted)
    assert [row.indirizzo for row in report.corrispondenti] == ["marco@example.com"]


def test_gmail_failing_is_a_sentence_to_retry_not_a_server_error(db_session: Session) -> None:
    """A press on the customer's Email tab reaches this (REB-766): Gmail failing after
    every retry answers the same retry sentence the proposals do, never a 500."""
    account = connected_account(db_session)
    customer = _example(db_session)
    fake = FakeGmail()
    service = sync_service(db_session, fake)
    service._access_token = lambda _account: "ya29.token"  # type: ignore[method-assign]
    fake.fail_with = [(503, b"{}", {})] * 10
    with pytest.raises(GmailUnavailable):
        service.discover(customer.id, actor=actor_for(account))


def test_only_the_most_recent_conversations_are_read(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read stops at the cap, so a customer with years of mail does not turn one
    press into a minutes-long request."""
    monkeypatch.setattr(sync_module, "DISCOVER_MAX_THREADS", 2)
    account = connected_account(db_session)
    customer = _example(db_session)
    fake = FakeGmail()
    for index, who in enumerate(("nuovo", "medio", "vecchio"), start=1):
        fake.messages[f"m{index}"] = _mail(
            index, frm=f"{who}@example.com", to=MAILBOX, thread=f"t{index}", days_ago=index
        )

    report = sync_service(db_session, fake).discover(customer.id, actor=actor_for(account))

    # Which two is Gmail's order (newest first); the fake lists oldest first, so the
    # test claims the cap and not the order.
    assert report.threads_scanned == 2
    assert len(report.corrispondenti) == 2
