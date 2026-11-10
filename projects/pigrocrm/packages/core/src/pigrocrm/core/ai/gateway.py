"""The gateway every model call of PigroCRM goes through (design 2026-11-09, incoming documents
inbox, § 3.2): the configuration, the day's cap and the usage rows are read and written here and
nowhere else, and the network is `transport.ModelCall`.

A call is refused **before any network** when the model is off or incomplete (`ModelOff`), when
its key cannot be opened with this installation's secret, or when the day's spend plus the
call's estimate would pass the cap (`ModelCapReached`). Otherwise it **reserves its estimated
cost first**, under a lock and committed, so concurrent calls cannot all pass the cap, and settles
the reservation to the real tokens after. A call that fails releases its hold (the usage row is
`failed` and costs nothing: there are no tokens to read). The estimate is deliberately rough and
on the safe side: the text's characters over three, for each file the model reads as it is
3000 tokens or one per twenty bytes if that is more (a scan is pages, and a page costs about as
much as a thousand bytes of it), plus the largest answer asked for. A call whose outcome is
unknown (`ModelUnavailable.maybe_billed`: a timeout, a server error, a body that is not a
response) keeps its reservation as spent: the provider may have charged it.

The key never leaves this module in the clear except to the transport, and is never logged.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.ai.models import AiModelConfig, AiModelUsage
from pigrocrm.core.ai.transport import (
    Attachment,
    ModelAnswer,
    ModelCall,
    ModelRequest,
    ModelUnavailable,
)
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import Conflict
from pigrocrm.core.space_settings.sealing import open_secret

MODEL_LOCK_KEY = 0x1B0C_0002
"""The advisory lock a call holds from the cap check to its reservation, to the end of that
transaction."""
CHARS_PER_TOKEN = 3
TOKENS_PER_ATTACHMENT = 3000
BYTES_PER_ATTACHMENT_TOKEN = 20
ROME = ZoneInfo("Europe/Rome")
_MILLION = Decimal(1_000_000)
_COST_STEP = Decimal("0.000001")


class ModelOff(Conflict):
    def __init__(self, sentence: str = "il modello di PigroCRM e' spento") -> None:
        super().__init__("ai_model", sentence)


class ModelCapReached(Conflict):
    def __init__(self) -> None:
        super().__init__(
            "ai_model",
            "il limite di spesa giornaliero del modello e' esaurito: si riprende domani, o "
            "un amministratore puo' alzarlo",
        )


@dataclass(frozen=True, slots=True)
class Spend:
    answer: ModelAnswer
    cost_eur: Decimal


def cost_of(config: AiModelConfig, input_tokens: int, output_tokens: int) -> Decimal:
    assert config.input_eur_per_mtok is not None and config.output_eur_per_mtok is not None
    total = (
        input_tokens * config.input_eur_per_mtok + output_tokens * config.output_eur_per_mtok
    ) / _MILLION
    return total.quantize(_COST_STEP, rounding=ROUND_HALF_UP)


def missing_settings(config: AiModelConfig | None) -> list[str]:
    """What a model that is to run still needs, as the names a settings form uses."""
    if config is None:
        return ["provider", "model", "api_key", "daily_cap_eur", "prices"]
    missing = []
    if config.provider is None:
        missing.append("provider")
    if config.model is None:
        missing.append("model")
    if config.api_key_sealed is None:
        missing.append("api_key")
    if config.daily_cap_eur is None:
        missing.append("daily_cap_eur")
    if config.input_eur_per_mtok is None or config.output_eur_per_mtok is None:
        missing.append("prices")
    return missing


class ModelGateway:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        call: ModelCall,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.settings = settings
        self.call = call
        self.clock = clock

    def config(self) -> AiModelConfig | None:
        return self.session.execute(select(AiModelConfig)).scalars().first()

    def is_on(self) -> bool:
        config = self.config()
        return config is not None and config.enabled and not missing_settings(config)

    def today(self) -> date:
        return self.clock().astimezone(ROME).date()

    def spent_today(self) -> Decimal:
        """What the day's calls cost, reservations included. A call that failed for certain costs
        nothing (its cost is 0); one that may have been billed keeps its estimate."""
        total = self.session.execute(
            select(
                func.coalesce(
                    func.sum(func.coalesce(AiModelUsage.cost_eur, AiModelUsage.estimated_cost_eur)),
                    0,
                )
            ).where(AiModelUsage.day == self.today())
        ).scalar_one()
        return Decimal(total)

    def estimate(
        self,
        config: AiModelConfig,
        system: str,
        prompt: str,
        attachments: Sequence[Attachment],
        max_output_tokens: int,
    ) -> Decimal:
        input_tokens = -(-(len(system) + len(prompt)) // CHARS_PER_TOKEN) + sum(
            max(TOKENS_PER_ATTACHMENT, len(a.data) // BYTES_PER_ATTACHMENT_TOKEN)
            for a in attachments
        )
        return cost_of(config, input_tokens, max_output_tokens)

    def run(
        self,
        *,
        task: str,
        system: str,
        text_input: str,
        attachments: Sequence[Attachment] = (),
        max_output_tokens: int,
        inbox_item_id: UUID | None = None,
        require_enabled: bool = True,
    ) -> Spend:
        """One call. The caller has nothing pending in the session: the call commits twice, the
        reservation and its settlement. `require_enabled=False` is the «Prova» of the settings page,
        which tries a model before anybody switches it on; every other task runs only when it is
        on."""
        self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MODEL_LOCK_KEY})
        config = self.config()
        missing = missing_settings(config)
        if config is None or missing:
            raise ModelOff("il modello di PigroCRM non e' configurato")
        if require_enabled and not config.enabled:
            raise ModelOff()
        assert config.provider and config.model and config.api_key_sealed
        assert config.daily_cap_eur is not None
        key = open_secret("ai_model_key", config.api_key_sealed, self.settings.jwt_secret)
        if key is None:
            raise ModelOff("la chiave del modello non e' leggibile: va inserita di nuovo")

        estimated = self.estimate(config, system, text_input, attachments, max_output_tokens)
        if self.spent_today() + estimated > config.daily_cap_eur:
            self.session.commit()  # releases the lock; the caller has nothing pending (see `run`)
            raise ModelCapReached()
        usage = AiModelUsage(
            task=task,
            day=self.today(),
            status="reserved",
            inbox_item_id=inbox_item_id,
            estimated_cost_eur=estimated,
        )
        self.session.add(usage)
        provider, model = config.provider, config.model
        self.session.commit()  # the hold is visible to the next call before this one is paid for

        try:
            answer = self.call(
                ModelRequest(
                    provider=provider,
                    model=model,
                    api_key=key,
                    system=system,
                    text=text_input,
                    attachments=attachments,
                    max_output_tokens=max_output_tokens,
                )
            )
        except BaseException as exc:
            usage.status = "failed"
            # A refusal the provider gave before running the model costs nothing; a call whose
            # outcome is unknown keeps what it reserved.
            maybe_billed = not isinstance(exc, ModelUnavailable) or exc.maybe_billed
            usage.cost_eur = estimated if maybe_billed else Decimal(0)
            self.session.commit()
            raise
        config = self.config()
        assert config is not None
        usage.status = "done"
        usage.input_tokens = answer.input_tokens
        usage.output_tokens = answer.output_tokens
        usage.cost_eur = cost_of(config, answer.input_tokens, answer.output_tokens)
        self.session.commit()
        return Spend(answer, usage.cost_eur)
