"""One endpoint. The palette calls it on every keystroke above three characters, so it is
the hottest read path in the product.

No transaction wrapper and no isolation level: see `SearchService`'s own docstring for why.
The dashboards are the surface that needs one instant (spec §7.1); a search does not, and
putting `REPEATABLE READ` here would pay for a property nothing reads.

No authorisation gate either, and that is a decision rather than an omission. Spec §13 adds
no role and no rule in this slice, and search reads exactly the rows the five list endpoints
already return to every role, `readonly` included. A check here would put a security rule on
a read-only surface, which is the place nobody looks for one.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from pigrocrm.core.search.schemas import (
    MAX_TERM_LENGTH,
    MIN_TERM_LENGTH,
    PER_CLASS_LIMIT,
    SearchQuery,
    SearchResults,
)
from pigrocrm.core.search.service import SearchService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/search", tags=["search"], responses=PROBLEM_RESPONSES)


@router.get("", response_model=SearchResults)
def search(
    session: SessionDep,
    actor: ActorDep,
    # `q` in the query string, `termine` in the schema: spec §11.2's endpoint block fixes
    # the parameter name and §8 fixes the field name, and they differ. Renaming either to
    # match the other would put one of the two specs out of date silently.
    #
    # The bounds are repeated here as well as on `SearchQuery` so FastAPI answers a
    # two-character term with a 422 before the service is constructed -- the palette does
    # not send one (§8.3 stops it in the browser), but an HTTP client will. They are also
    # the only place the bounds reach the OpenAPI document, which is what task A12's
    # generated client is built from; `apps/api/tests/test_search_api.py` asserts them on
    # the document itself, because `openapi-typescript` erases `minLength`/`maxLength`.
    #
    # `SafeStr` and not `str`: a free-text query parameter is user-supplied text like any
    # other, and a NUL byte in it would reach `ILIKE` and come back from the driver as a
    # raw `DataError` with the session poisoned.
    q: Annotated[SafeStr, Query(min_length=MIN_TERM_LENGTH, max_length=MAX_TERM_LENGTH)],
    limit: Annotated[int, Query(ge=1, le=20)] = PER_CLASS_LIMIT,
    azienda_id: Annotated[
        UUID | None, Query(description="Solo i risultati di questa azienda; omesso, tutti")
    ] = None,
) -> SearchResults:
    return SearchService(session).search_everything(
        SearchQuery(termine=q, limite=limit, azienda_id=azienda_id), actor
    )
