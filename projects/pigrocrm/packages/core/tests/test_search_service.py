"""Spec §8.1, §8.5 and §8.6's server half.

Three things get asserted here that a happy-path test would not reach: the floor discards
the trigram tail, the count is exact up to 200 and declared as a minimum beyond it, and a
soft-deleted row is not a search result.

Two more that a happy-path test would not reach either, and that §16 criterion 4 needs
before task A10 can assert twenty identical runs: the count a group reports is the same
predicate as the hits it carries (a card and its drill-through are one calculation, not
two), and the ordering is *total* -- two rows that tie on score and on `updated_at` still
come back in one fixed order.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import get_args

import pydantic
import pytest

# Top-level, not `from .corpus import ...`: none of this repository's three test roots has
# an `__init__.py`, so a relative import has no parent package to resolve against. See
# `test_corpus.py`, which is the shipped precedent.
from corpus import KNOWN_PARTITA_IVA, KNOWN_RAGIONE_SOCIALE, SMALL, build_corpus
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.people.models import Person
from pigrocrm.core.search.schemas import (
    COUNT_CEILING,
    SearchEntity,
    SearchGroup,
    SearchQuery,
    SearchResults,
)
from pigrocrm.core.search.scoring import SCORE_FLOOR
from pigrocrm.core.search.service import SearchService

READONLY = Actor(id=uuid7(), type="user", role="readonly")


def _groups(results: SearchResults) -> dict[str, SearchGroup]:
    return {group.entity: group for group in results.gruppi}


def _search(session: Session, termine: str, limite: int = 5) -> dict[str, SearchGroup]:
    return _groups(
        SearchService(session).search_everything(
            SearchQuery(termine=termine, limite=limite), READONLY
        )
    )


def test_a_vat_fragment_finds_the_customer(db_session: Session) -> None:
    """The use case §17 names as 6A's reason to exist on its own."""
    build_corpus(db_session, SMALL)
    customers = _search(db_session, "34567")["customer"]
    assert KNOWN_RAGIONE_SOCIALE in [hit.etichetta for hit in customers.hits]


def test_an_exact_vat_number_ranks_the_customer_first(db_session: Session) -> None:
    """§16 criterion 4, first sentence."""
    build_corpus(db_session, SMALL)
    customers = _search(db_session, KNOWN_PARTITA_IVA)["customer"]
    assert customers.hits[0].etichetta == KNOWN_RAGIONE_SOCIALE
    assert customers.hits[0].campo == "partita_iva"
    assert customers.hits[0].punteggio == Decimal("1.0000")


def test_every_group_is_present_even_when_empty(db_session: Session) -> None:
    """§8.6's states are per-palette, not per-group, so the response always carries all
    five groups: a missing group and an empty group would render identically, and the
    client would have to guess which it was.

    Five since Task C12. The fifth was the point: `SearchEntity` declared `invoice` from
    the start and the fan-out never queried it, so an invoice number came back as
    «Nessun risultato» -- an empty group and a group that was never asked, rendering the
    same, which is exactly what this assertion exists to prevent."""
    results = SearchService(db_session).search_everything(SearchQuery(termine="zzzqqq"), READONLY)
    assert [group.entity for group in results.gruppi] == [
        "customer",
        "person",
        "deal",
        "document",
        "invoice",
    ]
    # Every member of `SearchEntity`, not a list that happens to be five long: a sixth
    # entity declared and not searched would be the same defect again.
    assert {group.entity for group in results.gruppi} == set(get_args(SearchEntity))
    assert all(group.totale == 0 and group.hits == [] for group in results.gruppi)


def test_a_group_is_truncated_to_the_limit_and_reports_the_real_count(
    db_session: Session,
) -> None:
    for index in range(40):
        db_session.add(
            Customer(
                ragione_sociale=f"Vulcano Impianti {index} Srl",
                nazione="IT",
                custom_fields={},
            )
        )
    db_session.flush()

    customers = _search(db_session, "Vulcano")["customer"]
    assert len(customers.hits) == 5
    assert customers.totale == 40
    assert customers.totale_e_un_minimo is False


