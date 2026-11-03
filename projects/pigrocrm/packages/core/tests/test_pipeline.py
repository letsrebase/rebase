from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, PermissionDenied, ValidationFailed
from pigrocrm.core.pipeline.schemas import PipelineStageCreate, PipelineStageUpdate
from pigrocrm.core.pipeline.service import PipelineService

ADMIN = Actor(id=None, type="system", role="admin")
COLLAB = Actor(id=None, type="user", role="collaborator")


def test_seed_creates_the_default_italian_sales_process(db_session: Session) -> None:
    stages = PipelineService(db_session).seed_defaults(ADMIN)
    assert [s.name for s in stages] == [
        "Lead",
        "Contattato",
        "Offerta",
        "Negoziazione",
        "Vinto",
        "Perso",
    ]


def test_seed_marks_the_terminal_stages_so_dashboards_need_no_name_matching(
    db_session: Session,
) -> None:
    """Dashboards must know what 'won' means without string-matching a label the
    user is free to rename."""
    by_name = {s.name: s for s in PipelineService(db_session).seed_defaults(ADMIN)}
    assert by_name["Vinto"].type == "won"
    assert by_name["Perso"].type == "lost"
    assert by_name["Lead"].type == "open"


def test_seed_is_idempotent(db_session: Session) -> None:
    service = PipelineService(db_session)
    service.seed_defaults(ADMIN)
    service.seed_defaults(ADMIN)
    assert len(service.list()) == 6


def test_list_is_ordered_by_position(db_session: Session) -> None:
    service = PipelineService(db_session)
    service.create(PipelineStageCreate(name="Secondo", position=1), ADMIN)
    service.create(PipelineStageCreate(name="Primo", position=0), ADMIN)
    assert [s.name for s in service.list()] == ["Primo", "Secondo"]


def test_probability_outside_zero_to_hundred_is_rejected(db_session: Session) -> None:
    service = PipelineService(db_session)
    with pytest.raises(ValidationFailed):
        service.create(PipelineStageCreate(name="X", position=0, default_probability=101), ADMIN)


def test_only_admins_configure_the_pipeline(db_session: Session) -> None:
    with pytest.raises(PermissionDenied):
        PipelineService(db_session).create(PipelineStageCreate(name="X", position=0), COLLAB)


def test_update_changes_a_stage(db_session: Session) -> None:
    service = PipelineService(db_session)
    stage = service.create(PipelineStageCreate(name="Vecchio", position=0), ADMIN)
    assert service.update(stage.id, PipelineStageUpdate(name="Nuovo"), ADMIN).name == "Nuovo"


# --- Fix round 1 ------------------------------------------------------------------


def test_seed_is_idempotent_across_a_rename(db_session: Session) -> None:
    """`name` is a label the user is free to rename -- exactly why `type` exists, and
    exactly why `seed_defaults` must not use `name` as the seeded stages' identity
    either. Renaming "Vinto" and reseeding must not recreate a stage called "Vinto"."""
    service = PipelineService(db_session)
    stages = service.seed_defaults(ADMIN)
    won = next(s for s in stages if s.name == "Vinto")
    service.update(won.id, PipelineStageUpdate(name="Chiuso vinto"), ADMIN)

    reseeded = service.seed_defaults(ADMIN)

    assert len(reseeded) == 6
    assert {s.name for s in reseeded} == {
        "Lead",
        "Contattato",
        "Offerta",
        "Negoziazione",
        "Chiuso vinto",
        "Perso",
    }


def test_update_cannot_change_code(db_session: Session) -> None:
    """`code` is identity, like `field_type` for custom fields: it must not be a
    settable field on the update schema at all, not merely rejected by convention."""
    assert "code" not in PipelineStageUpdate.model_fields
    with pytest.raises(ValidationError):
        PipelineStageUpdate.model_validate({"code": "qualcosa"})


