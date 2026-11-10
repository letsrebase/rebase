"""The period bundle for the accountant (REB-1011, spec accountant export § 1 and § 3).

Thin, like every router here: resolve the actor, call `AccountantExportService`, answer with
the ZIP. Core in every space, the way invoices are: which parts the bundle holds follows the
modules and the entity's pack, and the index inside names the parts it left out. The check
that decides who may ask (an admin whose scope covers the entity, an unscoped one for every
entity) is the service's.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pigrocrm.core.accountant_export.parts import Period
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


class ExportParams(BaseModel):
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


@router.get(
    "",
    response_class=Response,
    responses={200: {"content": {ZIP_MIME: {}}, "description": "The period bundle as a ZIP"}},
)
def download_export(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    params: Annotated[ExportParams, Query()],
) -> Response:
    """One ZIP for the accountant: the sales register with its VAT columns, the issued
    invoices' files, the open due items and the payments of the period, and an index naming
    every part with its row count and every part left out with its reason."""
    export = AccountantExportService(session, storage, settings).build(
        Period(params.date_from, params.date_to),
        actor,
        params.legal_entity_id,
        all_entities=params.all_entities,
    )
    return attachment_response(export.content, ZIP_MIME, export.filename)
