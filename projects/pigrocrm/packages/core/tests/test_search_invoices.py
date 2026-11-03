"""§8.1's fifth branch: `memo` by trigram, and `(anno, numero)` by equality when the
term has the shape of a fiscal number.

**This closes a live defect, not a gap in coverage.** `SearchEntity` has declared five
entities since Task A7 and only four were ever queried, so searching an invoice number
answered «Nessun risultato» when the truth was "invoices were not looked at" -- a silent
partial result, which is the exact family §8.6 and criterion 5 exist to forbid. Task A14
found it; this file is what makes the answer true.

The shape cases are the interesting half. `2026/7` and `7/2026` are both how people write
the same number, and `007` means "invoice seven" -- while `007` searched as a trigram over
`memo` means "every invoice whose description contains 007", which is not an answer.

**A bare number is never shorter than three characters**, because `SearchQuery.deadline` is
`min_length=3` (§8.2: a trigram index cannot serve a pattern with no trigram in it, and
the palette issues no request below the threshold). So the reachable spelling of "invoice
seven" is `007`, or `2026/7`, and `7` is not a term this branch can ever be asked. The
parser is tested on `7` all the same, because it is a pure function and the bound belongs
to the query schema rather than to it -- but every search below uses a term the palette
could actually send.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.search.schemas import SearchGroup, SearchQuery, parse_fiscal_number
from pigrocrm.core.search.service import SearchService

READONLY = Actor(id=uuid7(), type="user", role="readonly")


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("2026/7", (2026, 7)),
        ("7/2026", (2026, 7)),
        ("2026-7", (2026, 7)),
        ("7-2026", (2026, 7)),
        ("7", (None, 7)),
        ("0007", (None, 7)),
        ("2026/007", (2026, 7)),
        ("999999", (None, 999999)),
        # A four-digit term is a number, not a year: there is no separator, so there is no
        # year to read. Invoice 2026 is a real invoice and this is the honest reading; a
        # `memo` containing "2026" is not reachable by that term, which is the cost of
        # the two paths being exclusive and is stated in `parse_fiscal_number`'s docstring.
        ("2026", (None, 2026)),
        # Not fiscal numbers.
        ("abc", None),
        ("2026/", None),
        ("/7", None),
        ("2026/7/3", None),
        ("999999999", None),  # beyond any plausible invoice number
        ("1000000", None),  # one past the bound, so the bound is the reason and not the length
        ("2026/0", None),  # invoice numbering starts at 1
        ("1999/7", None),  # before the bound, and a plausible free-text fragment
        ("3000/7", None),
        ("", None),
        # `re.fullmatch`, never `re.match` with `$`: `$` matches before a trailing newline,
        # so `7\n` would parse as invoice 7 and a term the user never typed would reach the
        # equality path. Whitespace is stripped, a newline *inside* is not a number.
        ("7\n", (None, 7)),
        ("7\n8", None),
    ],
)
def test_parse_fiscal_number(term: str, expected: tuple[int | None, int] | None) -> None:
    assert parse_fiscal_number(term) == expected


@pytest.fixture
def invoices(db_session: Session) -> Customer:
    """Five invoices on one customer, and every one of them is load-bearing.

    `2026/7` and `2025/7` share a number across two years, which is what makes "a bare
    number finds it in every year" a real assertion rather than a lookup. `2026/123` and
    `2025/400` are the exclusivity pair: the second's *causale* contains "123" while its
    number does not, so a search for `123` that also trigrammed the causale would return
    two rows instead of one.
    """
    customer = Customer(legal_name="Cliente Srl", country="IT", custom_fields={})
    db_session.add(customer)
    db_session.flush()
    for year, number, memo in (
        (2026, 7, "Progettazione impianti elettrici"),
        (2026, 8, "Collaudo cabina di trasformazione"),
        (2025, 7, "Manutenzione annuale"),
        (2026, 123, "Ristrutturazione uffici"),
        (2025, 400, "Rata 123 del contratto quadro"),
    ):
        db_session.add(
            Invoice(
                customer_id=customer.id,
                type="invoice",
                status="issued",
                payment_status="unpaid",
                year=year,
                number=number,
                memo=memo,
                taxable_amount=Decimal("1000.00"),
                vat_amount=Decimal("0.00"),
                stamp_duty=Decimal("0.00"),
                total=Decimal("1000.00"),
                issue_date=date(year, 3, 1),
                sdi_document_type="TD01",
                currency="EUR",
                custom_fields={},
            )
        )
    db_session.flush()
    return customer


def _group(db_session: Session, term: str) -> SearchGroup:
    results = SearchService(db_session).search_everything(SearchQuery(deadline=term), READONLY)
    return next(group for group in results.groups if group.entity == "invoice")


def test_the_invoice_group_is_always_present_and_fifth(
    db_session: Session, invoices: Customer
) -> None:
    """A missing group and an empty group render identically in a palette (§8.6), and the
    order is fixed because a palette whose sections move between keystrokes cannot be
    driven with the keyboard."""
    results = SearchService(db_session).search_everything(SearchQuery(deadline="zzzqqq"), READONLY)
    assert [group.entity for group in results.groups] == [
        "customer",
        "person",
        "deal",
        "document",
        "invoice",
        "attivita",
    ]
    # Fifth since Task C12, and since REB-723 no longer last: the attività branch follows it.
    invoices_group = next(group for group in results.groups if group.entity == "invoice")
    assert invoices_group.total == 0
    assert invoices_group.hits == []


def test_the_memo_is_searchable_by_to_fragment(db_session: Session, invoices: Customer) -> None:
    group = _group(db_session, "impianti")
    assert group.total == 1
    assert group.total_and_a_minimum is False
    assert group.hits[0].label == "2026/7 — Progettazione impianti elettrici"
    assert group.hits[0].field == "memo"


def test_a_full_fiscal_number_finds_exactly_that_invoice(
    db_session: Session, invoices: Customer
) -> None:
    group = _group(db_session, "2026/7")
    assert group.total == 1
    assert group.hits[0].label.startswith("2026/7")
    # An exact fiscal-number match is a code match: §8.5's "un match su un codice è voluto",
    # weight 1.00, score 1.00 -- the same value an exact `vat_number` match produces.
    assert group.hits[0].score == Decimal("1.0000")
    assert group.hits[0].field == "number"


@pytest.mark.parametrize("term", ["7/2026", "2026-7", "2026/007"])
def test_every_spelling_of_the_same_number_finds_the_same_invoice(
    db_session: Session, invoices: Customer, term: str
) -> None:
    group = _group(db_session, term)
    assert group.total == 1
    assert group.hits[0].label.startswith("2026/7")


def test_a_bare_number_finds_that_number_in_every_year(
    db_session: Session, invoices: Customer
) -> None:
    """`007` has no year, so it matches invoice 7 of every year. Guessing the current year
    would hide last year's invoice 7 with nothing on screen to say so -- a partial result
    presented as a complete one, which is the defect this branch exists to close."""
    group = _group(db_session, "007")
    assert group.total == 2
    assert {hit.label.split(" ")[0] for hit in group.hits} == {"2026/7", "2025/7"}
    # Most recent year first: the same "what was touched most recently is more likely what
    # is wanted" rule the trigram branches sort by, expressible here as the year.
    assert group.hits[0].label.startswith("2026/7")


def test_to_fiscal_number_term_does_not_also_trigram_the_memo(
    db_session: Session, invoices: Customer
) -> None:
    """The two paths are exclusive, and this is the assertion that measures it.

    `2025/400`'s causale is "Rata 123 del contratto quadro" and its number is 400. A branch
    that ran both paths for `123` would return it beside `2026/123`, and the palette would
    show two invoices for a search that named exactly one. Trigramming a number is how "show
    me invoice 123" becomes "every invoice whose description mentions 123".
    """
    group = _group(db_session, "123")
    assert group.total == 1
    assert group.hits[0].label == "2026/123 — Ristrutturazione uffici"
    assert group.hits[0].field == "number"
    # And the row that would have come back from the trigram path really does contain the
    # term -- otherwise this test would pass against a corpus that could not show the defect.
    assert (
        db_session.scalar(select(Invoice).where(Invoice.number == 400)).memo
        == "Rata 123 del contratto quadro"
    )


def test_a_draft_invoice_has_no_number_and_is_not_found_by_one(
    db_session: Session, invoices: Customer
) -> None:
    """`year`/`number` are NULL until emission -- that is what makes "a failed creation
    cannot burn a number" true by construction (slice 3). A draft is still findable by its
    causale, and its label says `draft` where the number would be rather than `None/None`.
    """
    db_session.add(
        Invoice(
            customer_id=invoices.id,
            type="invoice",
            status="draft",
            payment_status="unpaid",
            year=None,
            number=None,
            memo="Bozza 456 da rivedere",
            taxable_amount=Decimal("0.00"),
            vat_amount=Decimal("0.00"),
            stamp_duty=Decimal("0.00"),
            total=Decimal("0.00"),
            sdi_document_type="TD01",
            currency="EUR",
            custom_fields={},
        )
    )
    db_session.flush()

    by_memo = _group(db_session, "rivedere")
    assert by_memo.total == 1
    assert by_memo.hits[0].label == "bozza — Bozza 456 da rivedere"

    # And the number in its causale does not make it reachable as a fiscal number: an
    # unnumbered row has no number to be equal to, which is the point of the NULL.
    assert _group(db_session, "456").total == 0


def test_a_soft_deleted_invoice_is_not_a_result(db_session: Session, invoices: Customer) -> None:
    """Both directions, because `totale == 0` alone would also be what a term matching
    nothing looked like.

    On an **unnumbered** invoice, and that is the schema's doing rather than this test's
    convenience. `ck_invoices_no_delete_once_consumed` is
    `deleted_at IS NULL OR (numero IS NULL AND stato <> 'consumata')`: a row that consumed a
    number cannot be archived at all, not even by direct SQL, because a fiscal register with
    a hole in it is not a register. So the trigram path is the only one a soft delete can
    reach today -- which makes the `deleted_at IS NULL` clause on the equality path defence
    against that constraint changing rather than dead weight, and the second half of this
    test pins the constraint so that sentence stays a fact.
    """
    draft = Invoice(
        customer_id=invoices.id,
        type="invoice",
        status="draft",
        payment_status="unpaid",
        year=None,
        number=None,
        memo="Preventivo da archiviare",
        taxable_amount=Decimal("0.00"),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal("0.00"),
        sdi_document_type="TD01",
        currency="EUR",
        custom_fields={},
    )
    db_session.add(draft)
    db_session.flush()
    assert _group(db_session, "archiviare").total == 1

    draft.deleted_at = datetime.now(UTC)
    db_session.flush()
    assert _group(db_session, "archiviare").total == 0

    # The constraint itself, inside a savepoint so the refusal does not poison the session.
    savepoint = db_session.begin_nested()
    numbered = db_session.scalars(select(Invoice).where(Invoice.number == 8)).one()
    numbered.deleted_at = datetime.now(UTC)
    with pytest.raises(IntegrityError, match="ck_invoices_no_delete_once_consumed"):
        db_session.flush()
    savepoint.rollback()


def test_the_hit_carries_the_customer_as_its_subtitle(
    db_session: Session, invoices: Customer
) -> None:
    """On both paths. The subtitle is resolved after the limit, for at most `limit` rows,
    and a lookup wired into one path only would leave the other's rows unattributed."""
    assert _group(db_session, "impianti").hits[0].subtitle == "Cliente Srl"
    assert _group(db_session, "2026/7").hits[0].subtitle == "Cliente Srl"


