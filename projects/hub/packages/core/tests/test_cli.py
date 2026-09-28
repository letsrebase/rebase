"""`rebase contracts-sweep` (REB-391) and `rebase documenso-check` (REB-393): the CLI
wiring alone. `SigningService.sweep`'s own behaviour is `test_signing.py`'s
(`test_sweep_*`) and `documenso_check`'s own behaviour, against a fake Documenso, is
`test_documenso.py`'s. No real database or Documenso is reached here: `get_settings`,
`SigningService` and the settings `documenso_check` reads are all swapped out, so the
tests prove `main` dispatches to each command and reports what it returns, nothing
more."""

from typing import Any

import pytest

from rebase_core import cli, signing
from rebase_core.campaigns.tick import TickResult
from rebase_core.cli import main
from rebase_core.config import Settings
from rebase_core.engagements import EngagementService
from rebase_core.http import urllib_engagements_call
from rebase_core.signing import SweepResult


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
