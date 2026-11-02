"""`talenti`: freelancer cards and bare sign-ups as one list, `stato` `lead` for the bare
ones (REB-282), searchable and cursor-paginated (REB-285), the row each person has (REB-798)
and the cost of a page (REB-891). The filters that read the anonymous card are in
`test_talenti_card_filters.py`.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from admins import an_admin
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from talenti_fixtures import PDF, _application, _apply, _draft, _signup, _three_channels, _with_card

from rebase_core.admin_schemas import StatusChange
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.models import FreelancerCard, Login
from rebase_core.signup_schemas import SignupUtm
from rebase_core.talenti import TalentiService


def test_a_bare_signup_is_a_lead_and_a_card_is_its_own_state(clean_hub: Session) -> None:
    FreelancerService(clean_hub).apply(
        _application("ada@studio.it"), PDF, "cv.pdf", "application/pdf"
    )
    _signup(clean_hub, "lead@studio.it")
    listed = TalentiService(clean_hub).list_recent()
    by_email = {item.email: item for item in listed.items}
    assert by_email["ada@studio.it"].stato == "nuovo"
    assert by_email["ada@studio.it"].origine == "wizard"
    assert by_email["lead@studio.it"].stato == "lead"
    assert by_email["lead@studio.it"].origine == "form"
    assert listed.totale == 2


def test_a_card_carries_its_own_rate_and_mode_a_lead_carries_neither(clean_hub: Session) -> None:
    """REB-558: `tariffa_giornaliera` and `remoto` are a card's own two columns, so a
    bare sign-up, which has neither, reads `None` for both."""
    FreelancerService(clean_hub).apply(
        _application("ada@studio.it", tariffa_giornaliera=Decimal("450.00"), remoto="remoto"),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    _signup(clean_hub, "lead@studio.it")
    listed = TalentiService(clean_hub).list_recent()
    by_email = {item.email: item for item in listed.items}
    assert by_email["ada@studio.it"].tariffa_giornaliera == Decimal("450.00")
    assert by_email["ada@studio.it"].remoto == "remoto"
    assert by_email["lead@studio.it"].tariffa_giornaliera is None
    assert by_email["lead@studio.it"].remoto is None


def test_a_row_carries_the_vetted_date_and_whether_an_anonymous_card_exists(
    clean_hub: Session,
) -> None:
    """REB-518: «Talenti» shows the «Verificato» pill and knows which talent the team
    builder can propose, with or without a search term (which adds a score column)."""
    service = FreelancerService(clean_hub)
    ada, _ = service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "application/pdf")
    grace, _ = service.apply(
        _application("grace@studio.it", nome="Grace", cognome="Hopper"),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    _signup(clean_hub, "lead@studio.it")
    service.set_vetted(ada.id, True, an_admin(clean_hub))
    clean_hub.add(
        FreelancerCard(
            freelancer_id=ada.id, cv_sha256="0" * 64, card={"ruolo": "Backend developer"}
        )
    )
    # A card the writer retired: only the failure is left, which is no card.
    clean_hub.add(
        FreelancerCard(freelancer_id=grace.id, error="rifiutata", error_cv_sha256="1" * 64)
    )
    clean_hub.commit()

    for q in (None, "studio"):
        by_email = {item.email: item for item in TalentiService(clean_hub).list_recent(q=q).items}
        assert by_email["ada@studio.it"].vetted_at is not None
        assert by_email["ada@studio.it"].ha_scheda_anonima is True
        assert by_email["grace@studio.it"].vetted_at is None
        assert by_email["grace@studio.it"].ha_scheda_anonima is False
        assert by_email["lead@studio.it"].vetted_at is None
        assert by_email["lead@studio.it"].ha_scheda_anonima is False


def test_a_signup_whose_address_already_has_a_card_is_not_also_a_lead(clean_hub: Session) -> None:
    FreelancerService(clean_hub).apply(
        _application("ada@studio.it"), PDF, "cv.pdf", "application/pdf"
    )
    _signup(clean_hub, "ADA@studio.it")  # same address, different case: has a card already
    listed = TalentiService(clean_hub).list_recent()
    assert [item.email for item in listed.items] == ["ada@studio.it"]
    assert listed.totale == 1
    assert listed.per_stato["lead"] == 0


def test_a_card_drafted_by_an_admin_from_research_is_origine_admin(clean_hub: Session) -> None:
    signup_id = _signup(clean_hub, "ricerca@studio.it")
    FreelancerService(clean_hub).draft_from_signup(signup_id, _draft(), an_admin(clean_hub), "web")
    listed = TalentiService(clean_hub).list_recent()
    item = next(item for item in listed.items if item.email == "ricerca@studio.it")
    assert item.origine == "admin" and item.stato == "nuovo"
    assert item.id != signup_id  # the card has its own row, and its own id


def test_counts_per_state_include_the_leads_and_are_not_bounded_by_the_limit(
    clean_hub: Session,
) -> None:
    service = FreelancerService(clean_hub)
    ids = [
        service.apply(_application(email=f"p{i}@studio.it"), PDF, "cv.pdf", "")[0].id
        for i in range(3)
    ]
    service.set_status(
        ids[0], StatusChange(stato="contattato"), admin_id=an_admin(clean_hub), fonte="web"
    )
    _signup(clean_hub, "lead1@studio.it")
    _signup(clean_hub, "lead2@studio.it")
    listed = TalentiService(clean_hub).list_recent(limit=1)
    assert listed.per_stato == {"nuovo": 2, "contattato": 1, "attivo": 0, "scartato": 0, "lead": 2}
    assert len(listed.items) == 1  # the page is bounded, the counts are not
    # and the merge kept the newest row across both sources
    assert [item.email for item in listed.items] == ["lead2@studio.it"]


def test_the_list_is_newest_first_across_both_tables(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(_application("old@studio.it"), PDF, "cv.pdf", "")
    _signup(clean_hub, "middle@studio.it")
    service.apply(_application("new@studio.it"), PDF, "cv.pdf", "")
    listed = TalentiService(clean_hub).list_recent()
    assert [item.email for item in listed.items] == [
        "new@studio.it",
        "middle@studio.it",
        "old@studio.it",
    ]


def test_the_stato_filter_selects_leads_or_a_single_freelancer_state(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "")
    _signup(clean_hub, "lead@studio.it")

    only_leads = TalentiService(clean_hub).list_recent(stato="lead")
    assert [item.email for item in only_leads.items] == ["lead@studio.it"]
    assert only_leads.totale == 1

    only_new = TalentiService(clean_hub).list_recent(stato="nuovo")
    assert [item.email for item in only_new.items] == ["ada@studio.it"]
    assert only_new.totale == 1

    # A filter that never lands in the database is simply empty, like
    # `FreelancerService.list_recent`'s own pass-through filter.
    unknown = TalentiService(clean_hub).list_recent(stato="forse")
    assert unknown.items == [] and unknown.totale == 0


def test_an_empty_hub_answers_zero_of_everything(clean_hub: Session) -> None:
    listed = TalentiService(clean_hub).list_recent()
    assert listed.totale == 0
    assert listed.items == []
    assert listed.per_stato == {"nuovo": 0, "contattato": 0, "attivo": 0, "scartato": 0, "lead": 0}


# ---- search and cursor pagination (REB-285) -------------------------------------------


def test_search_hits_a_partial_surname_and_an_email_domain(clean_hub: Session) -> None:
    FreelancerService(clean_hub).apply(
        _application("ada@rossilab.it", cognome="Rossi"), PDF, "cv.pdf", "application/pdf"
    )
    FreelancerService(clean_hub).apply(
        _application("bob@other.it", nome="Bob", cognome="Bianchi"),
        PDF,
        "cv.pdf",
        "application/pdf",
    )

    by_surname = TalentiService(clean_hub).list_recent(q="oss")
    assert [item.email for item in by_surname.items] == ["ada@rossilab.it"]

    by_domain = TalentiService(clean_hub).list_recent(q="rossilab.it")
    assert [item.email for item in by_domain.items] == ["ada@rossilab.it"]


def test_the_cursor_walks_every_row_once_with_no_dupes_or_gaps(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    for i in range(4):
        service.apply(
            _application(f"card{i}@studio.it", cognome=f"Card{i}"), PDF, "cv.pdf", "application/pdf"
        )
    for i in range(4):
        _signup(clean_hub, f"lead{i}@studio.it")

    talenti = TalentiService(clean_hub)
    full = talenti.list_recent(limit=100)
    assert full.totale == 8 and len(full.items) == 8

    seen: list[UUID] = []
    cursor: str | None = None
    for _ in range(20):  # generous upper bound: 8 rows over a page size of 3
        page = talenti.list_recent(limit=3, cursor=cursor)
        seen.extend(item.id for item in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    else:
        pytest.fail("the cursor never reached its last page")

    assert len(seen) == len(set(seen)) == 8
    assert set(seen) == {item.id for item in full.items}


def test_a_malformed_cursor_is_refused(clean_hub: Session) -> None:
    with pytest.raises(ValidationFailed):
        TalentiService(clean_hub).list_recent(cursor="not-a-valid-cursor")


# ---- filters (REB-285) ------------------------------------------------------------------


def test_card_only_filters_narrow_the_cards_and_drop_every_lead(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(
        _application(
            "remote@studio.it",
            remoto="remoto",
            tariffa_giornaliera=Decimal("300.00"),
            posizione="Backend developer",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    service.apply(
        _application(
            "onsite@studio.it",
            remoto="in_sede",
            tariffa_giornaliera=Decimal("800.00"),
            posizione="Designer",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    _signup(clean_hub, "lead@studio.it")

    talenti = TalentiService(clean_hub)
    only_remote = talenti.list_recent(remoto="remoto")
    assert [item.email for item in only_remote.items] == ["remote@studio.it"]
    # A lead has no `remoto` of its own: a card-only filter drops it from the merge
    # and from the per-state count beside it, not just from `items`.
    assert only_remote.per_stato["lead"] == 0

    by_rate = talenti.list_recent(tariffa_min=Decimal("500"))
    assert [item.email for item in by_rate.items] == ["onsite@studio.it"]

    by_posizione = talenti.list_recent(posizione="design")
    assert [item.email for item in by_posizione.items] == ["onsite@studio.it"]


def test_has_cv_true_excludes_every_lead_false_includes_them(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(_application("cv@studio.it"), PDF, "cv.pdf", "application/pdf")
    service.apply(_application("nocv@studio.it"), None, "", "")
    _signup(clean_hub, "lead@studio.it")

    talenti = TalentiService(clean_hub)
    with_cv = talenti.list_recent(has_cv=True)
    assert [item.email for item in with_cv.items] == ["cv@studio.it"]
    assert with_cv.per_stato["lead"] == 0

    without_cv = talenti.list_recent(has_cv=False)
    assert {item.email for item in without_cv.items} == {"nocv@studio.it", "lead@studio.it"}


def test_con_accessi_filters_by_whether_the_address_ever_logged_in(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    read, _ = service.apply(_application("logged@studio.it"), PDF, "cv.pdf", "application/pdf")
    service.apply(_application("never@studio.it"), PDF, "cv.pdf", "application/pdf")
    user_id = clean_hub.execute(
        text("SELECT user_id FROM freelancers WHERE id = :id"), {"id": str(read.id)}
    ).scalar()
    clean_hub.add(Login(user_id=user_id))
    clean_hub.commit()

    logged_in = TalentiService(clean_hub).list_recent(con_accessi=True)
    assert [item.email for item in logged_in.items] == ["logged@studio.it"]

    never_logged_in = TalentiService(clean_hub).list_recent(con_accessi=False)
    assert [item.email for item in never_logged_in.items] == ["never@studio.it"]


def test_created_between_and_utm_source_narrow_both_sides_of_the_merge(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(_application("card@studio.it", utm=None), PDF, "cv.pdf", "application/pdf")
    _signup(clean_hub, "lead@studio.it", utm=SignupUtm(utm_source="newsletter"))
    _signup(clean_hub, "other@studio.it", utm=SignupUtm(utm_source="conferenza"))

    talenti = TalentiService(clean_hub)
    by_utm = talenti.list_recent(utm_source="newsletter")
    assert [item.email for item in by_utm.items] == ["lead@studio.it"]

    in_the_future = talenti.list_recent(creato_da=datetime(2099, 1, 1, tzinfo=UTC))
    assert in_the_future.items == [] and in_the_future.totale == 0


def test_per_stato_respects_an_active_search(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(_application("ada@studio.it", cognome="Rossi"), PDF, "cv.pdf", "application/pdf")
    service.apply(
        _application("bob@studio.it", cognome="Bianchi"), PDF, "cv.pdf", "application/pdf"
    )

    filtered = TalentiService(clean_hub).list_recent(q="Rossi")
    assert filtered.per_stato == {
        "nuovo": 1,
        "contattato": 0,
        "attivo": 0,
        "scartato": 0,
        "lead": 0,
    }
    assert filtered.totale == 1


@pytest.mark.parametrize("origine", ["form", "wizard", "admin"])
def test_origine_filters_the_value_each_row_shows(clean_hub: Session, origine: str) -> None:
    emails = _three_channels(clean_hub)
    listed = TalentiService(clean_hub).list_recent(origine=origine)
    assert [item.email for item in listed.items] == [emails[origine]]
    assert {item.origine for item in listed.items} == {origine}
    assert listed.totale == 1
    # the unfiltered list shows each of the three values, so the filter is the row.
    assert {item.origine for item in TalentiService(clean_hub).list_recent().items} == {
        "form",
        "wizard",
        "admin",
    }


def test_origine_counts_follow_the_filter_on_both_sides(clean_hub: Session) -> None:
    _three_channels(clean_hub)
    talenti = TalentiService(clean_hub)
    assert talenti.list_recent(origine="form").per_stato["lead"] == 1
    assert talenti.list_recent(origine="form").per_stato["nuovo"] == 0
    assert talenti.list_recent(origine="wizard").per_stato["lead"] == 0
    assert talenti.list_recent(origine="wizard").per_stato["nuovo"] == 1


def test_pagina_origine_is_the_marketing_page_the_old_origine_filter_was(
    clean_hub: Session,
) -> None:
    emails = _three_channels(clean_hub)
    talenti = TalentiService(clean_hub)
    assert [i.email for i in talenti.list_recent(pagina_origine="home").items] == [emails["wizard"]]
    assert [i.email for i in talenti.list_recent(pagina_origine="pigrocrm").items] == [
        emails["admin"]
    ]
    assert talenti.list_recent(pagina_origine="home").per_stato["lead"] == 0
    both = talenti.list_recent(origine="admin", pagina_origine="home")
    assert both.items == [] and both.totale == 0


def test_an_unknown_origine_is_refused_instead_of_answering_nobody(clean_hub: Session) -> None:
    with pytest.raises(ValidationFailed, match="form, wizard, admin"):
        TalentiService(clean_hub).list_recent(origine="home")


# ---- REB-798: the row of one freelancer is the list's row -----------------------------


def test_row_for_freelancer_is_the_row_the_list_shows(clean_hub: Session) -> None:
    ada = _apply(clean_hub, "ada@studio.it", tariffa_giornaliera=Decimal("320"))
    _with_card(
        clean_hub,
        ada,
        {
            "ruolo": "Data engineer",
            "seniority": "senior",
            "anni": 9,
            "competenze": ["SQL Server"],
            "settori": ["fintech", 3],
            "lingue": ["inglese"],
            "luogo": "Monza",
            "sintesi": "Costruisce pipeline.",
        },
    )
    listed = next(i for i in TalentiService(clean_hub).list_recent().items if i.id == ada)
    single = TalentiService(clean_hub).row_for_freelancer(ada)
    assert single == listed
    assert (single.settori, single.lingue, single.sintesi) == (
        ["fintech"],
        ["inglese"],
        "Costruisce pipeline.",
    )
    assert single.fascia is not None and single.fascia.min <= 320 * 1.3 <= (
        single.fascia.max or 10**6
    )


def test_row_for_freelancer_of_an_unknown_id_is_not_found(clean_hub: Session) -> None:
    with pytest.raises(NotFound):
        TalentiService(clean_hub).row_for_freelancer(UUID(int=7))


def test_a_page_costs_a_fixed_number_of_statements_whatever_the_table_holds(
    clean_hub: Session,
) -> None:
    """REB-891/REB-804: the cards, the leads, the duplicate flags (two reads) and the counts
    (one statement for every state and the leads) are five reads for a page of cards and
    leads together, and a longer table does not add to them."""

    def reads_for(people: int) -> int:
        for n in range(people):
            FreelancerService(clean_hub).apply(_application(f"card{n}@studio.it", nome=f"Nome{n}"))
            _signup(clean_hub, f"lead{n}@studio.it")
        seen: list[str] = []
        engine = clean_hub.get_bind()

        def record(*args: object) -> None:
            seen.append(str(args[2]))

        event.listen(engine, "before_cursor_execute", record)
        try:
            TalentiService(clean_hub).list_recent(limit=50)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    assert reads_for(3) == reads_for(20) == 5
