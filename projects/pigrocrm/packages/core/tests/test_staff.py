"""Staff and directors with their pay (REB-977, spec 2026-11-09, staff, payroll and expenses,
§ 1 and § 4).

Driven against the real schema: which legal entities may have an employee and a director, the
kind's own columns, the optional link to a user, the pay day of a legal entity and a member's
override, and, the card's «done when», who may read and write pay: administrators, and an agent
only with `full_access`; never a collaborator, a readonly member or a token without the flag.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.staff.models import StaffMember
from pigrocrm.core.staff.schemas import (
    DirectorCreate,
    EmployeeCreate,
    PayDayWrite,
    StaffListQuery,
    StaffMemberUpdate,
)
from pigrocrm.core.staff.service import StaffService

D = dt.date
ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
READONLY = Actor(id=uuid4(), type="user", role="readonly")
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
AGENT_COLLABORATOR_FULL = Actor(id=uuid4(), type="mcp", role="collaborator", full_access=True)


def _entity(session: Session, pack_id: str | None, tag: str = "x") -> UUID:
    entity = LegalEntity(name=f"staff {tag}", legal_name=f"Staff {tag} S.r.l.", country="IT")
    session.add(entity)
    session.flush()
    if pack_id is not None:
        session.add(FiscalProfile(legal_entity_id=entity.id, pack_id=pack_id, regime_code="RF01"))
        session.flush()
    return entity.id


def _employee(entity: UUID, **overrides: object) -> EmployeeCreate:
    values: dict[str, object] = {
        "legal_entity_id": entity,
        "name": "Giulia Bianchi",
        "start_date": D(2026, 1, 1),
        "collective_agreement": "Commercio e terziario",
        "contract_level": "3",
        "gross_annual_pay": Decimal("30000.00"),
        "monthly_payments": 14,
        "part_time_percent": Decimal("80"),
        "employer_inps_rate": Decimal("30.00"),
        "hours_per_month": Decimal("130"),
    }
    values.update(overrides)
    return EmployeeCreate(**values)  # type: ignore[arg-type]


def _director(entity: UUID, **overrides: object) -> DirectorCreate:
    values: dict[str, object] = {
        "legal_entity_id": entity,
        "name": "Marco Rossi",
        "start_date": D(2026, 1, 1),
        "annual_fee": Decimal("24000.00"),
        "accrues_tfm": True,
        "tfm_annual_amount": Decimal("2000.00"),
        "inps_company_share": Decimal("66.67"),
    }
    values.update(overrides)
    return DirectorCreate(**values)  # type: ignore[arg-type]


# --- who may have staff ---------------------------------------------------------------------


@pytest.mark.parametrize("pack", ["it-flat-rate", "it-standard", "it-company"])
def test_an_employee_is_accepted_on_every_italian_pack(db_session: Session, pack: str) -> None:
    entity = _entity(db_session, pack, pack)
    read = StaffService(db_session).create_staff_member(_employee(entity), ADMIN)
    assert (read.kind, read.gross_annual_pay, read.monthly_payments) == (
        "employee",
        Decimal("30000.00"),
        14,
    )
    assert read.part_time_percent == Decimal("80.00")
    assert read.annual_fee is None and read.accrues_tfm is False


def test_an_employee_on_a_legal_entity_abroad_is_refused_in_italian(db_session: Session) -> None:
    entity = _entity(db_session, "non-it")
    with pytest.raises(ValidationFailed) as refused:
        StaffService(db_session).create_staff_member(_employee(entity), ADMIN)
    assert "dipendenti" in str(refused.value)
    assert db_session.query(StaffMember).count() == 0


@pytest.mark.parametrize("pack", ["it-flat-rate", "it-standard", "non-it"])
def test_a_director_is_refused_on_every_pack_but_the_company(
    db_session: Session, pack: str
) -> None:
    """The card's «done when»: refused with an Italian message, and nothing is written."""
    entity = _entity(db_session, pack, pack)
    with pytest.raises(ValidationFailed) as refused:
        StaffService(db_session).create_staff_member(_director(entity), ADMIN)
    assert "amministratore" in str(refused.value)
    assert "società" in str(refused.value)
    assert db_session.query(StaffMember).count() == 0


def test_a_director_is_accepted_on_a_company(db_session: Session) -> None:
    entity = _entity(db_session, "it-company")
    read = StaffService(db_session).create_staff_member(_director(entity), ADMIN)
    assert (read.kind, read.annual_fee, read.fee_payment) == (
        "director",
        Decimal("24000.00"),
        "monthly",
    )
    assert (read.accrues_tfm, read.tfm_annual_amount) == (True, Decimal("2000.00"))
    assert read.gross_annual_pay is None and read.monthly_payments is None


