"""`rebase`: the operator's commands."""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.admin_tokens import DEFAULT_NAME, AdminTokenService
from rebase_core.config import Settings, get_settings
from rebase_core.contracts.fields import ContractFailed, Value, merge_data
from rebase_core.contracts.render import (
    DOCUMENTS,
    ContractRenderer,
    company_defaults,
    render,
    signature_blanks,
)
from rebase_core.conversions import pixel_from_settings
from rebase_core.db import create_engine_from_settings, session_factory
from rebase_core.documenso import client_from_settings
from rebase_core.errors import DocumensoFailed, DomainError
from rebase_core.freelancers import freelancer_read
from rebase_core.http import HttpCall
from rebase_core.mail import CardSummary, EmailSender, sender_from_settings, welcome_mail
from rebase_core.models import USER_ROLES, Freelancer, Signup, User
from rebase_core.signing import SigningService
from rebase_core.users import UserService


def createtoken(email: str | None, nome: str | None) -> int:
    """`rebase createtoken --email a@b.it [--nome "Claude Code"]`: a personal token
    for an admin, for the operator who has no browser at hand (REB-213). Prints the raw
    value once, on stdout and nowhere else; the hub keeps only its hash."""
    settings = get_settings()
    email = (email or input("Email: ")).strip().lower()
    session = session_factory(create_engine_from_settings(settings))()
    try:
        admin = UserService(session, settings).by_email(email)
        if admin is None or admin.role != "admin":
            print(f"Nessun amministratore con email {email}", file=sys.stderr)
            return 1
        _, raw = AdminTokenService(session).create(admin.id, nome or DEFAULT_NAME)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
    print(raw)
    return 0


def setrole(email: str | None, role: str | None, nome: str | None, cognome: str | None) -> int:
    """`rebase setrole --email a@b.it --role admin|member [--nome ...] [--cognome ...]`:
    admin creation from now on (REB-278), the shape of `createtoken`. Looks up `users`
    by lowercased email, creates a minimal row when none exists (`nome`/`cognome`
    prompted if not given), sets `role`, and for a promotion of a brand-new row sends
    the same magic link everyone else gets rather than a password printed to a
    terminal. Demoting is `--role member`, fully reversible."""
    settings = get_settings()
    email = (email or input("Email: ")).strip().lower()
    role = role or input("Ruolo (member/admin): ")
    if role not in USER_ROLES:
        print(f"Il ruolo deve essere uno fra {', '.join(USER_ROLES)}", file=sys.stderr)
        return 2
    session = session_factory(create_engine_from_settings(settings))()
    sent = False
    try:
        users = UserService(session, settings)
        if users.by_email(email) is None:
            nome = nome or input("Nome: ")
            cognome = cognome or input("Cognome: ")
        user, created = users.set_role(email, role, nome or "", cognome or "")
        if created and role == "admin":
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


def conversions_check() -> int:
    """`rebase conversions-check`: does the Conversions API key work?

    Sends one event with `validate_only: true`, which asks OpenAI to check it and record
    nothing. The real events are deliberately invisible -- sent from a background task,
    never logged -- so a wrong key would mean a campaign with no conversions and no sign
    anywhere that the key was the reason. This is how somebody finds out in ten seconds.
    Prints the status and nothing else: the key is never echoed.
    """
    settings = get_settings()
    pixel = pixel_from_settings(settings)
    if pixel is None:
        print(
            "Nessun pixel configurato: servono REBASE_OPENAI_PIXEL_ID e "
            "REBASE_OPENAI_CONVERSIONS_API_KEY.",
            file=sys.stderr,
        )
        return 1
    outcome = pixel.send(
        # A validation-only event needs an id like any other, and this one must not look
        # like a conversion in case the flag is ever ignored.
        event_id=f"verifica-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        source_url=settings.signup_url,
        validate_only=True,
    )
    if outcome.sent:
        print(f"La chiave funziona: evento validato ({outcome.status}), niente registrato.")
        return 0
    print(
        f"OpenAI ha rifiutato la verifica: {outcome.status} {outcome.detail}".strip(),
        file=sys.stderr,
    )
    return 1


