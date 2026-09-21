from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel

from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection
from pigrocrm.core.documents.schemas import (
    DocumentCreate,
    DocumentFromTemplate,
    DocumentListQuery,
    DocumentPage,
    DocumentRead,
    DocumentTextRead,
    DocumentTipo,
    DocumentUpdate,
    DocumentVersionRead,
    OfferState,
)
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, StorageDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/documents", tags=["documents"], responses=PROBLEM_RESPONSES)


class OfferStateBody(BaseModel):
    stato: OfferState


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


@router.get("", response_model=DocumentPage)
def list_documents(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    customer_id: Annotated[UUID | None, Query()] = None,
    deal_id: Annotated[UUID | None, Query()] = None,
    tipo: Annotated[DocumentTipo | None, Query()] = None,
    stato: Annotated[OfferState | None, Query()] = None,
    # New in slice 6 (spec §8.1). `SafeStr` for the same reason the other three lists
    # carry it on `search`: this is an ordinary query parameter, so the guard has to
    # sit on the parameter itself for FastAPI's own validation to answer 422 before
    # `DocumentListQuery` is hand-built below.
    search: Annotated[SafeStr | None, Query()] = None,
    # The drill-through of the commercial dashboard's "offerta accettata, deal non vinto"
    # card (§6.2). The card links here and nowhere else, and the rows returned are counted
    # by the same predicate function the card's `COUNT` uses.
    solo_deal_non_vinto: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    # `str`, not `UUID`, since slice 6 -- see the identical comment on list_customers
    # (routers/customers.py).
    cursor: Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)] = None,
    # A plain string and not a `Literal` -- see the reasoning on list_customers
    # (routers/customers.py).
    sort: Annotated[SafeStr | None, Query(description="created_at | updated_at | titolo")] = None,
    dir: Annotated[SortDirection, Query()] = "asc",
) -> DocumentPage:
    query = DocumentListQuery(
        customer_id=customer_id,
        deal_id=deal_id,
        tipo=tipo,
        stato=stato,
        search=search,
        solo_deal_non_vinto=solo_deal_non_vinto,
        limit=limit,
        cursor=cursor,
        sort=sort,
        dir=dir,
    )
    return DocumentService(session, storage, settings).list(query, actor)


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
        document_id, body.stato, actor
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
)
async def upload_version(
    document_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    file: Annotated[UploadFile, File()],
) -> DocumentVersionRead:
    """`file.content_type` is a client-supplied string and is validated against
    `ALLOWED_CONTENT_TYPES` inside the service, never trusted here."""
    data = await file.read()
    return DocumentService(session, storage, settings).add_version(
        document_id, data, file.content_type or "application/octet-stream", actor
    )


@router.post(
    "/{document_id}/versions/{numero}/regenerate",
    response_model=DocumentVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def regenerate(
    document_id: UUID,
    numero: int,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> DocumentVersionRead:
    return DocumentService(session, storage, settings).regenerate(document_id, numero, actor)


@router.get("/{document_id}/download")
def download(
    document_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    numero: Annotated[int | None, Query(ge=1, le=100_000)] = None,
) -> Response:
    """The download always goes through the API: it is the only place authorisation
    exists, on both storage backends (spec 5).

    The filename is percent-encoded into `filename*` (RFC 5987) rather than
    interpolated into `filename=`. The service already slugified it, so nothing
    dangerous should reach here -- encoding it anyway means a future change to that
    slug cannot turn a document title into a response header.
    """
    data, content_type, filename = DocumentService(session, storage, settings).download(
        document_id, numero, actor
    )
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            # A stored file is served from the app's own origin; without this a
            # browser may sniff a benign content type into something executable.
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{document_id}/text", response_model=DocumentTextRead)
def testo(
    document_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    numero: Annotated[int | None, Query(ge=1, le=100_000)] = None,
) -> DocumentTextRead:
    """The text of the file, for a reader that cannot open a PDF -- an agent, or a
    preview panel that would otherwise have to embed one.

    A separate route from `/download` rather than a `?formato=testo` on it: the two
    return different things (a JSON body against an attachment with its own
    `Content-Disposition`), and a single handler that branched on a query parameter
    would be a route whose response type nobody can state. `numero` carries the same
    bounds as the download's, from the same reasoning.
    """
    return DocumentService(session, storage, settings).extract_text(document_id, numero, actor)


@router.get("/{document_id}/timeline", response_model=list[ActivityRead])
def timeline(
    document_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActivityRead]:
    return ActivityService(session).timeline("document", document_id, limit)