def test_a_legal_entity_with_no_fiscal_profile_takes_nobody(db_session: Session) -> None:
    entity = _entity(db_session, None)
    with pytest.raises(ValidationFailed) as refused:
        StaffService(db_session).create_staff_member(_employee(entity), ADMIN)
    assert "profilo fiscale" in str(refused.value)


def test_an_unknown_legal_entity_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        StaffService(db_session).create_staff_member(_employee(uuid4()), ADMIN)


# --- the shape of a member ------------------------------------------------------------------


def test_the_create_schemas_refuse_what_the_kind_cannot_hold() -> None:
    entity = uuid4()
    with pytest.raises(ValueError, match="14|13"):
        _employee(entity, monthly_payments=12)
    with pytest.raises(ValueError, match="TFM"):
        _director(entity, accrues_tfm=True, tfm_annual_amount=None)
    with pytest.raises(ValueError, match="TFM"):
        _director(entity, accrues_tfm=False, tfm_annual_amount=Decimal("100"))
    with pytest.raises(ValueError, match="fine"):
        _employee(entity, end_date=D(2025, 12, 31))
    with pytest.raises(ValueError, match="insieme"):
        _employee(entity, pay_day=27)
    with pytest.raises(ValueError):
        _employee(entity, pay_day=29, pay_month_offset=0)


def test_a_rate_left_out_is_not_stated_and_never_a_default(db_session: Session) -> None:
    entity = _entity(db_session, "it-standard")
    read = StaffService(db_session).create_staff_member(
        _employee(entity, employer_inps_rate=None), ADMIN
    )
    assert read.employer_inps_rate is None and read.employer_inail_rate is None


def test_the_table_holds_the_kinds_own_columns_only(db_session: Session) -> None:
    """The database says it too, whatever the service does."""
    entity = _entity(db_session, "it-company")
    row = StaffMember(
        legal_entity_id=entity,
        kind="employee",
        name="X",
        start_date=D(2026, 1, 1),
        gross_annual_pay=Decimal("1000"),
        monthly_payments=13,
        part_time_percent=Decimal("100"),
        annual_fee=Decimal("5"),
    )
    db_session.add(row)
    with pytest.raises(IntegrityError, match="ck_staff_members_employee_columns"):
        db_session.flush()


def test_a_user_is_linked_to_at_most_one_member(db_session: Session, seeded_user_id: UUID) -> None:
    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    first = service.create_staff_member(_employee(entity, user_id=seeded_user_id), ADMIN)
    assert first.user_id == seeded_user_id
    with pytest.raises(Conflict) as refused:
        service.create_staff_member(_employee(entity, name="Altro", user_id=seeded_user_id), ADMIN)
    assert "già collegato" in str(refused.value)
    with pytest.raises(NotFound):
        service.create_staff_member(_employee(entity, name="Terzo", user_id=uuid4()), ADMIN)


# --- update ---------------------------------------------------------------------------------


def test_an_update_changes_only_what_it_names_and_never_the_kind(db_session: Session) -> None:
    entity = _entity(db_session, "it-company")
    service = StaffService(db_session)
    employee = service.create_staff_member(_employee(entity), ADMIN)
    director = service.create_staff_member(_director(entity), ADMIN)

    changed = service.update_staff_member(
        employee.id,
        StaffMemberUpdate(gross_annual_pay=Decimal("32000.00"), employer_inail_rate=None),
        ADMIN,
    )
    assert changed.gross_annual_pay == Decimal("32000.00")
    assert changed.monthly_payments == 14 and changed.employer_inps_rate == Decimal("30.00")

    with pytest.raises(ValidationFailed) as foreign:
        service.update_staff_member(employee.id, StaffMemberUpdate(annual_fee=Decimal("10")), ADMIN)
    assert "dipendente" in str(foreign.value)
    with pytest.raises(ValidationFailed) as foreign_director:
        service.update_staff_member(
            director.id, StaffMemberUpdate(gross_annual_pay=Decimal("10")), ADMIN
        )
    assert "amministratore" in str(foreign_director.value)


