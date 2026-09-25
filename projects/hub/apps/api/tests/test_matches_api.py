"""REB-387 phase 2 over HTTP: tax data, a match created from a card and a request, both
PDFs downloadable, a draft cancelled; nothing at all without the admin cookie."""

import re
from collections.abc import Iterator

import pytest
from fakes_contracts import FailingRenderer, FakeRenderer
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_renderer
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import Match, User

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"
MISSING = "00000000-0000-7000-8000-000000000000"
# What the company would pay rebase a day: it must appear nowhere this flow answers.
BUDGET = "777.77"
FISCAL = {
    "codice_fiscale": "LVLDAA85T50H501Z",
    "partita_iva": "01234567890",
    "domicilio": "Via Roma 1, Milano",
    "pec": None,
}
CLIENTE = {
    "cliente_ragione_sociale": "ACME S.r.l.",
    "cliente_piva": "01234567890",
    "cliente_sede": "Milano",
}
LETTERA = {
    "ruolo": "Backend developer",
    "attivita": "Le API del prodotto.",
    "data_inizio": "2026-10-01",
    "compenso": "450",
    "giorni_pagamento": 30,
    "fine_mese": True,
}
TABLES = (
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
    "comments",
    "freelancers",
    "companies",
    "users",
    "signups",
)


@pytest.fixture
def renderer(client: TestClient) -> Iterator[FakeRenderer]:
    fake = FakeRenderer()
    client.app.dependency_overrides[get_renderer] = lambda: fake  # type: ignore[attr-defined]
    yield fake


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _login(client: TestClient, sender: RecordingSender) -> None:
    assert client.post("/api/hub/auth/link", json={"email": ADMIN_EMAIL}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def _apply(client: TestClient) -> str:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "cognome": "Lovelace",
            "email": "ada@studio.it",
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return str(client.get("/api/hub/freelancers").json()["items"][0]["id"])


def _request_company(client: TestClient) -> str:
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
            "budget_giornaliero": BUDGET,
            "remoto": "remoto",
            "numero_risorse": 1,
        },
    )
    assert response.status_code == 201, response.text
    return str(client.get("/api/hub/companies").json()["items"][0]["id"])


def _ready(client: TestClient, sender: RecordingSender) -> tuple[str, str]:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    assert (
        client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL).status_code == 200
    )
    return freelancer_id, company_id


def test_without_the_cookie_every_match_route_is_a_401(client: TestClient, admin: None) -> None:
    for method, path in (
        ("GET", f"/api/hub/freelancers/{MISSING}/fiscal"),
        ("PUT", f"/api/hub/freelancers/{MISSING}/fiscal"),
        ("GET", f"/api/hub/freelancers/{MISSING}/matches"),
        ("GET", f"/api/hub/freelancers/{MISSING}/matches/prefill?company_id={MISSING}"),
        ("POST", f"/api/hub/freelancers/{MISSING}/matches/preview"),
        ("POST", f"/api/hub/freelancers/{MISSING}/matches"),
        ("GET", "/api/hub/matches"),
        ("GET", f"/api/hub/matches/{MISSING}"),
        ("POST", f"/api/hub/matches/{MISSING}/cancel"),
        ("POST", f"/api/hub/matches/{MISSING}/close"),
        ("GET", f"/api/hub/contract-documents/{MISSING}/pdf"),
    ):
        assert client.request(method, path, json={}).status_code == 401, (method, path)