def test_default_stage_returns_the_lowest_position_open_stage(db_session: Session) -> None:
    """A deal created without an explicit stage must start somewhere still in
    progress. A terminal stage at a lower position must not win just because it
    sorts first."""
    service = PipelineService(db_session)
    service.create(PipelineStageCreate(name="Chiuso perso", position=-1, type="lost"), ADMIN)
    service.create(PipelineStageCreate(name="Nuovo", position=0, type="open"), ADMIN)

    assert service.default_stage().name == "Nuovo"


def test_default_stage_raises_when_no_open_stage_exists(db_session: Session) -> None:
    service = PipelineService(db_session)
    service.create(PipelineStageCreate(name="Vinto", position=0, type="won"), ADMIN)

    with pytest.raises(ValidationFailed):
        service.default_stage()


def test_delete_removes_a_stage(db_session: Session) -> None:
    service = PipelineService(db_session)
    stage = service.create(PipelineStageCreate(name="Temporaneo", position=0), ADMIN)

    service.delete(stage.id, ADMIN)

    assert stage.id not in {s.id for s in service.list()}


def test_delete_requires_admin(db_session: Session) -> None:
    service = PipelineService(db_session)
    stage = service.create(PipelineStageCreate(name="Temporaneo", position=0), ADMIN)

    with pytest.raises(PermissionDenied):
        service.delete(stage.id, COLLAB)


def test_create_rejects_a_duplicate_code(db_session: Session) -> None:
    service = PipelineService(db_session)
    service.create(PipelineStageCreate(name="Uno", position=0, code="dup"), ADMIN)

    with pytest.raises(Conflict):
        service.create(PipelineStageCreate(name="Due", position=1, code="dup"), ADMIN)


def test_create_allows_several_stages_with_no_code(db_session: Session) -> None:
    """`code=None` is the normal case for a user-created stage. Postgres allows any
    number of `NULL`s under a unique index, so these must not collide with each other."""
    service = PipelineService(db_session)
    service.create(PipelineStageCreate(name="Uno", position=0), ADMIN)
    service.create(PipelineStageCreate(name="Due", position=1), ADMIN)

    assert {s.name for s in service.list()} == {"Uno", "Due"}


# --- Fix round 2 ------------------------------------------------------------------


