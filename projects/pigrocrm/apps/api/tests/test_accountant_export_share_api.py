"""Handing an export over, over REST (REB-1047): an admin makes a share link, opens it signed out
and downloads the export, sees the opening in the audit, revokes it and is refused; and writes an
export to a fake Drive. Who may, what a link says when it ends, and the limits on the public door
are proven here; the rules of the link are in the core test `test_accountant_export_sharing.py`.
"""

import datetime as dt
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# The in-memory Drive lives with the core tests, a pytest root of its own: reached by path, as
# `test_documents_api.py` does, rather than copied.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.fake_drive import FakeDrive  # noqa: E402

from pigrocrm.core.accountant_export.models import ExportShareLink  # noqa: E402
from pigrocrm.core.accountant_export.parts import Period  # noqa: E402
from pigrocrm.core.accountant_export.service import AccountantExportService  # noqa: E402
from pigrocrm.core.accountant_export.sharing import ExportShareService  # noqa: E402
from pigrocrm.core.actor import Actor  # noqa: E402
from pigrocrm.core.auth.schemas import UserCreate, UserUpdate  # noqa: E402
from pigrocrm.core.auth.service import UserService  # noqa: E402
from pigrocrm.core.config import Settings, get_settings  # noqa: E402
from pigrocrm.core.legal_entities.models import LegalEntity  # noqa: E402
from pigrocrm.core.legal_entities.repository import LegalEntityRepository  # noqa: E402
from pigrocrm.core.mail import RecordingSender  # noqa: E402
from pigrocrm.core.storage import GDriveStorage, LocalFileStorage  # noqa: E402
from pigrocrm_api.deps import get_storage  # noqa: E402
from pigrocrm_api.sessions import get_sender  # noqa: E402

QUARTER = {"date_from": "2026-01-01", "date_to": "2026-03-31"}
ADDRESS = "203.0.113.9"
ADMIN_PASSWORD = "supersegreta1"
SERVICE_ACCOUNT_JSON = (
    '{"type":"service_account","client_email":"pigro@example.iam.gserviceaccount.com",'
    '"private_key":"-----BEGIN PRIVATE KEY-----\\nFAKE\\n-----END PRIVATE KEY-----\\n",'
    '"token_uri":"https://oauth2.googleapis.com/token"}'
)
DOOR = "/api/accountant-export/shared"


@pytest.fixture
def public_settings(logged_in: TestClient) -> Iterator[None]:
    """The installation's address, without which a link has none to carry."""
    logged_in.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[call-arg]
        _env_file=None, public_url="https://pigro.example/studio"
    )
    yield


@pytest.fixture
def visitor(logged_in: TestClient) -> TestClient:
    """A browser with no session at all: nothing but the address of the link."""
    return TestClient(logged_in.app, base_url="https://testserver")


