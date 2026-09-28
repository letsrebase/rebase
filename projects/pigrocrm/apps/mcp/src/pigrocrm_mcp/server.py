import functools
import inspect
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.context import ServerMiddleware
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

from pigrocrm.core.config import Settings, get_settings, gmail_configured
from pigrocrm.core.errors import DomainError
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.storage import DocumentStorage, storage_from_settings
from pigrocrm_mcp import analytics
from pigrocrm_mcp.context import ActorProvider, McpContext, SessionProvider
from pigrocrm_mcp.errors import to_agent_message, to_domain_error
from pigrocrm_mcp.resources import entities
from pigrocrm_mcp.tools.schema import ENTITY_TYPES, entity_schema

INSTRUCTIONS = """PigroCRM — CRM per freelancer e piccole startup italiane.

Prima di creare o aggiornare un'entità, chiama `describe_schema` per conoscere i campi
personalizzati definiti dall'utente: non sono codificati negli strumenti e cambiano nel tempo.
Per leggere il contesto completo usa le risorse `customer://`, `person://` e `deal://`.
Nulla viene cancellato fisicamente: le operazioni di archiviazione sono reversibili.
L'unica eccezione e' `discard_proforma`, che non si ripristina: una proforma scartata si ricrea.
`confirm_proforma` non torna indietro, ma non chiude nulla: una proforma confermata resta
modificabile e si puo' ancora scartare.
"""


# The JSON-RPC code a domain refusal (a conflict, a permission, a value the domain rejects)
# carries on a protocol-error surface: implementation-defined, so a client never reads an
# expected refusal as the `-32603` the SDK answers for a crash. The 2026-07-28 allocation
# policy (`mcp_types/jsonrpc.py` lines 67-71) leaves -32000..-32019 to implementations; the
# SDK takes -32000 and -32001 for itself and -32002 is retired, so this sits clear of all three.
DOMAIN_REFUSAL = -32010


def _as_tool_error(exc: DomainError) -> ToolError:
    """Carry `to_agent_message`'s diagnosis out of a tool, as the failure a tool is meant
    to report.

    Verified against the installed package (mcp==2.2.0), not assumed from its changelog. A
    tool's error path (`mcpserver/tools/base.py`, `Tool.run`, lines 205-207) re-raises a
    `ToolError` as «Error executing tool <name>: <message>», an `is_error` result the model
    reads, and anything else but an `MCPError` as a bare «Error executing tool <name>»
    (lines 208-210): since 2.2 a crash keeps its text on the server. An `MCPError` would
    leave the tool as a JSON-RPC error instead of a result, which is why a tool does not
    share `_as_protocol_error`.
    """
    return ToolError(to_agent_message(exc))


def _as_protocol_error(exc: DomainError) -> MCPError:
    """Carry `to_agent_message`'s diagnosis out of a resource or a prompt, the two surfaces
    whose refusal is a JSON-RPC error rather than a result.

    On mcp==2.2.0 an `MCPError` is the one type both pass on with its own text and its own
    code. A resource: `ResourceTemplate.create_resource` (`mcpserver/resources/templates.py`
    lines 245-246) and `MCPServer.read_resource` (`mcpserver/server.py` lines 604-605)
    re-raise it, and `_handle_read_resource` (lines 458-466) converts only a `ResourceError`,
    to `-32603` unless it is `ResourceNotFoundError`, so raising the `MCPError` here is what
    lets this module choose the code. A prompt: `Prompt.render`
    (`mcpserver/prompts/base.py` lines 210-213) re-raises an `MCPError` and replaces anything
    else with «Error rendering prompt <name>», with nothing after it, and
    `MCPServer.get_prompt` (`mcpserver/server.py` lines 1358-1359) re-raises it again. Both
    dispatchers then write its `ErrorData` to the wire as it is
    (`shared/jsonrpc_dispatcher.py` lines 98-99, `handler_exception_to_error_data`, which
    the modern entry in `server/runner.py` shares); anything else, the modern protocol
    answers with «Internal server error».

    The code: `-32602` for `not_found`, because the prompts section of the MCP spec
    (2025-11-25 and 2026-07-28, «Error Handling») answers an invalid prompt name and a
    missing argument with `-32602` (Invalid params), an argument naming a record that does
    not exist is the same kind of mistake, and a missing resource has been `-32602` since
    SEP-2164 (`ResourceNotFoundError`'s docstring). Every other refusal takes
    `DOMAIN_REFUSAL`, and `-32603` is left to the SDK for a real crash.
    """
    code = INVALID_PARAMS if exc.code == "not_found" else DOMAIN_REFUSAL
    return MCPError(code=code, message=to_agent_message(exc))


