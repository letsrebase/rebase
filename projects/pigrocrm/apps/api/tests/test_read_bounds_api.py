"""The reads that used to be open-ended carry a ceiling at the request boundary: a value
past it is a 422 before any service runs, and the web client's own calls (which send no
`limit`) keep reading what they read."""

import pytest
from fastapi.testclient import TestClient

ANY_ID = "00000000-0000-0000-0000-000000000000"


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (f"/api/customers/{ANY_ID}/notes", {"limit": 0}),
        (f"/api/customers/{ANY_ID}/notes", {"limit": 201}),
        ("/api/reminders", {"limit": 0}),
        ("/api/reminders", {"limit": 201}),
        ("/api/work-units", {"contract_id": ANY_ID, "limit": 201}),
        ("/api/invoices/register/1999/gaps", {}),
        ("/api/invoices/register/3000/gaps", {}),
    ],
)
def test_a_read_past_its_ceiling_is_a_422(
    logged_in: TestClient, path: str, params: dict[str, object]
) -> None:
    assert logged_in.get(path, params=params).status_code == 422


def test_the_notes_thread_honours_its_limit(logged_in: TestClient) -> None:
    customer = logged_in.post("/api/customers", json={"legal_name": "ACME"}).json()["id"]
    for text in ("uno", "due", "tre"):
        added = logged_in.post(f"/api/customers/{customer}/notes", json={"text": text})
        assert added.status_code == 201, added.text

    everything = logged_in.get(f"/api/customers/{customer}/notes").json()
    capped = logged_in.get(f"/api/customers/{customer}/notes", params={"limit": 2}).json()

    assert len(everything) == 3
    assert [note["id"] for note in capped] == [note["id"] for note in everything[:2]]


def test_a_year_inside_the_register_range_is_served(logged_in: TestClient) -> None:
    assert logged_in.get("/api/invoices/register/2026/gaps").status_code == 200
