"""The member area over HTTP: a link in, a cookie out, and only your own row behind it."""

import logging
import re
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_sender
from rebase_api.ratelimit import reset_rate_limit
from rebase_core.config import Settings, get_settings
from rebase_core.mail import Mail, RecordingSender
from rebase_core.models import GuideDownload, User
from rebase_core.perks import GUIDE_PATH

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"


class RefusingSender:
    """A sender the provider always turns away, for the test that a refusal is logged."""

    def send(self, mail: Mail) -> bool:
        return False


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def clean(api_session: Session) -> Iterator[None]:
    yield
    api_session.rollback()
    for table in (
        "guide_downloads",
        "sessions",
        "magic_link_tokens",
        "comments",
        "companies",
        "freelancers",
        "users",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _apply(client: TestClient, email: str, nome: str = "Ada") -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": nome,
            "cognome": "Lovelace",
            "email": email,
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text


def _request_company(client: TestClient, email: str = "wile@acme.it", **extra: object) -> None:
    payload: dict[str, object] = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": email,
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Serve un backend developer per tre mesi, da ottobre.",
        "periodo_da": "2026-10-01",
        "durata": "3 mesi",
        "budget_giornaliero": "500",
        "remoto": "remoto",
        "numero_risorse": 1,
    }
    payload.update(extra)
    response = client.post("/api/hub/companies", json=payload)
    assert response.status_code == 201, response.text


def _enter(
    client: TestClient, sender: RecordingSender, email: str
) -> tuple[dict[str, object], httpx.Response]:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    entered = client.post("/api/hub/auth/enter", json={"token": match.group(1)})
    assert entered.status_code == 200, entered.text
    return entered.json(), entered


def test_without_a_sender_the_link_request_is_a_503_sentence(
    client: TestClient, clean: None
) -> None:
    client.app.dependency_overrides[get_sender] = lambda: None  # type: ignore[attr-defined]
    response = client.post("/api/hub/auth/link", json={"email": "ada@studio.it"})
    assert response.status_code == 503
    assert "non è ancora attivo" in response.json()["detail"]


def test_a_refused_mail_is_logged_without_the_address(
    client: TestClient, clean: None, caplog: pytest.LogCaptureFixture
) -> None:
    # `api_engine`'s `upgrade_to_head` runs Alembic's own `env.py`, whose `fileConfig`
    # disables every logger that already existed and is not in `alembic.ini`'s own
    # `[loggers]` list -- this module's among them. Undo that here so `caplog` can see
    # what this test is about; nothing at runtime relies on the logger being disabled.
    logging.getLogger("rebase_api.routers.members").disabled = False
    client.app.dependency_overrides[get_sender] = lambda: RefusingSender()  # type: ignore[attr-defined]
    _apply(client, "ada@studio.it")
    with caplog.at_level(logging.WARNING):
        response = client.post("/api/hub/auth/link", json={"email": "ada@studio.it"})
    assert response.status_code == 202
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert any("refused by the provider" in record.getMessage() for record in warnings)
    assert "ada@studio.it" not in caplog.text


