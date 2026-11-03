"""The `pigrocrm` command (`pigrocrm.core.cli:main`).

One module per family: `admin` (createadmin, resetpassword), `spaces` (ensure-space-defaults,
rebuild-identity-index, seed-templates), `cron` (digest, reminders, automations), `gmail`
(gmail-sync), `backfill` (owner-backfill, vapid-keys). What they share, the walk over the root
installation and the registry's spaces, is `spaces_walk`.
"""

import argparse
from collections.abc import Sequence
from datetime import date

from pigrocrm.core.cli.admin import createadmin, resetpassword
from pigrocrm.core.cli.backfill import owner_backfill, vapid_keys
from pigrocrm.core.cli.cron import automations, digest, reminders
from pigrocrm.core.cli.gmail import gmail_sync
from pigrocrm.core.cli.spaces import ensure_space_defaults, rebuild_identity_index, seed_templates
from pigrocrm.core.config import get_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pigrocrm")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("createadmin", help="Crea il primo utente amministratore")
    admin.add_argument("--email")
    admin.add_argument("--name")
    reset = sub.add_parser("resetpassword", help="Imposta una nuova password a un utente esistente")
    reset.add_argument("--email")
    sub.add_parser("seed-templates", help="Crea i template predefiniti, se mancano")
    sub.add_parser(
        "ensure-space-defaults",
        help="Dà a ogni spazio del registro stati, template e categorie predefiniti, se mancano",
    )
    sub.add_parser(
        "rebuild-identity-index",
        help="Indicizza in identities ogni indirizzo trovato negli users di ogni spazio",
    )
    sync = sub.add_parser(
        "gmail-sync",
        help="Sincronizza ogni casella Google collegata, della radice e di ogni spazio (per cron)",
    )
    sync.add_argument("--email", help="Solo la casella con questo indirizzo, ovunque sia collegata")
    weekly = sub.add_parser(
        "digest", help="Manda a ogni spazio il resoconto della settimana (per cron)"
    )
    weekly.add_argument("--slug", help="Un solo spazio, invece di tutto il registro")
    weekly.add_argument(
        "--date",
        # `type=` rather than a parse in the dispatch below: a mistyped date then comes
        # back as argparse's own usage message and an exit 2, not as a traceback at the
        # bottom of a cron log.
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Un giorno della settimana da mandare; senza, la settimana appena chiusa",
    )
    weekly.add_argument(
        "--force", action="store_true", help="Manda di nuovo una settimana già inviata"
    )
    weekly.add_argument(
        "--dry-run",
        action="store_true",
        help="Prova: prepara il resoconto, dice a quanti andrebbe e non manda niente",
    )

    reminder = sub.add_parser(
        "reminders", help="Alza i promemoria di ogni spazio e li mette nelle liste (per cron)"
    )
    reminder.add_argument("--slug", help="Un solo spazio, invece di tutto il registro")
    reminder.add_argument(
        "--date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Valuta un altro giorno, invece di oggi",
    )
    reminder.add_argument(
        "--dry-run",
        action="store_true",
        help="Prova: dice cosa metterebbe nelle liste e non scrive niente",
    )

    sub.add_parser(
        "vapid-keys",
        help="Genera la coppia di chiavi VAPID del push: scrive le righe per il .env (a mano)",
    )

    automations = sub.add_parser(
        "automations",
        help="Apre le attività delle regole che scattano col tempo, in ogni spazio (per cron)",
    )
    automations.add_argument("--slug", help="Un solo spazio, invece di tutto il registro")
    automations.add_argument(
        "--date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Valuta un altro giorno, invece di oggi (solo con --dry-run)",
    )
    automations.add_argument(
        "--dry-run",
        action="store_true",
        help="Prova: dice cosa aprirebbe e non scrive niente",
    )

    backfill = sub.add_parser(
        "owner-backfill",
        help="Dà a clienti e deal di uno spazio il responsabile che li ha creati (--apply scrive)",
    )
    backfill.add_argument("--slug", required=True, help="Lo spazio da sistemare (o root)")
    backfill.add_argument(
        "--generic-account",
        action="append",
        default=[],
        metavar="EMAIL",
        help="Un account generico: i suoi record si elencano e non si assegnano (ripetibile)",
    )
    backfill.add_argument(
        "--apply", action="store_true", help="Scrive; senza, stampa solo cosa farebbe"
    )
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
        case "rebuild-identity-index":
            return rebuild_identity_index(settings)
        case "gmail-sync":
            return gmail_sync(settings, args.email)
        case "digest":
            return digest(
                settings, slug=args.slug, data=args.data, force=args.force, dry_run=args.dry_run
            )
        case "reminders":
            return reminders(settings, slug=args.slug, data=args.data, dry_run=args.dry_run)
        case "automations":
            return automations(settings, slug=args.slug, data=args.data, dry_run=args.dry_run)
        case "owner-backfill":
            return owner_backfill(
                settings,
                slug=args.slug,
                generic_accounts=args.generic_account,
                apply=args.apply,
            )
    return 1
