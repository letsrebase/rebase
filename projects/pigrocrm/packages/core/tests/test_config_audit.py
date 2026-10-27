"""R5 — audit trail for configuration and tokens.

The four things an administrator can change that used to leave no trace at all: field
definitions, pipeline stages, users, and personal access tokens. Everything here is
written against the one `activities` table the rest of the timeline already uses --
`kind` is an open string by design, so none of this needed a migration.

The PAT half is the one that matters most, and the reason is worth stating where the
tests are: a personal access token inherits its owner's full role and never expires
(R10). A credential with those properties whose issue, first use and revocation left
no trace made every other guarantee in this codebase harder to reason about. The
absence tests at the bottom hold the audit payload to the same standard the token
handling itself is held to: the raw value and its hash must not appear in the
timeline, in an error, in a schema dump, or in a failure dump.
"""

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.diff import field_changes
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.pat_models import PersonalAccessToken
from pigrocrm.core.auth.pat_service import PAT_PREFIX, PatRead, PatService, _digest
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import UserCreate, UserRead, UserUpdate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.repository import FieldDefinitionRepository
from pigrocrm.core.fields.schemas import FieldDefinitionCreate, FieldDefinitionUpdate
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.pipeline.schemas import PipelineStageCreate, PipelineStageUpdate
from pigrocrm.core.pipeline.service import DEFAULT_STAGES, PipelineService

SYSTEM = Actor(id=None, type="system", role="admin")


def _deal_in_stage(db_session: Session, stage_id: UUID) -> None:
    """A stage with a deal in it cannot be deleted, which is the path this fixture
    exists to reach."""
    customer = CustomerService(db_session).create(CustomerCreate(ragione_sociale="ACME"), SYSTEM)
    DealService(db_session).create(
        DealCreate(nome="Un deal", customer_id=customer.id, pipeline_stage_id=stage_id), SYSTEM
    )


def _admin(db_session: Session, email: str) -> UserRead:
    return UserService(db_session).create(
        UserCreate(email=email, password="supersegreta1", nome="Amministratrice", ruolo="admin"),
        SYSTEM,
    )


def _entries(db_session: Session, entity_type: str, entity_id: UUID) -> list[Activity]:
    """Oldest first, unlike `ActivityService.timeline`: these tests read a sequence of
    events as a story, and reading it backwards makes every assertion harder than it
    needs to be. Straight off the model rather than through `ActivityRead` because the
    absence tests below need the ORM object itself."""
    stmt = (
        select(Activity)
        .where(Activity.entity_type == entity_type, Activity.entity_id == entity_id)
        .order_by(Activity.occurred_at, Activity.id)
    )
    return list(db_session.execute(stmt).scalars())


def _all_entries(db_session: Session) -> list[Activity]:
    return list(db_session.execute(select(Activity)).scalars())


def _entries_since(db_session: Session, seen: set[UUID]) -> list[Activity]:
    """Only the entries this test wrote.

    `_all_entries` reads the whole table, which is a fair question on a database only
    this test has touched and a wrong one here: other suites commit rows that outlive
    them -- pipeline stages and deals among them -- so a count over everything measures
    the order the suite happened to run in. These two call sites take a baseline first
    and assert on the delta, which is the same assertion about this test's own behaviour
    and does not move when a neighbour leaves something behind."""
    return [entry for entry in _all_entries(db_session) if entry.id not in seen]


def _kinds(db_session: Session, entity_type: str, entity_id: UUID) -> list[str]:
    return [e.kind for e in _entries(db_session, entity_type, entity_id)]


def _audit_is_down(*args: object, **kwargs: object) -> None:
    """Stands in for anything that can make the audit write fail after the change has
    already been staged. `RuntimeError`, not a domain error, so no `except` clause in
    any service can mistake it for a case it knows how to recover from."""
    raise RuntimeError("audit indisponibile")


# --- the before/after helper the three configuration audits share --------------------


