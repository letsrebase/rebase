"""The commands cron runs over the root installation and every space: `digest`,
`reminders` and `automations`.

What they share, and the reason each differs from `gmail-sync`:

- One line per space, counters only, never an address or a figure out of a report. A
  failure names the slug and the exception's *type*, never its text.
- The root installation is the first line, and `--slug root` visits it alone without
  opening the registry. Every other name is a registry space, in the order the spaces
  were created, so a crontab line does not change when one is added.
- **They migrate nothing.** `ensure-space-defaults` at boot is the only migrator
  (ORB-189): a cron that ran Alembic on every database at eight o'clock on a Monday
  would be the riskiest thing this product does, and would do it unattended. A space
  whose schema is behind fails as one `skipped` line and is picked up by the next
  deploy's boot command.
- **The space's clock** (REB-764). After each space a real run writes its row in
  `reminder_job_run` (`record_job`): a success moves `ran_at`, a failure records a short
  word for why and leaves `ran_at` alone. the settings job-runs screen and `job_late` read
  it. A rehearsal (`--dry-run`) writes nothing.
"""

import sys
from datetime import UTC, date, datetime, time
from functools import partial
from zoneinfo import ZoneInfo

from sqlalchemy import Engine

from pigrocrm.core import telemetry
from pigrocrm.core.accountant_export.retention import ExportRetention, RetentionReport
from pigrocrm.core.automations.daily import AutomationPass, PassReport
from pigrocrm.core.cli.signing import SpaceSweep, sweep_space
from pigrocrm.core.cli.spaces_walk import (
    read_spaces,
    record_job,
    report_unreadable,
    space_engine,
)
from pigrocrm.core.collaborators.hub_notices import HubNoticeSweep, NoticeSweepReport
from pigrocrm.core.collaborators.rules import is_leads_space
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.digest.run import DigestOutcome, DigestRun
from pigrocrm.core.digest.service import previous_week, week_containing
from pigrocrm.core.due_items.forecast import ForecastReport, refresh_forecasts
from pigrocrm.core.fiscal.year_roll import FiscalYearRoll, YearRollReport
from pigrocrm.core.mail import sender_from_settings
from pigrocrm.core.reminders.jobs import AUTOMATIONS_JOB, DIGEST_JOB, REMINDER_PASS_JOB
from pigrocrm.core.reminders.push import push_sender_from_settings
from pigrocrm.core.reminders.run import ReminderOutcome, ReminderRun
from pigrocrm.core.slack.client import slack_client_for
from pigrocrm.core.storage.factory import space_storage_from_settings

# Both forms of the same module, deliberately. `telemetry` as a module is what
# `digest` calls `shutdown()` through, so the call resolves the attribute at run time and
# a test that replaces it is obeyed; `tracker_from_settings` is imported by name because
# that is the seam the tests patch on *this* module.
from pigrocrm.core.telemetry import tracker_from_settings


def _refuse_other_day(settings: Settings, day: date | None, dry_run: bool) -> bool:
    """`--date` evaluates another day, and only as a rehearsal: a real pass of another
    day would write against today's reminders and task."""
    if day is not None and day != today_local(settings) and not dry_run:
        print("--date changes the day: use it only with --dry-run", file=sys.stderr)
        return True
    return False


