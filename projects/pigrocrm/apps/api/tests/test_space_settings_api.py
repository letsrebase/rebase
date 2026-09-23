"""Impostazioni → Spazio over HTTP, and the rows actually changing what every route sees."""

from fastapi.testclient import TestClient

from pigrocrm_api.deps import invalidate_space_settings


def test_reading_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/settings/space").status_code == 401
    assert client.put("/api/settings/space", json={"mcp_full_access": True}).status_code == 401


def test_the_root_reads_the_environment_and_writes_over_it(logged_in: TestClient) -> None:
    invalidate_space_settings(None)
    before = logged_in.get("/api/settings/space")
    assert before.status_code == 200, before.text
    body = before.json()
    assert body["spazio"] is None
    assert body["gmail_configurato"] is False
    assert body["concentrazione_soglia_preferita"] == 0.30
    assert body["sovrascritte"] == []

    saved = logged_in.put(
        "/api/settings/space",
        json={
            "solleciti_grace_days": 21,
            "mcp_full_access": True,
            "google_client_id": "abc.apps",
            "concentrazione_soglia_preferita": 0.5,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["solleciti_grace_days"] == 21
    assert saved.json()["concentrazione_soglia_preferita"] == 0.5
    assert saved.json()["mcp_full_access"] is True
    # A client id alone is not a configured Gmail: no secret, no public URL.
    assert saved.json()["gmail_configurato"] is False
    # But a token key was made for it, once, and is not shown.
    assert saved.json()["google_token_key_impostata"] is True
    assert "google_token_key" in saved.json()["sovrascritte"]
    # The key itself is never a field of the response: only the fact that it exists.
    assert "google_token_key" not in saved.json()
    assert not any(
        isinstance(v, str) and len(v) == 44 and v.endswith("=") for v in saved.json().values()
    )

    # The next read sees it, without waiting for the cache to expire.
    again = logged_in.get("/api/settings/space").json()
    assert again["solleciti_grace_days"] == 21
    assert again["concentrazione_soglia_preferita"] == 0.5

    # And clearing puts the environment's value back.
    cleared = logged_in.put("/api/settings/space", json={"google_client_id": ""}).json()
    assert cleared["google_client_id"] == ""
    assert "google_client_id" not in cleared["sovrascritte"]


def test_an_out_of_range_value_is_a_422(logged_in: TestClient) -> None:
    assert (
        logged_in.put("/api/settings/space", json={"solleciti_max_reminders": 7}).status_code == 422
    )
    assert (
        logged_in.put(
            "/api/settings/space", json={"concentrazione_soglia_preferita": 1.5}
        ).status_code
        == 422
    )
