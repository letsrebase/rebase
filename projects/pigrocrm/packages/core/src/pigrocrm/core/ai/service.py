"""The settings of PigroCRM's own model (design 2026-11-09, incoming documents inbox, § 3.2): an
administrator's act, a person's and never an agent's, like every credential (REB-880). Read,
write and the «Prova» that tries the configuration with a one-line request.

Every write is a timeline entry (`ai_model`) naming what changed and never the key.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.ai.gateway import ModelGateway, missing_settings
from pigrocrm.core.ai.models import AiModelConfig
from pigrocrm.core.ai.schemas import (
    PROVIDER_LABELS,
    AiModelRead,
    AiModelTestResult,
    AiModelUpdate,
)
from pigrocrm.core.ai.transport import ModelCall
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.space_settings.sealing import seal_value

ENTITY = "ai_model"
READ_ACTION = "read_ai_model_settings"
UPDATE_ACTION = "update_ai_model_settings"
TEST_ACTION = "test_ai_model"

_TEST_SYSTEM = "You answer with the single word: ok"
_TEST_PROMPT = "Answer with the single word: ok"
_TEST_MAX_OUTPUT_TOKENS = 16


def privacy_notice(provider: str | None, model: str | None) -> str:
    who = (
        f"{PROVIDER_LABELS[provider]} (modello {model})"
        if provider in PROVIDER_LABELS and model
        else "il fornitore che sceglierai"
    )
    return (
        f"Se accendi la lettura con il modello, PigroCRM invia a {who} il testo di ogni PDF che "
        "arriva in «In arrivo», il PDF stesso quando non ha testo, e ogni foto, per leggerne "
        "emittente, numero, date e importi. Non invia altro: ne' email, ne' elenchi, ne' i dati "
        "dell'anagrafica. Il fornitore tratta quel contenuto secondo le sue condizioni. Il testo "
        "letto e' trattato come dato, mai come istruzione, e la lettura e' sempre una proposta "
        "che una persona accetta o rifiuta."
    )


class AiModelService:
    def __init__(self, session: Session, settings: Settings, call: ModelCall) -> None:
        self.session = session
        self.settings = settings
        self.gateway = ModelGateway(session, settings, call)
        self.activities = ActivityService(session)

    def _read(self, config: AiModelConfig | None) -> AiModelRead:
        return AiModelRead(
            enabled=bool(config and config.enabled),
            provider=config.provider if config else None,  # type: ignore[arg-type]
            model=config.model if config else None,
            has_key=bool(config and config.api_key_sealed),
            daily_cap_eur=config.daily_cap_eur if config else None,
            input_eur_per_mtok=config.input_eur_per_mtok if config else None,
            output_eur_per_mtok=config.output_eur_per_mtok if config else None,
            missing=missing_settings(config),
            spent_today_eur=self.gateway.spent_today(),
            privacy_acknowledged_at=config.privacy_acknowledged_at if config else None,
            privacy_acknowledged_by_id=config.privacy_acknowledged_by_id if config else None,
            privacy_notice=privacy_notice(
                config.provider if config else None, config.model if config else None
            ),
        )

    def get(self, actor: Actor) -> AiModelRead:
        actor.require_unscoped_admin(READ_ACTION)
        return self._read(self.gateway.config())

    def update(self, data: AiModelUpdate, actor: Actor) -> AiModelRead:
        actor.require_unscoped_admin(UPDATE_ACTION)
        config = self.gateway.config()
        if config is None:
            config = AiModelConfig()
            self.session.add(config)
        sent = data.model_fields_set
        changed: list[str] = []

        provider_or_model_changed = (
            "provider" in sent
            and data.provider is not None
            and data.provider != config.provider
            or "model" in sent
            and data.model is not None
            and data.model != config.model
        )
        for name in (
            "provider",
            "model",
            "daily_cap_eur",
            "input_eur_per_mtok",
            "output_eur_per_mtok",
        ):
            value = getattr(data, name)
            if name in sent and value is not None and value != getattr(config, name):
                setattr(config, name, value)
                changed.append(name)
        if data.clear_api_key:
            if config.api_key_sealed is not None:
                config.api_key_sealed = None
                changed.append("api_key")
        elif data.api_key is not None:
            config.api_key_sealed = seal_value(data.api_key, self.settings.jwt_secret)
            changed.append("api_key")

        if provider_or_model_changed:
            # The notice names the provider and the model: a different one is a different
            # consent.
            config.privacy_acknowledged_at = None
            config.privacy_acknowledged_by_id = None
        if (provider_or_model_changed or data.clear_api_key) and config.enabled:
            config.enabled = False
            changed.append("enabled")

        if data.enabled is not None and data.enabled != config.enabled:
            if data.enabled:
                self._switch_on(config, data, actor)
            else:
                config.enabled = False
            if "enabled" not in changed:
                changed.append("enabled")
        elif data.enabled is True and data.privacy_acknowledged:
            # Already on, and the same person says they have read the notice again.
            self._acknowledge(config, actor)

        self.session.flush()
        self.activities.record(ENTITY, config.id, "updated", actor, {"changed": changed})
        self.session.commit()
        return self._read(config)

    def _acknowledge(self, config: AiModelConfig, actor: Actor) -> None:
        config.privacy_acknowledged_at = datetime.now(UTC)
        config.privacy_acknowledged_by_id = actor.id

    def _switch_on(self, config: AiModelConfig, data: AiModelUpdate, actor: Actor) -> None:
        missing = missing_settings(config)
        if missing:
            raise ValidationFailed(
                ENTITY,
                "enabled",
                f"per accendere il modello mancano: {', '.join(missing)}",
                expected="provider, model, api_key, daily_cap_eur e prices",
            )
        if not data.privacy_acknowledged:
            raise ValidationFailed(
                ENTITY,
                "privacy_acknowledged",
                "prima di accendere il modello va letto cosa riceve (privacy_notice)",
                expected="privacy_acknowledged = true",
            )
        self._acknowledge(config, actor)
        config.enabled = True

    def test(self, actor: Actor) -> AiModelTestResult:
        """«Prova»: one tiny call with the saved configuration, switched on or not. It spends
        what a call spends, against the same cap."""
        actor.require_unscoped_admin(TEST_ACTION)
        spend = self.gateway.run(
            task="test",
            system=_TEST_SYSTEM,
            text_input=_TEST_PROMPT,
            max_output_tokens=_TEST_MAX_OUTPUT_TOKENS,
            require_enabled=False,
        )
        return AiModelTestResult(
            ok=True,
            input_tokens=spend.answer.input_tokens,
            output_tokens=spend.answer.output_tokens,
            cost_eur=Decimal(spend.cost_eur),
        )
