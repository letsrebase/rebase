"""Handing an export over: the share link and the copy written to Drive (REB-1047, accountant
export design § 3 and § 5).

What these hold the code to: a link is made by an administrator whose scope covers the export (or
an agent with `full_access`), is stored only as the hash of a 256-bit token, lives 7 days by
default and at most 30; opened with no account it hands the ZIP out, counts the opening and writes
an audit row with the moment and the address; a revoked, an expired and an unknown link are
refused, each with its own word; and the Drive copy lands in the space's Drive under the folder
the design names, for a person alone.
"""

import datetime as dt
from uuid import UUID, uuid4

import pytest
from fakes.fake_drive import FakeDrive
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.accountant_export.drive_delivery import (
    ExportDriveService,
    ExportDriveStatus,
    period_folder,
)
from pigrocrm.core.accountant_export.models import (
    AccountantExportRow,
    ExportDriveWrite,
    ExportShareCode,
    ExportShareLink,
    ExportShareOpening,
)
from pigrocrm.core.accountant_export.parts import Period
from pigrocrm.core.accountant_export.retention import ExportRetention
from pigrocrm.core.accountant_export.service import AccountantExportService
from pigrocrm.core.accountant_export.sharing import (
    CODE_MAX_FAILURES,
    ExportCodeInvalid,
    ExportCodeMailFailed,
    ExportCodeRequired,
    ExportCodeThrottled,
    ExportLinkExpired,
    ExportLinkRevoked,
    ExportLinkUnknown,
    ExportShareDoor,
    ExportShareService,
    hash_token,
)
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import (
    AgentForbidden,
    NotFound,
    PermissionDenied,
    ScopedAdmin,
    ValidationFailed,
)
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.storage import DocumentStorage, GDriveStorage
from pigrocrm.core.storage.errors import StorageNotConfigured
from pigrocrm.core.storage.local import LocalFileStorage

D = dt.date
SYSTEM = Actor(id=None, type="system", role="admin")
ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
READONLY = Actor(id=uuid4(), type="user", role="readonly")
AGENT = Actor(id=uuid4(), type="mcp", role="admin")
FULL_ACCESS_AGENT = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
Q3 = Period(D(2026, 7, 1), D(2026, 9, 30))
NOW = dt.datetime(2026, 11, 10, 10, 40, 12, tzinfo=dt.UTC)
SETTINGS = Settings(_env_file=None, public_url="https://pigro.example/studio")  # type: ignore[call-arg]
ADDRESS = "203.0.113.7"

SERVICE_ACCOUNT_JSON = (
    '{"type":"service_account","client_email":"pigro@example.iam.gserviceaccount.com",'
    '"private_key":"-----BEGIN PRIVATE KEY-----\\nFAKE\\n-----END PRIVATE KEY-----\\n",'
    '"token_uri":"https://oauth2.googleapis.com/token"}'
)


def _build(session: Session, storage: DocumentStorage, **kwargs: object) -> UUID:
    stored = AccountantExportService(session, storage, SETTINGS).build_accountant_export(
        Q3,
        SYSTEM,
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )
    return stored.export.id


@pytest.fixture
def export_id(db_session: Session, local_storage: LocalFileStorage) -> UUID:
    return _build(db_session, local_storage)


def _links(session: Session) -> ExportShareService:
    return ExportShareService(session, SETTINGS)


def _door(session: Session, storage: DocumentStorage) -> ExportShareDoor:
    return ExportShareDoor(session, storage, SETTINGS)


def _token(url: str) -> str:
    return url.split("?t=", 1)[1]


def _second_entity(session: Session) -> LegalEntity:
    entity = LegalEntity(name="Rebase Ltd", legal_name="Rebase Ltd", country="GB", is_default=False)
    session.add(entity)
    session.flush()
    return entity


# --- the link ------------------------------------------------------------------------


