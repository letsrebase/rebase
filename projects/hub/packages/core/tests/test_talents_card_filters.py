"""The Talenti filters that read the anonymous card (REB-778, REB-801): skills, place, seniority,
years, sectors, languages, the card's own state, the order by rate or years, and the filters
composed in one call.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from talents_fixtures import _apply, _emails, _profile, _signup, _with_card

from rebase_core.errors import ValidationFailed
from rebase_core.models import FreelancerCard
from rebase_core.talents import TalentsService


def test_a_row_carries_the_position_and_what_the_anonymous_card_says(clean_hub: Session) -> None:
    """REB-778: an agent matching a request reads the role, the seniority, the years,
    the skills and the place from the list itself. A field of a card written by hand
    with the wrong type reads as missing rather than failing the whole list."""
    ada = _apply(clean_hub, "ada@studio.it", job_title="Backend developer")
    grace = _apply(clean_hub, "grace@studio.it", first_name="Grace", last_name="Hopper")
    _apply(clean_hub, "nocard@studio.it", first_name="Nadia", last_name="Senza")
    _signup(clean_hub, "lead@studio.it")
    _with_card(
        clean_hub,
        ada,
        {
            "role": "Data engineer",
            "seniority": "senior",
            "experience_years": 9,
            "skills": ["SQL Server", "React"],
            "location": "Monza",
        },
    )
    _with_card(
        clean_hub,
        grace,
        {"role": 7, "experience_years": "nove", "skills": "Python", "location": None},
    )

    for q in (None, "studio"):
        by_email = {item.email: item for item in TalentsService(clean_hub).list_recent(q=q).items}
        ada_row = by_email["ada@studio.it"]
        assert ada_row.job_title == "Backend developer"
        assert (ada_row.role, ada_row.seniority, ada_row.experience_years) == (
            "Data engineer",
            "senior",
            9,
        )
        assert ada_row.skills == ["SQL Server", "React"]
        assert ada_row.location == "Monza"
        grace_row = by_email["grace@studio.it"]
        assert grace_row.has_anonymous_card is True
        assert (grace_row.role, grace_row.experience_years, grace_row.location) == (
            None,
            None,
            None,
        )
        assert grace_row.skills == []
        no_card = by_email["nocard@studio.it"]
        assert no_card.has_anonymous_card is False
        assert no_card.job_title == "Backend developer" and no_card.skills == []
        assert by_email["lead@studio.it"].job_title is None


def test_skills_keeps_the_cards_that_name_every_term(clean_hub: Session) -> None:
    """Each term may sit in a skill, the role, the summary or the position, whatever the
    case; a card that names only some of them, a card whose skills are not a list, a
    card that is not a JSON object and every lead stay out, without failing the list."""
    both_skills = _apply(clean_hub, "ada@studio.it", job_title="Backend developer")
    spread = _apply(clean_hub, "bruno@studio.it", first_name="Bruno", job_title="React developer")
    in_the_role = _apply(clean_hub, "fabio@studio.it", first_name="Fabio")
    one_only = _apply(clean_hub, "cara@studio.it", first_name="Cara")
    not_a_list = _apply(clean_hub, "eva@studio.it", first_name="Eva")
    an_array = _apply(clean_hub, "gina@studio.it", first_name="Gina")
    a_string = _apply(clean_hub, "hugo@studio.it", first_name="Hugo")
    objects_inside = _apply(clean_hub, "ivo@studio.it", first_name="Ivo")
    _apply(clean_hub, "dario@studio.it", first_name="Dario", job_title="SQL Server DBA")
    _signup(clean_hub, "lead@studio.it")
    _with_card(clean_hub, both_skills, {"skills": ["SQL Server", "React"]})
    _with_card(
        clean_hub,
        spread,
        {"role": "Frontend", "profile_summary": "Usa SQL Server ogni giorno.", "skills": ["CSS"]},
    )
    _with_card(clean_hub, in_the_role, {"role": "SQL Server architect", "skills": ["React"]})
    _with_card(clean_hub, one_only, {"skills": ["sql server"]})
    _with_card(clean_hub, not_a_list, {"skills": "SQL Server, React"})
    _with_card(clean_hub, an_array, ["SQL Server", "React"])
    _with_card(clean_hub, a_string, "SQL Server React")
    # Values `_card_read` drops never match either, so a match is one the row shows.
    _with_card(
        clean_hub,
        objects_inside,
        {"role": {"first_name": "SQL Server"}, "skills": [{"first_name": "SQL Server"}, "React"]},
    )

    talents = TalentsService(clean_hub)
    matching = {"ada@studio.it", "bruno@studio.it", "fabio@studio.it"}
    found = talents.list_recent(skills=["sql server", "REACT"])
    assert {item.email for item in found.items} == matching
    assert found.total == 3 and found.by_status["lead"] == 0

    # One row a page still walks every match once, the card join included.
    walked: list[str] = []
    cursor = None
    while True:
        page = talents.list_recent(limit=1, cursor=cursor, skills=["sql server", "react"])
        walked += [item.email for item in page.items]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert sorted(walked) == sorted(matching)

    # A blank term narrows nothing, and `%` is a character, not a wildcard.
    assert talents.list_recent(skills=["  "]).total == 10
    assert talents.list_recent(skills=["%"]).total == 0


def test_location_matches_part_of_the_card_place(clean_hub: Session) -> None:
    monza = _apply(clean_hub, "ada@studio.it")
    rome = _apply(clean_hub, "bruno@studio.it", first_name="Bruno")
    an_array = _apply(clean_hub, "cara@studio.it", first_name="Cara")
    an_object = _apply(clean_hub, "eva@studio.it", first_name="Eva")
    _apply(clean_hub, "dario@studio.it", first_name="Dario")
    _signup(clean_hub, "lead@studio.it")
    _with_card(clean_hub, monza, {"location": "Monza e Brianza"})
    _with_card(clean_hub, rome, {"location": "Roma"})
    _with_card(clean_hub, an_array, ["Monza"])
    _with_card(clean_hub, an_object, {"location": {"nota": "Monza"}})

    talents = TalentsService(clean_hub)
    found = talents.list_recent(location="monza")
    assert [item.email for item in found.items] == ["ada@studio.it"]
    assert found.by_status["lead"] == 0
    assert talents.list_recent(location="%").total == 0


def test_seniority_takes_any_of_the_levels_and_refuses_an_unknown_one(clean_hub: Session) -> None:
    _profile(clean_hub, "j@studio.it", rate="200", seniority="junior", experience_years=1)
    _profile(clean_hub, "s@studio.it", rate="400", seniority="senior", experience_years=7)
    _profile(clean_hub, "l@studio.it", rate="600", seniority="lead", experience_years=12)
    _profile(clean_hub, "x@studio.it", rate="300", seniority=None)
    _signup(clean_hub, "lead@studio.it")
    talents = TalentsService(clean_hub)
    assert _emails(talents.list_recent(seniority=["senior"])) == ["s@studio.it"]
    assert sorted(_emails(talents.list_recent(seniority=["Senior", "lead"]))) == [
        "l@studio.it",
        "s@studio.it",
    ]
    found = talents.list_recent(seniority=["lead"])
    assert found.by_status["lead"] == 0 and found.total == 1
    with pytest.raises(ValidationFailed, match="junior, mid, senior, lead"):
        talents.list_recent(seniority=["guru"])


def test_the_admins_level_beats_the_cards_in_the_row_the_filters_and_the_ranking(
    clean_hub: Session,
) -> None:
    """REB-799: an admin's seniority and years are what the row shows and what the
    seniority and years filters and the years sort read, each on its own; a card with no
    dated years reads uncertain until an admin gives the seniority."""
    corrected = _profile(
        clean_hub, "fix@studio.it", rate="300", seniority="lead", experience_years=3
    )
    clean_hub.execute(
        text(
            "UPDATE freelancers SET seniority_admin = 'mid', experience_years_admin = 6 "
            "WHERE id = :id"
        ),
        {"id": corrected},
    )
    _profile(clean_hub, "plain@studio.it", rate="300", seniority="senior", experience_years=9)
    undated = _profile(clean_hub, "undated@studio.it", rate="300", seniority="mid")
    clean_hub.commit()
    talents = TalentsService(clean_hub)

    rows = {row.email: row for row in talents.list_recent().items}
    assert (rows["fix@studio.it"].seniority, rows["fix@studio.it"].experience_years) == ("mid", 6)
    assert rows["fix@studio.it"].seniority_uncertain is False
    assert (rows["plain@studio.it"].seniority, rows["plain@studio.it"].experience_years) == (
        "senior",
        9,
    )
    assert (rows["undated@studio.it"].seniority, rows["undated@studio.it"].experience_years) == (
        "mid",
        None,
    )
    assert rows["undated@studio.it"].seniority_uncertain is True

    assert _emails(talents.list_recent(seniority=["mid"])) == ["undated@studio.it", "fix@studio.it"]
    assert _emails(talents.list_recent(seniority=["lead"])) == []
    assert _emails(talents.list_recent(min_years=6, max_years=6)) == ["fix@studio.it"]
    assert _emails(talents.list_recent(max_years=3)) == []
    ranked = talents.list_recent(sort="experience_years_desc")
    assert _emails(ranked) == ["plain@studio.it", "fix@studio.it"]

    clean_hub.execute(
        text("UPDATE freelancers SET seniority_admin = 'senior' WHERE id = :id"), {"id": undated}
    )
    clean_hub.commit()
    [after] = [row for row in talents.list_recent().items if row.email == "undated@studio.it"]
    assert (after.seniority, after.seniority_uncertain) == ("senior", False)


def test_min_years_and_max_are_inclusive_and_skip_to_card_with_no_years(clean_hub: Session) -> None:
    for email, experience_years in (("a@studio.it", 2), ("b@studio.it", 5), ("c@studio.it", 8)):
        _profile(clean_hub, email, rate="300", seniority="mid", experience_years=experience_years)
    _profile(clean_hub, "nan@studio.it", rate="300", seniority="mid", experience_years="otto")
    # The row shows no years for a number that is not whole, so neither filter nor sort
    # may meet it.
    _profile(clean_hub, "half@studio.it", rate="300", seniority="mid", experience_years=5.5)
    _profile(clean_hub, "none@studio.it", rate="300", seniority="mid")
    talents = TalentsService(clean_hub)
    assert sorted(_emails(talents.list_recent(min_years=5))) == ["b@studio.it", "c@studio.it"]
    assert _emails(talents.list_recent(max_years=2)) == ["a@studio.it"]
    assert _emails(talents.list_recent(min_years=5, max_years=5)) == ["b@studio.it"]
    assert talents.list_recent(min_years=0).total == 3


def test_industries_and_languages_need_every_term_in_some_element(clean_hub: Session) -> None:
    _profile(
        clean_hub,
        "ada@studio.it",
        rate="300",
        seniority="senior",
        industries=["Fintech", "Sanità"],
        languages=["Inglese (C1)", "Italiano"],
    )
    _profile(
        clean_hub,
        "bob@studio.it",
        rate="300",
        seniority="senior",
        industries=["Retail"],
        languages=["Italiano"],
    )
    _signup(clean_hub, "lead@studio.it")
    talents = TalentsService(clean_hub)
    assert _emails(talents.list_recent(industries=["fintech"])) == ["ada@studio.it"]
    assert _emails(talents.list_recent(industries=["fintech", "sanit"])) == ["ada@studio.it"]
    assert talents.list_recent(industries=["fintech", "retail"]).items == []
    assert _emails(talents.list_recent(languages=["inglese"])) == ["ada@studio.it"]
    assert sorted(_emails(talents.list_recent(languages=["italiano"]))) == [
        "ada@studio.it",
        "bob@studio.it",
    ]
    assert talents.list_recent(industries=["fintech"]).by_status["lead"] == 0


def test_card_tells_to_live_card_from_to_failed_one_and_from_none(clean_hub: Session) -> None:
    live = _apply(clean_hub, "live@studio.it", first_name="Live")
    _with_card(clean_hub, live, {"role": "Dev"})
    failed = _apply(clean_hub, "failed@studio.it", first_name="Failed")
    clean_hub.add(FreelancerCard(freelancer_id=failed, error="no_text", error_cv_sha256="1" * 64))
    outage = _apply(clean_hub, "outage@studio.it", first_name="Outage")
    clean_hub.add(
        FreelancerCard(freelancer_id=outage, card={"role": "Dev"}, cv_sha256="2" * 64, error="x")
    )
    clean_hub.commit()
    _apply(clean_hub, "none@studio.it", first_name="None")
    talents = TalentsService(clean_hub)
    assert sorted(_emails(talents.list_recent(card="present"))) == [
        "live@studio.it",
        "outage@studio.it",
    ]
    assert _emails(talents.list_recent(card="failed")) == ["failed@studio.it"]
    assert _emails(talents.list_recent(card="absent")) == ["none@studio.it"]
    with pytest.raises(ValidationFailed, match="present, absent, failed"):
        talents.list_recent(card="rotta")


@pytest.mark.parametrize(
    "sort", ["rate_asc", "rate_desc", "experience_years_asc", "experience_years_desc"]
)
def test_sort_ranks_by_rate_or_years_and_the_cursor_walks_it_whole(
    clean_hub: Session, sort: str
) -> None:
    """`a` and `c` share a rate, `a` and `b` share years: a page of one cuts every tie,
    and the walk still meets each row once, in the order the whole list has. A card
    with no value to rank by, and every lead, is left out of the ranking and of its
    counts."""
    _profile(clean_hub, "a@studio.it", rate="450.10", seniority="mid", experience_years=4)
    _profile(clean_hub, "b@studio.it", rate="300", seniority="mid", experience_years=4)
    _profile(clean_hub, "c@studio.it", rate="450.10", seniority="mid", experience_years=9)
    _profile(clean_hub, "d@studio.it", rate="800", seniority="mid", experience_years=0)
    _profile(clean_hub, "norate@studio.it", rate=None, seniority="mid", experience_years=3)
    _profile(clean_hub, "noanni@studio.it", rate="100", seniority="mid", experience_years="molti")
    _signup(clean_hub, "lead@studio.it")
    talents = TalentsService(clean_hub)
    by_rate = sort.startswith("rate")

    whole = talents.list_recent(sort=sort)
    ranked = ["a", "b", "c", "d", "noanni" if by_rate else "norate"]
    assert sorted(_emails(whole)) == sorted(f"{name}@studio.it" for name in ranked)
    values = [
        float(item.daily_rate or 0) if by_rate else float(item.experience_years or 0)
        for item in whole.items
    ]
    assert values == sorted(values, reverse=sort.endswith("desc"))
    assert whole.by_status["lead"] == 0
    assert whole.total == len(whole.items) == 5

    walked: list[str] = []
    cursor: str | None = None
    while True:
        page = talents.list_recent(limit=1, sort=sort, cursor=cursor)
        walked += _emails(page)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert walked == _emails(whole)


def test_sort_recent_overrides_the_search_order_and_to_cursor_keeps_its_own_order(
    clean_hub: Session,
) -> None:
    _profile(clean_hub, "ada@studio.it", rate="300", seniority="mid", experience_years=1)
    _profile(
        clean_hub,
        "ada2@studio.it",
        rate="200",
        seniority="mid",
        experience_years=2,
        first_name="Adalberto",
    )
    talents = TalentsService(clean_hub)
    newest_first = talents.list_recent(q="ada", sort="recent")
    assert _emails(newest_first) == ["ada2@studio.it", "ada@studio.it"]
    by_rate = talents.list_recent(limit=1, sort="rate_asc")
    assert by_rate.next_cursor is not None
    with pytest.raises(ValidationFailed, match="altro ordinamento"):
        talents.list_recent(limit=1, sort="experience_years_asc", cursor=by_rate.next_cursor)
    # the same value in the other direction is another order too: replayed, its
    # `(value, id) > ...` would skip and repeat rows.
    with pytest.raises(ValidationFailed, match="altro ordinamento"):
        talents.list_recent(limit=1, sort="rate_desc", cursor=by_rate.next_cursor)
    with pytest.raises(ValidationFailed, match="recent, rate_asc"):
        talents.list_recent(sort="a_caso")


def test_a_blank_filter_is_no_filter_on_both_sides_of_the_merge(clean_hub: Session) -> None:
    _apply(clean_hub, "ada@studio.it")
    _signup(clean_hub, "lead@studio.it")
    blank = TalentsService(clean_hub).list_recent(
        origin=" ", origin_page="", card="", sort="  ", seniority=[" "], industries=[""]
    )
    assert sorted(_emails(blank)) == ["ada@studio.it", "lead@studio.it"]


def test_the_filters_and_the_sort_compose_in_one_call(clean_hub: Session) -> None:
    """«senior, 5+ years, English, under 400 a day, cheapest first»."""
    _profile(
        clean_hub,
        "a@studio.it",
        rate="380",
        seniority="senior",
        experience_years=6,
        languages=["Inglese"],
    )
    _profile(
        clean_hub,
        "b@studio.it",
        rate="350",
        seniority="senior",
        experience_years=9,
        languages=["Inglese"],
    )
    _profile(
        clean_hub,
        "c@studio.it",
        rate="500",
        seniority="senior",
        experience_years=9,
        languages=["Inglese"],
    )
    _profile(
        clean_hub,
        "d@studio.it",
        rate="300",
        seniority="mid",
        experience_years=9,
        languages=["Inglese"],
    )
    _profile(
        clean_hub,
        "e@studio.it",
        rate="300",
        seniority="senior",
        experience_years=2,
        languages=["Inglese"],
    )
    _profile(
        clean_hub,
        "f@studio.it",
        rate="300",
        seniority="senior",
        experience_years=9,
        languages=["Francese"],
    )
    found = TalentsService(clean_hub).list_recent(
        seniority=["senior"],
        min_years=5,
        languages=["inglese"],
        max_rate=Decimal("400"),
        sort="rate_asc",
    )
    assert _emails(found) == ["b@studio.it", "a@studio.it"]
