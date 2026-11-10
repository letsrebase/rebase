"""The inbox of incoming documents (REB-971, design 2026-11-09 § 2.1): upload, the list and one
item, and the file of an item. The inbox is core and always on, so no module guard. Bytes
never travel over MCP; this is the only door they use."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from pigrocrm.core.actor import Actor
from pigrocrm.core.ai.gateway import ModelGateway
from pigrocrm.core.ai.transport import ModelCall
from pigrocrm.core.config import Settings
from pigrocrm.core.documents.schemas import SIZE_MAX
from pigrocrm.core.inbox.classifier import InboxClassifier
from pigrocrm.core.inbox.filetypes import ACCEPTED_DESCRIPTION
from pigrocrm.core.inbox.issued import InboxIssuedImport
from pigrocrm.core.inbox.schemas import (
    InboxDiscard,
    InboxDuplicate,
    InboxItemDetail,
    InboxItemKind,
    InboxItemPage,
    InboxItemRead,
    InboxItemState,
    InboxKindSet,
    InboxListQuery,
    InboxUploadResult,
)
from pigrocrm.core.inbox.service import InboxService
from pigrocrm.core.inbox.uploads import UPLOAD_ACTION, InboxUploads
from pigrocrm.core.invoices.import_upload import (
    UploadedInvoiceConfirmResult,
    UploadedInvoiceDecisions,
    UploadedInvoiceReviewResult,
)
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm_api.deps import (
    ActorDep,
    ModelCallDep,
    SessionDep,
    SettingsDep,
    StorageDep,
    get_actor,
)
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import KeysetParams

MODEL_READS_PER_UPLOAD = 5
"""How many files one request sends to the model: each is a call that can take a while and
the request waits for it."""

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
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    actor: ActorDep,
    settings: SettingsDep,
    call: ModelCallDep,
    read: Annotated[
        bool,
        Query(
            description=(
                "`false` stores the files and reads none: no classification, no model call, "
                "and the items are answered as the upload made them (new, duplicate or "
                "discarded). The caller files them itself, for example a receipt photo "
                "that is only the justification of the purchase it registers. "
                "Default `true`."
            )
        ),
    ] = True,
) -> InboxUploadResult:
    """Puts every file of the request in the inbox, each one item: new, a duplicate of the item
    that came first, or discarded with its reason. A ZIP is kept as the parent of its entries.
    All or nothing for a request or a loose file the inbox does not take (422 with the reason);
    the body is not read before the role is checked. Every new item is then read once
    (`InboxClassifier`): a FatturaPA is routed, a PDF or a photo becomes a proposal when the
    space's model is on, and at most five files go to the model in one request:
    `POST /api/inbox/classify` reads the rest. With `read=false` none is read."""
    actor.require_write(UPLOAD_ACTION)
    form = await request.form()
    files = [
        (part.filename or "senza-nome", await part.read(SIZE_MAX + 1))
        for part in form.getlist("file")
        if isinstance(part, StarletteUploadFile)
    ]
    return await run_in_threadpool(
        _upload_and_classify, session, storage, settings, call, files, actor, read
    )


def _upload_and_classify(
    session: Session,
    storage: DocumentStorage,
    settings: Settings,
    call: ModelCall,
    files: list[tuple[str, bytes]],
    actor: Actor,
    read: bool,
) -> InboxUploadResult:
    uploaded = InboxUploads(session, storage).upload(files, actor)
    if not read:
        return uploaded
    classifier = InboxClassifier(session, storage, ModelGateway(session, settings, call))
    return classifier.classify_uploaded(uploaded, actor, model_budget=MODEL_READS_PER_UPLOAD)


@router.post("/classify", response_model=InboxItemPage)
def classify_pending(
    session: SessionDep,
    storage: StorageDep,
    actor: ActorDep,
    settings: SettingsDep,
    call: ModelCallDep,
    limit: Annotated[int, Query(ge=1, le=MODEL_READS_PER_UPLOAD)] = MODEL_READS_PER_UPLOAD,
) -> InboxItemPage:
    """Reads, with the space's model, the oldest items nobody has said what they are yet:
    the PDFs and photos an upload left because there were more than it reads in one request,
    or because the model was off or unavailable then. Answers the items it read."""
    classifier = InboxClassifier(session, storage, ModelGateway(session, settings, call))
    return classifier.classify_pending(actor, limit=limit)


class InboxListParams(KeysetParams):
    state: InboxItemState | None = None
    kind: InboxItemKind | None = None
    classified: bool | None = None
    parent_id: UUID | None = None


@router.get("/items", response_model=InboxItemPage)
def list_items(
    session: SessionDep, actor: ActorDep, params: Annotated[InboxListParams, Query()]
) -> InboxItemPage:
    return InboxService(session).list(params.to_query(InboxListQuery), actor)


@router.get("/items/{item_id}", response_model=InboxItemDetail)
def get_item(item_id: UUID, session: SessionDep, actor: ActorDep) -> InboxItemDetail:
    return InboxService(session).get(item_id, actor)


@router.post("/items/{item_id}/discard", response_model=InboxItemRead)
def discard_item(
    item_id: UUID, data: InboxDiscard, session: SessionDep, actor: ActorDep
) -> InboxItemRead:
    """Puts an item that waits aside, with the reason. It stays in the inbox, readable, with
    its file; a proposal waiting on it is rejected. Answers the item as it is when it was
    already discarded; 409 for one that was filed or is a duplicate."""
    return InboxService(session).discard(item_id, data, actor)


@router.post("/items/{item_id}/duplicate", response_model=InboxItemRead)
def mark_item_duplicate(
    item_id: UUID, data: InboxDuplicate, session: SessionDep, actor: ActorDep
) -> InboxItemRead:
    """Says an item that waits is a copy of `original_id`, an item the caller sees that is not a
    duplicate itself. Answers the item as it is when it already was that copy's."""
    return InboxService(session).mark_duplicate(item_id, data, actor)