def test_an_update_cannot_empty_what_the_kind_needs(db_session: Session) -> None:
    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    employee = service.create_staff_member(_employee(entity), ADMIN)
    for field in ("gross_annual_pay", "monthly_payments", "name", "start_date"):
        with pytest.raises(ValidationFailed) as refused:
            service.update_staff_member(employee.id, StaffMemberUpdate(**{field: None}), ADMIN)
        assert "svuotato" in str(refused.value)
    assert service.get_staff_member(employee.id, ADMIN).gross_annual_pay == Decimal("30000.00")


def test_an_update_keeps_the_merged_row_consistent(db_session: Session) -> None:
    entity = _entity(db_session, "it-company")
    service = StaffService(db_session)
    director = service.create_staff_member(_director(entity), ADMIN)
    with pytest.raises(ValidationFailed, match="TFM"):
        service.update_staff_member(director.id, StaffMemberUpdate(accrues_tfm=False), ADMIN)
    off = service.update_staff_member(
        director.id, StaffMemberUpdate(accrues_tfm=False, tfm_annual_amount=None), ADMIN
    )
    assert (off.accrues_tfm, off.tfm_annual_amount) == (False, None)
    with pytest.raises(ValidationFailed, match="fine"):
        service.update_staff_member(director.id, StaffMemberUpdate(end_date=D(2025, 1, 1)), ADMIN)
    with pytest.raises(ValidationFailed, match="insieme"):
        service.update_staff_member(director.id, StaffMemberUpdate(pay_day=10), ADMIN)


def test_an_update_can_move_a_link_and_refuses_a_taken_user(
    db_session: Session, seeded_user_id: UUID
) -> None:
    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    first = service.create_staff_member(_employee(entity, user_id=seeded_user_id), ADMIN)
    second = service.create_staff_member(_employee(entity, name="Altro"), ADMIN)
    with pytest.raises(Conflict):
        service.update_staff_member(second.id, StaffMemberUpdate(user_id=seeded_user_id), ADMIN)
    # The member's own link is not «taken»: sending it back unchanged is fine.
    same = service.update_staff_member(first.id, StaffMemberUpdate(user_id=seeded_user_id), ADMIN)
    assert same.user_id == seeded_user_id
    freed = service.update_staff_member(first.id, StaffMemberUpdate(user_id=None), ADMIN)
    assert freed.user_id is None


# --- the pay day ----------------------------------------------------------------------------


def test_the_pay_day_is_the_legal_entitys_unless_the_member_overrides_it(
    db_session: Session,
) -> None:
    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    member = service.create_staff_member(_employee(entity), ADMIN)
    assert member.effective_pay_day is None
    assert service.get_payroll_pay_day(entity, ADMIN).pay_day is None

    service.set_payroll_pay_day(entity, PayDayWrite(pay_day=27, pay_month_offset=0), ADMIN)
    follows = service.get_staff_member(member.id, ADMIN).effective_pay_day
    assert follows is not None
    assert (follows.pay_day, follows.pay_month_offset, follows.source) == (27, 0, "legal_entity")

    own = service.update_staff_member(
        member.id, StaffMemberUpdate(pay_day=10, pay_month_offset=1), ADMIN
    )
    assert own.effective_pay_day is not None
    assert (own.effective_pay_day.pay_day, own.effective_pay_day.source) == (10, "staff_member")

    back = service.update_staff_member(
        member.id, StaffMemberUpdate(pay_day=None, pay_month_offset=None), ADMIN
    )
    assert back.effective_pay_day is not None and back.effective_pay_day.source == "legal_entity"

    moved = service.set_payroll_pay_day(entity, PayDayWrite(pay_day=15, pay_month_offset=1), ADMIN)
    assert (moved.pay_day, moved.pay_month_offset) == (15, 1)
    listed = service.list_staff(StaffListQuery(legal_entity_id=entity), ADMIN)
    assert listed.items[0].effective_pay_day is not None
    assert listed.items[0].effective_pay_day.pay_day == 15


def test_a_pay_day_cannot_be_set_for_a_legal_entity_abroad(db_session: Session) -> None:
    entity = _entity(db_session, "non-it")
    with pytest.raises(ValidationFailed):
        StaffService(db_session).set_payroll_pay_day(
            entity, PayDayWrite(pay_day=27, pay_month_offset=0), ADMIN
        )
    with pytest.raises(ValueError):
        PayDayWrite(pay_day=31, pay_month_offset=0)


# --- the list -------------------------------------------------------------------------------


