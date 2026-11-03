"""The notes thread over HTTP (REB-879): `GET/POST /api/{deals|customers|people}/{id}/notes`,
`PATCH` and `DELETE /api/notes/{id}`, and the three records without a `note` of their own."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.pipeline.service import PipelineService

PASSWORD = "supersegreta1"
COLLECTIONS = ["deals", "customers", "people"]


@pytest.fixture(autouse=True)
def _stages(api_session: Session) -> None:
    PipelineService(api_session).seed_defaults(Actor(id=None, type="system", role="admin"))
    api_session.commit()


def _records(client: TestClient) -> dict[str, str]:
    customer = client.post("/api/customers", json={"legal_name": "ACME Srl"})
    deal = client.post("/api/deals", json={"name": "Sito", "customer_id": customer.json()["id"]})
    person = client.post("/api/people", json={"first_name": "Anna"})
    for response in (customer, deal, person):
        assert response.status_code == 201, response.text
    return {
        "customers": customer.json()["id"],
        "deals": deal.json()["id"],
        "people": person.json()["id"],
    }


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_a_note_is_added_listed_edited_and_deleted(logged_in: TestClient, collection: str) -> None:
    record = _records(logged_in)[collection]
    me = logged_in.get("/api/auth/me").json()

    added = logged_in.post(f"/api/{collection}/{record}/notes", json={"text": "  Prima nota  "})
    assert added.status_code == 201, added.text
    note = added.json()
    assert note["text"] == "Prima nota"
    assert (note["author"], note["author_id"], note["source"]) == (me["name"], me["id"], "web")
    assert note["editable"] is True and note["edited_at"] is None

    second = logged_in.post(f"/api/{collection}/{record}/notes", json={"text": "Seconda"}).json()
    listed = logged_in.get(f"/api/{collection}/{record}/notes")
    assert [n["text"] for n in listed.json()] == ["Seconda", "Prima nota"]

    edited = logged_in.patch(f"/api/notes/{note['id']}", json={"text": "Corretta"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["text"] == "Corretta"
    assert edited.json()["edited_by"] == me["name"] and edited.json()["edited_at"]

    deleted = logged_in.delete(f"/api/notes/{second['id']}")
    assert deleted.status_code == 204
    assert [n["text"] for n in logged_in.get(f"/api/{collection}/{record}/notes").json()] == [
        "Corretta"
    ]
    assert logged_in.patch(f"/api/notes/{second['id']}", json={"text": "x"}).status_code == 404


def test_the_body_carries_the_text_only(logged_in: TestClient) -> None:
    record = _records(logged_in)["deals"]
    for extra in ({"author": "Marco"}, {"source": "mcp"}, {"created_at": "2020-01-01T00:00:00Z"}):
        response = logged_in.post(f"/api/deals/{record}/notes", json={"text": "x", **extra})
        assert response.status_code == 422, extra
    for blank in ("", "   "):
        assert logged_in.post(f"/api/deals/{record}/notes", json={"text": blank}).status_code == 422


def test_a_note_on_a_record_that_is_not_there_is_a_404(logged_in: TestClient) -> None:
    ghost = "00000000-0000-0000-0000-000000000000"
    assert logged_in.get(f"/api/deals/{ghost}/notes").status_code == 404
    assert logged_in.post(f"/api/people/{ghost}/notes", json={"text": "x"}).status_code == 404
    assert logged_in.delete(f"/api/notes/{ghost}").status_code == 404
    assert logged_in.get(f"/api/contracts/{ghost}/notes").status_code == 422


def test_a_collaborator_reads_the_admins_note_and_cannot_change_it(
    logged_in: TestClient, api_session: Session
) -> None:
    record = _records(logged_in)["customers"]
    note = logged_in.post(f"/api/customers/{record}/notes", json={"text": "Dell'admin"}).json()
    UserService(api_session).create(
        UserCreate(
            email="collega@pigro.it", password=PASSWORD, name="Collega", role="collaborator"
        ),
        Actor.system(),
    )
    logged = logged_in.post(
        "/api/auth/login", json={"email": "collega@pigro.it", "password": PASSWORD}
    )
    assert logged.status_code == 200, logged.text

    seen = logged_in.get(f"/api/customers/{record}/notes").json()
    assert [(n["text"], n["editable"]) for n in seen] == [("Dell'admin", False)]
    refused = logged_in.patch(f"/api/notes/{note['id']}", json={"text": "Mia"})
    assert refused.status_code == 403 and refused.json()["code"] == "permission_denied"
    assert logged_in.delete(f"/api/notes/{note['id']}").status_code == 403
    own = logged_in.post(f"/api/customers/{record}/notes", json={"text": "Del collega"}).json()
    assert own["editable"] is True and own["author"] == "Collega"
    assert logged_in.patch(f"/api/notes/{own['id']}", json={"text": "Cambiata"}).status_code == 200


def test_create_takes_a_note_as_the_first_note_and_the_records_no_longer_carry_one(
    logged_in: TestClient,
) -> None:
    customer = logged_in.post(
        "/api/customers", json={"legal_name": "Beta", "note": "Cliente da una mail"}
    )
    deal = logged_in.post(
        "/api/deals",
        json={"name": "Sito", "customer_id": customer.json()["id"], "note": "Primo contatto"},
    )
    person = logged_in.post("/api/people", json={"first_name": "Anna", "note": "Referente"})
    for response, collection in ((customer, "customers"), (deal, "deals"), (person, "people")):
        assert response.status_code == 201, response.text
        assert "note" not in response.json()
        [note] = logged_in.get(f"/api/{collection}/{response.json()['id']}/notes").json()
        assert note["source"] == "web" and note["author"] == "Admin"
        assert "note" not in logged_in.get(f"/api/{collection}/{response.json()['id']}").json()


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_an_update_refuses_a_note(logged_in: TestClient, collection: str) -> None:
    record = _records(logged_in)[collection]
    response = logged_in.patch(f"/api/{collection}/{record}", json={"note": "x"})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_the_timeline_of_the_record_says_who_wrote_edited_and_deleted_a_note(
    logged_in: TestClient, collection: str
) -> None:
    record = _records(logged_in)[collection]
    note = logged_in.post(f"/api/{collection}/{record}/notes", json={"text": "Prima"}).json()
    logged_in.patch(f"/api/notes/{note['id']}", json={"text": "Dopo"})
    logged_in.delete(f"/api/notes/{note['id']}")

    timeline = logged_in.get(f"/api/{collection}/{record}/timeline").json()

    kinds = {e["kind"]: e for e in timeline if e["kind"].startswith("note_")}
    assert set(kinds) == {"note_added", "note_edited", "note_deleted"}
    assert all(e["actor_name"] == "Admin" and e["actor_type"] == "user" for e in kinds.values())


def test_the_revisions_of_a_note_keep_each_text_it_had_whole(logged_in: TestClient) -> None:
    record = _records(logged_in)["deals"]
    long_text = "Appunti lunghi sulla trattativa. " * 150  # past the timeline's 2000-char cut
    note = logged_in.post(f"/api/deals/{record}/notes", json={"text": long_text}).json()
    logged_in.patch(f"/api/notes/{note['id']}", json={"text": "Breve."})
    logged_in.patch(f"/api/notes/{note['id']}", json={"text": "Breve, rivista."})

    response = logged_in.get(f"/api/notes/{note['id']}/revisions")

    assert response.status_code == 200, response.text
    revisions = response.json()
    assert [r["text"] for r in revisions] == ["Breve.", long_text.strip()]
    assert all(r["edited_by"] == "Admin" and r["source"] == "web" for r in revisions)
    timeline = logged_in.get(f"/api/deals/{record}/timeline").json()
    edited = [e for e in timeline if e["kind"] == "note_edited"]
    assert edited and all("before" not in e["payload"] for e in edited)
    ghost = "00000000-0000-0000-0000-000000000000"
    assert logged_in.get(f"/api/notes/{ghost}/revisions").status_code == 404


def test_a_custom_field_cannot_be_called_note(logged_in: TestClient) -> None:
    for entity in ("deal", "customer", "person"):
        response = logged_in.post(
            "/api/field-definitions",
            json={"entity_type": entity, "key": "note", "label": "Note", "field_type": "text"},
        )
        assert response.status_code == 422, (entity, response.text)
