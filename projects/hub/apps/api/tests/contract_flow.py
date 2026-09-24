"""What «Crea match» leaves behind, over HTTP, for the signing tests (REB-387 phase 3):
an admin signed in, a card, a request, the tax data and a draft match. Reuses
`test_matches_api.py`'s own constants and route helpers rather than copying them (REB-406
controller ruling); this module only adds what phase 3 needs on top of them: signing in
as an arbitrary address (a freelancer, for the member page, not only the admin) and the
one-call `draft_match` composition. Tasks 3 and 5 import this module too."""

import re
from typing import Any

from fastapi.testclient import TestClient
from test_matches_api import (
    ADMIN_EMAIL,
    CLIENTE,
    FISCAL,
    LETTERA,
    MISSING,
    PDF,
    TABLES,
    _apply,
    _request_company,
)

from rebase_core.mail import RecordingSender

FREELANCER_EMAIL = "ada@studio.it"
# Fiction, like the public example: rebase's own fields as the setting would carry them.
SIGNER = {
    "rebase-sede": "Milano",
    "rebase-cf": "00000000000",
    "rebase-piva": "00000000000",
    "rebase-codice-destinatario": "0000000",
    "rebase-pec": "rebase@pec.example",
    "rebase-rappresentante": "Nome Cognome",
}

__all__ = [
    "ADMIN_EMAIL",
    "CLIENTE",
    "FISCAL",
    "FREELANCER_EMAIL",
    "LETTERA",
    "MISSING",
    "PDF",
    "SIGNER",
    "TABLES",
    "draft_match",
    "enter",
]


def enter(client: TestClient, sender: RecordingSender, email: str) -> None:
    """Signs `email` in through the magic link, as the member area does."""
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    found = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert found
    assert client.post("/api/hub/auth/enter", json={"token": found.group(1)}).status_code == 200


def draft_match(client: TestClient, sender: RecordingSender) -> dict[str, Any]:
    """The admin signed in, Ada's card, ACME's request, her tax data and a draft match:
    the match as `POST /api/hub/freelancers/{id}/matches` answered it."""
    enter(client, sender, ADMIN_EMAIL)
    freelancer_id, company_id = _apply(client), _request_company(client)
    saved = client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL)
    assert saved.status_code == 200, saved.text
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text
    body: dict[str, Any] = created.json()
    return body
