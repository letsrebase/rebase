"""The inbox of incoming documents (REB-971, design 2026-11-09 § 2.1): upload, the list and one
item, and the file of an item. The inbox is core and always on, so no module guard. Bytes
never travel over MCP; this is the only door they use."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from starlette.datastructures import UploadFile as StarletteUploadFile

from pigrocrm.core.documents.schemas import SIZE_MAX
from pigrocrm.core.inbox.filetypes import ACCEPTED_DESCRIPTION
from pigrocrm.core.inbox.schemas import (
    InboxItemKind,
    InboxItemPage,
    InboxItemRead,
    InboxItemState,
    InboxListQuery,
    InboxUploadResult,
)
from pigrocrm.core.inbox.service import InboxService
from pigrocrm.core.inbox.uploads import UPLOAD_ACTION, InboxUploads
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep, get_actor
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import KeysetParams

router = APIRouter(
    prefix="/api/inbox",
    tags=["inbox"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)

# The multipart body is read by the handler after the role check, not declared as `File()`
# parameters: FastAPI parses a declared body before it calls the handler and Starlette spools
# every file part to disk while it does, so a `readonly` caller would have had up to the
# proxy's limit written before the 403 (`uploads.py`). The schema is stated by hand.
_UPLOAD_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": f"One part per file: {ACCEPTED_DESCRIPTION}.",
                    }
                },
            }
        }
    },
}


@router.post(
    "/upload",
    response_model=InboxUploadResult,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={"requestBody": _UPLOAD_BODY},
)
async def upload(
    request: Request, session: SessionDep, storage: StorageDep, actor: ActorDep
) -> InboxUploadResult:
    """Puts every file of the request in the inbox, each one item: new, a duplicate of the item
    that came first, or discarded with its reason. A ZIP is kept as the parent of its entries.
    All or nothing for a request or a loose file the inbox does not take (422 with the reason);
    the body is not read before the role is checked."""
    actor.require_write(UPLOAD_ACTION)
    form = await request.form()
    files = [
        (part.filename or "senza-nome", await part.read(SIZE_MAX + 1))
        for part in form.getlist("file")
        if isinstance(part, StarletteUploadFile)
    ]
    return await run_in_threadpool(InboxUploads(session, storage).upload, files, actor)


class InboxListParams(KeysetParams):
    state: InboxItemState | None = None
    kind: InboxItemKind | None = None
    parent_id: UUID | None = None


@router.get("/items", response_model=InboxItemPage)
def list_items(
    session: SessionDep, actor: ActorDep, params: Annotated[InboxListParams, Query()]
) -> InboxItemPage:
    return InboxService(session).list(params.to_query(InboxListQuery), actor)


@router.get("/items/{item_id}", response_model=InboxItemRead)
def get_item(item_id: UUID, session: SessionDep, actor: ActorDep) -> InboxItemRead:
    return InboxService(session).get(item_id, actor)


# The 200 names a body, since a response with none types every test that spies on `api.GET`
# against a union that no mock of a JSON answer fits (REB-971).
_FILE_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The file, as it was uploaded",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


@router.get("/items/{item_id}/file", response_class=Response, responses=_FILE_RESPONSE)
def download_item(
    item_id: UUID, session: SessionDep, storage: StorageDep, actor: ActorDep
) -> Response:
    """The file of an item, as it was uploaded. An item that holds no bytes (a duplicate of
    the same bytes) answers 404: its original has them."""
    data, content_type, filename = InboxUploads(session, storage).download(item_id, actor)
    return attachment_response(data, content_type, filename)