# Fiction only, as the public example is: no signer data and no person, so the check
# can run anywhere and prints nothing anybody would mind reading.
_CHECK_DATA: dict[str, Value] = {
    "numero": "2026-000",
    "professionista-nome": "Nome Cognome",
    "cliente-ragione-sociale": "Azienda Esempio S.r.l.",
    "compenso": 450,
}


def contracts_check() -> int:
    """`rebase contracts-check`: can this machine typeset a contract?

    Renders both texts from fiction with this machine's pandoc, Typst, palette and
    typeface, and asks Typst where the signing blanks landed. Writes no file and no row.
    The `hub-image` preflight check and CI's image job run it inside the built image."""
    try:
        for document in DOCUMENTS:
            data = merge_data(company_defaults(), _CHECK_DATA)
            result = render(document, data)
            blanks = signature_blanks(document, data)
            print(
                f"{document}: {len(result.pdf)} byte, versione {result.version}, "
                f"{len(blanks)} spazi da firmare"
            )
    except ContractFailed as exc:
        print(exc.message, file=sys.stderr)
        return 1
    return 0


def contracts_sweep() -> int:
    """`rebase contracts-sweep`: redoes what a lost background task or a restart left
    behind (REB-391).

    Runs `SigningService.sweep()` with this environment's own collaborators -- the same
    ones `SigningDep` builds for a request, gathered here by hand since this command has
    no request to build one from. Runs every ten minutes on production and the preview
    alike, from the `sweep` service in `docker-compose.yml` (REB-393). Prints the
    `unconfirmed` count only when it is not zero (REB-431): an expired, revoked or
    wrong token, or Documenso itself unreachable, otherwise failed silently, leaving a
    document `inviato` and «0 documenti ripresi» printed every ten minutes with nothing
    to say why."""
    settings = get_settings()
    session = session_factory(create_engine_from_settings(settings))()
    try:
        signing = SigningService(
            session,
            renderer=ContractRenderer(),
            documenso=client_from_settings(settings),
            sender=sender_from_settings(settings),
            signer_json=settings.signer_json,
            contracts_mail=settings.contracts_mail,
            allow_draft=settings.contracts_allow_draft,
        )
        result = signing.sweep()
    finally:
        session.close()
    line = f"{result.touched} documenti ripresi"
    if result.unconfirmed:
        line += f", {result.unconfirmed} non confermati"
    print(line)
    return 0


def documenso_check(settings: Settings, http: HttpCall | None = None) -> int:
    """`rebase documenso-check`: does this environment reach its Documenso, and does its
    token open it? Reads one page of the team's envelopes and prints none of them. Run
    inside the api container after `REBASE_DOCUMENSO_*` change (REB-393)."""
    client = client_from_settings(settings, http)
    if client is None:
        print(
            "REBASE_DOCUMENSO_URL o REBASE_DOCUMENSO_API_TOKEN mancano: la firma è spenta.",
            file=sys.stderr,
        )
        return 1
    try:
        client.ping()
    except DocumensoFailed as exc:
        print(exc.message, file=sys.stderr)
        # Never the token: a 301, a 404, a 502 or a DNS failure look alike from
        # `exc.message` alone, and this detail is what tells them apart.
        if exc.detail:
            print(exc.detail, file=sys.stderr)
        return 1
    print(f"Documenso risponde a {settings.documenso_url} e accetta il token.")
    return 0


