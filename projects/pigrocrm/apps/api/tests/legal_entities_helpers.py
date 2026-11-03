"""The one legal entity every API test space has (REB-616).

A real space is born with its default legal entity (`ensure_defaults` writes it at
provisioning), and since REB-615 the fiscal profile belongs to it, so a test that used
to `PUT /api/emitter` or `/api/fiscal-profile` now addresses `/api/legal-entities/{id}`. The
autouse fixture in `conftest.py` seeds that row for every test; this helper reads its
id back through the API, the way the SPA does, so a test never has to know it.
"""

from fastapi.testclient import TestClient


def legal_entity_url(client: TestClient, suffix: str = "") -> str:
    legal_entities = client.get("/api/legal-entities")
    assert legal_entities.status_code == 200, legal_entities.text
    assert legal_entities.json(), "the default azienda is missing: the autouse fixture did not run"
    return f"/api/legal-entities/{legal_entities.json()[0]['id']}{suffix}"
