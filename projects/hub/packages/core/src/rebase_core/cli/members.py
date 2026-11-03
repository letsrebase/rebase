"""The people's commands: admin tokens and roles, the welcome mail and the duplicate talents."""

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from rebase_core.cli.common import Command, open_session
from rebase_core.config import Settings, get_settings
from rebase_core.errors import DomainError
from rebase_core.models import USER_ROLES, Freelancer

if TYPE_CHECKING:
    from rebase_core.mail import EmailSender
    from rebase_core.merge import MergeRead


def _ask(value: str | None, label: str) -> str | None:
    """The argument, or, at a terminal, the answer to a prompt for it. Without a terminal a
    missing argument is `None`: a script that forgot a flag fails, rather than waiting for an
    input nobody will give."""
    if value is not None:
        return value
    return input(f"{label}: ") if sys.stdin.isatty() else None


def createtoken(
    email: str | None,
    name: str | None,
    *,
    token_file: str | None = None,
    expires_in_days: int | None = None,
) -> int:
    """`rebase createtoken --email a@b.it [--name "Claude Code"] [--expires-in-days N]
    [--token-file PATH]`: a personal token for an admin, for the operator who has no browser
    at hand (REB-213). The raw value is shown once and the hub keeps only its hash: on stdout
    and nowhere else, or, with `--token-file`, in that file alone, created with mode 0600 and
    never over a file that exists (nothing of the token is printed then, so a captured
    terminal or CI log never holds it). `--expires-in-days` makes it stop working after that
    many days (REB-803); without it the token has no expiry."""
    from rebase_core.admin_tokens import DEFAULT_NAME, AdminTokenService
    from rebase_core.users import UserService

    if expires_in_days is not None and expires_in_days < 1:
        print("--expires-in-days deve essere almeno 1.", file=sys.stderr)
        return 2
    asked = _ask(email, "Email")
    if asked is None:
        print("--email è obbligatorio quando non c'è un terminale.", file=sys.stderr)
        return 2
    email = asked.strip().lower()
    settings = get_settings()
    slot: int | None = None
    if token_file is not None:
        # Before anything is minted: a token that cannot be written down is never made.
        try:
            slot = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print(f"{token_file} esiste già: non lo sovrascrivo.", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"Non posso scrivere {token_file}: {exc.strerror}", file=sys.stderr)
            return 2
    expires_at = (
        datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days is not None else None
    )
    session = open_session(settings)()
    raw: str | None = None
    try:
        admin = UserService(session, settings).by_email(email)
        if admin is None or admin.role != "admin":
            print(f"Nessun amministratore con email {email}", file=sys.stderr)
            return 1
        _, raw = AdminTokenService(session).create(admin.id, name or DEFAULT_NAME, expires_at)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
        if slot is not None and token_file is not None:
            if raw is not None:
                with os.fdopen(slot, "w") as handle:
                    handle.write(raw + "\n")
            else:  # nothing was minted: leave no empty file behind
                os.close(slot)
                os.unlink(token_file)
    if token_file is not None:
        print(f"Token scritto in {token_file}")
    else:
        print(raw)
    return 0


def setrole(
    email: str | None, role: str | None, first_name: str | None, last_name: str | None
) -> int:
    """`rebase setrole --email a@b.it --role admin|member [--first-name ...] [--last-name ...]`:
    admin creation from now on (REB-278), the shape of `createtoken`. Looks up `users`
    by lowercased email, creates a minimal row when none exists (`first_name`/`last_name`
    prompted at a terminal if not given, a refusal without one), sets `role`, and for a
    promotion of a brand-new row sends the same magic link everyone else gets rather than
    a password printed to a terminal. Demoting is `--role member`, fully reversible."""
    from rebase_core.mail import sender_from_settings
    from rebase_core.users import UserService

    settings = get_settings()
    asked_email = _ask(email, "Email")
    asked_role = _ask(role, "Ruolo (member/admin)")
    if asked_email is None or asked_role is None:
        print("--email e --role sono obbligatori quando non c'è un terminale.", file=sys.stderr)
        return 2
    email = asked_email.strip().lower()
    if asked_role not in USER_ROLES:
        print(f"Il ruolo deve essere uno fra {', '.join(USER_ROLES)}", file=sys.stderr)
        return 2
    session = open_session(settings)()
    sent = False
    try:
        users = UserService(session, settings)
        if users.by_email(email) is None:
            first_name = _ask(first_name, "Nome")
            last_name = _ask(last_name, "Cognome")
            if first_name is None or last_name is None:
                print("--first-name e --last-name servono per un indirizzo nuovo.", file=sys.stderr)
                return 2
        user, created = users.set_role(email, asked_role, first_name or "", last_name or "")
        if created and asked_role == "admin":
            sender = sender_from_settings(settings)
            if sender is not None:
                mail = users.request_link(user.email)
                if mail is not None:
                    sent = sender.send(mail)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
    suffix = " (link mandato)" if sent else ""
    print(f"{user.email}: ruolo impostato a {user.role}{suffix}")
    return 0


