"""`rebase loop <campaigns|sweep|cards>`: the three long-lived loops of `docker-compose.yml` as
one process each, with no shell between them (REB-892).

They used to be `while :; do sleep N; uv run --no-sync rebase <command>; done`: a new `uv`
and a new interpreter that imported everything, every 60 seconds for the campaigns. Here the
process starts once, imports a pass's modules the first time that pass runs, and then sleeps
and runs the same functions the commands run (`campaigns-tick`, `contracts-sweep`,
`cards-batch-poll`, `sources-refetch`, `cards-refresh`), unchanged.

- `campaigns`: every 60 seconds, `campaigns-tick`.
- `sweep`: every 600 seconds, `contracts-sweep`.
- `cards`: every 300 seconds `cards-batch-poll` (whatever `REBASE_CARDS_LOOP` says: an admin
  started the batch); and on every 12th pass (hourly) `sources-refetch --limit 50`, then, only
  with `REBASE_CARDS_LOOP=on` (`Settings.cards_loop`), `cards-refresh --limit 50`, else a line
  saying the loop is off.

A pass that fails is logged (its step, the exception's class, and the message only for a
`DomainError`, whose sentences carry no personal data: any other exception's text can quote a
row) and the loop goes on, as `|| true` did. SIGTERM and SIGINT end it: a sleep stops at
once, a pass in progress finishes first (a card half written is paid for and not stored),
and a second signal exits immediately. The commands' own output goes to stdout, line by
line, as before.
"""

import argparse
import io
import logging
import signal
import sys
import threading
from collections.abc import Callable
from types import FrameType

from rebase_core.cli.common import Command, dispose_engines
from rebase_core.config import get_settings
from rebase_core.errors import DomainError

logger = logging.getLogger("rebase.loop")

LOOPS = ("campaigns", "sweep", "cards")
# Seconds between two passes.
INTERVALS = {"campaigns": 60.0, "sweep": 600.0, "cards": 300.0}
# The cards loop's hourly work comes every this many of its passes.
CARDS_HOURLY_EVERY = 12
CARDS_LIMIT = 50

Pass = Callable[[], None]


def _guarded(label: str, step: Callable[[], object]) -> None:
    """`step`, and if it raises, a log line instead: one failing step stops nothing."""
    try:
        step()
    except Exception as exc:
        detail = f": {exc.message}" if isinstance(exc, DomainError) else ""
        logger.error("loop: %s failed (%s%s)", label, type(exc).__name__, detail)


def _campaigns_pass() -> Pass:
    from rebase_core.cli import campaigns

    return lambda: _guarded("campaigns-tick", campaigns.campaigns_tick)


def _sweep_pass() -> Pass:
    from rebase_core.cli import signing

    return lambda: _guarded("contracts-sweep", signing.contracts_sweep)


def _cards_pass() -> Pass:
    from rebase_core.cli import cards

    passes = 0

    def run() -> None:
        nonlocal passes
        _guarded("cards-batch-poll", cards.cards_batch_poll)
        passes += 1
        if passes < CARDS_HOURLY_EVERY:
            return
        passes = 0
        _guarded("sources-refetch", lambda: cards.sources_refetch(CARDS_LIMIT))
        if get_settings().cards_loop:
            _guarded("cards-refresh", lambda: cards.cards_refresh(CARDS_LIMIT))
        else:
            print("cards: loop off (REBASE_CARDS_LOOP)", flush=True)

    return run


_PASSES: dict[str, Callable[[], Pass]] = {
    "campaigns": _campaigns_pass,
    "sweep": _sweep_pass,
    "cards": _cards_pass,
}


def run_loop(name: str, wait: Callable[[float], bool]) -> int:
    """Sleeps `INTERVALS[name]` through `wait` (which answers `True` when the loop is to
    stop) and runs the loop's pass, until it is told to stop. The sleep comes first, as in
    the shell loops: a deploy's first pass is one interval after the start."""
    run = _PASSES[name]()
    interval = INTERVALS[name]
    logger.info("loop %s: every %s seconds", name, int(interval))
    while not wait(interval):
        run()
        dispose_engines()  # each pass opens its own engine; none stays behind
    logger.info("loop %s: stopped", name)
    return 0


def _loop_command(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(message)s", stream=sys.stderr
    )
    # Pipes block-buffer: the commands' lines must reach `docker logs` as they are printed.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    stop = threading.Event()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        if stop.is_set():
            raise SystemExit(1)  # the second signal: no more waiting for the pass
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    return run_loop(args.name, stop.wait)


def _arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", choices=LOOPS)


COMMANDS = {"loop": Command(_arguments, _loop_command)}
