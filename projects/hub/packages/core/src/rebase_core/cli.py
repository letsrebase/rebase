"""`rebase`: the operator's commands."""

import argparse
import base64
import binascii
import os
import sys
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from rebase_core.admin_tokens import DEFAULT_NAME, AdminTokenService
from rebase_core.ai_config import AiConfigService
from rebase_core.campaigns.sender import campaign_sender_from_settings
from rebase_core.campaigns.tick import run_tick
from rebase_core.cards import CardWriter
from rebase_core.config import Settings, get_settings
from rebase_core.contracts.fields import ContractFailed, Value, merge_data
from rebase_core.contracts.render import (
    DOCUMENTS,
    company_defaults,
    render,
    signature_blanks,
)
from rebase_core.conversions import pixel_from_settings
from rebase_core.db import create_engine_from_settings, session_factory
from rebase_core.documenso import client_from_settings
from rebase_core.duplicates import UNIQUE_INDEXES, ensure_unique_indexes, find_duplicates
from rebase_core.errors import DocumensoFailed, DomainError
from rebase_core.freelancers import freelancer_read
from rebase_core.http import HttpCall
from rebase_core.llm import call_for_task
from rebase_core.mail import CardSummary, EmailSender, sender_from_settings, welcome_mail
from rebase_core.merge import MergeRead, PersonMergeService
from rebase_core.models import USER_ROLES, Freelancer, Signup, User
from rebase_core.secrets import KEY_BYTES
from rebase_core.signing import signing_from_settings
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

    Renders every text, the ordinary pair and the occasional pair (REB-687), from fiction
    with this machine's pandoc, Typst, palette and typeface, and asks Typst where the
    signing blanks landed. Writes no file and no row.
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

    Runs `SigningService.sweep()` with this environment's own collaborators, built by
    `signing_from_settings`, the same builder `SigningDep` and the MCP server use. Runs
    every ten minutes on production and the preview alike, from the `sweep` service in
    `docker-compose.yml` (REB-393). Prints the `unconfirmed` count only when it is not
    zero (REB-431): an expired, revoked or wrong token, or Documenso itself unreachable,
    otherwise failed silently, leaving a document `inviato` and «0 documenti ripresi»
    printed every ten minutes with nothing to say why. Then the round that links active
    matches to their deal on Pigro (REB-499): how many it linked, always, and how many
    it tried and left unlinked, only when there are any."""
    settings = get_settings()
    session = session_factory(create_engine_from_settings(settings))()
    try:
        result = signing_from_settings(settings)(session).sweep()
    finally:
        session.close()
    line = f"{result.touched} documenti ripresi"
    if result.unconfirmed:
        line += f", {result.unconfirmed} non confermati"
    line += f", {result.linked} match collegati a Pigro"
    if result.link_failed:
        line += f", {result.link_failed} non collegati"
    print(line)
    return 0


def campaigns_tick() -> int:
    """`rebase campaigns-tick`: one pass of the campaigns loop (P-REB-41). Runs every
    minute from the `campaigns` service in `docker-compose.yml`. Without a Resend key it
    sends nothing and says so, and exits 0 so the loop keeps running."""
    settings = get_settings()
    sender = campaign_sender_from_settings(settings)
    if sender is None:
        print("invio non configurato: nessuna campagna parte senza REBASE_RESEND_API_KEY")
        return 0
    session = session_factory(create_engine_from_settings(settings))()
    try:
        result = run_tick(session, sender, settings)
    finally:
        session.close()
    print(
        f"{result.campagne} campagne, {result.inviate} inviate, "
        f"{result.saltate} saltate, {result.fallite} fallite, "
        f"{result.stampate} esiti registrati"
    )
    return 0


def cards_refresh(limit: int) -> int:
    """`rebase cards-refresh [--limit N]`: the anonymous card of every freelancer whose CV
    has none yet, or has changed since, `limit` at a time, the oldest first (REB-510).
    Run once after the deploy that brings the key, and again until it prints «0 schede
    scritte, 0 non riuscite». A CV that failed on its own account is not tried again
    until it changes, so the runs end; a batch that met an outage stops there, counts it
    among «non riuscite», says «fermato: Claude non disponibile» and leaves it and the
    rest to the next run. A card still giving the gender away after its rewrite is among
    «non riuscite» (REB-802), and not tried again until its CV changes. Without a model
    configured for the `card` task it says so, rather than printing «0 schede scritte» for
    ever."""
    if limit < 1:
        print("--limit deve essere almeno 1.", file=sys.stderr)
        return 2
    settings = get_settings()
    open_session = session_factory(create_engine_from_settings(settings))
    llm = call_for_task(lambda: closing(open_session()), settings, "card")
    if llm is None:
        print(
            "Nessun modello configurato per le schede: apri «Modelli AI».",
            file=sys.stderr,
        )
        return 1
    session = open_session()
    try:
        result = CardWriter(session, llm).refresh_stale(limit)
    finally:
        session.close()
    summary = f"{result.written} schede scritte, {result.failed} non riuscite"
    if result.stopped:
        summary += ", fermato: Claude non disponibile"
    print(summary)
    return 0


def ai_seed(eur_per_usd: str) -> int:
    """`rebase ai-seed --eur-per-usd 0.92`: once, moves `REBASE_ANTHROPIC_API_KEY` and
    `REBASE_TEAM_BUILDER_MODEL` (read from the environment: the Settings fields are gone,
    REB-831) into «Modelli AI»: the key sealed, `anthropic` on, both tasks on that model,
    and the prices of the Claude models in euros at `--eur-per-usd` (EUR per 1 USD, the
    inverse of the ECB's quote). Prints the hint of the key, never the key (REB-832)."""
    try:
        rate = Decimal(eur_per_usd)
    except InvalidOperation:
        print("--eur-per-usd deve essere un numero, per esempio 0.92.", file=sys.stderr)
        return 2
    api_key = os.environ.get("REBASE_ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("REBASE_ANTHROPIC_API_KEY manca: non c'è niente da spostare.", file=sys.stderr)
        return 1
    model = os.environ.get("REBASE_TEAM_BUILDER_MODEL", "").strip() or "claude-opus-5"
    settings = get_settings()
    session = session_factory(create_engine_from_settings(settings))()
    try:
        result = AiConfigService(session, settings).seed_from_env(None, api_key, model, rate)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
    print(f"chiave salvata, finisce con …{result.key_hint[-4:]}")
    print(f"modello {result.model} per le schede e per il team, {result.prices} prezzi scritti")
    return 0


def _key_from_argument(value: str) -> bytes | None:
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    return key if len(key) == KEY_BYTES else None


def ai_rotate_secrets(old_key: str, new_key: str) -> int:
    """`rebase ai-rotate-secrets --old-key X --new-key Y`: re-seals every stored provider
    key from the old `REBASE_SECRETS_KEY` to the new one, in one transaction, and prints
    how many. Put the new value in `.env` afterwards (REB-832)."""
    old, new = _key_from_argument(old_key), _key_from_argument(new_key)
    if old is None or new is None:
        print("Le due chiavi devono essere 32 byte in base64.", file=sys.stderr)
        return 2
    settings = get_settings()
    session = session_factory(create_engine_from_settings(settings))()
    try:
        count = AiConfigService(session, settings).rotate(old, new)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
    print(f"{count} chiavi ricifrate")
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
    (`unibili`, with what a merge would move), every pair that only shares a name or a
    telephone number (`da rivedere`: never merged on its own), and the sign-ups that
    repeat a profile. It prints counts and internal ids, never a name, an address or a
    profile, so its output can go on a card. `--apply --admin EMAIL` merges every unibile
    pair, the card with the newest CV surviving (`--survivor ID --absorbed ID` merges that
    one pair instead, whichever signals it has: an admin who read both cards chose), then
    creates the LinkedIn unique indexes if nothing shares a profile any more. `--undo
    ACTION_ID --admin EMAIL` puts one merge back. Answers the exit code and the lines."""
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
        assert admin_id is not None
        try:
            done = merges.undo(undo, admin_id)
        except DomainError as exc:
            return 1, [exc.message]
        moved = ", ".join(f"{key}={count}" for key, count in sorted(done.moved.items()))
        note = (
            " (il profilo LinkedIn resta alla scheda che l'ha tenuto: l'indice unico ne vuole uno)"
            if done.lasciati.get("linkedin")
            else ""
        )
        return 0, [
            f"unione {undo} annullata: scheda {done.absorbed_freelancer_id} di nuovo viva "
            f"({moved}){note}"
        ]
    if survivor is not None or absorbed is not None:
        if not (apply and survivor and absorbed):
            return 2, ["--survivor e --absorbed vanno insieme, e con --apply."]
        assert admin_id is not None
        try:
            done = merges.merge(survivor, absorbed, admin_id)
        except DomainError as exc:
            return 1, [exc.message]
        return 0, [_merge_line(done)]

    report = find_duplicates(session)
    lines.append(
        f"{len(report.unibili)} coppie unibili (stesso LinkedIn), "
        f"{len(report.da_rivedere)} da rivedere (solo nome o telefono), "
        f"{len(report.iscrizioni)} iscrizioni che ripetono un profilo"
    )
    failed = 0
    for number, pair in enumerate(report.unibili, 1):
        head = (
            f"coppia {number}: tieni {pair.survivor.freelancer_id} "
            f"assorbi {pair.assorbito.freelancer_id} [{','.join(pair.segnali)}]"
        )
        if not apply:
            try:
                plan = merges.plan(pair.survivor.freelancer_id, pair.assorbito.freelancer_id)
            except DomainError as exc:
                lines.append(f"{head} bloccata: {exc.message}")
                continue
            moved = ", ".join(f"{key}={count}" for key, count in sorted(plan.items())) or "niente"
            lines.append(f"{head} sposterebbe: {moved}")
            continue
        assert admin_id is not None
        try:
            if (
                session.scalar(
                    select(Freelancer.deleted_at).where(
                        Freelancer.id == pair.assorbito.freelancer_id
                    )
                )
                is not None
            ):
                lines.append(f"{head} già assorbita")
                continue
            done = merges.merge(pair.survivor.freelancer_id, pair.assorbito.freelancer_id, admin_id)
        except DomainError as exc:
            session.rollback()
            failed += 1
            lines.append(f"{head} bloccata: {exc.message}")
            continue
        lines.append(f"{head} {_merge_line(done, short=True)}")
    for pair in report.da_rivedere:
        lines.append(
            f"da rivedere: {pair.survivor.freelancer_id} e {pair.assorbito.freelancer_id} "
            f"[{','.join(pair.segnali)}]"
        )
    for lead in report.iscrizioni:
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


def _merge_line(done: MergeRead, *, short: bool = False) -> str:
    moved = ", ".join(f"{key}={count}" for key, count in sorted(done.moved.items())) or "niente"
    flag = ", scheda da rigenerare" if done.scheda_da_rigenerare else ""
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
    session = session_factory(create_engine_from_settings(settings))()
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
        help="Compone i contratti con pandoc e Typst: questa macchina li sa generare?",
    )
    sub.add_parser(
        "documenso-check",
        help="Documenso risponde, e il token di questo ambiente lo apre?",
    )
    sub.add_parser(
        "contracts-sweep",
        help="Rifà quanto un riavvio o una mail rifiutata hanno lasciato indietro",
    )
    sub.add_parser(
        "campaigns-tick",
        help="Invia le campagne arrivate alla loro ora, una mail alla volta",
    )
    cards_parser = sub.add_parser(
        "cards-refresh",
        help="Scrive la scheda anonima di chi ha un CV nuovo o cambiato, i più vecchi prima",
    )
    cards_parser.add_argument("--limit", type=int, default=50)
    seed_parser = sub.add_parser(
        "ai-seed",
        help="Sposta la chiave Anthropic e il modello vecchi in «Modelli AI», con i prezzi",
    )
    seed_parser.add_argument("--eur-per-usd", required=True)
    rotate_parser = sub.add_parser(
        "ai-rotate-secrets", help="Ricifra le chiavi dei modelli con un nuovo REBASE_SECRETS_KEY"
    )
    rotate_parser.add_argument("--old-key", required=True)
    rotate_parser.add_argument("--new-key", required=True)

    dup_parser = sub.add_parser(
        "talent-duplicates",
        help="Elenca chi è in lista due volte (a secco) e con --apply unisce le schede",
    )
    dup_parser.add_argument("--apply", action="store_true")
    dup_parser.add_argument("--admin")
    dup_parser.add_argument("--survivor", type=UUID)
    dup_parser.add_argument("--absorbed", type=UUID)
    dup_parser.add_argument("--undo", type=UUID)
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
    if args.command == "campaigns-tick":
        return campaigns_tick()
    if args.command == "cards-refresh":
        return cards_refresh(args.limit)
    if args.command == "ai-seed":
        return ai_seed(args.eur_per_usd)
    if args.command == "ai-rotate-secrets":
        return ai_rotate_secrets(args.old_key, args.new_key)

    if args.command == "talent-duplicates":
        return talent_duplicates_command(
            args.apply, args.admin, args.survivor, args.absorbed, args.undo
        )
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