def send_welcome(
    session: Session, settings: Settings, sender: EmailSender, emails: Sequence[str] | None
) -> list[tuple[str, str]]:
    """One welcome mail per address the hub knows (ORB-157): every signup and every card,
    or the addresses given. The voice follows what we hold for the address: a card the
    person filled, a card we drafted from public sources, or no card at all (the wizard,
    then). Answers one line per address -- `inviata` with the kind, or `rifiutata dal
    provider` -- which is the whole record of the mailing."""
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
    accedi, wizard = f"{base}/accedi", f"{base}/freelance"
    outcomes: list[tuple[str, str]] = []
    for email in targets:
        entry = cards.get(email)
        signup = signups.get(email)
        if entry is None and signup is None:
            outcomes.append((email, "indirizzo sconosciuto"))
            continue
        if entry is not None:
            card, user = entry
            if card.compilata_da == "persona":
                kind = "persona"
                mail = welcome_mail(
                    user.email,
                    user.nome,
                    accedi,
                    kind=kind,
                    posizione=card.posizione,
                    # Read through the schema so «completa» is the one definition the
                    # admin area and the member area already read (`_is_complete`),
                    # never a second list of columns written here. For this voice the
                    # CV is the only thing that can be missing: the rate, the position
                    # and the remote option are steps of the wizard the person went
                    # through.
                    completa=freelancer_read(card, user).completa,
                )
            else:
                kind = "admin"
                mail = welcome_mail(
                    user.email,
                    user.nome,
                    accedi,
                    kind=kind,
                    summary=CardSummary(
                        nome=user.nome,
                        cognome=user.cognome,
                        posizione=card.posizione,
                        linkedin_url=user.linkedin_url,
                        links=tuple(card.links),
                    ),
                )
        else:
            assert signup is not None
            kind = "nessuna"
            mail = welcome_mail(signup.email, signup.nome, accedi, kind=kind, wizard_link=wizard)
        sent = sender.send(mail)
        outcomes.append((email, f"inviata ({kind})" if sent else "rifiutata dal provider"))
    return outcomes


def welcome(emails: Sequence[str], everyone: bool) -> int:
    """`rebase welcome --email a@b.it [--email ...]` or `rebase welcome --all`."""
    if everyone == bool(emails):
        print("Serve --all oppure almeno un --email, non entrambi.", file=sys.stderr)
        return 2
    settings = get_settings()
    sender = sender_from_settings(settings)
    if sender is None:
        print("Nessuna chiave per la posta: serve REBASE_RESEND_API_KEY.", file=sys.stderr)
        return 1
    session = session_factory(create_engine_from_settings(settings))()
    try:
        outcomes = send_welcome(session, settings, sender, None if everyone else emails)
    finally:
        session.close()
    for email, outcome in outcomes:
        print(f"{email}: {outcome}")
    return 0 if all(outcome.startswith("inviata") for _, outcome in outcomes) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rebase")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "conversions-check",
        help="Verifica la chiave della Conversions API senza registrare una conversione",
    )
    sub.add_parser(
        "contracts-check",
        help="Compone i due contratti con pandoc e Typst: questa macchina li sa generare?",
    )
    sub.add_parser(
        "documenso-check",
        help="Documenso risponde, e il token di questo ambiente lo apre?",
    )
    sub.add_parser(
        "contracts-sweep",
        help="Rifà quanto un riavvio o una mail rifiutata hanno lasciato indietro",
    )
    token = sub.add_parser(
        "createtoken", help="Crea un token personale di un amministratore, per un agente"
    )
    token.add_argument("--email")
    token.add_argument("--nome")
    role_parser = sub.add_parser(
        "setrole", help="Imposta il ruolo (member/admin) di un indirizzo, creandolo se serve"
    )
    role_parser.add_argument("--email")
    role_parser.add_argument("--role", choices=USER_ROLES)
    role_parser.add_argument("--nome")
    role_parser.add_argument("--cognome")
    welcome_parser = sub.add_parser(
        "welcome",
        help="Manda la mail «la tua area è aperta» a un indirizzo o a tutti quelli noti",
    )
    welcome_parser.add_argument("--email", action="append", default=[])
    welcome_parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "conversions-check":
        return conversions_check()
    if args.command == "contracts-check":
        return contracts_check()
    if args.command == "documenso-check":
        return documenso_check(get_settings())
    if args.command == "contracts-sweep":
        return contracts_sweep()
    if args.command == "createtoken":
        return createtoken(args.email, args.nome)
    if args.command == "setrole":
        return setrole(args.email, args.role, args.nome, args.cognome)
    if args.command == "welcome":
        return welcome(args.email, args.all)
    parser.error(f"comando sconosciuto: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
