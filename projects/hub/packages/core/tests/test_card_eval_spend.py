"""REB-842: what a run spends and where it is recorded: writing and judging each under the card
task for the evaluation, and the daily cap that stops a run before any call.
"""

from collections.abc import Callable, Iterator
from contextlib import closing
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from admins import an_admin
from card_eval_fixtures import API_KEY, MODEL, OTHER_MODEL, SECRETS_KEY, _person
from fakes_cards import (
    draft_body,
    judgement_body,
)
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from test_llm import Stub

from rebase_core.ai_config import CAP_REACHED, AiConfigService
from rebase_core.card_eval import (
    results_of,
    run_eval,
    sample,
)
from rebase_core.config import Settings
from rebase_core.llm import AiCall, TaskOverride
from rebase_core.models import (
    AiTask,
    AiUsage,
)

# ---- what it costs ----------------------------------------------------------------------------


@pytest.fixture
def ai_ready(hub_engine: Engine) -> Iterator[Callable[[], Any]]:
    """The card task callable: key saved, Anthropic on, a price for two models. Wiped back
    to what migration 0036 seeded."""

    def open_session() -> Any:
        return closing(Session(hub_engine))

    settings = Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]
    with open_session() as session:
        admin = an_admin(session, "root@rebase.it", "Root", "Admin")
        service = AiConfigService(session, settings)
        service.save_key(admin, "anthropic", API_KEY)
        service.set_provider_enabled(admin, "anthropic", True)
        service.upsert_price(admin, "anthropic", MODEL, Decimal("5"), Decimal("25"))
        service.upsert_price(admin, "anthropic", OTHER_MODEL, Decimal("2"), Decimal("10"))
    yield open_session
    with open_session() as session:
        for statement in (
            "DELETE FROM ai_usage",
            "DELETE FROM ai_prices",
            "UPDATE ai_providers SET key_ciphertext = NULL, key_nonce = NULL, "
            "key_last4 = NULL, saved_by = NULL, saved_at = NULL, enabled = false",
            "UPDATE ai_tasks SET provider = 'anthropic', model = 'claude-opus-5', "
            "effort = 'medium', daily_cap_eur = NULL, enabled = true",
        ):
            session.execute(text(statement))
        session.commit()


def _calls(
    ai_ready: Callable[[], Any], stub: Stub, writer: tuple[str, str], judging: tuple[str, str]
) -> tuple[Callable[[UUID], AiCall], Callable[[UUID], AiCall]]:
    settings = Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]

    def call_as(provider: str, model: str) -> Callable[[UUID], AiCall]:
        return lambda freelancer_id: AiCall(
            ai_ready,
            settings,
            "card",
            override=TaskOverride(provider, model, None),
            purpose="eval",
            freelancer_id=freelancer_id,
            model_factory=stub.factory,
        )

    return call_as(*writer), call_as(*judging)


def test_writing_and_judging_are_each_spent_under_the_card_task_for_eval(
    clean_hub: Session, ai_ready: Callable[[], Any]
) -> None:
    person = _person(clean_hub, label="lead")
    stub = Stub(
        Stub.reply(draft_body(), input_tokens=1000, output_tokens=200),
        Stub.reply(judgement_body(), input_tokens=3000, output_tokens=400),
    )
    write_as, judge_as = _calls(ai_ready, stub, ("anthropic", MODEL), ("anthropic", OTHER_MODEL))

    run = run_eval(
        clean_hub,
        write_as,
        judge_as,
        task="card",
        provider="anthropic",
        model=MODEL,
        effort=None,
        targets=sample(clean_hub),
        judge_by=("anthropic", OTHER_MODEL),
    )

    usage = clean_hub.scalars(select(AiUsage).order_by(AiUsage.at)).all()
    assert [(u.task, u.purpose, u.model, u.freelancer_id) for u in usage] == [
        ("card", "eval", MODEL, person),
        ("card", "eval", OTHER_MODEL, person),
    ]
    # 1000 in and 200 out at 5 and 25 euro per million; 3000 and 400 at 2 and 10.
    assert [u.cost_eur for u in usage] == [Decimal("0.010000"), Decimal("0.010000")]
    [result] = results_of(clean_hub, run.id)
    assert result.cost_eur == Decimal("0.010000") and result.judge_cost_eur == Decimal("0.010000")
    assert result.latency_ms is not None and result.judge_latency_ms is not None
    assert (Decimal(run.summary["cost_eur"]), Decimal(run.summary["judge_cost_eur"])) == (
        Decimal("0.01"),
        Decimal("0.01"),
    )
    assert run.summary["judge"] == {"provider": "anthropic", "model": OTHER_MODEL}
    settings = Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]
    with ai_ready() as session:
        # Both count against the task's daily cap, and the task's own settings stay.
        assert AiConfigService(session, settings).spent_today("card") == Decimal("0.02")
        task = session.get(AiTask, "card")
        assert task is not None and (task.model, task.effort) == (MODEL, "medium")


def test_the_daily_cap_stops_a_run_before_any_call_and_says_why(
    clean_hub: Session, ai_ready: Callable[[], Any]
) -> None:
    _person(clean_hub, label="lead")
    with ai_ready() as session:
        task = session.get(AiTask, "card")
        assert task is not None
        task.daily_cap_eur = Decimal("0")
        session.commit()
    stub = Stub()
    write_as, judge_as = _calls(ai_ready, stub, ("anthropic", MODEL), ("anthropic", MODEL))

    run = run_eval(
        clean_hub,
        write_as,
        judge_as,
        task="card",
        provider="anthropic",
        model=MODEL,
        effort=None,
        targets=sample(clean_hub),
        judge_by=("anthropic", MODEL),
    )

    assert stub.built == []
    assert run.summary["complete"] is False and run.summary["stopped"] == CAP_REACHED
