"""The member area: the freelancer card, the referente's own company request, and
what each owner may change once in.

The way in -- the magic link, sessions, `resolve` -- moved to `rebase_core.users`
(REB-278) and is tested in `test_users.py`; this file keeps what is specific to the
freelancer card and, since REB-314, to a company contact's own most-recent request.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from admins import an_admin
from pydantic import ValidationError
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core.admin_schemas import StatusChange
from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate, CompanyFields, CompanyUpdate
from rebase_core.config import Settings
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancer_schemas import FreelancerCreate, MemberUpdate
from rebase_core.freelancers import FreelancerService
from rebase_core.member_schemas import MemberProfile
from rebase_core.members import MemberService
from rebase_core.models import Freelancer
from rebase_core.notes import NoteService
from rebase_core.signup_schemas import SignupUtm
from rebase_core.users import UserService

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"

GOOD = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "linkedin_url": "https://www.linkedin.com/in/ada",
    "daily_rate": "450",
    "job_title": "Backend developer",
    "remote": "remote",
    "links": ["https://github.com/ada", " "],
}

GOOD_COMPANY = {
    "project": "Serve un backend developer per tre mesi, da ottobre.",
    "period_from": date(2026, 10, 1),
    "duration": "3 mesi",
    "daily_budget": Decimal("500"),
    "remote": "remote",
    "headcount": 1,
    "requested_role": "Backend developer",
}


def test_company_update_accepts_only_the_eight_project_answers() -> None:
    update = CompanyUpdate(**GOOD_COMPANY)
    assert update.daily_budget == Decimal("500") and update.duration == "3 mesi"
    with pytest.raises(ValidationError):
        CompanyUpdate(**{**GOOD_COMPANY, "status": "closed"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CompanyUpdate(**{**GOOD_COMPANY, "note": "qualcosa"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CompanyUpdate(**{**GOOD_COMPANY, "company_name": "ACME"})  # type: ignore[arg-type]


def test_member_update_applies_the_wizards_rules_and_nothing_else() -> None:
    update = MemberUpdate(**GOOD)
    assert update.first_name == "Ada" and update.daily_rate == Decimal("450")
    with pytest.raises(ValidationError):
        MemberUpdate(**{**GOOD, "email": "ada@studio.it"})  # type: ignore[arg-type]


def test_freelancer_create_strips_blank_links() -> None:
    assert FreelancerCreate(**GOOD, email="ada@studio.it").links == ["https://github.com/ada"]


def test_member_profile_carries_no_admin_field() -> None:
    fields = set(MemberProfile.model_fields)
    assert {"first_name", "last_name", "email", "cv_filename", "cv_size", "links"} <= fields
    assert not fields & {"status", "note", "utm_source", "utm_campaign", "cv_bytes"}


@pytest.fixture
def members(hub_engine: Engine, hub_session: Session) -> MemberService:
    yield MemberService(hub_session)
    hub_session.rollback()
    for table in (
        "sessions",
        "magic_link_tokens",
        "admin_actions",
        "notes",
        "talent_cloud_grants",
        "companies",
        "freelancers",
        "users",
    ):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


@pytest.fixture
def settings(hub_engine: Engine) -> Settings:
    return Settings(
        database_url=hub_engine.url.render_as_string(hide_password=False),
        hub_url="http://localhost:5180/hub",
        _env_file=None,  # type: ignore[call-arg]
    )


def _apply(session: Session, email: str = "ada@studio.it") -> UUID:
    # One profile per person (REB-800): each address gets a profile of its own.
    profile = {**GOOD, "linkedin_url": f"https://www.linkedin.com/in/{email.split('@')[0]}"}
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(**profile, email=email), PDF, "Ada CV.pdf", "application/pdf"
    )
    return read.id


def _draft_card(session: Session, email: str = "ada@studio.it") -> UUID:
    from rebase_core.freelancer_schemas import FreelancerDraft
    from rebase_core.service import SignupService
    from rebase_core.signup_schemas import SignupCreate

    signup = SignupService(session).subscribe(
        SignupCreate(email=email, first_name="Ada", last_name="Lovelace")
    )
    draft = FreelancerDraft(
        first_name="Ada",
        last_name="Lovelace",
        job_title="Backend developer",
        sources=["https://www.linkedin.com/in/ada"],
    )
    return (
        FreelancerService(session).draft_from_signup(signup.id, draft, an_admin(session), "web").id
    )


def _request_company(session: Session, email: str = "wile@acme.it", **extra: object) -> UUID:
    payload: dict[str, object] = {
        "company_name": "ACME Srl",
        "contact_first_name": "Wile",
        "contact_last_name": "E.",
        "email": email,
        "phone": "+39 345 1234567",
        **GOOD_COMPANY,
    }
    payload.update(extra)
    row, _ = CompanyService(session).request(CompanyCreate(**payload))  # type: ignore[arg-type]
    return row.id


def test_an_update_changes_the_row_and_leaves_one_note_naming_what_moved(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _apply(hub_session)
    FreelancerService(hub_session).set_status(
        freelancer_id,
        StatusChange(status="contacted"),
        admin_id=an_admin(hub_session),
        source="web",
    )

    unchanged = members.update(freelancer_id, MemberUpdate(**GOOD))
    assert unchanged.daily_rate == Decimal("450")
    assert NoteService(hub_session).list("freelancer", freelancer_id) == []

    changed = members.update(
        freelancer_id,
        MemberUpdate(**{**GOOD, "daily_rate": "500", "links": []}),
    )
    assert changed.daily_rate == Decimal("500") and changed.links == []
    thread = NoteService(hub_session).list("freelancer", freelancer_id)
    assert len(thread) == 1
    assert thread[0].text == "Profilo aggiornato dalla persona: tariffa giornaliera, link"
    assert (thread[0].author, thread[0].source, thread[0].editable) == (
        "Ada Lovelace",
        "person",
        False,
    )

    admin_view = FreelancerService(hub_session).get(freelancer_id)
    assert admin_view.status == "contacted"


def test_a_name_change_is_also_written_onto_the_linked_user(
    members: MemberService, hub_session: Session
) -> None:
    """`GET /me` reads `nome`/`last_name`/`linkedin_url` off `users`, so a member's own
    edit here has to land there too, in the same commit (REB-278's "no dual write")."""
    freelancer_id = _apply(hub_session)
    row = hub_session.scalar(select(Freelancer).where(Freelancer.id == freelancer_id))
    assert row is not None
    members.update(
        freelancer_id, MemberUpdate(**{**GOOD, "first_name": "Augusta", "last_name": "King"})
    )
    user = UserService(hub_session).by_email("ada@studio.it")
    assert user is not None
    assert (user.first_name, user.last_name) == ("Augusta", "King")


def test_a_new_cv_is_checked_like_the_wizards_and_leaves_its_note(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _apply(hub_session)
    with pytest.raises(ValidationFailed) as refused:
        members.replace_cv(freelancer_id, b"non un pdf", "cv.pdf", "application/pdf")
    assert refused.value.details["field"] == "cv"

    new_pdf = PDF + b"\n% versione 2\n"
    profile = members.replace_cv(freelancer_id, new_pdf, "Ada 2026.pdf", "application/pdf")
    assert (profile.cv_filename, profile.cv_size) == ("Ada 2026.pdf", len(new_pdf))
    assert members.cv(freelancer_id).content == new_pdf
    thread = NoteService(hub_session).list("freelancer", freelancer_id)
    assert [comment.text for comment in thread] == ["CV aggiornato dalla persona"]


def test_a_row_that_is_not_there_is_not_found(members: MemberService) -> None:
    missing = UUID("00000000-0000-7000-8000-000000000000")
    with pytest.raises(NotFound):
        members.profile(missing)
    with pytest.raises(NotFound):
        members.update(missing, MemberUpdate(**GOOD))


def test_a_signed_in_person_with_no_card_gets_a_named_404(
    members: MemberService, hub_session: Session
) -> None:
    """An admin promoted with no freelancer card (§1): `card_for_user`/`require_card`
    answer the shape `GET /me`'s mutation routes need to refuse cleanly."""
    user = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    assert members.card_for_user(user.id) is None
    with pytest.raises(NotFound) as refused:
        members.require_card(user.id)
    assert refused.value.details["entity"] == "card"


def test_me_read_answers_the_identity_and_the_card_together(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _apply(hub_session)
    user = UserService(hub_session).by_email("ada@studio.it")
    assert user is not None
    me = members.me_read(user.id)
    assert me.has_card is True and me.first_name == "Ada" and me.role == "member"
    assert me.cv_filename == "Ada CV.pdf" and me.complete is True

    bare = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    bare_read = members.me_read(bare.id)
    assert bare_read.has_card is False
    assert (bare_read.cv_filename, bare_read.daily_rate, bare_read.links) == (
        None,
        None,
        [],
    )
    assert bare_read.complete is False
    assert freelancer_id  # the fixture's card is untouched by the bare read


def test_me_carries_talent_cloud(
    members: MemberService, hub_session: Session, settings: Settings
) -> None:
    """REB-518: the member area's «Talent cloud» shows while any grant of the person is
    live, whichever company it was opened for."""
    from rebase_core.cloud import TalentCloudService

    acme = _request_company(hub_session)
    second_company = _request_company(hub_session, company_name="Bianchi Srl")
    user = UserService(hub_session).by_email("wile@acme.it")
    assert user is not None
    assert members.me_read(user.id).talent_cloud is False

    admin = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Sala")
    cloud = TalentCloudService(hub_session, settings)
    cloud.grant(acme, admin.id)
    cloud.grant(second_company, admin.id)
    assert members.me_read(user.id).talent_cloud is True

    cloud.revoke(acme, admin.id)
    assert members.me_read(user.id).talent_cloud is True
    cloud.revoke(second_company, admin.id)
    assert members.me_read(user.id).talent_cloud is False
    # The admin who opened it is not a referente: nothing opened for them.
    assert members.me_read(admin.id).talent_cloud is False


def test_a_person_can_carry_both_a_card_and_a_company_at_once(
    members: MemberService, hub_session: Session
) -> None:
    """REB-314: the two are independent, and either or both may be there."""
    _apply(hub_session, email="ada@studio.it")
    _request_company(hub_session, email="ada@studio.it")
    user = UserService(hub_session).by_email("ada@studio.it")
    assert user is not None
    me = members.me_read(user.id)
    assert me.has_card is True and me.has_company is True


def test_me_read_lists_every_live_request_newest_first_with_nothing_the_admin_wrote(
    members: MemberService, hub_session: Session
) -> None:
    """REB-602: `requests` carries each request the person filed, newest first, each
    with its own id and answers, and never `status` or `note`. REB-380: `phone` comes
    along on the identity. `has_company` follows the list."""
    older_id = _request_company(hub_session, duration="1 mese")
    newest_id = _request_company(
        hub_session, duration="3 mesi", remote="hybrid", onsite_days=3, headcount=2
    )
    CompanyService(hub_session).set_status(
        older_id, StatusChange(status="in_progress"), admin_id=an_admin(hub_session), source="web"
    )
    user = UserService(hub_session).by_email("wile@acme.it")
    assert user is not None
    me = members.me_read(user.id)
    assert me.has_company is True
    assert me.phone == "+39 345 1234567"
    assert [item.id for item in me.requests] == [newest_id, older_id]
    newest, older = me.requests
    assert (newest.duration, newest.remote, newest.onsite_days, newest.headcount) == (
        "3 mesi",
        "hybrid",
        3,
        2,
    )
    assert (older.duration, older.remote, older.onsite_days) == ("1 mese", "remote", None)
    assert newest.requested_role == "Backend developer"
    assert members.require_company(user.id).id == newest_id
    dumped = me.model_dump()["requests"][0]
    assert "status" not in dumped and "note" not in dumped and "company_name" not in dumped

    bare = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    bare_read = members.me_read(bare.id)
    assert bare_read.has_company is False and bare_read.requests == []


def test_a_company_update_by_id_changes_that_row_and_leaves_one_note(
    members: MemberService, hub_session: Session
) -> None:
    older_id = _request_company(hub_session, duration="1 mese")
    newest_id = _request_company(hub_session, duration="3 mesi")
    CompanyService(hub_session).set_status(
        older_id, StatusChange(status="in_progress"), admin_id=an_admin(hub_session), source="web"
    )
    user = UserService(hub_session).by_email("wile@acme.it")
    assert user is not None

    unchanged = members.update_company(
        user.id, CompanyUpdate(**{**GOOD_COMPANY, "duration": "1 mese"}), older_id
    )
    assert [item.duration for item in unchanged.requests] == ["3 mesi", "1 mese"]
    assert NoteService(hub_session).list("company", older_id) == []

    changed = members.update_company(
        user.id,
        CompanyUpdate(**{**GOOD_COMPANY, "duration": "4 mesi", "daily_budget": Decimal("600")}),
        older_id,
    )
    older = next(item for item in changed.requests if item.id == older_id)
    assert older.duration == "4 mesi" and older.daily_budget == Decimal("600")
    thread = NoteService(hub_session).list("company", older_id)
    assert len(thread) == 1
    assert thread[0].text == "Richiesta aggiornata dal referente: durata, budget giornaliero"
    assert (thread[0].author, thread[0].source) == ("Wile E.", "person")

    # `status` is the admin's, and the newer request is untouched.
    admin_view = CompanyService(hub_session).get(older_id)
    assert admin_view.status == "in_progress"
    assert CompanyService(hub_session).get(newest_id).duration == "3 mesi"


def test_a_company_update_without_an_id_changes_the_newest_row(
    members: MemberService, hub_session: Session
) -> None:
    older_id = _request_company(hub_session, duration="1 mese")
    newest_id = _request_company(hub_session, duration="3 mesi")
    user = UserService(hub_session).by_email("wile@acme.it")
    assert user is not None

    members.update_company(user.id, CompanyUpdate(**{**GOOD_COMPANY, "duration": "5 mesi"}))
    assert CompanyService(hub_session).get(newest_id).duration == "5 mesi"
    assert CompanyService(hub_session).get(older_id).duration == "1 mese"


def test_a_request_of_somebody_else_is_a_named_404_and_stays_untouched(
    members: MemberService, hub_session: Session
) -> None:
    theirs = _request_company(hub_session, email="wile@acme.it", duration="1 mese")
    _request_company(hub_session, email="road@runner.it", duration="2 mesi")
    me = UserService(hub_session).by_email("road@runner.it")
    assert me is not None

    with pytest.raises(NotFound) as refused:
        members.update_company(me.id, CompanyUpdate(**GOOD_COMPANY), theirs)
    assert refused.value.details["entity"] == "company"
    assert CompanyService(hub_session).get(theirs).duration == "1 mese"


def test_a_signed_in_person_with_no_company_gets_a_named_404(
    members: MemberService, hub_session: Session
) -> None:
    user = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    assert members.company_for_user(user.id) is None
    with pytest.raises(NotFound) as refused:
        members.require_company(user.id)
    assert refused.value.details["entity"] == "company"
    with pytest.raises(NotFound):
        members.update_company(user.id, CompanyUpdate(**GOOD_COMPANY))


# ---- filing a genuinely new request instead of editing (REB-381) ----------------------


def test_an_additional_request_carries_the_name_forward_as_a_new_row(
    members: MemberService, hub_session: Session
) -> None:
    """REB-381: a fresh `Company` row, not an edit -- the company's name comes along,
    nothing else does (no UTM, unlike a public wizard submission), and the request
    self-edit already reaches stands exactly as it was."""
    first_id = _request_company(
        hub_session,
        duration="1 mese",
        utm=SignupUtm(utm_source="google", utm_campaign="lancio"),
    )
    user = UserService(hub_session).by_email("wile@acme.it")
    assert user is not None

    created, created_id = members.create_additional_request(
        user.id,
        CompanyFields(**{**GOOD_COMPANY, "duration": "6 mesi", "requested_role": "Data engineer"}),
    )
    assert created.has_company is True
    assert [item.duration for item in created.requests] == ["6 mesi", "1 mese"]
    assert created.requests[0].requested_role == "Data engineer"

    newest = members.require_company(user.id)
    assert newest.id == created_id != first_id
    assert newest.crm_status == "to_link"  # REB-868: queued in the same transaction
    assert newest.company_name == "ACME Srl"  # carried forward, never asked again
    assert newest.utm_source is None and newest.utm_campaign is None  # never carried forward

    older = CompanyService(hub_session).get(first_id)
    assert older.duration == "1 mese" and older.company_name == "ACME Srl"
    assert CompanyService(hub_session).list_recent().total == 2


def test_an_additional_request_with_no_company_yet_is_the_same_named_404(
    members: MemberService, hub_session: Session
) -> None:
    user = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    with pytest.raises(NotFound) as refused:
        members.create_additional_request(user.id, CompanyFields(**GOOD_COMPANY))
    assert refused.value.details["entity"] == "company"


# ---- completing a card an admin wrote from a signup (ORB-155) -------------------------


def test_an_incomplete_card_reads_as_such_and_has_no_cv_to_download(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _draft_card(hub_session)
    profile = members.profile(freelancer_id)
    assert profile.complete is False
    assert (profile.cv_filename, profile.daily_rate, profile.remote) == (None, None, None)
    with pytest.raises(NotFound):
        members.cv(freelancer_id)


def test_a_wizard_card_that_came_without_a_cv_is_completed_from_the_area(
    members: MemberService, hub_session: Session
) -> None:
    """The promise the wizard makes when it lets somebody past the CV: the card is
    theirs already, it is simply not `complete`, and the upload here is what finishes
    it. The same ending as a card an admin drafted, from the other beginning."""
    freelancer_id = (
        FreelancerService(hub_session).apply(FreelancerCreate(**GOOD, email="ada@studio.it"))[0].id
    )
    assert members.profile(freelancer_id).complete is False
    with pytest.raises(NotFound):
        members.cv(freelancer_id)

    after_cv = members.replace_cv(freelancer_id, PDF, "Ada CV.pdf", "application/pdf")
    assert after_cv.complete is True and after_cv.cv_filename == "Ada CV.pdf"
    texts = [c.text for c in NoteService(hub_session).list("freelancer", freelancer_id)]
    assert texts[0] == "CV caricato dalla persona"


def test_the_person_completes_the_card_and_takes_it_over(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _draft_card(hub_session)
    after_answers = members.update(freelancer_id, MemberUpdate(**GOOD))
    assert after_answers.complete is False  # the CV is still missing
    after_cv = members.replace_cv(freelancer_id, PDF, "Ada CV.pdf", "application/pdf")
    assert after_cv.complete is True and after_cv.cv_filename == "Ada CV.pdf"
    row = hub_session.scalar(select(Freelancer).where(Freelancer.id == freelancer_id))
    assert row is not None and row.filled_by == "person"
    texts = [c.text for c in NoteService(hub_session).list("freelancer", freelancer_id)]
    assert texts[0] == "CV caricato dalla persona"
    assert texts[1].startswith("Profilo aggiornato dalla persona: ")
    assert "tariffa giornaliera" in texts[1] and "modalità di lavoro" in texts[1]


def test_confirming_a_researched_card_unchanged_still_makes_it_the_persons(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _draft_card(hub_session)
    members.update(
        freelancer_id,
        MemberUpdate(**{**GOOD, "linkedin_url": None, "links": []}),
    )
    # Nothing but the rate and the remote option moved the first time; the second call
    # sends the very same answers, and the card still says «person» afterwards.
    row = hub_session.scalar(select(Freelancer).where(Freelancer.id == freelancer_id))
    assert row is not None
    row.filled_by = "admin"
    hub_session.commit()
    members.update(freelancer_id, MemberUpdate(**{**GOOD, "linkedin_url": None, "links": []}))
    hub_session.refresh(row)
    assert row.filled_by == "person"
    texts = [c.text for c in NoteService(hub_session).list("freelancer", freelancer_id)]
    assert texts[0] == "Scheda confermata dalla persona"


# ---- who entered, and when (ORB-158) ----------------------------------------------------


def test_entering_is_recorded_and_the_card_and_the_stats_read_it_back(
    members: MemberService, hub_session: Session, settings: Settings
) -> None:
    from rebase_core.logins import LoginService

    ada = _apply(hub_session, "ada@studio.it")
    _apply(hub_session, "bob@studio.it")
    ada_row = hub_session.scalar(select(Freelancer).where(Freelancer.id == ada))
    assert ada_row is not None
    before = LoginService(hub_session).stats()
    assert (before.total, before.members, before.total_members) == (0, 0, 2)
    assert FreelancerService(hub_session).get(ada).logins == 0

    users = UserService(hub_session, settings)
    for _ in range(2):
        mail = users.request_link("ada@studio.it")
        assert mail is not None
        raw = mail.text.split("/verify?t=")[1].split()[0]
        assert users.enter(raw) is not None
    # A spent link and a wrong token open nothing, so they record nothing either.
    assert users.enter("non-un-token-vero-ma-lungo-abbastanza") is None

    card = FreelancerService(hub_session).get(ada)
    assert card.logins == 2 and card.last_access is not None
    listed = {item.email: item for item in FreelancerService(hub_session).list_recent().items}
    assert listed["ada@studio.it"].logins == 2
    assert listed["bob@studio.it"].logins == 0 and listed["bob@studio.it"].last_access is None

    stats = LoginService(hub_session).stats()
    assert (stats.total, stats.members, stats.total_members, stats.last_7_days) == (2, 1, 2, 2)
    assert [login.email for login in stats.recent] == ["ada@studio.it", "ada@studio.it"]
    assert stats.recent[0].logged_at >= stats.recent[1].logged_at
    assert stats.recent[0].user_id == ada_row.user_id


# ---- the welcome mailing (ORB-157) ------------------------------------------------------


def test_the_welcome_mailing_speaks_to_every_address_the_hub_knows(
    members: MemberService, hub_session: Session, settings: Settings
) -> None:
    from rebase_core.cli.members import send_welcome
    from rebase_core.mail import RecordingSender
    from rebase_core.service import SignupService
    from rebase_core.signup_schemas import SignupCreate

    _apply(hub_session, "ada@studio.it")  # a card the person filled, no signup
    _draft_card(hub_session, "bruna@studio.it")  # a signup and a card we drafted
    SignupService(hub_session).subscribe(
        SignupCreate(email="carlo@studio.it", first_name="Carlo", last_name="Verdi")
    )  # a signup and nothing else
    sender = RecordingSender()
    outcomes = send_welcome(hub_session, settings, sender, None)
    assert outcomes == [
        ("bruna@studio.it", "sent (admin)"),
        ("carlo@studio.it", "sent (none)"),
        ("ada@studio.it", "sent (person)"),
    ]
    by_to = {mail.to: mail for mail in sender.sent}
    assert "http://localhost:5180/hub/login" in by_to["ada@studio.it"].text
    assert "scheda è completa" in by_to["ada@studio.it"].text
    assert "- Posizione: Backend developer" in by_to["bruna@studio.it"].text
    assert "offerta in linea" in by_to["bruna@studio.it"].text
    assert "http://localhost:5180/hub/freelance" in by_to["carlo@studio.it"].text
    assert by_to["carlo@studio.it"].text.startswith("Ciao Carlo,")

    named = send_welcome(
        hub_session, settings, RecordingSender(), ["ADA@studio.it", "nessuno@studio.it"]
    )
    assert named == [
        ("ada@studio.it", "sent (person)"),
        ("nessuno@studio.it", "unknown address"),
    ]
    hub_session.execute(text("DELETE FROM signups"))
    hub_session.commit()
