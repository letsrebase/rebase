"""`rebase loop <campaigns|sweep|cards>`: the passes of the three compose loops (REB-892).

The sleep is injected, so no test waits: `wait(interval)` answers `True` when the loop is to
stop, and is called once before every pass, as the shell loops slept first.
"""

import argparse
import logging
import os
import signal
import threading
from collections.abc import Callable

import pytest

from rebase_core.cli import campaigns as cli_campaigns
from rebase_core.cli import cards as cli_cards
from rebase_core.cli import loops
from rebase_core.cli import signing as cli_signing
from rebase_core.config import Settings
from rebase_core.errors import NotFound


def _waits(passes: int, asked: list[float]) -> Callable[[float], bool]:
    """A clock that lets `passes` passes run and then says stop."""
    left = [passes]

    def wait(interval: float) -> bool:
        asked.append(interval)
        if left[0] == 0:
            return True
        left[0] -= 1
        return False

    return wait


def test_the_campaigns_loop_ticks_once_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks: list[str] = []
    monkeypatch.setattr(cli_campaigns, "campaigns_tick", lambda: ticks.append("tick") or 0)
    asked: list[float] = []

    assert loops.run_loop("campaigns", _waits(3, asked)) == 0

    assert ticks == ["tick"] * 3
    assert asked == [60.0] * 4  # the last wait is the one that said stop


def test_the_sweep_loop_sweeps_every_ten_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    sweeps: list[str] = []
    monkeypatch.setattr(cli_signing, "contracts_sweep", lambda: sweeps.append("sweep") or 0)
    asked: list[float] = []

    loops.run_loop("sweep", _waits(2, asked))

    assert sweeps == ["sweep"] * 2
    assert asked == [600.0] * 3


def test_a_failing_pass_is_logged_and_the_loop_goes_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = [0]

    def tick() -> int:
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("ada@example.org said no")
        if calls[0] == 3:
            raise NotFound("freelancer", "abc")
        return 0

    monkeypatch.setattr(cli_campaigns, "campaigns_tick", tick)
    logging.getLogger("rebase.loop").disabled = False
    caplog.set_level(logging.INFO, logger="rebase.loop")

    loops.run_loop("campaigns", _waits(4, []))

    assert calls[0] == 4  # the pass after each failure ran
    failures = [r.getMessage() for r in caplog.records if "failed" in r.getMessage()]
    # The class of an unknown exception, never its text (it can quote a row); a domain
    # error's own sentence, which carries no personal data.
    assert failures == [
        "loop: campaigns-tick failed (RuntimeError)",
        "loop: campaigns-tick failed (NotFound: freelancer abc non trovato)",
    ]
    assert "ada@example.org" not in caplog.text


class _CardsCommands:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, loop_on: bool) -> None:
        self.calls: list[str] = []
        monkeypatch.setattr(cli_cards, "cards_batch_poll", self._call("poll"))
        monkeypatch.setattr(
            cli_cards, "sources_refetch", lambda limit: self.calls.append(f"refetch {limit}") or 0
        )
        monkeypatch.setattr(
            cli_cards, "cards_refresh", lambda limit: self.calls.append(f"refresh {limit}") or 0
        )
        monkeypatch.setattr(
            loops,
            "get_settings",
            lambda: Settings(_env_file=None, cards_loop=loop_on),  # type: ignore[call-arg]
        )

    def _call(self, name: str) -> Callable[[], int]:
        return lambda: self.calls.append(name) or 0


def test_the_cards_loop_polls_every_pass_and_refreshes_on_the_twelfth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = _CardsCommands(monkeypatch, loop_on=True)
    asked: list[float] = []

    loops.run_loop("cards", _waits(24, asked))

    assert asked == [300.0] * 25
    assert cards.calls.count("poll") == 24
    assert [c for c in cards.calls if c != "poll"] == [
        "refetch 50",
        "refresh 50",
        "refetch 50",
        "refresh 50",
    ]
    # The hourly work comes right after the twelfth and twenty-fourth poll.
    assert cards.calls[11:14] == ["poll", "refetch 50", "refresh 50"]
    assert cards.calls[:11] == ["poll"] * 11


def test_with_the_loop_off_the_cards_loop_still_polls_and_refetches_and_says_it_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cards = _CardsCommands(monkeypatch, loop_on=False)

    loops.run_loop("cards", _waits(12, []))

    assert cards.calls.count("poll") == 12
    assert [c for c in cards.calls if c != "poll"] == ["refetch 50"]
    assert capsys.readouterr().out.strip() == "cards: loop off (REBASE_CARDS_LOOP)"


def test_a_failing_step_of_the_cards_pass_does_not_stop_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cards = _CardsCommands(monkeypatch, loop_on=True)

    def broken() -> int:
        cards.calls.append("poll")
        raise RuntimeError("down")

    monkeypatch.setattr(cli_cards, "cards_batch_poll", broken)
    monkeypatch.setattr(
        cli_cards,
        "sources_refetch",
        lambda limit: (_ for _ in ()).throw(RuntimeError("github")),
    )

    loops.run_loop("cards", _waits(12, []))

    assert cards.calls.count("poll") == 12  # every pass polled, though each poll failed
    assert cards.calls[-1] == "refresh 50"  # and the failed refetch did not skip the refresh


def test_a_loop_told_to_stop_before_its_first_pass_runs_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks: list[str] = []
    monkeypatch.setattr(cli_campaigns, "campaigns_tick", lambda: ticks.append("tick") or 0)

    assert loops.run_loop("campaigns", lambda interval: True) == 0

    assert ticks == []


def test_sigterm_ends_the_sleep_at_once_and_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real handler and the real wait: a SIGTERM during a sleep of an hour returns."""
    monkeypatch.setitem(loops.INTERVALS, "campaigns", 3600.0)
    ticks: list[str] = []
    monkeypatch.setattr(cli_campaigns, "campaigns_tick", lambda: ticks.append("tick") or 0)
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    timer = threading.Timer(0.05, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        code = loops.COMMANDS["loop"].run(_namespace("campaigns"))
    finally:
        timer.cancel()
        for sig, handler in before.items():
            signal.signal(sig, handler)

    assert code == 0
    assert ticks == []


def _namespace(name: str) -> argparse.Namespace:
    return argparse.Namespace(name=name)
