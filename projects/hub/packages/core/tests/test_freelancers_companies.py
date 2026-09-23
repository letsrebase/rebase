"""The two wizards' rows: what is kept, what is refused, what an admin may change."""

import json
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.config import Settings
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService, check_cv
from rebase_core.models import CV_MAX_BYTES, Freelancer, Login
from rebase_core.perks import PerkService
from rebase_core.schemas import (
    CompanyCreate,
    FreelancerCreate,
    FreelancerDraft,
    SignupUtm,
    StatusChange,
)

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


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


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM comments"))
    hub_session.execute(text("DELETE FROM freelancers"))
    hub_session.execute(text("DELETE FROM companies"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def test_an_application_is_stored_with_its_cv_and_read_back_without_the_bytes(
    clean: Session,
) -> None:
    service = FreelancerService(clean)
    read, created = service.apply(
        _application(utm=SignupUtm(utm_source="linkedin")), PDF, "Ada CV.pdf", "application/pdf"
    )
    assert created is True
    assert read.email == "ada@studio.it"
    assert (read.cv_filename, read.cv_mime, read.cv_size) == (
        "Ada CV.pdf",
        "application/pdf",
        len(PDF),
    )
    assert read.tariffa_giornaliera == Decimal("450.00")
    assert read.links == ["https://github.com/ada"]
    assert read.stato == "nuovo" and read.utm_source == "linkedin"
    assert "cv_bytes" not in type(read).model_fields
    cv = service.cv(read.id)
    assert (cv.filename, cv.mime, cv.content) == ("Ada CV.pdf", "application/pdf", PDF)


def test_a_second_application_from_the_same_address_leaves_the_card_unchanged(
    clean: Session,
) -> None:
    """REB-272: this route is public and unauthenticated, so a second application
    proves nothing about who is sending it. It changes nothing on the card already
    there -- not the fields, not the status an admin set, not the CV -- and answers
    with the same read the first application produced."""
    service = FreelancerService(clean)
    first, _ = service.apply(_application(), PDF, "cv.pdf", "application/pdf")
    service.set_status(first.id, StatusChange(stato="contattato", note="ha risposto"))
    again, created_again = service.apply(
        _application(
            email="ADA@studio.it", posizione="Tech lead", tariffa_giornaliera=Decimal("600")
        ),
        PDF + b"v2",
        "cv-2.pdf",
        "application/pdf",
    )
    assert created_again is False
    assert again.id == first.id
    assert (again.posizione, again.tariffa_giornaliera, again.cv_filename) == (
        "Backend developer",
        Decimal("450.00"),
        "cv.pdf",
    )
    assert (again.stato, again.note) == ("contattato", "ha risposto")
    assert service.cv(again.id).content == PDF
    assert clean.execute(text("SELECT count(*) FROM freelancers")).scalar() == 1


def test_apply_reports_whether_it_wrote_a_new_card(clean: Session) -> None:
    service = FreelancerService(clean)
    _, first_created = service.apply(_application())
    assert first_created is True
    _, second_created = service.apply(_application(posizione="Tech lead"))
    assert second_created is False


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "serve il CV"),
        (b"not a pdf at all", "deve essere un PDF"),
        (b"%PDF-" + b"x" * CV_MAX_BYTES, "al massimo 5 MB"),
    ],
)
def test_a_cv_that_is_not_a_small_pdf_is_refused_naming_the_field(
    content: bytes, reason: str
) -> None:
    with pytest.raises(ValidationFailed) as refused:
        check_cv(content, "cv.pdf", "application/pdf")
    assert refused.value.details["field"] == "cv"
    assert reason in refused.value.details["reason"]


def test_the_filename_keeps_only_its_last_segment() -> None:
    name, mime = check_cv(PDF, "..\\..\\etc\\Ada.pdf", "application/octet-stream")
    assert (name, mime) == ("Ada.pdf", "application/pdf")


