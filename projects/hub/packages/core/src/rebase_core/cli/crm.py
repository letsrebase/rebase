"""The command that files company requests in rebase's CRM."""

from contextlib import closing

from rebase_core.cli.common import Command, no_arguments, open_session
from rebase_core.config import Settings, get_settings


def crm_sweep(settings: Settings) -> str:
    """The round that files company requests in rebase's CRM (REB-868): every live request
    still `to_link` or in `error`, in a session of its own. Its line says how many it filed,
    and how many it tried and left unfiled only when there are any. The draft's model is asked
    for only when a request has none yet (`crm_lead` in «Modelli AI»)."""
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


def crm_sweep_command() -> int:
    """`rebase crm-sweep`: files the company requests the CRM does not have yet, or refused
    for a reason that may have passed (REB-868). Runs every ten minutes on production and the
    preview alike, from the `sweep` service in `docker-compose.yml` (REB-393); the line it
    prints is what `docker logs rebase-sweep-1` (production) or `rebase-preview-sweep-1`
    (preview) shows. Signatures are no longer the hub's: the CRM recovers its own (REB-1108)."""
    print(crm_sweep(get_settings()))
    return 0


COMMANDS = {
    "crm-sweep": Command(no_arguments, lambda args: crm_sweep_command()),
}
