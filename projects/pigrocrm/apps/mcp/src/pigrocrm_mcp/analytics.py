"""PostHog on the MCP surface: every tool call an agent makes, counted.

The web reports pageviews and the activation events (ORB-184); the MCP is the surface
the onboarding spec sells first, and until this module nothing recorded whether an
agent used it. PostHog's own adapter wraps the `MCPServer` and emits `$mcp_tool_call`,
`$mcp_tools_list` and `$mcp_initialize` with the tool name, the parameters, the
response, the duration and the error flag; this module decides four things around it.

- **Off by default.** An empty `PIGROCRM_POSTHOG_KEY` builds no client and changes
  nothing: a self-hosted CRM measures nothing unless its operator says so, and the test
  suite never sends an event. The key is the public project key
  (`shared/analytics/posthog.ts` carries the same one for the browsers), read from the
  environment rather than imported because this process runs in the API image.
- **The same identity as the web.** The actor's user id is the `distinct_id` and the
  space's slug is the `spazio` group, so a customer created from Claude and one created
  from the UI land on the same person and the same space. An actor with no id (the
  fixtures' `mcp` admin) stays anonymous.
- **No schema change, no error-tracking noise.** The adapter would inject a required
  `context` argument on every tool to capture the agent's intent; that alters every
  tool's input schema, which the surface tests pin and which every connected agent has
  already learned. Off. It would also file every exception a tool raises as an
  `$exception` for Error Tracking; `_guard` raises on purpose to hand an agent «cliente
  non trovato», and that is guidance, not a defect. Off too: `$mcp_tool_call` keeps
  `$mcp_is_error` and the message regardless. The same goes for the two the SDK turned
  on by default in 7.56.0, model capture (a required `llm_model` argument on every
  tool) and conversation correlation (a `conversation_id` argument whose description
  asks the agent to echo it, an `_mcp_instructions` property on eligible output
  schemas and the matching object-valued structured results, and a text block
  whenever the SDK mints a conversation id), and for the two virtual tools the SDK can add to the
  listing, `report_missing` and `collect_feedback`: all off, named explicitly, decided
  on REB-642. `test_analytics_instrumentation.py` holds `tools/list` equal with and
  without a key, so an SDK default cannot reshape the surface again unnoticed.
- **One client per process, flushed at exit.** The HTTP transport builds one server per
  space; each shares the one client built here, and `shutdown()` at the end of the
  process (the stdio `main`, the HTTP lifespan) is what gets the last call's event out
  before the loop is torn down.

Design: `docs/design/2026-09-12-posthog-analytics-design.md`, ORB-186.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from pigrocrm.core.config import Settings
from pigrocrm_mcp.context import ActorProvider

if TYPE_CHECKING:
    from mcp.server import MCPServer
    from posthog import Posthog
    from posthog.mcp.types import UserIdentity

GROUP_TYPE = "spazio"
ROOT_GROUP = "root"

_client: Posthog | None = None
_client_key: tuple[str, str] | None = None
_lock = threading.Lock()


def space_group(settings: Settings) -> str:
    """The `spazio` group key: the installation's slug, `root` when it has none."""
    return settings.root_slug or ROOT_GROUP


def identity_for(
    actor_provider: ActorProvider, spazio: str
) -> Callable[[object, object], UserIdentity | None]:
    """The callback the adapter calls per request: the actor of *this* call.

    Reads the provider each time rather than once, because the HTTP transport
    (ORB-170) resolves the actor per request from the bearer token; the stdio process
    resolves it once at start-up and the provider simply keeps answering the same one.
    """
    from posthog.mcp.types import UserIdentity

    def identify(request: object, extra: object) -> UserIdentity | None:
        actor = actor_provider()
        if actor.id is None:
            return None
        return UserIdentity(
            distinct_id=str(actor.id),
            properties={"ruolo": actor.role, "via": actor.type},
            groups={GROUP_TYPE: spazio},
        )

    return identify


def build_client(settings: Settings) -> Posthog | None:
    """The process's one client when the installation has a key, `None` when it has not.

    One per process rather than one per server: the HTTP transport builds a server per
    space, and a consumer thread per space would each claim the SDK's single one-second
    exit budget. Keyed by key and host so a test that changes settings gets a fresh one.
    """
    global _client, _client_key
    if not settings.posthog_key:
        return None
    wanted = (settings.posthog_key, settings.posthog_host)
    with _lock:
        if _client is None or _client_key != wanted:
            from posthog import Posthog

            _client = Posthog(settings.posthog_key, host=settings.posthog_host)
            _client_key = wanted
        return _client


def shutdown() -> None:
    """Flush and stop the process's client, if one was built. Safe to call twice."""
    global _client, _client_key
    with _lock:
        client, _client, _client_key = _client, None, None
    if client is not None:
        client.shutdown()


def install(
    mcp: MCPServer,
    settings: Settings,
    actor_provider: ActorProvider,
    client: Posthog | None = None,
    space: str | None = None,
) -> bool:
    """Wrap `mcp` for PostHog when the installation asks for it; answers whether it did.

    `client` is injectable so a test can hand one whose `capture` it observes; every
    other caller lets `build_client` decide from the settings. `space` is the slug of
    the space this server serves: the HTTP transport builds one server per space and
    names it; the stdio process serves the installation itself and leaves it to
    `space_group`. Idempotent per server instance, like the adapter underneath: a second
    call on the same server keeps the first client.
    """
    resolved = client if client is not None else build_client(settings)
    if resolved is None:
        return False
    from posthog.mcp import instrument
    from posthog.mcp.types import MCPAnalyticsOptions

    # Every option the tool surface depends on is named here, never left to the SDK's
    # default: posthog 7.56.0 flipped `capture_model` and `enable_conversation_id` from
    # off to on, and Dependabot's bump to 7.60.1 (PR #516) would have given every tool a
    # required `llm_model` argument, a `conversation_id` and an `_mcp_instructions`
    # output property, with no test noticing (REB-642). Both stay off by decision on that
    # card, and so do the two virtual tools the SDK can add to the listing
    # (`report_missing`, `collect_feedback`), off by default today: an agent receives the
    # schemas the uninstrumented server advertises, exactly.
    instrument(
        mcp,
        resolved,
        MCPAnalyticsOptions(
            context=False,
            enable_exception_autocapture=False,
            enable_conversation_id=False,
            capture_model=False,
            report_missing=False,
            collect_feedback=False,
            identify=identity_for(actor_provider, space or space_group(settings)),
        ),
    )
    return True
