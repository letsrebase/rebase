"""The accountant's period export (REB-1011, REB-1012; spec accountant export § 1 and § 3).

Thin, like every router here: resolve the actor, call `AccountantExportService`. A build
stores the export (a row and its ZIP parts in the space's storage) and answers where each
part can be fetched; the parts are served from the storage, an export past 13 months has no
files. Core in every space, the way invoices are: which parts the bundle holds follows the
modules and the entity's pack, and the index inside names the parts it left out. The check
that decides who may ask (an admin whose scope covers the entity, an unscoped one for every
entity) is the service's.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pigrocrm.core.accountant_export.parts import Period
from pigrocrm.core.accountant_export.schemas import ExportListRead, ExportRead, PartPreviewRead
from pigrocrm.core.accountant_export.service import ZIP_MIME, AccountantExportService
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, StorageDep, get_actor
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/accountant-export",
    tags=["accountant-export"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


class ExportBuild(BaseModel):
    date_from: date = Field(description="First day of the period, included")
    date_to: date = Field(description="Last day of the period, included; at most 366 days")
    legal_entity_id: UUID | None = Field(
        default=None,
        description="The legal entity to export; omitted, the only one of a one-entity space",
    )
    all_entities: bool = Field(
        default=False,
        description=(
            "Every legal entity plus the shared purchases, in one bundle; only an admin whose "
            "view is not limited to some legal entities, and not together with legal_entity_id"
        ),
    )


def _service(
    session: SessionDep, storage: StorageDep, settings: SettingsDep
) -> AccountantExportService:
    return AccountantExportService(session, storage, settings)


@router.post("/exports", status_code=201)
def build_export(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    body: ExportBuild,
) -> ExportRead:
    """Builds the period's export and stores it, one ZIP or several when it is too big for
    one file: the sales register with its VAT columns, the invoices issued and received with
    their files, the receipts and expenses, the open due items and the payments of the period,
    and an index naming every part with its row count, every part left out with its reason and
    every part whose source does not exist yet. Building the same period again keeps both
    exports; each is deleted after 13 months."""
    stored = _service(session, storage, settings).build_accountant_export(
        Period(body.date_from, body.date_to),
        actor,
        body.legal_entity_id,
        all_entities=body.all_entities,
    )
    return stored.export


@router.get("/parts")
def describe_parts(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    legal_entity_id: UUID | None = None,
    all_entities: bool = False,
) -> list[PartPreviewRead]:
    """The parts a build for this company would hold, from its modules and fiscal pack, in
    the order of the index. With `all_entities`, a part that applies to any company."""
    return _service(session, storage, settings).describe_parts(
        actor, legal_entity_id, all_entities=all_entities
    )


@router.get("/exports")
def list_exports(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    legal_entity_id: UUID | None = None,
    year: int | None = Query(
        default=None, ge=1900, le=2200, description="Only exports whose period starts that year"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> ExportListRead:
    """The stored exports, newest first, with how many there are in all. One past 13 months
    stays listed, with no files."""
    service = _service(session, storage, settings)
    return ExportListRead(
        items=service.list_exports(actor, legal_entity_id, year=year, offset=offset, limit=limit),
        total_count=service.count_exports(actor, legal_entity_id, year=year),
    )


@router.get("/exports/{export_id}")
def get_export(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    export_id: UUID,
) -> ExportRead:
    return _service(session, storage, settings).get_export_download_url(export_id, actor)


@router.get(
    "/exports/{export_id}/parts/{part}",
    response_class=Response,
    responses={200: {"content": {ZIP_MIME: {}}, "description": "One ZIP of the export"}},
)
def download_export_part(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    export_id: UUID,
    part: int,
) -> Response:
    """One ZIP of a stored export. A bundle too big for one file is several, each a complete
    archive; the index inside the first says which holds which file."""
    data, content_type, filename = _service(session, storage, settings).download_part(
        export_id, part, actor
    )
    return attachment_response(data, content_type, filename)
