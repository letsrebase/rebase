"""`rebase contracts-sweep` (REB-391), `rebase documenso-check` (REB-393) and `rebase
cards-refresh` (REB-510): the CLI wiring alone. `SigningService.sweep`'s own behaviour
is `test_signing.py`'s (`test_sweep_*`), `documenso_check`'s own behaviour, against a
fake Documenso, is `test_documenso.py`'s, and `CardWriter.refresh_stale`'s is
`test_cards.py`'s. No real database, Documenso or Claude is reached here:
`get_settings`, `SigningService`, `CardWriter` and the settings `documenso_check` reads
are all swapped out, so the tests prove `main` dispatches to each command and reports
what it returns, nothing more."""

from typing import Any

import pytest

from rebase_core import cli, signing
from rebase_core.campaigns.tick import TickResult
from rebase_core.cli import main
from rebase_core.config import Settings
from rebase_core.llm import AnthropicCall
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
    out = capsys.readouterr().out
    assert out.strip() == "3 documenti ripresi"


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
    assert out.strip() == "1 documenti ripresi, 2 non confermati"


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
        cli, "run_tick", lambda *_a, **_k: TickResult(campagne=1, inviate=2, saltate=1, fallite=0)
    )
    assert main(["campaigns-tick"]) == 0
    assert capsys.readouterr().out.strip() == "1 campagne, 2 inviate, 1 saltate, 0 fallite"


def test_without_a_key_the_tick_sends_nothing_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    assert main(["campaigns-tick"]) == 0
    assert "invio non configurato" in capsys.readouterr().out


class _FakeCardWriter:
    """Records what `cards_refresh` built it with and the limit it was asked for."""

    instances: list["_FakeCardWriter"] = []

    def __init__(self, session: object, llm: object) -> None:
        self.session = session
        self.llm = llm
        self.limits: list[int] = []
        _FakeCardWriter.instances.append(self)

    def refresh_stale(self, limit: int = 50) -> CardsRefreshed:
        self.limits.append(limit)
        return CardsRefreshed(written=3, failed=1)


def test_cards_refresh_prints_the_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One batch of `--limit` per run, the oldest first (`refresh_stale`), so the
    operator runs it again until it prints «0 schede scritte»."""
    _FakeCardWriter.instances = []
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(anthropic_api_key="sk-ant-test-not-a-real-key", _env_file=None),  # type: ignore[call-arg]
    )
    monkeypatch.setattr(cli, "CardWriter", _FakeCardWriter)

    assert main(["cards-refresh", "--limit", "10"]) == 0

    [writer] = _FakeCardWriter.instances
    assert isinstance(writer.llm, AnthropicCall)
    assert writer.limits == [10]
    assert capsys.readouterr().out.strip() == "3 schede scritte, 1 non riuscite"


def test_cards_refresh_without_a_key_says_so_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a key a run would print «0 schede scritte» for ever: it says why instead."""
    _FakeCardWriter.instances = []
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "CardWriter", _FakeCardWriter)

    assert main(["cards-refresh"]) == 1

    assert _FakeCardWriter.instances == []
    assert "REBASE_ANTHROPIC_API_KEY" in capsys.readouterr().err
