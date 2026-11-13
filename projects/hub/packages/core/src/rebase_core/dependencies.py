"""The set of collaborators a long-lived adapter builds from the settings.

The MCP server over stdio and over HTTP, and any other process that serves tools or
actions for an admin, need the same things: the model seam of the card and `crm_lead` tasks,
the mail sender, and the service that talks to the CRM. One builder here, so the two
transports cannot drift (a token that reaches one door and not the other, a timeout that differs).

`http` is the seam the CRM services speak through. In production it is `urllib_call`,
whose ten seconds are too short for a link that opens a new space, so that service
gets `urllib_engagements_call` instead; a test's own fake is passed through unchanged.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.crm_link import CrmLeadService
from rebase_core.db import SessionOpener
from rebase_core.http import HttpCall, urllib_call, urllib_engagements_call
from rebase_core.llm import LlmCall, call_for_task
from rebase_core.mail import EmailSender, sender_from_settings


@dataclass(frozen=True)
class Dependencies:
    """What `build_dependencies` hands an adapter. Each field is a factory or a seam an
    adapter calls per request, never a shared session."""

    # Asked on each call: an admin can configure a model in «Modelli AI» while the
    # process runs, and a task with no model configured answers `None`.
    card_llm: Callable[[], LlmCall | None]
    sender: EmailSender | None
    crm_leads: Callable[[Session], CrmLeadService]


def build_dependencies(
    settings: Settings,
    open_session: SessionOpener,
    *,
    http: HttpCall = urllib_call,
) -> Dependencies:
    """Everything a tool server needs beyond a session factory. `open_session` is the
    context-managed session the model seam reads «Modelli AI» through."""
    sender = sender_from_settings(settings)
    crm_http = urllib_engagements_call if http is urllib_call else http

    return Dependencies(
        card_llm=lambda: call_for_task(open_session, settings, "card"),
        sender=sender,
        crm_leads=lambda session: CrmLeadService(
            session,
            settings,
            crm_http,
            lambda: call_for_task(open_session, settings, "crm_lead"),
        ),
    )
