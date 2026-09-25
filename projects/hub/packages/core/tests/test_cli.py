"""`rebase contracts-sweep` (REB-391): the CLI wiring alone -- `SigningService.sweep`'s
own behaviour is `test_signing.py`'s (`test_sweep_*`). No real database or Documenso is
reached here: `get_settings` and `SigningService` are both swapped out, so the test
proves `main` builds the service and reports what it returns, nothing more."""

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
