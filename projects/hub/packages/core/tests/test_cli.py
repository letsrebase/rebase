"""`rebase contracts-sweep` (REB-391), `rebase documenso-check` (REB-393) and `rebase
cards-refresh` (REB-510): the CLI wiring alone. `SigningService.sweep`'s own behaviour
is `test_signing.py`'s (`test_sweep_*`), `documenso_check`'s own behaviour, against a
fake Documenso, is `test_documenso.py`'s, and `CardWriter.refresh_stale`'s is
`test_cards.py`'s. No real database, Documenso or Claude is reached here:
`get_settings`, `SigningService`, `CardWriter` and the settings `documenso_check` reads
are all swapped out, so the tests prove `main` dispatches to each command and reports
what it returns, nothing more."""

import base64
import os
from typing import Any

import pytest
from sqlalchemy import Engine, text

from rebase_core import cli, signing
from rebase_core.campaigns.tick import TickResult
from rebase_core.cli import main
from rebase_core.config import Settings
from rebase_core.engagements import EngagementService
from rebase_core.http import urllib_engagements_call
from rebase_core.llm import RecordingCall
from rebase_core.signing import SweepResult
from rebase_core.team_schemas import CardsRefreshed


class _FakeSigningService:
    """Records the session and the collaborators `contracts_sweep` built it with, and
    answers `sweep()` with a canned result."""

    instances: list["_FakeSigningService"] = []
    result = SweepResult(touched=3, unconfirmed=0)

    def __init__(self, session: object, **kwargs: Any) -> None:
        self.session = session
        self.kwargs = kwargs
        _FakeSigningService.instances.append(self)

    def sweep(self) -> SweepResult:
        return _FakeSigningService.result


@pytest.fixture(autouse=True)
def _reset() -> None:
    _FakeSigningService.instances = []
    _FakeSigningService.result = SweepResult(touched=3, unconfirmed=0)


def test_the_contracts_sweep_command_builds_the_signing_service_and_prints_the_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(signing, "SigningService", _FakeSigningService)

    assert main(["contracts-sweep"]) == 0

    assert len(_FakeSigningService.instances) == 1
    # Built by `signing_from_settings`: without a Documenso or a mail key, both are off.
    kwargs = _FakeSigningService.instances[0].kwargs
    assert (kwargs["documenso"], kwargs["sender"]) == (None, None)
    # And with the link to Pigro, over the seam that waits for a new space (REB-499).
    assert isinstance(kwargs["engagements"], EngagementService)
    assert kwargs["engagements"].http is urllib_engagements_call
    out = capsys.readouterr().out
    assert out.strip() == "3 documenti ripresi, 0 match collegati a Pigro"


def test_the_contracts_sweep_command_also_prints_the_unconfirmed_count_when_it_is_not_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """REB-431: a refusal or an unreachable Documenso during the confirmation step
    leaves a document `inviato` -- not silently, any more. Left off the line entirely
    when it is zero, so the ordinary run reads exactly as it always has."""
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(signing, "SigningService", _FakeSigningService)
    _FakeSigningService.result = SweepResult(touched=1, unconfirmed=2)

    assert main(["contracts-sweep"]) == 0

    out = capsys.readouterr().out
    assert out.strip() == "1 documenti ripresi, 2 non confermati, 0 match collegati a Pigro"


def test_contracts_sweep_prints_the_pigro_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """REB-499: the round after the documents says how many active matches it linked to
    their deal on Pigro, always, and how many it tried and left unlinked when there are
    any, so a CRM that stopped answering shows on the same line."""
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(signing, "SigningService", _FakeSigningService)
    _FakeSigningService.result = SweepResult(touched=2, unconfirmed=0, linked=3, link_failed=1)

    assert main(["contracts-sweep"]) == 0

    out = capsys.readouterr().out
    assert out.strip() == "2 documenti ripresi, 3 match collegati a Pigro, 1 non collegati"

    _FakeSigningService.result = SweepResult(touched=0, unconfirmed=1, linked=1, link_failed=0)

    assert main(["contracts-sweep"]) == 0

    out = capsys.readouterr().out
    assert out.strip() == "0 documenti ripresi, 1 non confermati, 1 match collegati a Pigro"


