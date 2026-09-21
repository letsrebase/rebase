"""`GET /api/calendar` and `/api/activities`: the month in one request, and the
commitments that fill it.

What the router adds to the service is the month parameter, whose validation has to be
a 422 and not a 500, and the scoping of the hours to whoever is asking. Both are here;
the assembly itself is `packages/core/tests/test_calendario_service.py`.
"""

import calendar
from datetime import date

from fastapi.testclient import TestClient

from pigrocrm.core.clock import oggi_in_italia

# Derived and never written as a literal, for the reason `packages/core/tests/
# periodo_fiscale.py` sets out at length: a suite with `2026-09` in it is a suite with an
# expiry date on it. `TimeEntryService` refuses a day after today, so the hours go on the
# first of the current month -- always in the month being read, never in the future, and
# the same day on every run.
OGGI = oggi_in_italia()
MESE = OGGI.strftime("%Y-%m")
PRIMO = OGGI.replace(day=1)
ULTIMO = OGGI.replace(day=calendar.monthrange(OGGI.year, OGGI.month)[1])


def _customer(client: TestClient) -> str:
    created = client.post("/api/customers", json={"ragione_sociale": "ACME S.r.l."})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _deal(client: TestClient) -> str:
    """A deal, and the pipeline it needs to exist in: a deal with no stage is refused,
    so the seed is part of the fixture rather than an assumption about the database."""
    assert client.post("/api/pipeline-stages/seed").status_code == 200
    created = client.post("/api/deals", json={"nome": "Progetto", "customer_id": _customer(client)})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _me(client: TestClient) -> str:
    """The caller's own user id. `user_id` is required on a time entry -- without it
    there is no labour cost and half the margin is undefined -- and the calendar logs
    against whoever is looking at it."""
    return str(client.get("/api/auth/me").json()["id"])


# --- the month -------------------------------------------------------------------------


def test_a_month_with_nothing_in_it_answers_an_empty_grid(logged_in: TestClient) -> None:
    response = logged_in.get("/api/calendar", params={"mese": MESE})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["giorni"] == []
    assert (body["da"], body["a"]) == (PRIMO.isoformat(), ULTIMO.isoformat())
    # A string, like every other Decimal in this API: the browser reads it digit by
    # digit and never through a binary float.
    assert body["ore_totali"] == "0.00"


def test_a_month_that_is_not_one_is_a_422_before_anything_is_read(
    logged_in: TestClient,
) -> None:
    """The pattern is on the query parameter as well as in `month_bounds`, and both are
    wanted: here it refuses before a session is touched."""
    assert logged_in.get("/api/calendar", params={"mese": "2026-13"}).status_code == 422
    assert logged_in.get("/api/calendar", params={"mese": "settembre"}).status_code == 422
    assert logged_in.get("/api/calendar").status_code == 422


def test_an_activity_appears_on_its_day_with_its_state(logged_in: TestClient) -> None:
    created = logged_in.post(
        "/api/activities", json={"titolo": "Sollecitare Rossi", "scadenza": PRIMO.isoformat()}
    )
    assert created.status_code == 201, created.text

    body = logged_in.get("/api/calendar", params={"mese": MESE}).json()

    assert [day["giorno"] for day in body["giorni"]] == [PRIMO.isoformat()]
    assert [item["titolo"] for item in body["giorni"][0]["attivita"]] == ["Sollecitare Rossi"]
    assert body["giorni"][0]["attivita"][0]["stato"] == "aperta"


def test_an_activity_without_a_date_is_outside_the_grid(logged_in: TestClient) -> None:
    """The section under the calendar, never a cell: an undated commitment is not late
    and is not for today."""
    logged_in.post("/api/activities", json={"titolo": "Chiedere il codice SDI"})

    body = logged_in.get("/api/calendar", params={"mese": MESE}).json()

    assert body["giorni"] == []
    assert [item["titolo"] for item in body["attivita_senza_scadenza"]] == [
        "Chiedere il codice SDI"
    ]


def test_the_calendar_shows_the_hours_of_whoever_is_asking(
    logged_in: TestClient, admin_user: object
) -> None:
    """«My calendar». The hours are logged through the ordinary endpoint, so the rate is
    frozen and the period lock applies -- the calendar is a way of filling that form and
    not a second way of writing the table."""
    deal_id = _deal(logged_in)
    logged = logged_in.post(
        "/api/time-entries",
        json={
            "deal_id": deal_id,
            "user_id": _me(logged_in),
            "data": PRIMO.isoformat(),
            "ore": "8.00",
            "descrizione": "Giornata",
        },
    )
    assert logged.status_code == 201, logged.text

    body = logged_in.get("/api/calendar", params={"mese": MESE}).json()

    assert [day["giorno"] for day in body["giorni"]] == [PRIMO.isoformat()]
    day = body["giorni"][0]
    assert day["ore"] == "8.00"
    assert day["per_deal"][0]["deal_nome"] == "Progetto"
    assert day["per_deal"][0]["cliente"] == "ACME S.r.l."
    assert body["ore_totali"] == "8.00"


# --- the commitments -------------------------------------------------------------------


