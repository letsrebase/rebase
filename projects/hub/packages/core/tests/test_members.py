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
from pydantic import ValidationError
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core.comments import CommentService
from rebase_core.companies import CompanyService
from rebase_core.config import Settings
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.members import MemberService
from rebase_core.models import Freelancer
from rebase_core.schemas import (
    CompanyCreate,
    CompanyFields,
    CompanyUpdate,
    FreelancerCreate,
    MemberProfile,
    MemberUpdate,
    SignupUtm,
    StatusChange,
)
from rebase_core.users import UserService

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"

GOOD = {
    "nome": "Ada",
    "cognome": "Lovelace",
    "linkedin_url": "https://www.linkedin.com/in/ada",
    "tariffa_giornaliera": "450",
    "posizione": "Backend developer",
    "remoto": "remoto",
    "links": ["https://github.com/ada", " "],
}

GOOD_COMPANY = {
    "progetto": "Serve un backend developer per tre mesi, da ottobre.",
    "periodo_da": date(2026, 10, 1),
    "durata": "3 mesi",
    "budget_giornaliero": Decimal("500"),
    "remoto": "remoto",
    "numero_risorse": 1,
    "figura_richiesta": "Backend developer",
}


def test_company_update_accepts_only_the_eight_project_answers() -> None:
    update = CompanyUpdate(**GOOD_COMPANY)
    assert update.budget_giornaliero == Decimal("500") and update.durata == "3 mesi"
    with pytest.raises(ValidationError):
        CompanyUpdate(**{**GOOD_COMPANY, "stato": "chiuso"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CompanyUpdate(**{**GOOD_COMPANY, "note": "qualcosa"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        CompanyUpdate(**{**GOOD_COMPANY, "nome_azienda": "ACME"})  # type: ignore[arg-type]


def test_member_update_applies_the_wizards_rules_and_nothing_else() -> None:
    update = MemberUpdate(**GOOD)
    assert update.nome == "Ada" and update.tariffa_giornaliera == Decimal("450")
    with pytest.raises(ValidationError):
        MemberUpdate(**{**GOOD, "email": "ada@studio.it"})  # type: ignore[arg-type]


def test_freelancer_create_strips_blank_links() -> None:
    assert FreelancerCreate(**GOOD, email="ada@studio.it").links == ["https://github.com/ada"]


def test_member_profile_carries_no_admin_field() -> None:
    fields = set(MemberProfile.model_fields)
    assert {"nome", "cognome", "email", "cv_filename", "cv_size", "links"} <= fields
    assert not fields & {"stato", "note", "utm_source", "utm_campaign", "cv_bytes"}


@pytest.fixture
def members(hub_engine: Engine, hub_session: Session) -> MemberService:
    yield MemberService(hub_session)
    hub_session.rollback()
    for table in ("sessions", "magic_link_tokens", "comments", "companies", "freelancers", "users"):
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
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(**GOOD, email=email), PDF, "Ada CV.pdf", "application/pdf"
    )
    return read.id


def _draft_card(session: Session, email: str = "ada@studio.it") -> UUID:
    from rebase_core.schemas import FreelancerDraft, SignupCreate
    from rebase_core.service import SignupService

    signup = SignupService(session).subscribe(
        SignupCreate(email=email, nome="Ada", cognome="Lovelace")
    )
    draft = FreelancerDraft(
        nome="Ada",
        cognome="Lovelace",
        posizione="Backend developer",
        fonti=["https://www.linkedin.com/in/ada"],
    )
    return FreelancerService(session).draft_from_signup(signup.id, draft, "Claude").id


def _request_company(session: Session, email: str = "wile@acme.it", **extra: object) -> UUID:
    payload: dict[str, object] = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": email,
        "telefono": "+39 345 1234567",
        **GOOD_COMPANY,
    }
    payload.update(extra)
    row, _ = CompanyService(session).request(CompanyCreate(**payload))  # type: ignore[arg-type]
    return row.id


def test_an_update_changes_the_row_and_leaves_one_comment_naming_what_moved(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _apply(hub_session)
    FreelancerService(hub_session).set_status(
        freelancer_id, StatusChange(stato="contattato", note="da sentire")
    )

    unchanged = members.update(freelancer_id, MemberUpdate(**GOOD))
    assert unchanged.tariffa_giornaliera == Decimal("450")
    assert CommentService(hub_session).list("freelancer", freelancer_id) == []

    changed = members.update(
        freelancer_id,
        MemberUpdate(**{**GOOD, "tariffa_giornaliera": "500", "links": []}),
    )
    assert changed.tariffa_giornaliera == Decimal("500") and changed.links == []
    thread = CommentService(hub_session).list("freelancer", freelancer_id)
    assert len(thread) == 1
    assert thread[0].testo == "Profilo aggiornato dalla persona: tariffa giornaliera, link"
    assert thread[0].autore == "Ada Lovelace"

    admin_view = FreelancerService(hub_session).get(freelancer_id)
    assert (admin_view.stato, admin_view.note) == ("contattato", "da sentire")


def test_a_name_change_is_also_written_onto_the_linked_user(
    members: MemberService, hub_session: Session
) -> None:
    """`GET /me` reads `nome`/`cognome`/`linkedin_url` off `users`, so a member's own
    edit here has to land there too, in the same commit (REB-278's "no dual write")."""
    freelancer_id = _apply(hub_session)
    row = hub_session.scalar(select(Freelancer).where(Freelancer.id == freelancer_id))
    assert row is not None
    members.update(freelancer_id, MemberUpdate(**{**GOOD, "nome": "Augusta", "cognome": "King"}))
    user = UserService(hub_session).by_email("ada@studio.it")
    assert user is not None
    assert (user.nome, user.cognome) == ("Augusta", "King")


def test_a_new_cv_is_checked_like_the_wizards_and_leaves_its_comment(
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
    thread = CommentService(hub_session).list("freelancer", freelancer_id)
    assert [comment.testo for comment in thread] == ["CV aggiornato dalla persona"]


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
    assert refused.value.details["entity"] == "scheda"


def test_me_read_answers_the_identity_and_the_card_together(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _apply(hub_session)
    user = UserService(hub_session).by_email("ada@studio.it")
    assert user is not None
    me = members.me_read(user.id)
    assert me.ha_scheda is True and me.nome == "Ada" and me.role == "member"
    assert me.cv_filename == "Ada CV.pdf" and me.completa is True

    bare = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    bare_read = members.me_read(bare.id)
    assert bare_read.ha_scheda is False
    assert (bare_read.cv_filename, bare_read.tariffa_giornaliera, bare_read.links) == (
        None,
        None,
        [],
    )
    assert bare_read.completa is False
    assert freelancer_id  # the fixture's card is untouched by the bare read


def test_a_person_can_carry_both_a_card_and_a_company_at_once(
    members: MemberService, hub_session: Session
) -> None:
    """REB-314: the two are independent, and either or both may be there."""
    _apply(hub_session, email="ada@studio.it")
    _request_company(hub_session, email="ada@studio.it")
    user = UserService(hub_session).by_email("ada@studio.it")
    assert user is not None
    me = members.me_read(user.id)
    assert me.ha_scheda is True and me.ha_azienda is True


def test_me_read_answers_the_identity_and_the_most_recent_company_together(
    members: MemberService, hub_session: Session
) -> None:
    """REB-314 decision: self-edit, and `me_read`, reach only the newest of a
    company's several requests. REB-380: `telefono` and the four `azienda_`
    fields come along too, off that same newest row."""
    _request_company(hub_session, durata="1 mese")
    newest_id = _request_company(
        hub_session, durata="3 mesi", remoto="ibrido", giorni_presenza=3, numero_risorse=2
    )
    user = UserService(hub_session).by_email("wile@acme.it")
    assert user is not None
    me = members.me_read(user.id)
    assert me.ha_azienda is True and me.durata == "3 mesi"
    assert me.telefono == "+39 345 1234567"
    assert (me.azienda_remoto, me.azienda_giorni_presenza, me.azienda_numero_risorse) == (
        "ibrido",
        3,
        2,
    )
    assert me.azienda_figura_richiesta == "Backend developer"
    assert members.require_company(user.id).id == newest_id

    bare = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    bare_read = members.me_read(bare.id)
    assert bare_read.ha_azienda is False
    assert (
        bare_read.progetto,
        bare_read.periodo_da,
        bare_read.durata,
        bare_read.budget_giornaliero,
        bare_read.azienda_remoto,
        bare_read.azienda_giorni_presenza,
        bare_read.azienda_numero_risorse,
        bare_read.azienda_figura_richiesta,
    ) == (None, None, None, None, None, None, None, None)


def test_a_company_update_changes_the_most_recent_row_and_leaves_one_comment(
    members: MemberService, hub_session: Session
) -> None:
    older_id = _request_company(hub_session, durata="1 mese")
    newest_id = _request_company(hub_session, durata="3 mesi")
    CompanyService(hub_session).set_status(
        newest_id, StatusChange(stato="in_corso", note="da richiamare")
    )
    user = UserService(hub_session).by_email("wile@acme.it")
    assert user is not None

    unchanged = members.update_company(user.id, CompanyUpdate(**GOOD_COMPANY))
    assert unchanged.durata == "3 mesi"
    assert CommentService(hub_session).list("company", newest_id) == []

    changed = members.update_company(
        user.id,
        CompanyUpdate(**{**GOOD_COMPANY, "durata": "4 mesi", "budget_giornaliero": Decimal("600")}),
    )
    assert changed.durata == "4 mesi" and changed.budget_giornaliero == Decimal("600")
    thread = CommentService(hub_session).list("company", newest_id)
    assert len(thread) == 1
    assert thread[0].testo == "Richiesta aggiornata dal referente: durata, budget giornaliero"
    assert thread[0].autore == "Wile E."

    # `stato`/`note` are the admin's, and the older request is untouched.
    admin_view = CompanyService(hub_session).get(newest_id)
    assert (admin_view.stato, admin_view.note) == ("in_corso", "da richiamare")
    older_view = CompanyService(hub_session).get(older_id)
    assert older_view.durata == "1 mese"


def test_a_signed_in_person_with_no_company_gets_a_named_404(
    members: MemberService, hub_session: Session
) -> None:
    user = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    assert members.company_for_user(user.id) is None
    with pytest.raises(NotFound) as refused:
        members.require_company(user.id)
    assert refused.value.details["entity"] == "azienda"
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
        durata="1 mese",
        utm=SignupUtm(utm_source="google", utm_campaign="lancio"),
    )
    user = UserService(hub_session).by_email("wile@acme.it")
    assert user is not None

    created = members.create_additional_request(
        user.id,
        CompanyFields(**{**GOOD_COMPANY, "durata": "6 mesi", "figura_richiesta": "Data engineer"}),
    )
    assert created.ha_azienda is True
    assert created.durata == "6 mesi" and created.azienda_figura_richiesta == "Data engineer"

    newest = members.require_company(user.id)
    assert newest.id != first_id
    assert newest.nome_azienda == "ACME Srl"  # carried forward, never asked again
    assert newest.utm_source is None and newest.utm_campaign is None  # never carried forward

    older = CompanyService(hub_session).get(first_id)
    assert older.durata == "1 mese" and older.nome_azienda == "ACME Srl"
    assert CompanyService(hub_session).list_recent().totale == 2


def test_an_additional_request_with_no_company_yet_is_the_same_named_404(
    members: MemberService, hub_session: Session
) -> None:
    user = UserService(hub_session).get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    with pytest.raises(NotFound) as refused:
        members.create_additional_request(user.id, CompanyFields(**GOOD_COMPANY))
    assert refused.value.details["entity"] == "azienda"


# ---- completing a card an admin wrote from a signup (ORB-155) -------------------------


def test_an_incomplete_card_reads_as_such_and_has_no_cv_to_download(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _draft_card(hub_session)
    profile = members.profile(freelancer_id)
    assert profile.completa is False
    assert (profile.cv_filename, profile.tariffa_giornaliera, profile.remoto) == (None, None, None)
    with pytest.raises(NotFound):
        members.cv(freelancer_id)


def test_a_wizard_card_that_came_without_a_cv_is_completed_from_the_area(
    members: MemberService, hub_session: Session
) -> None:
    """The promise the wizard makes when it lets somebody past the CV: the card is
    theirs already, it is simply not `completa`, and the upload here is what finishes
    it. The same ending as a card an admin drafted, from the other beginning."""
    freelancer_id = (
        FreelancerService(hub_session).apply(FreelancerCreate(**GOOD, email="ada@studio.it"))[0].id
    )
    assert members.profile(freelancer_id).completa is False
    with pytest.raises(NotFound):
        members.cv(freelancer_id)

    after_cv = members.replace_cv(freelancer_id, PDF, "Ada CV.pdf", "application/pdf")
    assert after_cv.completa is True and after_cv.cv_filename == "Ada CV.pdf"
    texts = [c.testo for c in CommentService(hub_session).list("freelancer", freelancer_id)]
    assert texts[0] == "CV caricato dalla persona"


def test_the_person_completes_the_card_and_takes_it_over(
    members: MemberService, hub_session: Session
) -> None:
    freelancer_id = _draft_card(hub_session)
    after_answers = members.update(freelancer_id, MemberUpdate(**GOOD))
    assert after_answers.completa is False  # the CV is still missing
    after_cv = members.replace_cv(freelancer_id, PDF, "Ada CV.pdf", "application/pdf")
    assert after_cv.completa is True and after_cv.cv_filename == "Ada CV.pdf"
    row = hub_session.scalar(select(Freelancer).where(Freelancer.id == freelancer_id))
    assert row is not None and row.compilata_da == "persona"
    texts = [c.testo for c in CommentService(hub_session).list("freelancer", freelancer_id)]
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
    # sends the very same answers, and the card still says «persona» afterwards.
    row = hub_session.scalar(select(Freelancer).where(Freelancer.id == freelancer_id))
    assert row is not None
    row.compilata_da = "admin"
    hub_session.commit()
    members.update(freelancer_id, MemberUpdate(**{**GOOD, "linkedin_url": None, "links": []}))
    hub_session.refresh(row)
    assert row.compilata_da == "persona"
    texts = [c.testo for c in CommentService(hub_session).list("freelancer", freelancer_id)]
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
    assert (before.totale, before.membri, before.membri_totali) == (0, 0, 2)
    assert FreelancerService(hub_session).get(ada).accessi == 0

    users = UserService(hub_session, settings)
    for _ in range(2):
        mail = users.request_link("ada@studio.it")
        assert mail is not None
        raw = mail.text.split("/entra?t=")[1].split()[0]
        assert users.enter(raw) is not None
    # A spent link and a wrong token open nothing, so they record nothing either.
    assert users.enter("non-un-token-vero-ma-lungo-abbastanza") is None

    card = FreelancerService(hub_session).get(ada)
    assert card.accessi == 2 and card.ultimo_accesso is not None
    listed = {item.email: item for item in FreelancerService(hub_session).list_recent().items}
    assert listed["ada@studio.it"].accessi == 2
    assert listed["bob@studio.it"].accessi == 0 and listed["bob@studio.it"].ultimo_accesso is None

    stats = LoginService(hub_session).stats()
    assert (stats.totale, stats.membri, stats.membri_totali, stats.ultimi_7_giorni) == (2, 1, 2, 2)
    assert [login.email for login in stats.recenti] == ["ada@studio.it", "ada@studio.it"]
    assert stats.recenti[0].logged_at >= stats.recenti[1].logged_at
    assert stats.recenti[0].user_id == ada_row.user_id


# ---- the welcome mailing (ORB-157) ------------------------------------------------------


def test_the_welcome_mailing_speaks_to_every_address_the_hub_knows(
    members: MemberService, hub_session: Session, settings: Settings
) -> None:
    from rebase_core.cli import send_welcome
    from rebase_core.mail import RecordingSender
    from rebase_core.schemas import SignupCreate
    from rebase_core.service import SignupService

    _apply(hub_session, "ada@studio.it")  # a card the person filled, no signup
    _draft_card(hub_session, "bruna@studio.it")  # a signup and a card we drafted
    SignupService(hub_session).subscribe(
        SignupCreate(email="carlo@studio.it", nome="Carlo", cognome="Verdi")
    )  # a signup and nothing else
    sender = RecordingSender()
    outcomes = send_welcome(hub_session, settings, sender, None)
    assert outcomes == [
        ("bruna@studio.it", "inviata (admin)"),
        ("carlo@studio.it", "inviata (nessuna)"),
        ("ada@studio.it", "inviata (persona)"),
    ]
    by_to = {mail.to: mail for mail in sender.sent}
    assert "http://localhost:5180/hub/accedi" in by_to["ada@studio.it"].text
    assert "scheda è completa" in by_to["ada@studio.it"].text
    assert "- Posizione: Backend developer" in by_to["bruna@studio.it"].text
    assert "offerta in linea" in by_to["bruna@studio.it"].text
    assert "http://localhost:5180/hub/freelance" in by_to["carlo@studio.it"].text
    assert by_to["carlo@studio.it"].text.startswith("Ciao Carlo,")

    named = send_welcome(
        hub_session, settings, RecordingSender(), ["ADA@studio.it", "nessuno@studio.it"]
    )
    assert named == [
        ("ada@studio.it", "inviata (persona)"),
        ("nessuno@studio.it", "indirizzo sconosciuto"),
    ]
    hub_session.execute(text("DELETE FROM signups"))
    hub_session.commit()