def _export(client: TestClient, **extra: object) -> dict:
    response = client.post("/api/accountant-export/exports", json={**QUARTER, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _link(client: TestClient, export: dict, **body: object) -> dict:
    response = client.post(f"/api/accountant-export/exports/{export['id']}/share-links", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _token(link: dict) -> str:
    return link["url"].split("?t=", 1)[1]


def test_an_admin_makes_a_link_a_visitor_downloads_it_and_the_admin_sees_the_opening(
    logged_in: TestClient, visitor: TestClient, public_settings: None
) -> None:
    export = _export(logged_in)
    link = _link(logged_in, export)
    token = _token(link)

    assert link["state"] == "active" and link["download_count"] == 0
    assert link["url"].startswith("https://pigro.example/studio/app/shared-export?t=")
    seen = visitor.post(f"{DOOR}/describe", json={"token": token})
    assert seen.status_code == 200, seen.text
    assert seen.json()["parts"][0]["filename"] == export["parts"][0]["filename"]
    assert seen.json()["period_from"] == "2026-01-01"
    assert seen.headers["cache-control"] == "no-store"
    assert seen.headers["referrer-policy"] == "no-referrer"
    # Reading what it opens is not an opening.
    assert (
        logged_in.get(f"/api/accountant-export/share-links/{link['id']}").json()["openings"] == []
    )

    file = visitor.post(
        f"{DOOR}/download", json={"token": token, "part": 1}, headers={"X-Real-IP": ADDRESS}
    )

    assert file.status_code == 200, file.text
    assert file.headers["content-type"] == "application/zip"
    assert file.headers["cache-control"] == "no-store"
    assert file.headers["referrer-policy"] == "no-referrer"
    assert file.headers["content-disposition"].startswith(
        "attachment; filename*=UTF-8''commercialista-"
    )
    assert file.content == logged_in.get(export["parts"][0]["download_url"]).content
    read = logged_in.get(f"/api/accountant-export/share-links/{link['id']}").json()
    assert read["download_count"] == 1
    assert [(o["address"], o["part"]) for o in read["openings"]] == [(ADDRESS, 1)]
    assert "url" not in read
    listed = logged_in.get("/api/accountant-export/share-links").json()
    assert listed["total_count"] == 1 and listed["items"][0]["download_count"] == 1
    assert listed["items"][0]["openings"] == []
    assert _token(link) not in str(listed) and _token(link) not in str(read)


def test_a_revoked_link_is_refused_by_the_door_and_stays_in_the_list(
    logged_in: TestClient, visitor: TestClient, public_settings: None
) -> None:
    link = _link(logged_in, _export(logged_in))
    token = _token(link)
    assert visitor.post(f"{DOOR}/download", json={"token": token}).status_code == 200

    revoked = logged_in.post(f"/api/accountant-export/share-links/{link['id']}/revoke")

    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
    for path in ("describe", "download"):
        refused = visitor.post(f"{DOOR}/{path}", json={"token": token})
        assert refused.status_code == 410
        assert refused.json()["code"] == "export_link_revoked"
        assert "revocato" in refused.json()["detail"]
    listed = logged_in.get("/api/accountant-export/share-links").json()["items"]
    assert [(item["state"], item["download_count"]) for item in listed] == [("revoked", 1)]


def test_an_expired_link_is_refused_with_its_own_word(
    logged_in: TestClient, visitor: TestClient, api_session: Session, public_settings: None
) -> None:
    link = _link(logged_in, _export(logged_in), days=1)
    row = api_session.get(ExportShareLink, link["id"])
    assert row is not None
    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    api_session.flush()

    refused = visitor.post(f"{DOOR}/download", json={"token": _token(link)})

    assert refused.status_code == 410 and refused.json()["code"] == "export_link_expired"
    assert (
        logged_in.get("/api/accountant-export/share-links").json()["items"][0]["state"] == "expired"
    )


def test_a_token_nobody_was_given_is_a_not_found(visitor: TestClient) -> None:
    refused = visitor.post(f"{DOOR}/describe", json={"token": "x" * 43})

    assert refused.status_code == 404 and refused.json()["code"] == "export_link_unknown"
    assert visitor.post(f"{DOOR}/describe", json={}).status_code == 422
    assert visitor.post(f"{DOOR}/describe", json={"token": "x" * 500}).status_code == 422


def test_the_token_does_not_travel_in_the_address_of_the_door(
    visitor: TestClient, logged_in: TestClient, public_settings: None
) -> None:
    link = _link(logged_in, _export(logged_in))

    # The one way in is a POST body: a GET with the token in the path or the query finds nothing.
    assert visitor.get(f"{DOOR}/{_token(link)}").status_code == 404
    assert visitor.get(f"{DOOR}/describe", params={"token": _token(link)}).status_code == 405


def test_the_lifetime_defaults_to_a_week_and_never_passes_thirty_days(
    logged_in: TestClient, public_settings: None
) -> None:
    export = _export(logged_in)
    default = _link(logged_in, export)
    path = f"/api/accountant-export/exports/{export['id']}/share-links"

    lived = dt.datetime.fromisoformat(default["expires_at"]) - dt.datetime.fromisoformat(
        default["created_at"]
    )
    assert dt.timedelta(days=6, hours=23) < lived < dt.timedelta(days=7, hours=1)
    assert logged_in.post(path, json={"days": 30}).status_code == 201
    assert logged_in.post(path, json={"days": 31}).status_code == 422
    assert logged_in.post(path, json={"days": 0}).status_code == 422


def test_without_the_public_address_there_is_no_link_to_give(
    logged_in: TestClient,
) -> None:
    export = _export(logged_in)

    refused = logged_in.post(f"/api/accountant-export/exports/{export['id']}/share-links", json={})

    assert refused.status_code == 422 and refused.json()["field"] == "public_url"
    assert logged_in.get("/api/accountant-export/share-links").json()["total_count"] == 0


def test_the_door_throttles_a_client_that_keeps_guessing(visitor: TestClient) -> None:
    answers = [
        visitor.post(f"{DOOR}/describe", json={"token": f"{n:043d}"}).status_code for n in range(20)
    ]

    assert set(answers[:10]) == {404}
    assert 429 in answers


def test_a_collaborator_and_a_reader_reach_no_link_and_no_drive(
    client: TestClient, api_session: Session, tmp_path: Path
) -> None:
    # One client object serves every role fixture and the last login wins its cookies, so the
    # export and its link are made by the services and each role logs in in turn.
    UserService(api_session).create(
        UserCreate(email="c@pigro.it", password=ADMIN_PASSWORD, name="C", role="collaborator"),
        Actor.system(),
    )
    UserService(api_session).create(
        UserCreate(email="r@pigro.it", password=ADMIN_PASSWORD, name="R", role="readonly"),
        Actor.system(),
    )
    export = AccountantExportService(
        api_session,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    ).build_accountant_export(Period(dt.date(2026, 1, 1), dt.date(2026, 3, 31)), Actor.system())
    link = ExportShareService(
        api_session,
        Settings(_env_file=None, public_url="https://pigro.example"),  # type: ignore[call-arg]
    ).create_export_share_link(export.export.id, Actor.system())
    for email in ("c@pigro.it", "r@pigro.it"):
        assert (
            client.post(
                "/api/auth/login", json={"email": email, "password": ADMIN_PASSWORD}
            ).status_code
            == 200
        )
        calls = [
            client.post(f"/api/accountant-export/exports/{export.export.id}/share-links", json={}),
            client.get("/api/accountant-export/share-links"),
            client.get(f"/api/accountant-export/share-links/{link.id}"),
            client.post(f"/api/accountant-export/share-links/{link.id}/revoke"),
            client.get("/api/accountant-export/drive"),
            client.get(f"/api/accountant-export/exports/{export.export.id}/drive-writes"),
            client.post(f"/api/accountant-export/exports/{export.export.id}/drive-writes"),
        ]
        assert {call.status_code for call in calls} == {403}
        assert {call.json()["code"] for call in calls} == {"permission_denied"}


def test_a_visitor_with_no_session_reaches_nothing_but_the_door(client: TestClient) -> None:
    anon = TestClient(client.app, base_url="https://testserver")
    assert anon.get("/api/accountant-export/share-links").status_code == 401
    assert anon.post("/api/accountant-export/share-links/x/revoke").status_code in (401, 422)
    assert anon.get("/api/accountant-export/drive").status_code == 401


def test_an_admin_outside_the_scope_of_the_entity_cannot_make_or_see_its_link(
    logged_in: TestClient,
    client: TestClient,
    api_session: Session,
    public_settings: None,
) -> None:
    default = LegalEntityRepository(api_session).default()
    assert default is not None
    other = LegalEntity(name="Rebase Ltd", legal_name="Rebase Ltd", country="GB", is_default=False)
    api_session.add(other)
    api_session.flush()
    mine = _export(logged_in, legal_entity_id=str(default.id))
    theirs = _export(logged_in, legal_entity_id=str(other.id))
    everything = _export(logged_in, all_entities=True)
    foreign = _link(logged_in, theirs)
    own = _link(logged_in, mine)
    service = UserService(api_session)
    scoped = service.create(
        UserCreate(email="scoped@pigro.it", password="supersegreta1", name="Scoped", role="admin"),
        Actor.system(),
    )
    service.update(scoped.id, UserUpdate(legal_entities=[default.id]), Actor.system())
    scoped_client = TestClient(client.app, base_url="https://testserver")
    assert (
        scoped_client.post(
            "/api/auth/login", json={"email": "scoped@pigro.it", "password": "supersegreta1"}
        ).status_code
        == 200
    )

    assert _link(scoped_client, mine)["state"] == "active"
    refused = [
        scoped_client.post(
            f"/api/accountant-export/exports/{theirs['id']}/share-links", json={}
        ).status_code,
        scoped_client.post(
            f"/api/accountant-export/exports/{everything['id']}/share-links", json={}
        ).status_code,
        scoped_client.get(f"/api/accountant-export/share-links/{foreign['id']}").status_code,
        scoped_client.post(
            f"/api/accountant-export/share-links/{foreign['id']}/revoke"
        ).status_code,
    ]
    assert refused == [404, 403, 404, 404]
    ids = {
        item["id"]
        for item in scoped_client.get("/api/accountant-export/share-links").json()["items"]
    }
    assert own["id"] in ids and foreign["id"] not in ids


def test_a_link_for_an_export_that_does_not_exist_is_not_found(
    logged_in: TestClient, public_settings: None
) -> None:
    unknown = "00000000-0000-7000-8000-00000000dead"

    made = logged_in.post(f"/api/accountant-export/exports/{unknown}/share-links", json={})
    revoked = logged_in.post(f"/api/accountant-export/share-links/{unknown}/revoke")

    assert (made.status_code, revoked.status_code) == (404, 404)


# --- Drive --------------------------------------------------------------------------


def test_with_local_storage_the_page_is_told_there_is_no_drive_and_the_write_is_refused(
    logged_in: TestClient,
) -> None:
    export = _export(logged_in)

    status = logged_in.get("/api/accountant-export/drive")
    refused = logged_in.post(f"/api/accountant-export/exports/{export['id']}/drive-writes")

    assert status.json() == {"available": False, "reason": "local_storage"}
    assert refused.status_code == 422 and "non su Google Drive" in refused.json()["detail"]
    assert logged_in.get(f"/api/accountant-export/exports/{export['id']}/drive-writes").json() == []


def test_an_admin_writes_an_export_to_the_drive_folder_and_reads_where_it_went(
    logged_in: TestClient,
) -> None:
    drive = FakeDrive()
    storage = GDriveStorage.from_service_account(
        service_account_json=SERVICE_ACCOUNT_JSON,
        root_folder_id=drive.root_id,
        http=drive,
        sign_assertion=lambda claims: "assertion",
    )
    logged_in.app.dependency_overrides[get_storage] = lambda: storage
    logged_in.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[call-arg]
        _env_file=None,
        storage_backend="gdrive",
        gdrive_service_account_json=SERVICE_ACCOUNT_JSON,
        gdrive_root_folder_id=drive.root_id,
    )
    export = _export(logged_in)

    assert logged_in.get("/api/accountant-export/drive").json() == {
        "available": True,
        "reason": None,
    }
    written = logged_in.post(f"/api/accountant-export/exports/{export['id']}/drive-writes")

    assert written.status_code == 201, written.text
    body = written.json()
    assert body["prefix"].startswith("commercialista/") and "/2026-t1/" in body["prefix"]
    assert body["files"] == [export["parts"][0]["filename"]]
    copy = storage.get(f"{body['prefix']}/{body['files'][0]}")
    assert (
        copy.startswith(b"PK") and copy == logged_in.get(export["parts"][0]["download_url"]).content
    )
    listed = logged_in.get(f"/api/accountant-export/exports/{export['id']}/drive-writes").json()
    assert [item["id"] for item in listed] == [body["id"]]


def test_an_export_with_pay_is_shared_only_by_a_link_that_asks_a_code(
    logged_in: TestClient, visitor: TestClient, public_settings: None
) -> None:
    sender = RecordingSender()
    logged_in.app.dependency_overrides[get_sender] = lambda: sender  # type: ignore[attr-defined]
    export = _export(logged_in, kind="payroll_changes")
    refused = logged_in.post(f"/api/accountant-export/exports/{export['id']}/share-links", json={})
    assert refused.status_code == 422 and "con codice" in refused.text
    link = _link(logged_in, export, recipient_email="paghe@studio-esempio.it")
    token = _token(link)
    assert link["recipient_email"] == "paghe@studio-esempio.it"

    seen = visitor.post(f"{DOOR}/describe", json={"token": token}).json()
    assert seen["requires_code"] and seen["unlocked"] is False and seen["parts"] == []
    locked = visitor.post(f"{DOOR}/download", json={"token": token, "part": 1})
    assert locked.status_code == 403 and locked.json()["code"] == "export_code_required"

    sent = visitor.post(f"{DOOR}/code", json={"token": token})
    assert sent.status_code == 200 and sent.json() == {"sent_to": "p…@studio-esempio.it"}
    assert token not in sender.sent[0].text
    again = visitor.post(f"{DOOR}/code", json={"token": token})
    assert again.status_code == 429 and again.json()["code"] == "export_code_throttled"
    wrong = visitor.post(f"{DOOR}/verify", json={"token": token, "code": "abcdef"})
    assert wrong.status_code == 403 and wrong.json()["code"] == "export_code_invalid"

    code = sender.sent[0].text.split("\n\n")[2].strip()
    access = visitor.post(f"{DOOR}/verify", json={"token": token, "code": code}).json()["access"]
    opened = visitor.post(f"{DOOR}/describe", json={"token": token, "access": access}).json()
    assert opened["unlocked"] and len(opened["parts"]) == 1
    file = visitor.post(f"{DOOR}/download", json={"token": token, "part": 1, "access": access})
    assert file.status_code == 200 and file.headers["content-type"] == "application/zip"
    assert file.headers["cache-control"] == "no-store"


def test_a_code_cannot_be_mailed_by_an_installation_that_cannot_mail(
    logged_in: TestClient, visitor: TestClient, public_settings: None
) -> None:
    logged_in.app.dependency_overrides[get_sender] = lambda: None  # type: ignore[attr-defined]
    link = _link(
        logged_in,
        _export(logged_in, kind="payroll_changes"),
        recipient_email="paghe@studio-esempio.it",
    )

    response = visitor.post(f"{DOOR}/code", json={"token": _token(link)})

    assert response.status_code == 503