def test_a_commitment_can_be_completed_cancelled_and_reopened(logged_in: TestClient) -> None:
    """Three closures, three routes: the state machine is the service's business, and
    «done» has a side effect -- the day -- a caller must not be able to supply."""
    attivita_id = logged_in.post("/api/activities", json={"titolo": "Da fare"}).json()["id"]

    done = logged_in.post(f"/api/activities/{attivita_id}/complete").json()
    assert done["stato"] == "completata"
    assert done["completata_il"] is not None

    reopened = logged_in.post(f"/api/activities/{attivita_id}/reopen").json()
    assert (reopened["stato"], reopened["completata_il"]) == ("aperta", None)

    cancelled = logged_in.post(f"/api/activities/{attivita_id}/cancel").json()
    assert cancelled["stato"] == "annullata"


def test_completing_a_cancelled_commitment_is_a_409_that_says_what_to_do(
    logged_in: TestClient,
) -> None:
    attivita_id = logged_in.post("/api/activities", json={"titolo": "Annullata"}).json()["id"]
    logged_in.post(f"/api/activities/{attivita_id}/cancel")

    response = logged_in.post(f"/api/activities/{attivita_id}/complete")

    assert response.status_code == 409
    assert "riaprila" in response.json()["detail"]


def test_two_references_are_a_422_naming_the_second_one(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    deal_id = _deal(logged_in)

    response = logged_in.post(
        "/api/activities",
        json={"titolo": "Due riferimenti", "customer_id": customer_id, "deal_id": deal_id},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


def test_the_date_can_be_removed_through_its_own_flag(logged_in: TestClient) -> None:
    """PATCH cannot tell «absent» from «null», so clearing is a flag -- and an activity
    whose date turns out to be wrong has to be able to lose it rather than keep an
    invented one."""
    attivita_id = logged_in.post(
        "/api/activities", json={"titolo": "Con data", "scadenza": PRIMO.isoformat()}
    ).json()["id"]

    cleared = logged_in.patch(
        f"/api/activities/{attivita_id}", json={"scadenza_da_rimuovere": True}
    ).json()

    assert cleared["scadenza"] is None


def test_a_commitment_filtered_by_due_date_never_includes_the_undated_ones(
    logged_in: TestClient,
) -> None:
    logged_in.post("/api/activities", json={"titolo": "Senza data"})
    logged_in.post("/api/activities", json={"titolo": "Con data", "scadenza": PRIMO.isoformat()})

    body = logged_in.get("/api/activities", params={"scade_entro": ULTIMO.isoformat()}).json()

    assert [item["titolo"] for item in body["items"]] == ["Con data"]


def test_archiving_hides_it_from_the_list_and_from_the_month(logged_in: TestClient) -> None:
    attivita_id = logged_in.post(
        "/api/activities", json={"titolo": "Creata per errore", "scadenza": PRIMO.isoformat()}
    ).json()["id"]

    assert logged_in.delete(f"/api/activities/{attivita_id}").status_code == 204
    assert logged_in.get("/api/activities").json()["items"] == []
    assert logged_in.get("/api/calendar", params={"mese": MESE}).json()["giorni"] == []

    restored = logged_in.post(f"/api/activities/{attivita_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["scadenza"] == PRIMO.isoformat()


def test_a_collaborator_can_write_their_own_commitments(collaborator_client: TestClient) -> None:
    """Nothing here is admin-gated: a commitment is not a fiscal act, and the refusals
    that exist live in the service."""
    response = collaborator_client.post("/api/activities", json={"titolo": "La mia"})

    assert response.status_code == 201, response.text


def test_the_openapi_document_declares_the_new_routes(logged_in: TestClient) -> None:
    paths = logged_in.get("/openapi.json").json()["paths"]
    for path in (
        "/api/calendar",
        "/api/activities",
        "/api/activities/{attivita_id}",
        "/api/activities/{attivita_id}/complete",
        "/api/activities/{attivita_id}/cancel",
        "/api/activities/{attivita_id}/reopen",
    ):
        assert path in paths, path


def test_a_day_can_carry_hours_a_commitment_and_nothing_else_it_did_not_earn(
    logged_in: TestClient,
) -> None:
    """The two kinds meeting on one day, which is the case the grid draws."""
    deal_id = _deal(logged_in)
    logged_in.post(
        "/api/time-entries",
        json={
            "deal_id": deal_id,
            "user_id": _me(logged_in),
            "data": PRIMO.isoformat(),
            "ore": "4.00",
            "descrizione": "Mezza",
        },
    )
    logged_in.post("/api/activities", json={"titolo": "Sollecito", "scadenza": PRIMO.isoformat()})

    day = logged_in.get("/api/calendar", params={"mese": MESE}).json()["giorni"][0]

    assert day["giorno"] == PRIMO.isoformat()
    assert day["ore"] == "4.00"
    assert len(day["attivita"]) == 1
    # No invoice falls due that day, and the key is there and empty rather than absent:
    # a client should not have to distinguish «no invoices» from «this field is missing».
    assert day["fatture"] == []


def test_the_month_says_which_day_is_today(logged_in: TestClient) -> None:
    """`oggi` travels because «today» is a day in the emitter's zone, and a browser in
    another timezone would otherwise mark the wrong cell."""
    body = logged_in.get("/api/calendar", params={"mese": MESE}).json()

    assert date.fromisoformat(body["oggi"])