@router.post("/items/{item_id}/kind", response_model=InboxItemRead)
def set_item_kind(
    item_id: UUID, data: InboxKindSet, session: SessionDep, actor: ActorDep
) -> InboxItemRead:
    """Says what an item that waits is, and for which legal entity. Nothing becomes a record:
    the item waits for the module that files that kind. A kind that a FatturaPA decided by
    itself is not changed (409), and a proposal waiting on the item is rejected."""
    return InboxService(session).set_kind(item_id, data, actor)


@router.post("/items/{item_id}/classify", response_model=InboxItemRead)
def classify_item(
    item_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    actor: ActorDep,
    settings: SettingsDep,
    call: ModelCallDep,
) -> InboxItemRead:
    """Reads one item that has no kind yet (the model was off, over its cap or down when it
    arrived). An item already read is answered as it is."""
    classifier = InboxClassifier(session, storage, ModelGateway(session, settings, call))
    return classifier.classify(item_id, actor)


@router.get("/items/{item_id}/issued-review", response_model=UploadedInvoiceReviewResult)
def issued_review(
    item_id: UUID, session: SessionDep, storage: StorageDep, actor: ActorDep, settings: SettingsDep
) -> UploadedInvoiceReviewResult:
    """The issued-invoice review of a sales invoice that waits in the inbox: what the import
    would do with each of its invoices, writing nothing."""
    rows = InboxIssuedImport(session, storage, settings).review(item_id, actor)
    return UploadedInvoiceReviewResult(lines=rows)


@router.post("/items/{item_id}/issued-import", response_model=UploadedInvoiceConfirmResult)
def issued_import(
    item_id: UUID,
    data: UploadedInvoiceDecisions,
    session: SessionDep,
    storage: StorageDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> UploadedInvoiceConfirmResult:
    """A person's confirmation of that review: writes the invoices on the register (it
    consumes a number, so it is not an agent's by default) and files the item."""
    rows = InboxIssuedImport(session, storage, settings).confirm(item_id, data.organizations, actor)
    return UploadedInvoiceConfirmResult(lines=rows)


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
