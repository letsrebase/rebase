from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from pigrocrm.core.staff.schemas import (
    Kind,
    PayDayRead,
    PayDayWrite,
    StaffListQuery,
    StaffMemberCreate,
    StaffMemberRead,
    StaffMemberUpdate,
    StaffPage,
)
from pigrocrm.core.staff.service import StaffService
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import LEGAL_ENTITY_QUERY_DESCRIPTION, KeysetParams

# Staff and their pay are an administrator's, on every route here: the service asks for the
# role first, so a collaborator or a readonly member is refused before anything is looked up.
router = APIRouter(
    prefix="/api/staff",
    tags=["staff"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("staff")],
)


class StaffListParams(KeysetParams):
    legal_entity_id: UUID | None = Field(default=None, description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    kind: Kind | None = None
    include_ended: bool = Field(
        default=False,
        description="Also the staff whose end date is past; omitted, who is in service",
    )


@router.post("", response_model=StaffMemberRead, status_code=status.HTTP_201_CREATED)
def create_staff_member(
    data: StaffMemberCreate, session: SessionDep, actor: ActorDep
) -> StaffMemberRead:
    """An employee or a director of a legal entity, told apart by `kind`. Directors only on
    a company; none on a legal entity abroad."""
    return StaffService(session).create_staff_member(data, actor)


@router.get("", response_model=StaffPage)
def list_staff(
    session: SessionDep, actor: ActorDep, params: Annotated[StaffListParams, Query()]
) -> StaffPage:
    return StaffService(session).list_staff(params.to_query(StaffListQuery), actor)


@router.get("/pay-days/{legal_entity_id}", response_model=PayDayRead)
def get_pay_day(legal_entity_id: UUID, session: SessionDep, actor: ActorDep) -> PayDayRead:
    """The legal entity's own pay day; both fields are null until one is set."""
    return StaffService(session).get_payroll_pay_day(legal_entity_id, actor)


@router.put("/pay-days/{legal_entity_id}", response_model=PayDayRead)
def set_pay_day(
    legal_entity_id: UUID, data: PayDayWrite, session: SessionDep, actor: ActorDep
) -> PayDayRead:
    """Sets the day net pay leaves for every staff member of the legal entity without an
    override of their own. A person's act: no agent credential."""
    return StaffService(session).set_payroll_pay_day(legal_entity_id, data, actor)


@router.get("/{staff_member_id}", response_model=StaffMemberRead)
def get_staff_member(
    staff_member_id: UUID, session: SessionDep, actor: ActorDep
) -> StaffMemberRead:
    return StaffService(session).get_staff_member(staff_member_id, actor)


@router.patch("/{staff_member_id}", response_model=StaffMemberRead)
def update_staff_member(
    staff_member_id: UUID, data: StaffMemberUpdate, session: SessionDep, actor: ActorDep
) -> StaffMemberRead:
    """Changes what is named. The kind and the legal entity never change; a field of the
    other kind is refused."""
    return StaffService(session).update_staff_member(staff_member_id, data, actor)
