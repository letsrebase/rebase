"""`GET /api/calendar` and `/api/activities`: the month in one request, and the
commitments that fill it.

What the router adds to the service is the month parameter, whose validation has to be
a 422 and not a 500, and the scoping of the hours to whoever is asking. Both are here;
the assembly itself is `packages/core/tests/test_calendario_service.py`.
"""

import calendar
from datetime import date, timedelta

from fastapi.testclient import TestClient

from pigrocrm.core.db.clock import today_local

# Derived and never written as a literal, for the reason `packages/core/tests/
# periodo_fiscale.py` sets out at length: a suite with `2026-09` in it is a suite with an
# expiry date on it. `TimeEntryService` refuses a day after today, so the hours go on the
# first of the current month -- always in the month being read, never in the future, and
# the same day on every run.
TODAY = today_local()
Month = TODAY.strftime("%Y-%m")
FIRST = TODAY.replace(day=1)
LAST = TODAY.replace(day=calendar.monthrange(TODAY.year, TODAY.month)[1])


def _customer(client: TestClient) -> str:
    created = client.post("/api/customers", json={"legal_name": "ACME S.r.l."})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _deal(client: TestClient) -> str:
    """A deal, and the pipeline it needs to exist in: a deal with no stage is refused,
    so the seed is part of the fixture rather than an assumption about the database."""
    assert client.post("/api/pipeline-stages/seed").status_code == 200
    created = client.post("/api/deals", json={"name": "Progetto", "customer_id": _customer(client)})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _me(client: TestClient) -> str:
    """The caller's own user id. `user_id` is required on a time entry -- without it
    there is no labour cost and half the margin is undefined -- and the calendar logs
    against whoever is looking at it."""
    return str(client.get("/api/auth/me").json()["id"])


# --- the month -------------------------------------------------------------------------


def test_a_month_with_nothing_in_it_answers_an_empty_grid(logged_in: TestClient) -> None:
    response = logged_in.get("/api/calendar", params={"month": Month})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["days"] == []
    assert (body["da"], body["a"]) == (FIRST.isoformat(), LAST.isoformat())
    # A string, like every other Decimal in this API: the browser reads it digit by
    # digit and never through a binary float.
    assert body["total_hours"] == "0.00"


def test_a_month_that_is_not_one_is_a_422_before_anything_is_read(
    logged_in: TestClient,
) -> None:
    """The pattern is on the query parameter as well as in `month_bounds`, and both are
    wanted: here it refuses before a session is touched."""
    assert logged_in.get("/api/calendar", params={"month": "2026-13"}).status_code == 422
    assert logged_in.get("/api/calendar", params={"month": "settembre"}).status_code == 422
    assert logged_in.get("/api/calendar").status_code == 422


def test_an_activity_appears_on_its_day_with_its_state(logged_in: TestClient) -> None:
    created = logged_in.post(
        "/api/activities", json={"title": "Sollecitare Rossi", "due_date": FIRST.isoformat()}
    )
    assert created.status_code == 201, created.text

    body = logged_in.get("/api/calendar", params={"month": Month}).json()

    assert [day["day"] for day in body["days"]] == [FIRST.isoformat()]
    assert [item["title"] for item in body["days"][0]["attivita"]] == ["Sollecitare Rossi"]
    assert body["days"][0]["attivita"][0]["status"] == "open"


def test_an_activity_without_a_date_is_outside_the_grid(logged_in: TestClient) -> None:
    """The section under the calendar, never a cell: an undated commitment is not late
    and is not for today."""
    logged_in.post("/api/activities", json={"title": "Chiedere il codice SDI"})

    body = logged_in.get("/api/calendar", params={"month": Month}).json()

    assert body["days"] == []
    assert [item["title"] for item in body["task_without_due_date"]] == ["Chiedere il codice SDI"]


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
            "date": FIRST.isoformat(),
            "hours": "8.00",
            "description": "Giornata",
        },
    )
    assert logged.status_code == 201, logged.text

    body = logged_in.get("/api/calendar", params={"month": Month}).json()

    assert [day["day"] for day in body["days"]] == [FIRST.isoformat()]
    day = body["days"][0]
    assert day["hours"] == "8.00"
    assert day["per_deal"][0]["deal_name"] == "Progetto"
    assert day["per_deal"][0]["customer"] == "ACME S.r.l."
    assert body["total_hours"] == "8.00"


