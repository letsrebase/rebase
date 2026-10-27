"""The model providers and tasks, as an admin configures them (REB-830, «Modelli AI»).

`AiConfigService` is the one door to `ai_providers`, `ai_tasks`, `ai_prices` and
`ai_usage`:

* **Keys are write-only.** `save_key` seals the key (`rebase_core.secrets`) and keeps the
  ciphertext, the nonce and the last four characters; nothing returns it, and the audit
  entry says «chiave sostituita», never the key. `resolve_key` is the only reader, for the
  seam that calls the provider, and its errors never carry key text. With
  `REBASE_SECRETS_KEY` missing nothing is saved or read and nothing falls back to clear
  text.
* **Every change is an `AdminAction`** on entity `ai_config`: who, when, before and after.
* **Cost is recorded per call, and a call is never free by omission**: no price row for the
  model means `can_call` refuses, and a usage row written anyway is `priced = false` with a
  `NULL` cost, counted as unpriced.
* **The daily cap** is the task's `daily_cap_eur`, summed over every purpose (`run`,
  `prova`, `eval`) of that task since midnight in Rome, where the day of the people running
  rebase begins (`team_caps.py` counts the same day; it is not imported here because it
  reaches the seam this module feeds).
"""

from collections.abc import Callable
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from rebase_core.ai_schemas import (
    AiConfigRead,
    PriceRead,
    ProviderRead,
    SpendRead,
    SpendRow,
    TaskRead,
)
from rebase_core.audit import AdminActionService, field_changes, utcnow
from rebase_core.config import Settings
from rebase_core.errors import (
    InvalidState,
    NotFound,
    SecretsKeyMissing,
    ValidationFailed,
)
from rebase_core.models import (
    AI_EFFORTS,
    AI_PROVIDERS,
    AI_TASKS,
    AI_USAGE_PURPOSES,
    CARD_MODEL_MAX_LENGTH,
    AiPrice,
    AiProvider,
    AiTask,
    AiUsage,
    User,
)
from rebase_core.secrets import last_four, seal, secrets_key_bytes, unseal

ROME = ZoneInfo("Europe/Rome")
# `AdminAction.entity_id` is a UUID and the configuration is not a row: one fixed id.
AI_CONFIG_ENTITY = "ai_config"
AI_CONFIG_ENTITY_ID = uuid5(NAMESPACE_URL, "https://letsrebase.com/hub/ai_config")

CAP_REACHED = "Tetto giornaliero raggiunto"
NO_PRICE = "Manca il prezzo del modello: aggiungilo in «Modelli AI»"
TASK_OFF = "Il compito è spento"
PROVIDER_OFF = "Il fornitore è spento"
NO_KEY = "Manca la chiave del fornitore"
NO_SECRETS_KEY = "Manca REBASE_SECRETS_KEY: le chiavi salvate non si possono leggere"
KEY_REPLACED = "chiave sostituita"

# Suggestions for the model field only: a model is free text, and a name that is not here
# still saves (and still needs a price before it can be called).
KNOWN_MODELS: dict[str, list[str]] = {
    "anthropic": ["claude-opus-5", "claude-opus-5-5", "claude-sonnet-5-5"],
    "openai": ["gpt-5"],
    "google": ["gemini-3-pro-preview"],
}

_MTOK = Decimal(1_000_000)
_COST_PLACES = Decimal("0.000001")
_PRICE_PLACES = Decimal("0.0001")
_CAP_PLACES = Decimal("0.01")


def key_hint(last4: str) -> str:
    return f"configurata, finisce con …{last4}"


def rome_midnight(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(ROME).date(), time.min, tzinfo=ROME)


def _require_provider(provider: str) -> None:
    if provider not in AI_PROVIDERS:
        raise ValidationFailed("ai_provider", "provider", f"fornitore sconosciuto: {provider}")


def _require_model(model: str) -> str:
    cleaned = model.strip()
    if not cleaned or len(cleaned) > CARD_MODEL_MAX_LENGTH:
        raise ValidationFailed(
            "ai_task",
            "model",
            f"il modello è obbligatorio, al massimo {CARD_MODEL_MAX_LENGTH} caratteri",
        )
    return cleaned


def _non_negative(
    entity: str, field: str, value: Decimal | None, places: Decimal
) -> Decimal | None:
    if value is None:
        return None
    if value < 0:
        raise ValidationFailed(entity, field, "non può essere negativo")
    return value.quantize(places)


