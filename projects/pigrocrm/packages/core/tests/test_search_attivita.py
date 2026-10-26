"""The sixth branch of the global search: an attività by its title and its notes (REB-723,
slice 7 §10 criterion 9).

What is pinned: both columns are searched, a title above a mention in the notes, the
closed ones are found and say what they are, an archived one is not found, and the group
is always present with the real count -- the properties the other five branches already
have, asserted here for the one that was missing.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.search.schemas import SearchGroup, SearchQuery
from pigrocrm.core.search.service import SearchService

READONLY = Actor(id=uuid7(), type="user", role="readonly")


def _add(session: Session, titolo: str, **columns: object) -> Attivita:
    row = Attivita(
        titolo=titolo,
        stato=str(columns.pop("stato", "aperta")),
        origine="manuale",
        custom_fields={},
        **columns,
    )
    session.add(row)
    session.flush()
    return row


def _group(session: Session, term: str, limite: int = 5) -> SearchGroup:
    results = SearchService(session).search_everything(
        SearchQuery(termine=term, limite=limite), READONLY
    )
    return next(group for group in results.gruppi if group.entity == "attivita")


def test_a_word_of_the_title_finds_it(db_session: Session) -> None:
    row = _add(db_session, "Preparare le proposte", scadenza=date(2026, 10, 30))
    _add(db_session, "Chiedere il codice SDI")

    group = _group(db_session, "proposte")

    assert [hit.id for hit in group.hits] == [row.id]
    assert group.hits[0].etichetta == "Preparare le proposte"
    assert group.hits[0].campo == "titolo"
    # An open one with a date says the date: it is what tells two of the same name apart.
    assert group.hits[0].sottotitolo == "30/10/2026"
    assert group.totale == 1


def test_the_notes_are_searched_too_and_rank_below_the_title(db_session: Session) -> None:
    in_notes = _add(db_session, "Telefonare", note="Per il preventivo di Rossi")
    in_title = _add(db_session, "Preventivo Rossi")

    group = _group(db_session, "preventivo")

    assert [hit.id for hit in group.hits] == [in_title.id, in_notes.id]
    assert [hit.campo for hit in group.hits] == ["titolo", "note"]
    assert group.hits[1].sottotitolo == "senza scadenza"


def test_a_closed_one_is_found_and_says_it_is_closed(db_session: Session) -> None:
    """A search that hid what was done would answer «did I ever do that» with «no»."""
    _add(
        db_session,
        "Mandare la fattura di settembre",
        stato="completata",
        completata_il=date(2026, 10, 2),
    )

    group = _group(db_session, "settembre")

    assert [hit.sottotitolo for hit in group.hits] == ["completata"]


def test_an_archived_one_is_not_found(db_session: Session) -> None:
    from datetime import UTC, datetime

    _add(db_session, "Creata per errore", deleted_at=datetime.now(UTC))

    group = _group(db_session, "errore")

    assert group.hits == []
    assert group.totale == 0


def test_the_group_is_there_when_nothing_matches_and_counts_past_the_limit(
    db_session: Session,
) -> None:
    assert _group(db_session, "zzzqqq").totale == 0

    for index in range(8):
        _add(db_session, f"Sollecito cliente {index}")

    group = _group(db_session, "sollecito")
    assert len(group.hits) == 5
    assert group.totale == 8
    assert group.totale_e_un_minimo is False