def test_the_count_is_the_same_predicate_as_the_hits(db_session: Session) -> None:
    """A card and its drill-through are one calculation, not two (Global Constraints).

    `totale` comes from a second query, so it can drift from the hits: a `deleted_at`
    filter or a floor applied on one side and not the other shows up here and nowhere else
    in this file. Fifteen live rows -- under `limite`, so the group is not truncated and
    the two numbers have to agree exactly -- plus one soft-deleted row and one row the
    term does not reach, each of which a half-written count query would add to `totale`
    and not to `hits`. The floor's own half of the same agreement is
    `test_a_containment_below_the_floor_is_not_a_result`, which asserts both numbers.
    """
    for index in range(15):
        db_session.add(
            Customer(
                ragione_sociale=f"Vulcano Impianti {index} Srl",
                nazione="IT",
                custom_fields={},
            )
        )
    cancellata = Customer(ragione_sociale="Vulcano Cancellata Srl", nazione="IT", custom_fields={})
    db_session.add(cancellata)
    db_session.add(
        Customer(ragione_sociale="Quadrifoglio Logistica Spa", nazione="IT", custom_fields={})
    )
    db_session.flush()
    cancellata.deleted_at = datetime.now(UTC)
    db_session.flush()

    customers = _search(db_session, "Vulcano", limite=20)["customer"]
    assert customers.totale == len(customers.hits), (
        "the count and the hits disagree, so one of them applies a filter the other does not"
    )
    assert customers.totale == 15
    assert customers.totale_e_un_minimo is False


def test_beyond_the_ceiling_the_count_is_declared_as_a_minimum(
    db_session: Session,
) -> None:
    """Exact when exactness serves, cheap when it does not, never a lie (§8.5)."""
    for index in range(COUNT_CEILING + 25):
        db_session.add(
            Customer(
                ragione_sociale=f"Quadrifoglio {index} Srl",
                nazione="IT",
                custom_fields={},
            )
        )
    db_session.flush()

    customers = _search(db_session, "Quadrifoglio")["customer"]
    assert customers.totale == COUNT_CEILING
    assert customers.totale_e_un_minimo is True


def test_every_returned_hit_is_above_the_floor(db_session: Session) -> None:
    """The tail of a trigram match is noise, and showing noise in a palette teaches the
    user to ignore the palette."""
    build_corpus(db_session, SMALL)
    results = SearchService(db_session).search_everything(
        SearchQuery(termine="ingegneria", limite=20), READONLY
    )
    assert any(group.hits for group in results.gruppi), "nothing matched, so nothing was tested"
    for group in results.gruppi:
        for hit in group.hits:
            assert hit.punteggio >= SCORE_FLOOR, (group.entity, hit)


def test_a_containment_below_the_floor_is_not_a_result(db_session: Session) -> None:
    """The floor, in the one shape that can still fire once the substring arm measures
    `word_similarity`.

    Every candidate row already contains the term verbatim -- `matches_any` is an
    `ILIKE '%…%'`, not a similarity operator -- so the floor cannot be tested with a row
    that merely resembles the term: such a row is never a candidate. What it does discard
    is a *buried* fragment: "ino" strictly inside "Quadrifoglinoxyz" shares one trigram
    with the term's four and scores 0.6 x 0.25 = 0.1500, while the same three characters
    ending the word "Marino" score 0.6 x 0.50 = 0.3000. The first is the accident the
    floor exists for; the second is a surname somebody typed the end of.

    Without the floor in both the scored query and the count, this is the test that goes
    red.
    """
    buried = Customer(
        ragione_sociale="Quadrifoglinoxyz Logistica Spa", nazione="IT", custom_fields={}
    )
    ending = Customer(ragione_sociale="Marino Trasporti Srl", nazione="IT", custom_fields={})
    db_session.add_all([buried, ending])
    db_session.flush()

    customers = _search(db_session, "ino", limite=20)["customer"]

    assert [hit.id for hit in customers.hits] == [ending.id]
    assert customers.totale == 1


def test_the_hits_are_ordered_by_score_and_the_order_is_total(db_session: Session) -> None:
    """§16 criterion 4 asks for twenty byte-identical runs, which needs a *total* order.

    Score first, then `updated_at`, then `id` -- and the third key is not decoration: all
    the rows below are written in one transaction, so `server_default=func.now()` gives
    them the same `updated_at` to the microsecond, and every one of them scores exactly
    0.80. Without the tie-break Postgres may return them in any order it likes, which is
    the same set in a different sequence on the next keystroke.
    """
    tied = [
        Customer(ragione_sociale=f"Vulcano Impianti {index} Srl", nazione="IT", custom_fields={})
        for index in range(6)
    ]
    for row in tied:
        db_session.add(row)
    db_session.flush()

    first = _search(db_session, "Vulcano", limite=6)["customer"]
    second = _search(db_session, "Vulcano", limite=6)["customer"]

    assert {hit.punteggio for hit in first.hits} == {Decimal("0.8000")}
    assert [hit.id for hit in first.hits] == sorted((row.id for row in tied), reverse=True)
    assert [hit.id for hit in first.hits] == [hit.id for hit in second.hits]