@pytest.mark.parametrize(
    "bad",
    [
        {"tariffa_giornaliera": Decimal("0")},
        {"tariffa_giornaliera": Decimal("100000")},
        {"remoto": "quando capita"},
        {"links": ["http://insecure.example/x"]},
        {"links": ["https://ok.example/" + "x" * 300]},
        {"links": [f"https://l{i}.example/" for i in range(11)]},
        {"posizione": "   "},
        {"linkedin_url": "https://example.com/ada"},
        {"nome": "Ada‮"},
    ],
)
def test_an_application_outside_the_form_is_refused_before_the_database(
    bad: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _application(**bad)


def test_links_are_trimmed_and_blank_ones_dropped() -> None:
    assert _application(links=["  https://ada.dev/ ", "", "   "]).links == ["https://ada.dev/"]


def test_the_list_is_newest_first_filters_by_state_and_counts_the_whole(clean: Session) -> None:
    service = FreelancerService(clean)
    ids = [
        service.apply(_application(email=f"p{i}@studio.it"), PDF, "cv.pdf", "")[0].id
        for i in range(3)
    ]
    service.set_status(ids[0], StatusChange(stato="scartato"))
    page = service.list_recent(limit=2)
    assert page.totale == 3 and [item.id for item in page.items] == [ids[2], ids[1]]
    only_new = service.list_recent(stato="nuovo")
    assert only_new.totale == 2 and {item.id for item in only_new.items} == {ids[1], ids[2]}


def test_a_state_outside_the_four_is_refused_and_a_missing_row_is_not_found(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application(), PDF, "cv.pdf", "")
    with pytest.raises(ValidationFailed):
        service.set_status(row.id, StatusChange(stato="forse"))
    with pytest.raises(NotFound):
        service.get(uuid4())


def test_a_company_request_is_a_row_every_time(clean: Session) -> None:
    service = CompanyService(clean)
    data = CompanyCreate(
        nome_azienda="ACME Srl",
        referente_nome="Wile",
        referente_cognome="E.",
        email="Wile@ACME.it",
        telefono="+39 345 1234567",
        figura_richiesta="Backend developer",
        progetto="Serve un backend developer\nper tre mesi, da settembre.",
        periodo_da=date(2026, 10, 1),
        durata="3 mesi",
        budget_giornaliero=Decimal("500"),
        remoto="remoto",
        numero_risorse=1,
    )
    first, first_existed = service.request(data)
    second, second_existed = service.request(data)
    assert first.id != second.id
    assert first.email == "wile@acme.it"
    assert "\n" in first.progetto
    assert first_existed is False and second_existed is True
    assert service.list_recent().totale == 2
    moved = service.set_status(first.id, StatusChange(stato="in_corso", note="  "))
    assert (moved.stato, moved.note) == ("in_corso", None)
    with pytest.raises(ValidationFailed):
        service.set_status(first.id, StatusChange(stato="aperto"))


@pytest.mark.parametrize(
    "bad",
    [
        {"progetto": "   "},
        {"progetto": "ok\x00"},
        {"budget_giornaliero": Decimal("-1")},
        {"nome_azienda": ""},
        {"numero_risorse": 0},
        {"figura_richiesta": ""},
        {"remoto": "ibrido", "giorni_presenza": None},
        {"remoto": "remoto", "giorni_presenza": 2},
    ],
)
def test_a_company_request_outside_the_form_is_refused(bad: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": "wile@acme.it",
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Un progetto",
        "periodo_da": date(2026, 10, 1),
        "durata": "3 mesi",
        "budget_giornaliero": Decimal("500"),
        "remoto": "remoto",
        "numero_risorse": 1,
    }
    payload.update(bad)
    with pytest.raises(ValidationError):
        CompanyCreate(**payload)  # type: ignore[arg-type]


# ---- a card born from a signup (ORB-155) ---------------------------------------------


def _signup(session: Session, email: str = "ada@studio.it", **extra: object) -> UUID:
    from rebase_core.schemas import SignupCreate
    from rebase_core.service import SignupService

    payload: dict[str, object] = {"email": email, "nome": "Ada", "cognome": "Lovelace"}
    payload.update(extra)
    read = SignupService(session).subscribe(SignupCreate(**payload))  # type: ignore[arg-type]
    return read.id


def _draft(**extra: object) -> FreelancerDraft:
    payload: dict[str, object] = {
        "nome": "Ada",
        "cognome": "Lovelace",
        "linkedin_url": "https://www.linkedin.com/in/ada",
        "posizione": "Backend developer",
        "links": ["https://github.com/ada"],
        "fonti": ["https://www.linkedin.com/in/ada", "https://ada.dev"],
    }
    payload.update(extra)
    return FreelancerDraft(**payload)  # type: ignore[arg-type]


def test_a_draft_needs_a_source_and_takes_only_https_ones() -> None:
    assert _draft().tariffa_giornaliera is None and _draft().remoto is None
    for bad in ({"fonti": []}, {"fonti": ["http://ada.dev"]}, {"posizione": "   "}):
        with pytest.raises(ValidationError):
            _draft(**bad)


def test_a_card_from_a_signup_is_incomplete_and_carries_the_signup_attribution(
    clean: Session,
) -> None:
    signup_id = _signup(clean, utm=SignupUtm(utm_source="openai"))
    read = FreelancerService(clean).draft_from_signup(signup_id, _draft(), "Claude")
    assert read.email == "ada@studio.it" and read.utm_source == "openai"
    assert read.compilata_da == "admin" and read.completa is False
    assert (read.cv_filename, read.cv_size, read.tariffa_giornaliera, read.remoto) == (
        None,
        None,
        None,
        None,
    )
    assert read.posizione == "Backend developer" and read.links == ["https://github.com/ada"]
    assert read.stato == "nuovo"
    assert len(read.commenti) == 1
    comment = read.commenti[0]
    assert comment.autore == "Claude"
    assert comment.testo.startswith("Scheda creata dall'iscrizione del ")
    assert "https://www.linkedin.com/in/ada" in comment.testo and "https://ada.dev" in comment.testo
    # «Iscrizioni» can now point at it.
    from rebase_core.service import SignupService

    item = SignupService(clean).list_recent().iscrizioni[0]
    assert item.freelancer_id == read.id


def test_a_second_research_replaces_the_researched_fields_and_leaves_the_admins(
    clean: Session,
) -> None:
    service = FreelancerService(clean)
    signup_id = _signup(clean)
    first = service.draft_from_signup(signup_id, _draft(), "Claude")
    service.set_status(first.id, StatusChange(stato="contattato", note="chiamata fatta"))
    again = service.draft_from_signup(
        signup_id, _draft(posizione="CTO", fonti=["https://ada.dev/about"]), "Claude"
    )
    assert again.id == first.id
    assert again.posizione == "CTO"
    assert (again.stato, again.note) == ("contattato", "chiamata fatta")
    assert again.commenti[0].testo.startswith("Scheda aggiornata dalla ricerca.")
    assert len(again.commenti) == 2


def test_research_never_overwrites_a_card_the_person_filled(clean: Session) -> None:
    service = FreelancerService(clean)
    service.apply(_application(), PDF, "Ada CV.pdf", "application/pdf")
    signup_id = _signup(clean)
    with pytest.raises(ValidationFailed) as refused:
        service.draft_from_signup(signup_id, _draft(posizione="CTO"), "Claude")
    assert refused.value.details["field"] == "email"
    assert service.list_recent().items[0].posizione == "Backend developer"


def test_a_draft_on_an_unknown_signup_is_not_found(clean: Session) -> None:
    with pytest.raises(NotFound):
        FreelancerService(clean).draft_from_signup(uuid4(), _draft(), "Claude")


def test_the_wizard_does_not_take_over_a_researched_card(clean: Session) -> None:
    """REB-272: an admin's draft is taken over only from the member area, once the
    person is behind their own session -- never by an unauthenticated repost to the
    same address, which would let anyone who knows it claim the card."""
    service = FreelancerService(clean)
    signup_id = _signup(clean)
    drafted = service.draft_from_signup(signup_id, _draft(), "Claude")
    assert drafted.compilata_da == "admin"
    applied, created = service.apply(_application(), PDF, "Ada CV.pdf", "application/pdf")
    assert created is False
    assert applied.id == drafted.id
    assert applied.compilata_da == "admin" and applied.completa is False


def test_an_application_without_a_cv_is_stored_and_waits_for_one(clean: Session) -> None:
    """The CV is optional in the wizard, so `apply` takes none: the card exists, it is
    not `completa`, and there is nothing to download until the person adds it from
    their area."""
    service = FreelancerService(clean)
    read, _ = service.apply(_application())
    assert (read.cv_filename, read.cv_mime, read.cv_size) == (None, None, None)
    assert read.completa is False
    with pytest.raises(NotFound) as missing:
        service.cv(read.id)
    assert missing.value.details["entity"] == "cv"


def test_an_empty_cv_is_refused_while_no_cv_at_all_is_not(clean: Session) -> None:
    """`None` is "nobody attached a file"; `b""` is a file with nothing in it. The
    second is a refusal, as it always was, and the caller is the one who can tell them
    apart."""
    service = FreelancerService(clean)
    with pytest.raises(ValidationFailed) as refused:
        service.apply(_application(), b"", "cv.pdf", "application/pdf")
    assert refused.value.details["field"] == "cv"
    assert service.apply(_application())[0].cv_filename is None


def test_a_card_without_a_cv_has_none_to_download(clean: Session) -> None:
    service = FreelancerService(clean)
    drafted = service.draft_from_signup(_signup(clean), _draft(), "Claude")
    with pytest.raises(NotFound) as missing:
        service.cv(drafted.id)
    assert missing.value.details["entity"] == "cv"


def test_a_card_says_whether_its_address_also_signed_up_on_the_landing(clean: Session) -> None:
    service = FreelancerService(clean)
    wizard_only, _ = service.apply(_application("solo@studio.it"), PDF, "cv.pdf", "application/pdf")
    _signup(clean, "Ada@studio.it")
    both, _ = service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "application/pdf")
    listed = {item.email: item.provenienza for item in service.list_recent().items}
    assert listed == {"solo@studio.it": "landing", "ada@studio.it": "form"}
    assert service.get(wizard_only.id).provenienza == "landing"
    assert service.get(both.id).provenienza == "form"


