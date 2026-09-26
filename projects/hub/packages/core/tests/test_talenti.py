"""`talenti`: freelancer cards and bare sign-ups as one list, `stato` `lead` for the
bare ones (REB-282), searchable and cursor-paginated (REB-285)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.errors import ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.models import FreelancerCard, Login, User
from rebase_core.schemas import (
    FreelancerCreate,
    FreelancerDraft,
    SignupCreate,
    SignupUtm,
    StatusChange,
)
from rebase_core.service import SignupService
from rebase_core.talenti import TalentiService

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM admin_actions"))
    hub_session.execute(text("DELETE FROM comments"))
    hub_session.execute(text("DELETE FROM freelancers"))
    hub_session.execute(text("DELETE FROM companies"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def _application(email: str = "ada@studio.it", **extra: object) -> FreelancerCreate:
    payload: dict[str, object] = {
        "nome": "Ada",
        "cognome": "Lovelace",
        "email": email,
        "tariffa_giornaliera": Decimal("450.00"),
        "posizione": "Backend developer",
        "remoto": "remoto",
        "links": ["https://github.com/ada"],
    }
    payload.update(extra)
    return FreelancerCreate(**payload)  # type: ignore[arg-type]


def _signup(session: Session, email: str = "lead@studio.it", **extra: object) -> UUID:
    payload: dict[str, object] = {"email": email, "nome": "Nuovo", "cognome": "Arrivato"}
    payload.update(extra)
    return SignupService(session).subscribe(SignupCreate(**payload)).id  # type: ignore[arg-type]


def _draft(**extra: object) -> FreelancerDraft:
    payload: dict[str, object] = {
        "nome": "Ada",
        "cognome": "Lovelace",
        "linkedin_url": "https://www.linkedin.com/in/ada",
        "posizione": "Backend developer",
        "links": ["https://github.com/ada"],
        "fonti": ["https://www.linkedin.com/in/ada"],
    }
    payload.update(extra)
    return FreelancerDraft(**payload)  # type: ignore[arg-type]


def test_a_bare_signup_is_a_lead_and_a_card_is_its_own_state(clean: Session) -> None:
    FreelancerService(clean).apply(_application("ada@studio.it"), PDF, "cv.pdf", "application/pdf")
    _signup(clean, "lead@studio.it")
    listed = TalentiService(clean).list_recent()
    by_email = {item.email: item for item in listed.items}
    assert by_email["ada@studio.it"].stato == "nuovo"
    assert by_email["ada@studio.it"].origine == "wizard"
    assert by_email["lead@studio.it"].stato == "lead"
    assert by_email["lead@studio.it"].origine == "form"
    assert listed.totale == 2


def test_a_row_carries_the_vetted_date_and_whether_an_anonymous_card_exists(
    clean: Session,
) -> None:
    """REB-518: «Talenti» shows the «Verificato» pill and knows which talent the team
    builder can propose, with or without a search term (which adds a score column)."""
    service = FreelancerService(clean)
    ada, _ = service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "application/pdf")
    grace, _ = service.apply(
        _application("grace@studio.it", nome="Grace", cognome="Hopper"),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    _signup(clean, "lead@studio.it")
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    clean.add(admin)
    clean.commit()
    service.set_vetted(ada.id, True, admin.id)
    clean.add(
        FreelancerCard(
            freelancer_id=ada.id, cv_sha256="0" * 64, card={"ruolo": "Backend developer"}
        )
    )
    # A card the writer retired: only the failure is left, which is no card.
    clean.add(FreelancerCard(freelancer_id=grace.id, error="rifiutata", error_cv_sha256="1" * 64))
    clean.commit()

    for q in (None, "studio"):
        by_email = {item.email: item for item in TalentiService(clean).list_recent(q=q).items}
        assert by_email["ada@studio.it"].vetted_at is not None
        assert by_email["ada@studio.it"].ha_scheda_anonima is True
        assert by_email["grace@studio.it"].vetted_at is None
        assert by_email["grace@studio.it"].ha_scheda_anonima is False
        assert by_email["lead@studio.it"].vetted_at is None
        assert by_email["lead@studio.it"].ha_scheda_anonima is False


def test_a_signup_whose_address_already_has_a_card_is_not_also_a_lead(clean: Session) -> None:
    FreelancerService(clean).apply(_application("ada@studio.it"), PDF, "cv.pdf", "application/pdf")
    _signup(clean, "ADA@studio.it")  # same address, different case: has a card already
    listed = TalentiService(clean).list_recent()
    assert [item.email for item in listed.items] == ["ada@studio.it"]
    assert listed.totale == 1
    assert listed.per_stato["lead"] == 0


def test_a_card_drafted_by_an_admin_from_research_is_origine_admin(clean: Session) -> None:
    signup_id = _signup(clean, "ricerca@studio.it")
    FreelancerService(clean).draft_from_signup(signup_id, _draft(), "Claude")
    listed = TalentiService(clean).list_recent()
    item = next(item for item in listed.items if item.email == "ricerca@studio.it")
    assert item.origine == "admin" and item.stato == "nuovo"
    assert item.id != signup_id  # the card has its own row, and its own id


def test_counts_per_state_include_the_leads_and_are_not_bounded_by_the_limit(
    clean: Session,
) -> None:
    service = FreelancerService(clean)
    ids = [
        service.apply(_application(email=f"p{i}@studio.it"), PDF, "cv.pdf", "")[0].id
        for i in range(3)
    ]
    service.set_status(ids[0], StatusChange(stato="contattato"))
    _signup(clean, "lead1@studio.it")
    _signup(clean, "lead2@studio.it")
    listed = TalentiService(clean).list_recent(limit=1)
    assert listed.per_stato == {"nuovo": 2, "contattato": 1, "attivo": 0, "scartato": 0, "lead": 2}
    assert len(listed.items) == 1  # the page is bounded, the counts are not
    # and the merge kept the newest row across both sources
    assert [item.email for item in listed.items] == ["lead2@studio.it"]


def test_the_list_is_newest_first_across_both_tables(clean: Session) -> None:
    service = FreelancerService(clean)
    service.apply(_application("old@studio.it"), PDF, "cv.pdf", "")
    _signup(clean, "middle@studio.it")
    service.apply(_application("new@studio.it"), PDF, "cv.pdf", "")
    listed = TalentiService(clean).list_recent()
    assert [item.email for item in listed.items] == [
        "new@studio.it",
        "middle@studio.it",
        "old@studio.it",
    ]


def test_the_stato_filter_selects_leads_or_a_single_freelancer_state(clean: Session) -> None:
    service = FreelancerService(clean)
    service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "")
    _signup(clean, "lead@studio.it")

    only_leads = TalentiService(clean).list_recent(stato="lead")
    assert [item.email for item in only_leads.items] == ["lead@studio.it"]
    assert only_leads.totale == 1

    only_new = TalentiService(clean).list_recent(stato="nuovo")
    assert [item.email for item in only_new.items] == ["ada@studio.it"]
    assert only_new.totale == 1

    # A filter that never lands in the database is simply empty, like
    # `FreelancerService.list_recent`'s own pass-through filter.
    unknown = TalentiService(clean).list_recent(stato="forse")
    assert unknown.items == [] and unknown.totale == 0


def test_an_empty_hub_answers_zero_of_everything(clean: Session) -> None:
    listed = TalentiService(clean).list_recent()
    assert listed.totale == 0
    assert listed.items == []
    assert listed.per_stato == {"nuovo": 0, "contattato": 0, "attivo": 0, "scartato": 0, "lead": 0}


# ---- search and cursor pagination (REB-285) -------------------------------------------


def test_search_hits_a_partial_surname_and_an_email_domain(clean: Session) -> None:
    FreelancerService(clean).apply(
        _application("ada@rossilab.it", cognome="Rossi"), PDF, "cv.pdf", "application/pdf"
    )
    FreelancerService(clean).apply(
        _application("bob@other.it", nome="Bob", cognome="Bianchi"),
        PDF,
        "cv.pdf",
        "application/pdf",
    )

    by_surname = TalentiService(clean).list_recent(q="oss")
    assert [item.email for item in by_surname.items] == ["ada@rossilab.it"]

    by_domain = TalentiService(clean).list_recent(q="rossilab.it")
    assert [item.email for item in by_domain.items] == ["ada@rossilab.it"]


def test_the_cursor_walks_every_row_once_with_no_dupes_or_gaps(clean: Session) -> None:
    service = FreelancerService(clean)
    for i in range(4):
        service.apply(
            _application(f"card{i}@studio.it", cognome=f"Card{i}"), PDF, "cv.pdf", "application/pdf"
        )
    for i in range(4):
        _signup(clean, f"lead{i}@studio.it")

    talenti = TalentiService(clean)
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


def test_a_malformed_cursor_is_refused(clean: Session) -> None:
    with pytest.raises(ValidationFailed):
        TalentiService(clean).list_recent(cursor="not-a-valid-cursor")


# ---- filters (REB-285) ------------------------------------------------------------------


def test_card_only_filters_narrow_the_cards_and_drop_every_lead(clean: Session) -> None:
    service = FreelancerService(clean)
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
    _signup(clean, "lead@studio.it")

    talenti = TalentiService(clean)
    only_remote = talenti.list_recent(remoto="remoto")
    assert [item.email for item in only_remote.items] == ["remote@studio.it"]
    # A lead has no `remoto` of its own: a card-only filter drops it from the merge
    # and from the per-state count beside it, not just from `items`.
    assert only_remote.per_stato["lead"] == 0

    by_rate = talenti.list_recent(tariffa_min=Decimal("500"))
    assert [item.email for item in by_rate.items] == ["onsite@studio.it"]

    by_posizione = talenti.list_recent(posizione="design")
    assert [item.email for item in by_posizione.items] == ["onsite@studio.it"]


def test_has_cv_true_excludes_every_lead_false_includes_them(clean: Session) -> None:
    service = FreelancerService(clean)
    service.apply(_application("cv@studio.it"), PDF, "cv.pdf", "application/pdf")
    service.apply(_application("nocv@studio.it"), None, "", "")
    _signup(clean, "lead@studio.it")

    talenti = TalentiService(clean)
    with_cv = talenti.list_recent(has_cv=True)
    assert [item.email for item in with_cv.items] == ["cv@studio.it"]
    assert with_cv.per_stato["lead"] == 0

    without_cv = talenti.list_recent(has_cv=False)
    assert {item.email for item in without_cv.items} == {"nocv@studio.it", "lead@studio.it"}


def test_con_accessi_filters_by_whether_the_address_ever_logged_in(clean: Session) -> None:
    service = FreelancerService(clean)
    read, _ = service.apply(_application("logged@studio.it"), PDF, "cv.pdf", "application/pdf")
    service.apply(_application("never@studio.it"), PDF, "cv.pdf", "application/pdf")
    user_id = clean.execute(
        text("SELECT user_id FROM freelancers WHERE id = :id"), {"id": str(read.id)}
    ).scalar()
    clean.add(Login(user_id=user_id))
    clean.commit()

    logged_in = TalentiService(clean).list_recent(con_accessi=True)
    assert [item.email for item in logged_in.items] == ["logged@studio.it"]

    never_logged_in = TalentiService(clean).list_recent(con_accessi=False)
    assert [item.email for item in never_logged_in.items] == ["never@studio.it"]


def test_created_between_and_utm_source_narrow_both_sides_of_the_merge(clean: Session) -> None:
    service = FreelancerService(clean)
    service.apply(_application("card@studio.it", utm=None), PDF, "cv.pdf", "application/pdf")
    _signup(clean, "lead@studio.it", utm=SignupUtm(utm_source="newsletter"))
    _signup(clean, "other@studio.it", utm=SignupUtm(utm_source="conferenza"))

    talenti = TalentiService(clean)
    by_utm = talenti.list_recent(utm_source="newsletter")
    assert [item.email for item in by_utm.items] == ["lead@studio.it"]

    in_the_future = talenti.list_recent(creato_da=datetime(2099, 1, 1, tzinfo=UTC))
    assert in_the_future.items == [] and in_the_future.totale == 0


def test_per_stato_respects_an_active_search(clean: Session) -> None:
    service = FreelancerService(clean)
    service.apply(_application("ada@studio.it", cognome="Rossi"), PDF, "cv.pdf", "application/pdf")
    service.apply(
        _application("bob@studio.it", cognome="Bianchi"), PDF, "cv.pdf", "application/pdf"
    )

    filtered = TalentiService(clean).list_recent(q="Rossi")
    assert filtered.per_stato == {
        "nuovo": 1,
        "contattato": 0,
        "attivo": 0,
        "scartato": 0,
        "lead": 0,
    }
    assert filtered.totale == 1