class AiConfigService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session = session
        self.settings = settings
        self.now = now

    # ---- reads -------------------------------------------------------------------------

    def read(self) -> AiConfigRead:
        now = self.now()
        return AiConfigRead(
            secrets_key_present=self._secrets_key_present(),
            providers=[self._provider_read(row) for row in self._providers()],
            tasks=[self._task_read(row, now) for row in self._tasks()],
            prices=[
                _price_read(price)
                for price in self.session.scalars(
                    select(AiPrice).order_by(AiPrice.provider, AiPrice.model)
                )
            ],
            spend=self._spend(now),
            known_models=KNOWN_MODELS,
        )

    def resolve_key(self, provider: str) -> str | None:
        """The provider's key in clear, for the seam that is about to call it: `None` when
        none is saved, `SecretsKeyMissing` without `REBASE_SECRETS_KEY`, `SecretsKeyInvalid`
        when it does not open. Never logged, never in an error text."""
        row = self.session.get(AiProvider, provider)
        if row is None or row.key_ciphertext is None or row.key_nonce is None:
            return None
        return unseal(row.key_ciphertext, row.key_nonce, secrets_key_bytes(self.settings))

    def spent_today(self, task: str) -> Decimal:
        """What the task spent since midnight in Rome, every purpose together. An unpriced
        call adds nothing here (it has no cost), which is why `can_call` refuses a model
        with no price rather than let it spend unseen."""
        return self._spent(task, self.now())

    def can_call(self, task: str) -> tuple[bool, str | None]:
        """Whether a call as `task` may go out now, and when not, the sentence why. One
        reason is given, the first of: the daily cap reached, no price for the task's
        model, the task off, its provider off, no key saved, a key that cannot be read
        without `REBASE_SECRETS_KEY`."""
        row = self.session.get(AiTask, task)
        if row is None:
            raise NotFound("ai_task", task)
        reason = self._reason(row, self.spent_today(task))
        return reason is None, reason

    # ---- writes ------------------------------------------------------------------------

    def save_key(self, admin_id: UUID, provider: str, key: str) -> ProviderRead:
        secret = secrets_key_bytes(self.settings)  # SecretsKeyMissing before anything else
        _require_provider(provider)
        cleaned = key.strip()
        if not cleaned:
            raise ValidationFailed("ai_provider", "key", "la chiave è vuota")
        row = self._provider(provider)
        row.key_ciphertext, row.key_nonce = seal(cleaned, secret)
        row.key_last4 = last_four(cleaned)
        row.saved_by = admin_id
        row.saved_at = self.now()
        self.session.commit()
        AdminActionService(self.session).record(
            AI_CONFIG_ENTITY,
            AI_CONFIG_ENTITY_ID,
            "ai_key",
            admin_id,
            {"provider": provider, "azione": KEY_REPLACED, "key_hint": key_hint(row.key_last4)},
        )
        return self._provider_read(row)

    def set_provider_enabled(self, admin_id: UUID, provider: str, enabled: bool) -> ProviderRead:
        _require_provider(provider)
        row = self._provider(provider)
        if enabled and row.key_ciphertext is None:
            raise InvalidState("Salva prima la chiave del fornitore")
        before = row.enabled
        if before != enabled:
            row.enabled = enabled
            self.session.commit()
            AdminActionService(self.session).record(
                AI_CONFIG_ENTITY,
                AI_CONFIG_ENTITY_ID,
                "ai_switch",
                admin_id,
                {"provider": provider, **field_changes({"enabled": before}, {"enabled": enabled})},
            )
        return self._provider_read(row)

    def update_task(
        self,
        admin_id: UUID,
        task: str,
        *,
        provider: str,
        model: str,
        effort: str | None,
        max_output_tokens: int,
        daily_cap_eur: Decimal | None,
        enabled: bool,
    ) -> TaskRead:
        row = self.session.get(AiTask, task)
        if task not in AI_TASKS or row is None:
            raise NotFound("ai_task", task)
        _require_provider(provider)
        if effort is not None and effort not in AI_EFFORTS:
            raise ValidationFailed("ai_task", "effort", f"livello sconosciuto: {effort}")
        if max_output_tokens < 1:
            raise ValidationFailed("ai_task", "max_output_tokens", "deve essere almeno 1")
        after: dict[str, Any] = {
            "provider": provider,
            "model": _require_model(model),
            "effort": effort,
            "max_output_tokens": max_output_tokens,
            "daily_cap_eur": _non_negative("ai_task", "daily_cap_eur", daily_cap_eur, _CAP_PLACES),
            "enabled": enabled,
        }
        before = {field: getattr(row, field) for field in after}
        changes = field_changes(before, after)
        if changes:
            for field, value in after.items():
                setattr(row, field, value)
            self.session.commit()
            AdminActionService(self.session).record(
                AI_CONFIG_ENTITY,
                AI_CONFIG_ENTITY_ID,
                "ai_task",
                admin_id,
                {"task": task, **changes},
            )
        return self._task_read(row, self.now())

    def upsert_price(
        self,
        admin_id: UUID,
        provider: str,
        model: str,
        input_eur_per_mtok: Decimal,
        output_eur_per_mtok: Decimal,
        thinking_eur_per_mtok: Decimal | None = None,
        cache_read_eur_per_mtok: Decimal | None = None,
    ) -> PriceRead:
        _require_provider(provider)
        model = _require_model(model)
        after: dict[str, Any] = {
            "input_eur_per_mtok": _non_negative(
                "ai_price", "input_eur_per_mtok", input_eur_per_mtok, _PRICE_PLACES
            ),
            "output_eur_per_mtok": _non_negative(
                "ai_price", "output_eur_per_mtok", output_eur_per_mtok, _PRICE_PLACES
            ),
            "thinking_eur_per_mtok": _non_negative(
                "ai_price", "thinking_eur_per_mtok", thinking_eur_per_mtok, _PRICE_PLACES
            ),
            "cache_read_eur_per_mtok": _non_negative(
                "ai_price", "cache_read_eur_per_mtok", cache_read_eur_per_mtok, _PRICE_PLACES
            ),
        }
        row = self.session.get(AiPrice, (provider, model))
        before: dict[str, Any] = {}
        if row is None:
            row = AiPrice(provider=provider, model=model, **after)
            self.session.add(row)
        else:
            before = {field: getattr(row, field) for field in after}
            for field, value in after.items():
                setattr(row, field, value)
        changes = field_changes(before, after)
        self.session.commit()
        if changes:
            AdminActionService(self.session).record(
                AI_CONFIG_ENTITY,
                AI_CONFIG_ENTITY_ID,
                "ai_price",
                admin_id,
                {"provider": provider, "model": model, **changes},
            )
        return _price_read(row)

    def record_usage(
        self,
        *,
        task: str,
        purpose: str,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        thinking_tokens: int = 0,
        cache_read_tokens: int = 0,
        freelancer_id: UUID | None = None,
    ) -> AiUsage:
        """Writes one call's row and commits it on its own, so a failure further up never
        loses what was spent. `input_tokens` is the input that was not read from a cache;
        `cache_read_tokens` is billed at the cache price, or at the input price when the
        model has none. Thinking tokens are billed at the thinking price, or at the output
        price when it is null. A model with no price row is recorded `priced = false`, cost
        `NULL`: unpriced, not free."""
        if task not in AI_TASKS:
            raise ValidationFailed("ai_usage", "task", f"compito sconosciuto: {task}")
        if purpose not in AI_USAGE_PURPOSES:
            raise ValidationFailed("ai_usage", "purpose", f"scopo sconosciuto: {purpose}")
        price = self.session.get(AiPrice, (provider, model))
        cost: Decimal | None = None
        if price is not None:
            cost = (
                input_tokens * price.input_eur_per_mtok
                + output_tokens * price.output_eur_per_mtok
                + thinking_tokens
                * (
                    price.thinking_eur_per_mtok
                    if price.thinking_eur_per_mtok is not None
                    else price.output_eur_per_mtok
                )
                + cache_read_tokens
                * (
                    price.cache_read_eur_per_mtok
                    if price.cache_read_eur_per_mtok is not None
                    else price.input_eur_per_mtok
                )
            ) / _MTOK
            cost = cost.quantize(_COST_PLACES)
        usage = AiUsage(
            at=self.now(),
            task=task,
            purpose=purpose,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            cache_read_tokens=cache_read_tokens,
            cost_eur=cost,
            priced=price is not None,
            freelancer_id=freelancer_id,
        )
        self.session.add(usage)
        self.session.commit()
        return usage

    # ---- internals ---------------------------------------------------------------------

    def _secrets_key_present(self) -> bool:
        try:
            secrets_key_bytes(self.settings)
        except SecretsKeyMissing:
            return False
        return True

    def _providers(self) -> list[AiProvider]:
        rows = {row.provider: row for row in self.session.scalars(select(AiProvider))}
        return [rows[name] for name in AI_PROVIDERS if name in rows]

    def _tasks(self) -> list[AiTask]:
        rows = {row.task: row for row in self.session.scalars(select(AiTask))}
        return [rows[name] for name in AI_TASKS if name in rows]

    def _provider(self, provider: str) -> AiProvider:
        row = self.session.get(AiProvider, provider)
        if row is None:
            raise NotFound("ai_provider", provider)
        return row

    def _provider_read(self, row: AiProvider) -> ProviderRead:
        saved_by_name = None
        if row.saved_by is not None:
            user = self.session.get(User, row.saved_by)
            if user is not None:
                saved_by_name = f"{user.nome} {user.cognome}".strip()
        configured = row.key_ciphertext is not None
        return ProviderRead(
            provider=row.provider,
            configured=configured,
            key_hint=key_hint(row.key_last4 or "") if configured else None,
            enabled=row.enabled,
            saved_at=row.saved_at,
            saved_by_name=saved_by_name,
        )

    def _reason(self, task: AiTask, spent: Decimal) -> str | None:
        if task.daily_cap_eur is not None and spent >= task.daily_cap_eur:
            return CAP_REACHED
        if self.session.get(AiPrice, (task.provider, task.model)) is None:
            return NO_PRICE
        if not task.enabled:
            return TASK_OFF
        provider = self.session.get(AiProvider, task.provider)
        if provider is None or not provider.enabled:
            return PROVIDER_OFF
        if provider.key_ciphertext is None:
            return NO_KEY
        if not self._secrets_key_present():
            return NO_SECRETS_KEY
        return None

    def _spent(self, task: str, now: datetime) -> Decimal:
        total = self.session.scalar(
            select(func.coalesce(func.sum(AiUsage.cost_eur), 0)).where(
                AiUsage.task == task, AiUsage.at >= rome_midnight(now)
            )
        )
        return Decimal(total or 0)

    def _task_read(self, row: AiTask, now: datetime) -> TaskRead:
        spent = self._spent(row.task, now)
        return TaskRead(
            task=row.task,
            provider=row.provider,
            model=row.model,
            effort=row.effort,
            max_output_tokens=row.max_output_tokens,
            daily_cap_eur=row.daily_cap_eur,
            enabled=row.enabled,
            spent_today_eur=spent,
            cap_reached=row.daily_cap_eur is not None and spent >= row.daily_cap_eur,
            reason=self._reason(row, spent),
        )

    def _spend(self, now: datetime) -> SpendRead:
        local = now.astimezone(ROME)
        start = datetime.combine(local.date().replace(day=1), time.min, tzinfo=ROME)
        rows = self.session.execute(
            select(
                AiUsage.task,
                AiUsage.provider,
                func.coalesce(func.sum(AiUsage.cost_eur), 0),
                func.count(),
                func.coalesce(func.sum(case((AiUsage.priced.is_(False), 1), else_=0)), 0),
            )
            .where(AiUsage.at >= start)
            .group_by(AiUsage.task, AiUsage.provider)
            .order_by(AiUsage.task, AiUsage.provider)
        ).all()
        return SpendRead(
            month=local.strftime("%Y-%m"),
            rows=[
                SpendRow(
                    task=task,
                    provider=provider,
                    cost_eur=Decimal(cost),
                    calls=calls,
                    unpriced_calls=int(unpriced),
                )
                for task, provider, cost, calls, unpriced in rows
            ],
        )


def _price_read(row: AiPrice) -> PriceRead:
    return PriceRead(
        provider=row.provider,
        model=row.model,
        input_eur_per_mtok=row.input_eur_per_mtok,
        output_eur_per_mtok=row.output_eur_per_mtok,
        thinking_eur_per_mtok=row.thinking_eur_per_mtok,
        cache_read_eur_per_mtok=row.cache_read_eur_per_mtok,
    )