def test_a_soft_deleted_row_is_not_a_result(db_session: Session) -> None:
    row = Customer(ragione_sociale="Cancellata Srl", nazione="IT", custom_fields={})
    db_session.add(row)
    db_session.flush()
    # Found while it lives, so the assertion below is about the soft delete and not about
    # a term that never matched anything.
    assert _search(db_session, "Cancellata")["customer"].totale == 1

    row.deleted_at = datetime.now(UTC)
    db_session.flush()

    gone = _search(db_session, "Cancellata")["customer"]
    assert gone.totale == 0
    assert gone.hits == []


def test_people_are_found_by_first_name_by_surname_and_by_email(
    db_session: Session,
) -> None:
    db_session.add(
        Person(nome="Ludovica", cognome="Ferraresi", email="lf@studio.example", custom_fields={})
    )
    db_session.flush()

    for term in ("Ludovi", "Ferrar", "lf@studio"):
        group = _search(db_session, term)["person"]
        assert group.totale == 1, term
        assert group.hits[0].etichetta == "Ludovica Ferraresi", term


def test_a_person_without_a_surname_has_a_label_and_no_trailing_space(
    db_session: Session,
) -> None:
    db_session.add(Person(nome="Ludovica", cognome=None, custom_fields={}))
    db_session.flush()
    group = _search(db_session, "Ludovi")["person"]
    assert group.hits[0].etichetta == "Ludovica"


def test_a_deal_hit_carries_its_customer_as_the_subtitle(db_session: Session) -> None:
    """A palette row reading "Rifacimento impianti 42" with no client is not an answer.
    The subtitle is built by the repository, never by the browser concatenating fields."""
    ids = build_corpus(db_session, SMALL)
    customer = db_session.get(Customer, ids.customer_ids[0])
    assert customer is not None
    db_session.add(
        Deal(
            nome="Rifacimento cabina elettrica",
            customer_id=customer.id,
            pipeline_stage_id=ids.stage_open_id,
            probabilita=50,
            custom_fields={},
        )
    )
    db_session.flush()

    group = _search(db_session, "cabina elettrica")["deal"]
    assert group.hits[0].etichetta == "Rifacimento cabina elettrica"
    assert group.hits[0].sottotitolo == customer.ragione_sociale


def test_a_document_hit_carries_its_type_as_the_subtitle(db_session: Session) -> None:
    ids = build_corpus(db_session, SMALL)
    db_session.add(
        Document(
            customer_id=ids.customer_ids[0],
            tipo="offerta",
            titolo="Capitolato speciale d'appalto",
            versione_corrente=1,
            custom_fields={},
        )
    )
    db_session.flush()

    group = _search(db_session, "capitolato speciale")["document"]
    assert group.hits[0].etichetta == "Capitolato speciale d'appalto"
    assert group.hits[0].sottotitolo == "offerta"


def test_a_two_character_term_is_refused_by_the_schema() -> None:
    """The server-side half of the three-character rule. The palette does not send it,
    and an agent that does gets a named validation error rather than a table scan."""
    with pytest.raises(pydantic.ValidationError):
        SearchQuery(termine="ab")


def test_the_term_is_stripped_before_it_is_matched(db_session: Session) -> None:
    """A palette sends what the user typed, spaces included, and a leading space would
    otherwise turn a prefix match into a substring one -- a different score for the same
    intention. The echoed `termine` is the stripped one, because that is what was
    searched for."""
    db_session.add(Customer(ragione_sociale="Vulcano Srl", nazione="IT", custom_fields={}))
    db_session.flush()

    results = SearchService(db_session).search_everything(
        SearchQuery(termine="  Vulcano  "), READONLY
    )
    assert results.termine == "Vulcano"
    assert _groups(results)["customer"].hits[0].punteggio == Decimal("0.8000")


def test_a_readonly_actor_can_search(db_session: Session) -> None:
    """Search is a read, and slice 4 §11 gives every dashboard read to every role. There
    is no new authorisation rule in this slice (§13)."""
    build_corpus(db_session, SMALL)
    results = SearchService(db_session).search_everything(SearchQuery(termine="Rossi"), READONLY)
    assert results.termine == "Rossi"
    assert _groups(results)["customer"].totale > 0


def test_search_service_exposes_exactly_one_public_method() -> None:
    """Task A11's exclusion list must be exactly `update_automation_config`, so every
    other public method of an audited service needs a tool. Pinning the count here makes
    a second method a failure in this file rather than a surprise in the architecture
    test."""
    import inspect

    public = {
        name
        for name, member in inspect.getmembers(SearchService, predicate=inspect.isfunction)
        if not name.startswith("_") and member.__qualname__.startswith("SearchService.")
    }
    assert public == {"search_everything"}