def test_a_link_is_made_with_a_week_to_live_and_stored_only_as_a_hash(
    db_session: Session, export_id: UUID
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)

    assert created.state == "active" and created.download_count == 0
    assert created.expires_at == NOW + dt.timedelta(days=7)
    assert created.url.startswith("https://pigro.example/studio/app/shared-export?t=")
    raw = _token(created.url)
    # 32 random bytes, urlsafe: 256 bits, 43 characters.
    assert len(raw) == 43
    row = db_session.get(ExportShareLink, created.id)
    assert row is not None
    assert row.token_hash == hash_token(raw) and row.token_hash != raw
    assert raw not in repr(row.__dict__)
    assert "url" not in _links(db_session).get_export_share_link(created.id, ADMIN).model_dump()


def test_two_links_never_share_a_token(db_session: Session, export_id: UUID) -> None:
    first = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)
    second = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)

    assert _token(first.url) != _token(second.url)


@pytest.mark.parametrize("days", [1, 14, 30])
def test_a_link_lives_from_one_to_thirty_days(
    db_session: Session, export_id: UUID, days: int
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, days=days, now=NOW)

    assert created.expires_at == NOW + dt.timedelta(days=days)


@pytest.mark.parametrize("days", [0, -1, 31, 365])
def test_a_link_cannot_outlive_thirty_days_or_be_over_already(
    db_session: Session, export_id: UUID, days: int
) -> None:
    with pytest.raises(ValidationFailed) as refused:
        _links(db_session).create_export_share_link(export_id, ADMIN, days=days, now=NOW)

    assert refused.value.details["field"] == "days"


def test_a_link_needs_the_public_address_of_the_installation(
    db_session: Session, export_id: UUID
) -> None:
    bare = ExportShareService(db_session, Settings(_env_file=None, public_url=""))  # type: ignore[call-arg]

    with pytest.raises(ValidationFailed) as refused:
        bare.create_export_share_link(export_id, ADMIN, now=NOW)

    assert refused.value.details["field"] == "public_url"
    assert db_session.scalars(select(ExportShareLink)).all() == []


def test_making_and_ending_a_link_leave_a_trace_without_the_address(
    db_session: Session, export_id: UUID
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)
    _links(db_session).revoke_export_share_link(created.id, ADMIN, now=NOW)

    entries = db_session.scalars(
        select(Activity).where(Activity.entity_id == export_id).order_by(Activity.id)
    ).all()
    kinds = [entry.kind for entry in entries]
    assert kinds == [
        "accountant_export_built",
        "export_share_link_created",
        "export_share_link_revoked",
    ]
    assert _token(created.url) not in repr([entry.payload for entry in entries])


# --- opened with no account ---------------------------------------------------------


def test_a_link_opened_signed_out_hands_the_zip_out_and_is_counted_and_audited(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)
    door = _door(db_session, local_storage)
    token = _token(created.url)

    seen = door.describe(token, now=NOW)
    first = door.open(token, 1, ADDRESS, now=NOW + dt.timedelta(hours=1))
    second = door.open(token, 1, "2001:db8::1", now=NOW + dt.timedelta(hours=2))

    assert seen.company == LegalEntityRepository(db_session).default().name  # type: ignore[union-attr]
    assert (seen.period_from, seen.period_to) == (Q3.date_from, Q3.date_to)
    assert seen.expires_at == created.expires_at
    assert [part.filename for part in seen.parts] == [first.filename]
    assert first.data.startswith(b"PK") and first.data == second.data
    assert first.content_type == "application/zip" and first.filename.endswith(".zip")
    # Reading what the link opens is not an opening.
    read = _links(db_session).get_export_share_link(created.id, ADMIN, now=NOW)
    assert read.download_count == 2
    assert [(o.address, o.part, o.opened_at) for o in read.openings] == [
        ("2001:db8::1", 1, NOW + dt.timedelta(hours=2)),
        (ADDRESS, 1, NOW + dt.timedelta(hours=1)),
    ]
    rows = db_session.scalars(select(ExportShareOpening)).all()
    assert len(rows) == 2 and {row.link_id for row in rows} == {created.id}


