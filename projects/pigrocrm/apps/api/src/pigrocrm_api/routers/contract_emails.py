from uuid import UUID

from fastapi import APIRouter, Depends, status

from pigrocrm.core.contract_emails.schemas import (
    ContractEmailPrepare,
    ContractEmailPreview,
    ContractEmailRequest,
    ContractEmailTemplateCreate,
    ContractEmailTemplateRead,
    ContractEmailTemplateUpdate,
    ContractEmailVariable,
)
from pigrocrm.core.contract_emails.service import ContractEmailService, variables
from pigrocrm.core.gmail.schemas import EmailDraftRead
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, StorageDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/contracts",
    tags=["contract-emails"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("contracts")],
)


@router.get("/{contract_id}/email-template-variables", response_model=list[ContractEmailVariable])
def template_variables(contract_id: UUID) -> list[ContractEmailVariable]:  # noqa: ARG001
    """The names a template can use as `{{nome}}`. The path names the contract the page
    asks from; the names are the same for every contract, and the router's `get_actor`
    dependency is what authenticates."""
    return variables()


@router.get("/{contract_id}/email-templates", response_model=list[ContractEmailTemplateRead])
def list_templates(
    contract_id: UUID, session: SessionDep, actor: ActorDep
) -> list[ContractEmailTemplateRead]:
    return ContractEmailService(session).list_for_contract(contract_id, actor)


@router.post(
    "/{contract_id}/email-templates",
    response_model=ContractEmailTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
def create_template(
    contract_id: UUID, data: ContractEmailTemplateCreate, session: SessionDep, actor: ActorDep
) -> ContractEmailTemplateRead:
    return ContractEmailService(session).create(contract_id, data, actor)


@router.patch(
    "/{contract_id}/email-templates/{template_id}", response_model=ContractEmailTemplateRead
)
def update_template(
    contract_id: UUID,
    template_id: UUID,
    data: ContractEmailTemplateUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> ContractEmailTemplateRead:
    return ContractEmailService(session).update(contract_id, template_id, data, actor)


@router.post("/{contract_id}/email/preview", response_model=ContractEmailPreview)
def preview(
    contract_id: UUID, data: ContractEmailRequest, session: SessionDep, actor: ActorDep
) -> ContractEmailPreview:
    """What would go out with this template for this invoice, without writing anything."""
    return ContractEmailService(session).preview(
        contract_id, data.template_id, data.invoice_id, actor
    )


@router.post(
    "/{contract_id}/email/draft",
    response_model=EmailDraftRead,
    status_code=status.HTTP_201_CREATED,
)
def prepare_draft(
    contract_id: UUID,
    data: ContractEmailPrepare,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> EmailDraftRead:
    """Prepares the email draft with the invoice and the register attached. It does not send: the
    person sends with `POST /api/email-drafts/{id}/send`, which has no MCP equivalent."""
    return ContractEmailService(session).prepare(
        contract_id,
        data.template_id,
        data.invoice_id,
        list(data.to_addresses),
        actor,
        settings=settings,
        storage=storage,
    )
