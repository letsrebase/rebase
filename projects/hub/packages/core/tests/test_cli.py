"""`rebase contracts-sweep` (REB-391) and `rebase documenso-check` (REB-393): the CLI
wiring alone. `SigningService.sweep`'s own behaviour is `test_signing.py`'s
(`test_sweep_*`) and `documenso_check`'s own behaviour, against a fake Documenso, is
`test_documenso.py`'s. No real database or Documenso is reached here: `get_settings`,
`SigningService` and the settings `documenso_check` reads are all swapped out, so the
tests prove `main` dispatches to each command and reports what it returns, nothing
more."""

from typing import Any

import pytest

from rebase_core import cli
from rebase_core.cli import main
from rebase_core.config import Settings
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
    monkeypatch.setattr(cli, "SigningService", _FakeSigningService)

    assert main(["contracts-sweep"]) == 0

    assert len(_FakeSigningService.instances) == 1
    out = capsys.readouterr().out
    assert out.strip() == "3 documenti ripresi"


def test_the_contracts_sweep_command_also_prints_the_unconfirmed_count_when_it_is_not_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """REB-431: a refusal or an unreachable Documenso during the confirmation step
    leaves a document `inviato` -- not silently, any more. Left off the line entirely
    when it is zero, so the ordinary run reads exactly as it always has."""
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "SigningService", _FakeSigningService)
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
