"""The one azienda every API test space has (REB-616).

A real space is born with its default azienda (`ensure_defaults` writes it at
provisioning), and since REB-615 the fiscal profile belongs to it, so a test that used
to `PUT /api/emitter` or `/api/fiscal-profile` now addresses `/api/aziende/{id}`. The
autouse fixture in `conftest.py` seeds that row for every test; this helper reads its
id back through the API, the way the SPA does, so a test never has to know it.
"""

from fastapi.testclient import TestClient


def azienda_url(client: TestClient, suffix: str = "") -> str:
    aziende = client.get("/api/aziende")
    assert aziende.status_code == 200, aziende.text
    assert aziende.json(), "the default azienda is missing: the autouse fixture did not run"
    return f"/api/aziende/{aziende.json()[0]['id']}{suffix}"
