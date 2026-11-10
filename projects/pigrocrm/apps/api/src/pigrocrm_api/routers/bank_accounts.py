"""Bank accounts, their statements and movements, and the revert of an import (REB-1025, cash
design §§ 2 and 4), behind the `cash` module.

Every route is an administrator's and the service asks for it before it looks a row up.
Authorisation, the balance, the checks and the counts are the services'; a router that holds
an `if` for them is one the MCP adapter cannot reuse. The upload takes the `Request` and
checks the role before it reads the body (`uploads.py`).
"""

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from pigrocrm.core.bank.accounts import BankAccountService
from pigrocrm.core.bank.schemas import (
    AccountCoverageRead,
    BankAccountCreate,
    BankAccountRead,
    BankAccountUpdate,
    MovementFilter,
    MovementPage,
    StatementImportResult,
    StatementPage,
    StatementRead,
)
from pigrocrm.core.bank.statements import (
    STATEMENT_SIZE_MAX,
    BankStatementReadService,
    StatementImportService,
)
from pigrocrm.core.errors import ValidationFailed
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import LEGAL_ENTITY_QUERY_DESCRIPTION, ListParams
from pigrocrm_api.uploads import read_upload_with_fields, single_file_body

router = APIRouter(
    prefix="/api",
    tags=["bank-accounts"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("cash")],
)


class StatementListParams(ListParams):
    cursor: str | None = Field(
        default=None, max_length=4096, description="`next_cursor` of the previous page"
    )


class MovementListParams(StatementListParams):
    state: MovementFilter = Field(
        default="all", description="`unmatched` only the movements that wait to be matched"
    )
    statement_id: UUID | None = Field(default=None, description="Only this statement's movements")


@router.get("/bank-accounts", response_model=list[BankAccountRead])
def list_bank_accounts(
    session: SessionDep,
    actor: ActorDep,
    include_archived: Annotated[
        bool, Query(description="Also the accounts that were archived")
    ] = False,
    legal_entity_id: Annotated[
        UUID | None, Query(description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    ] = None,
) -> list[BankAccountRead]:
    """The accounts with their balance, how stale it is and how many movements wait."""
    return BankAccountService(session).list_bank_accounts(
        actor, include_archived=include_archived, legal_entity_id=legal_entity_id
    )


@router.post("/bank-accounts", response_model=BankAccountRead, status_code=status.HTTP_201_CREATED)
def create_bank_account(
    data: BankAccountCreate, session: SessionDep, actor: ActorDep
) -> BankAccountRead:
    """A bank account of a legal entity. With several companies `legal_entity_id` is
    required; the IBAN is unique."""
    return BankAccountService(session).create_bank_account(data, actor)


@router.patch("/bank-accounts/{account_id}", response_model=BankAccountRead)
def update_bank_account(
    account_id: UUID, data: BankAccountUpdate, session: SessionDep, actor: ActorDep
) -> BankAccountRead:
    """The IBAN, the currency and the legal entity cannot change once a statement exists."""
    return BankAccountService(session).update_bank_account(account_id, data, actor)


@router.post("/bank-accounts/{account_id}/archive", response_model=BankAccountRead)
def archive_bank_account(account_id: UUID, session: SessionDep, actor: ActorDep) -> BankAccountRead:
    """Hides the account from the list and from the balances. Its movements stay."""
    return BankAccountService(session).archive_bank_account(account_id, actor)


@router.get("/bank-accounts/{account_id}/coverage", response_model=AccountCoverageRead)
def get_coverage(account_id: UUID, session: SessionDep, actor: ActorDep) -> AccountCoverageRead:
    """The last twelve months: `covered` by the imported statements, `missing` after the
    grace, `pending` while it is not due yet."""
    return BankAccountService(session).get_coverage(account_id, actor)


@router.get("/bank-accounts/{account_id}/statements", response_model=StatementPage)
def list_statements(
    account_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    params: Annotated[StatementListParams, Query()],
) -> StatementPage:
    """The statements, newest period first, each with the check of its stated balances."""
    return BankStatementReadService(session).list_statements(
        account_id, actor, cursor=params.cursor, limit=params.limit
    )


@router.post(
    "/bank-accounts/{account_id}/statements",
    response_model=StatementImportResult,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=single_file_body(
        "The statement: a CAMT.053 XML or the Banco BPM «Saldo e movimenti» XLSX, "
        f"at most {STATEMENT_SIZE_MAX} bytes. `period_from` and `period_to` (form fields, "
        "both or none) say the period a file that does not state one covers.",
        text_fields={
            "period_from": {"type": "string", "format": "date"},
            "period_to": {"type": "string", "format": "date"},
        },
    ),
)
async def import_statement(
    account_id: UUID, request: Request, session: SessionDep, actor: ActorDep
) -> StatementImportResult:
    """Reads the file into the account: the movements it already holds are linked, not
    stored again. The format is told by the file's content. A file that states no period
    needs `period_from` and `period_to` as form fields. The role is checked before the body
    is read."""
    actor.require_admin("import_statement")
    data, _content_type, fields = await read_upload_with_fields(
        request, STATEMENT_SIZE_MAX, "period_from", "period_to"
    )
    period_from = _day(fields.get("period_from"), "period_from")
    period_to = _day(fields.get("period_to"), "period_to")
    return await run_in_threadpool(
        StatementImportService(session).import_file,
        account_id,
        data,
        actor,
        period_from=period_from,
        period_to=period_to,
    )


@router.get("/bank-accounts/{account_id}/movements", response_model=MovementPage)
def list_movements(
    account_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    params: Annotated[MovementListParams, Query()],
) -> MovementPage:
    """The movements, newest booking first, an unmatched one flagged once it waits too long.
    A movement present in two statements is one row."""
    return BankStatementReadService(session).list_movements(
        account_id,
        actor,
        state=params.state,
        statement_id=params.statement_id,
        cursor=params.cursor,
        limit=params.limit,
    )


@router.post("/bank-statements/{statement_id}/revert", response_model=StatementRead)
def revert_statement(statement_id: UUID, session: SessionDep, actor: ActorDep) -> StatementRead:
    """Removes the statement's movements and the automatic matches they carried. A movement
    already matched, a transfer or ignored blocks it (409)."""
    return StatementImportService(session).revert_statement(statement_id, actor)


def _day(raw: str | None, field: str) -> dt.date | None:
    if raw is None or raw == "":
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise ValidationFailed(
            "bank_statement", field, "data non valida", expected="una data AAAA-MM-GG"
        ) from None
