"""`rebase crm-sweep` (REB-868, the old `contracts-sweep`'s CRM half after REB-1108) and `rebase
cards-refresh` (REB-510): the CLI wiring alone. `CrmLeadService.link_pending`'s own behaviour is
`test_crm_link.py`'s, and `CardWriter.refresh_stale`'s is `test_cards.py`'s. No real database or
Claude is reached here: `get_settings`, `CrmLeadService` and `CardWriter` are swapped out, so the
tests prove `main` dispatches to each command and reports what it returns, nothing more."""

import base64
import json
import os
import stat
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes_cards import CARD, card_response, judge_response, text_pdf
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core import (
    ai_verify as ai_verify_module,
)
from rebase_core import (
    card_batch as card_batch_module,
)
from rebase_core import (
    card_writer as card_writer_module,
)
from rebase_core import (
    crm_link as crm_link_module,
)
from rebase_core import (
    llm as llm_module,
)
from rebase_core import (
    llm_batch as llm_batch_module,
)
from rebase_core import (
    sources as sources_module,
)
from rebase_core.ai_config import AiConfigService
from rebase_core.ai_verify import VerifyReport
from rebase_core.campaigns import tick as tick_module
from rebase_core.campaigns.tick import TickResult
from rebase_core.card_batch import BatchPolled, CardBatches
from rebase_core.card_judge import JUDGE_MAX_OUTPUT_TOKENS
from rebase_core.cli import ai as cli_ai
from rebase_core.cli import campaigns as cli_campaigns
from rebase_core.cli import cards as cli_cards
from rebase_core.cli import common as cli_common
from rebase_core.cli import crm as cli_crm
from rebase_core.cli import main
from rebase_core.cli import members as cli_members
from rebase_core.config import Settings
from rebase_core.db import session_factory
from rebase_core.errors import LlmCapReached
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.http import urllib_engagements_call
from rebase_core.llm import LlmResponse, RecordingCall, TaskOverride
from rebase_core.llm_batch import RecordingBatch
from rebase_core.models import AdminToken, CardEvalProfile, CardEvalRun, FreelancerCard, User
from rebase_core.pigro_door import SweepCount
from rebase_core.team_schemas import Card, CardsRefreshed


class _FakeCrmLeadService:
    """What `crm_sweep` (REB-868) is built with, and a canned count."""

    instances: list["_FakeCrmLeadService"] = []
    count = SweepCount(linked=0, failed=0)

    def __init__(self, session: object, settings: Settings, http: object, llm: object) -> None:
        self.http = http
        self.llm = llm
        _FakeCrmLeadService.instances.append(self)

    def link_pending(self) -> SweepCount:
        return _FakeCrmLeadService.count


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeCrmLeadService.instances = []
    _FakeCrmLeadService.count = SweepCount(linked=0, failed=0)
    monkeypatch.setattr(crm_link_module, "CrmLeadService", _FakeCrmLeadService)


def test_the_crm_sweep_command_files_the_requests_and_prints_the_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """REB-868: it reads over the seam that waits for a slow CRM, and prints the count it
    filed, and the ones it left only when there are any."""
    monkeypatch.setattr(cli_crm, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]

    assert main(["crm-sweep"]) == 0

    assert capsys.readouterr().out.strip() == "0 requests linked to the CRM"
    assert _FakeCrmLeadService.instances[0].http is urllib_engagements_call

    _FakeCrmLeadService.count = SweepCount(linked=4, failed=2)

    assert main(["crm-sweep"]) == 0

    assert capsys.readouterr().out.strip() == "4 requests linked to the CRM, 2 not linked"