def talent_duplicates(
    session: Session,
    *,
    apply: bool = False,
    admin_email: str | None = None,
    survivor: UUID | None = None,
    absorbed: UUID | None = None,
    undo: UUID | None = None,
) -> tuple[int, list[str]]:
    """`rebase talent-duplicates`: the people who are on the list twice (REB-800).

    Without flags it only reads: every pair of live cards that share a LinkedIn profile
    (`mergeable`, with what a merge would move), every pair that only shares a name or a
    telephone number (`da rivedere`: never merged on its own), and the sign-ups that
    repeat a profile. It prints counts and internal ids, never a name, an address or a
    profile, so its output can go on a card. `--apply --admin EMAIL` merges every unibile
    pair, the card with the newest CV surviving (`--survivor ID --absorbed ID` merges that
    one pair instead, whichever signals it has: an admin who read both cards chose), then
    creates the LinkedIn unique indexes if nothing shares a profile any more. `--undo
    ACTION_ID --admin EMAIL` puts one merge back. Answers the exit code and the lines."""
    from rebase_core.duplicates import UNIQUE_INDEXES, ensure_unique_indexes, find_duplicates
    from rebase_core.merge import PersonMergeService
    from rebase_core.users import UserService

    lines: list[str] = []
    if (apply or undo is not None) and not admin_email:
        return 2, ["--apply e --undo vogliono --admin con l'indirizzo di un amministratore."]
    admin_id: UUID | None = None
    if admin_email:
        admin = UserService(session).by_email(admin_email)
        if admin is None or admin.role != "admin":
            return 1, [f"Nessun amministratore con email {admin_email.strip().lower()}"]
        admin_id = admin.id
    merges = PersonMergeService(session)
    if undo is not None:
        if admin_id is None:
            return 2, ["--undo vuole --admin con l'indirizzo di un amministratore."]
        try:
            done = merges.undo(undo, admin_id)
        except DomainError as exc:
            return 1, [exc.message]
        moved = ", ".join(f"{key}={count}" for key, count in sorted(done.moved.items()))
        note = (
            " (il profilo LinkedIn resta alla scheda che l'ha tenuto: l'indice unico ne vuole uno)"
            if done.left_behind.get("linkedin")
            else ""
        )
        return 0, [
            f"unione {undo} annullata: scheda {done.absorbed_freelancer_id} di nuovo viva "
            f"({moved}){note}"
        ]
    if survivor is not None or absorbed is not None:
        if not (apply and survivor and absorbed):
            return 2, ["--survivor e --absorbed vanno insieme, e con --apply."]
        if admin_id is None:
            return 2, ["--apply vuole --admin con l'indirizzo di un amministratore."]
        try:
            done = merges.merge(survivor, absorbed, admin_id)
        except DomainError as exc:
            return 1, [exc.message]
        return 0, [_merge_line(done)]

    report = find_duplicates(session)
    lines.append(
        f"{len(report.mergeable)} coppie unibili (stesso LinkedIn), "
        f"{len(report.to_review)} da rivedere (solo nome o telefono), "
        f"{len(report.signups)} iscrizioni che ripetono un profilo"
    )
    failed = 0
    for number, pair in enumerate(report.mergeable, 1):
        head = (
            f"coppia {number}: tieni {pair.survivor.freelancer_id} "
            f"assorbi {pair.absorbed.freelancer_id} [{','.join(pair.signals)}]"
        )
        if not apply:
            try:
                plan = merges.plan(pair.survivor.freelancer_id, pair.absorbed.freelancer_id)
            except DomainError as exc:
                lines.append(f"{head} bloccata: {exc.message}")
                continue
            moved = ", ".join(f"{key}={count}" for key, count in sorted(plan.items())) or "niente"
            lines.append(f"{head} sposterebbe: {moved}")
            continue
        if admin_id is None:  # `--apply` without `--admin` was refused above
            return 2, ["--apply vuole --admin con l'indirizzo di un amministratore."]
        try:
            if (
                session.scalar(
                    select(Freelancer.deleted_at).where(
                        Freelancer.id == pair.absorbed.freelancer_id
                    )
                )
                is not None
            ):
                lines.append(f"{head} già assorbita")
                continue
            done = merges.merge(pair.survivor.freelancer_id, pair.absorbed.freelancer_id, admin_id)
        except DomainError as exc:
            session.rollback()
            failed += 1
            lines.append(f"{head} bloccata: {exc.message}")
            continue
        lines.append(f"{head} {_merge_line(done, short=True)}")
    for pair in report.to_review:
        lines.append(
            f"da rivedere: {pair.survivor.freelancer_id} e {pair.absorbed.freelancer_id} "
            f"[{','.join(pair.signals)}]"
        )
    for lead in report.signups:
        other = lead.card_user_id or lead.other_signup_id
        lines.append(
            f"iscrizione {lead.signup_id} ripete il profilo di "
            f"{'una scheda (utente' if lead.card_user_id else "un'altra iscrizione ("}{other}): "
            "fuori dalle liste dei lead, niente da unire"
        )
    if apply:
        ensured = ensure_unique_indexes(session)
        if ensured.created:
            lines.append(f"indici unici creati: {', '.join(ensured.created)}")
        for name, shared in ensured.blocked.items():
            lines.append(
                f"indice {name} non creato: {shared} profili sono ancora su più righe "
                "(coppie bloccate sopra, iscrizioni o utenti senza scheda da sistemare a mano)"
            )
        if not ensured.created and not ensured.blocked:
            lines.append("indici unici già presenti")
    else:
        present = {
            row
            for row in session.scalars(
                text("SELECT indexname FROM pg_indexes WHERE indexname IN :names").bindparams(
                    bindparam("names", [name for name, _ in UNIQUE_INDEXES], expanding=True)
                )
            )
        }
        missing = [name for name, _ in UNIQUE_INDEXES if name not in present]
        lines.append(f"indici unici mancanti: {', '.join(missing) or 'nessuno'}")
    return (1 if failed else 0), lines


