"""A settings save built on a stale row answers 409 `stale_row` on every settings `PUT`
(REB-622, spec 2026-10-03 §11): the azienda, its fiscal profile and the space settings.
A save built on the current row still succeeds, and a body with no version keeps the
old whole-row behaviour. The role matrix is untouched: the check runs after the role
gate, on the same routes, with the same roles."""

from datetime import datetime
from typing import Any

from aziende_helpers import azienda_url
from fastapi.testclient import TestClient

from pigrocrm_api.deps import invalidate_space_settings

# What `LegalEntityRead` carries beyond the upsert body: stripped so the read can be sent
# back, the way the panel does, with `updated_at` kept as the version.
READ_ONLY = {"id", "predefinita", "attiva", "logo_key", "firma_key", "created_at"}


def _upsert_body(read: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**{k: v for k, v in read.items() if k not in READ_ONLY}, **changes}


def _instant(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _assert_stale(response: Any, *, current: str) -> None:
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "stale_row"
    assert body["title"] == "Riga cambiata nel frattempo"
    assert body["detail"] == "qualcun altro ha salvato nel frattempo: ricarica e riprova"
    # The same instant as the read's: the problem document is rendered by
    # `jsonable_encoder` (`+00:00`), the response model by pydantic (`Z`).
    assert _instant(body["updated_at"]) == _instant(current)


def test_the_azienda_put_refuses_a_stale_version_and_accepts_the_current_one(
    logged_in: TestClient,
) -> None:
    url = azienda_url(logged_in)
    first = logged_in.get(url).json()
    second = logged_in.put(url, json=_upsert_body(first, comune="Torino"))
    assert second.status_code == 200, second.text
    assert second.json()["updated_at"] != first["updated_at"]

    stale = logged_in.put(url, json=_upsert_body(first, comune="Genova"))
    _assert_stale(stale, current=second.json()["updated_at"])
    assert logged_in.get(url).json()["comune"] == "Torino"
    # The row always exists, so `null` is never its version: sent, it is refused.
    _assert_stale(
        logged_in.put(url, json=_upsert_body(first, comune="Genova", updated_at=None)),
        current=second.json()["updated_at"],
    )

    # No version, no check: an older client's whole-row replace.
    unversioned = _upsert_body(first, comune="Genova")
    del unversioned["updated_at"]
    assert logged_in.put(url, json=unversioned).status_code == 200


def test_the_fiscal_profile_put_refuses_a_stale_version_and_accepts_the_current_one(
    logged_in: TestClient,
) -> None:
    url = azienda_url(logged_in, "/fiscal-profile")
    # `null` is the version of a profile not saved yet: the panel's first save.
    created = logged_in.put(url, json={"codice_regime": "RF19", "updated_at": None})
    assert created.status_code == 200, created.text
    first = created.json()
    # The same `null` once the row exists is a draft built on a state that is gone.
    _assert_stale(
        logged_in.put(url, json={"codice_regime": "RF19", "updated_at": None}),
        current=first["updated_at"],
    )
    second = logged_in.put(
        url,
        json={"codice_regime": "RF19", "giorni_scadenza": 60, "updated_at": first["updated_at"]},
    )
    assert second.status_code == 200, second.text

    stale = logged_in.put(
        url,
        json={"codice_regime": "RF19", "giorni_scadenza": 90, "updated_at": first["updated_at"]},
    )
    _assert_stale(stale, current=second.json()["updated_at"])
    assert logged_in.get(url).json()["giorni_scadenza"] == 60
    assert (
        logged_in.put(url, json={"codice_regime": "RF19", "giorni_scadenza": 90}).status_code == 200
    )


def test_the_space_settings_put_refuses_a_stale_version_and_accepts_the_current_one(
    logged_in: TestClient,
) -> None:
    invalidate_space_settings(None)
    before = logged_in.get("/api/settings/space").json()
    # The autouse fixtures write no override row, so the version starts empty.
    assert before["updated_at"] is None
    first = logged_in.put("/api/settings/space", json={"mcp_full_access": True, "updated_at": None})
    assert first.status_code == 200, first.text
    assert first.json()["updated_at"] is not None

    # The same `null` again is a draft built on a state that is gone.
    stale_null = logged_in.put(
        "/api/settings/space", json={"gmail_backfill_days": 10, "updated_at": None}
    )
    _assert_stale(stale_null, current=first.json()["updated_at"])

    second = logged_in.put(
        "/api/settings/space",
        json={"gmail_backfill_days": 10, "updated_at": first.json()["updated_at"]},
    )
    assert second.status_code == 200, second.text
    stale = logged_in.put(
        "/api/settings/space",
        json={"gmail_backfill_days": 20, "updated_at": first.json()["updated_at"]},
    )
    _assert_stale(stale, current=second.json()["updated_at"])
    assert logged_in.get("/api/settings/space").json()["gmail_backfill_days"] == 10
    # A body without the key is an older client: nothing is checked.
    assert logged_in.put("/api/settings/space", json={"gmail_backfill_days": 20}).status_code == 200


def test_a_collaborator_meets_the_role_gate_before_the_version_check(
    collaborator_client: TestClient,
) -> None:
    """The matrix is unchanged: a role the route refuses is refused with 403 whatever
    version it sends, stale or not."""
    url = azienda_url(collaborator_client)
    read = collaborator_client.get(url).json()
    refused = collaborator_client.put(
        url, json=_upsert_body(read, comune="Torino", updated_at="2020-01-01T00:00:00Z")
    )
    assert refused.status_code == 403, refused.text
