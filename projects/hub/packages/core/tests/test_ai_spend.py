"""HCORE-01 (REB-889): the daily cap as holds. A call reserves its worst case under a lock on
the task before the network, settles it to the real numbers after and gives it back when it
fails; a hold nobody settles stops counting once its TTL passes; and a hold is never read as
a call that was answered."""

import base64
import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from rebase_core.ai_config import AiConfigService
from rebase_core.ai_spend import HOLD_TTL
from rebase_core.config import Settings
from rebase_core.errors import LlmCapReached, LlmNotConfigured, SecretsKeyInvalid
from rebase_core.models import AiProvider, AiTask, AiUsage, User

SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()
NOON = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
MODEL = "claude-opus-5"


def _settings() -> Settings:
    return Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]


def _service(session: Session, now: datetime = NOON) -> AiConfigService:
    return AiConfigService(session, _settings(), now=lambda: now)


@pytest.fixture
def capped(hub_engine: Engine, hub_session: Session) -> Iterator[User]:
    """The card task with a cap of 1 euro and a price for its model, wiped after."""
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    hub_session.add(admin)
    hub_session.commit()
    service = _service(hub_session)
    service.upsert_price(admin.id, "anthropic", MODEL, Decimal("5"), Decimal("25"))
    task = hub_session.get(AiTask, "card")
    assert task is not None
    task.daily_cap_eur = Decimal("1.00")
    hub_session.commit()
    yield admin
    hub_session.rollback()
    for statement in (
        "DELETE FROM ai_usage",
        "DELETE FROM ai_prices",
        "UPDATE ai_providers SET key_ciphertext = NULL, key_nonce = NULL, key_last4 = NULL, "
        "saved_by = NULL, saved_at = NULL, enabled = false",
        "UPDATE ai_tasks SET daily_cap_eur = NULL",
        "DELETE FROM admin_actions",
        "DELETE FROM users",
    ):
        hub_session.execute(text(statement))
    hub_session.commit()


def _reserve(service: AiConfigService, estimate: str) -> UUID:
    return service.reserve("card", "run", "anthropic", MODEL, Decimal(estimate))


def _rows(session: Session) -> list[AiUsage]:
    session.expire_all()
    return list(session.scalars(select(AiUsage).order_by(AiUsage.at)))


