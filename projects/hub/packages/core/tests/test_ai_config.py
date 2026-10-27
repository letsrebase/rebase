"""REB-830: `AiConfigService`, the model providers' keys (write-only), tasks, prices, the
audit trail and the spend."""

import base64
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core.ai_config import (
    AI_CONFIG_ENTITY,
    AI_CONFIG_ENTITY_ID,
    CAP_REACHED,
    NO_PRICE,
    AiConfigService,
)
from rebase_core.config import Settings
from rebase_core.errors import (
    InvalidState,
    NotFound,
    SecretsKeyInvalid,
    SecretsKeyMissing,
    ValidationFailed,
)
from rebase_core.models import AdminAction, AiProvider, AiUsage, User

SECRET = "sk-secret-abcd"
SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()
NOON = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _settings(secrets_key: str = SECRETS_KEY) -> Settings:
    return Settings(secrets_key=secrets_key, _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def admin(hub_engine: Engine, hub_session: Session) -> Iterator[User]:
    row = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    hub_session.add(row)
    hub_session.commit()
    yield row
    hub_session.rollback()
    # Back to what migration 0035 seeded, for the next test of this worker.
    for statement in (
        "DELETE FROM ai_usage",
        "DELETE FROM ai_prices",
        "UPDATE ai_providers SET key_ciphertext = NULL, key_nonce = NULL, key_last4 = NULL, "
        "saved_by = NULL, saved_at = NULL, enabled = false",
        "UPDATE ai_tasks SET provider = 'anthropic', model = 'claude-opus-5', "
        "effort = 'medium', daily_cap_eur = NULL, enabled = true, "
        "max_output_tokens = CASE task WHEN 'card' THEN 2000 ELSE 8000 END",
        "DELETE FROM admin_actions",
        "DELETE FROM users",
    ):
        hub_session.execute(text(statement))
    hub_session.commit()


def _service(
    session: Session, *, secrets_key: str = SECRETS_KEY, now: datetime = NOON
) -> AiConfigService:
    return AiConfigService(session, _settings(secrets_key), now=lambda: now)


def _ready(service: AiConfigService, admin: User, *, price: bool = True) -> None:
    """The card task callable: key saved, provider on, a price for its model."""
    service.save_key(admin.id, "anthropic", SECRET)
    service.set_provider_enabled(admin.id, "anthropic", True)
    if price:
        service.upsert_price(admin.id, "anthropic", "claude-opus-5", Decimal("5"), Decimal("25"))


def _actions(session: Session, kind: str) -> list[AdminAction]:
    return list(
        session.scalars(
            select(AdminAction)
            .where(AdminAction.entity_type == AI_CONFIG_ENTITY, AdminAction.kind == kind)
            .order_by(AdminAction.created_at)
        )
    )


def test_save_key_stores_ciphertext_and_hint(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    provider = service.save_key(admin.id, "anthropic", SECRET)

    row = hub_session.get(AiProvider, "anthropic")
    assert row is not None
    assert row.key_ciphertext is not None and row.key_nonce is not None
    assert SECRET.encode() not in row.key_ciphertext
    assert SECRET.encode() not in row.key_nonce
    assert row.key_last4 == "abcd"
    assert provider.key_hint == "configurata, finisce con …abcd"
    assert provider.configured is True
    assert provider.saved_by_name == "Ivan Sala"
    config = service.read()
    assert config.secrets_key_present is True
    assert next(p for p in config.providers if p.provider == "anthropic").key_hint == (
        "configurata, finisce con …abcd"
    )
    assert "sk-secret" not in config.model_dump_json()
    assert service.resolve_key("anthropic") == SECRET


def test_unconfigured_provider_reads_without_a_hint(hub_session: Session, admin: User) -> None:
    provider = next(p for p in _service(hub_session).read().providers if p.provider == "google")
    assert (provider.configured, provider.key_hint, provider.enabled) == (False, None, False)
    assert _service(hub_session).resolve_key("google") is None


def test_save_key_without_secrets_key_refused_and_nothing_stored(
    hub_session: Session, admin: User
) -> None:
    service = _service(hub_session, secrets_key="")
    with pytest.raises(SecretsKeyMissing):
        service.save_key(admin.id, "anthropic", SECRET)
    row = hub_session.get(AiProvider, "anthropic")
    assert row is not None
    assert (row.key_ciphertext, row.key_nonce, row.key_last4, row.saved_at) == (None,) * 4
    assert _actions(hub_session, "ai_key") == []
    assert service.read().secrets_key_present is False


def test_save_key_audit_says_replaced_not_key(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    service.save_key(admin.id, "anthropic", SECRET)
    service.save_key(admin.id, "anthropic", "sk-secret-wxyz")

    actions = _actions(hub_session, "ai_key")
    assert len(actions) == 2
    for action in actions:
        assert action.entity_id == AI_CONFIG_ENTITY_ID
        assert action.admin_id == admin.id
        dumped = json.dumps(action.payload)
        assert "chiave sostituita" in dumped
        assert "sk-secret" not in dumped
    assert actions[1].payload["key_hint"] == "configurata, finisce con …wxyz"


def test_save_key_refuses_an_empty_key_and_an_unknown_provider(
    hub_session: Session, admin: User
) -> None:
    service = _service(hub_session)
    with pytest.raises(ValidationFailed):
        service.save_key(admin.id, "anthropic", "   ")
    with pytest.raises(ValidationFailed):
        service.save_key(admin.id, "mistral", SECRET)


def test_update_task_audit_has_before_and_after(hub_session: Session, admin: User) -> None:
    task = _service(hub_session).update_task(
        admin.id,
        "card",
        provider="anthropic",
        model="claude-opus-5-5",
        effort="high",
        max_output_tokens=3000,
        daily_cap_eur=Decimal("12.5"),
        enabled=True,
    )
    assert (task.model, task.effort, task.max_output_tokens, task.daily_cap_eur) == (
        "claude-opus-5-5",
        "high",
        3000,
        Decimal("12.50"),
    )
    (action,) = _actions(hub_session, "ai_task")
    assert action.payload["task"] == "card"
    assert action.payload["changed"] == ["daily_cap_eur", "effort", "max_output_tokens", "model"]
    assert action.payload["before"] == {
        "daily_cap_eur": None,
        "effort": "medium",
        "max_output_tokens": 2000,
        "model": "claude-opus-5",
    }
    assert action.payload["after"] == {
        "daily_cap_eur": "12.50",
        "effort": "high",
        "max_output_tokens": 3000,
        "model": "claude-opus-5-5",
    }


def test_update_task_with_nothing_changed_writes_no_action(
    hub_session: Session, admin: User
) -> None:
    _service(hub_session).update_task(
        admin.id,
        "card",
        provider="anthropic",
        model="claude-opus-5",
        effort="medium",
        max_output_tokens=2000,
        daily_cap_eur=None,
        enabled=True,
    )
    assert _actions(hub_session, "ai_task") == []


def test_update_task_rejects_unknown_provider_and_unknown_task(
    hub_session: Session, admin: User
) -> None:
    service = _service(hub_session)
    fields = {
        "provider": "anthropic",
        "model": "claude-opus-5",
        "effort": "medium",
        "max_output_tokens": 2000,
        "daily_cap_eur": None,
        "enabled": True,
    }
    with pytest.raises(NotFound):
        service.update_task(admin.id, "summary", **fields)  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed):
        service.update_task(admin.id, "card", **{**fields, "provider": "mistral"})  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed):
        service.update_task(admin.id, "card", **{**fields, "effort": "extreme"})  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed):
        service.update_task(admin.id, "card", **{**fields, "daily_cap_eur": Decimal("-1")})  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed):
        service.update_task(admin.id, "card", **{**fields, "model": " "})  # type: ignore[arg-type]
    with pytest.raises(ValidationFailed):
        service.update_task(admin.id, "card", **{**fields, "max_output_tokens": 0})  # type: ignore[arg-type]
    assert _actions(hub_session, "ai_task") == []