def test_the_list_filters_pages_and_leaves_out_who_has_left(db_session: Session) -> None:
    entity = _entity(db_session, "it-company")
    other = _entity(db_session, "it-standard", "other")
    service = StaffService(db_session)
    names = ["Anna", "Bruno", "Carla"]
    for name in names:
        service.create_staff_member(_employee(entity, name=name), ADMIN)
    director = service.create_staff_member(_director(entity), ADMIN)
    service.create_staff_member(_employee(other, name="Altrove"), ADMIN)
    gone = service.create_staff_member(
        _employee(entity, name="Partito", start_date=D(2020, 1, 1), end_date=D(2021, 1, 1)), ADMIN
    )

    everyone = service.list_staff(StaffListQuery(legal_entity_id=entity), ADMIN)
    assert [m.name for m in everyone.items] == [*names, "Marco Rossi"]
    assert gone.id not in {m.id for m in everyone.items}
    with_ended = service.list_staff(
        StaffListQuery(legal_entity_id=entity, include_ended=True), ADMIN
    )
    assert gone.id in {m.id for m in with_ended.items}
    only_directors = service.list_staff(
        StaffListQuery(legal_entity_id=entity, kind="director"), ADMIN
    )
    assert [m.id for m in only_directors.items] == [director.id]

    first = service.list_staff(StaffListQuery(legal_entity_id=entity, limit=2), ADMIN)
    assert [m.name for m in first.items] == names[:2] and first.next_cursor is not None
    second = service.list_staff(
        StaffListQuery(legal_entity_id=entity, limit=2, cursor=first.next_cursor), ADMIN
    )
    assert [m.name for m in second.items] == ["Carla", "Marco Rossi"]
    assert second.next_cursor is None


# --- who may read and write pay -------------------------------------------------------------


def _every_call(service: StaffService, entity: UUID, member: UUID, actor: Actor) -> list[object]:
    """Every pay read and write the service has, as calls to try with one actor."""
    return [
        lambda: service.create_staff_member(_employee(entity, name="Nuovo"), actor),
        lambda: service.update_staff_member(
            member, StaffMemberUpdate(gross_annual_pay=Decimal("1.00")), actor
        ),
        lambda: service.get_staff_member(member, actor),
        lambda: service.list_staff(StaffListQuery(), actor),
        lambda: service.get_payroll_pay_day(entity, actor),
        lambda: service.set_payroll_pay_day(
            entity, PayDayWrite(pay_day=1, pay_month_offset=0), actor
        ),
    ]


@pytest.mark.parametrize("actor", [COLLABORATOR, READONLY], ids=["collaborator", "readonly"])
def test_a_collaborator_and_a_readonly_member_are_refused_every_pay_read_and_write(
    db_session: Session, actor: Actor
) -> None:
    """The card's «done when»: a member who is not an administrator reads no pay."""
    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    member = service.create_staff_member(_employee(entity), ADMIN)
    for call in _every_call(service, entity, member.id, actor):
        with pytest.raises(PermissionDenied):
            call()  # type: ignore[operator]
    assert service.get_staff_member(member.id, ADMIN).gross_annual_pay == Decimal("30000.00")
    assert db_session.query(StaffMember).count() == 1


def test_a_refused_member_learns_nothing_about_a_staff_member_that_does_not_exist(
    db_session: Session,
) -> None:
    """The role is asked before the lookup: the answer for a collaborator is the same for a
    real id and a made-up one."""
    service = StaffService(db_session)
    with pytest.raises(PermissionDenied):
        service.get_staff_member(uuid4(), COLLABORATOR)
    with pytest.raises(NotFound):
        service.get_staff_member(uuid4(), ADMIN)


@pytest.mark.parametrize(
    "actor", [AGENT_ADMIN, AGENT_COLLABORATOR_FULL], ids=["no-full-access", "not-an-admin"]
)
def test_a_token_without_full_access_or_without_an_admin_behind_it_is_refused_everything(
    db_session: Session, actor: Actor
) -> None:
    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    member = service.create_staff_member(_employee(entity), ADMIN)
    calls = _every_call(service, entity, member.id, actor)
    for call in calls:
        with pytest.raises((AgentForbidden, PermissionDenied)):
            call()  # type: ignore[operator]
    assert db_session.query(StaffMember).count() == 1


