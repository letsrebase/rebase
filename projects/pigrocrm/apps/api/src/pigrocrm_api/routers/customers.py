from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.customers.schemas import (
    CustomerCreate,
    CustomerListQuery,
    CustomerPage,
    CustomerRead,
    CustomersFromSuggestions,
    CustomerUpdate,
)
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import CUSTOM_QUERY_DESCRIPTION, parse_custom_filter

router = APIRouter(prefix="/api/customers", tags=["customers"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create(data: CustomerCreate, session: SessionDep, actor: ActorDep) -> CustomerRead:
    return CustomerService(session).create(data, actor)


# The customers a person ticked among the Gmail proposals (REB-223), created with their
# people in one transaction. Its own path rather than a list body on `POST ""`: the
# analytics table counts it as `clienti_importati`, not as one `cliente_creato`.
@router.post(
    "/from-suggestions", response_model=list[CustomerRead], status_code=status.HTTP_201_CREATED
)
def create_from_suggestions(
    data: CustomersFromSuggestions, session: SessionDep, actor: ActorDep
) -> list[CustomerRead]:
    return CustomerService(session).create_from_suggestions(data, actor)


@router.get("", response_model=CustomerPage)
def list_customers(
    session: SessionDep,
    actor: ActorDep,
    # SafeStr here, not just on Create/Update: `search`/`stato`/`custom` are
    # ordinary FastAPI query parameters, so a NUL byte in one of them is caught by
    # FastAPI's own request-parameter validation (a clean 422) *before* this
    # function body ever runs and hand-constructs CustomerListQuery below --
    # unlike putting SafeStr only on CustomerListQuery's own fields, which would
    # not help here: this route never lets pydantic validate that construction
    # for it the way it does for a request-body model, so a ValidationError raised
    # there would surface as an uncaught 500, not a 422. Verified directly: a NUL
    # byte in `search` now comes back 422, not 500, with no other change needed.
    search: Annotated[SafeStr | None, Query()] = None,
    stato: Annotated[SafeStr | None, Query()] = None,
    custom: Annotated[list[SafeStr] | None, Query(description=CUSTOM_QUERY_DESCRIPTION)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    # `str`, not `UUID`, since slice 6: the cursor encodes `(sort value, id)` so that a
    # scan ordered by a non-unique or nullable column can resume. Opaque by design --
    # clients echo `next_cursor` back and never parse it. `max_length` here as well as
    # on the schema, so an oversized value is refused by FastAPI before the decoder
    # sees it.
    cursor: Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)] = None,
    # A plain string, not a `Literal` of the three keys: the whitelist lives in
    # `CUSTOMER_SORTS` and answers with this project's own `ValidationFailed`, whose
    # `field` key both the web client's `fieldErrorFrom` and an MCP agent read. A
    # `Literal` here would answer with FastAPI's `HTTPValidationError` shape instead,
    # which neither of them parses -- so the admissible values go in the description,
    # where they reach the OpenAPI document and the generated client's JSDoc.
    sort: Annotated[
        SafeStr | None, Query(description="created_at | updated_at | ragione_sociale")
    ] = None,
    dir: Annotated[SortDirection, Query()] = "asc",
) -> CustomerPage:
    query = CustomerListQuery(
        search=search,
        stato=stato,
        custom=parse_custom_filter(custom),
        limit=limit,
        cursor=cursor,
        sort=sort,
        dir=dir,
    )
    return CustomerService(session).list(query, actor)


@router.get("/{customer_id}", response_model=CustomerRead)
def get(customer_id: UUID, session: SessionDep, actor: ActorDep) -> CustomerRead:
    return CustomerService(session).get(customer_id, actor)


@router.patch("/{customer_id}", response_model=CustomerRead)
def update(
    customer_id: UUID, data: CustomerUpdate, session: SessionDep, actor: ActorDep
) -> CustomerRead:
    return CustomerService(session).update(customer_id, data, actor)


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(customer_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    """Sets deleted_at. Nothing in this slice removes a row."""
    CustomerService(session).soft_delete(customer_id, actor)


@router.post("/{customer_id}/restore", response_model=CustomerRead)
def restore(customer_id: UUID, session: SessionDep, actor: ActorDep) -> CustomerRead:
    return CustomerService(session).restore(customer_id, actor)


@router.get("/{customer_id}/timeline", response_model=list[ActivityRead])
def timeline(
    customer_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActivityRead]:
    return ActivityService(session).timeline("customer", customer_id, limit)