def test_enable_provider_without_key_refused(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    with pytest.raises(InvalidState):
        service.set_provider_enabled(admin.id, "openai", True)
    assert _actions(hub_session, "ai_switch") == []


def test_enable_and_disable_provider_are_audited(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    service.save_key(admin.id, "openai", SECRET)
    assert service.set_provider_enabled(admin.id, "openai", True).enabled is True
    assert service.set_provider_enabled(admin.id, "openai", True).enabled is True  # no change
    assert service.set_provider_enabled(admin.id, "openai", False).enabled is False
    on, off = _actions(hub_session, "ai_switch")
    assert on.payload["before"] == {"enabled": False} and on.payload["after"] == {"enabled": True}
    assert off.payload["after"] == {"enabled": False}
    assert on.payload["provider"] == "openai"


def test_upsert_price_creates_updates_and_audits(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    first = service.upsert_price(
        admin.id,
        "anthropic",
        "claude-opus-5",
        Decimal("4.6"),
        Decimal("23"),
        cache_read_eur_per_mtok=Decimal("0.46"),
    )
    assert (first.input_eur_per_mtok, first.thinking_eur_per_mtok) == (Decimal("4.6000"), None)
    service.upsert_price(admin.id, "anthropic", "claude-opus-5", Decimal("5"), Decimal("23"))
    created, updated = _actions(hub_session, "ai_price")
    assert created.payload["before"]["input_eur_per_mtok"] is None
    assert updated.payload["changed"] == ["cache_read_eur_per_mtok", "input_eur_per_mtok"]
    assert [price.model for price in service.read().prices] == ["claude-opus-5"]
    with pytest.raises(ValidationFailed):
        service.upsert_price(admin.id, "anthropic", "m", Decimal("-1"), Decimal("1"))
    with pytest.raises(ValidationFailed):
        service.upsert_price(admin.id, "mistral", "m", Decimal("1"), Decimal("1"))


def _usage(
    service: AiConfigService, *, task: str = "card", purpose: str = "run", **tokens: int
) -> AiUsage:
    return service.record_usage(
        task=task,
        purpose=purpose,
        provider="anthropic",
        model="claude-opus-5",
        **tokens,
    )


def test_record_usage_cost_formula(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    service.upsert_price(
        admin.id,
        "anthropic",
        "claude-opus-5",
        Decimal("5"),
        Decimal("25"),
        cache_read_eur_per_mtok=Decimal("0.5"),
    )
    usage = _usage(
        service,
        input_tokens=1_000_000,
        output_tokens=100_000,
        thinking_tokens=200_000,
        cache_read_tokens=2_000_000,
    )
    # 5 + 2.5 + (thinking at the output price: 5) + 1
    assert usage.cost_eur == Decimal("13.500000")
    assert usage.priced is True
    service.upsert_price(
        admin.id,
        "anthropic",
        "claude-opus-5",
        Decimal("5"),
        Decimal("25"),
        thinking_eur_per_mtok=Decimal("10"),
    )
    # cache reads fall back to the input price, thinking uses its own
    assert _usage(service, thinking_tokens=100_000, cache_read_tokens=1_000_000).cost_eur == (
        Decimal("6.000000")
    )


def test_unpriced_usage_is_counted_unpriced_not_free(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    usage = _usage(service, input_tokens=1000, output_tokens=10)
    assert usage.priced is False
    assert usage.cost_eur is None
    spend = service.read().spend
    assert spend.month == "2026-10"
    (row,) = spend.rows
    assert (row.task, row.provider, row.calls, row.unpriced_calls, row.cost_eur) == (
        "card",
        "anthropic",
        1,
        1,
        Decimal(0),
    )


def test_record_usage_refuses_an_unknown_task_or_purpose(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    with pytest.raises(ValidationFailed):
        _usage(service, task="prova")
    with pytest.raises(ValidationFailed):
        _usage(service, purpose="card")


def test_spent_today_and_cap_reached_reason(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    _ready(service, admin)
    service.update_task(
        admin.id,
        "card",
        provider="anthropic",
        model="claude-opus-5",
        effort="medium",
        max_output_tokens=2000,
        daily_cap_eur=Decimal("10"),
        enabled=True,
    )
    _usage(service, output_tokens=200_000)  # 5 EUR
    assert service.spent_today("card") == Decimal("5.000000")
    assert service.can_call("card") == (True, None)
    _usage(service, output_tokens=200_000)  # 10 EUR: the cap is reached, not exceeded
    task = next(t for t in service.read().tasks if t.task == "card")
    assert task.spent_today_eur == Decimal("10.000000")
    assert task.cap_reached is True
    assert task.reason == CAP_REACHED == "Tetto giornaliero raggiunto"
    assert service.can_call("card") == (False, CAP_REACHED)
    # The other task has its own cap: it is still callable.
    assert service.can_call("team_proposal") == (True, None)


def test_yesterday_in_rome_does_not_count_today(hub_session: Session, admin: User) -> None:
    # Summer time ended on 25 October 2026: 23:00 UTC on the 26th is midnight in Rome.
    midnight_rome = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)
    service = _service(hub_session, now=midnight_rome - timedelta(minutes=1))
    _ready(service, admin)
    _usage(service, output_tokens=200_000)
    assert service.spent_today("card") == Decimal("5.000000")
    after_midnight = _service(hub_session, now=midnight_rome + timedelta(minutes=1))
    assert after_midnight.spent_today("card") == Decimal(0)


def test_task_without_a_price_for_its_model_is_not_callable(
    hub_session: Session, admin: User
) -> None:
    service = _service(hub_session)
    _ready(service, admin, price=False)
    task = next(t for t in service.read().tasks if t.task == "card")
    assert task.cap_reached is False
    assert task.reason == NO_PRICE == "Manca il prezzo del modello: aggiungilo in «Modelli AI»"
    assert service.can_call("card") == (False, NO_PRICE)
    service.upsert_price(admin.id, "anthropic", "claude-opus-5", Decimal("5"), Decimal("25"))
    assert service.can_call("card") == (True, None)


def test_can_call_names_the_first_obstacle(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    service.upsert_price(admin.id, "anthropic", "claude-opus-5", Decimal("5"), Decimal("25"))
    assert service.can_call("card") == (False, "Il fornitore è spento")
    service.save_key(admin.id, "anthropic", SECRET)
    service.set_provider_enabled(admin.id, "anthropic", True)
    assert service.can_call("card") == (True, None)
    # The key is stored, but this process lost REBASE_SECRETS_KEY.
    no_secret = _service(hub_session, secrets_key="")
    assert no_secret.can_call("card")[0] is False
    assert "REBASE_SECRETS_KEY" in (no_secret.can_call("card")[1] or "")
    service.update_task(
        admin.id,
        "card",
        provider="anthropic",
        model="claude-opus-5",
        effort="medium",
        max_output_tokens=2000,
        daily_cap_eur=None,
        enabled=False,
    )
    assert service.can_call("card") == (False, "Il compito è spento")
    with pytest.raises(NotFound):
        service.can_call("summary")


def test_provider_without_a_key_is_not_callable(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    service.upsert_price(admin.id, "anthropic", "claude-opus-5", Decimal("5"), Decimal("25"))
    hub_session.execute(text("UPDATE ai_providers SET enabled = true WHERE provider = 'anthropic'"))
    hub_session.commit()
    assert service.can_call("card") == (False, "Manca la chiave del fornitore")


def test_spent_today_counts_every_purpose_of_the_task(hub_session: Session, admin: User) -> None:
    service = _service(hub_session)
    _ready(service, admin)
    _usage(service, purpose="run", output_tokens=40_000)  # 1 EUR
    _usage(service, purpose="prova", output_tokens=40_000)  # 1 EUR
    _usage(service, purpose="eval", output_tokens=40_000)  # 1 EUR
    _usage(service, task="team_proposal", output_tokens=40_000)  # not the card's
    assert service.spent_today("card") == Decimal("3.000000")
    assert service.spent_today("team_proposal") == Decimal("1.000000")
    rows = {(row.task, row.calls) for row in service.read().spend.rows}
    assert rows == {("card", 3), ("team_proposal", 1)}


def test_resolve_key_wrong_secrets_key_raises_without_key_text(
    hub_session: Session, admin: User
) -> None:
    _service(hub_session).save_key(admin.id, "anthropic", SECRET)
    other = _service(hub_session, secrets_key=base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(SecretsKeyInvalid) as raised:
        other.resolve_key("anthropic")
    assert SECRET not in str(raised.value) and SECRET not in repr(raised.value)
    with pytest.raises(SecretsKeyMissing):
        _service(hub_session, secrets_key="").resolve_key("anthropic")


def test_read_lists_the_seeded_providers_tasks_and_known_models(
    hub_session: Session, admin: User
) -> None:
    config = _service(hub_session).read()
    assert [p.provider for p in config.providers] == ["anthropic", "openai", "google"]
    assert [t.task for t in config.tasks] == ["card", "team_proposal"]
    assert config.known_models["anthropic"][0] == "claude-opus-5"
    assert set(config.known_models) == {"anthropic", "openai", "google"}
    assert config.prices == []
    assert config.spend.rows == []
