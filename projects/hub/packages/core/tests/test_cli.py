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


class _FakeSigningService:
    """Records the session and the collaborators `contracts_sweep` built it with, and
    answers `sweep()` with a canned count."""

    instances: list["_FakeSigningService"] = []

    def __init__(self, session: object, **kwargs: Any) -> None:
        self.session = session
        self.kwargs = kwargs
        _FakeSigningService.instances.append(self)

    def sweep(self) -> int:
        return 3


@pytest.fixture(autouse=True)
def _reset() -> None:
    _FakeSigningService.instances = []


def test_the_contracts_sweep_command_builds_the_signing_service_and_prints_the_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "SigningService", _FakeSigningService)

    assert main(["contracts-sweep"]) == 0

    assert len(_FakeSigningService.instances) == 1
    out = capsys.readouterr().out
    assert out.strip() == "3 documenti ripresi"


def test_the_documenso_check_command_dispatches_to_documenso_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No Documenso configured: `main` reaches `documenso_check` with this process's
    settings and reports its refusal, rather than doing nothing on an unknown command."""
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]

    assert main(["documenso-check"]) == 1

    assert "la firma è spenta" in capsys.readouterr().err