def digest(
    settings: Settings, *, slug: str | None, day: date | None, force: bool, dry_run: bool
) -> int:
    """`pigrocrm digest`: the weekly report of the root installation and of every space in
    the registry (spec 2026-09-16 §3.4, REB-263). One run a week, Monday morning (the
    runbook is `docs/superpowers/notes/2026-09-09-gmail-cron-runbook.md`).

    Exit 0 whatever happens: the spaces are independent, so one that fails must not take
    the exit status -- and with it the operator's attention -- away from the ones that
    worked. What decides anything about a space's week is `DigestRun`.

    **One sender, one tracker, one week, for the whole run.** Resend's client and
    PostHog's are per process, not per space, and `previous_week` asked once is what makes
    a run that starts at 07:59:59 on a Monday send *one* week to every space rather than
    two different ones side by side. Only `public_url` is per space. The week comes from
    the *root* settings: asking per space would be the same answer computed eight times,
    with eight chances to straddle midnight.

    **`telemetry.shutdown()` in a `finally`.** The PostHog SDK queues captures on a
    background thread; a process that exits without shutting it down loses whatever was
    still in the queue. It is a no-op on an installation with no key."""
    walk = read_spaces(settings, slug=slug)
    report_unreadable(walk)
    if not walk.targets:
        # A typo in a cron line, said only when the registry was really read: an
        # unreachable one has had its own line already. Never as a week that went out.
        if slug is not None and walk.registry_read:
            print(f"{slug}: not in the registry", file=sys.stderr)
        return 0

    week = week_containing(day) if day is not None else previous_week(settings)
    try:
        # Both clients are built *inside* this `try`, so that the `finally` below reaches
        # them: PostHog's constructor can fail here -- a malformed key, a host it
        # refuses -- and its failure must leave a line, not a traceback and an SDK that
        # was never shut down.
        try:
            sender = sender_from_settings(settings)
            tracker = tracker_from_settings(settings)
        except Exception as exc:  # noqa: BLE001 - a cron line, never a traceback
            print(f"sending cannot be configured ({type(exc).__name__})", file=sys.stderr)
            return 0
        for target in walk.targets:
            with space_engine(target.url) as engine:
                try:
                    # A session of its own per space, with no transaction open:
                    # `DigestRun` opens the dashboard's snapshot itself.
                    with session_factory(engine)() as space:
                        outcome = DigestRun(
                            space,
                            settings,
                            sender=sender,
                            tracker=tracker,
                            public_url=target.public_url,
                        ).send_for_space(
                            target.label,
                            target.owner_email,
                            week,
                            force=force,
                            dry_run=dry_run,
                        )
                    if not dry_run:
                        record_job(
                            engine,
                            DIGEST_JOB,
                            target.label,
                            outcome.reason if outcome.outcome == "skipped" else None,
                        )
                except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                    # `send_for_space` answers `skipped` for everything that fails once
                    # it has started; what is left for here is what fails before that
                    # -- a database that cannot be reached, a schema behind the image's.
                    print(f"{target.label}: skipped ({type(exc).__name__})", file=sys.stderr)
                    if not dry_run:
                        record_job(engine, DIGEST_JOB, target.label, type(exc).__name__, quiet=True)
                    continue
            _print_digest_outcome(outcome, dry_run=dry_run)
    finally:
        telemetry.shutdown()
    return 0


def _print_digest_outcome(outcome: DigestOutcome, *, dry_run: bool) -> None:
    """One space, one line. `skipped` goes to stderr, so `2>&1` in the cron line keeps
    the order and a log split by stream keeps the failures on their own.

    `(dry run)` on a rehearsal: `--dry-run` answers `sent` with the people it *would*
    have written to, and a log where the rehearsal and the real Monday read identically is
    a log that cannot answer whether last week went out."""
    if outcome.outcome == "sent":
        print(f"{outcome.slug}: sent to {outcome.recipients}{' (dry run)' if dry_run else ''}")
    elif outcome.outcome == "empty":
        print(f"{outcome.slug}: empty")
    elif outcome.outcome == "already_sent":
        print(f"{outcome.slug}: already sent for {outcome.week}")
    elif outcome.outcome == "no_recipient":
        print(f"{outcome.slug}: no recipient")
    else:
        print(f"{outcome.slug}: skipped ({outcome.reason})", file=sys.stderr)


