"""The «Modelli AI» settings: the model PigroCRM reads documents with (REB-972, design
2026-11-09, incoming documents inbox, § 3.2). Off by default, an unscoped administrator's
and never an agent's: the key, the spend cap and the switch are credentials and money.
The key is written here and never read back."""

from fastapi import APIRouter, Depends

from pigrocrm.core.ai.schemas import AiModelRead, AiModelTestResult, AiModelUpdate
from pigrocrm.core.ai.service import AiModelService
from pigrocrm_api.deps import ActorDep, BaseSettingsDep, ModelCallDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/settings/ai-model",
    tags=["settings"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.get("", response_model=AiModelRead)
def read(
    session: SessionDep, actor: ActorDep, base: BaseSettingsDep, call: ModelCallDep
) -> AiModelRead:
    """The settings, without the key, with what the model still needs, today's spend and the
    notice a person reads before switching it on."""
    return AiModelService(session, base, call).get(actor)


@router.put("", response_model=AiModelRead)
def update(
    data: AiModelUpdate,
    session: SessionDep,
    actor: ActorDep,
    base: BaseSettingsDep,
    call: ModelCallDep,
) -> AiModelRead:
    """Only what is sent changes. Switching it on needs the settings complete and
    `privacy_acknowledged`; changing the provider or the model switches it off."""
    return AiModelService(session, base, call).update(data, actor)


@router.post("/test", response_model=AiModelTestResult)
def test(
    session: SessionDep, actor: ActorDep, base: BaseSettingsDep, call: ModelCallDep
) -> AiModelTestResult:
    """«Prova»: one tiny call with the saved settings, on or off. It costs what a call costs."""
    return AiModelService(session, base, call).test(actor)
