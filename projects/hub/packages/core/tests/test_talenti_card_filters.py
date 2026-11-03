"""The Talenti filters that read the anonymous card (REB-778, REB-801): skills, place, seniority,
years, sectors, languages, the card's own state, the order by rate or years, and the filters
composed in one call.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from talenti_fixtures import _apply, _emails, _profile, _signup, _with_card

from rebase_core.errors import ValidationFailed
from rebase_core.models import FreelancerCard
from rebase_core.talenti import TalentiService


def test_a_row_carries_the_position_and_what_the_anonymous_card_says(clean_hub: Session) -> None:
    """REB-778: an agent matching a request reads the role, the seniority, the years,
    the skills and the place from the list itself. A field of a card written by hand
    with the wrong type reads as missing rather than failing the whole list."""
    ada = _apply(clean_hub, "ada@studio.it", posizione="Backend developer")
    grace = _apply(clean_hub, "grace@studio.it", nome="Grace", cognome="Hopper")
    _apply(clean_hub, "nocard@studio.it", nome="Nadia", cognome="Senza")
    _signup(clean_hub, "lead@studio.it")
    _with_card(
        clean_hub,
        ada,
        {
            "ruolo": "Data engineer",
            "seniority": "senior",
            "anni": 9,
            "competenze": ["SQL Server", "React"],
            "luogo": "Monza",
        },
    )
    _with_card(
        clean_hub, grace, {"ruolo": 7, "anni": "nove", "competenze": "Python", "luogo": None}
    )

    for q in (None, "studio"):
        by_email = {item.email: item for item in TalentiService(clean_hub).list_recent(q=q).items}
        ada_row = by_email["ada@studio.it"]
        assert ada_row.posizione == "Backend developer"
        assert (ada_row.ruolo, ada_row.seniority, ada_row.anni) == ("Data engineer", "senior", 9)
        assert ada_row.competenze == ["SQL Server", "React"]
        assert ada_row.luogo == "Monza"
        grace_row = by_email["grace@studio.it"]
        assert grace_row.ha_scheda_anonima is True
        assert (grace_row.ruolo, grace_row.anni, grace_row.luogo) == (None, None, None)
        assert grace_row.competenze == []
        no_card = by_email["nocard@studio.it"]
        assert no_card.ha_scheda_anonima is False
        assert no_card.posizione == "Backend developer" and no_card.competenze == []
        assert by_email["lead@studio.it"].posizione is None


def test_competenze_keeps_the_cards_that_name_every_term(clean_hub: Session) -> None:
    """Each term may sit in a skill, the role, the summary or the position, whatever the
    case; a card that names only some of them, a card whose skills are not a list, a
    card that is not a JSON object and every lead stay out, without failing the list."""
    both_skills = _apply(clean_hub, "ada@studio.it", posizione="Backend developer")
    spread = _apply(clean_hub, "bruno@studio.it", nome="Bruno", posizione="React developer")
    in_the_role = _apply(clean_hub, "fabio@studio.it", nome="Fabio")
    one_only = _apply(clean_hub, "cara@studio.it", nome="Cara")
    not_a_list = _apply(clean_hub, "eva@studio.it", nome="Eva")
    an_array = _apply(clean_hub, "gina@studio.it", nome="Gina")
    a_string = _apply(clean_hub, "hugo@studio.it", nome="Hugo")
    objects_inside = _apply(clean_hub, "ivo@studio.it", nome="Ivo")
    _apply(clean_hub, "dario@studio.it", nome="Dario", posizione="SQL Server DBA")
    _signup(clean_hub, "lead@studio.it")
    _with_card(clean_hub, both_skills, {"competenze": ["SQL Server", "React"]})
    _with_card(
        clean_hub,
        spread,
        {"ruolo": "Frontend", "sintesi": "Usa SQL Server ogni giorno.", "competenze": ["CSS"]},
    )
    _with_card(clean_hub, in_the_role, {"ruolo": "SQL Server architect", "competenze": ["React"]})
    _with_card(clean_hub, one_only, {"competenze": ["sql server"]})
    _with_card(clean_hub, not_a_list, {"competenze": "SQL Server, React"})
    _with_card(clean_hub, an_array, ["SQL Server", "React"])
    _with_card(clean_hub, a_string, "SQL Server React")
    # Values `_card_read` drops never match either, so a match is one the row shows.
    _with_card(
        clean_hub,
        objects_inside,
        {"ruolo": {"nome": "SQL Server"}, "competenze": [{"nome": "SQL Server"}, "React"]},
    )

    talenti = TalentiService(clean_hub)
    matching = {"ada@studio.it", "bruno@studio.it", "fabio@studio.it"}
    found = talenti.list_recent(competenze=["sql server", "REACT"])
    assert {item.email for item in found.items} == matching
    assert found.totale == 3 and found.per_stato["lead"] == 0

    # One row a page still walks every match once, the card join included.
    walked: list[str] = []
    cursor = None
    while True:
        page = talenti.list_recent(limit=1, cursor=cursor, competenze=["sql server", "react"])
        walked += [item.email for item in page.items]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert sorted(walked) == sorted(matching)

    # A blank term narrows nothing, and `%` is a character, not a wildcard.
    assert talenti.list_recent(competenze=["  "]).totale == 10
    assert talenti.list_recent(competenze=["%"]).totale == 0


def test_luogo_matches_part_of_the_card_place(clean_hub: Session) -> None:
    monza = _apply(clean_hub, "ada@studio.it")
    rome = _apply(clean_hub, "bruno@studio.it", nome="Bruno")
    an_array = _apply(clean_hub, "cara@studio.it", nome="Cara")
    an_object = _apply(clean_hub, "eva@studio.it", nome="Eva")
    _apply(clean_hub, "dario@studio.it", nome="Dario")
    _signup(clean_hub, "lead@studio.it")
    _with_card(clean_hub, monza, {"luogo": "Monza e Brianza"})
    _with_card(clean_hub, rome, {"luogo": "Roma"})
    _with_card(clean_hub, an_array, ["Monza"])
    _with_card(clean_hub, an_object, {"luogo": {"nota": "Monza"}})

    talenti = TalentiService(clean_hub)
    found = talenti.list_recent(luogo="monza")
    assert [item.email for item in found.items] == ["ada@studio.it"]
    assert found.per_stato["lead"] == 0
    assert talenti.list_recent(luogo="%").totale == 0


def test_seniority_takes_any_of_the_levels_and_refuses_an_unknown_one(clean_hub: Session) -> None:
    _profile(clean_hub, "j@studio.it", rate="200", seniority="junior", anni=1)
    _profile(clean_hub, "s@studio.it", rate="400", seniority="senior", anni=7)
    _profile(clean_hub, "l@studio.it", rate="600", seniority="lead", anni=12)
    _profile(clean_hub, "x@studio.it", rate="300", seniority=None)
    _signup(clean_hub, "lead@studio.it")
    talenti = TalentiService(clean_hub)
    assert _emails(talenti.list_recent(seniority=["senior"])) == ["s@studio.it"]
    assert sorted(_emails(talenti.list_recent(seniority=["Senior", "lead"]))) == [
        "l@studio.it",
        "s@studio.it",
    ]
    found = talenti.list_recent(seniority=["lead"])
    assert found.per_stato["lead"] == 0 and found.totale == 1
    with pytest.raises(ValidationFailed, match="junior, mid, senior, lead"):
        talenti.list_recent(seniority=["guru"])


def test_the_admins_level_beats_the_cards_in_the_row_the_filters_and_the_ranking(
    clean_hub: Session,
) -> None:
    """REB-799: an admin's seniority and years are what the row shows and what the
    seniority and anni filters and the anni sort read, each on its own; a card with no
    dated years reads uncertain until an admin gives the seniority."""
    corrected = _profile(clean_hub, "fix@studio.it", rate="300", seniority="lead", anni=3)
    clean_hub.execute(
        text("UPDATE freelancers SET seniority_admin = 'mid', anni_admin = 6 WHERE id = :id"),
        {"id": corrected},
    )
    _profile(clean_hub, "plain@studio.it", rate="300", seniority="senior", anni=9)
    undated = _profile(clean_hub, "undated@studio.it", rate="300", seniority="mid")
    clean_hub.commit()
    talenti = TalentiService(clean_hub)

    rows = {row.email: row for row in talenti.list_recent().items}
    assert (rows["fix@studio.it"].seniority, rows["fix@studio.it"].anni) == ("mid", 6)
    assert rows["fix@studio.it"].seniority_incerta is False
    assert (rows["plain@studio.it"].seniority, rows["plain@studio.it"].anni) == ("senior", 9)
    assert (rows["undated@studio.it"].seniority, rows["undated@studio.it"].anni) == ("mid", None)
    assert rows["undated@studio.it"].seniority_incerta is True

    assert _emails(talenti.list_recent(seniority=["mid"])) == ["undated@studio.it", "fix@studio.it"]
    assert _emails(talenti.list_recent(seniority=["lead"])) == []
    assert _emails(talenti.list_recent(anni_min=6, anni_max=6)) == ["fix@studio.it"]
    assert _emails(talenti.list_recent(anni_max=3)) == []
    ranked = talenti.list_recent(ordina="anni_desc")
    assert _emails(ranked) == ["plain@studio.it", "fix@studio.it"]

    clean_hub.execute(
        text("UPDATE freelancers SET seniority_admin = 'senior' WHERE id = :id"), {"id": undated}
    )
    clean_hub.commit()
    [after] = [row for row in talenti.list_recent().items if row.email == "undated@studio.it"]
    assert (after.seniority, after.seniority_incerta) == ("senior", False)


def test_anni_min_and_max_are_inclusive_and_skip_a_card_with_no_years(clean_hub: Session) -> None:
    for email, anni in (("a@studio.it", 2), ("b@studio.it", 5), ("c@studio.it", 8)):
        _profile(clean_hub, email, rate="300", seniority="mid", anni=anni)
    _profile(clean_hub, "nan@studio.it", rate="300", seniority="mid", anni="otto")
    # The row shows no years for a number that is not whole, so neither filter nor sort
    # may meet it.
    _profile(clean_hub, "half@studio.it", rate="300", seniority="mid", anni=5.5)
    _profile(clean_hub, "none@studio.it", rate="300", seniority="mid")
    talenti = TalentiService(clean_hub)
    assert sorted(_emails(talenti.list_recent(anni_min=5))) == ["b@studio.it", "c@studio.it"]
    assert _emails(talenti.list_recent(anni_max=2)) == ["a@studio.it"]
    assert _emails(talenti.list_recent(anni_min=5, anni_max=5)) == ["b@studio.it"]
    assert talenti.list_recent(anni_min=0).totale == 3


def test_settori_and_lingue_need_every_term_in_some_element(clean_hub: Session) -> None:
    _profile(
        clean_hub,
        "ada@studio.it",
        rate="300",
        seniority="senior",
        settori=["Fintech", "Sanità"],
        lingue=["Inglese (C1)", "Italiano"],
    )
    _profile(
        clean_hub,
        "bob@studio.it",
        rate="300",
        seniority="senior",
        settori=["Retail"],
        lingue=["Italiano"],
    )
    _signup(clean_hub, "lead@studio.it")
    talenti = TalentiService(clean_hub)
    assert _emails(talenti.list_recent(settori=["fintech"])) == ["ada@studio.it"]
    assert _emails(talenti.list_recent(settori=["fintech", "sanit"])) == ["ada@studio.it"]
    assert talenti.list_recent(settori=["fintech", "retail"]).items == []
    assert _emails(talenti.list_recent(lingue=["inglese"])) == ["ada@studio.it"]
    assert sorted(_emails(talenti.list_recent(lingue=["italiano"]))) == [
        "ada@studio.it",
        "bob@studio.it",
    ]
    assert talenti.list_recent(settori=["fintech"]).per_stato["lead"] == 0


def test_scheda_tells_a_live_card_from_a_failed_one_and_from_none(clean_hub: Session) -> None:
    live = _apply(clean_hub, "live@studio.it", nome="Live")
    _with_card(clean_hub, live, {"ruolo": "Dev"})
    failed = _apply(clean_hub, "failed@studio.it", nome="Failed")
    clean_hub.add(FreelancerCard(freelancer_id=failed, error="no_text", error_cv_sha256="1" * 64))
    outage = _apply(clean_hub, "outage@studio.it", nome="Outage")
    clean_hub.add(
        FreelancerCard(freelancer_id=outage, card={"ruolo": "Dev"}, cv_sha256="2" * 64, error="x")
    )
    clean_hub.commit()
    _apply(clean_hub, "none@studio.it", nome="None")
    talenti = TalentiService(clean_hub)
    assert sorted(_emails(talenti.list_recent(scheda="presente"))) == [
        "live@studio.it",
        "outage@studio.it",
    ]
    assert _emails(talenti.list_recent(scheda="fallita")) == ["failed@studio.it"]
    assert _emails(talenti.list_recent(scheda="assente")) == ["none@studio.it"]
    with pytest.raises(ValidationFailed, match="presente, assente, fallita"):
        talenti.list_recent(scheda="rotta")


@pytest.mark.parametrize("ordina", ["tariffa_asc", "tariffa_desc", "anni_asc", "anni_desc"])
def test_ordina_ranks_by_rate_or_years_and_the_cursor_walks_it_whole(
    clean_hub: Session, ordina: str
) -> None:
    """`a` and `c` share a rate, `a` and `b` share years: a page of one cuts every tie,
    and the walk still meets each row once, in the order the whole list has. A card
    with no value to rank by, and every lead, is left out of the ranking and of its
    counts."""
    _profile(clean_hub, "a@studio.it", rate="450.10", seniority="mid", anni=4)
    _profile(clean_hub, "b@studio.it", rate="300", seniority="mid", anni=4)
    _profile(clean_hub, "c@studio.it", rate="450.10", seniority="mid", anni=9)
    _profile(clean_hub, "d@studio.it", rate="800", seniority="mid", anni=0)
    _profile(clean_hub, "norate@studio.it", rate=None, seniority="mid", anni=3)
    _profile(clean_hub, "noanni@studio.it", rate="100", seniority="mid", anni="molti")
    _signup(clean_hub, "lead@studio.it")
    talenti = TalentiService(clean_hub)
    by_rate = ordina.startswith("tariffa")

    whole = talenti.list_recent(ordina=ordina)
    ranked = ["a", "b", "c", "d", "noanni" if by_rate else "norate"]
    assert sorted(_emails(whole)) == sorted(f"{name}@studio.it" for name in ranked)
    values = [
        float(item.tariffa_giornaliera or 0) if by_rate else float(item.anni or 0)
        for item in whole.items
    ]
    assert values == sorted(values, reverse=ordina.endswith("desc"))
    assert whole.per_stato["lead"] == 0
    assert whole.totale == len(whole.items) == 5

    walked: list[str] = []
    cursor: str | None = None
    while True:
        page = talenti.list_recent(limit=1, ordina=ordina, cursor=cursor)
        walked += _emails(page)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert walked == _emails(whole)


def test_ordina_recenti_overrides_the_search_order_and_a_cursor_keeps_its_own_order(
    clean_hub: Session,
) -> None:
    _profile(clean_hub, "ada@studio.it", rate="300", seniority="mid", anni=1)
    _profile(clean_hub, "ada2@studio.it", rate="200", seniority="mid", anni=2, nome="Adalberto")
    talenti = TalentiService(clean_hub)
    newest_first = talenti.list_recent(q="ada", ordina="recenti")
    assert _emails(newest_first) == ["ada2@studio.it", "ada@studio.it"]
    by_rate = talenti.list_recent(limit=1, ordina="tariffa_asc")
    assert by_rate.next_cursor is not None
    with pytest.raises(ValidationFailed, match="altro ordinamento"):
        talenti.list_recent(limit=1, ordina="anni_asc", cursor=by_rate.next_cursor)
    # the same value in the other direction is another order too: replayed, its
    # `(value, id) > ...` would skip and repeat rows.
    with pytest.raises(ValidationFailed, match="altro ordinamento"):
        talenti.list_recent(limit=1, ordina="tariffa_desc", cursor=by_rate.next_cursor)
    with pytest.raises(ValidationFailed, match="recenti, tariffa_asc"):
        talenti.list_recent(ordina="a_caso")


def test_a_blank_filter_is_no_filter_on_both_sides_of_the_merge(clean_hub: Session) -> None:
    _apply(clean_hub, "ada@studio.it")
    _signup(clean_hub, "lead@studio.it")
    blank = TalentiService(clean_hub).list_recent(
        origine=" ", pagina_origine="", scheda="", ordina="  ", seniority=[" "], settori=[""]
    )
    assert sorted(_emails(blank)) == ["ada@studio.it", "lead@studio.it"]


def test_the_filters_and_the_sort_compose_in_one_call(clean_hub: Session) -> None:
    """«senior, 5+ years, English, under 400 a day, cheapest first»."""
    _profile(clean_hub, "a@studio.it", rate="380", seniority="senior", anni=6, lingue=["Inglese"])
    _profile(clean_hub, "b@studio.it", rate="350", seniority="senior", anni=9, lingue=["Inglese"])
    _profile(clean_hub, "c@studio.it", rate="500", seniority="senior", anni=9, lingue=["Inglese"])
    _profile(clean_hub, "d@studio.it", rate="300", seniority="mid", anni=9, lingue=["Inglese"])
    _profile(clean_hub, "e@studio.it", rate="300", seniority="senior", anni=2, lingue=["Inglese"])
    _profile(clean_hub, "f@studio.it", rate="300", seniority="senior", anni=9, lingue=["Francese"])
    found = TalentiService(clean_hub).list_recent(
        seniority=["senior"],
        anni_min=5,
        lingue=["inglese"],
        tariffa_max=Decimal("400"),
        ordina="tariffa_asc",
    )
    assert _emails(found) == ["b@studio.it", "a@studio.it"]