def test_concurrent_reservations_near_the_cap_cannot_all_pass(
    hub_engine: Engine, hub_session: Session, capped: User
) -> None:
    """Eight calls ask for 0.30 each against a cap of 1.00 at the same moment: room for three.
    Before the holds every one of them read the same empty sum and all eight went out."""
    start = threading.Barrier(8)
    outcomes: list[str] = []

    def call() -> None:
        with Session(hub_engine) as session:
            start.wait()
            try:
                _reserve(_service(session), "0.30")
                outcomes.append("held")
            except LlmCapReached:
                outcomes.append("refused")

    threads = [threading.Thread(target=call) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["held"] * 3 + ["refused"] * 5
    assert _service(hub_session).spent_today("card") == Decimal("0.90")
    assert len(_rows(hub_session)) == 3


def test_a_refused_reservation_leaves_nothing_behind(hub_session: Session, capped: User) -> None:
    service = _service(hub_session)
    _reserve(service, "0.80")

    with pytest.raises(LlmCapReached):
        _reserve(service, "0.30")

    assert len(_rows(hub_session)) == 1
    assert service.spent_today("card") == Decimal("0.80")


def test_a_released_hold_frees_the_cap(hub_session: Session, capped: User) -> None:
    service = _service(hub_session)
    hold = _reserve(service, "0.80")
    assert service.spent_today("card") == Decimal("0.80")

    service.release(hold)

    assert service.spent_today("card") == Decimal(0)
    assert _rows(hub_session) == []
    _reserve(service, "0.80")  # the room is back


def test_an_expired_hold_stops_counting_and_is_cleaned(hub_session: Session, capped: User) -> None:
    _reserve(_service(hub_session), "0.80")
    later = NOON + HOLD_TTL + timedelta(seconds=1)

    assert _service(hub_session, later).spent_today("card") == Decimal(0)
    _reserve(_service(hub_session, later), "0.80")  # fits again

    [row] = _rows(hub_session)  # the expired one was deleted by the reservation
    assert row.at == later


def test_settling_writes_the_real_cost_into_the_hold_row(
    hub_session: Session, capped: User
) -> None:
    service = _service(hub_session)
    hold = _reserve(service, "0.60")

    usage = service.record_usage(
        task="card",
        purpose="run",
        provider="anthropic",
        model=MODEL,
        input_tokens=1000,
        output_tokens=200,
        hold_id=hold,
    )

    [row] = _rows(hub_session)
    assert row.id == usage.id == hold
    assert row.reserved_until is None
    assert (row.input_tokens, row.output_tokens) == (1000, 200)
    assert row.cost_eur == Decimal("0.010000")  # 1000 * 5/M + 200 * 25/M
    assert row.priced is True
    assert service.spent_today("card") == Decimal("0.010000")


def test_a_hold_that_is_gone_is_written_anew_when_the_answer_comes(
    hub_session: Session, capped: User
) -> None:
    """A call slower than the TTL: its hold was cleaned by another reservation, and the
    answer is paid for all the same."""
    hold = _reserve(_service(hub_session), "0.60")
    later = NOON + HOLD_TTL + timedelta(seconds=1)
    _reserve(_service(hub_session, later), "0.10")  # deletes the expired hold

    usage = _service(hub_session, later).record_usage(
        task="card",
        purpose="run",
        provider="anthropic",
        model=MODEL,
        input_tokens=1000,
        hold_id=hold,
    )

    assert usage.id != hold
    assert sorted(row.cost_eur or 0 for row in _rows(hub_session)) == [
        Decimal("0.005000"),
        Decimal("0.100000"),
    ]


def test_a_hold_counts_toward_spent_but_is_not_a_call_anywhere_else(
    hub_session: Session, capped: User
) -> None:
    service = _service(hub_session)
    _reserve(service, "0.40")
    service.record_usage(
        task="card", purpose="run", provider="anthropic", model=MODEL, input_tokens=1000
    )

    config = service.read()

    [card] = [task for task in config.tasks if task.task == "card"]
    assert card.spent_today_eur == Decimal("0.405000")  # the call and the hold
    [spend] = [row for row in config.spend.rows if row.task == "card"]
    assert (spend.calls, spend.cost_eur) == (1, Decimal("0.005000"))  # the call alone
    assert hub_session.scalar(select(func.count()).select_from(AiUsage)) == 2


def test_the_estimate_is_the_worst_case_at_the_dearer_of_the_output_prices(
    hub_session: Session, capped: User
) -> None:
    service = _service(hub_session)
    assert service.estimate_call("anthropic", MODEL, 1000, 2000) == Decimal("0.055000")

    service.upsert_price(
        capped.id,
        "anthropic",
        MODEL,
        Decimal("5"),
        Decimal("25"),
        thinking_eur_per_mtok=Decimal(30),
    )
    assert service.estimate_call("anthropic", MODEL, 1000, 2000) == Decimal("0.065000")
    with pytest.raises(LlmNotConfigured):
        service.estimate_call("anthropic", "no-such-model", 1, 1)


def test_save_key_binds_the_provider_and_reading_never_writes(
    hub_session: Session, capped: User
) -> None:
    """HCORE-10: a key sealed before the provider was bound opens through the fallback and
    stays as it is when read; saving it again seals it for its provider, and a ciphertext
    copied to another provider's row then does not open."""
    service = _service(hub_session)
    nonce = os.urandom(12)
    legacy = AESGCM(base64.b64decode(SECRETS_KEY)).encrypt(nonce, b"sk-legacy", None)
    anthropic = hub_session.get(AiProvider, "anthropic")
    assert anthropic is not None
    anthropic.key_ciphertext, anthropic.key_nonce, anthropic.key_last4 = legacy, nonce, "gacy"
    hub_session.commit()

    assert service.resolve_key("anthropic") == "sk-legacy"
    hub_session.refresh(anthropic)
    assert anthropic.key_ciphertext == legacy  # reading did not re-seal it

    service.save_key(capped.id, "anthropic", "sk-legacy")
    hub_session.refresh(anthropic)
    assert anthropic.key_ciphertext != legacy
    assert service.resolve_key("anthropic") == "sk-legacy"

    openai = hub_session.get(AiProvider, "openai")
    assert openai is not None
    openai.key_ciphertext, openai.key_nonce = anthropic.key_ciphertext, anthropic.key_nonce
    hub_session.commit()
    with pytest.raises(SecretsKeyInvalid):
        service.resolve_key("openai")
