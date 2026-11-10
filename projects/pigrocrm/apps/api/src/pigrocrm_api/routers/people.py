from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pigrocrm.core.people.schemas import (
    PersonCreate,
    PersonListQuery,
    PersonPage,
    PersonRead,
    PersonUpdate,
)
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import CustomParams, SearchParams, SortedParams, sort_field
from pigrocrm_api.timeline import timeline_router

router = APIRouter(
    prefix="/api/people",
    tags=["people"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=PersonRead, status_code=status.HTTP_201_CREATED)
def create(data: PersonCreate, session: SessionDep, actor: ActorDep) -> PersonRead:
    return PersonService(session).create(data, actor)


class PersonListParams(SortedParams, SearchParams, CustomParams):
    customer_id: UUID | None = None
    # `last_name` is the one nullable sort column in the whole whitelist.
    sort: SafeStr | None = sort_field("created_at", "updated_at", "last_name")


@router.get("", response_model=PersonPage)
def list_people(
    session: SessionDep, actor: ActorDep, params: Annotated[PersonListParams, Query()]
) -> PersonPage:
    return PersonService(session).list(params.to_query(PersonListQuery), actor)


@router.get("/{person_id}", response_model=PersonRead)
def get(person_id: UUID, session: SessionDep, actor: ActorDep) -> PersonRead:
    return PersonService(session).get(person_id, actor)


@router.patch("/{person_id}", response_model=PersonRead)
def update(person_id: UUID, data: PersonUpdate, session: SessionDep, actor: ActorDep) -> PersonRead:
    return PersonService(session).update(person_id, data, actor)


@router.delete("/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(person_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    PersonService(session).soft_delete(person_id, actor)


@router.post("/{person_id}/restore", response_model=PersonRead)
def restore(person_id: UUID, session: SessionDep, actor: ActorDep) -> PersonRead:
    return PersonService(session).restore(person_id, actor)


router.include_router(timeline_router("person", id_param="person_id"))