def test_signups_without_a_card_are_the_leads_beside_the_cards(clean: Session) -> None:
    service = FreelancerService(clean)
    service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "application/pdf")
    _signup(clean, "ADA@studio.it")  # has a card: not a lead
    _signup(clean, "nuovo@studio.it", nome="Nuovo", cognome="Arrivato")
    everything = service.list_recent()
    assert [item.email for item in everything.items] == ["ada@studio.it"]
    assert [lead.email for lead in everything.lead] == ["nuovo@studio.it"]
    assert (everything.totale, everything.totale_lead) == (1, 1)
    assert everything.lead[0].freelancer_id is None and everything.lead[0].nome == "Nuovo"
    only_leads = service.list_recent(stato="lead")
    assert only_leads.items == [] and [lead.email for lead in only_leads.lead] == [
        "nuovo@studio.it"
    ]
    only_new = service.list_recent(stato="nuovo")
    assert len(only_new.items) == 1 and only_new.lead == [] and only_new.totale_lead == 0


# ---- the enriched detail: sign-up, logins, downloads, Pigro space (REB-284) -----------


def test_the_detail_carries_the_origin_signups_own_utm_when_one_exists(clean: Session) -> None:
    """The sign-up's own attribution, never the card's: an admin-drafted card copies
    the signup's UTM at creation but a wizard card carries its own, and here they
    genuinely differ, so the detail must read the sign-up's row, not the card's."""
    service = FreelancerService(clean)
    _signup(clean, "ada@studio.it", utm=SignupUtm(utm_source="newsletter", utm_medium="email"))
    with_signup, _ = service.apply(
        _application("ada@studio.it", utm=SignupUtm(utm_source="linkedin")),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    detail = service.get(with_signup.id)
    assert detail.iscrizione_utm is not None
    assert (detail.iscrizione_utm.utm_source, detail.iscrizione_utm.utm_medium) == (
        "newsletter",
        "email",
    )
    # The card's own attribution above is untouched.
    assert detail.utm_source == "linkedin"

    no_signup, _ = service.apply(_application("solo@studio.it"), PDF, "cv.pdf", "application/pdf")
    assert service.get(no_signup.id).iscrizione_utm is None


def test_the_detail_carries_the_last_logins_and_guide_downloads(clean: Session) -> None:
    """Both short lists read off the person's own `user_id`, newest first, and empty
    rather than absent when the address never did either (REB-284)."""
    service = FreelancerService(clean)
    active, _ = service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "application/pdf")
    row = clean.get(Freelancer, active.id)
    assert row is not None
    for _ in range(2):
        clean.add(Login(user_id=row.user_id))
    clean.commit()
    PerkService(clean).record_guide_download(row.user_id)

    detail = service.get(active.id)
    assert len(detail.ultimi_accessi) == 2
    assert len(detail.ultimi_download_guida) == 1
    assert detail.ultimi_accessi[0].logged_at >= detail.ultimi_accessi[1].logged_at

    inactive, _ = service.apply(_application("mai@studio.it"), PDF, "cv.pdf", "application/pdf")
    quiet = service.get(inactive.id)
    assert quiet.ultimi_accessi == [] and quiet.ultimi_download_guida == []