# --- the commitments -------------------------------------------------------------------


def test_a_commitment_can_be_completed_cancelled_and_reopened(logged_in: TestClient) -> None:
    """Three closures, three routes: the state machine is the service's business, and
    «done» has a side effect -- the day -- a caller must not be able to supply."""
    task_id = logged_in.post("/api/activities", json={"title": "Da fare"}).json()["id"]

    done = logged_in.post(f"/api/activities/{task_id}/complete").json()
    assert done["status"] == "completed"
    assert done["completed_at"] is not None

    reopened = logged_in.post(f"/api/activities/{task_id}/reopen").json()
    assert (reopened["status"], reopened["completed_at"]) == ("open", None)

    cancelled = logged_in.post(f"/api/activities/{task_id}/cancel").json()
    assert cancelled["status"] == "cancelled"


def test_completing_a_cancelled_commitment_is_a_409_that_says_what_to_do(
    logged_in: TestClient,
) -> None:
    task_id = logged_in.post("/api/activities", json={"title": "Annullata"}).json()["id"]
    logged_in.post(f"/api/activities/{task_id}/cancel")

    response = logged_in.post(f"/api/activities/{task_id}/complete")

    assert response.status_code == 409
    assert "riaprila" in response.json()["detail"]


def test_two_references_are_a_422_naming_the_second_one(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    deal_id = _deal(logged_in)

    response = logged_in.post(
        "/api/activities",
        json={"title": "Due riferimenti", "customer_id": customer_id, "deal_id": deal_id},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


def test_the_date_can_be_removed_through_its_own_flag(logged_in: TestClient) -> None:
    """PATCH cannot tell «absent» from «null», so clearing is a flag -- and an activity
    whose date turns out to be wrong has to be able to lose it rather than keep an
    invented one."""
    task_id = logged_in.post(
        "/api/activities", json={"title": "Con data", "due_date": FIRST.isoformat()}
    ).json()["id"]

    cleared = logged_in.patch(
        f"/api/activities/{task_id}", json={"due_date_to_remove": True}
    ).json()

    assert cleared["due_date"] is None


def test_a_commitment_filtered_by_due_date_never_includes_the_undated_ones(
    logged_in: TestClient,
) -> None:
    logged_in.post("/api/activities", json={"title": "Senza data"})
    logged_in.post("/api/activities", json={"title": "Con data", "due_date": FIRST.isoformat()})

    body = logged_in.get("/api/activities", params={"due_within": LAST.isoformat()}).json()

    assert [item["title"] for item in body["items"]] == ["Con data"]


def test_archiving_hides_it_from_the_list_and_from_the_month(logged_in: TestClient) -> None:
    task_id = logged_in.post(
        "/api/activities", json={"title": "Creata per errore", "due_date": FIRST.isoformat()}
    ).json()["id"]

    assert logged_in.delete(f"/api/activities/{task_id}").status_code == 204
    assert logged_in.get("/api/activities").json()["items"] == []
    assert logged_in.get("/api/calendar", params={"month": Month}).json()["days"] == []

    restored = logged_in.post(f"/api/activities/{task_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["due_date"] == FIRST.isoformat()


def test_the_archived_ones_are_listed_apart_so_they_can_be_restored(logged_in: TestClient) -> None:
    task_id = logged_in.post("/api/activities", json={"title": "Creata per errore"}).json()["id"]
    logged_in.delete(f"/api/activities/{task_id}")

    assert logged_in.get("/api/activities").json()["items"] == []
    archived = logged_in.get("/api/activities", params={"archived": "true"}).json()
    assert [item["id"] for item in archived["items"]] == [task_id]

    logged_in.post(f"/api/activities/{task_id}/restore")
    assert logged_in.get("/api/activities", params={"archived": "true"}).json()["items"] == []
    assert [item["id"] for item in logged_in.get("/api/activities").json()["items"]] == [task_id]


def test_a_collaborator_can_write_their_own_commitments(collaborator_client: TestClient) -> None:
    """Nothing here is admin-gated: a commitment is not a fiscal act, and the refusals
    that exist live in the service."""
    response = collaborator_client.post("/api/activities", json={"title": "La mia"})

    assert response.status_code == 201, response.text


def test_the_openapi_document_declares_the_new_routes(logged_in: TestClient) -> None:
    paths = logged_in.get("/openapi.json").json()["paths"]
    for path in (
        "/api/calendar",
        "/api/activities",
        "/api/activities/{task_id}",
        "/api/activities/{task_id}/complete",
        "/api/activities/{task_id}/cancel",
        "/api/activities/{task_id}/reopen",
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
            "date": FIRST.isoformat(),
            "hours": "4.00",
            "description": "Mezza",
        },
    )
    logged_in.post("/api/activities", json={"title": "Sollecito", "due_date": FIRST.isoformat()})

    day = logged_in.get("/api/calendar", params={"month": Month}).json()["days"][0]

    assert day["day"] == FIRST.isoformat()
    assert day["hours"] == "4.00"
    assert len(day["attivita"]) == 1
    # No invoice falls due that day, and the key is there and empty rather than absent:
    # a client should not have to distinguish «no invoices» from «this field is missing».
    assert day["invoices"] == []


def test_the_month_says_which_day_is_today(logged_in: TestClient) -> None:
    """`today` travels because «today» is a day in the emitter's zone, and a browser in
    another timezone would otherwise mark the wrong cell."""
    body = logged_in.get("/api/calendar", params={"month": Month}).json()

    assert date.fromisoformat(body["today"])


# --- the Oggi panel (REB-725) ----------------------------------------------------------


def test_today_is_not_read_as_an_task_id(logged_in: TestClient) -> None:
    """`/api/activities/today` is declared before `/{task_id}`: a path parameter that
    claimed the word would answer 422 for the page that needs it."""
    response = logged_in.get("/api/activities/today")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "today",
        "overdue",
        "today_due",
        "next_seven_days",
        "without_due_date",
    }


def test_an_overdue_task_leaves_the_panel_when_it_is_completed(
    logged_in: TestClient,
) -> None:
    """The card's own sentence: due yesterday, it is under «in ritardo» and counted;
    completed, it is gone from both."""
    me = _me(logged_in)
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    created = logged_in.post(
        "/api/activities",
        json={"title": "Sollecitare Rossi", "due_date": yesterday, "assigned_to": me},
    )
    assert created.status_code == 201, created.text

    before = logged_in.get("/api/activities/today", params={"assigned_to": me}).json()
    assert before["overdue"]["total"] == 1
    assert [a["title"] for a in before["overdue"]["items"]] == ["Sollecitare Rossi"]

    done = logged_in.post(f"/api/activities/{created.json()['id']}/complete")
    assert done.status_code == 200, done.text

    after = logged_in.get("/api/activities/today", params={"assigned_to": me}).json()
    assert after["overdue"] == {"total": 0, "items": []}


# --- «mine and nobody's» ---------------------------------------------------------------


def test_include_unassigned_adds_the_unassigned_to_one_persons_list(
    logged_in: TestClient,
) -> None:
    me = _me(logged_in)
    logged_in.post("/api/activities", json={"title": "Mia", "assigned_to": me})
    logged_in.post("/api/activities", json={"title": "Di nessuno"})

    def titles(path: str, **query: object) -> set[str]:
        body = logged_in.get(path, params=query).json()
        return {a["title"] for a in body["items"]}

    assert titles("/api/activities", assigned_to=me) == {"Mia"}
    assert titles("/api/activities", assigned_to=me, include_unassigned="true") == {
        "Mia",
        "Di nessuno",
    }
    # Without an assignee the flag has nothing to attach to: the whole space.
    assert titles("/api/activities", include_unassigned="true") == {"Mia", "Di nessuno"}


def test_today_honours_include_unassigned(logged_in: TestClient) -> None:
    me = _me(logged_in)
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    logged_in.post(
        "/api/activities", json={"title": "Mia", "due_date": yesterday, "assigned_to": me}
    )
    logged_in.post("/api/activities", json={"title": "Di nessuno", "due_date": yesterday})

    solo = logged_in.get("/api/activities/today", params={"assigned_to": me}).json()
    also = logged_in.get(
        "/api/activities/today", params={"assigned_to": me, "include_unassigned": "true"}
    ).json()

    assert [a["title"] for a in solo["overdue"]["items"]] == ["Mia"]
    assert {a["title"] for a in also["overdue"]["items"]} == {"Mia", "Di nessuno"}