def test_field_changes_reports_only_the_keys_that_really_moved() -> None:
    """The set of keys a caller *sent* is not the set of keys that changed. A patch
    re-sending `required=False` on a field that was already optional is not an event,
    and recording it as one fills the timeline with decisions nobody took."""
    delta = field_changes(
        {"label": "Settore", "required": False}, {"label": "Settore", "required": True}
    )

    assert delta["changed"] == ["required"]
    assert delta["before"] == {"required": False}
    assert delta["after"] == {"required": True}


def test_field_changes_is_empty_when_nothing_moved() -> None:
    assert field_changes({"a": 1, "b": "x"}, {"a": 1, "b": "x"}) == {}


def test_field_changes_reads_a_key_that_did_not_exist_before_as_none() -> None:
    """An audit path must never raise on the shape of its own input: `before` missing a
    key the caller is setting for the first time is a `None -> value` change, not a
    KeyError taking down the operation being recorded."""
    delta = field_changes({}, {"code": "lead"})

    assert delta == {"changed": ["code"], "before": {"code": None}, "after": {"code": "lead"}}


def test_field_changes_ignores_keys_absent_from_the_patch() -> None:
    assert field_changes({"a": 1, "b": 2}, {"a": 1}) == {}


# --- the payload sanitizer had no encoder for the types these audits carry -----------


def test_a_uuid_in_a_payload_is_stored_as_text(db_session: Session) -> None:
    """JSONB serialization goes through `json.dumps`, which has no encoder for `UUID`:
    before the sanitizer handled it, putting a token's id in an audit payload raised
    `TypeError` at flush and took down the very operation it was recording."""
    service = ActivityService(db_session)
    entity_id, referenced = uuid4(), uuid4()
    service.record("user", entity_id, "pat_created", SYSTEM, {"token_id": referenced})
    db_session.commit()

    assert service.timeline("user", entity_id)[0].payload == {"token_id": str(referenced)}


def test_a_datetime_in_a_payload_is_stored_as_iso_text(db_session: Session) -> None:
    from datetime import UTC, datetime

    moment = datetime(2026, 9, 3, 10, 30, tzinfo=UTC)
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record("user", entity_id, "pat_revoked", SYSTEM, {"revoked_at": moment})
    db_session.commit()

    assert service.timeline("user", entity_id)[0].payload == {"revoked_at": moment.isoformat()}


# --- field definitions ---------------------------------------------------------------


def _field(db_session: Session, key: str = "settore", **overrides: Any) -> Any:
    data = {
        "entity_type": "customer",
        "key": key,
        "label": "Settore",
        "field_type": "text",
        "required": False,
    }
    data.update(overrides)
    return FieldDefinitionService(db_session).create(
        FieldDefinitionCreate.model_validate(data), SYSTEM
    )


def test_creating_a_field_definition_records_what_was_created(db_session: Session) -> None:
    field = _field(db_session)

    entry = _entries(db_session, "field_definition", field.id)[0]
    assert entry.kind == "created"
    assert entry.payload == {
        "entity_type": "customer",
        "key": "settore",
        "label": "Settore",
        "field_type": "text",
        "required": False,
    }
    assert entry.actor_type == "system"


def test_renaming_a_field_definition_records_the_old_and_the_new_label(
    db_session: Session,
) -> None:
    """ "Who renamed this and what was it called before" is unanswerable from a bare
    list of touched key names, which is why configuration audits carry both sides."""
    field = _field(db_session)

    FieldDefinitionService(db_session).update(
        field.id, FieldDefinitionUpdate(label="Settore merceologico"), SYSTEM
    )

    entry = _entries(db_session, "field_definition", field.id)[-1]
    assert entry.kind == "updated"
    assert entry.payload["changed"] == ["label"]
    assert entry.payload["before"] == {"label": "Settore"}
    assert entry.payload["after"] == {"label": "Settore merceologico"}


