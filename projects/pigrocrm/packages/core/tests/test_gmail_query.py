"""Relevance as a mechanism.

Spec 4.1 is not a policy: *"every call to `users.messages.list` carries a `q`
containing at least one email address known to the CRM. A `list` without a `q` is a
bug."* A comment saying so protects nothing, so there are two independent guards and
both are here rather than in a convention:

1. `messages_list_url` **refuses** a `q` with no address-bearing clause, so the bug is
   unconstructible rather than merely discouraged.
2. `test_no_other_module_can_build_a_gmail_listing_url` walks the AST of every source
   file in the repository and fails if any of them contains the Gmail API host or a
   `/messages` path. Guard 1 is one edit away from being deleted; guard 2 is what makes
   deleting it insufficient, because a caller that bypasses this module has to write a
   URL somewhere and there is nowhere left to write it.

B1-8 adds the third: reading `FakeGmail.requests` after a full sync and asserting the
property over every request the transport actually made.
"""

import ast
import inspect
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.gmail import query as module
from pigrocrm.core.gmail.query import (
    ADDRESS_BATCH_DEFAULT,
    build_address_clause,
    build_list_queries,
    message_get_url,
    messages_list_url,
    rfc822msgid_query,
    thread_get_url,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOTS = (
    REPO_ROOT / "packages" / "core" / "src",
    REPO_ROOT / "apps" / "api" / "src",
    REPO_ROOT / "apps" / "mcp" / "src",
)
QUERY_MODULE = REPO_ROOT / "packages" / "core" / "src" / "pigrocrm" / "core" / "gmail" / "query.py"
# Any one of these in a string literal means somebody is assembling a Gmail URL by
# hand. `/messages` also catches `f"{GMAIL_API_ROOT}/messages?q=..."`, which would slip
# past a scan for the host alone.
URL_FRAGMENTS = ("gmail.googleapis.com", "/messages", "/threads")

# The one class of false positive the fragments above cannot tell apart from a Gmail
# URL: a route path on *our own* API. `GET /api/gmail/messages` reads the CRM's stored
# copy and never leaves the database, so it is not a listing and cannot escape the
# address filter -- but its path literal is `/messages` all the same.
#
# Named as exact (file, literal) pairs rather than by excluding the file, so a *second*
# literal in the same module still fails, and rechecked below so a stale entry cannot
# quietly outlive the route it describes. Concatenating the string to slip past the
# scan was the alternative and is worse: it would leave the guard green while teaching
# the next reader the way around it.
OUR_OWN_ROUTE_PATHS = frozenset(
    {
        (
            REPO_ROOT / "apps" / "api" / "src" / "pigrocrm_api" / "routers" / "gmail.py",
            "/messages",
        ),
    }
)


def test_the_default_batch_is_twenty_addresses() -> None:
    # Twenty, because Gmail's `q` has a practical length limit and twenty addresses
    # with two clauses each fit inside it with margin.
    assert ADDRESS_BATCH_DEFAULT == 20


def test_each_address_contributes_both_directions() -> None:
    clause = build_address_clause(["ada@acme.it", "bob@acme.it"])
    assert clause == "(from:ada@acme.it OR to:ada@acme.it OR from:bob@acme.it OR to:bob@acme.it)"


def test_an_empty_roster_produces_no_query_at_all() -> None:
    """Not an empty query -- no query. A sync with nothing to look for must issue zero
    requests, never one unfiltered request."""
    assert build_list_queries([], after_epoch=1_700_000_000) == ()


def test_addresses_are_batched_and_every_batch_keeps_the_filter() -> None:
    addresses = [f"user{n}@acme.it" for n in range(45)]
    queries = build_list_queries(addresses, after_epoch=1_700_000_000, batch_size=20)
    assert len(queries) == 3
    for query in queries:
        assert query.startswith("(from:")
        assert " after:1700000000" in query
        assert query.count("from:") == query.count("to:")
    # Every address appears exactly once across the batches: a dropped address is a
    # silently missing conversation, and a duplicated one is a wasted page of quota.
    for address in addresses:
        appearances = sum(query.count(f"from:{address} ") for query in queries)
        assert appearances == 1, address


def test_a_repeated_address_is_asked_for_once() -> None:
    """The roster is built from customers, people and deals, so the same address can
    arrive three times. Three identical clauses would cost a third of the `q`'s length
    limit for nothing."""
    query = build_list_queries(
        ["ada@acme.it", "ADA@acme.it", " ada@acme.it "], after_epoch=1_700_000_000
    )[0]
    assert query.count("from:ada@acme.it") == 1


def test_after_is_epoch_seconds_and_never_a_date() -> None:
    """A date loses the hours and forces re-reading a whole day every cycle."""
    query = build_list_queries(["ada@acme.it"], after_epoch=1_723_766_400)
    assert query[0].endswith(" after:1723766400")


def test_an_address_that_could_break_out_of_the_query_is_refused() -> None:
    hostile = [
        "ada@acme.it OR from:ceo@rival.com",
        "ada@acme.it)",
        'ada"@acme.it',
        "ada acme@it",
        "ada@acme.it after:0",
    ]
    for address in hostile:
        with pytest.raises(ValidationFailed) as caught:
            build_address_clause([address])
        assert caught.value.details["field"] == "address"


def test_every_query_this_module_builds_is_one_it_would_also_accept() -> None:
    """The two guards have to agree. A builder that produced a `q` the URL builder then
    refused would fail at the first sync rather than here."""
    for query in build_list_queries([f"user{n}@acme.it" for n in range(3)], after_epoch=0):
        assert messages_list_url(query)
    assert messages_list_url(rfc822msgid_query("<abc.123@crm.example.it>"))


def test_the_list_url_refuses_a_query_without_an_address_clause() -> None:
    """Guard one of two. The only function that can build a messages.list URL will not
    build one that does not filter by a known address -- so the bug spec 4.1 names is
    not merely discouraged, it is unconstructible."""
    unfiltered = [
        "",
        "   ",
        "after:1700000000",
        "is:unread",
        "subject:fattura",
        "has:attachment after:1700000000",
        # Shaped like a filter, filtering nothing: `from:` with no address behind it is
        # a Gmail free-text search over the sender's display name.
        "from:acme",
    ]
    for bad in unfiltered:
        with pytest.raises(ValidationFailed, match="senza un filtro"):
            messages_list_url(bad)


def test_the_list_url_accepts_a_query_that_does_filter() -> None:
    url = messages_list_url(
        "(from:ada@acme.it OR to:ada@acme.it) after:1700000000", page_token="tok"
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "gmail.googleapis.com"
    assert parsed.path == "/gmail/v1/users/me/messages"
    assert query["q"] == ["(from:ada@acme.it OR to:ada@acme.it) after:1700000000"]
    assert query["pageToken"] == ["tok"]
    assert query["maxResults"] == ["100"]


def test_the_reconciliation_query_is_the_one_exception_and_is_still_specific() -> None:
    """rfc822msgid: names one exact message we ourselves generated. It is not a search
    of the mailbox, which is why it is allowed past the address-filter guard."""
    query = rfc822msgid_query("<abc.123@crm.example.it>")
    assert query == "rfc822msgid:abc.123@crm.example.it"
    assert messages_list_url(query)


def test_a_message_id_that_is_not_one_is_refused() -> None:
    """The one query not built from the roster is also the one an attacker would most
    like to steer, so it is checked as strictly as an address."""
    for hostile in ["<abc OR from:ceo@rival.com>", "<>", "<abc.123@crm.example.it> is:unread"]:
        with pytest.raises(ValidationFailed):
            rfc822msgid_query(hostile)


def test_no_helper_can_build_a_url_that_takes_a_caller_supplied_search_string() -> None:
    """Spec 8.2 and 12: no surface in this slice accepts a Gmail search string. The
    module exposes exactly these builders, and none of them takes free text."""
    exported = [
        name
        for name, value in vars(module).items()
        if not name.startswith("_")
        and inspect.isfunction(value)
        # Defined here, not merely imported here: `urlencode` is a public function in
        # this module's namespace and has nothing to do with the surface being fixed.
        and value.__module__ == module.__name__
    ]
    assert sorted(exported) == [
        # Two ids of Gmail's own, one of them straight out of a message payload, and no
        # free text: what `attachment_get_url` interpolates is checked more strictly
        # than anything a person types, because it goes into a *path*.
        "attachment_get_url",
        "build_address_clause",
        # The three discovery builders take a *domain*, checked against an alphabet
        # narrower than a hostname's, or derive it from the customer record -- never a
        # search string. See `test_gmail_discovery.py`.
        "build_domain_clause",
        "build_list_queries",
        "customer_domain",
        "discovery_query",
        # A predicate over a domain the caller already holds; it builds nothing.
        "is_provider_domain",
        "message_get_url",
        "messages_list_url",
        "rfc822msgid_query",
        # The customer proposals (REB-223): the connected mailbox's own address, checked
        # like any roster address, and a number; and a thread id with a fixed set of
        # headers. See `test_gmail_suggestions.py`.
        "sent_since_query",
        "thread_get_url",
        "thread_metadata_url",
    ]


def test_thread_get_asks_for_the_full_format() -> None:
    url = thread_get_url("thread-1")
    assert url.endswith("/threads/thread-1?format=full")


def test_a_gmail_id_that_is_not_one_is_refused() -> None:
    """An id arrives from Gmail's own listing, so a hostile one means the response was
    tampered with -- but path traversal into another endpoint is cheap to make
    impossible and expensive to discover later."""
    for hostile in ["../../users/other/messages", "thread 1", "thread/1", ""]:
        with pytest.raises(ValidationFailed):
            thread_get_url(hostile)
        with pytest.raises(ValidationFailed):
            message_get_url(hostile)


# --- guard two: nowhere else may build one of these URLs -----------------------------


def _string_constants(path: Path) -> list[str]:
    """String literals only, from the AST.

    Not a `grep`: this file, the module's own docstring and half the comments in the
    slice discuss `/messages` in prose, and a scan that could not tell a sentence from
    a URL would either be noisy enough to be disabled or narrow enough to be useless.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_no_other_module_can_build_a_gmail_listing_url() -> None:
    """Guard two. `messages_list_url` refusing an unfiltered `q` is worth exactly as
    much as the guarantee that it is the only way to reach `users.messages.list` at
    all -- otherwise the next caller writes its own f-string and the refusal guards a
    function nobody calls.

    Docstrings are excluded by the AST walk's own shape: they are `ast.Constant`s too,
    so a module that merely *documents* the endpoint would fail this. That is deliberate
    and cheap to satisfy -- write the path with a space in it, or name the helper -- and
    it is the price of not having to distinguish a URL from a sentence about one.
    """
    offenders: list[str] = []
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if path == QUERY_MODULE:
                continue
            for literal in _string_constants(path):
                if (path, literal) in OUR_OWN_ROUTE_PATHS:
                    continue
                for fragment in URL_FRAGMENTS:
                    if fragment in literal:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: {literal!r}")
    assert offenders == [], (
        "solo gmail/query.py può costruire un URL di Gmail: un elenco costruito "
        "altrove sfugge al filtro sugli indirizzi noti (spec 4.1). Usa "
        "messages_list_url / thread_get_url / message_get_url.\n" + "\n".join(offenders)
    )


def test_every_allowed_route_path_is_still_a_route_path_in_that_file() -> None:
    """The allowlist's own guard. An entry that outlived its route would silently widen
    the scan's blind spot by exactly one literal in exactly the file somebody is most
    likely to add a Gmail call to."""
    for path, literal in OUR_OWN_ROUTE_PATHS:
        assert path.exists(), f"{path} non esiste piu': l'eccezione va rimossa"
        assert literal in _string_constants(path), (
            f"{path.relative_to(REPO_ROOT)} non contiene piu' {literal!r}: "
            "l'eccezione va rimossa insieme alla rotta"
        )


def test_the_guard_above_would_notice_a_hand_built_listing_url(tmp_path: Path) -> None:
    """A scan that cannot fail is a scan nobody will notice breaking. This runs the
    same reader over a file that does exactly what the guard forbids."""
    offender = tmp_path / "sync.py"
    offender.write_text(
        'URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages?q=is:unread"\n',
        encoding="utf-8",
    )
    literals = _string_constants(offender)
    assert any(fragment in literal for literal in literals for fragment in URL_FRAGMENTS)


def test_a_full_backfill_has_no_after_clause_at_all() -> None:
    """Found in production: Gmail answers *nothing* to `after:0`. The fake accepted it as
    "since the epoch", every test passed, and `backfill(full=True)` -- the one call spec
    4.4 makes a person ask for by hand -- returned an empty report against a mailbox
    with the very threads discovery had just listed. "No horizon" is spelled by leaving
    the clause out, not by naming the beginning of time."""
    (query,) = build_list_queries(["ada@acme.it"], after_epoch=0)
    assert query == "(from:ada@acme.it OR to:ada@acme.it)"
    assert "after:" not in query
    # And a real horizon still gets its clause.
    (bounded,) = build_list_queries(["ada@acme.it"], after_epoch=1700000000)
    assert bounded.endswith(" after:1700000000")
