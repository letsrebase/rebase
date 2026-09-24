"""What «Crea match» leaves behind, over HTTP, for the signing tests (REB-387 phase 3):
an admin signed in, a card, a request, the tax data and a draft match. Reuses
`test_matches_api.py`'s own constants and route helpers rather than copying them
(REB-406 fix round 1, M7): `enter` is `test_matches_api._login_as`, the one copy of
that recipe, and `draft_match` builds on `_ready` instead of redoing it. Tasks 3 and 5
import this module too."""

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
    _login_as,
    _ready,
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
    """Signs `email` in through the magic link, as the member area does: Tasks 3 and 5
    use this to enter as the freelancer, not only the admin `_ready` signs in as."""
    _login_as(client, sender, email)


def draft_match(client: TestClient, sender: RecordingSender) -> dict[str, Any]:
    """The admin signed in, Ada's card, ACME's request and her tax data (`_ready`), then
    a draft match: the match as `POST /api/hub/freelancers/{id}/matches` answered it."""
    freelancer_id, company_id = _ready(client, sender)
    created = client.post(
        f"/api/hub/freelancers/{freelancer_id}/matches",
        json={"company_id": company_id, "cliente": CLIENTE, "lettera": LETTERA},
    )
    assert created.status_code == 201, created.text
    body: dict[str, Any] = created.json()
    return body