def test_the_link_request_answers_the_same_whether_the_address_applied_or_not(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    known = client.post("/api/hub/auth/link", json={"email": "Ada@studio.it"})
    unknown = client.post("/api/hub/auth/link", json={"email": "nessuno@studio.it"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json() == {"ok": True}
    assert [mail.to for mail in sender.sent] == ["ada@studio.it"]
    assert "/entra?t=" in sender.sent[0].text


def test_the_link_enters_once_sets_the_member_cookie_and_opens_only_the_members_routes(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    for path in ("/api/hub/me", "/api/hub/me/cv", "/api/hub/me/guide"):
        assert client.get(path).status_code == 401, path

    profile, entered = _enter(client, sender, "ada@studio.it")
    assert profile["email"] == "ada@studio.it"
    assert "stato" not in profile and "note" not in profile and "utm_source" not in profile
    cookie = client.cookies.get("orbiters_user")
    assert cookie
    set_cookie = entered.headers["set-cookie"].lower()
    assert "orbiters_user=" in set_cookie
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie
    assert "max-age=2592000" in set_cookie  # settings.member_session_days * 86400, 30 days
    assert client.get("/api/hub/me").json()["nome"] == "Ada"

    # The same link a second time opens nothing.
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    again = client.post("/api/hub/auth/enter", json={"token": match.group(1)})
    assert again.status_code == 401
    assert again.json()["detail"].startswith("Link non valido o scaduto")

    # A member cookie is a real identity, just not an admin's: 403, not 401
    # (REB-278's own AdminDep, §4).
    assert client.get("/api/hub/freelancers").status_code == 403

    cv = client.get("/api/hub/me/cv")
    assert cv.status_code == 200 and cv.content == PDF
    assert 'filename="Ada CV.pdf"' in cv.headers["content-disposition"]

    logged_out = client.post("/api/hub/me/logout")
    assert logged_out.status_code == 204
    cleared = logged_out.headers["set-cookie"].lower()
    assert "orbiters_user=" in cleared
    assert 'orbiters_user=""' in cleared or "max-age=0" in cleared
    assert client.get("/api/hub/me").status_code == 401


def test_a_cv_filename_outside_latin_1_still_downloads_instead_of_500(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    """`isalnum()` alone is Unicode-aware and used to let a CJK character or an emoji
    through the sanitiser whole, which Starlette then failed to encode into the
    Latin-1 `Content-Disposition` header at all: the download answered 500 instead of
    the file (REB-241)."""
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Cheng",
            "cognome": "Wei",
            "email": "cheng@studio.it",
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("简历📄.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text

    _enter(client, sender, "cheng@studio.it")
    cv = client.get("/api/hub/me/cv")
    assert cv.status_code == 200 and cv.content == PDF
    disposition = cv.headers["content-disposition"]
    assert disposition.isascii()  # the header Starlette must encode as latin-1
    assert 'filename="___.pdf"' in disposition
    assert "filename*=UTF-8''%E7%AE%80%E5%8E%86%F0%9F%93%84.pdf" in disposition


def test_an_accented_cv_filename_keeps_its_real_name_in_filename_star(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    """The ASCII fallback alone would turn every accent in an Italian name into an
    underscore (`Niccolò Forlì.pdf` -> `Niccol_ Forl_.pdf`). `filename*` (RFC 5987)
    carries the real name percent-encoded, so a browser that understands it shows the
    person's own name whole."""
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Niccolò",
            "cognome": "Forlì",
            "email": "niccolo@studio.it",
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Niccolò Forlì.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text

    _enter(client, sender, "niccolo@studio.it")
    cv = client.get("/api/hub/me/cv")
    assert cv.status_code == 200 and cv.content == PDF
    disposition = cv.headers["content-disposition"]
    assert disposition.isascii()
    assert 'filename="Niccol_ Forl_.pdf"' in disposition
    assert "filename*=UTF-8''Niccol%C3%B2%20Forl%C3%AC.pdf" in disposition


def test_the_guide_is_a_perk_of_the_session_and_not_a_public_file(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    """The whole point of ORB-70's second half: the guide is behind the member session.

    Nothing about the file is asserted here beyond it being the one on disk, byte for
    byte, since `packages/core/tests/test_guide_pdf.py` owns what the PDF is. What this
    owns is who may have it: an anonymous caller gets the same 401 as `/me`, and a
    member gets the bytes as an attachment under the name they will see in their
    downloads folder.
    """
    _apply(client, "ada@studio.it")
    assert client.get("/api/hub/me/guide").status_code == 401

    _enter(client, sender, "ada@studio.it")
    answer = client.get("/api/hub/me/guide")
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "application/pdf"
    assert (
        answer.headers["content-disposition"]
        == 'attachment; filename="rebase-guida-primi-passi-freelance.pdf"'
    )
    assert answer.content == GUIDE_PATH.read_bytes()

    client.post("/api/hub/me/logout")
    assert client.get("/api/hub/me/guide").status_code == 401


def test_every_download_of_the_guide_is_written_down_with_the_member_behind_it(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    """ORB-156: the admin's counter is a row per download, who and when. Two downloads
    by the same member are two rows for one person, an anonymous 401 writes nothing,
    and the bytes still arrive: recording is a side of the route, not a gate."""
    _apply(client, "ada@studio.it")
    assert client.get("/api/hub/me/guide").status_code == 401
    assert api_session.scalar(select(func.count()).select_from(GuideDownload)) == 0

    profile, _ = _enter(client, sender, "ada@studio.it")
    for _ in range(2):
        answer = client.get("/api/hub/me/guide")
        assert answer.status_code == 200 and answer.content == GUIDE_PATH.read_bytes()
    api_session.expire_all()
    rows = api_session.scalars(select(GuideDownload)).all()
    assert len(rows) == 2
    assert {str(row.user_id) for row in rows} == {profile["id"]}
    assert all(row.downloaded_at is not None for row in rows)


def test_a_wrong_token_is_a_401_and_a_malformed_one_a_422(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    assert client.post("/api/hub/auth/enter", json={"token": "a" * 43}).status_code == 401
    assert client.post("/api/hub/auth/enter", json={"token": "corto"}).status_code == 422


def test_replacing_the_cv_without_a_cookie_is_a_401_even_with_a_valid_pdf(
    client: TestClient, clean: None
) -> None:
    response = client.put("/api/hub/me/cv", files={"cv": ("cv.pdf", PDF, "application/pdf")})
    assert response.status_code == 401


def test_a_member_changes_their_answers_and_the_admin_sees_the_comment(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")

    refused = client.patch(
        "/api/hub/me",
        json={
            "nome": "Ada",
            "cognome": "Lovelace",
            "linkedin_url": "https://example.com/in/ada",
            "tariffa_giornaliera": "500",
            "posizione": "Backend developer",
            "remoto": "remoto",
            "links": [],
        },
    )
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"][-1] == "linkedin_url"

    changed = client.patch(
        "/api/hub/me",
        json={
            "nome": "Ada",
            "cognome": "Lovelace",
            "linkedin_url": None,
            "tariffa_giornaliera": "500",
            "posizione": "Staff engineer",
            "remoto": "ibrido",
            "links": ["https://github.com/ada"],
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["posizione"] == "Staff engineer"

    new_pdf = PDF + b"\n% v2\n"
    replaced = client.put(
        "/api/hub/me/cv", files={"cv": ("Ada 2026.pdf", new_pdf, "application/pdf")}
    )
    assert replaced.status_code == 200 and replaced.json()["cv_filename"] == "Ada 2026.pdf"
    not_a_pdf = client.put("/api/hub/me/cv", files={"cv": ("x.pdf", b"ciao", "application/pdf")})
    assert not_a_pdf.status_code == 422 and not_a_pdf.json()["detail"][0]["loc"][-1] == "cv"

    # `PUT /me/cv` now spends from the same public bucket as the wizard's routes
    # (the fix for the unbounded, unmetered upload); a fresh minute here keeps this
    # test about the admin's view of the thread, not about the rate limit's budget.
    reset_rate_limit()

    # The admin reads the thread the member wrote into.
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    # Log the member out first, so the freelancer thread below is read as the admin,
    # not as Ada still holding the earlier session; REB-278's unified identity puts
    # both roles behind the same `/me`, so the admin also gets a 200 there, with
    # `role` the thing that tells the two apart.
    assert client.post("/api/hub/me/logout").status_code == 204
    _enter(client, sender, ADMIN_EMAIL)
    assert client.get("/api/hub/me").json()["role"] == "admin"
    listed = client.get("/api/hub/freelancers").json()["items"]
    assert len(listed) == 1 and listed[0]["posizione"] == "Staff engineer"
    thread = client.get(f"/api/hub/freelancers/{listed[0]['id']}/comments").json()
    assert [comment["testo"] for comment in thread] == [
        "CV aggiornato dalla persona",
        "Profilo aggiornato dalla persona: tariffa giornaliera, posizione, modalità di lavoro, "
        "link",
    ]
    assert thread[0]["autore"] == "Ada Lovelace"


def test_a_member_never_sees_another_members_row(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it", nome="Ada")
    _apply(client, "grace@studio.it", nome="Grace")
    assert _enter(client, sender, "grace@studio.it")[0]["nome"] == "Grace"
    assert client.get("/api/hub/me").json()["email"] == "grace@studio.it"
    # There is no route that takes an id: the only row reachable is the session's.
    assert client.get("/api/hub/me/00000000-0000-7000-8000-000000000000").status_code == 404


# ---- a company contact's own request (REB-314) -----------------------------------------


def test_a_company_contact_edits_their_most_recent_request(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _request_company(client, durata="1 mese")
    _request_company(client, durata="3 mesi")
    profile, _ = _enter(client, sender, "wile@acme.it")
    assert profile["ha_azienda"] is True and profile["durata"] == "3 mesi"

    refused = client.patch(
        "/api/hub/me/company",
        json={
            "progetto": "Serve un backend developer per tre mesi, da ottobre.",
            "periodo_da": "2026-10-01",
            "durata": "4 mesi",
            "budget_giornaliero": "600",
            "remoto": "remoto",
            "numero_risorse": 1,
            "figura_richiesta": "Backend developer",
            "stato": "chiuso",
        },
    )
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"][-1] == "stato"

    changed = client.patch(
        "/api/hub/me/company",
        json={
            "progetto": "Serve un backend developer per tre mesi, da ottobre.",
            "periodo_da": "2026-10-01",
            "durata": "4 mesi",
            "budget_giornaliero": "600",
            "remoto": "remoto",
            "numero_risorse": 1,
            "figura_richiesta": "Backend developer",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["durata"] == "4 mesi"

    # The admin reads the comment the referente left, and the older request stands.
    # `POST /auth/link` and `POST /auth/enter` both spend from the public bucket this
    # test's two `_request_company` calls already drew on; a fresh minute here keeps
    # the test about the thread, not about the rate limit's budget.
    reset_rate_limit()
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    assert client.post("/api/hub/me/logout").status_code == 204
    _enter(client, sender, ADMIN_EMAIL)
    listed = client.get("/api/hub/companies").json()["items"]
    assert len(listed) == 2
    newest = next(item for item in listed if item["durata"] == "4 mesi")
    thread = client.get(f"/api/hub/companies/{newest['id']}/comments").json()
    assert [comment["testo"] for comment in thread] == [
        "Richiesta aggiornata dal referente: durata, budget giornaliero"
    ]
    assert thread[0]["autore"] == "Wile E."
    older = next(item for item in listed if item["durata"] == "1 mese")
    assert older["budget_giornaliero"] == "500.00"


def test_a_member_with_no_company_gets_ha_azienda_false_and_a_404_on_edit(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada@studio.it")
    profile, _ = _enter(client, sender, "ada@studio.it")
    assert profile["ha_azienda"] is False
    assert profile["progetto"] is None and profile["budget_giornaliero"] is None

    refused = client.patch(
        "/api/hub/me/company",
        json={
            "progetto": "Un progetto qualsiasi abbastanza lungo da passare",
            "periodo_da": "2026-10-01",
            "durata": "3 mesi",
            "budget_giornaliero": "500",
            "remoto": "remoto",
            "numero_risorse": 1,
            "figura_richiesta": "Backend developer",
        },
    )
    assert refused.status_code == 404


# ---- completing a card an admin wrote from a signup (ORB-155) -------------------------


def test_a_member_completes_the_card_an_admin_drafted(
    client: TestClient, sender: RecordingSender, clean: None, api_session: Session
) -> None:
    from rebase_core.freelancers import FreelancerService
    from rebase_core.schemas import FreelancerDraft, SignupCreate
    from rebase_core.service import SignupService

    signup = SignupService(api_session).subscribe(
        SignupCreate(email="ada@studio.it", nome="Ada", cognome="Lovelace")
    )
    FreelancerService(api_session).draft_from_signup(
        signup.id,
        FreelancerDraft(nome="Ada", cognome="Lovelace", fonti=["https://ada.dev"]),
        "Claude",
    )
    profile, _ = _enter(client, sender, "ada@studio.it")
    assert profile["completa"] is False and profile["cv_filename"] is None
    assert client.get("/api/hub/me/cv").status_code == 404

    answered = client.patch(
        "/api/hub/me",
        json={
            "nome": "Ada",
            "cognome": "Lovelace",
            "linkedin_url": None,
            "tariffa_giornaliera": "500",
            "posizione": "CTO",
            "remoto": "ibrido",
            "links": [],
        },
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["completa"] is False
    with_cv = client.put("/api/hub/me/cv", files={"cv": ("Ada CV.pdf", PDF, "application/pdf")})
    assert with_cv.status_code == 200, with_cv.text
    assert with_cv.json()["completa"] is True
    assert client.get("/api/hub/me/cv").status_code == 200
    api_session.execute(text("DELETE FROM signups"))
    api_session.commit()


def test_a_login_shows_up_on_the_admin_side(
    client: TestClient, sender: RecordingSender, clean: None, api_session: Session
) -> None:
    _apply(client, "ada@studio.it")
    _enter(client, sender, "ada@studio.it")
    assert client.post("/api/hub/me/logout").status_code == 204
    # The session is gone; the login stays.
    assert client.get("/api/hub/logins").status_code == 401
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    _enter(client, sender, ADMIN_EMAIL)
    stats = client.get("/api/hub/logins").json()
    # The admin's own entry is a login too, since REB-278 a login is for any
    # signed-in person, not only a freelancer with a card.
    assert (stats["totale"], stats["membri"], stats["membri_totali"]) == (2, 2, 1)
    assert stats["recenti"][0]["email"] == ADMIN_EMAIL
    assert stats["recenti"][1]["email"] == "ada@studio.it"
    card = client.get("/api/hub/freelancers").json()["items"][0]
    assert card["accessi"] == 1 and card["ultimo_accesso"] is not None
    detail = client.get(f"/api/hub/freelancers/{card['id']}").json()
    assert detail["accessi"] == 1


# --- what PigroCRM may ask (ORB-173) -------------------------------------------------

PIGRO_TOKEN = "un-token-lungo-condiviso-con-il-crm"
LOOKUP = "/api/hub/members/lookup"


@pytest.fixture
def registry_token(client: TestClient) -> None:
    """The installation with `REBASE_PIGRO_REGISTRY_TOKEN` set: the one the CRM's
    signup may ask. Declared, never inherited from a developer's `.env`."""
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        pigro_registry_token=PIGRO_TOKEN,
        _env_file=None,  # type: ignore[call-arg]
    )


def _ask(client: TestClient, email: str, **headers: str | bytes) -> httpx.Response:
    return client.post(LOOKUP, json={"email": email}, headers=headers)  # type: ignore[arg-type]


def test_without_a_registry_token_the_member_lookup_does_not_exist(client: TestClient) -> None:
    """The route is a 404 whatever the caller presents, not a 401 that says «there is a
    door»: the same rule as the CRM's `GET /api/tenants/` (ORB-142)."""
    assert _ask(client, "ada@studio.it").status_code == 404
    assert _ask(client, "ada@studio.it", Authorization=f"Bearer {PIGRO_TOKEN}").status_code == 404


def test_the_member_lookup_answers_only_to_the_registry_token(
    client: TestClient, registry_token: None
) -> None:
    assert _ask(client, "ada@studio.it").status_code == 401
    assert _ask(client, "ada@studio.it", Authorization="Bearer sbagliato").status_code == 401
    # Starlette decodes headers as latin-1, and `secrets.compare_digest` refuses a `str`
    # with a non-ASCII character: a stray byte must be a 401 like any wrong token, never a 500.
    odd = _ask(client, "ada@studio.it", Authorization=b"Bearer t\xe9ken")
    assert odd.status_code == 401, odd.text


def test_the_member_lookup_names_a_member_and_says_no_to_anyone_else(
    client: TestClient, registry_token: None, clean: None
) -> None:
    """Case-insensitive, like `uq_freelancers_email_lower`; an unknown address is a plain
    `membro: false` and never an error, and nothing beyond the two names leaves. The
    address travels in the body: a GET with `?email=` would sit in every access log."""
    _apply(client, "Ada@Studio.it")
    bearer = f"Bearer {PIGRO_TOKEN}"

    member = _ask(client, "  ada@studio.IT ", Authorization=bearer)
    assert member.status_code == 200, member.text
    assert member.json() == {"membro": True, "nome": "Ada", "cognome": "Lovelace"}

    unknown = _ask(client, "nessuno@example.org", Authorization=bearer)
    assert unknown.status_code == 200, unknown.text
    assert unknown.json() == {"membro": False, "nome": None, "cognome": None}

    # Not an address at all: still a `false`, the CRM decides what to make of the input.
    assert _ask(client, "non-una-mail", Authorization=bearer).json() == {
        "membro": False,
        "nome": None,
        "cognome": None,
    }
    # No body, or the address in the query string instead: a 422, never a lookup.
    assert client.post(LOOKUP, headers={"Authorization": bearer}).status_code == 422
    assert (
        client.post(
            LOOKUP, params={"email": "ada@studio.it"}, headers={"Authorization": bearer}
        ).status_code
        == 422
    )