def reminders(settings: Settings, *, slug: str | None, day: date | None, dry_run: bool) -> int:
    """`pigrocrm reminders`: the reminder pass over the root installation and every space
    in the registry, for cron (spec 2026-10-26 §9, REB-736).

    **Exits 1 when any line went to `stderr`**, unlike the digest: the pass runs every
    fifteen minutes and its exit status is what the host's dead-man's-switch check reads
    (the runbook's `&&` ping), so a space that could not be walked has to be able to stop
    the ping.

    `ReminderRun.run_for_space` decides; `--dry-run` asks it what would be put in people's
    lists and writes nothing, and `--date` evaluates another day only with `--dry-run`.
    The pass writes its own success in the transaction that ends it, so only a failure is
    written from here.

    **It is also what delivers the engagement notices to the hub** (REB-1106): in the space
    `PIGROCRM_LEADS_SPACE` names, and only there, `HubNoticeSweep` posts what the outbox holds,
    ahead of the reminders and apart from them. It is the CRM's one frequent pass: the daily
    `automations` would leave a notice that failed once waiting a day."""
    if _refuse_other_day(settings, day, dry_run):
        return 2
    walk = read_spaces(settings, slug=slug)
    failed = report_unreadable(walk)
    if not walk.targets:
        if slug is not None and walk.registry_read:
            print(f"{slug}: not in the registry", file=sys.stderr)
            return 1
        return 1 if failed else 0

    # The two transports are built once for the pass. No key, no transport: that channel
    # sends nothing and records nothing, and says so in its counter.
    try:
        run = ReminderRun(
            settings,
            sender=sender_from_settings(settings),
            pusher=push_sender_from_settings(settings),
            slack=slack_client_for(settings) if settings.slack_client_id else None,
        )
    except Exception as exc:  # noqa: BLE001 - a cron line, never a traceback
        print(f"channels cannot be configured ({type(exc).__name__})", file=sys.stderr)
        return 1
    now = None
    if day is not None and day != today_local(settings):
        # Another day: its last minute, so a timer that was running by then has run.
        now = datetime.combine(day, time(23, 59, 59), tzinfo=ZoneInfo(settings.timezone))
    for target in walk.targets:
        with space_engine(target.url) as engine:
            # Apart from the reminders and before them, so a space whose reminders fail still
            # tells the hub what happened to its engagements (REB-1106): only the leads space,
            # the one that holds them.
            if is_leads_space(settings, target.slug):
                failed = (
                    _sweep_hub_notices(settings, engine, target.label, dry_run=dry_run) or failed
                )
            try:
                outcome = run.run_for_space(
                    target.label,
                    engine,
                    today=day,
                    now=now,
                    dry_run=dry_run,
                    public_url=target.public_url,
                )
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(f"{target.label}: skipped ({type(exc).__name__})", file=sys.stderr)
                failed = True
                if not dry_run:
                    record_job(
                        engine, REMINDER_PASS_JOB, target.label, type(exc).__name__, quiet=True
                    )
                continue
        _print_reminder_outcome(outcome, dry_run=dry_run)
    return 1 if failed else 0