def build_server(
    session_provider: SessionProvider,
    actor_provider: ActorProvider,
    storage: DocumentStorage | None = None,
    settings: Settings | None = None,
    *,
    middleware: Sequence[ServerMiddleware[Any]] | None = None,
    space: str | None = None,
) -> MCPServer:
    # One session per logical call (Task 4A-1, residual R1). `_guard` opens the
    # scope via `session_provider.scope()` when the provider exposes one --
    # `ScopedSessionProvider`, used in production (`__main__.py`) -- and
    # `McpContext.session` then resolves to the one session bound to it, however
    # many times a tool or a resource render reads the property
    # (`resources/entities.py`'s renders read it up to four times). A plain
    # callable provider (a test passing `lambda: session`, as this package's own
    # `server` fixture still does) has no `.scope` attribute, so it falls back to a
    # null scope and keeps the old, single-shared-session behaviour that fixture
    # relies on. The measurement this replaces: 10 concurrent writes against one
    # shared Session produced 0 successes and 0 rows.
    #
    # `storage` is an explicit, optional parameter -- not always resolved
    # internally from `get_settings()` -- for the same reason `session_provider`/
    # `actor_provider` are already constructor-injected rather than imported: a
    # test builds its own isolated backend (a tmp-dir-backed `LocalFileStorage`)
    # instead of a document/version tool call writing real files into this
    # repository's own working tree under the default `./var/documents` root.
    # `__main__.py` never passes one, so production still gets exactly one
    # `storage_from_settings(...)` per process, same as the API -- `build_server` is
    # called once, at start-up, and the storage it builds is the one every tool call
    # then shares (which is what keeps a Drive backend's access token cached across
    # calls instead of re-authenticating on each).
    #
    # `settings` is optional for the same reason and resolved the same way. It decides
    # one thing only: whether the Gmail tools are registered at all. A test that wants
    # them passes a configured `Settings`; every other caller, `__main__.py` included,
    # gets the process's own -- and an installation with no Google client therefore has
    # no Gmail surface rather than a broken one (spec 5.3).
    resolved_settings = settings or get_settings()
    # `resolved_settings`, not a second `get_settings()`: an installation is something a
    # caller declares once. A test that says "storage_backend is gdrive" and lets this
    # build the storage was, until this line, silently given the *process's* backend
    # instead -- and production is unaffected either way, since `__main__.py` passes
    # neither argument.
    #
    # `session_factory` is what the titolare's-own-Drive backend needs and no other
    # backend does: the account and the folder it writes into live in a row, so the
    # storage has to be able to open a session at each operation, long after this
    # function returned. `new_session` opens a fresh one the storage owns and closes
    # (see `ScopedSessionProvider.new_session`); a plain callable provider does not have
    # it, and `storage_from_settings` then refuses that configuration by name rather
    # than closing the session a tool call is running in.
    #
    # `middleware` is the SDK's context-tier hook, used by the HTTP transport to bind
    # the request's actor (`actor_scope.py`); stdio passes none.
    context = McpContext(
        session_provider,
        actor_provider,
        storage
        or storage_from_settings(
            resolved_settings, session_factory=getattr(session_provider, "new_session", None)
        ),
    )
    mcp = MCPServer("PigroCRM", instructions=INSTRUCTIONS, middleware=middleware)

    # Resolved once, here, rather than per call (Task 4A-1). `ScopedSessionProvider`
    # (production, via `__main__.py`) exposes `.scope()`; a plain callable provider
    # (a test passing `lambda: session`, as this package's own `server` fixture
    # still does) does not, and falls back to `nullcontext()` -- the old behaviour,
    # one session shared for the whole lifetime of whatever holds the provider.
    _scope: Callable[[], AbstractContextManager[Any]] | None = getattr(
        session_provider, "scope", None
    )

    def _session_scope() -> AbstractContextManager[Any]:
        return _scope() if _scope is not None else nullcontext()

    def _guarded[T: Callable[..., Any]](fn: T, translate: Callable[[DomainError], Exception]) -> T:
        """Every tool, resource and prompt renders a domain error as guidance instead
        of leaking a stack trace or a bare status code -- and, whatever else it does,
        always leaves `context.session` usable for the *next* call.

        One body, two translations, chosen by the surface and not by the tool:
        `_guard` below hands tools `_as_tool_error`, and `_protocol_guard` hands
        resources and prompts `_as_protocol_error`, because on mcp 2.2 a tool reports a
        refusal as a result and the other two as a JSON-RPC error (both docstrings cite
        the lines). Everything else in here is the same for all three.

        A nested function, not a module-level one: it needs `context` in scope to
        roll back its session, and `context` only exists once `build_server` has
        constructed it. `register_entity_tools(mcp, context, _guard)` below passes
        this closure on, exactly as it passed the old module-level function.

        `functools.wraps` is load-bearing, not cosmetic. `mcp.tool()`/`mcp.resource()`
        infer a JSON Schema from `inspect.signature(fn)`, and that inspection follows
        `__wrapped__` by default — confirmed directly against the installed SDK, where
        a wrapper without it produced two *required* "args"/"kwargs" fields instead of,
        say, `entity_type`, because the tool manager saw this wrapper's own bare
        `(*args, **kwargs)` signature instead of the guarded function's real one.

        `except ValueError` (which also catches `pydantic.ValidationError`, a subclass)
        is what keeps an argument-conversion failure — `uuid.UUID(bad_string)` inside a
        tool, or constructing one of `pigrocrm.core`'s own Create/Update/ListQuery
        schemas from caller-supplied data — from reaching the client as raw, English,
        link-carrying text instead of the same rendered guidance a hand-raised
        `DomainError` gets. `to_domain_error` does the translation; see its own
        docstring for why the two sources need different handling despite both
        arriving here as `ValueError`. This must stay a single guard fixing both,
        not a per-tool try/except: a fix that only covered today's tools would not
        cover the next one.

        `with _session_scope():` wraps the whole call (Task 4A-1, residual R1): with
        a real `ScopedSessionProvider`, this opens one `Session` for this logical
        call and closes it on the way out whatever happened, so a raw DBAPI failure
        poisoning a transaction can no longer follow the next unrelated call the way
        a single, process-lifetime session used to let it. `context.session.
        rollback()` stays in every arm regardless, because `_session_scope()` is a
        no-op `nullcontext()` when `session_provider` is a plain callable — this
        package's own `server` fixture builds one that way, sharing one `Session`
        across every tool call in a test the way `__main__.py` used to for the whole
        process — and that shared-session path still needs the explicit rollback:
        without it, an exception this guard did not already know how to translate (a
        raw DBAPI failure, most realistically) leaves that shared session's
        transaction failed, and every later call on it — including an unrelated read
        like `describe_schema` — fails too (`sqlalchemy.exc.PendingRollbackError` in
        production terms, or its underlying `psycopg.errors.InFailedSqlTransaction`
        under the savepoint-based sessions this project's tests use), forever, until
        the process (or, in a test, the fixture) is torn down. Calling `rollback()`
        on a session `_session_scope()` is about to close anyway (the
        `ScopedSessionProvider` path) is harmless — `Session.close()` already
        discards any open transaction — so one guard body serves both providers
        without branching on which kind it received. The trailing
        `except Exception: ...; raise` re-raises the original exception completely
        unchanged. It must not also translate a `KeyError`/`AttributeError` through
        `translate`, the same "guard must not be too wide" property Task 17
        verified about the two narrower `except` clauses above it; it exists only
        to guarantee the rollback runs for literally anything that can come out of
        `fn`, not to add another translated error shape. What the assistant then reads
        is the SDK's own crash sentence, «Error executing tool <name>» or «Error
        rendering prompt <name>», and since mcp 2.2 never the exception's text (REB-451).
        """
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with _session_scope():
                    try:
                        return await fn(*args, **kwargs)
                    except DomainError as exc:
                        context.session.rollback()
                        raise translate(exc) from exc
                    except ValueError as exc:
                        context.session.rollback()
                        raise translate(to_domain_error(exc)) from exc
                    except Exception:
                        context.session.rollback()
                        raise

            return cast(T, async_wrapper)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with _session_scope():
                try:
                    return fn(*args, **kwargs)
                except DomainError as exc:
                    context.session.rollback()
                    raise translate(exc) from exc
                except ValueError as exc:
                    context.session.rollback()
                    raise translate(to_domain_error(exc)) from exc
                except Exception:
                    context.session.rollback()
                    raise

        return cast(T, wrapper)

    def _guard[T: Callable[..., Any]](fn: T) -> T:
        """The guard for a tool: a domain error leaves as a `ToolError`."""
        return _guarded(fn, _as_tool_error)

    def _protocol_guard[T: Callable[..., Any]](fn: T) -> T:
        """The guard for a resource or a prompt: a domain error leaves as an `MCPError`."""
        return _guarded(fn, _as_protocol_error)

    @mcp.tool()
    @_guard
    def describe_schema(entity_type: EntityType) -> dict[str, Any]:
        """Campi nativi e personalizzati attualmente definiti per un'entità.

        Legge dal database a ogni chiamata: usalo prima di creare o aggiornare.
        """
        return entity_schema(context, entity_type)

    @mcp.tool()
    @_guard
    async def refresh_schema(ctx: Context) -> dict[str, int]:
        """Ricarica i campi personalizzati e riporta quanti sono definiti per entità.

        Dopo aver chiamato questo strumento, richiama `describe_schema` per vedere
        i campi aggiornati: a seconda della versione di protocollo negoziata con
        questo client, potresti non ricevere alcun avviso automatico di
        cambiamento. Non aspettare una notifica che potrebbe non arrivare mai.
        """
        counts: dict[str, int] = {
            entity: len(entity_schema(context, entity)["custom_fields"]) for entity in ENTITY_TYPES
        }
        # Best-effort, kept because it is correct and harmless -- not because it
        # reliably reaches the client. Verified against the installed SDK
        # (mcp==2.0.0), not assumed: with the default connection mode (`Client(
        # server)`, `mode="auto"`), this negotiates the modern 2026-07-28
        # protocol, under which `notifications/tools/list_changed` is delivered
        # only to a client that opened a `subscriptions/listen` stream —
        # `mcp.server.lowlevel.server.Server.get_capabilities`'s own docstring
        # says so, and a live reproduction confirmed it: a `Client(server)`
        # connection with a registered `message_handler` received zero messages
        # after this call, even though the server advertises
        # `tools.listChanged=True` for that same connection. Forcing the classic
        # handshake protocol instead (`Client(server, mode="legacy")`, which
        # negotiates 2025-11-25) delivered a `ToolListChangedNotification`
        # immediately, despite that connection advertising `listChanged=False`.
        # See `test_refresh_schema_notification_is_a_documented_sdk_limitation`
        # for the reproduction this comment is based on. Re-verified on mcp==2.2.0
        # (REB-451): that test still passes, and the capabilities are still
        # `listChanged=True` on the modern connection and `False` on the legacy one.
        # Re-verify both directions the next time `mcp` is upgraded. Either the
        # modern protocol's listen-stream requirement, or this SDK's capability
        # advertisement for it, may have changed.
        await ctx.session.send_tool_list_changed()
        return counts

    @mcp.resource("customer://{customer_id}")
    @_protocol_guard
    def customer_resource(customer_id: str) -> str:
        """Scheda completa di un cliente: dati fiscali, contatti, deal e timeline."""
        return entities.render_customer(context, UUID(customer_id))

    @mcp.resource("person://{person_id}")
    @_protocol_guard
    def person_resource(person_id: str) -> str:
        """Scheda completa di una persona."""
        return entities.render_person(context, UUID(person_id))

    @mcp.resource("deal://{deal_id}")
    @_protocol_guard
    def deal_resource(deal_id: str) -> str:
        """Scheda completa di un deal, incluso stato di pipeline e timeline."""
        return entities.render_deal(context, UUID(deal_id))

    from pigrocrm_mcp.prompts import register_prompts
    from pigrocrm_mcp.tools import register_entity_tools

    register_entity_tools(mcp, context, _guard)
    # §10's four prompts, guarded by the same body the tools get and only a different
    # translation at the end (`_protocol_guard`: a prompt's refusal has to be an `MCPError`
    # to reach the client with its text on mcp 2.2). A prompt reads the
    # database exactly as a tool does, so it inherits `_session_scope()` and the rollback
    # with it -- there is no second session story for prompts, and there must not be: three
    # of the four open a `REPEATABLE READ` snapshot through `DashboardService`, which is
    # possible only on a session nothing has touched. `ScopedSessionProvider` gives them
    # one; a caller wiring `lambda: shared_session` gets the service's loud `RuntimeError`
    # rather than a briefing whose figures were true at no single instant.
    register_prompts(mcp, context, _protocol_guard)

    if gmail_configured(resolved_settings):
        # Conditional, and this is the whole of "absent, not broken": not registered
        # means not listed and not callable. An installation that self-hosts precisely
        # in order not to have Google does not get three tools that answer 409.
        from pigrocrm_mcp.tools import gmail as gmail_tools

        gmail_tools.register(mcp, context, _guard, resolved_settings)

        # Same block, same reason: one Google OAuth client issues both the Gmail grant
        # and the Drive one, so whether Drive diagnosis is available is the same fact as
        # whether Gmail is. `describe_drive_account` costs no quota and exercises no
        # consent -- it is not privileged for the same reason `describe_gmail_account`
        # is not (see `tools/drive.py`'s docstring).
        from pigrocrm_mcp.tools import drive as drive_tools

        drive_tools.register(mcp, context, _guard, resolved_settings)

    if resolved_settings.mcp_full_access:
        # The other half of the switch. `Actor.full_access` (stamped in
        # `PatService.resolve`) decides whether the *service* says yes; this decides
        # whether there is a door at all. Both read the same setting, and
        # `test_mcp_invoice_ban.py` fails if they disagree -- registered tools that all
        # refuse, or capabilities with no way to reach them, are both worse than either
        # honest state.
        #
        # Conditional for the same reason Gmail is: not registered means not listed and
        # not callable. An installation that has not opted in does not get tools answering
        # «vietato», it gets a surface on which they do not exist. The module
        # reads the settings once more for the one tool that also needs a mailbox
        # (`discover_gmail_correspondents`), which is absent without Google exactly as
        # `tools/gmail.py` is.
        from pigrocrm_mcp.tools import privileged as privileged_tools

        privileged_tools.register(mcp, context, _guard, resolved_settings)

        if gmail_configured(resolved_settings):
            # Slice 9C §4.2's three Drive reads, and the only privileged module whose
            # *whole* content needs Google as well as the switch: reading a folder or a
            # file spends the titolare's Drive quota under their OAuth consent, and
            # without a Google client there is no credential to spend it with. So the
            # second condition is expressed here, in the import, rather than as an `if`
            # inside `register` -- unlike `privileged.py`, which is registered by the
            # switch alone and re-reads the settings for its one Gmail tool.
            #
            # Both privileged modules are named in `test_mcp_invoice_ban.py`'s
            # `PRIVILEGED_MODULES`, and that file fails if either is reachable outside
            # this block: the exemption its source scans grant is enumerated in one
            # place and checked, not left to a comment.
            from pigrocrm_mcp.tools import drive_privileged as drive_privileged_tools

            drive_privileged_tools.register(mcp, context, _guard, resolved_settings)
    # Last, once every tool and resource is registered, so the wrapper sees them all.
    # A no-op without `PIGROCRM_POSTHOG_KEY` (see `analytics.py`), which is what every
    # test and every self-hosted installation without a key gets. `space` is the slug
    # the HTTP transport builds this server for; the stdio process leaves it `None` and
    # the group is the installation's own.
    analytics.install(mcp, resolved_settings, actor_provider, space=space)
    return mcp
