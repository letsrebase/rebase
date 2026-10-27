from uuid import UUID

from fastapi import APIRouter, status

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
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, StorageDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/contracts", tags=["contract-emails"], responses=PROBLEM_RESPONSES)


@router.get("/{contract_id}/email-template-variables", response_model=list[ContractEmailVariable])
def template_variables(contract_id: UUID, actor: ActorDep) -> list[ContractEmailVariable]:
    """I nomi che un modello può usare come `{{nome}}`."""
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
    """Cosa partirebbe con questo modello per questa fattura, senza scrivere niente."""
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
    """Prepara la bozza di email con la fattura e il registro in allegato. Non invia: la
    persona invia con `POST /api/email-drafts/{id}/send`, che non ha un equivalente MCP."""
    return ContractEmailService(session).prepare(
        contract_id,
        data.template_id,
        data.invoice_id,
        list(data.to_addresses),
        actor,
        settings=settings,
        storage=storage,
    )