def test_the_documenso_check_command_dispatches_to_documenso_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No Documenso configured: `main` reaches `documenso_check` with this process's
    settings and reports its refusal, rather than doing nothing on an unknown command."""
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]

    assert main(["documenso-check"]) == 1

    assert "la firma è spenta" in capsys.readouterr().err


def test_the_campaigns_tick_command_runs_one_pass_and_prints_what_it_did(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, resend_api_key="k"))  # type: ignore[call-arg]
    monkeypatch.setattr(
        cli,
        "session_factory",
        lambda _engine: lambda: type("S", (), {"close": lambda self: None})(),
    )
    monkeypatch.setattr(cli, "create_engine_from_settings", lambda _settings: None)
    monkeypatch.setattr(
        cli,
        "run_tick",
        lambda *_a, **_k: TickResult(campagne=1, inviate=2, saltate=1, fallite=0, stampate=3),
    )
    assert main(["campaigns-tick"]) == 0
    assert (
        capsys.readouterr().out.strip()
        == "1 campagne, 2 inviate, 1 saltate, 0 fallite, 3 esiti registrati"
    )


def test_without_a_key_the_tick_sends_nothing_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    assert main(["campaigns-tick"]) == 0
    assert "invio non configurato" in capsys.readouterr().out


class _FakeCardWriter:
    """Records what `cards_refresh` built it with and the limit it was asked for."""

    instances: list["_FakeCardWriter"] = []
    result = CardsRefreshed(written=3, failed=1)

    def __init__(self, session: object, llm: object) -> None:
        self.session = session
        self.llm = llm
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
    operator runs it again until it prints «0 schede scritte»."""
    _FakeCardWriter.instances = []
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "call_for_task", _configured_card_task)
    monkeypatch.setattr(cli, "CardWriter", _FakeCardWriter)

    assert main(["cards-refresh", "--limit", "10"]) == 0

    [writer] = _FakeCardWriter.instances
    assert writer.llm is _CARD_CALL
    assert _TASKS_ASKED[-1] == "card"
    assert writer.limits == [10]
    assert capsys.readouterr().out.strip() == "3 schede scritte, 1 non riuscite"


@pytest.mark.parametrize(
    ("result", "line"),
    [
        (
            CardsRefreshed(written=3, failed=1),
            "3 schede scritte, 1 non riuscite",
        ),
        # An outage: counted among the failed, and the stop said.
        (
            CardsRefreshed(written=1, failed=1, stopped=True),
            "1 schede scritte, 1 non riuscite, fermato: Claude non disponibile",
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
    «0 schede scritte, 0 non riuscite» still says the backlog is done. A card still
    gendered after its rewrite is among the «non riuscite» (REB-802)."""
    _FakeCardWriter.instances = []
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "call_for_task", _configured_card_task)
    monkeypatch.setattr(cli, "CardWriter", _FakeCardWriter)
    monkeypatch.setattr(_FakeCardWriter, "result", result)

    assert main(["cards-refresh"]) == 0

    assert capsys.readouterr().out.strip() == line


def test_cards_refresh_names_modelli_ai_when_no_task_configured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no model configured for the card task a run would print «0 schede scritte» for
    ever: it says why instead, and where to fix it."""
    _FakeCardWriter.instances = []
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "call_for_task", lambda opener, settings, task: None)
    monkeypatch.setattr(cli, "CardWriter", _FakeCardWriter)

    assert main(["cards-refresh"]) == 1

    assert _FakeCardWriter.instances == []
    assert capsys.readouterr().err.strip() == (
        "Nessun modello configurato per le schede: apri «Modelli AI»."
    )


# ---- ai-seed / ai-rotate-secrets (REB-832) ----------------------------------------------

_SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()
_OLD_KEY = "sk-ant-old-key-abcd"


@pytest.fixture
def ai_db(hub_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The command's settings point at the test database; the AI tables go back to what
    migration 0035 seeded afterwards."""

    def use(secrets_key: str = _SECRETS_KEY) -> None:
        url = hub_engine.url.render_as_string(hide_password=False)
        monkeypatch.setattr(
            cli,
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
    assert "chiave salvata, finisce con …abcd" in captured.out
    assert _OLD_KEY not in captured.out + captured.err
    with hub_engine.connect() as conn:
        row = conn.execute(
            text("SELECT key_ciphertext, enabled FROM ai_providers WHERE provider = 'anthropic'")
        ).one()
        prices = conn.execute(text("SELECT count(*) FROM ai_prices")).scalar_one()
    assert row.enabled is True
    assert _OLD_KEY.encode() not in bytes(row.key_ciphertext)
    assert prices == 2


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


def test_ai_rotate_secrets_command_reencrypts_and_prints_the_count(
    ai_db: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["ai-seed", "--eur-per-usd", "0.92"]) == 0
    capsys.readouterr()
    new = base64.b64encode(os.urandom(32)).decode()

    monkeypatch.setenv("REBASE_SECRETS_KEY_OLD", _SECRETS_KEY)
    monkeypatch.setenv("REBASE_SECRETS_KEY_NEW", new)

    assert main(["ai-rotate-secrets"]) == 0
    assert capsys.readouterr().out.strip() == "1 chiavi ricifrate"

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
