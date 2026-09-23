"""The admin area over HTTP: a cookie in, the lists out, and nothing without it."""

import json
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_http_call, get_sender
from rebase_core.config import Settings, get_settings
from rebase_core.http import MAX_BODY_BYTES
from rebase_core.mail import RecordingSender
from rebase_core.models import Freelancer, Login, User
from rebase_core.perks import PerkService

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in (
        "guide_downloads",
        "comments",
        "admin_tokens",
        "freelancers",
        "companies",
        "users",
        "signups",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _apply(client: TestClient, email: str = "ada@studio.it") -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "cognome": "Lovelace",
            "email": email,
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text


def test_without_the_cookie_every_admin_route_is_a_401(client: TestClient, admin: None) -> None:
    for path in (
        "/api/hub/freelancers",
        "/api/hub/companies",
        "/api/hub/signups",
        "/api/hub/talent",
        "/api/hub/admins",
        "/api/hub/pigro/instances",
        "/api/hub/perks/guide",
    ):
        assert client.get(path).status_code == 401, path
    refused = client.post("/api/hub/admins/promote", json={"email": "x@rebase.it"})
    assert refused.status_code == 401
    assert client.post(f"/api/hub/admins/{MISSING}/demote").status_code == 401


def test_the_guide_page_counts_downloads_and_names_who_took_it(
    client: TestClient, admin: None, api_session: Session, sender: RecordingSender
) -> None:
    """ORB-156: zero of everything on an empty hub, then the numbers follow the rows.
    Two people on file, three downloads by one of them: totale 3, membri 1 of 2, all
    three in the last week, the latest first, each with the member's name."""
    _login(client, sender)
    empty = client.get("/api/hub/perks/guide")
    assert empty.status_code == 200
    assert empty.json() == {
        "totale": 0,
        "membri": 0,
        "membri_totali": 0,
        "ultimi_7_giorni": 0,
        "recenti": [],
    }

    _apply(client, "ada@studio.it")
    _apply(client, "bob@studio.it")
    listed = client.get("/api/hub/freelancers").json()["items"]
    people = {item["email"]: item["id"] for item in listed}
    ada_user_id = api_session.scalar(
        select(Freelancer.user_id)
        .join(User, User.id == Freelancer.user_id)
        .where(User.email == "ada@studio.it")
    )
    perks = PerkService(api_session)
    for _ in range(3):
        perks.record_guide_download(ada_user_id)

    stats = client.get("/api/hub/perks/guide").json()
    assert (stats["totale"], stats["membri"], stats["membri_totali"]) == (3, 1, 2)
    assert stats["ultimi_7_giorni"] == 3
    assert len(stats["recenti"]) == 3
    latest = stats["recenti"][0]
    assert (latest["nome"], latest["cognome"]) == ("Ada", "Lovelace")
    assert latest["email"] == "ada@studio.it"
    assert latest["user_id"] == str(ada_user_id)
    assert people["ada@studio.it"] != str(ada_user_id)  # the freelancer id, not the user id
    moments = [item["downloaded_at"] for item in stats["recenti"]]
    assert moments == sorted(moments, reverse=True)


def test_an_admin_manages_a_freelancer_card_through_the_lists(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    _apply(client)
    listed = client.get("/api/hub/freelancers").json()
    assert listed["totale"] == 1
    item = listed["items"][0]
    assert item["cv_filename"] == "Ada CV.pdf" and "cv_bytes" not in item

    cv = client.get(f"/api/hub/freelancers/{item['id']}/cv")
    assert cv.status_code == 200
    assert cv.content == PDF
    assert cv.headers["content-type"].startswith("application/pdf")
    assert 'filename="Ada CV.pdf"' in cv.headers["content-disposition"]

    moved = client.patch(
        f"/api/hub/freelancers/{item['id']}", json={"stato": "contattato", "note": "ok"}
    )
    assert moved.status_code == 200 and moved.json()["stato"] == "contattato"
    wrong = client.patch(f"/api/hub/freelancers/{item['id']}", json={"stato": "forse"})
    assert wrong.status_code == 422
    assert wrong.json()["detail"][0]["loc"][-1] == "stato"
    missing = client.get("/api/hub/companies/00000000-0000-7000-8000-000000000000")
    assert missing.status_code == 404


# ---- talenti (REB-282): the freelancer cards and the bare sign-ups, as one list ------


def test_talenti_merges_cards_and_leads_and_counts_per_state(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    """A card and a bare sign-up read as one list: the card carries its own `stato`
    and `origine == "wizard"`, the bare sign-up reads `stato == "lead"` and
    `origine == "form"`, and `per_stato` counts both whatever `stato` was asked for."""
    signup_id = _signup(client, sender, "lead@studio.it")
    _apply(client, "ada@studio.it")

    listed = client.get("/api/hub/talent").json()
    assert listed["totale"] == 2
    by_email = {item["email"]: item for item in listed["items"]}
    assert by_email["ada@studio.it"]["stato"] == "nuovo"
    assert by_email["ada@studio.it"]["origine"] == "wizard"
    assert by_email["lead@studio.it"]["stato"] == "lead"
    assert by_email["lead@studio.it"]["origine"] == "form"
    assert by_email["lead@studio.it"]["id"] == signup_id
    assert listed["per_stato"] == {
        "nuovo": 1,
        "contattato": 0,
        "attivo": 0,
        "scartato": 0,
        "lead": 1,
    }

    only_leads = client.get("/api/hub/talent", params={"stato": "lead"}).json()
    assert [item["email"] for item in only_leads["items"]] == ["lead@studio.it"]
    assert only_leads["totale"] == 1
    # `per_stato` counts the whole list regardless of the filter asked for.
    assert only_leads["per_stato"] == listed["per_stato"]

    only_new = client.get("/api/hub/talent", params={"stato": "nuovo"}).json()
    assert [item["email"] for item in only_new["items"]] == ["ada@studio.it"]
    assert only_new["totale"] == 1


def test_a_card_drafted_from_research_reads_origine_admin_on_talenti(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    signup_id = _signup(client, sender, "ricerca@studio.it")
    created = client.post(f"/api/hub/signups/{signup_id}/card", json=DRAFT)
    assert created.status_code == 201, created.text
    item = next(
        item
        for item in client.get("/api/hub/talent").json()["items"]
        if item["email"] == "ricerca@studio.it"
    )
    assert item["origine"] == "admin" and item["stato"] == "nuovo"


def test_a_signed_in_member_hitting_talenti_is_403_not_401(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _apply(client, "membro@studio.it")
    _login(client, sender, "membro@studio.it")
    assert client.get("/api/hub/talent").status_code == 403


# ---- search, cursor pagination and filters over HTTP (REB-285) -----------------------


def test_talenti_search_hits_a_partial_surname_and_an_email_domain_over_http(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Bob",
            "cognome": "Rossi",
            "email": "bob@rossilab.it",
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Bob CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    _apply(client, "carol@other.it")
    _login(client, sender)

    by_surname = client.get("/api/hub/talent", params={"q": "oss"}).json()
    assert [item["email"] for item in by_surname["items"]] == ["bob@rossilab.it"]

    by_domain = client.get("/api/hub/talent", params={"q": "rossilab.it"}).json()
    assert [item["email"] for item in by_domain["items"]] == ["bob@rossilab.it"]


def test_talenti_filters_are_wired_through_the_router(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    for email, remoto in (("remote@studio.it", "remoto"), ("onsite@studio.it", "in_sede")):
        response = client.post(
            "/api/hub/freelancers",
            data={
                "nome": "Worker",
                "cognome": "Bee",
                "email": email,
                "tariffa_giornaliera": "450",
                "posizione": "Backend developer",
                "remoto": remoto,
            },
            files={"cv": ("cv.pdf", PDF, "application/pdf")},
        )
        assert response.status_code == 201, response.text

    only_remote = client.get("/api/hub/talent", params={"remoto": "remoto"}).json()
    assert [item["email"] for item in only_remote["items"]] == ["remote@studio.it"]


def test_companies_search_hits_a_partial_referente_surname(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    response = client.post(
        "/api/hub/companies",
        json={
            "nome_azienda": "Rossi Labs",
            "referente_nome": "Wile",
            "referente_cognome": "Rossi",
            "email": "wile@rossilab.it",
            "telefono": "+39 345 1234567",
            "figura_richiesta": "Backend developer",
            "progetto": "Un backend developer.",
            "periodo_da": "2026-10-01",
            "durata": "3 mesi",
            "budget_giornaliero": "500",
            "remoto": "remoto",
            "numero_risorse": 1,
        },
    )
    assert response.status_code == 201, response.text

    by_surname = client.get("/api/hub/companies", params={"q": "oss"}).json()
    assert [item["email"] for item in by_surname["items"]] == ["wile@rossilab.it"]


def test_a_malformed_cursor_is_a_422_on_both_lists(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    for path in ("/api/hub/talent", "/api/hub/companies"):
        response = client.get(path, params={"cursor": "not-a-valid-cursor"})
        assert response.status_code == 422, (path, response.text)


def test_admindep_is_enforced_before_the_new_search_params_are_even_read(
    client: TestClient, admin: None
) -> None:
    """An unauthenticated request carrying every new REB-285 parameter, including a
    cursor that would otherwise be refused as malformed, still answers 401: `AdminDep`
    runs before the query is ever built."""
    params = {"q": "ada", "cursor": "not-a-valid-cursor", "tariffa_min": "100"}
    assert client.get("/api/hub/talent", params=params).status_code == 401
    assert client.get("/api/hub/companies", params=params).status_code == 401


def test_admins_search_hits_a_partial_name(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    promoted = client.post(
        "/api/hub/admins/promote",
        json={"email": "grace@rebase.it", "nome": "Grace", "cognome": "Hopper"},
    )
    assert promoted.status_code == 200, promoted.text

    by_name = client.get("/api/hub/admins", params={"q": "Hopper"}).json()
    assert [row["email"] for row in by_name["items"]] == ["grace@rebase.it"]


def test_admins_list_stays_oldest_first_with_no_term(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    """ORB-123: REB-313's cursor keeps the admins list reading as a history."""
    _login(client, sender)
    client.post(
        "/api/hub/admins/promote",
        json={"email": "grace@rebase.it", "nome": "Grace", "cognome": "Hopper"},
    )
    listed = client.get("/api/hub/admins").json()["items"]
    assert [row["email"] for row in listed] == [ADMIN_EMAIL, "grace@rebase.it"]


def test_logins_search_hits_a_partial_name_and_keeps_the_counters(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    _login(client, sender)
    _apply(client, "ada@studio.it")
    ada_user_id = api_session.scalar(
        select(Freelancer.user_id)
        .join(User, User.id == Freelancer.user_id)
        .where(User.email == "ada@studio.it")
    )
    api_session.add(Login(user_id=ada_user_id))
    api_session.commit()

    by_name = client.get("/api/hub/logins", params={"q": "Ada"}).json()
    assert [row["email"] for row in by_name["recenti"]] == ["ada@studio.it"]
    # The aggregate counters read the whole table -- the admin's own sign-in above and
    # Ada's -- unaffected by `q` narrowing `recenti` to Ada alone (REB-313).
    assert by_name["totale"] == 2 and by_name["membri"] == 2


def test_a_malformed_cursor_is_a_422_on_admins_and_logins(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    for path in ("/api/hub/admins", "/api/hub/logins"):
        response = client.get(path, params={"cursor": "not-a-valid-cursor"})
        assert response.status_code == 422, (path, response.text)


# ---- comments --------------------------------------------------------------------------

MISSING = "00000000-0000-7000-8000-000000000000"


def _login(client: TestClient, sender: RecordingSender, email: str = ADMIN_EMAIL) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def _request_company(client: TestClient) -> None:
    response = client.post(
        "/api/hub/companies",
        json={
            "nome_azienda": "ACME Srl",
            "referente_nome": "Wile",
            "referente_cognome": "E.",
            "email": "wile@acme.it",
            "telefono": "+39 345 1234567",
            "figura_richiesta": "Backend developer",
            "progetto": "Un backend developer per tre mesi.",
            "periodo_da": "2026-10-01",
            "durata": "3 mesi",
            "budget_giornaliero": "500",
            "remoto": "remoto",
            "numero_risorse": 1,
        },
    )
    assert response.status_code == 201, response.text


def test_without_the_cookie_the_comment_routes_are_a_401(client: TestClient, admin: None) -> None:
    for kind in ("freelancers", "companies"):
        assert client.get(f"/api/hub/{kind}/{MISSING}/comments").status_code == 401
        assert (
            client.post(f"/api/hub/{kind}/{MISSING}/comments", json={"testo": "x"}).status_code
            == 401
        )


def test_a_comment_is_signed_by_the_logged_in_admin_and_read_newest_first(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    _apply(client)
    freelancer_id = client.get("/api/hub/freelancers").json()["items"][0]["id"]

    assert client.get(f"/api/hub/freelancers/{freelancer_id}/comments").json() == []
    first = client.post(
        f"/api/hub/freelancers/{freelancer_id}/comments",
        json={"testo": "  Sentito al telefono.\nRichiamare lunedì.  "},
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["testo"] == "Sentito al telefono.\nRichiamare lunedì."
    assert body["autore"] == "Ivan"
    assert body["entity_type"] == "freelancer" and body["entity_id"] == freelancer_id
    second = client.post(
        f"/api/hub/freelancers/{freelancer_id}/comments", json={"testo": "Ha mandato il portfolio."}
    )
    assert second.status_code == 201

    thread = client.get(f"/api/hub/freelancers/{freelancer_id}/comments").json()
    assert [c["id"] for c in thread] == [second.json()["id"], body["id"]]
    # The detail carries the same thread; the list does not.
    detail = client.get(f"/api/hub/freelancers/{freelancer_id}").json()
    assert [c["id"] for c in detail["commenti"]] == [second.json()["id"], body["id"]]
    assert detail["note"] is None
    assert client.get("/api/hub/freelancers").json()["items"][0]["commenti"] == []


def test_a_company_gets_its_own_thread(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    _request_company(client)
    company_id = client.get("/api/hub/companies").json()["items"][0]["id"]
    posted = client.post(f"/api/hub/companies/{company_id}/comments", json={"testo": "Budget ok."})
    assert posted.status_code == 201, posted.text
    assert posted.json()["autore"] == "Ivan"
    assert [c["testo"] for c in client.get(f"/api/hub/companies/{company_id}/comments").json()] == [
        "Budget ok."
    ]
    assert [
        c["testo"] for c in client.get(f"/api/hub/companies/{company_id}").json()["commenti"]
    ] == ["Budget ok."]


def test_a_comment_on_a_missing_row_is_a_404_and_an_empty_one_a_422_naming_the_field(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    assert client.get(f"/api/hub/freelancers/{MISSING}/comments").status_code == 200
    missing = client.post(f"/api/hub/freelancers/{MISSING}/comments", json={"testo": "Nessuno."})
    assert missing.status_code == 404
    assert missing.json()["detail"] == f"freelancer {MISSING} non trovato"
    assert (
        client.post(f"/api/hub/companies/{MISSING}/comments", json={"testo": "x"}).status_code
        == 404
    )

    _apply(client)
    freelancer_id = client.get("/api/hub/freelancers").json()["items"][0]["id"]
    for testo in ("", "   ", "x" * 4001):
        refused = client.post(
            f"/api/hub/freelancers/{freelancer_id}/comments", json={"testo": testo}
        )
        assert refused.status_code == 422, testo[:10]
        assert refused.json()["detail"][0]["loc"][-1] == "testo"
    # The author is the session's, never the body's.
    forged = client.post(
        f"/api/hub/freelancers/{freelancer_id}/comments", json={"testo": "ok", "autore": "Altro"}
    )
    assert forged.status_code == 422
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/comments").json() == []


# ---- the spaces of PigroCRM (ORB-142) --------------------------------------------------
#
# The hub never touches the CRM's database: it asks the CRM's API with a token, through
# the same HTTP seam the mail and the pixel use, so these tests hand a fake and read what
# would have left.

PIGRO_TOKEN = "un-token-lungo-solo-per-questa-suite"
PIGRO_ROWS = [
    {
        "id": "0192c6f0-0000-7000-8000-000000000002",
        "slug": "studio-ada",
        "owner_email": "ada@studio.it",
        "created_at": "2026-09-10T09:00:00Z",
    },
    {
        "id": "0192c6f0-0000-7000-8000-000000000001",
        "slug": "bob-dev",
        "owner_email": "Bob@Example.org",
        "created_at": "2026-09-09T09:00:00Z",
    },
]


class FakePigro:
    """Answers what a test tells it to and keeps every call it received."""

    def __init__(self, status: int = 200, body: object = None) -> None:
        self.status = status
        self.body = json.dumps(PIGRO_ROWS if body is None else body).encode()
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.raises: Exception | None = None

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers))
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


@pytest.fixture
def pigro(client: TestClient) -> Iterator[FakePigro]:
    fake = FakePigro()
    client.app.dependency_overrides[get_http_call] = lambda: fake  # type: ignore[attr-defined]
    # Both values declared, so a developer's shell exporting `REBASE_PIGRO_API_URL`
    # cannot change what the assertion below expects.
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        pigro_api_url="https://pigro.letsrebase.com",
        pigro_registry_token=PIGRO_TOKEN,
        _env_file=None,  # type: ignore[call-arg]
    )
    yield fake


def test_without_a_pigro_token_the_spaces_are_a_503_sentence(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    response = client.get("/api/hub/pigro/instances")
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == (
        "Il registro di Pigro non è configurato: manca REBASE_PIGRO_REGISTRY_TOKEN."
    )


def test_the_spaces_come_from_the_crm_with_the_token_and_name_the_member_who_owns_one(
    client: TestClient, admin: None, pigro: FakePigro, sender: RecordingSender
) -> None:
    _login(client, sender)
    _apply(client, email="ada@studio.it")
    response = client.get("/api/hub/pigro/instances")
    assert response.status_code == 200, response.text

    # One GET to the CRM, the token as a bearer, and nothing else in the request.
    assert [(method, url) for method, url, _ in pigro.calls] == [
        ("GET", "https://pigro.letsrebase.com/api/tenants/")
    ]
    assert pigro.calls[0][2]["Authorization"] == f"Bearer {PIGRO_TOKEN}"

    body = response.json()
    assert body["totale"] == 2
    ada, bob = body["items"]
    assert ada["slug"] == "studio-ada"
    assert ada["url"] == "https://pigro.letsrebase.com/studio-ada/app/"
    assert ada["owner_email"] == "ada@studio.it"
    assert ada["created_at"].startswith("2026-09-10")
    # Ada filled in the wizard, so her space names her and points at her card.
    assert ada["membro"]["nome"] == "Ada"
    assert ada["membro"]["cognome"] == "Lovelace"
    freelancer_id = ada["membro"]["id"]
    assert client.get(f"/api/hub/freelancers/{freelancer_id}").json()["email"] == "ada@studio.it"
    # Bob never did: the address is all the hub knows, as the CRM wrote it.
    assert bob["slug"] == "bob-dev"
    assert bob["membro"] is None


def test_a_member_is_matched_whatever_the_case_of_the_address(
    client: TestClient, admin: None, pigro: FakePigro, sender: RecordingSender
) -> None:
    _login(client, sender)
    _apply(client, email="bob@example.org")
    items = client.get("/api/hub/pigro/instances").json()["items"]
    assert items[1]["owner_email"] == "Bob@Example.org"
    assert items[1]["membro"]["nome"] == "Ada"


def test_q_searches_the_slug_and_the_owner_address(
    client: TestClient, admin: None, pigro: FakePigro, sender: RecordingSender
) -> None:
    _login(client, sender)
    by_slug = client.get("/api/hub/pigro/instances", params={"q": "studio"}).json()
    assert [item["slug"] for item in by_slug["items"]] == ["studio-ada"]
    by_email = client.get("/api/hub/pigro/instances", params={"q": "bob@"}).json()
    assert [item["slug"] for item in by_email["items"]] == ["bob-dev"]
    no_match = client.get("/api/hub/pigro/instances", params={"q": "nessuno"}).json()
    assert no_match["items"] == [] and no_match["totale"] == 0


def test_the_cursor_walks_every_space_once_with_no_gap_or_repeat(
    client: TestClient, admin: None, pigro: FakePigro, sender: RecordingSender
) -> None:
    rows = [
        {
            "id": f"0192c6f0-0000-7000-8000-{i:012d}",
            "slug": f"spazio-{i}",
            "owner_email": f"persona{i}@studio.it",
            "created_at": f"2026-09-{10 + i:02d}T09:00:00Z",
        }
        for i in range(5)
    ]
    pigro.body = json.dumps(rows).encode()
    _login(client, sender)

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        page = client.get("/api/hub/pigro/instances", params=params).json()
        seen.extend(item["slug"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"spazio-{i}" for i in reversed(range(5))]


def test_a_malformed_cursor_is_a_422(
    client: TestClient, admin: None, pigro: FakePigro, sender: RecordingSender
) -> None:
    _login(client, sender)
    response = client.get("/api/hub/pigro/instances", params={"cursor": "non-un-cursore"})
    assert response.status_code == 422, response.text


def test_when_the_crm_refuses_or_falls_over_the_answer_is_a_502_sentence(
    client: TestClient, admin: None, pigro: FakePigro, sender: RecordingSender
) -> None:
    _login(client, sender)
    pigro.status = 401
    pigro.body = b'{"detail":"token non valido"}'
    refused = client.get("/api/hub/pigro/instances")
    assert refused.status_code == 502, refused.text
    assert refused.json()["detail"] == "Pigro non ha risposto (401)."
    pigro.status = 200
    pigro.body = b"<html>not json</html>"
    garbled = client.get("/api/hub/pigro/instances")
    assert garbled.status_code == 502, garbled.text
    assert garbled.json()["detail"] == "Pigro ha risposto qualcosa che non è un elenco."
    pigro.body = b"[" + b"x" * MAX_BODY_BYTES  # what the seam's own cap would truncate to
    too_long = client.get("/api/hub/pigro/instances")
    assert too_long.status_code == 502, too_long.text
    assert too_long.json()["detail"] == "Pigro ha risposto qualcosa di troppo lungo."
    # A refused connection, a DNS miss or a timeout: the seam raises, and that is the most
    # likely failure of all, so it too is a 502 sentence rather than a traceback.
    pigro.raises = OSError("connection refused")
    down = client.get("/api/hub/pigro/instances")
    assert down.status_code == 502, down.text
    assert down.json()["detail"] == "Pigro non risponde."


# ---- the enriched detail: sign-up, logins, downloads, Pigro space (REB-284) -----------


def test_the_detail_shows_the_origin_signups_own_utm(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    """The sign-up's own attribution, separate from the card's own `utm_source`
    above: an address can leave its email on the landing with one campaign and apply
    through the wizard with another, and the detail must tell the two apart."""
    _login(client, sender)
    signed_up = client.post(
        "/api/community/signups",
        json={
            "email": "ada@studio.it",
            "nome": "Ada",
            "cognome": "Lovelace",
            "utm": {"utm_source": "newsletter", "utm_medium": "email"},
        },
    )
    assert signed_up.status_code in (200, 201), signed_up.text
    _apply(client, "ada@studio.it")
    freelancer_id = client.get("/api/hub/freelancers").json()["items"][0]["id"]
    detail = client.get(f"/api/hub/freelancers/{freelancer_id}").json()
    assert detail["iscrizione_utm"] == {
        "utm_source": "newsletter",
        "utm_medium": "email",
        "utm_campaign": None,
        "utm_content": None,
        "utm_term": None,
        "utm_id": None,
        "origine": None,
    }


def test_the_detail_shows_the_last_logins_and_guide_downloads(
    client: TestClient, admin: None, api_session: Session, sender: RecordingSender
) -> None:
    """The two short lists come off the person's own `user_id`, written straight to
    `logins`/`guide_downloads` here rather than through the magic-link flow, which
    shares the hub's one rate limiter with every other public write and is already
    exercised end to end elsewhere (ORB-158, ORB-156)."""
    _login(client, sender)
    _apply(client, "ada@studio.it")
    freelancer_id = client.get("/api/hub/freelancers").json()["items"][0]["id"]
    ada_user_id = api_session.scalar(
        select(Freelancer.user_id)
        .join(User, User.id == Freelancer.user_id)
        .where(User.email == "ada@studio.it")
    )
    api_session.add(Login(user_id=ada_user_id))
    api_session.commit()
    PerkService(api_session).record_guide_download(ada_user_id)

    detail = client.get(f"/api/hub/freelancers/{freelancer_id}").json()
    assert len(detail["ultimi_accessi"]) == 1
    assert len(detail["ultimi_download_guida"]) == 1
    # The field REB-278 already exposed keeps counting the same table.
    assert detail["accessi"] == 1


def test_the_detail_shows_the_pigro_slug_only_with_a_configured_token(
    client: TestClient, admin: None, pigro: FakePigro, sender: RecordingSender
) -> None:
    _login(client, sender)
    _apply(client, "ada@studio.it")
    freelancer_id = client.get("/api/hub/freelancers").json()["items"][0]["id"]
    detail = client.get(f"/api/hub/freelancers/{freelancer_id}").json()
    assert detail["pigro_slug"] == "studio-ada"


def test_the_detail_has_sensible_empty_values_with_none_of_the_four_sources(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    """No sign-up, no logins, no downloads, no Pigro token configured: absent and
    empty values, never an error (REB-284)."""
    _login(client, sender)
    _apply(client, "sola@studio.it")
    freelancer_id = client.get("/api/hub/freelancers").json()["items"][0]["id"]
    detail = client.get(f"/api/hub/freelancers/{freelancer_id}").json()
    assert detail["iscrizione_utm"] is None
    assert detail["ultimi_accessi"] == []
    assert detail["ultimi_download_guida"] == []
    assert detail["pigro_slug"] is None


# ---- a card from a signup (ORB-155) --------------------------------------------------


def _signup(client: TestClient, sender: RecordingSender, email: str = "ada@studio.it") -> str:
    response = client.post(
        "/api/community/signups", json={"email": email, "nome": "Ada", "cognome": "Lovelace"}
    )
    assert response.status_code in (200, 201), response.text
    _login(client, sender)
    listed = client.get("/api/hub/signups").json()["iscrizioni"]
    return next(item["id"] for item in listed if item["email"] == email)


DRAFT = {
    "nome": "Ada",
    "cognome": "Lovelace",
    "linkedin_url": "https://www.linkedin.com/in/ada",
    "posizione": "Backend developer",
    "links": ["https://github.com/ada"],
    "fonti": ["https://www.linkedin.com/in/ada"],
}


def test_without_the_cookie_the_card_from_a_signup_is_a_401(
    client: TestClient, admin: None
) -> None:
    response = client.post(f"/api/hub/signups/{MISSING}/card", json=DRAFT)
    assert response.status_code == 401


def test_an_admin_writes_an_incomplete_card_from_a_signup_and_the_list_points_at_it(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    signup_id = _signup(client, sender)
    created = client.post(f"/api/hub/signups/{signup_id}/card", json=DRAFT)
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["email"] == "ada@studio.it"
    assert card["compilata_da"] == "admin" and card["completa"] is False
    assert card["cv_filename"] is None and card["tariffa_giornaliera"] is None
    assert card["commenti"][0]["autore"] == "Ivan"
    assert "https://www.linkedin.com/in/ada" in card["commenti"][0]["testo"]

    items = client.get("/api/hub/signups").json()["iscrizioni"]
    item = next(i for i in items if i["id"] == signup_id)
    assert item["freelancer_id"] == card["id"]
    assert client.get(f"/api/hub/freelancers/{card['id']}/cv").status_code == 404
    everything = client.get("/api/hub/freelancers").json()
    listed = everything["items"]
    assert listed[0]["id"] == card["id"] and listed[0]["completa"] is False
    # With a card, the signup is no longer a lead (ORB-163).
    assert everything["lead"] == [] and everything["totale_lead"] == 0
    # Born from a signup, so it also signed up on the landing: «form» (ORB-161).
    assert listed[0]["provenienza"] == "form"
    assert client.get(f"/api/hub/freelancers/{card['id']}").json()["provenienza"] == "form"

    # A wrong id is a 404, a bad body a 422 naming the field, as everywhere else.
    assert client.post(f"/api/hub/signups/{MISSING}/card", json=DRAFT).status_code == 404
    bad = client.post(f"/api/hub/signups/{signup_id}/card", json={**DRAFT, "fonti": []})
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["loc"][-1] == "fonti"


def test_a_signup_without_a_card_is_a_lead_on_the_freelancer_list(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    signup_id = _signup(client, sender, "lead@studio.it")
    everything = client.get("/api/hub/freelancers").json()
    assert everything["items"] == [] and everything["totale_lead"] == 1
    assert everything["lead"][0]["id"] == signup_id
    assert everything["lead"][0]["email"] == "lead@studio.it"
    assert client.get("/api/hub/freelancers", params={"stato": "lead"}).json()["totale_lead"] == 1
    assert client.get("/api/hub/freelancers", params={"stato": "nuovo"}).json()["lead"] == []


def test_research_is_refused_on_a_card_the_person_filled(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _apply(client)
    signup_id = _signup(client, sender)
    body = {**DRAFT, "posizione": "CTO"}
    refused = client.post(f"/api/hub/signups/{signup_id}/card", json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"][0]["loc"][-1] == "email"
    assert client.get("/api/hub/freelancers").json()["items"][0]["posizione"] == "Backend developer"