def test_a_full_access_admin_token_has_the_four_tools_and_not_the_pay_day_write(
    db_session: Session,
) -> None:
    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    created = service.create_staff_member(_employee(entity), AGENT_ADMIN_FULL)
    assert service.get_staff_member(created.id, AGENT_ADMIN_FULL).name == "Giulia Bianchi"
    updated = service.update_staff_member(
        created.id, StaffMemberUpdate(contract_level="4"), AGENT_ADMIN_FULL
    )
    assert updated.contract_level == "4"
    assert [m.id for m in service.list_staff(StaffListQuery(), AGENT_ADMIN_FULL).items] == [
        created.id
    ]
    # The pay day of a legal entity is a person's: read with the list, never written by a token.
    assert service.get_payroll_pay_day(entity, AGENT_ADMIN_FULL).pay_day is None
    with pytest.raises(AgentForbidden):
        service.set_payroll_pay_day(
            entity, PayDayWrite(pay_day=27, pay_month_offset=0), AGENT_ADMIN_FULL
        )


def test_a_change_leaves_a_timeline_entry_without_the_pay(db_session: Session) -> None:
    from pigrocrm.core.activities.models import Activity

    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    member = service.create_staff_member(_employee(entity), ADMIN)
    service.update_staff_member(
        member.id, StaffMemberUpdate(gross_annual_pay=Decimal("31000.00")), ADMIN
    )
    rows = (
        db_session.query(Activity)
        .filter(Activity.entity_type == "legal_entity", Activity.entity_id == entity)
        .order_by(Activity.occurred_at, Activity.id)
        .all()
    )
    assert rows == []
    own = (
        db_session.query(Activity)
        .filter(Activity.entity_type == "staff_member", Activity.entity_id == member.id)
        .order_by(Activity.occurred_at, Activity.id)
        .all()
    )
    assert [row.kind for row in own] == ["staff_member_created", "staff_member_updated"]
    for row in own:
        assert row.payload == {}


# --- what the space's read and the feed say about staff -------------------------------------


def _describe_staff_records(session: Session, actor: Actor | None) -> int:
    from pigrocrm.core.config import Settings
    from pigrocrm.core.modules.service import SpaceModulesService

    state = SpaceModulesService(session, Settings(_env_file=None)).describe(actor)  # type: ignore[call-arg]
    staff = next(module for module in state.modules if module.key == "staff")
    assert staff.holds_data is (staff.records > 0)
    return staff.records


def test_the_modules_read_hides_the_staff_head_count_from_whoever_may_not_list_staff(
    db_session: Session,
) -> None:
    entity = _entity(db_session, "it-standard")
    StaffService(db_session).create_staff_member(_employee(entity), ADMIN)

    assert _describe_staff_records(db_session, ADMIN) == 1
    assert _describe_staff_records(db_session, AGENT_ADMIN_FULL) == 1
    for hidden in (COLLABORATOR, READONLY, AGENT_ADMIN, AGENT_COLLABORATOR_FULL, None):
        assert _describe_staff_records(db_session, hidden) == 0


def test_switching_staff_off_still_asks_for_the_real_count(db_session: Session) -> None:
    from pigrocrm.core.config import Settings
    from pigrocrm.core.modules.schemas import SpaceModulesSet
    from pigrocrm.core.modules.service import SpaceModulesService

    entity = _entity(db_session, "it-standard")
    StaffService(db_session).create_staff_member(_employee(entity), ADMIN)
    service = SpaceModulesService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    with pytest.raises(Conflict) as refused:
        service.set_modules(SpaceModulesSet(off=["staff"]), ADMIN)
    assert "1 record" in str(refused.value)


def test_staff_entries_are_in_an_administrators_feed_only_while_the_module_is_on(
    db_session: Session,
) -> None:
    from pigrocrm.core.dashboard.service import DashboardService

    entity = _entity(db_session, "it-standard")
    service = StaffService(db_session)
    service.create_staff_member(_employee(entity), ADMIN)
    service.set_payroll_pay_day(entity, PayDayWrite(pay_day=27, pay_month_offset=0), ADMIN)

    def feed(actor: Actor, modules: frozenset[str]) -> set[str]:
        # The dashboard's own feed read; `get_operational_dashboard` opens a snapshot session
        # of its own, which a rolled-back test transaction cannot give it.
        rows = DashboardService(db_session)._recent_feed(actor, modules)
        return {row.entity_type for row in rows}

    with_staff = frozenset({"staff"})
    assert {"staff_member", "payroll_settings"} <= feed(ADMIN, with_staff)
    assert not {"staff_member", "payroll_settings"} & feed(ADMIN, frozenset())
    for other in (COLLABORATOR, READONLY, AGENT_ADMIN_FULL):
        assert not {"staff_member", "payroll_settings"} & feed(other, with_staff)
    # And never on the legal entity's own timeline, which every member reads.
    from pigrocrm.core.activities.service import ActivityService

    assert ActivityService(db_session).timeline("legal_entity", entity) == []