def _merge_line(done: "MergeRead", *, short: bool = False) -> str:
    moved = ", ".join(f"{key}={count}" for key, count in sorted(done.moved.items())) or "niente"
    flag = ", scheda da rigenerare" if done.card_to_regenerate else ""
    text_ = f"unite (azione {done.action_id}): cv {done.cv}, spostato {moved}{flag}"
    return (
        text_
        if short
        else (
            f"scheda {done.absorbed_freelancer_id} unita in {done.survivor_freelancer_id}: {text_}"
        )
    )


def talent_duplicates_command(
    apply: bool,
    admin_email: str | None,
    survivor: UUID | None,
    absorbed: UUID | None,
    undo: UUID | None,
) -> int:
    settings = get_settings()
    session = open_session(settings)()
    try:
        code, lines = talent_duplicates(
            session,
            apply=apply,
            admin_email=admin_email,
            survivor=survivor,
            absorbed=absorbed,
            undo=undo,
        )
    finally:
        session.close()
    for line in lines:
        print(line, file=sys.stderr if code else sys.stdout)
    return code


def send_welcome(
    session: Session, settings: Settings, sender: "EmailSender", emails: Sequence[str] | None
) -> list[tuple[str, str]]:
    """One welcome mail per address the hub knows (ORB-157): every signup and every card,
    or the addresses given. The voice follows what we hold for the address: a card the
    person filled, a card we drafted from public sources, or no card at all (the wizard,
    then). Answers one line per address -- `sent` with the kind, or `rifiutata dal
    provider` -- which is the whole record of the mailing."""
    from rebase_core.freelancers import freelancer_read
    from rebase_core.mail_member import CardSummary, welcome_mail
    from rebase_core.models import Signup, User

    cards = {
        user.email.lower(): (card, user)
        for card, user in session.execute(
            select(Freelancer, User)
            .join(User, User.id == Freelancer.user_id)
            .order_by(Freelancer.created_at, Freelancer.id)
        ).all()
    }
    signups = {
        row.email.lower(): row
        for row in session.scalars(select(Signup).order_by(Signup.created_at, Signup.id)).all()
    }
    if emails is not None:
        targets = [email.strip().lower() for email in emails]
    else:
        targets = list(dict.fromkeys([*signups, *cards]))
    base = settings.hub_url.rstrip("/")
    login, wizard = f"{base}/login", f"{base}/freelance"
    outcomes: list[tuple[str, str]] = []
    for email in targets:
        entry = cards.get(email)
        signup = signups.get(email)
        if entry is not None:
            card, user = entry
            if card.filled_by == "person":
                kind = "person"
                mail = welcome_mail(
                    user.email,
                    user.first_name,
                    login,
                    kind=kind,
                    job_title=card.job_title,
                    # Read through the schema so «completa» is the one definition the
                    # admin area and the member area already read (`_is_complete`),
                    # never a second list of columns written here. For this voice the
                    # CV is the only thing that can be missing: the rate, the position
                    # and the remote option are steps of the wizard the person went
                    # through.
                    complete=freelancer_read(card, user).complete,
                )
            else:
                kind = "admin"
                mail = welcome_mail(
                    user.email,
                    user.first_name,
                    login,
                    kind=kind,
                    summary=CardSummary(
                        first_name=user.first_name,
                        last_name=user.last_name,
                        job_title=card.job_title,
                        linkedin_url=user.linkedin_url,
                        links=tuple(card.links),
                    ),
                )
        elif signup is not None:
            kind = "none"
            mail = welcome_mail(
                signup.email, signup.first_name, login, kind=kind, wizard_link=wizard
            )
        else:
            outcomes.append((email, "indirizzo sconosciuto"))
            continue
        sent = sender.send(mail)
        outcomes.append((email, f"inviata ({kind})" if sent else "rifiutata dal provider"))
    return outcomes