def test_a_renamed_field_and_an_archived_field_do_not_look_alike(db_session: Session) -> None:
    """The one distinction R5 names explicitly. Archiving is this domain's delete --
    there is no hard delete for a definition -- so if a rename and an archive shared a
    kind, the timeline could not tell "it is called something else now" from "it is
    gone from every form"."""
    renamed = _field(db_session, "rinominato")
    archived = _field(db_session, "archiviato")
    service = FieldDefinitionService(db_session)

    service.update(renamed.id, FieldDefinitionUpdate(label="Nuovo nome"), SYSTEM)
    service.archive(archived.id, SYSTEM)

    assert _kinds(db_session, "field_definition", renamed.id) == ["created", "updated"]
    assert _kinds(db_session, "field_definition", archived.id) == ["created", "archived"]


def test_making_a_field_required_records_who_turned_it_on(db_session: Session) -> None:
    admin = _admin(db_session, "campi@test.it")
    actor = Actor(id=admin.id, type="user", role="admin")
    field = _field(db_session)

    FieldDefinitionService(db_session).update(field.id, FieldDefinitionUpdate(required=True), actor)

    entry = _entries(db_session, "field_definition", field.id)[-1]
    assert entry.payload["before"] == {"required": False}
    assert entry.payload["after"] == {"required": True}
    assert entry.actor_id == admin.id
    assert entry.actor_type == "user"


def test_an_update_that_changes_nothing_records_nothing(db_session: Session) -> None:
    field = _field(db_session)

    FieldDefinitionService(db_session).update(
        field.id, FieldDefinitionUpdate(label="Settore", required=False), SYSTEM
    )

    assert _kinds(db_session, "field_definition", field.id) == ["created"]


def test_archiving_records_the_definition_and_unarchiving_records_the_return(
    db_session: Session,
) -> None:
    field = _field(db_session)
    service = FieldDefinitionService(db_session)

    service.archive(field.id, SYSTEM)
    service.unarchive(field.id, SYSTEM)

    entries = _entries(db_session, "field_definition", field.id)
    assert [e.kind for e in entries] == ["created", "archived", "unarchived"]
    assert entries[1].payload["key"] == "settore"
    assert entries[2].payload["key"] == "settore"


def test_archiving_an_already_archived_definition_records_nothing(db_session: Session) -> None:
    """The same discipline `CustomerService.restore` applies: an entry for a state
    change that did not happen is a lie the timeline cannot be talked out of later."""
    field = _field(db_session)
    service = FieldDefinitionService(db_session)
    service.archive(field.id, SYSTEM)

    service.archive(field.id, SYSTEM)

    assert _kinds(db_session, "field_definition", field.id) == ["created", "archived"]


