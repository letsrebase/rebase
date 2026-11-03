"""`talents`: freelancer cards and bare sign-ups as one list, `status` `lead` for the bare
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
from talents_fixtures import PDF, _application, _apply, _draft, _signup, _three_channels, _with_card

from rebase_core.admin_schemas import StatusChange
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.models import FreelancerCard, Login
from rebase_core.signup_schemas import SignupUtm
from rebase_core.talents import TalentsService


def test_a_bare_signup_is_a_lead_and_a_card_is_its_own_state(clean_hub: Session) -> None:
    FreelancerService(clean_hub).apply(
        _application("ada@studio.it"), PDF, "cv.pdf", "application/pdf"
    )
    _signup(clean_hub, "lead@studio.it")
    listed = TalentsService(clean_hub).list_recent()
    by_email = {item.email: item for item in listed.items}
    assert by_email["ada@studio.it"].status == "new"
    assert by_email["ada@studio.it"].origin == "wizard"
    assert by_email["lead@studio.it"].status == "lead"
    assert by_email["lead@studio.it"].origin == "form"
    assert listed.total == 2


def test_a_card_carries_its_own_rate_and_mode_a_lead_carries_neither(clean_hub: Session) -> None:
    """REB-558: `daily_rate` and `remote` are a card's own two columns, so a
    bare sign-up, which has neither, reads `None` for both."""
    FreelancerService(clean_hub).apply(
        _application("ada@studio.it", daily_rate=Decimal("450.00"), remote="remote"),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    _signup(clean_hub, "lead@studio.it")
    listed = TalentsService(clean_hub).list_recent()
    by_email = {item.email: item for item in listed.items}
    assert by_email["ada@studio.it"].daily_rate == Decimal("450.00")
    assert by_email["ada@studio.it"].remote == "remote"
    assert by_email["lead@studio.it"].daily_rate is None
    assert by_email["lead@studio.it"].remote is None


def test_a_row_carries_the_vetted_date_and_whether_an_anonymous_card_exists(
    clean_hub: Session,
) -> None:
    """REB-518: «Talenti» shows the «Verificato» pill and knows which talent the team
    builder can propose, with or without a search term (which adds a score column)."""
    service = FreelancerService(clean_hub)
    ada, _ = service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "application/pdf")
    grace, _ = service.apply(
        _application("grace@studio.it", first_name="Grace", last_name="Hopper"),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    _signup(clean_hub, "lead@studio.it")
    service.set_vetted(ada.id, True, an_admin(clean_hub))
    clean_hub.add(
        FreelancerCard(freelancer_id=ada.id, cv_sha256="0" * 64, card={"role": "Backend developer"})
    )
    # A card the writer retired: only the failure is left, which is no card.
    clean_hub.add(
        FreelancerCard(freelancer_id=grace.id, error="rifiutata", error_cv_sha256="1" * 64)
    )
    clean_hub.commit()

    for q in (None, "studio"):
        by_email = {item.email: item for item in TalentsService(clean_hub).list_recent(q=q).items}
        assert by_email["ada@studio.it"].vetted_at is not None
        assert by_email["ada@studio.it"].has_anonymous_card is True
        assert by_email["grace@studio.it"].vetted_at is None
        assert by_email["grace@studio.it"].has_anonymous_card is False
        assert by_email["lead@studio.it"].vetted_at is None
        assert by_email["lead@studio.it"].has_anonymous_card is False


def test_a_signup_whose_address_already_has_a_card_is_not_also_a_lead(clean_hub: Session) -> None:
    FreelancerService(clean_hub).apply(
        _application("ada@studio.it"), PDF, "cv.pdf", "application/pdf"
    )
    _signup(clean_hub, "ADA@studio.it")  # same address, different case: has a card already
    listed = TalentsService(clean_hub).list_recent()
    assert [item.email for item in listed.items] == ["ada@studio.it"]
    assert listed.total == 1
    assert listed.by_status["lead"] == 0


def test_to_card_drafted_by_an_admin_from_research_is_origin_admin(clean_hub: Session) -> None:
    signup_id = _signup(clean_hub, "ricerca@studio.it")
    FreelancerService(clean_hub).draft_from_signup(signup_id, _draft(), an_admin(clean_hub), "web")
    listed = TalentsService(clean_hub).list_recent()
    item = next(item for item in listed.items if item.email == "ricerca@studio.it")
    assert item.origin == "admin" and item.status == "new"
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
        ids[0], StatusChange(status="contacted"), admin_id=an_admin(clean_hub), source="web"
    )
    _signup(clean_hub, "lead1@studio.it")
    _signup(clean_hub, "lead2@studio.it")
    listed = TalentsService(clean_hub).list_recent(limit=1)
    assert listed.by_status == {"new": 2, "contacted": 1, "active": 0, "discarded": 0, "lead": 2}
    assert len(listed.items) == 1  # the page is bounded, the counts are not
    # and the merge kept the newest row across both sources
    assert [item.email for item in listed.items] == ["lead2@studio.it"]


def test_the_list_is_newest_first_across_both_tables(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(_application("old@studio.it"), PDF, "cv.pdf", "")
    _signup(clean_hub, "middle@studio.it")
    service.apply(_application("new@studio.it"), PDF, "cv.pdf", "")
    listed = TalentsService(clean_hub).list_recent()
    assert [item.email for item in listed.items] == [
        "new@studio.it",
        "middle@studio.it",
        "old@studio.it",
    ]


def test_the_status_filter_selects_leads_or_to_single_freelancer_state(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "")
    _signup(clean_hub, "lead@studio.it")

    only_leads = TalentsService(clean_hub).list_recent(status="lead")
    assert [item.email for item in only_leads.items] == ["lead@studio.it"]
    assert only_leads.total == 1

    only_new = TalentsService(clean_hub).list_recent(status="new")
    assert [item.email for item in only_new.items] == ["ada@studio.it"]
    assert only_new.total == 1

    # A filter that never lands in the database is simply empty, like
    # `FreelancerService.list_recent`'s own pass-through filter.
    unknown = TalentsService(clean_hub).list_recent(status="forse")
    assert unknown.items == [] and unknown.total == 0


def test_an_empty_hub_answers_zero_of_everything(clean_hub: Session) -> None:
    listed = TalentsService(clean_hub).list_recent()
    assert listed.total == 0
    assert listed.items == []
    assert listed.by_status == {"new": 0, "contacted": 0, "active": 0, "discarded": 0, "lead": 0}


# ---- search and cursor pagination (REB-285) -------------------------------------------


def test_search_hits_a_partial_surname_and_an_email_domain(clean_hub: Session) -> None:
    FreelancerService(clean_hub).apply(
        _application("ada@rossilab.it", last_name="Rossi"), PDF, "cv.pdf", "application/pdf"
    )
    FreelancerService(clean_hub).apply(
        _application("bob@other.it", first_name="Bob", last_name="Bianchi"),
        PDF,
        "cv.pdf",
        "application/pdf",
    )

    by_surname = TalentsService(clean_hub).list_recent(q="oss")
    assert [item.email for item in by_surname.items] == ["ada@rossilab.it"]

    by_domain = TalentsService(clean_hub).list_recent(q="rossilab.it")
    assert [item.email for item in by_domain.items] == ["ada@rossilab.it"]


def test_the_cursor_walks_every_row_once_with_no_dupes_or_gaps(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    for i in range(4):
        service.apply(
            _application(f"card{i}@studio.it", last_name=f"Card{i}"),
            PDF,
            "cv.pdf",
            "application/pdf",
        )
    for i in range(4):
        _signup(clean_hub, f"lead{i}@studio.it")

    talents = TalentsService(clean_hub)
    full = talents.list_recent(limit=100)
    assert full.total == 8 and len(full.items) == 8

    seen: list[UUID] = []
    cursor: str | None = None
    for _ in range(20):  # generous upper bound: 8 rows over a page size of 3
        page = talents.list_recent(limit=3, cursor=cursor)
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
        TalentsService(clean_hub).list_recent(cursor="not-a-valid-cursor")


# ---- filters (REB-285) ------------------------------------------------------------------


def test_card_only_filters_narrow_the_cards_and_drop_every_lead(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(
        _application(
            "remote@studio.it",
            remote="remote",
            daily_rate=Decimal("300.00"),
            job_title="Backend developer",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    service.apply(
        _application(
            "onsite@studio.it",
            remote="on_site",
            daily_rate=Decimal("800.00"),
            job_title="Designer",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    _signup(clean_hub, "lead@studio.it")

    talents = TalentsService(clean_hub)
    only_remote = talents.list_recent(remote="remote")
    assert [item.email for item in only_remote.items] == ["remote@studio.it"]
    # A lead has no `remote` of its own: a card-only filter drops it from the merge
    # and from the per-state count beside it, not just from `items`.
    assert only_remote.by_status["lead"] == 0

    by_rate = talents.list_recent(min_rate=Decimal("500"))
    assert [item.email for item in by_rate.items] == ["onsite@studio.it"]

    by_position = talents.list_recent(job_title="design")
    assert [item.email for item in by_position.items] == ["onsite@studio.it"]


def test_has_cv_true_excludes_every_lead_false_includes_them(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(_application("cv@studio.it"), PDF, "cv.pdf", "application/pdf")
    service.apply(_application("nocv@studio.it"), None, "", "")
    _signup(clean_hub, "lead@studio.it")

    talents = TalentsService(clean_hub)
    with_cv = talents.list_recent(has_cv=True)
    assert [item.email for item in with_cv.items] == ["cv@studio.it"]
    assert with_cv.by_status["lead"] == 0

    without_cv = talents.list_recent(has_cv=False)
    assert {item.email for item in without_cv.items} == {"nocv@studio.it", "lead@studio.it"}


def test_with_logins_filters_by_whether_the_address_ever_logged_in(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    read, _ = service.apply(_application("logged@studio.it"), PDF, "cv.pdf", "application/pdf")
    service.apply(_application("never@studio.it"), PDF, "cv.pdf", "application/pdf")
    user_id = clean_hub.execute(
        text("SELECT user_id FROM freelancers WHERE id = :id"), {"id": str(read.id)}
    ).scalar()
    clean_hub.add(Login(user_id=user_id))
    clean_hub.commit()

    logged_in = TalentsService(clean_hub).list_recent(with_logins=True)
    assert [item.email for item in logged_in.items] == ["logged@studio.it"]

    never_logged_in = TalentsService(clean_hub).list_recent(with_logins=False)
    assert [item.email for item in never_logged_in.items] == ["never@studio.it"]


def test_created_between_and_utm_source_narrow_both_sides_of_the_merge(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(_application("card@studio.it", utm=None), PDF, "cv.pdf", "application/pdf")
    _signup(clean_hub, "lead@studio.it", utm=SignupUtm(utm_source="newsletter"))
    _signup(clean_hub, "other@studio.it", utm=SignupUtm(utm_source="conferenza"))

    talents = TalentsService(clean_hub)
    by_utm = talents.list_recent(utm_source="newsletter")
    assert [item.email for item in by_utm.items] == ["lead@studio.it"]

    in_the_future = talents.list_recent(created_from=datetime(2099, 1, 1, tzinfo=UTC))
    assert in_the_future.items == [] and in_the_future.total == 0


def test_by_status_respects_an_active_search(clean_hub: Session) -> None:
    service = FreelancerService(clean_hub)
    service.apply(
        _application("ada@studio.it", last_name="Rossi"), PDF, "cv.pdf", "application/pdf"
    )
    service.apply(
        _application("bob@studio.it", last_name="Bianchi"), PDF, "cv.pdf", "application/pdf"
    )

    filtered = TalentsService(clean_hub).list_recent(q="Rossi")
    assert filtered.by_status == {
        "new": 1,
        "contacted": 0,
        "active": 0,
        "discarded": 0,
        "lead": 0,
    }
    assert filtered.total == 1


@pytest.mark.parametrize("origin", ["form", "wizard", "admin"])
def test_origin_filters_the_value_each_row_shows(clean_hub: Session, origin: str) -> None:
    emails = _three_channels(clean_hub)
    listed = TalentsService(clean_hub).list_recent(origin=origin)
    assert [item.email for item in listed.items] == [emails[origin]]
    assert {item.origin for item in listed.items} == {origin}
    assert listed.total == 1
    # the unfiltered list shows each of the three values, so the filter is the row.
    assert {item.origin for item in TalentsService(clean_hub).list_recent().items} == {
        "form",
        "wizard",
        "admin",
    }


def test_origin_counts_follow_the_filter_on_both_sides(clean_hub: Session) -> None:
    _three_channels(clean_hub)
    talents = TalentsService(clean_hub)
    assert talents.list_recent(origin="form").by_status["lead"] == 1
    assert talents.list_recent(origin="form").by_status["new"] == 0
    assert talents.list_recent(origin="wizard").by_status["lead"] == 0
    assert talents.list_recent(origin="wizard").by_status["new"] == 1


def test_origin_page_is_the_marketing_page_the_old_origin_filter_was(
    clean_hub: Session,
) -> None:
    emails = _three_channels(clean_hub)
    talents = TalentsService(clean_hub)
    assert [i.email for i in talents.list_recent(origin_page="home").items] == [emails["wizard"]]
    assert [i.email for i in talents.list_recent(origin_page="pigrocrm").items] == [emails["admin"]]
    assert talents.list_recent(origin_page="home").by_status["lead"] == 0
    both = talents.list_recent(origin="admin", origin_page="home")
    assert both.items == [] and both.total == 0


def test_an_unknown_origin_is_refused_instead_of_answering_nobody(clean_hub: Session) -> None:
    with pytest.raises(ValidationFailed, match="form, wizard, admin"):
        TalentsService(clean_hub).list_recent(origin="home")


# ---- REB-798: the row of one freelancer is the list's row -----------------------------


def test_row_for_freelancer_is_the_row_the_list_shows(clean_hub: Session) -> None:
    ada = _apply(clean_hub, "ada@studio.it", daily_rate=Decimal("320"))
    _with_card(
        clean_hub,
        ada,
        {
            "role": "Data engineer",
            "seniority": "senior",
            "experience_years": 9,
            "skills": ["SQL Server"],
            "industries": ["fintech", 3],
            "languages": ["inglese"],
            "location": "Monza",
            "profile_summary": "Costruisce pipeline.",
        },
    )
    listed = next(i for i in TalentsService(clean_hub).list_recent().items if i.id == ada)
    single = TalentsService(clean_hub).row_for_freelancer(ada)
    assert single == listed
    assert (single.industries, single.languages, single.profile_summary) == (
        ["fintech"],
        ["inglese"],
        "Costruisce pipeline.",
    )
    assert single.band is not None and single.band.min <= 320 * 1.3 <= (single.band.max or 10**6)


def test_row_for_freelancer_of_an_unknown_id_is_not_found(clean_hub: Session) -> None:
    with pytest.raises(NotFound):
        TalentsService(clean_hub).row_for_freelancer(UUID(int=7))


def test_a_page_costs_a_fixed_number_of_statements_whatever_the_table_holds(
    clean_hub: Session,
) -> None:
    """REB-891/REB-804: the cards, the leads, the duplicate flags (two reads) and the counts
    (one statement for every state and the leads) are five reads for a page of cards and
    leads together, and a longer table does not add to them."""

    def reads_for(people: int) -> int:
        for n in range(people):
            FreelancerService(clean_hub).apply(
                _application(f"card{n}@studio.it", first_name=f"Nome{n}")
            )
            _signup(clean_hub, f"lead{n}@studio.it")
        seen: list[str] = []
        engine = clean_hub.get_bind()

        def record(*args: object) -> None:
            seen.append(str(args[2]))

        event.listen(engine, "before_cursor_execute", record)
        try:
            TalentsService(clean_hub).list_recent(limit=50)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    assert reads_for(3) == reads_for(20) == 5