def welcome(emails: Sequence[str], everyone: bool) -> int:
    """`rebase welcome --email a@b.it [--email ...]` or `rebase welcome --all`."""
    from rebase_core.mail import sender_from_settings

    if everyone == bool(emails):
        print("Serve --all oppure almeno un --email, non entrambi.", file=sys.stderr)
        return 2
    settings = get_settings()
    sender = sender_from_settings(settings)
    if sender is None:
        print("Nessuna chiave per la posta: serve REBASE_RESEND_API_KEY.", file=sys.stderr)
        return 1
    session = open_session(settings)()
    try:
        outcomes = send_welcome(session, settings, sender, None if everyone else emails)
    finally:
        session.close()
    for email, outcome in outcomes:
        print(f"{email}: {outcome}")
    return 0 if all(outcome.startswith("sent") for _, outcome in outcomes) else 1


def _token_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--email")
    parser.add_argument("--name")
    parser.add_argument(
        "--expires-in-days", type=int, help="il token smette di funzionare dopo N giorni"
    )
    parser.add_argument(
        "--token-file",
        help="scrive il token in questo file (0600, mai sopra uno che esiste) invece di stamparlo",
    )


def _role_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--email")
    parser.add_argument("--role", choices=USER_ROLES)
    parser.add_argument("--first-name")
    parser.add_argument("--last-name")


def _welcome_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--email", action="append", default=[])
    parser.add_argument("--all", action="store_true")


def _duplicates_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--admin")
    parser.add_argument("--survivor", type=UUID)
    parser.add_argument("--absorbed", type=UUID)
    parser.add_argument("--undo", type=UUID)


COMMANDS = {
    "createtoken": Command(
        _token_arguments,
        lambda args: createtoken(
            args.email,
            args.name,
            token_file=args.token_file,
            expires_in_days=args.expires_in_days,
        ),
    ),
    "setrole": Command(
        _role_arguments,
        lambda args: setrole(args.email, args.role, args.first_name, args.last_name),
    ),
    "welcome": Command(_welcome_arguments, lambda args: welcome(args.email, args.all)),
    "talent-duplicates": Command(
        _duplicates_arguments,
        lambda args: talent_duplicates_command(
            args.apply, args.admin, args.survivor, args.absorbed, args.undo
        ),
    ),
}