def _sweep_hub_notices(settings: Settings, engine: Engine, label: str, *, dry_run: bool) -> bool:
    """The engagement notices the outbox owes the hub (`HubNoticeSweep`, REB-1106), delivered from
    the quarter-hourly pass of the leads space. A line only when there is something to say. Rows
    left to try again are counted on `stdout` (the backoff does the retrying, and an outage of the
    hub should not stop the dead-man's-switch ping); rows the hub refused for good, and a sweep that
    could not run at all, go to `stderr` and stop it, since a person has to look. Whether it
    printed to `stderr`."""
    try:
        with session_factory(engine)() as space:
            report = HubNoticeSweep(space, settings).run(dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - reported, the pass goes on
        print(f"{label}: hub notices not sent ({type(exc).__name__})", file=sys.stderr)
        return True
    return _print_notice_sweep(label, report, dry_run=dry_run)


def _print_notice_sweep(label: str, report: NoticeSweepReport, *, dry_run: bool) -> bool:
    if dry_run:
        if report.waiting:
            print(f"{label}: hub notices due {report.waiting} (dry run)")
        return False
    if report.delivered or report.retrying:
        print(f"{label}: hub notices sent {report.delivered}, to try again {report.retrying}")
    elif report.waiting:
        print(f"{label}: hub notices waiting for PIGROCRM_HUB_NOTICES_TOKEN {report.waiting}")
    if report.rejected:
        print(f"{label}: hub notices refused {report.rejected}", file=sys.stderr)
    return report.rejected > 0


def _print_reminder_outcome(outcome: ReminderOutcome, *, dry_run: bool) -> None:
    """One space, one line, counters only. A pass that found another one running is the
    lock doing its work and goes to `stdout`, like gmail-sync's «already running»: an error
    in the log for the system working as designed teaches the operator to ignore the log.

    `(dry run)` on a rehearsal, for the reason the digest's has it."""
    if outcome.outcome == "in_progress":
        print(f"{outcome.slug}: skipped (in_progress)")
        return
    # What the other channels sent is told only when there is something to tell, so the
    # line of a space with no transport configured reads as it always did.
    sent = "".join(
        f", {name} {count}"
        for name, count in (
            ("push", outcome.pushes),
            ("slack", outcome.slacks),
            ("email", outcome.emails),
        )
        if count
    )
    print(
        f"{outcome.slug}: reminders {outcome.raised}, new {outcome.delivered}, "
        f"closed {outcome.ended}{sent}{' (dry run)' if dry_run else ''}"
    )


def automations(settings: Settings, *, slug: str | None, day: date | None, dry_run: bool) -> int:
    """`pigrocrm automations`: the daily pass of the rules that open task (REB-727,
    slice 7 §6), over the root installation and every space in the registry, for cron.

    One line per space and one per rule. **Exits 1 when any line went to `stderr`**, so a
    host that pings a dead-man's switch after `&&` stops pinging when a space could not
    be walked, and that includes a clock that cannot be written after a run that worked
    (`clock not written`): the ping should not say a space is fine when its own
    database refuses a write.

    On 1 January the pass also moves the active srl entities still on `it-standard` to `it-company`
    (`FiscalYearRoll`, REB-1005), one line per space; a rehearsal counts and writes nothing. A
    refusal there is its own line and an exit status, and leaves the rules' own report and
    clock alone. Every day it also deletes the accountant exports older than 13 months
    (`ExportRetention`, REB-1012): their files leave the storage and the export stays as the
    trace; a space with none due prints nothing.

    `AutomationPass.run` decides; `--dry-run` runs it and rolls back, so the counters of a
    rehearsal are the real ones, and `--date` evaluates another day only with `--dry-run`:
    a real pass for another day would open task due on days that are not today."""
    if _refuse_other_day(settings, day, dry_run):
        return 2
    walk = read_spaces(settings, slug=slug)
    failed = report_unreadable(walk)
    if not walk.targets:
        if slug is not None and walk.registry_read:
            print(f"{slug}: not in the registry", file=sys.stderr)
            return 1
        return 1 if failed else 0

    for target in walk.targets:
        with space_engine(target.url) as engine:
            # First, and apart from the rules: a space whose rules failed today still has its
            # expired exports deleted, and is tried again tomorrow either way.
            retention: RetentionReport | None = None
            try:
                with session_factory(engine)() as space:
                    retention = ExportRetention(
                        space,
                        partial(
                            space_storage_from_settings,
                            settings,
                            target.slug,
                            session_factory=session_factory(engine),
                        ),
                    ).run(now=datetime.now(UTC), dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001 - reported below, the pass goes on
                print(
                    f"{target.label}: accountant exports not deleted ({type(exc).__name__})",
                    file=sys.stderr,
                )
                failed = True
            # Also apart from the rules: signatures a lost webhook left behind are recovered for
            # every space, the root included, and a space whose rules failed is not left without
            # (REB-1019). Without a Documenso it moves nothing that needs one.
            try:
                recovered = sweep_space(
                    settings,
                    engine,
                    slug=target.slug,
                    tenant_id=target.tenant_id,
                    dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001 - reported, the pass goes on
                print(
                    f"{target.label}: signatures not recovered ({type(exc).__name__})",
                    file=sys.stderr,
                )
                failed = True
            else:
                failed = _print_signature_sweep(target.label, recovered, dry_run=dry_run) or failed
            try:
                with session_factory(engine)() as space:
                    report = AutomationPass(space, settings).run(today=day, dry_run=dry_run)
                    # The forecasts of money in are materialised with the other daily rules
                    # (spec 2026-11-09, due items, § 1.2); the pass bound the system scope.
                    forecasts = refresh_forecasts(space, today=day, dry_run=dry_run)
                if not dry_run and not record_job(engine, AUTOMATIONS_JOB, target.label, None):
                    failed = True
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(f"{target.label}: skipped ({type(exc).__name__})", file=sys.stderr)
                failed = True
                if retention is not None:
                    _print_export_retention(target.label, retention, dry_run=dry_run)
                if not dry_run:
                    record_job(
                        engine, AUTOMATIONS_JOB, target.label, type(exc).__name__, quiet=True
                    )
                continue
            # After the rules, and apart from them: a refusal on the year boundary must not
            # take the rules' report away or record the automations' own clock as failed.
            roll: YearRollReport | None = None
            try:
                with session_factory(engine)() as space:
                    roll = FiscalYearRoll(space).run(
                        today=day if day is not None else today_local(settings), dry_run=dry_run
                    )
            except Exception as exc:  # noqa: BLE001 - reported below, the pass goes on
                print(
                    f"{target.label}: fiscal packs not moved ({type(exc).__name__})",
                    file=sys.stderr,
                )
                failed = True
        failed = failed or forecasts.failed > 0
        _print_automation_report(target.label, report, forecasts, dry_run=dry_run)
        if report.payroll_failed:
            print(
                f"{target.label}: payroll forecasts not refreshed {report.payroll_failed} "
                f"({', '.join(sorted(set(report.payroll_failures)))})",
                file=sys.stderr,
            )
            failed = True
        if report.iva_failed:
            print(
                f"{target.label}: IVA settlements failed {report.iva_failed} "
                f"({', '.join(sorted(set(report.iva_failures)))})",
                file=sys.stderr,
            )
            failed = True
        if report.tax_items_failed:
            print(
                f"{target.label}: tax due items not recomputed {report.tax_items_failed} "
                f"({', '.join(sorted(set(report.tax_items_failures)))})",
                file=sys.stderr,
            )
            failed = True
        if report.iva_frozen:
            # A reminder and not a failure: the item was paid, so the figures it would take
            # are a person's decision.
            print(
                f"{target.label}: IVA periods not recomputed, their due item has a payment: "
                f"{', '.join(report.iva_frozen)}",
                file=sys.stderr,
            )
        if retention is not None:
            failed = _print_export_retention(target.label, retention, dry_run=dry_run) or failed
        if roll is not None:
            failed = _print_year_roll(target.label, roll, dry_run=dry_run) or failed
    return 1 if failed else 0


def _print_signature_sweep(slug: str, report: SpaceSweep, *, dry_run: bool) -> bool:
    """The signature recovery's line (REB-1019): only when there was something to say, so the log
    of a daily pass does not grow a line that is almost always zero. A document Documenso could
    not be asked about (a refused token, an unreachable instance) goes to `stderr` as a count and
    an exit status: nothing else says a signature is stuck."""
    if dry_run:
        if report.visited:
            print(f"{slug}: signatures to look at {report.visited} (dry run)")
        return False
    if report.touched:
        print(f"{slug}: signatures recovered {report.touched}")
    if report.unconfirmed:
        print(
            f"{slug}: signatures not confirmed with Documenso {report.unconfirmed}",
            file=sys.stderr,
        )
    return report.unconfirmed > 0


def _print_export_retention(slug: str, report: RetentionReport, *, dry_run: bool) -> bool:
    """The accountant exports past 13 months (REB-1012): a line only when there was something
    to delete, so the log of a daily pass does not grow a line that is almost always zero. An
    export whose files the storage would not delete goes to `stderr` as a count and an exit
    status, and is tried again the next day."""
    if report.deleted:
        suffix = " (dry run)" if dry_run else ""
        print(f"{slug}: accountant exports deleted {report.deleted}{suffix}")
    if report.failed:
        print(f"{slug}: accountant exports not deleted {report.failed}", file=sys.stderr)
    return report.failed > 0


def _print_year_roll(slug: str, roll: YearRollReport, *, dry_run: bool) -> bool:
    """The year boundary's line (REB-1005), on 1 January only: the other 364 days it has
    nothing to say, so the log of a daily pass does not grow a line that never changes. An
    entity a save refused goes to `stderr` as a count, and the answer is `True`: the exit
    status must say it, since nothing moves it again before the next 1 January."""
    if roll.due:
        suffix = " (dry run)" if dry_run else ""
        print(f"{slug}: fiscal packs moved to it-company {roll.moved}{suffix}")
    if roll.failed:
        print(f"{slug}: fiscal packs not moved {roll.failed}", file=sys.stderr)
    return roll.failed > 0


def _print_automation_report(
    slug: str, report: PassReport, forecasts: ForecastReport, *, dry_run: bool
) -> None:
    """One line per rule, counters only. A rule that is switched off says so rather than
    printing zeros, which would read as «looked, found nothing»."""
    suffix = " (dry run)" if dry_run else ""
    for rule in report.rules:
        if rule.disabled:
            print(f"{slug}: {rule.rule} disabled")
        else:
            print(
                f"{slug}: {rule.rule} opened {rule.open}, already open {rule.already_open}{suffix}"
            )
    if report.payroll_refreshed:
        print(f"{slug}: payroll forecasts refreshed {report.payroll_refreshed}{suffix}")
    if report.iva_settled:
        print(f"{slug}: IVA settled for {report.iva_settled} legal entities{suffix}")
    if report.tax_items_refreshed:
        print(
            f"{slug}: tax due items recomputed for {report.tax_items_refreshed} legal "
            f"entities{suffix}"
        )
    print(
        f"{slug}: forecasts created {forecasts.created}, revised {forecasts.revised}, "
        f"dropped {forecasts.dropped}{suffix}"
    )
    if forecasts.failed:
        # To `stderr`, so the exit status says a contract or a draft kept its old forecast.
        print(f"{slug}: forecasts failed for {forecasts.failed} sources", file=sys.stderr)