def test_the_detail_carries_the_pigro_slug_when_the_address_owns_a_space(clean: Session) -> None:
    """`pigro_slug` comes off the same registry `PigroRegistry.list_spaces` reads
    (REB-284), matched to this one address; `None` without a token configured, never
    an outbound request nobody asked for."""
    service = FreelancerService(clean)
    owner, _ = service.apply(_application("ada@studio.it"), PDF, "cv.pdf", "application/pdf")
    settings = Settings(
        pigro_api_url="https://pigro.test",
        pigro_registry_token="un-token",
        _env_file=None,  # type: ignore[call-arg]
    )
    body = json.dumps(
        [
            {
                "slug": "studio-ada",
                "owner_email": "ada@studio.it",
                "created_at": "2026-09-10T09:00:00Z",
            }
        ]
    ).encode()

    def fake_http(
        method: str, url: str, headers: dict[str, str], payload: bytes
    ) -> tuple[int, bytes]:
        return 200, body

    with_pigro = FreelancerService(clean, settings, fake_http).get(owner.id)
    assert with_pigro.pigro_slug == "studio-ada"

    # Without a configured token, the same card answers `None`: no request attempted.
    assert service.get(owner.id).pigro_slug is None


def test_the_detail_carries_no_pigro_slug_when_the_registry_answers_but_has_no_match(
    clean: Session,
) -> None:
    """A configured token with a real 200 answer, but no row for this address (REB-284):
    the exact case `find_by_email`'s own docstring claims to handle, distinct from the
    unconfigured-token case above, which never makes the request at all."""
    service = FreelancerService(clean)
    owner, _ = service.apply(_application("ines@studio.it"), PDF, "cv.pdf", "application/pdf")
    settings = Settings(
        pigro_api_url="https://pigro.test",
        pigro_registry_token="un-token",
        _env_file=None,  # type: ignore[call-arg]
    )
    body = json.dumps(
        [
            {
                "slug": "someone-elses-space",
                "owner_email": "qualcunaltro@studio.it",
                "created_at": "2026-09-10T09:00:00Z",
            }
        ]
    ).encode()

    def fake_http(
        method: str, url: str, headers: dict[str, str], payload: bytes
    ) -> tuple[int, bytes]:
        return 200, body

    assert FreelancerService(clean, settings, fake_http).get(owner.id).pigro_slug is None


def test_the_detail_has_sensible_empty_values_with_none_of_the_four_sources(
    clean: Session,
) -> None:
    """A card with no matching sign-up, no logins, no downloads and no Pigro space
    answers absent/empty values, never an error (REB-284)."""
    service = FreelancerService(clean)
    plain, _ = service.apply(_application("nessuno@studio.it"), PDF, "cv.pdf", "application/pdf")
    detail = service.get(plain.id)
    assert detail.iscrizione_utm is None
    assert detail.ultimi_accessi == []
    assert detail.ultimi_download_guida == []
    assert detail.pigro_slug is None