def test_the_commands_the_hub_gave_to_the_crm_are_gone(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """REB-1108: signatures, the contracts' typesetting and Documenso are PigroCRM's."""
    for name in ("contracts-sweep", "contracts-check", "documenso-check"):
        with pytest.raises(SystemExit):
            main([name])
    capsys.readouterr()


def test_the_campaigns_tick_command_runs_one_pass_and_prints_what_it_did(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli_campaigns, "get_settings", lambda: Settings(_env_file=None, resend_api_key="k")
    )  # type: ignore[call-arg]
    monkeypatch.setattr(
        cli_common,
        "session_factory",
        lambda _engine: lambda: type("S", (), {"close": lambda self: None})(),
    )
    monkeypatch.setattr(cli_common, "create_engine_from_settings", lambda _settings: None)
    monkeypatch.setattr(
        tick_module,
        "run_tick",
        lambda *_a, **_k: TickResult(campaigns=1, sent=2, skipped=1, failed=0, stamped=3),
    )
    assert main(["campaigns-tick"]) == 0
    assert (
        capsys.readouterr().out.strip()
        == "1 campaigns, 2 sent, 1 skipped, 0 failed, 3 outcomes recorded"
    )


def test_without_a_key_the_tick_sends_nothing_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_campaigns, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    assert main(["campaigns-tick"]) == 0
    assert "sending not configured" in capsys.readouterr().out


class _FakeCardWriter:
    """Records what `cards_refresh` built it with and the limit it was asked for."""

    instances: list["_FakeCardWriter"] = []
    result = CardsRefreshed(written=3, failed=1)

    def __init__(self, session: object, llm: object, **options: object) -> None:
        self.session = session
        self.llm = llm
        self.options = options
        self.limits: list[int] = []
        _FakeCardWriter.instances.append(self)

    def refresh_stale(self, limit: int = 50) -> CardsRefreshed:
        self.limits.append(limit)
        return self.result


_CARD_CALL = RecordingCall([])
_TASKS_ASKED: list[str] = []


def _configured_card_task(opener: object, settings: Settings, task: str) -> RecordingCall:
    """`call_for_task` with a model configured: the seam itself is `test_llm.py`'s."""
    _TASKS_ASKED.append(task)
    return _CARD_CALL


def test_cards_refresh_prints_the_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One batch of `--limit` per run, the oldest first (`refresh_stale`), so the
    operator runs it again until it prints «0 cards written»."""
    _FakeCardWriter.instances = []
    monkeypatch.setattr(cli_cards, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(llm_module, "call_for_task", _configured_card_task)
    monkeypatch.setattr(card_writer_module, "CardWriter", _FakeCardWriter)

    assert main(["cards-refresh", "--limit", "10"]) == 0

    [writer] = _FakeCardWriter.instances
    assert writer.llm is _CARD_CALL
    assert writer.options == {"workers": 5}  # `REBASE_CARDS_WORKERS`' default
    assert _TASKS_ASKED[-1] == "card"
    assert writer.limits == [10]
    assert capsys.readouterr().out.strip() == "3 cards written, 1 failed"


@pytest.mark.parametrize(
    ("result", "line"),
    [
        (
            CardsRefreshed(written=3, failed=1),
            "3 cards written, 1 failed",
        ),
        # An outage: counted among the failed, and the stop said.
        (
            CardsRefreshed(written=1, failed=1, stopped=True),
            "1 cards written, 1 failed, stopped: model unavailable",
        ),
    ],
    ids=["failures", "outage"],
)
def test_cards_refresh_says_the_stop(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: CardsRefreshed,
    line: str,
) -> None:
    """A batch stopped by an outage says so; with no stop, the line is the plain one, so
    «0 cards written, 0 failed» still says the backlog is done. A card still
    gendered after its rewrite is among the «failed» (REB-802)."""
    _FakeCardWriter.instances = []
    monkeypatch.setattr(cli_cards, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(llm_module, "call_for_task", _configured_card_task)
    monkeypatch.setattr(card_writer_module, "CardWriter", _FakeCardWriter)
    monkeypatch.setattr(_FakeCardWriter, "result", result)

    assert main(["cards-refresh"]) == 0

    assert capsys.readouterr().out.strip() == line


class _FakeRegeneratingWriter(_FakeCardWriter):
    running = False
    started: list[tuple[object, int, str]] = []

    def regeneration_running(self) -> bool:
        return self.running

    def regenerate_stale(self, admin_id: object, limit: int, *, source: str) -> CardsRefreshed:
        self.started.append((admin_id, limit, source))
        return self.result


@pytest.fixture
def regen_admin(eval_db: Session) -> UUID:
    """The settings point at the test database, where an administrator and a member exist:
    the command resolves `--admin` against it. Cleaned by `eval_db`."""
    admin = User(email="ivan@rebase.it", first_name="Ivan", last_name="Sala", role="admin")
    member = User(email="ada@example.org", first_name="Ada", last_name="Lovelace", role="member")
    eval_db.add_all([admin, member])
    eval_db.commit()
    return admin.id


@pytest.mark.parametrize(
    ("argv", "threshold"), [(["cards-regenerate"], 10), (["cards-regenerate", "--batch"], 1)]
)
def test_cards_regenerate_goes_through_the_batch_api_above_the_threshold(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    regen_admin: UUID,
    argv: list[str],
    threshold: int,
) -> None:
    """REB-853: `--batch` always, otherwise from `REBASE_CARDS_BATCH_THRESHOLD` cards up;
    the workers are `REBASE_CARDS_WORKERS`. REB-861: signed by `--admin`, whose address is
    matched without regard to case, with `source` `cli`."""
    _FakeCardWriter.instances = []
    _FakeRegeneratingWriter.started = []
    monkeypatch.setattr(llm_module, "call_for_task", _configured_card_task)
    monkeypatch.setattr(
        llm_batch_module, "batch_for_task", lambda opener, settings, task: RecordingBatch()
    )
    monkeypatch.setattr(card_writer_module, "CardWriter", _FakeRegeneratingWriter)
    monkeypatch.setattr(
        _FakeRegeneratingWriter, "result", CardsRefreshed(written=0, failed=0, batched=14)
    )

    assert main([*argv, "--limit", "20", "--admin", " Ivan@Rebase.it "]) == 0

    [writer] = _FakeCardWriter.instances
    assert writer.options["workers"] == 5
    batches = writer.options["batches"]
    assert isinstance(batches, CardBatches) and batches.threshold == threshold
    assert _FakeRegeneratingWriter.started == [(regen_admin, 20, "cli")]
    assert "14 cards sent to the Batch API" in capsys.readouterr().out


def test_cards_regenerate_without_a_batch_api_writes_with_the_workers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], regen_admin: UUID
) -> None:
    _FakeCardWriter.instances = []
    _FakeRegeneratingWriter.started = []
    monkeypatch.setattr(llm_module, "call_for_task", _configured_card_task)
    monkeypatch.setattr(llm_batch_module, "batch_for_task", lambda opener, settings, task: None)
    monkeypatch.setattr(card_writer_module, "CardWriter", _FakeRegeneratingWriter)
    monkeypatch.setattr(_FakeRegeneratingWriter, "result", CardsRefreshed(written=4, failed=1))

    assert main(["cards-regenerate", "--batch", "--admin", "ivan@rebase.it"]) == 0

    [writer] = _FakeCardWriter.instances
    assert writer.options["batches"] is None
    out = capsys.readouterr().out
    assert "has no Batch API" in out and "4 cards written, 1 failed" in out


def test_cards_regenerate_says_when_one_is_already_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], regen_admin: UUID
) -> None:
    _FakeCardWriter.instances = []
    _FakeRegeneratingWriter.started = []
    monkeypatch.setattr(llm_module, "call_for_task", _configured_card_task)
    monkeypatch.setattr(llm_batch_module, "batch_for_task", lambda opener, settings, task: None)
    monkeypatch.setattr(card_writer_module, "CardWriter", _FakeRegeneratingWriter)
    monkeypatch.setattr(_FakeRegeneratingWriter, "running", True)

    assert main(["cards-regenerate", "--admin", "ivan@rebase.it"]) == 0
    monkeypatch.setattr(_FakeRegeneratingWriter, "running", False)

    assert _FakeRegeneratingWriter.started == []
    assert "already running" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        # REB-861: a regeneration is always signed, so nothing starts without an admin.
        ([], "--admin needs an administrator's address"),
        (["--admin", "  "], "--admin needs an administrator's address"),
        (["--admin", "nessuno@rebase.it"], "No administrator with email nessuno@rebase.it"),
        # A member of the hub is not an administrator.
        (["--admin", "ada@example.org"], "No administrator with email ada@example.org"),
    ],
    ids=["missing", "blank", "unknown", "member"],
)
def test_cards_regenerate_refuses_without_an_administrator(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    regen_admin: UUID,
    argv: list[str],
    message: str,
) -> None:
    _FakeCardWriter.instances = []
    _FakeRegeneratingWriter.started = []
    monkeypatch.setattr(llm_module, "call_for_task", _configured_card_task)
    monkeypatch.setattr(llm_batch_module, "batch_for_task", lambda opener, settings, task: None)
    monkeypatch.setattr(card_writer_module, "CardWriter", _FakeRegeneratingWriter)

    assert main(["cards-regenerate", *argv]) == 2

    captured = capsys.readouterr()
    assert message in captured.err and captured.out == ""
    assert _FakeCardWriter.instances == [] and _FakeRegeneratingWriter.started == []


@pytest.mark.parametrize(
    ("polled", "line"),
    [
        (BatchPolled("none"), "no card batch in progress"),
        (
            BatchPolled("running", left=14),
            "batch in progress at the provider: 14 cards left to apply",
        ),
        (
            BatchPolled("applied", written=13, failed=1),
            "batch finished: 13 cards written, 1 failed",
        ),
        (
            BatchPolled("applying", written=5, failed=0, left=9),
            "5 cards written, 0 failed, 9 still to apply",
        ),
        (BatchPolled("unavailable"), "the provider does not answer: will retry on the next pass"),
    ],
)
def test_cards_batch_poll_says_where_the_batch_stands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    polled: BatchPolled,
    line: str,
) -> None:
    """One pass of the loop: it always exits 0, so the `cards` service keeps running."""

    class FakeBatches:
        def __init__(self, call: object, threshold: int) -> None:
            pass

        def poll(self, writer: object) -> BatchPolled:
            return polled

    _FakeCardWriter.instances = []
    monkeypatch.setattr(cli_cards, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(llm_module, "call_for_task", _configured_card_task)
    monkeypatch.setattr(
        llm_batch_module, "AiBatch", lambda opener, settings, task: RecordingBatch()
    )
    monkeypatch.setattr(card_batch_module, "CardBatches", FakeBatches)
    monkeypatch.setattr(card_writer_module, "CardWriter", _FakeCardWriter)

    assert main(["cards-batch-poll"]) == 0

    assert capsys.readouterr().out.strip() == line


def test_cards_refresh_names_models_ai_when_no_task_configured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no model configured for the card task a run would print «0 cards written» for
    ever: it says why instead, and where to fix it."""
    _FakeCardWriter.instances = []
    monkeypatch.setattr(cli_cards, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(llm_module, "call_for_task", lambda opener, settings, task: None)
    monkeypatch.setattr(card_writer_module, "CardWriter", _FakeCardWriter)

    assert main(["cards-refresh"]) == 1

    assert _FakeCardWriter.instances == []
    assert capsys.readouterr().err.strip() == (
        "No model configured for the cards: open «Modelli AI»."
    )


# ---- sources-refetch (REB-839) ------------------------------------------------------------


class _FakeSources:
    """What `sources_refetch` builds in place of the service on the network: the switches
    it is told, the people it was asked to read."""

    enabled: set[str] = set()
    asked: list[int] = []

    def __init__(self, session: object) -> None:
        self.session = session

    def enabled_kinds(self) -> set[str]:
        return self.enabled

    def refetch_due(self, limit: int) -> int:
        self.asked.append(limit)
        return 2


@pytest.fixture
def fake_sources(monkeypatch: pytest.MonkeyPatch, hub_engine: Engine) -> type[_FakeSources]:
    url = hub_engine.url.render_as_string(hide_password=False)
    monkeypatch.setattr(
        cli_cards,
        "get_settings",
        lambda: Settings(database_url=url, _env_file=None),  # type: ignore[call-arg]
    )
    monkeypatch.setattr(sources_module, "default_source_service", _FakeSources)
    _FakeSources.enabled, _FakeSources.asked = set(), []
    return _FakeSources


def test_sources_refetch_reads_nothing_while_every_switch_is_off(
    fake_sources: type[_FakeSources], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["sources-refetch"]) == 0

    assert fake_sources.asked == []
    assert capsys.readouterr().out.strip() == "No source switched on: open «Modelli AI»."


def test_sources_refetch_reads_the_limit_and_calls_no_model(
    fake_sources: type[_FakeSources],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The weekly pass is on a machine with no model configured too: it never asks for
    the `card` task's seam."""
    fake_sources.enabled = {"github"}
    monkeypatch.setattr(
        llm_module, "call_for_task", lambda *args: pytest.fail("the weekly pass asked for a model")
    )

    assert main(["sources-refetch", "--limit", "7"]) == 0

    assert fake_sources.asked == [7]
    assert capsys.readouterr().out.strip() == "2 people reviewed"
    assert main(["sources-refetch", "--limit", "0"]) == 2


# ---- ai-seed / ai-rotate-secrets (REB-832) ----------------------------------------------

_SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()
_OLD_KEY = "sk-ant-old-key-abcd"


@pytest.fixture
def ai_db(hub_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The command's settings point at the test database; the AI tables go back to what
    migration 0036 seeded afterwards."""

    def use(secrets_key: str = _SECRETS_KEY) -> None:
        url = hub_engine.url.render_as_string(hide_password=False)
        monkeypatch.setattr(
            cli_ai,
            "get_settings",
            lambda: Settings(database_url=url, secrets_key=secrets_key, _env_file=None),  # type: ignore[call-arg]
        )

    use()
    monkeypatch.setenv("REBASE_ANTHROPIC_API_KEY", _OLD_KEY)
    monkeypatch.setenv("REBASE_TEAM_BUILDER_MODEL", "claude-opus-5")
    yield use
    with hub_engine.begin() as conn:
        for statement in (
            "DELETE FROM ai_prices",
            "UPDATE ai_providers SET key_ciphertext = NULL, key_nonce = NULL, "
            "key_last4 = NULL, saved_by = NULL, saved_at = NULL, enabled = false",
            "UPDATE ai_tasks SET provider = 'anthropic', model = 'claude-opus-5'",
        ):
            conn.execute(text(statement))


def test_ai_seed_stores_encrypted_and_prints_hint_only(
    ai_db: Any, hub_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ai-seed", "--eur-per-usd", "0.92"]) == 0

    captured = capsys.readouterr()
    assert "key saved, ends with …abcd" in captured.out
    assert _OLD_KEY not in captured.out + captured.err
    with hub_engine.connect() as conn:
        row = conn.execute(
            text("SELECT key_ciphertext, enabled FROM ai_providers WHERE provider = 'anthropic'")
        ).one()
        prices = conn.execute(text("SELECT count(*) FROM ai_prices")).scalar_one()
    assert row.enabled is True
    assert _OLD_KEY.encode() not in bytes(row.key_ciphertext)
    assert prices == 3


def test_ai_seed_twice_refuses_and_changes_nothing(
    ai_db: Any, hub_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ai-seed", "--eur-per-usd", "0.92"]) == 0
    capsys.readouterr()
    with hub_engine.connect() as conn:
        before = conn.execute(text("SELECT key_ciphertext FROM ai_providers")).all()

    assert main(["ai-seed", "--eur-per-usd", "0.92"]) == 1

    assert "non la sovrascrive" in capsys.readouterr().err
    with hub_engine.connect() as conn:
        assert conn.execute(text("SELECT key_ciphertext FROM ai_providers")).all() == before


def test_ai_seed_without_secrets_key_exits_1_with_sentence(
    ai_db: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    ai_db("")

    assert main(["ai-seed", "--eur-per-usd", "0.92"]) == 1

    assert "REBASE_SECRETS_KEY" in capsys.readouterr().err


@pytest.mark.parametrize("rate", ["NaN", "Infinity", "-1", "0", "100000"])
def test_ai_seed_refuses_a_rate_that_is_not_a_sensible_number_with_a_sentence(
    ai_db: Any, capsys: pytest.CaptureFixture[str], rate: str
) -> None:
    """Review of #666, nit 3: `NaN` used to reach a comparison and print a traceback."""
    assert main(["ai-seed", f"--eur-per-usd={rate}"]) == 1
    err = capsys.readouterr().err
    assert "il cambio deve essere un numero positivo" in err
    assert "Traceback" not in err


def test_ai_seed_requires_the_exchange_rate(ai_db: Any) -> None:
    with pytest.raises(SystemExit):
        main(["ai-seed"])


def test_ai_prices_seed_writes_the_table_and_keeps_edited_rows(
    ai_db: Any, hub_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with hub_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ai_prices (provider, model, input_eur_per_mtok, output_eur_per_mtok) "
                "VALUES ('openai', 'gpt-6.1-sol', 7, 8)"
            )
        )

    assert main(["ai-prices-seed", "--eur-per-usd", "0.92"]) == 0

    assert "4 prices written, 1 already present" in capsys.readouterr().out
    with hub_engine.connect() as conn:
        edited = conn.execute(
            text("SELECT input_eur_per_mtok FROM ai_prices WHERE model = 'gpt-6.1-sol'")
        ).scalar_one()
        enabled = conn.execute(text("SELECT count(*) FROM ai_providers WHERE enabled")).scalar_one()
    assert edited == 7
    assert enabled == 0


def test_ai_prices_seed_refuses_a_bad_rate(ai_db: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ai-prices-seed", "--eur-per-usd", "0"]) == 1
    assert "il cambio deve essere un numero positivo" in capsys.readouterr().err
    assert main(["ai-prices-seed", "--eur-per-usd", "abc"]) == 2


def test_cli_requires_saved_key_and_exits_1_without(
    ai_db: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ai-verify", "--provider", "google", "--model", "gemini-4-argon"]) == 1
    assert "No key for google" in capsys.readouterr().err


def test_ai_verify_prints_the_report_and_never_the_key(
    ai_db: Any,
    hub_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = "AIza-test-key-not-real-9876"
    with Session(hub_engine) as session:
        admin = User(email="ivan@rebase.it", first_name="Ivan", last_name="Sala", role="admin")
        session.add(admin)
        session.commit()
        AiConfigService(session, cli_ai.get_settings()).save_key(admin.id, "google", key)
    seen: list[str] = []

    def fake_verify(provider: str, model: str, api_key: str) -> VerifyReport:
        seen.append(api_key)
        return VerifyReport(True, True, False, True, "ModelHTTPError (status 400)")

    monkeypatch.setattr(ai_verify_module, "verify", fake_verify)

    assert main(["ai-verify", "--provider", "google", "--model", "gemini-4-argon"]) == 1

    out = capsys.readouterr()
    assert seen == [key]
    assert "key_injected=True native_output_ok=True thinking_ok=False" in out.out
    assert key not in out.out + out.err
    with hub_engine.begin() as conn:
        conn.execute(text("UPDATE ai_providers SET saved_by = NULL"))
        conn.execute(text("DELETE FROM admin_actions"))
        conn.execute(text("DELETE FROM users"))


def test_ai_rotate_secrets_command_reencrypts_and_prints_the_count(
    ai_db: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ai-seed", "--eur-per-usd", "0.92"]) == 0
    capsys.readouterr()
    new = base64.b64encode(os.urandom(32)).decode()

    monkeypatch.setenv("REBASE_SECRETS_KEY_OLD", _SECRETS_KEY)
    monkeypatch.setenv("REBASE_SECRETS_KEY_NEW", new)

    assert main(["ai-rotate-secrets"]) == 0
    assert capsys.readouterr().out.strip() == "1 keys re-sealed"

    # The old key no longer opens it.
    assert main(["ai-rotate-secrets"]) == 1
    assert "REBASE_SECRETS_KEY" in capsys.readouterr().err


def test_ai_rotate_secrets_takes_no_key_on_the_command_line(
    ai_db: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review of #666, minor 1: what opens every stored key is in the environment, not in
    `ps`; without both values the command says so and changes nothing."""
    monkeypatch.delenv("REBASE_SECRETS_KEY_OLD", raising=False)
    monkeypatch.delenv("REBASE_SECRETS_KEY_NEW", raising=False)

    assert main(["ai-rotate-secrets"]) == 2
    assert "REBASE_SECRETS_KEY_OLD" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["ai-rotate-secrets", "--old-key", _SECRETS_KEY, "--new-key", _SECRETS_KEY])


# ---- cards-eval (REB-842) ----------------------------------------------------------------

# The CV dates its one role: the card's nine years are 2017 - 2025.
_CV = text_pdf("Backend developer, Acme, 2017 - 2025. Python, FastAPI, AWS, a Torino.")


@pytest.fixture
def eval_db(hub_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """The commands' settings point at the test database (the seam itself is
    `test_llm.py`'s), and the evaluation tables are emptied after."""
    url = hub_engine.url.render_as_string(hide_password=False)
    monkeypatch.setattr(
        cli_cards,
        "get_settings",
        lambda: Settings(database_url=url, _env_file=None),  # type: ignore[call-arg]
    )
    session = session_factory(hub_engine)()
    yield session
    session.rollback()
    for statement in (
        "DELETE FROM card_eval_runs",
        "DELETE FROM admin_actions",
        "DELETE FROM ai_usage",
        "DELETE FROM freelancer_cards",
        "DELETE FROM freelancers",
        "DELETE FROM users",
    ):
        session.execute(text(statement))
    session.commit()
    session.close()


def _person(
    session: Session, email: str, last_name: str = "Lovelace", label: str | None = None
) -> UUID:
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(
            first_name="Ada",
            last_name=last_name,
            email=email,
            daily_rate=Decimal("450.00"),
            job_title="Backend developer",
            remote="remote",
        ),
        _CV,
        "cv.pdf",
        "",
    )
    if label is not None:
        session.add(CardEvalProfile(freelancer_id=read.id, label=label))
        session.commit()
    return read.id


def _bare_card() -> LlmResponse:
    """An answer under prompt version 1: the card alone."""
    return LlmResponse(
        text=json.dumps(CARD),
        parsed=Card.model_validate(CARD),
        stop_reason="end_turn",
        refusal_category=None,
        model="claude-opus-5",
        input_tokens=900,
        output_tokens=150,
        cache_read_tokens=0,
    )


def _fake_ai_calls(
    monkeypatch: pytest.MonkeyPatch, *responses: Any
) -> tuple[list[dict[str, Any]], RecordingCall]:
    """`AiCall` replaced by one scripted call that every build hands back, in the order the
    run makes them (a card, then its judgement, person after person); what each build was
    asked for is recorded."""
    built: list[dict[str, Any]] = []
    llm = RecordingCall(list(responses))

    def fake_ai_call(opener: object, settings: Settings, task: str, **kwargs: Any) -> RecordingCall:
        built.append({"task": task, **kwargs})
        return llm

    monkeypatch.setattr(llm_module, "AiCall", fake_ai_call)
    return built, llm


def test_cards_eval_exits_1_when_the_sample_is_empty(
    eval_db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built, _ = _fake_ai_calls(monkeypatch)

    assert main(["cards-eval", "--allow-disabled"]) == 1

    assert "Il campione di valutazione è vuoto" in capsys.readouterr().err
    assert built == []


def test_cards_eval_prints_the_report_of_the_sample_as_the_task_is_set(
    eval_db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    person = _person(eval_db, "ada@studio.it", label="senza date")
    built, _ = _fake_ai_calls(monkeypatch, card_response(), judge_response())

    code = main(["cards-eval", "--allow-disabled"])

    assert code == 0
    out = capsys.readouterr().out
    # With no provider or model given, the `card` task's own (migration 0036's seed).
    assert "anthropic/claude-opus-5, effort medium, prompt v3" in out
    assert "giudice anthropic/claude-opus-5-5" in out
    assert "senza date" in out and str(person)[:8] in out and "campi supportati 8/8" in out
    for private in ("Lovelace", "Backend developer", "Python", "Torino"):
        assert private not in out
    writer, judging = built
    live = TaskOverride("anthropic", "claude-opus-5", "medium", allow_disabled=True)
    assert writer == {
        "task": "card",
        "override": live,
        "purpose": "eval",
        "freelancer_id": person,
    }
    # Not the writer: the default judge is the first of DEFAULT_JUDGES that differs from it.
    assert judging["override"] == TaskOverride(
        "anthropic", "claude-opus-5-5", None, True, max_output_tokens=JUDGE_MAX_OUTPUT_TOKENS
    )
    assert eval_db.scalars(select(FreelancerCard)).all() == []
    assert eval_db.scalars(select(CardEvalRun)).one().summary["complete"] is True


def test_cards_eval_writes_with_the_provider_model_effort_and_prompt_it_was_asked(
    eval_db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    person = _person(eval_db, "ada@studio.it", label="lead")
    built, llm = _fake_ai_calls(monkeypatch, _bare_card(), judge_response())

    code = main(
        ["cards-eval", "--freelancer", str(person), "--provider", "openai", "--model", "gpt-5"]
        + ["--effort", "high", "--prompt-version", "1", "--allow-disabled"]
        + ["--judge-provider", "google", "--judge-model", "gemini-3"]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "openai/gpt-5, effort high, prompt v1" in out
    assert "giudice google/gemini-3" in out and "stesso modello" not in out
    assert "ATTENZIONE: --allow-disabled" in out
    writer, judging = built
    assert writer["override"] == TaskOverride("openai", "gpt-5", "high", allow_disabled=True)
    # The judge has its own output ceiling; the writer keeps the task's.
    assert judging["override"] == TaskOverride(
        "google", "gemini-3", None, True, max_output_tokens=JUDGE_MAX_OUTPUT_TOKENS
    )
    # Version 1 reads the CV alone.
    assert "Sources available" not in llm.requests[0].messages[0].text
    assert eval_db.scalars(select(CardEvalRun)).one().prompt_version == 1


def test_cards_eval_takes_the_people_named_or_the_first_of_the_sample(
    eval_db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _person(eval_db, "ada@studio.it", label="lead")
    grace = _person(eval_db, "grace@studio.it", "Hopper", label="duplicato")
    outside = _person(eval_db, "alan@studio.it", "Turing")
    built, _ = _fake_ai_calls(monkeypatch, card_response(), judge_response())

    assert main(["cards-eval", "--sample", "1", "--allow-disabled"]) == 0
    assert {call["freelancer_id"] for call in built} == {grace}  # «duplicato» comes first
    capsys.readouterr()

    built.clear()
    _fake_ai_calls(monkeypatch, card_response(), judge_response())
    assert main(["cards-eval", "--freelancer", str(outside), "--allow-disabled"]) == 0
    assert "fuori campione" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["--sample", "2", "--freelancer", str(uuid4())],
        ["--provider", "openai"],
        ["--model", "gpt-5"],
        ["--judge-provider", "openai"],
        ["--judge-model", "gpt-5"],
    ],
)
def test_cards_eval_refuses_options_that_do_not_go_together(
    eval_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: list[str],
) -> None:
    built, _ = _fake_ai_calls(monkeypatch)

    assert main(["cards-eval", "--allow-disabled", *args]) == 2

    assert built == [] and capsys.readouterr().err


def test_cards_eval_never_lets_the_writer_judge_itself(
    eval_db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _person(eval_db, "ada@studio.it", label="lead")
    built, _ = _fake_ai_calls(monkeypatch, card_response(), judge_response())

    same = ["--provider", "anthropic", "--model", "claude-opus-5-5"]
    assert (
        main(
            ["cards-eval", "--allow-disabled", *same, "--judge-provider", "anthropic"]
            + ["--judge-model", "claude-opus-5-5"]
        )
        == 2
    )
    assert "same model as the writer" in capsys.readouterr().err
    assert built == []

    # A writer that is the first default hands the judging to the next one.
    assert main(["cards-eval", "--allow-disabled", *same]) == 0
    assert "giudice anthropic/claude-sonnet-5-5" in capsys.readouterr().out
    assert built[1]["override"].model == "claude-sonnet-5-5"


def test_cards_eval_exits_1_and_says_so_when_the_cap_stops_it(
    eval_db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _person(eval_db, "ada@studio.it", label="lead")

    class Spent:
        def complete(self, request: object) -> object:
            raise LlmCapReached("Tetto giornaliero raggiunto")

    monkeypatch.setattr(llm_module, "AiCall", lambda *args, **kwargs: Spent())

    assert main(["cards-eval", "--allow-disabled"]) == 1
    assert "INCOMPLETA, fermata qui: Tetto giornaliero raggiunto" in capsys.readouterr().out


def test_cards_eval_refuses_a_provider_that_is_off_unless_allowed(
    eval_db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _person(eval_db, "ada@studio.it", label="lead")
    built, _ = _fake_ai_calls(monkeypatch)

    assert main(["cards-eval", "--provider", "openai", "--model", "gpt-5"]) == 1
    # The writer's provider on, the judge's off: the judge's is refused too.
    eval_db.execute(text("UPDATE ai_providers SET enabled = true WHERE provider = 'openai'"))
    eval_db.commit()
    try:
        code = main(
            ["cards-eval", "--provider", "openai", "--model", "gpt-5"]
            + ["--judge-provider", "google", "--judge-model", "gemini-3"]
        )
    finally:
        eval_db.execute(text("UPDATE ai_providers SET enabled = false WHERE provider = 'openai'"))
        eval_db.commit()

    assert code == 1
    err = capsys.readouterr().err
    assert err.count("Il fornitore è spento") == 2 and "--allow-disabled" in err
    assert "(openai)" in err and "(google)" in err
    assert built == []
    assert eval_db.scalars(select(CardEvalRun)).all() == []


def test_cards_eval_passes_an_explicit_baseline_and_refuses_one_that_is_not_there(
    eval_db: Session, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _person(eval_db, "ada@studio.it", label="lead")
    built, _ = _fake_ai_calls(monkeypatch, card_response(), judge_response())

    code = main(["cards-eval", "--allow-disabled", "--baseline", str(uuid4())])

    assert code == 1
    assert "non trovato" in capsys.readouterr().err
    assert built == []


def test_cards_eval_takes_only_a_known_provider_effort_and_prompt_version() -> None:
    with pytest.raises(SystemExit):
        main(["cards-eval", "--provider", "mistral", "--model", "x"])
    with pytest.raises(SystemExit):
        main(["cards-eval", "--provider", "openai", "--model", "x", "--effort", "extreme"])
    with pytest.raises(SystemExit):
        main(["cards-eval", "--prompt-version", "7"])


def test_the_manual_evaluation_command_is_gone() -> None:
    with pytest.raises(SystemExit):
        main(["cards-eval-draft", "--freelancer", str(uuid4()), "--label", "x"])


@pytest.fixture
def token_db(hub_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """The command's settings point at the test database, with one admin in it."""
    url = hub_engine.url.render_as_string(hide_password=False)
    monkeypatch.setattr(
        cli_members,
        "get_settings",
        lambda: Settings(database_url=url, _env_file=None),  # type: ignore[call-arg]
    )
    session = session_factory(hub_engine)()
    session.add(User(email="ivan@rebase.it", first_name="Ivan", last_name="", role="admin"))
    session.commit()
    yield session
    session.rollback()
    session.execute(text("DELETE FROM admin_tokens"))
    session.execute(text("DELETE FROM users"))
    session.commit()
    session.close()


def test_createtoken_prints_the_token_once_on_stdout(
    token_db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["createtoken", "--email", " Ivan@Rebase.it "]) == 0

    printed = capsys.readouterr().out.strip()
    assert printed.startswith("reb_")
    [row] = token_db.scalars(select(AdminToken)).all()
    assert row.expires_at is None
    assert printed.startswith(row.prefix)


def test_createtoken_with_a_token_file_writes_it_0600_and_prints_nothing_of_it(
    token_db: Session, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "token"

    assert main(["createtoken", "--email", "ivan@rebase.it", "--token-file", str(target)]) == 0

    written = target.read_text().strip()
    out = capsys.readouterr().out
    assert written.startswith("reb_") and written not in out
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    [row] = token_db.scalars(select(AdminToken)).all()
    assert written.startswith(row.prefix)


def test_createtoken_never_overwrites_a_token_file_and_mints_nothing_then(
    token_db: Session, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "token"
    target.write_text("keep me")

    assert main(["createtoken", "--email", "ivan@rebase.it", "--token-file", str(target)]) == 2

    assert target.read_text() == "keep me"
    assert token_db.scalars(select(AdminToken)).all() == []
    assert "already exists" in capsys.readouterr().err


def test_createtoken_leaves_no_empty_file_when_no_token_was_minted(
    token_db: Session, tmp_path: Path
) -> None:
    target = tmp_path / "token"

    assert main(["createtoken", "--email", "nessuno@rebase.it", "--token-file", str(target)]) == 1

    assert not target.exists()


def test_createtoken_expires_after_the_days_asked(token_db: Session) -> None:
    assert main(["createtoken", "--email", "ivan@rebase.it", "--expires-in-days", "3"]) == 0

    [row] = token_db.scalars(select(AdminToken)).all()
    assert row.expires_at is not None
    left = row.expires_at - datetime.now(UTC)
    assert timedelta(days=2, hours=23) < left <= timedelta(days=3)
    assert main(["createtoken", "--email", "ivan@rebase.it", "--expires-in-days", "0"]) == 2


def test_createtoken_and_setrole_fail_rather_than_wait_for_input_without_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["createtoken"]) == 2
    assert main(["setrole", "--role", "admin"]) == 2
    assert capsys.readouterr().err.count("when there is no terminal") == 2
