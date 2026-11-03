"""The contracts' and signing commands: typesetting check, the recovery sweep, Documenso."""

import sys
from contextlib import closing

from rebase_core.cli.common import Command, no_arguments, open_session
from rebase_core.config import Settings, get_settings
from rebase_core.http import HttpCall


def contracts_check() -> int:
    """`rebase contracts-check`: can this machine typeset a contract?

    Renders every text, the ordinary pair and the occasional pair (REB-687), from fiction
    with this machine's pandoc, Typst, palette and typeface, and asks Typst where the
    signing blanks landed. Writes no file and no row.
    The `hub-image` preflight check and CI's image job run it inside the built image."""
    from rebase_core.contracts.fields import ContractFailed, Value, merge_data
    from rebase_core.contracts.render import (
        DOCUMENTS,
        company_defaults,
        render,
        signature_blanks,
    )

    # Fiction only, as the public example is: no signer data and no person, so the check
    # can run anywhere and prints nothing anybody would mind reading.
    check_data: dict[str, Value] = {
        "numero": "2026-000",
        "professionista-nome": "Nome Cognome",
        "cliente-ragione-sociale": "Azienda Esempio S.r.l.",
        "compenso": 450,
    }
    try:
        for document in DOCUMENTS:
            data = merge_data(company_defaults(), check_data)
            result = render(document, data)
            blanks = signature_blanks(document, data)
            print(
                f"{document}: {len(result.pdf)} bytes, version {result.version}, "
                f"{len(blanks)} signing blanks"
            )
    except ContractFailed as exc:
        print(exc.message, file=sys.stderr)
        return 1
    return 0


def contracts_sweep() -> int:
    """`rebase contracts-sweep`: redoes what a lost background task or a restart left
    behind (REB-391).

    Runs `SigningRecovery.sweep()` with this environment's own collaborators, built by
    `signing_from_settings`, the same builder `SigningDep` and the MCP server use. Runs
    every ten minutes on production and the preview alike, from the `sweep` service in
    `docker-compose.yml` (REB-393). Prints the `unconfirmed` count only when it is not
    zero (REB-431): an expired, revoked or wrong token, or Documenso itself unreachable,
    otherwise failed silently, leaving a document `sent` and «0 documents resumed»
    printed every ten minutes with nothing to say why. Then the round that links active
    matches to their deal on Pigro (REB-499): how many it linked, always, and how many
    it tried and left unlinked, only when there are any. Then, on a line of its own, the
    round that files company requests in the CRM (`crm_sweep`, REB-868)."""
    from rebase_core.signing import signing_from_settings

    settings = get_settings()
    session = open_session(settings)()
    try:
        result = signing_from_settings(settings)(session).recovery.sweep()
    finally:
        session.close()
    line = f"{result.touched} documents resumed"
    if result.unconfirmed:
        line += f", {result.unconfirmed} unconfirmed"
    line += f", {result.linked} matches linked to Pigro"
    if result.link_failed:
        line += f", {result.link_failed} not linked"
    print(line)
    print(crm_sweep(settings))
    return 0


def crm_sweep(settings: Settings) -> str:
    """The round of `rebase contracts-sweep` that files company requests in rebase's CRM
    (REB-868): every live request still `to_link` or in `error`, in a session of its
    own and wired outside `SigningService`, whose round returns early without an
    engagement service. Its line says how many it filed, and how many it tried and left
    unfiled only when there are any. The draft's model is asked for only when a request
    has none yet (`crm_lead` in «Modelli AI»)."""
    from rebase_core.crm_link import CrmLeadService
    from rebase_core.http import urllib_engagements_call
    from rebase_core.llm import call_for_task

    opener = open_session(settings)
    session = opener()
    try:
        counts = CrmLeadService(
            session,
            settings,
            urllib_engagements_call,
            lambda: call_for_task(lambda: closing(opener()), settings, "crm_lead"),
        ).link_pending()
    finally:
        session.close()
    line = f"{counts.linked} requests linked to the CRM"
    if counts.failed:
        line += f", {counts.failed} not linked"
    return line


def documenso_check(settings: Settings, http: HttpCall | None = None) -> int:
    """`rebase documenso-check`: does this environment reach its Documenso, and does its
    token open it? Reads one page of the team's envelopes and prints none of them. Run
    inside the api container after `REBASE_DOCUMENSO_*` change (REB-393)."""
    from rebase_core.documenso import client_from_settings
    from rebase_core.errors import DocumensoFailed

    client = client_from_settings(settings, http)
    if client is None:
        print(
            "REBASE_DOCUMENSO_URL or REBASE_DOCUMENSO_API_TOKEN is missing: signing is off.",
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
    print(f"Documenso answers at {settings.documenso_url} and accepts the token.")
    return 0


COMMANDS = {
    "contracts-check": Command(no_arguments, lambda args: contracts_check()),
    "contracts-sweep": Command(no_arguments, lambda args: contracts_sweep()),
    "documenso-check": Command(no_arguments, lambda args: documenso_check(get_settings())),
}