def test_an_admin_matches_a_card_with_a_request_and_downloads_both_pdfs(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/fiscal").json() is None
    saved = client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL)
    assert saved.status_code == 200, saved.text
    assert saved.json()["partita_iva"] == "01234567890"

    prefill = client.get(
        f"/api/hub/freelancers/{freelancer_id}/matches/prefill", params={"company_id": company_id}
    )
    assert prefill.status_code == 200, prefill.text
    assert prefill.json()["lettera"]["compenso"] == "450.00"
    assert prefill.json()["quadro_necessario"] is True
    assert BUDGET not in prefill.text

    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text
    match = created.json()
    letter = match["lettera"]
    assert match["stato"] == "bozza"
    assert re.fullmatch(r"\d{4}-001", letter["numero"])
    assert letter["stato"] == "in_attesa"
    assert BUDGET not in created.text

    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert page["quadro"]["stato"] == "generato"
    assert [item["id"] for item in page["matches"]] == [match["id"]]
    assert page["fiscale"]["codice_fiscale"] == "LVLDAA85T50H501Z"
    assert (
        client.get(f"/api/hub/matches/{match['id']}").json()["lettera"]["numero"]
        == letter["numero"]
    )

    pdf = client.get(f"/api/hub/contract-documents/{letter['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert f"lettera-di-incarico-{letter['numero']}.pdf" in pdf.headers["content-disposition"]
    assert client.get(f"/api/hub/contract-documents/{page['quadro']['id']}/pdf").status_code == 200
    signed = client.get(
        f"/api/hub/contract-documents/{letter['id']}/pdf", params={"firmato": "true"}
    )
    assert signed.status_code == 404


def test_a_repeated_create_with_the_same_id_writes_the_match_once(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    """REB-406: the wizard's own retry after a lost response sends the same
    client-generated id again with «Salva come bozza» or «Invia per la firma», and gets
    the match already written back, never a second one with another letter number."""
    freelancer_id, company_id = _ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    body = {"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA}

    first = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)
    second = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"] == given_id
    assert first.json()["lettera"]["numero"] == second.json()["lettera"]["numero"]
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert len(page["matches"]) == 1


def test_the_same_id_used_by_another_freelancers_match_is_a_409(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text

    _apply_member(client, "grace@studio.it")
    other_freelancer_id = str(client.get("/api/hub/freelancers").json()["items"][0]["id"])
    assert (
        client.put(f"/api/hub/freelancers/{other_freelancer_id}/fiscal", json=FISCAL).status_code
        == 200
    )

    refused = client.post(
        f"/api/hub/freelancers/{other_freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )

    assert refused.status_code == 409, refused.text
    assert client.get(f"/api/hub/freelancers/{other_freelancer_id}/matches").json()["matches"] == []


def test_a_real_id_collision_at_the_flush_is_a_409_not_a_500(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    renderer: FakeRenderer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REB-433: two different freelancers can race past the id check -- each reads
    "nothing written under this id yet" before the other commits (the real race, under
    Postgres, is `test_matches.py`'s own two-session gate test) -- and only the insert
    itself, protected by the id's primary key, catches the second one. This drives a
    real collision rather than forging the exception: a first freelancer's match is
    actually committed under `given_id`, the ordinary id check is made to miss that row
    once (standing in for the race window: it read "nothing written yet" a moment
    before the first committed), and the second freelancer's own `INSERT` then hits the
    real `matches_pkey` constraint. The route must still answer 409, not an unhandled
    500."""
    freelancer_id, company_id = _ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    committed = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert committed.status_code == 201, committed.text

    _apply_member(client, "grace@studio.it")
    other_freelancer_id = str(client.get("/api/hub/freelancers").json()["items"][0]["id"])
    assert (
        client.put(f"/api/hub/freelancers/{other_freelancer_id}/fiscal", json=FISCAL).status_code
        == 200
    )
    real_get = Session.get
    missed = False

    def get_missing_the_committed_match(
        self: Session, entity: type, ident: object, **kwargs: object
    ) -> object:
        nonlocal missed
        if not missed and entity is Match and str(ident) == given_id:
            missed = True
            return None
        return real_get(self, entity, ident, **kwargs)

    monkeypatch.setattr(Session, "get", get_missing_the_committed_match)

    refused = client.post(
        f"/api/hub/freelancers/{other_freelancer_id}/matches",
        json={"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )

    assert refused.status_code == 409, refused.text
    assert "un altro freelance" in refused.json()["detail"]


def test_a_repeated_create_with_the_same_id_and_changed_data_is_a_409(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    """REB-406: a retry that corrects the letter or the client before sending it again
    must not be handed back the stale match under cover of the idempotent id -- the
    request no longer matches what was saved, so this is a 409, not a 201."""
    freelancer_id, company_id = _ready(client, sender)
    given_id = "01234567-89ab-7cde-8123-456789abcdef"
    body = {"id": given_id, "company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA}

    first = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=body)
    assert first.status_code == 201, first.text

    changed = {**body, "lettera": {**LETTERA, "ruolo": "Un altro ruolo"}}
    refused = client.post(f"/api/hub/freelancers/{freelancer_id}/matches", json=changed)

    assert refused.status_code == 409, refused.text
    assert "dati diversi" in refused.json()["detail"]
    assert "Match e contratti" in refused.json()["detail"]
    page = client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()
    assert len(page["matches"]) == 1


def test_the_preview_renders_a_document_without_saving_or_numbering_it(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    body = {"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA}
    preview = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview",
        params={"documento": "lettera"},
        json=body,
    )
    assert preview.status_code == 200, preview.text
    assert preview.content.startswith(b"%PDF-")
    assert renderer.calls[-1][0] == "lettera-di-incarico"
    assert renderer.calls[-1][1]["numero"] is None
    quadro = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches/preview",
        params={"documento": "quadro"},
        json=body,
    )
    assert quadro.status_code == 200
    assert renderer.calls[-1][0] == "contratto-quadro"
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["matches"] == []


def test_a_match_without_tax_data_names_the_step_that_is_missing(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    _login(client, sender)
    freelancer_id, company_id = _apply(client), _request_company(client)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 422
    assert created.json()["detail"][0]["loc"] == ["body", "fiscale"]


def test_a_closed_request_and_a_fee_the_law_refuses_are_422s_on_their_field(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    over = {**LETTERA, "giorni_pagamento": 45}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": over},
    )
    assert refused.status_code == 422
    assert (
        client.patch(f"/api/hub/companies/{company_id}", json={"stato": "chiuso"}).status_code
        == 200
    )
    closed = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert closed.status_code == 422
    assert closed.json()["detail"][0]["loc"] == ["body", "company_id"]


def test_an_end_date_before_the_start_names_data_fine(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    backwards = {**LETTERA, "data_fine": "2026-09-01"}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": backwards},
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"][0]
    assert detail["loc"][-1] == "data_fine"
    assert detail["msg"] == "la fine prevista viene prima dell'inizio"


def test_a_payment_term_past_thirty_days_from_month_end_names_giorni_pagamento(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    over = {**LETTERA, "giorni_pagamento": 45, "fine_mese": True}
    refused = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": over},
    )
    assert refused.status_code == 422
    detail = refused.json()["detail"][0]
    assert detail["loc"][-1] == "giorni_pagamento"
    assert (
        detail["msg"]
        == "contati da fine mese, i giorni di pagamento sono al massimo 30 (legge 81/2017)"
    )


def test_a_draft_is_cancelled_once_and_only_an_active_match_closes(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    match = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    ).json()
    assert client.post(f"/api/hub/matches/{match['id']}/close").status_code == 409
    cancelled = client.post(f"/api/hub/matches/{match['id']}/cancel")
    assert cancelled.status_code == 200
    assert (cancelled.json()["stato"], cancelled.json()["lettera"]["stato"]) == (
        "annullato",
        "annullato",
    )
    again = client.post(f"/api/hub/matches/{match['id']}/cancel")
    assert again.status_code == 409
    assert "bozza" in again.json()["detail"]


def test_a_soft_deleted_freelancer_hides_all_four_match_routes(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    match = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    ).json()
    letter_id = match["lettera"]["id"]
    assert client.delete(f"/api/hub/freelancers/{freelancer_id}").status_code == 200

    assert client.get(f"/api/hub/matches/{match['id']}").status_code == 404
    assert client.get(f"/api/hub/contract-documents/{letter_id}/pdf").status_code == 404
    assert client.post(f"/api/hub/matches/{match['id']}/cancel").status_code == 404
    assert client.post(f"/api/hub/matches/{match['id']}/close").status_code == 404


def test_a_render_that_fails_is_a_503_with_a_sentence(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    client.app.dependency_overrides[get_renderer] = lambda: FailingRenderer()  # type: ignore[attr-defined]
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 503
    assert created.json()["detail"].startswith("La generazione del contratto non è riuscita")
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/matches").json()["matches"] == []


def _apply_member(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Bob",
            "cognome": "Ross",
            "email": email,
            "tariffa_giornaliera": "300",
            "posizione": "Designer",
            "remoto": "remoto",
        },
        files={"cv": ("Bob CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text


def _login_as(client: TestClient, sender: RecordingSender, email: str) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def test_without_the_cookie_the_match_list_is_a_401(client: TestClient, admin: None) -> None:
    assert client.get("/api/hub/matches").status_code == 401


def test_a_signed_in_member_hitting_the_match_list_is_403_not_401(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _apply_member(client, "bob@studio.it")
    _login_as(client, sender, "bob@studio.it")
    assert client.get("/api/hub/matches").status_code == 403


def test_the_match_list_answers_200_with_filters_and_carries_no_budget_or_tax_field(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text
    match = created.json()

    listed = client.get("/api/hub/matches")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["totale"] == 1
    assert [item["id"] for item in body["items"]] == [match["id"]]
    row = body["items"][0]
    assert row["nome_azienda"] == "ACME Srl"
    assert row["lettera_numero"] == match["lettera"]["numero"]
    assert BUDGET not in listed.text
    assert not any(key in row for key in ("codice_fiscale", "partita_iva", "domicilio", "pec"))

    by_state = client.get("/api/hub/matches", params={"stato": "bozza"})
    assert by_state.status_code == 200
    assert by_state.json()["totale"] == 1
    by_wrong_state = client.get("/api/hub/matches", params={"stato": "attivo"})
    assert by_wrong_state.json()["totale"] == 0

    by_search = client.get("/api/hub/matches", params={"q": "ACME"})
    assert [item["id"] for item in by_search.json()["items"]] == [match["id"]]
    by_missing_search = client.get("/api/hub/matches", params={"q": "nessuno"})
    assert by_missing_search.json()["items"] == []


def test_the_match_list_422s_on_an_unknown_state(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    refused = client.get("/api/hub/matches", params={"stato": "chissà"})
    assert refused.status_code == 422


def test_a_malformed_signer_setting_is_a_503_not_a_crash(
    client: TestClient, admin: None, sender: RecordingSender, renderer: FakeRenderer
) -> None:
    freelancer_id, company_id = _ready(client, sender)
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        _env_file=None,
        signer_json="{non json",  # type: ignore[call-arg]
    )
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 503
    assert "REBASE_SIGNER_JSON" in created.json()["detail"]