def test_create_rejects_a_duplicate_code_race_past_the_precheck(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`test_create_rejects_a_duplicate_code` above only exercises the precheck
    branch -- the second call's own `get_by_code` correctly finds the first row, so
    the `except IntegrityError` branch never runs. This mirrors
    `test_duplicate_key_race_past_the_precheck_still_becomes_a_domain_conflict` in
    fields (force the precheck to report "not found" while a real duplicate already
    exists, so the INSERT hits the database's own unique index) and is the test that
    was actually missing, not the one already there."""
    service = PipelineService(db_session)
    service.create(PipelineStageCreate(name="Uno", position=0, code="dup"), ADMIN)

    monkeypatch.setattr(service.repo, "get_by_code", lambda code: None)

    with pytest.raises(Conflict) as exc:
        service.create(PipelineStageCreate(name="Due", position=1, code="dup"), ADMIN)
    assert exc.value.details["stage_code"] == "dup"

    # The session must still be usable right after -- a leftover PendingRollbackError
    # would blow up on the very next statement issued on it.
    assert len(service.list()) == 1


def test_seed_defaults_race_past_the_precheck_converges_silently(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape as the create()-race tests (force the precheck to lie so the INSERT
    hits the real unique constraint), but seed_defaults's contract is the opposite of
    create()'s: the caller only wanted the defaults to exist, not to create *this
    specific* row, so losing the race must converge silently instead of raising
    Conflict. Lies only on the first `repo.list()` call (the precheck that computes
    `existing_codes`) -- `seed_defaults`'s own trailing `return self.list()` also calls
    `repo.list()`, and that call must see the real rows."""
    service = PipelineService(db_session)
    service.seed_defaults(ADMIN)

    real_list = service.repo.list
    calls = {"n": 0}

    def _lie_on_first_call() -> list[Any]:
        calls["n"] += 1
        return [] if calls["n"] == 1 else real_list()

    monkeypatch.setattr(service.repo, "list", _lie_on_first_call)

    result = service.seed_defaults(ADMIN)

    assert len(result) == 6


def test_name_over_the_column_width_is_rejected_on_create_and_update() -> None:
    """Same class of gap `code`'s `CODE_MAX_LENGTH` closed in fix round 1, but on
    `name` -- present since the original Task 9 brief, unbounded until now. The
    fourth time this project has hit an unbounded string column that can reach
    `flush()` and come back as a raw, session-poisoning `DataError`."""
    with pytest.raises(ValidationError):
        PipelineStageCreate(name="x" * 61, position=0)
    with pytest.raises(ValidationError):
        PipelineStageUpdate(name="x" * 61)


# --- Task 15 review, fix round 1 --------------------------------------------------


def test_seed_defaults_requires_admin(db_session: Session) -> None:
    """`create`, `update` and `delete` on this same service all call
    `actor.require_admin(...)` themselves -- `seed_defaults` is the only write
    method that did not, which meant the admin check for reseeding the pipeline
    lived solely in the REST router (`routers/pipeline.py`'s `seed()`) and would
    have silently gone missing the moment anything else -- an MCP tool, a script,
    a future router -- called `seed_defaults()` directly. A shared service is the
    whole point of this architecture only if authorization travels with it."""
    with pytest.raises(PermissionDenied):
        PipelineService(db_session).seed_defaults(COLLAB)


# --- Final review item 2 (CRITICAL): posizione is a bare Integer, unbounded ------
#
# `pipeline_stages.position` is `Integer`. Nothing anywhere checked its range --
# unlike `default_probability`, this column has no equivalent service-level guard,
# so `2**40` reached `flush()` raw as `psycopg.errors.NumericValueOutOfRange` before
# `Field(ge=..., le=...)` existed on `PipelineStageCreate.position`/
# `PipelineStageUpdate.position`.


def test_position_at_the_bound_is_accepted_on_create(db_session: Session) -> None:
    from pigrocrm.core.pipeline.schemas import POSITION_MAX, POSITION_MIN

    service = PipelineService(db_session)
    at_min = service.create(PipelineStageCreate(name="Min", position=POSITION_MIN), ADMIN)
    at_max = service.create(PipelineStageCreate(name="Max", position=POSITION_MAX), ADMIN)
    assert at_min.position == POSITION_MIN
    assert at_max.position == POSITION_MAX


def test_position_far_beyond_the_bound_is_rejected_on_create() -> None:
    with pytest.raises(ValidationError):
        PipelineStageCreate(name="X", position=2**40)


def test_position_far_below_the_bound_is_rejected_on_create() -> None:
    """Symmetric around zero (see POSIZIONE_MIN's own comment for why negative
    values are legitimate, unlike `field_definitions.position`): a huge negative
    value is exactly as unrepresentable in `Integer` as a huge positive one."""
    with pytest.raises(ValidationError):
        PipelineStageCreate(name="X", position=-(2**40))


def test_position_far_beyond_the_bound_is_rejected_on_update() -> None:
    with pytest.raises(ValidationError):
        PipelineStageUpdate(position=2**40)


def test_to_realistic_negative_position_still_works_end_to_end(
    db_session: Session,
) -> None:
    """Regression guard for the exact scenario the bound must not break:
    test_default_stage_returns_the_lowest_position_open_stage above already relies
    on posizione=-1 constructing and round-tripping cleanly."""
    service = PipelineService(db_session)
    stage = service.create(PipelineStageCreate(name="Prima", position=-1), ADMIN)
    assert stage.position == -1
