"""The `pigrocrm` command (`pigrocrm.core.cli:main`).

One module per family: `admin` (createadmin, resetpassword), `spaces` (ensure-space-defaults,
reconcile-due-items, rebuild-identity-index, seed-templates), `cron` (digest, reminders,
automations), `mail` (mail-sync, alias gmail-sync), `signing` (contracts-check, documenso-check and
the daily recovery of signatures), `backfill` (owner-backfill, duplicate-organizations,
vapid-keys). What they share, the walk over the root installation and the registry's spaces, is
`spaces_walk`.
"""

import argparse
from collections.abc import Sequence
from datetime import date

from pigrocrm.core.cli.admin import createadmin, resetpassword
from pigrocrm.core.cli.backfill import duplicate_organizations, owner_backfill, vapid_keys
from pigrocrm.core.cli.cron import automations, digest, reminders
from pigrocrm.core.cli.mail import mail_sync
from pigrocrm.core.cli.signing import contracts_check, documenso_check
from pigrocrm.core.cli.spaces import (
    ensure_space_defaults,
    rebuild_identity_index,
    reconcile_due_items,
    seed_templates,
)
from pigrocrm.core.config import get_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pigrocrm")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("createadmin", help="Create the first admin user")
    admin.add_argument("--email")
    admin.add_argument("--name")
    reset = sub.add_parser("resetpassword", help="Set a new password for an existing user")
    reset.add_argument("--email")
    sub.add_parser("seed-templates", help="Create the default templates, if missing")
    sub.add_parser(
        "ensure-space-defaults",
        help="Give every space in the registry the default statuses, templates and categories, if "
        "missing",
    )
    reconcile = sub.add_parser(
        "reconcile-due-items",
        help="Give the invoices written by the previous release their due item, or bring a "
        "disagreeing one back in step; the root and every space, or one",
    )
    reconcile.add_argument("--slug", help="A single space, instead of the whole registry")
    sub.add_parser(
        "rebuild-identity-index",
        help="Index in identities every address found in the users of every space",
    )
    for name, help_text in (
        ("mail-sync", "Sync every connected mailbox, of the root and of every space (for cron)"),
        ("gmail-sync", "The old name of mail-sync, kept until the crontabs say mail-sync"),
    ):
        sync = sub.add_parser(name, help=help_text)
        sync.add_argument(
            "--email", help="Only the mailbox with this address, wherever it is linked"
        )
    weekly = sub.add_parser("digest", help="Send every space its weekly digest (for cron)")
    weekly.add_argument("--slug", help="A single space, instead of the whole registry")
    weekly.add_argument(
        "--date",
        # `type=` rather than a parse in the dispatch below: a mistyped date then comes
        # back as argparse's own usage message and an exit 2, not as a traceback at the
        # bottom of a cron log.
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="A day of the week to send; omitted, the week that just closed",
    )
    weekly.add_argument("--force", action="store_true", help="Send again a week already sent")
    weekly.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: prepares the digest, says how many recipients it would reach "
        "and sends nothing",
    )

    reminder = sub.add_parser(
        "reminders", help="Raise the reminders of every space and put them in the lists (for cron)"
    )
    reminder.add_argument("--slug", help="A single space, instead of the whole registry")
    reminder.add_argument(
        "--date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Evaluate another day, instead of today",
    )
    reminder.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: says what it would put in the lists and writes nothing",
    )

    sub.add_parser(
        "vapid-keys",
        help="Generate the VAPID key pair for push: writes the lines for the .env (by hand)",
    )

    automations = sub.add_parser(
        "automations",
        help="Open the tasks of the time-triggered rules, in every space (for cron)",
    )
    automations.add_argument("--slug", help="A single space, instead of the whole registry")
    automations.add_argument(
        "--date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Evaluate another day, instead of today (only with --dry-run)",
    )
    automations.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: says what it would open and writes nothing",
    )

    backfill = sub.add_parser(
        "owner-backfill",
        help="Give the clients and deals of a space the owner who created them (--apply writes)",
    )
    backfill.add_argument("--slug", required=True, help="The space to fix (or root)")
    backfill.add_argument(
        "--generic-account",
        action="append",
        default=[],
        metavar="EMAIL",
        help="A generic account: its records are listed and not assigned (repeatable)",
    )
    backfill.add_argument(
        "--apply", action="store_true", help="Writes; without it, only prints what it would do"
    )
    sub.add_parser(
        "contracts-check",
        help="Typeset every framework agreement and letter text from fiction, and ask where the "
        "signing blanks land: can this machine typeset a contract? Writes nothing",
    )
    sub.add_parser(
        "documenso-check",
        help="Does this installation reach its Documenso, and does its token open it?",
    )
    duplicates = sub.add_parser(
        "duplicate-organizations",
        help="List the organizations of a space that share a VAT number (exit 2 when there are)",
    )
    duplicates.add_argument("--slug", required=True, help="The space to read (or root)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "vapid-keys":
        return vapid_keys()
    settings = get_settings()
    match args.command:
        case "createadmin":
            return createadmin(settings, args.email, args.name)
        case "resetpassword":
            return resetpassword(settings, args.email)
        case "seed-templates":
            return seed_templates(settings)
        case "ensure-space-defaults":
            return ensure_space_defaults(settings)
        case "reconcile-due-items":
            return reconcile_due_items(settings, slug=args.slug)
        case "rebuild-identity-index":
            return rebuild_identity_index(settings)
        case "mail-sync" | "gmail-sync":
            return mail_sync(settings, args.email)
        case "digest":
            return digest(
                settings, slug=args.slug, day=args.date, force=args.force, dry_run=args.dry_run
            )
        case "reminders":
            return reminders(settings, slug=args.slug, day=args.date, dry_run=args.dry_run)
        case "automations":
            return automations(settings, slug=args.slug, day=args.date, dry_run=args.dry_run)
        case "contracts-check":
            return contracts_check(settings)
        case "documenso-check":
            return documenso_check(settings)
        case "duplicate-organizations":
            return duplicate_organizations(settings, slug=args.slug)
        case "owner-backfill":
            return owner_backfill(
                settings,
                slug=args.slug,
                generic_accounts=args.generic_account,
                apply=args.apply,
            )
    return 1