def test_a_field_definition_is_not_created_when_its_audit_entry_cannot_be(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One service method, one transaction, in the direction that is easy to get wrong.

    An audit entry written *after* the commit still looks correct in every happy-path
    test, and is exactly what `ActivityService.record`'s docstring warns against: the
    change would then be durable while the entry recording it is not. Breaking the
    audit and demanding the change be gone is the only assertion that separates the
    two orderings. `rollback()` here stands in for the adapter's own request boundary,
    which is what discards a transaction nobody committed.
    """
    monkeypatch.setattr(ActivityService, "record", _audit_is_down)

    with pytest.raises(RuntimeError):
        _field(db_session, "mai_creato")
    db_session.rollback()

    assert FieldDefinitionRepository(db_session).get_by_key("customer", "mai_creato") is None


# --- pipeline stages -----------------------------------------------------------------


def test_creating_a_pipeline_stage_records_its_identity(db_session: Session) -> None:
    stage = PipelineService(db_session).create(
        PipelineStageCreate(nome="Preventivo", posizione=7, tipo="open", code="preventivo"), SYSTEM
    )

    entry = _entries(db_session, "pipeline_stage", stage.id)[0]
    assert entry.kind == "created"
    assert entry.payload == {"nome": "Preventivo", "code": "preventivo", "tipo": "open"}


def test_renaming_a_pipeline_stage_records_both_names_and_pins_the_entry_to_the_code(
    db_session: Session,
) -> None:
    service = PipelineService(db_session)
    stage = service.create(
        PipelineStageCreate(nome="Preventivo", posizione=7, code="preventivo"), SYSTEM
    )

    service.update(stage.id, PipelineStageUpdate(nome="Offerta inviata"), SYSTEM)

    entry = _entries(db_session, "pipeline_stage", stage.id)[-1]
    assert entry.payload["code"] == "preventivo", "the entry must not be pinned to the name"
    assert entry.payload["before"] == {"nome": "Preventivo"}
    assert entry.payload["after"] == {"nome": "Offerta inviata"}


def test_changing_a_stages_outcome_type_is_recorded_with_both_sides(
    db_session: Session,
) -> None:
    """`tipo` is the attribute the rest of the domain reads: it decides which stages
    `default_stage` may hand a new deal, and whether a deal counts as won. Turning one
    into a terminal stage is exactly the "who did this and when" case."""
    service = PipelineService(db_session)
    stage = service.create(PipelineStageCreate(nome="Chiuso", posizione=9), SYSTEM)

    service.update(stage.id, PipelineStageUpdate(tipo="won"), SYSTEM)

    entry = _entries(db_session, "pipeline_stage", stage.id)[-1]
    assert entry.payload["changed"] == ["tipo"]
    assert entry.payload["before"] == {"tipo": "open"}
    assert entry.payload["after"] == {"tipo": "won"}


def test_deleting_a_stage_leaves_the_only_remaining_record_that_it_existed(
    db_session: Session,
) -> None:
    """`delete` is the one hard DELETE in the domain. After it the row is gone, so the
    timeline entry has to carry enough to recognise the stage without it."""
    service = PipelineService(db_session)
    stage = service.create(
        PipelineStageCreate(nome="Da eliminare", posizione=8, code="da_eliminare"), SYSTEM
    )

    service.delete(stage.id, SYSTEM)

    with pytest.raises(NotFound):
        service.get(stage.id)
    entry = _entries(db_session, "pipeline_stage", stage.id)[-1]
    assert entry.kind == "deleted"
    assert entry.payload == {"nome": "Da eliminare", "code": "da_eliminare", "tipo": "open"}


def test_seeding_records_each_default_stage_as_seeded_and_says_nothing_on_a_reseed(
    db_session: Session,
) -> None:
    """`seed_defaults` is idempotent and converges silently; the audit has to converge
    with it, or every restart would grow the timeline for free."""
    service = PipelineService(db_session)
    # The codes this call will actually insert. Not `DEFAULT_STAGES` wholesale: other
    # suites commit stages that outlive them, so a count against the full list measures
    # the order the suite happened to run in rather than this method's behaviour. And
    # not `seed_defaults`' own return value either -- it answers with `self.list()`,
    # every stage there is, which on a reseed is six rows it did not create.
    existing = {stage.code for stage in service.list() if stage.code is not None}
    attesi = {code for code, *_ in DEFAULT_STAGES if code not in existing}

    before = {entry.id for entry in _all_entries(db_session)}
    service.seed_defaults(SYSTEM)

    seeded = [e for e in _entries_since(db_session, before) if e.entity_type == "pipeline_stage"]
    assert {e.payload["code"] for e in seeded} == attesi
    assert all(e.kind == "created" and e.payload["seeded"] is True for e in seeded)

    # The half that always bites, and the one the name is about. Whatever the database
    # held a moment ago, it has converged now, so a second pass must insert nothing and
    # therefore say nothing -- otherwise every restart grows the timeline for free.
    after_first = {entry.id for entry in _all_entries(db_session)}
    service.seed_defaults(SYSTEM)
    assert _entries_since(db_session, after_first) == [], (
        "a second seed inserted nothing and must log nothing"
    )


def test_a_refused_stage_deletion_records_nothing(db_session: Session) -> None:
    """A stage with deals in it is not deleted, so nothing happened to record."""
    service = PipelineService(db_session)
    stage = service.create(PipelineStageCreate(nome="Con deal", posizione=3), SYSTEM)
    _deal_in_stage(db_session, stage.id)

    with pytest.raises(Conflict):
        service.delete(stage.id, SYSTEM)

    assert _kinds(db_session, "pipeline_stage", stage.id) == ["created"]


# --- users ---------------------------------------------------------------------------


def test_creating_a_user_records_the_email_and_the_role(db_session: Session) -> None:
    user = _admin(db_session, "nuova@test.it")

    entry = _entries(db_session, "user", user.id)[0]
    assert entry.kind == "created"
    assert entry.payload == {"email": "nuova@test.it", "ruolo": "admin"}


def test_deactivating_a_user_records_who_turned_the_account_off(db_session: Session) -> None:
    """The literal question R5 leaves unanswerable today."""
    who = _admin(db_session, "capo@test.it")
    actor = Actor(id=who.id, type="user", role="admin")
    victim = _admin(db_session, "vittima@test.it")

    UserService(db_session).update(victim.id, UserUpdate(attivo=False), actor)

    entry = _entries(db_session, "user", victim.id)[-1]
    assert entry.kind == "updated"
    assert entry.payload["changed"] == ["attivo"]
    assert entry.payload["before"] == {"attivo": True}
    assert entry.payload["after"] == {"attivo": False}
    assert entry.actor_id == who.id


def test_promoting_a_user_to_administrator_is_recorded_with_both_roles(
    db_session: Session,
) -> None:
    """A PAT inherits its owner's role, so a silent promotion is also a silent
    escalation of every token that user already holds."""
    service = UserService(db_session)
    user = service.create(
        UserCreate(email="collab@test.it", password="supersegreta1", nome="Collab"), SYSTEM
    )

    service.update(user.id, UserUpdate(ruolo="admin"), SYSTEM)

    entry = _entries(db_session, "user", user.id)[-1]
    assert entry.payload["before"] == {"ruolo": "collaboratore"}
    assert entry.payload["after"] == {"ruolo": "admin"}


def test_a_user_update_that_changes_nothing_records_nothing(db_session: Session) -> None:
    user = _admin(db_session, "invariata@test.it")

    UserService(db_session).update(user.id, UserUpdate(attivo=True, ruolo="admin"), SYSTEM)

    assert _kinds(db_session, "user", user.id) == ["created"]


# --- personal access tokens ----------------------------------------------------------


def test_issuing_a_token_is_recorded_on_the_owners_timeline(db_session: Session) -> None:
    """Giving an agent a PAT is giving it the account, for as long as the token lives
    (R10). It belongs on the account's own timeline, not on a per-token one nobody
    would think to open."""
    user = _admin(db_session, "pat-create@test.it")
    actor = Actor(id=user.id, type="user", role="admin")

    record, _ = PatService(db_session).create("Claude locale", actor)

    entry = _entries(db_session, "user", user.id)[-1]
    assert entry.kind == "pat_created"
    assert entry.payload == {
        "token_id": str(record.id),
        "nome": "Claude locale",
        "full_access": False,
    }
    assert entry.actor_id == user.id


def test_revoking_a_token_is_recorded(db_session: Session) -> None:
    user = _admin(db_session, "pat-revoke@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session)
    record, _ = service.create("Da revocare", actor)

    service.revoke(record.id, actor)

    assert _kinds(db_session, "user", user.id) == ["created", "pat_created", "pat_revoked"]


def test_a_failed_revocation_of_someone_elses_token_records_nothing(
    db_session: Session,
) -> None:
    owner = _admin(db_session, "propietaria@test.it")
    other = _admin(db_session, "estranea@test.it")
    service = PatService(db_session)
    record, _ = service.create("Mio", Actor(id=owner.id, type="user", role="admin"))

    with pytest.raises(NotFound):
        service.revoke(record.id, Actor(id=other.id, type="user", role="admin"))

    assert _kinds(db_session, "user", owner.id) == ["created", "pat_created"]
    assert _kinds(db_session, "user", other.id) == ["created"]


def test_only_the_first_use_of_a_token_is_recorded(db_session: Session) -> None:
    """`resolve()` runs on every request an agent makes. One entry per call would
    double the write volume of the API and bury the account timeline; the transition
    from issued to in-use is the event that was actually missing."""
    user = _admin(db_session, "pat-use@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session)
    _, raw = service.create("Claude locale", actor)

    service.resolve(raw)
    service.resolve(raw)
    service.resolve(raw)

    entry = _entries(db_session, "user", user.id)[-1]
    assert _kinds(db_session, "user", user.id).count("pat_first_used") == 1
    assert entry.actor_type == "mcp", "the timeline must say an agent used it, not a browser"


def test_a_revoked_token_still_being_presented_raises_the_alarm_exactly_once(
    db_session: Session,
) -> None:
    """The most informative event in the whole file: a revoked credential still in use
    is either an agent nobody reconfigured or a copy of the token somewhere its owner
    did not intend. Recorded once per revocation, not once per attempt -- otherwise
    whoever holds the dead token can grow the table one row per request.
    """
    user = _admin(db_session, "pat-zombie@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session)
    record, raw = service.create("Rubato", actor)
    service.revoke(record.id, actor)

    for _ in range(4):
        with pytest.raises(ValidationFailed):
            service.resolve(raw)

    kinds = _kinds(db_session, "user", user.id)
    assert kinds.count("pat_used_after_revocation") == 1, kinds
    alarm = _entries(db_session, "user", user.id)[-1]
    assert alarm.payload["token_id"] == str(record.id)
    assert alarm.actor_id is None, "nobody knows who presented it; do not invent an actor"


def test_an_unknown_token_records_nothing_at_all(db_session: Session) -> None:
    """There is no account to hang it on, and writing one row per guess would turn the
    audit trail into an amplifier for whoever is doing the guessing."""
    before = {entry.id for entry in _all_entries(db_session)}
    with pytest.raises(ValidationFailed):
        PatService(db_session).resolve("pgc_mai-emesso-da-nessuno")

    assert _entries_since(db_session, before) == []


def test_a_token_is_not_issued_when_its_audit_entry_cannot_be(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same guarantee as for a field definition, on the path where it matters
    most: a token that works but was never recorded as issued is precisely the hole
    R5 describes, and an audit written after the commit would leave exactly that."""
    user = _admin(db_session, "pat-atomico@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    monkeypatch.setattr(ActivityService, "record", _audit_is_down)

    with pytest.raises(RuntimeError):
        PatService(db_session).create("Mai emesso", actor)
    db_session.rollback()

    assert PatService(db_session).list(actor) == []


def test_a_user_is_not_created_when_its_audit_entry_cannot_be(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = UserService(db_session).count()
    monkeypatch.setattr(ActivityService, "record", _audit_is_down)

    with pytest.raises(RuntimeError):
        UserService(db_session).create(
            UserCreate(email="mai@test.it", password="supersegreta1", nome="Mai"), SYSTEM
        )
    db_session.rollback()

    assert UserService(db_session).count() == before


def test_a_stage_is_not_deleted_when_its_audit_entry_cannot_be(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hard-delete path, where the ordering is most dangerous: `repo.delete` has
    already issued the DELETE by the time the audit is written, so a commit that ran
    before the entry would destroy the row and the only record of it in one go."""
    service = PipelineService(db_session)
    stage = service.create(PipelineStageCreate(nome="Sopravvive", posizione=4), SYSTEM)
    monkeypatch.setattr(ActivityService, "record", _audit_is_down)

    with pytest.raises(RuntimeError):
        service.delete(stage.id, SYSTEM)
    db_session.rollback()

    assert PipelineService(db_session).get(stage.id).nome == "Sopravvive"


# --- the secret must never reach the timeline ----------------------------------------


def _haystacks(*values: object) -> list[str]:
    """Every rendering a secret could realistically escape through: the value itself,
    its `str`, and its `repr` -- the last one because a failing assertion, a logged
    exception and a pytest dump all go through `repr`."""
    out: list[str] = []
    for value in values:
        out.extend([str(value), repr(value)])
    return out


def test_the_pat_audit_never_carries_the_token_or_its_hash(db_session: Session) -> None:
    """Slice-1A standard, applied to the audit trail: the raw value, the random half
    of it, and the SHA-256 digest `resolve()` matches on must be absent from the
    activity payloads, from the errors raised on every failing path, from the read
    schema and its JSON, and from the ORM objects' own reprs.

    The digest matters as much as the raw value: it is the lookup key, so anything
    holding it can recognise the token wherever else it appears.
    """
    user = _admin(db_session, "segreti@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session)

    record, raw = service.create("Claude locale", actor)
    service.resolve(raw)
    service.revoke(record.id, actor)
    with pytest.raises(ValidationFailed) as revoked_exc:
        service.resolve(raw)
    with pytest.raises(ValidationFailed) as unknown_exc:
        service.resolve("pgc_mai-emesso-da-nessuno")
    db_session.commit()

    # A positive control: without it this sweep would pass just as happily over an
    # empty timeline, which is the failure mode R5 is about in the first place.
    assert _kinds(db_session, "user", user.id) == [
        "created",
        "pat_created",
        "pat_first_used",
        "pat_revoked",
        "pat_used_after_revocation",
    ]

    forbidden = (raw, raw[len(PAT_PREFIX) :], _digest(raw))
    stored = db_session.get(PersonalAccessToken, record.id)
    assert stored is not None

    haystacks: list[str] = []
    for entry in _all_entries(db_session):
        haystacks.append(json.dumps(entry.payload))
        haystacks.extend(_haystacks(entry.payload, entry))
    for error in (revoked_exc.value, unknown_exc.value):
        haystacks.extend([error.message, json.dumps(error.details, default=str)])
        haystacks.extend(_haystacks(error, error.args, error.details))
    haystacks.append(record.model_dump_json())
    haystacks.extend(_haystacks(record, record.model_dump(), stored))

    for secret in forbidden:
        for haystack in haystacks:
            assert secret not in haystack, f"{secret!r} leaked into {haystack!r}"

    assert "token_hash" not in PatRead.model_fields, "the read schema must not expose the hash"


def test_the_user_audit_never_carries_the_password_or_its_hash(db_session: Session) -> None:
    """A timeline is read by more people, kept longer and exported more casually than
    the `users` table it describes."""
    password = "unaPasswordDavveroSegreta1"
    service = UserService(db_session)
    user = service.create(
        UserCreate(email="pw@test.it", password=password, nome="Con credenziali"), SYSTEM
    )
    service.update(user.id, UserUpdate(ruolo="admin", nome="Rinominata"), SYSTEM)
    db_session.commit()

    stored = UserRepository(db_session).get(user.id)
    assert stored is not None
    entries = _entries(db_session, "user", user.id)
    assert [e.kind for e in entries] == ["created", "updated"]

    for entry in entries:
        rendered = json.dumps(entry.payload) + repr(entry.payload)
        assert password not in rendered
        assert stored.password_hash not in rendered
        # The key name too, not only the value: the realistic mutation here is
        # someone widening `_snapshot` to the whole row, which would carry
        # `password_hash` under a name this catches even if the hash itself changed.
        assert "password" not in rendered
    assert stored.password_hash not in repr(stored)