def test_who_shared_the_link_is_named_to_the_person_who_opens_it(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    user = User(email="lucia@example.it", name="Lucia Verdi", role="admin")
    db_session.add(user)
    db_session.flush()
    actor = Actor(id=user.id, type="user", role="admin")
    created = _links(db_session).create_export_share_link(export_id, actor, now=NOW)

    seen = _door(db_session, local_storage).describe(_token(created.url), now=NOW)

    assert seen.shared_by == "Lucia Verdi"


def test_a_part_that_does_not_exist_is_not_found_and_not_counted(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)

    with pytest.raises(NotFound):
        _door(db_session, local_storage).open(_token(created.url), 2, ADDRESS, now=NOW)

    assert db_session.get(ExportShareLink, created.id).download_count == 0  # type: ignore[union-attr]


def test_a_download_the_storage_failed_is_not_counted(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)
    for part in db_session.get(AccountantExportRow, export_id).parts:  # type: ignore[union-attr]
        local_storage.delete(part["key"])

    with pytest.raises(NotFound):
        _door(db_session, local_storage).open(_token(created.url), 1, ADDRESS, now=NOW)

    assert db_session.get(ExportShareLink, created.id).download_count == 0  # type: ignore[union-attr]
    assert db_session.scalars(select(ExportShareOpening)).all() == []


def test_the_address_of_an_opening_is_kept_to_the_width_of_its_column(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)

    _door(db_session, local_storage).open(_token(created.url), 1, "x" * 500, now=NOW)

    assert len(db_session.scalars(select(ExportShareOpening.address)).one()) == 64


# --- refused ---------------------------------------------------------------------------


def test_a_revoked_link_is_refused_with_its_own_word_and_the_admin_sees_it_ended(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)
    token = _token(created.url)
    door = _door(db_session, local_storage)
    door.open(token, 1, ADDRESS, now=NOW)

    revoked = _links(db_session).revoke_export_share_link(created.id, ADMIN, now=NOW)

    assert revoked.state == "revoked" and revoked.revoked_at == NOW
    with pytest.raises(ExportLinkRevoked):
        door.open(token, 1, ADDRESS, now=NOW)
    with pytest.raises(ExportLinkRevoked):
        door.describe(token, now=NOW)
    # The count of what was handed out before stays, and nothing was added after.
    assert _links(db_session).get_export_share_link(created.id, ADMIN).download_count == 1
    # Revoking again changes nothing, and writes no second trace.
    again = _links(db_session).revoke_export_share_link(created.id, ADMIN, now=NOW)
    assert again.revoked_at == NOW
    revocations = db_session.scalars(
        select(Activity).where(Activity.kind == "export_share_link_revoked")
    ).all()
    assert len(revocations) == 1


def test_an_expired_link_is_refused_from_the_moment_it_expires(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, days=1, now=NOW)
    token = _token(created.url)
    door = _door(db_session, local_storage)
    last_moment = created.expires_at - dt.timedelta(seconds=1)

    assert door.open(token, 1, ADDRESS, now=last_moment).data.startswith(b"PK")
    with pytest.raises(ExportLinkExpired):
        door.open(token, 1, ADDRESS, now=created.expires_at)
    with pytest.raises(ExportLinkExpired):
        door.describe(token, now=created.expires_at + dt.timedelta(days=2))
    listed = _links(db_session).list_export_share_links(
        ADMIN, now=created.expires_at + dt.timedelta(days=2)
    )
    assert [link.state for link in listed.items] == ["expired"]


def test_a_token_nobody_was_given_is_unknown(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)

    with pytest.raises(ExportLinkUnknown):
        _door(db_session, local_storage).open("not-a-token", 1, ADDRESS, now=NOW)
    with pytest.raises(ExportLinkUnknown):
        _door(db_session, local_storage).describe("", now=NOW)


def test_a_link_to_an_export_past_its_thirteen_months_is_over_and_no_new_one_is_made(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    created = _links(db_session).create_export_share_link(export_id, ADMIN, days=30, now=NOW)
    later = dt.datetime(2027, 12, 31, tzinfo=dt.UTC)
    ExportRetention(db_session, lambda: local_storage).run(now=later, dry_run=False)

    with pytest.raises(ExportLinkExpired):
        _door(db_session, local_storage).open(_token(created.url), 1, ADDRESS, now=NOW)
    with pytest.raises(ValidationFailed):
        _links(db_session).create_export_share_link(export_id, ADMIN, now=later)


# --- who may --------------------------------------------------------------------------


@pytest.mark.parametrize("actor", [COLLABORATOR, READONLY])
def test_only_an_admin_makes_reads_and_ends_a_link(
    db_session: Session, export_id: UUID, actor: Actor
) -> None:
    made = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)

    with pytest.raises(PermissionDenied):
        _links(db_session).create_export_share_link(export_id, actor, now=NOW)
    with pytest.raises(PermissionDenied):
        _links(db_session).list_export_share_links(actor)
    with pytest.raises(PermissionDenied):
        _links(db_session).get_export_share_link(made.id, actor)
    with pytest.raises(PermissionDenied):
        _links(db_session).revoke_export_share_link(made.id, actor)
    assert db_session.get(ExportShareLink, made.id).revoked_at is None  # type: ignore[union-attr]


def test_an_agent_makes_a_link_only_with_full_access_and_never_reads_or_ends_one(
    db_session: Session, export_id: UUID
) -> None:
    with pytest.raises(AgentForbidden):
        _links(db_session).create_export_share_link(export_id, AGENT, now=NOW)

    made = _links(db_session).create_export_share_link(export_id, FULL_ACCESS_AGENT, now=NOW)

    assert made.created_by == FULL_ACCESS_AGENT.id
    with pytest.raises(AgentForbidden):
        _links(db_session).list_export_share_links(FULL_ACCESS_AGENT)
    with pytest.raises(AgentForbidden):
        _links(db_session).revoke_export_share_link(made.id, FULL_ACCESS_AGENT)


def test_an_admin_outside_the_scope_of_the_entity_cannot_reach_its_links(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    default = LegalEntityRepository(db_session).default()
    assert default is not None
    other = _second_entity(db_session)
    mine = _build(db_session, local_storage, legal_entity_id=default.id)
    theirs = _build(db_session, local_storage, legal_entity_id=other.id)
    everything = _build(db_session, local_storage, all_entities=True)
    scoped = Actor(id=uuid4(), type="user", role="admin", legal_entities=(default.id,))
    service = _links(db_session)
    own = service.create_export_share_link(mine, scoped, now=NOW)
    foreign = service.create_export_share_link(theirs, ADMIN, now=NOW)
    wide = service.create_export_share_link(everything, ADMIN, now=NOW)

    with pytest.raises(NotFound):
        service.create_export_share_link(theirs, scoped, now=NOW)
    with pytest.raises(ScopedAdmin):
        service.create_export_share_link(everything, scoped, now=NOW)
    for link in (foreign, wide):
        with pytest.raises(NotFound if link is foreign else ScopedAdmin):
            service.get_export_share_link(link.id, scoped)
        with pytest.raises(NotFound if link is foreign else ScopedAdmin):
            service.revoke_export_share_link(link.id, scoped)
    assert {link.id for link in service.list_export_share_links(scoped).items} == {own.id}
    assert service.list_export_share_links(scoped).total_count == 1
    assert {link.id for link in service.list_export_share_links(ADMIN).items} == {
        own.id,
        foreign.id,
        wide.id,
    }


def test_the_list_pages_newest_first_and_narrows_to_one_export(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    other_export = _build(db_session, local_storage)
    service = _links(db_session)
    made = [
        service.create_export_share_link(export_id, ADMIN, now=NOW).id,
        service.create_export_share_link(export_id, ADMIN, now=NOW).id,
        service.create_export_share_link(other_export, ADMIN, now=NOW).id,
    ]

    page = service.list_export_share_links(ADMIN, limit=2)
    rest = service.list_export_share_links(ADMIN, offset=2, limit=2)
    narrowed = service.list_export_share_links(ADMIN, export_id)

    assert page.total_count == 3 and [link.id for link in page.items + rest.items] == made[::-1]
    assert {link.id for link in narrowed.items} == set(made[:2]) and narrowed.total_count == 2
    assert all(link.openings == [] for link in page.items)


# --- Drive ----------------------------------------------------------------------------


def _drive() -> tuple[GDriveStorage, FakeDrive, Settings]:
    drive = FakeDrive()
    storage = GDriveStorage.from_service_account(
        service_account_json=SERVICE_ACCOUNT_JSON,
        root_folder_id=drive.root_id,
        http=drive,
        sign_assertion=lambda claims: "assertion",
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        storage_backend="gdrive",
        gdrive_service_account_json=SERVICE_ACCOUNT_JSON,
        gdrive_root_folder_id=drive.root_id,
    )
    return storage, drive, settings


def test_an_export_is_written_to_the_drive_folder_the_design_names(
    db_session: Session,
) -> None:
    storage, _, settings = _drive()
    exported = _build(db_session, storage)
    row = db_session.get(AccountantExportRow, exported)
    assert row is not None
    default = LegalEntityRepository(db_session).default()
    assert default is not None

    written = ExportDriveService(db_session, storage, settings).write_export_to_drive(
        exported, ADMIN, now=NOW
    )

    assert written.prefix == (
        f"commercialista/{row.prefix.split('/')[1]}/2026-t3/20261110t104012z-{written.id.hex[-8:]}"
    )
    assert written.files == [row.parts[0]["filename"]]
    assert written.written_by == ADMIN.id and written.written_at == NOW
    copy = storage.get(f"{written.prefix}/{row.parts[0]['filename']}")
    assert copy == storage.get(row.parts[0]["key"]) and copy.startswith(b"PK")
    kept = db_session.scalars(select(ExportDriveWrite)).one()
    assert kept.files[0]["key"] == f"{written.prefix}/{row.parts[0]['filename']}"
    entry = db_session.scalars(
        select(Activity).where(Activity.kind == "accountant_export_written_to_drive")
    ).one()
    assert entry.entity_id == exported and entry.payload["prefix"] == written.prefix
    listed = ExportDriveService(db_session, storage, settings).list_drive_writes(exported, ADMIN)
    assert [write.id for write in listed] == [written.id]


def test_writing_twice_keeps_both_copies_apart(db_session: Session) -> None:
    storage, _, settings = _drive()
    exported = _build(db_session, storage)
    service = ExportDriveService(db_session, storage, settings)

    first = service.write_export_to_drive(exported, ADMIN, now=NOW)
    second = service.write_export_to_drive(exported, ADMIN, now=NOW + dt.timedelta(minutes=5))

    assert first.prefix != second.prefix
    # Two writes in the same second are apart too.
    third = service.write_export_to_drive(exported, ADMIN, now=NOW + dt.timedelta(minutes=5))
    assert third.prefix != second.prefix
    assert [write.id for write in service.list_drive_writes(exported, ADMIN)] == [
        third.id,
        second.id,
        first.id,
    ]


def test_with_local_storage_there_is_no_drive_and_the_status_says_so(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    service = ExportDriveService(db_session, local_storage, SETTINGS)

    status = ExportDriveStatus(db_session, SETTINGS).get_drive_status(ADMIN)

    assert (status.available, status.reason) == (False, "local_storage")
    with pytest.raises(ValidationFailed) as refused:
        service.write_export_to_drive(export_id, ADMIN, now=NOW)
    assert "non su Google Drive" in refused.value.message
    assert db_session.scalars(select(ExportDriveWrite)).all() == []


def test_drive_chosen_but_not_connected_is_one_sentence_and_no_button(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID
) -> None:
    unconnected = Settings(_env_file=None, storage_backend="gdrive")  # type: ignore[call-arg]
    service = ExportDriveService(db_session, local_storage, unconnected)

    status = ExportDriveStatus(db_session, unconnected).get_drive_status(ADMIN)

    assert (status.available, status.reason) == (False, "not_connected")
    with pytest.raises(StorageNotConfigured):
        service.write_export_to_drive(export_id, ADMIN, now=NOW)


def test_a_half_configured_drive_is_answered_not_connected_and_not_a_failure(
    db_session: Session,
) -> None:
    half = Settings(  # type: ignore[call-arg]
        _env_file=None, storage_backend="gdrive", gdrive_service_account_json=SERVICE_ACCOUNT_JSON
    )

    status = ExportDriveStatus(db_session, half).get_drive_status(ADMIN)

    assert (status.available, status.reason) == (False, "not_connected")


def test_the_status_says_available_with_a_service_account(db_session: Session) -> None:
    storage, _, settings = _drive()

    status = ExportDriveStatus(db_session, settings).get_drive_status(ADMIN)

    assert (status.available, status.reason) == (True, None)
    only_the_folder = Settings(  # type: ignore[call-arg]
        _env_file=None, storage_backend="gdrive", gdrive_root_folder_id="abc"
    )
    assert ExportDriveStatus(db_session, only_the_folder).get_drive_status(ADMIN).reason == (
        "not_connected"
    )


def test_writing_to_drive_is_a_persons_act_and_an_admins(db_session: Session) -> None:
    storage, _, settings = _drive()
    exported = _build(db_session, storage)
    service = ExportDriveService(db_session, storage, settings)

    for agent in (AGENT, FULL_ACCESS_AGENT):
        with pytest.raises(AgentForbidden):
            service.write_export_to_drive(exported, agent, now=NOW)
        with pytest.raises(AgentForbidden):
            ExportDriveStatus(db_session, settings).get_drive_status(agent)
    for other in (COLLABORATOR, READONLY):
        with pytest.raises(PermissionDenied):
            service.write_export_to_drive(exported, other, now=NOW)
    default = LegalEntityRepository(db_session).default()
    assert default is not None
    elsewhere = _second_entity(db_session)
    scoped = Actor(id=uuid4(), type="user", role="admin", legal_entities=(elsewhere.id,))
    with pytest.raises(NotFound):
        service.write_export_to_drive(
            _build(db_session, storage, legal_entity_id=default.id), scoped, now=NOW
        )
    assert db_session.scalars(select(ExportDriveWrite)).all() == []


def test_a_copy_that_fails_halfway_leaves_nothing_in_drive_and_no_row(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed = UUID("00000000-0000-7000-8000-000000000000")
    monkeypatch.setattr("pigrocrm.core.accountant_export.drive_delivery.uuid7", lambda: fixed)
    storage, _, settings = _drive()
    exported = _build(db_session, storage)
    row = db_session.get(AccountantExportRow, exported)
    assert row is not None
    row.parts = [row.parts[0], {**row.parts[0], "key": "commercialista/missing/part-2.zip"}]
    db_session.flush()
    service = ExportDriveService(db_session, storage, settings)

    with pytest.raises(NotFound):
        service.write_export_to_drive(exported, ADMIN, now=NOW)

    assert db_session.scalars(select(ExportDriveWrite)).all() == []
    with pytest.raises(NotFound):
        storage.get(
            f"commercialista/{row.prefix.split('/')[1]}/2026-t3/20261110t104012z/{row.parts[0]['filename']}"
        )


def test_the_copies_in_drive_go_with_the_export_after_thirteen_months(
    db_session: Session,
) -> None:
    storage, _, settings = _drive()
    exported = _build(db_session, storage)
    written = ExportDriveService(db_session, storage, settings).write_export_to_drive(
        exported, ADMIN, now=NOW
    )
    key = f"{written.prefix}/{written.files[0]}"
    assert storage.get(key).startswith(b"PK")

    ExportRetention(db_session, lambda: storage).run(
        now=dt.datetime(2027, 12, 31, tzinfo=dt.UTC), dry_run=False
    )

    with pytest.raises(NotFound):
        storage.get(key)
    assert (
        ExportDriveService(db_session, storage, settings).list_drive_writes(exported, ADMIN) == []
    )


@pytest.mark.parametrize(
    ("first", "last", "folder"),
    [
        (D(2026, 7, 1), D(2026, 9, 30), "2026-t3"),
        (D(2026, 1, 1), D(2026, 3, 31), "2026-t1"),
        (D(2026, 10, 1), D(2026, 12, 31), "2026-t4"),
        (D(2026, 9, 1), D(2026, 9, 30), "2026-09"),
        (D(2024, 2, 1), D(2024, 2, 29), "2024-02"),
        (D(2026, 1, 1), D(2026, 12, 31), "2026"),
        (D(2026, 2, 1), D(2026, 4, 30), "2026-02-01_2026-04-30"),
        (D(2026, 7, 15), D(2026, 9, 30), "2026-07-15_2026-09-30"),
        (D(2025, 11, 1), D(2026, 1, 31), "2025-11-01_2026-01-31"),
    ],
)
def test_a_period_is_a_folder_a_person_can_read(first: dt.date, last: dt.date, folder: str) -> None:
    assert period_folder(first, last) == folder


# --- an export with pay: the link with a code (REB-1048) ----------------------------------------


@pytest.fixture
def payroll_export_id(db_session: Session, local_storage: LocalFileStorage) -> UUID:
    return _build(db_session, local_storage, kind="payroll_changes")


def _code_of(sender: RecordingSender) -> str:
    return sender.sent[-1].text.split("\n\n")[2].strip()


def _coded(session: Session, export_id: UUID, **kwargs: object) -> tuple[str, UUID]:
    created = _links(session).create_export_share_link(
        export_id, ADMIN, recipient_email="Studio.Paghe@Example.COM", now=NOW, **kwargs  # type: ignore[arg-type]
    )
    return _token(created.url), created.id


def test_an_export_with_pay_has_no_plain_link(
    db_session: Session, payroll_export_id: UUID, export_id: UUID
) -> None:
    with pytest.raises(ValidationFailed, match="solo con un link con codice"):
        _links(db_session).create_export_share_link(payroll_export_id, ADMIN, now=NOW)
    with pytest.raises(ValidationFailed, match="solo con un link con codice"):
        _links(db_session).create_export_share_link(
            payroll_export_id, ADMIN, recipient_email="  ", now=NOW
        )
    with pytest.raises(ValidationFailed, match="non è valido"):
        _links(db_session).create_export_share_link(
            payroll_export_id, ADMIN, recipient_email="non-un-indirizzo", now=NOW
        )
    # An agent with full access gets the same rule, and a plain export stays plain.
    with pytest.raises(ValidationFailed):
        _links(db_session).create_export_share_link(payroll_export_id, FULL_ACCESS_AGENT, now=NOW)
    plain = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)
    assert plain.recipient_email is None


def test_a_link_with_a_code_opens_nothing_until_the_recipient_gives_the_code(
    db_session: Session, local_storage: LocalFileStorage, payroll_export_id: UUID
) -> None:
    token, link_id = _coded(db_session, payroll_export_id)
    door = _door(db_session, local_storage)
    sender = RecordingSender()

    seen = door.describe(token, now=NOW)
    assert seen.requires_code and not seen.unlocked and seen.parts == []
    assert seen.recipient_hint == "s…@example.com"
    with pytest.raises(ExportCodeRequired):
        door.open(token, 1, ADDRESS, now=NOW)

    assert door.send_code(token, sender, now=NOW) == "s…@example.com"
    mail = sender.sent[0]
    assert mail.to == "studio.paghe@example.com" and "15 minuti" in mail.text
    assert token not in mail.text and "?t=" not in mail.text
    code = _code_of(sender)
    assert len(code) == 6 and code.isdigit()
    stored = db_session.scalar(select(ExportShareCode).where(ExportShareCode.link_id == link_id))
    assert stored is not None and code not in stored.code_hash and stored.consumed_at is None

    access = door.verify_code(token, code, now=NOW + dt.timedelta(minutes=1))
    unlocked = door.describe(token, access=access, now=NOW + dt.timedelta(minutes=2))
    assert unlocked.unlocked and [p.part for p in unlocked.parts] == [1]
    opened = door.open(token, 1, ADDRESS, access=access, now=NOW + dt.timedelta(minutes=2))
    assert opened.content_type == "application/zip"
    row = db_session.get(ExportShareLink, link_id)
    assert row is not None and row.download_count == 1
    # The code is spent, and the session is not the code.
    with pytest.raises(ExportCodeInvalid):
        door.verify_code(token, code, now=NOW + dt.timedelta(minutes=3))
    with pytest.raises(ExportCodeRequired):
        door.open(token, 1, ADDRESS, access=code, now=NOW + dt.timedelta(minutes=3))
    # The session ends after half an hour; the link itself does not.
    with pytest.raises(ExportCodeRequired):
        door.open(token, 1, ADDRESS, access=access, now=NOW + dt.timedelta(minutes=45))


def test_a_wrong_code_counts_and_five_wrong_answers_burn_the_code(
    db_session: Session, local_storage: LocalFileStorage, payroll_export_id: UUID
) -> None:
    token, _ = _coded(db_session, payroll_export_id)
    door = _door(db_session, local_storage)
    sender = RecordingSender()
    door.send_code(token, sender, now=NOW)
    code = _code_of(sender)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(CODE_MAX_FAILURES):
        with pytest.raises(ExportCodeInvalid):
            door.verify_code(token, wrong, now=NOW)

    # The right code now is as refused as a wrong one: a new code has to be asked.
    with pytest.raises(ExportCodeInvalid):
        door.verify_code(token, code, now=NOW)
    door.send_code(token, sender, now=NOW + dt.timedelta(minutes=2))
    assert door.verify_code(token, _code_of(sender), now=NOW + dt.timedelta(minutes=3))


def test_a_code_expires_after_a_quarter_of_an_hour(
    db_session: Session, local_storage: LocalFileStorage, payroll_export_id: UUID
) -> None:
    token, _ = _coded(db_session, payroll_export_id)
    door = _door(db_session, local_storage)
    sender = RecordingSender()
    door.send_code(token, sender, now=NOW)

    with pytest.raises(ExportCodeInvalid):
        door.verify_code(token, _code_of(sender), now=NOW + dt.timedelta(minutes=16))


def test_codes_are_mailed_one_a_minute_and_five_an_hour(
    db_session: Session, local_storage: LocalFileStorage, payroll_export_id: UUID
) -> None:
    token, _ = _coded(db_session, payroll_export_id)
    door = _door(db_session, local_storage)
    sender = RecordingSender()
    door.send_code(token, sender, now=NOW)
    with pytest.raises(ExportCodeThrottled, match="un minuto"):
        door.send_code(token, sender, now=NOW + dt.timedelta(seconds=30))
    for minute in range(2, 6):
        door.send_code(token, sender, now=NOW + dt.timedelta(minutes=minute))
    with pytest.raises(ExportCodeThrottled, match="un'ora"):
        door.send_code(token, sender, now=NOW + dt.timedelta(minutes=7))
    assert len(sender.sent) == 5


def test_a_mail_the_provider_refuses_leaves_no_code_behind(
    db_session: Session, local_storage: LocalFileStorage, payroll_export_id: UUID
) -> None:
    token, link_id = _coded(db_session, payroll_export_id)
    door = _door(db_session, local_storage)

    with pytest.raises(ExportCodeMailFailed):
        door.send_code(token, RecordingSender(accepts=False), now=NOW)

    assert db_session.scalars(select(ExportShareCode)).all() == []
    # So the next ask is not throttled by a mail that never left.
    door.send_code(token, RecordingSender(), now=NOW + dt.timedelta(seconds=5))
    assert link_id


def test_a_plain_link_has_no_code_to_send_and_a_coded_link_ends_like_any_other(
    db_session: Session, local_storage: LocalFileStorage, export_id: UUID, payroll_export_id: UUID
) -> None:
    plain = _links(db_session).create_export_share_link(export_id, ADMIN, now=NOW)
    door = _door(db_session, local_storage)
    with pytest.raises(ValidationFailed, match="non chiede un codice"):
        door.send_code(_token(plain.url), RecordingSender(), now=NOW)
    with pytest.raises(ExportCodeInvalid):
        door.verify_code(_token(plain.url), "123456", now=NOW)
    assert door.describe(_token(plain.url), now=NOW).unlocked

    token, link_id = _coded(db_session, payroll_export_id)
    _links(db_session).revoke_export_share_link(link_id, ADMIN, now=NOW)
    with pytest.raises(ExportLinkRevoked):
        door.send_code(token, RecordingSender(), now=NOW)


def test_a_payroll_row_with_no_recipient_opens_nothing(
    db_session: Session, local_storage: LocalFileStorage, payroll_export_id: UUID
) -> None:
    # Not made by any service: a row written another way is still refused.
    raw = "a-token-written-by-hand"
    db_session.add(
        ExportShareLink(
            export_id=payroll_export_id,
            token_hash=hash_token(raw),
            expires_at=NOW + dt.timedelta(days=1),
            created_by_type="system",
        )
    )
    db_session.flush()

    with pytest.raises(ExportLinkRevoked):
        _door(db_session, local_storage).open(raw, 1, ADDRESS, now=NOW)
