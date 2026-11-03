from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pigrocrm.core.documents.schemas import (
    SIZE_MAX,
    DocumentCreate,
    DocumentFromTemplate,
    DocumentListQuery,
    DocumentPage,
    DocumentRead,
    DocumentTextRead,
    DocumentType,
    DocumentUpdate,
    DocumentVersionRead,
    OfferState,
)
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.documents.uploads import ADD_VERSION_ACTION
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, StorageDep, get_actor
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import (
    LEGAL_ENTITY_QUERY_DESCRIPTION,
    SearchParams,
    SortedParams,
    sort_field,
)
from pigrocrm_api.timeline import timeline_router
from pigrocrm_api.uploads import read_upload, single_file_body

router = APIRouter(
    prefix="/api/documents",
    tags=["documents"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


class OfferStateBody(BaseModel):
    status: OfferState


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create(
    data: DocumentCreate,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> DocumentRead:
    return DocumentService(session, storage, settings).create(data, actor)


@router.post("/from-template", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_from_template(
    data: DocumentFromTemplate,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> DocumentRead:
    """Renders synchronously: a few pages compose in well under a second, and a job
    queue is complexity that does not pay for itself today (spec 6)."""
    return DocumentService(session, storage, settings).create_from_template(data, actor)


class DocumentListParams(SortedParams, SearchParams):
    customer_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    legal_entity_id: UUID | None = Field(default=None, description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    type: DocumentType | None = None
    status: OfferState | None = None
    # The drill-through of the commercial dashboard's "offerta accettata, deal non vinto"
    # card (§6.2). The card links here and nowhere else, and the rows returned are counted
    # by the same predicate function the card's `COUNT` uses.
    only_unwon_deals: bool = False
    sort: SafeStr | None = sort_field("created_at", "updated_at", "title")


@router.get("", response_model=DocumentPage)
def list_documents(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    params: Annotated[DocumentListParams, Query()],
) -> DocumentPage:
    return DocumentService(session, storage, settings).list(
        params.to_query(DocumentListQuery), actor
    )


@router.get("/{document_id}", response_model=DocumentRead)
def get(
    document_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> DocumentRead:
    return DocumentService(session, storage, settings).get(document_id, actor)


@router.patch("/{document_id}", response_model=DocumentRead)
def update(
    document_id: UUID,
    data: DocumentUpdate,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> DocumentRead:
    return DocumentService(session, storage, settings).update(document_id, data, actor)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(
    document_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> None:
    """Sets deleted_at. The stored bytes are left alone -- see
    DocumentService.soft_delete's own docstring."""
    DocumentService(session, storage, settings).soft_delete(document_id, actor)


@router.post("/{document_id}/restore", response_model=DocumentRead)
def restore(
    document_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> DocumentRead:
    return DocumentService(session, storage, settings).restore(document_id, actor)


@router.post("/{document_id}/status", response_model=DocumentRead)
def set_offer_state(
    document_id: UUID,
    body: OfferStateBody,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> DocumentRead:
    return DocumentService(session, storage, settings).set_offer_state(
        document_id, body.status, actor
    )


@router.get("/{document_id}/versions", response_model=list[DocumentVersionRead])
def versions(
    document_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> list[DocumentVersionRead]:
    return DocumentService(session, storage, settings).versions(document_id, actor)


@router.post(
    "/{document_id}/versions",
    response_model=DocumentVersionRead,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=single_file_body("The new version's file."),
)
async def upload_version(
    document_id: UUID,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> DocumentVersionRead:
    """`file.content_type` is a client-supplied string and is validated against
    `ALLOWED_CONTENT_TYPES` inside the service, never trusted here. The role is checked
    before the body is read (`uploads.py`); the service checks it again."""
    actor.require_write(ADD_VERSION_ACTION)
    data, content_type = await read_upload(request, SIZE_MAX)
    return await run_in_threadpool(
        DocumentService(session, storage, settings).add_version,
        document_id,
        data,
        content_type or "application/octet-stream",
        actor,
    )


@router.post(
    "/{document_id}/versions/{number}/regenerate",
    response_model=DocumentVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def regenerate(
    document_id: UUID,
    number: int,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> DocumentVersionRead:
    return DocumentService(session, storage, settings).regenerate(document_id, number, actor)


@router.get("/{document_id}/download")
def download(
    document_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    number: Annotated[int | None, Query(ge=1, le=100_000)] = None,
) -> Response:
    """The download always goes through the API: it is the only place authorisation
    exists, on both storage backends (spec 5).
    """
    data, content_type, filename = DocumentService(session, storage, settings).download(
        document_id, number, actor
    )
    return attachment_response(data, content_type, filename)


@router.get("/{document_id}/text", response_model=DocumentTextRead)
def text(
    document_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    number: Annotated[int | None, Query(ge=1, le=100_000)] = None,
) -> DocumentTextRead:
    """The text of the file, for a reader that cannot open a PDF -- an agent, or a
    preview panel that would otherwise have to embed one.

    A separate route from `/download` rather than a `?format=text` on it: the two
    return different things (a JSON body against an attachment with its own
    `Content-Disposition`), and a single handler that branched on a query parameter
    would be a route whose response type nobody can state. `number` carries the same
    bounds as the download's, from the same reasoning.
    """
    return DocumentService(session, storage, settings).extract_text(document_id, number, actor)


router.include_router(timeline_router("document", id_param="document_id"))
