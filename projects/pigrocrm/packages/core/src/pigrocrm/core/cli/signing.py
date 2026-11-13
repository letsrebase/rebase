"""The signing commands: the typesetting check, the Documenso check and the daily recovery of
signatures (REB-1019, moved from the hub's `rebase contracts-check`, `rebase documenso-check` and
the signature half of `rebase contracts-sweep`).

`contracts-check` and `documenso-check` are run by hand, inside the api container, the first by CI's
image job and the second after the `PIGROCRM_DOCUMENSO_*` settings change. The recovery is not a
command: it is a step of the daily `pigrocrm automations` pass (`sweep_space`), so no crontab line
is added.
"""

import sys
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.letter_pdf import compile_text, signature_blanks, typeset
from pigrocrm.core.collaborators.letter_templates import SEEDS, seed_body
from pigrocrm.core.collaborators.letters import LETTER_TEMPLATE_TYPES
from pigrocrm.core.collaborators.signing import SigningService
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.scope import SPACE_SLUG_KEY, bind_scope
from pigrocrm.core.documenso.client import client_from_settings
from pigrocrm.core.errors import DocumensoFailed, DomainError
from pigrocrm.core.http import HttpCall
from pigrocrm.core.mail import sender_from_settings
from pigrocrm.core.space_settings import SpaceSettingsService
from pigrocrm.core.storage.factory import space_storage_from_settings
from pigrocrm.core.tenants import space_base_settings

# Fiction only: no person and no figure anybody would mind reading, so the check can run anywhere.
_CHECK_SCOPE = {
    "emittente": {"ragione_sociale": "Azienda Emittente S.r.l.", "nome": "Emittente"},
    "professionista": {"nome": "Nome Cognome"},
    "cliente": {"ragione_sociale": "Azienda Esempio S.r.l."},
    "incarico": {"numero": "2026-000", "compenso": "450,00 €"},
}


def contracts_check(settings: Settings) -> int:
    """`pigrocrm contracts-check`: can this machine typeset a contract?

    Renders every framework agreement and engagement letter text the module ships, the ordinary
    pair and the occasional pair, from fiction with this machine's pandoc, Typst and typeface, and
    asks Typst where the signing blanks landed. Writes no file and no row. CI's image job runs it
    inside the built image."""
    failed = False
    for seed in SEEDS:
        if seed.type not in LETTER_TEMPLATE_TYPES:
            continue
        try:
            compiled = compile_text(seed_body(seed), _CHECK_SCOPE)
            rendered = typeset(compiled, "Emittente", settings, signing=True)
            blanks = signature_blanks(compiled, "Emittente", settings, signing=True)
        except DomainError as exc:
            print(f"{seed.file}: {exc.message}", file=sys.stderr)
            failed = True
            continue
        names = {blank.name for blank in blanks}
        if "firma professionista" not in names:
            print(f"{seed.file}: no blank for the freelancer's signature", file=sys.stderr)
            failed = True
            continue
        print(
            f"{seed.file}: {len(rendered.pdf)} bytes, version {rendered.version}, "
            f"{len(blanks)} signing blanks"
        )
    return 1 if failed else 0


def documenso_check(settings: Settings, http: HttpCall | None = None) -> int:
    """`pigrocrm documenso-check`: does this installation reach its Documenso, and does its token
    open it? Reads one page of the team's envelopes and prints none of them. Run inside the api
    container after `PIGROCRM_DOCUMENSO_*` change."""
    client = client_from_settings(settings, http)
    if client is None:
        print(
            "PIGROCRM_DOCUMENSO_URL or PIGROCRM_DOCUMENSO_API_TOKEN is missing: signing is off.",
            file=sys.stderr,
        )
        return 1
    try:
        client.ping()
    except DocumensoFailed as exc:
        print(exc.message, file=sys.stderr)
        # Never the token: a 301, a 404, a 502 or a DNS failure look alike from `exc.message`
        # alone, and this detail is what tells them apart.
        if exc.detail:
            print(exc.detail, file=sys.stderr)
        return 1
    print(f"Documenso answers at {settings.documenso_url} and accepts the token.")
    return 0


@dataclass(frozen=True)
class SpaceSweep:
    """What the recovery did in one space: documents moved and documents it could not confirm with
    Documenso, or, in a rehearsal, how many it would have visited."""

    touched: int
    unconfirmed: int
    visited: int = 0


def sweep_space(
    settings: Settings,
    engine: Engine,
    *,
    slug: str | None,
    tenant_id: UUID | None,
    dry_run: bool,
    http: HttpCall | None = None,
) -> SpaceSweep:
    """The signature recovery of one space (or of the root, `slug` `None`): `finish` again for
    every document a lost webhook or a restart left behind. **It always recovers signatures**: the
    hub's round returned early without its engagement service, this one has no such condition.
    Without a Documenso on this installation it still looks, stores no copy and moves nothing that
    needs one. A rehearsal only counts what it would visit and calls nothing."""
    with session_factory(engine)() as session:
        session.info[SPACE_SLUG_KEY] = slug
        bind_scope(session, Actor.system())
        base = space_base_settings(settings, slug)
        effective = SpaceSettingsService(session, base).effective()
        storage = space_storage_from_settings(
            effective, slug, session_factory=lambda: session_factory(engine)()
        )
        service = SigningService(
            session,
            storage,
            effective,
            documenso=client_from_settings(effective, http),
            sender=sender_from_settings(effective),
            tenant_id=tenant_id,
        )
        if dry_run:
            return SpaceSweep(0, 0, visited=len(service.recovery.to_finish()))
        result = service.recovery.sweep()
        return SpaceSweep(result.touched, result.unconfirmed)