def test_the_group_is_truncated_to_the_limit_and_reports_the_real_count(
    db_session: Session, invoices: Customer
) -> None:
    """§8.5's "five per class plus the real count", on the trigram path.

    Twelve invoices share a word; the palette shows three and says twelve. A branch that
    reported `len(hits)` would say three, and «3 di 3» and «3 di 12» are different answers.
    """
    for number in range(500, 512):
        db_session.add(
            Invoice(
                customer_id=invoices.id,
                type="invoice",
                status="issued",
                payment_status="unpaid",
                year=2024,
                number=number,
                memo=f"Sopralluogo cantiere nord {number}",
                taxable_amount=Decimal("10.00"),
                vat_amount=Decimal("0.00"),
                stamp_duty=Decimal("0.00"),
                total=Decimal("10.00"),
                issue_date=date(2024, 5, 1),
                sdi_document_type="TD01",
                currency="EUR",
                custom_fields={},
            )
        )
    db_session.flush()

    results = SearchService(db_session).search_everything(
        SearchQuery(deadline="sopralluogo", limit=3), READONLY
    )
    group = next(g for g in results.groups if g.entity == "invoice")
    assert len(group.hits) == 3
    assert group.total == 12
    assert group.total_and_a_minimum is False


def test_the_order_is_total_so_two_identical_searches_agree(
    db_session: Session, invoices: Customer
) -> None:
    """Criterion 4 in miniature. Both paths, because the equality path has an `ORDER BY` of
    its own and a missing tie-break there would be just as unstable as one in the scored
    query."""
    for term in ("impianti", "007"):
        first = _group(db_session, term)
        second = _group(db_session, term)
        assert first.model_dump() == second.model_dump()
