"""REB-732 over HTTP: the reminder settings in two layers, acknowledge and snooze."""

from uuid import uuid4

from fastapi.testclient import TestClient

KEY = f"attivita_overdue:{uuid4()}"


def test_the_catalogue_lists_the_types_with_their_default_ladders(logged_in: TestClient) -> None:
    response = logged_in.get("/api/reminders/types")

    assert response.status_code == 200
    by_key = {item["key"]: item for item in response.json()}
    assert by_key["attivita_overdue"]["ladder"] == {"serious": 1, "critical": 14}
    assert by_key["timer_running_long"]["unit"] == "hours"
    assert by_key["attivita_due"]["direction"] == "until"


def test_an_admin_sets_a_default_and_a_member_overrides_it_for_themselves(
    logged_in: TestClient,
) -> None:
    default = logged_in.patch(
        "/api/reminders/preferences/space/attivita_overdue", json={"channels": ["email"]}
    )
    assert default.status_code == 200, default.text
    mine = logged_in.patch(
        "/api/reminders/preferences/me/attivita_overdue", json={"enabled": False}
    )
    assert mine.status_code == 200, mine.text

    read = logged_in.get("/api/reminders/preferences").json()
    effective = {item["type"]: item for item in read["effective"]}["attivita_overdue"]
    assert effective["enabled"] is False
    assert effective["channels"] == ["in_app", "email"]
    assert [row["scope"] for row in read["space"]] == ["space"]
    assert [row["scope"] for row in read["mine"]] == ["me"]

    back = logged_in.delete("/api/reminders/preferences/me/attivita_overdue")
    assert back.status_code == 204
    again = logged_in.get("/api/reminders/preferences").json()
    assert again["mine"] == []


def test_a_threshold_save_that_breaks_the_order_is_a_422_in_italian(logged_in: TestClient) -> None:
    response = logged_in.patch(
        "/api/reminders/preferences/me/attivita_overdue",
        json={"ladder": {"warning": 9, "serious": 3}},
    )

    assert response.status_code == 422
    assert "più alta" in response.text


def test_a_member_cannot_set_the_spaces_defaults(collaborator_client: TestClient) -> None:
    response = collaborator_client.patch(
        "/api/reminders/preferences/space/attivita_overdue", json={"enabled": False}
    )

    assert response.status_code == 403


def test_the_last_override_cleared_answers_null(collaborator_client: TestClient) -> None:
    collaborator_client.patch(
        "/api/reminders/preferences/me/deal_stale", json={"urgent_from": "serious"}
    )

    cleared = collaborator_client.patch(
        "/api/reminders/preferences/me/deal_stale", json={"urgent_from": None}
    )

    assert cleared.status_code == 200
    assert cleared.json() is None


def test_acknowledge_and_snooze_are_the_callers_own(
    readonly_client: TestClient,
) -> None:
    acknowledged = readonly_client.post(
        "/api/reminders/acknowledge", json={"key": KEY, "severity": "serious"}
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["acknowledged_severity"] == "serious"

    snoozed = readonly_client.post(
        "/api/reminders/snooze", json={"key": KEY, "severity": "critical", "until": "tomorrow"}
    )
    assert snoozed.status_code == 200, snoozed.text
    assert snoozed.json()["snoozed_severity"] == "critical"
    assert snoozed.json()["snoozed_until"] is not None

    state = readonly_client.get("/api/reminders/state").json()
    assert [item["key"] for item in state] == [KEY]


def test_a_snooze_is_never_a_free_date(collaborator_client: TestClient) -> None:
    response = collaborator_client.post(
        "/api/reminders/snooze", json={"key": KEY, "severity": "serious", "until": "2026-12-01"}
    )

    assert response.status_code == 422


def test_a_key_of_a_type_that_does_not_exist_is_refused(collaborator_client: TestClient) -> None:
    response = collaborator_client.post(
        "/api/reminders/acknowledge", json={"key": "nope:1", "severity": "serious"}
    )

    assert response.status_code == 422


def _today(logged_in: TestClient) -> str:
    from pigrocrm.core.db.clock import today_local

    return today_local().isoformat()


def test_the_reminders_of_a_person_are_listed_and_closing_one_changes_its_status(
    logged_in: TestClient,
) -> None:
    me = logged_in.get("/api/auth/me").json()["id"]
    created = logged_in.post(
        "/api/activities",
        json={"titolo": "Mandare il preventivo", "scadenza": _today(logged_in), "assegnata_a": me},
    )
    assert created.status_code == 201, created.text

    listed = logged_in.get("/api/reminders")
    assert listed.status_code == 200, listed.text
    (reminder,) = listed.json()
    assert reminder["type"] == "attivita_due"
    assert reminder["severity"] == "warning"
    assert reminder["title"] == "Attività in scadenza: Mandare il preventivo"
    assert reminder["detail"] == "Scade oggi"
    assert reminder["status"] == "open"
    assert reminder["entity"] == "attivita" and reminder["entity_id"] == created.json()["id"]

    logged_in.post(
        "/api/reminders/acknowledge", json={"key": reminder["key"], "severity": "warning"}
    )
    again = logged_in.get("/api/reminders").json()
    assert [item["status"] for item in again] == ["acknowledged"]

    done = logged_in.post(f"/api/activities/{created.json()['id']}/complete")
    assert done.status_code == 200, done.text
    assert logged_in.get("/api/reminders").json() == [], "the cause is fixed, the reminder is gone"


def test_nothing_is_listed_for_a_space_with_nothing_due(logged_in: TestClient) -> None:
    assert logged_in.get("/api/reminders").json() == []
