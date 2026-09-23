"""An agent must not be able to issue a fiscal document, nor touch the handful of
slice-4 operations that are closer to configuration or to rewriting the past than to
recording an entity.

The guarantee is *structural*: the tool is not registered. It is deliberately not a
permission check, because a personal access token inherits its owner's full role and
never expires (residuo R10) -- so a check inside a registered tool is a check an
administrator's token passes, and "give a token to Claude" would mean "let Claude issue
invoices in your name", or recalculate what a quarter's work was worth.

A comment saying so protects nothing. These tests read every module under `tools/` and
fail if any forbidden operation ever appears as a tool, or is called under another name,
so the guarantee survives someone adding one without reading the comment. Slice 4 and
slice 6 extend the same two lists rather than writing a parallel mechanism -- which is
why `FORBIDDEN`/`FORBIDDEN_SERVICE_CALLS` are module-level constants with one name per
line rather than inline literals, and why the source scan below covers every file in
`tools/`, not only `tools/__init__.py`: slice 4's tool call-throughs live in their own
module (`tools/timetracking.py`, mirroring `tools/invoices.py`), and a registered tool
that called a same-named wrapper in that module which itself called a forbidden service
method would leave no trace in `tools/__init__.py` alone.
"""

import ast
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS_DIR = Path(__file__).resolve().parents[1] / "src" / "pigrocrm_mcp" / "tools"

# The modules the scans below skip, and the only ones allowed to reach a forbidden
# operation. Their whole existence is conditional: `server.py` imports and registers each
# only when `Settings.mcp_full_access` is true, so on a default installation their calls
# are as unreachable as if the files were not there. The scans therefore ask a narrower
# and truer question than "does this call appear anywhere" -- they ask whether it appears
# anywhere that runs unconditionally.
#
# Two files and not one since slice 9C. `drive_privileged.py` holds the three Drive reads
# and needs a *second* condition on top of the switch -- a configured Google client, since
# there is no credential to read Drive with otherwise -- and expressing that as a separate
# module makes the condition visible in `server.py`'s import instead of as an `if` nested
# inside a `register` that anybody could widen. The exemption stays enumerated here, in
# one tuple, rather than becoming "any file whose name contains privileged".
#
# `test_the_privileged_module_is_the_only_place_they_appear` closes the obvious hole in
# that exemption: a third file quietly added to the skip list, or either of these two
# imported outside the guard, all fail there.
PRIVILEGED_MODULES = (TOOLS_DIR / "privileged.py", TOOLS_DIR / "drive_privileged.py")

# The banned operations this package does have a tool for -- the count is deliberately
# not written down here, because it has already gone stale twice: the fiscal ones, which
# turn a draft into a fiscal fact or change what one says after the fact, then the ones
# closer to configuration or to rewriting the past than to recording an entity, then the
# groups the comments below introduce. Each is reachable over REST by a
# human with the right role; none is reachable by an agent at all. `bind_time_to_invoice`
# and `get_fiscal_estimate` belong to `AnalyticsService`, which does not exist until plan
# 4B -- declared here regardless, because the ban is a decision made now and plan 4B must
# not have to touch this list to honour it.
#
# Two of them were added after an audit found the repo stating this policy twice
# and the two statements disagreeing:
#
#   * `update_fiscal_profile` was one of the *four* names slice 3 §11 actually banned
#     (`issue_invoice`, `annul_invoice`, `mark_transmitted_externally`,
#     `update_fiscal_profile`); this list had dropped it and put `export_invoice_xml` --
#     a real, correct addition -- in its place, while `tools/invoices.py` went on
#     declaring it forbidden in a table nothing imported. The audit restored it. It left
#     again with ORB-188 (2026-09-12), this time on purpose and in both halves at once
#     (`AGENT_FORBIDDEN_ACTIONS` and here): the profile is one rewritable row, an
#     issued invoice keeps its own copy of it, and in a space born empty setting it is
#     the first thing a person asks their assistant. It is on the default surface with
#     the emitter's write, admin-only through the service.
#   * `unarchive_cost_category` is `archive_cost_category` in the other direction, and
#     the same decision about which categories the CRM offers. Its absence from slice 4
#     §11's list of ten is an omission rather than a distinction -- there is no reading
#     under which creating, renaming and archiving a category are configuration and
#     bringing one back is not.
FORBIDDEN = (
    "issue_invoice",
    "annul_invoice",
    "mark_invoice_transmitted",
    "export_invoice_xml",
    "recalculate_rates",
    "update_user_rates",
    "update_deal_rate",
    "create_cost_category",
    "update_cost_category",
    "archive_cost_category",
    "unarchive_cost_category",
    "close_period",
    "reopen_period",
    "bind_time_to_invoice",
    "get_fiscal_estimate",
    # The first that is not fiscal: asking Gmail who at a customer's
    # domain the owner has corresponded with. Not irreversible -- it stores nothing --
    # but it spends the owner's Gmail quota under the owner's OAuth consent, which is
    # the exact reason `sync` and `backfill` are refused to agents outright. It is on
    # this list rather than on `FORBIDDEN_GMAIL` because, unlike those two, the
    # installation *can* opt in: the same switch that hands an agent the fiscal acts.
    "discover_gmail_correspondents",
    # Nemmeno questa e' fiscale: il testo di un allegato di una mail archiviata. Di un
    # allegato il CRM conserva nome, tipo e peso e mai i byte (spec 5.4), quindi lo
    # strumento va a prenderlo da Google al momento -- quota e consenso del titolare,
    # contenuto scritto da un mittente esterno -- e non archivia niente. Stessa porta di
    # `discover_gmail_correspondents`, per la stessa ragione.
    "read_gmail_attachment",
    # Fiscal again: writing a numbered, issued
    # row straight into the register (slice 9 §3), and declaring the numbers it will
    # never carry (slice 9 §3.2).
    "import_issued_invoice",
    "declare_invoice_register_gaps",
    # REB-365: reads a document's own stored bytes back and reports what would happen
    # to it, gated the same way even though it writes nothing itself -- it exposes the
    # same incoming-supplier and register-conflict facts as the two writes above.
    "review_invoice_import",
    # None of these three fiscal: reading
    # the titolare's Google Drive (slice 9 §4.2). Like `discover_gmail_correspondents`
    # they spend the titolare's quota under the titolare's OAuth consent, and unlike
    # every other entry here what they *return* is the content of a personal Drive --
    # where the folder of another job, the rent contract and the photos of somebody's
    # children live next to the client's contract. The confinement to
    # `root_folder_ids` (see `core/drive/reader.py`) is what makes them offerable at
    # all; this list is what says the installation has to ask for them.
    "list_drive_files",
    "read_drive_file",
    "import_drive_file",
)

# The other half of `AGENT_FORBIDDEN_ACTIONS`: operations that are banned to an agent
# credential and have **no MCP tool at all** -- not "not registered by default", but no
# tool anywhere in `apps/mcp`, now or planned.
#
# They exist because the ban list is keyed on the operation rather than on a transport,
# so it can hold an operation this package never offers. All three are Drive
# *configuration* (spec 9 §5.2): naming the root folders the reader may see and the
# folder the document storage writes into (`account.py`'s `_ROOTS_ACTION`), and
# connecting or disconnecting the grant itself (`oauth.py`'s
# `_CONNECT_ACTION`/`_DISCONNECT_ACTION`). A settings panel is the only caller; there is
# nothing here for an agent to be handed even with the switch on.
#
# The reason they are banned anyway is the reason this file's own subject exists: a
# personal access token is accepted on every REST route, and `PATCH
# /api/drive/account/roots` is a REST route. Without the ban an agent token could
# repoint the roots at any folder of the titolare's Drive -- making every confinement
# `core/drive/reader.py` enforces true of a folder list the agent chose -- or disconnect
# the account and take the CRM's own document store offline. `apps/api/tests/
# test_drive_api.py` pins all three at the route.
#
# Enumerated here, and this is why the equality above is a union rather than a plain
# `==`: the two lists still have to be one list, but "in `AGENT_FORBIDDEN_ACTIONS` and
# not in `FORBIDDEN`" now has exactly three admissible answers, written down with their
# justification instead of left as slack in the assertion. A fourth added to the ban
# list without a tool -- or one of these three growing a tool and staying here -- fails.
#
# Spelled as the action strings the services pass to `require_write`, which for a
# settings operation are Italian sentences and not tool names: the string *is* the
# identity of the operation for `_refuse_if_agent`, so a copy re-spelled prettily here
# would be a ban that silently stopped matching.
REST_ONLY_FORBIDDEN = frozenset(
    {
        "impostare le cartelle Drive",
        "collegare Google Drive",
        "scollegare Google Drive",
    }
)

# The tools above that exist only on an installation where Google is configured as well:
# on one without it there is no mailbox to ask and no credential to read Drive with, so
# the switch alone does not make them appear.
# `test_they_are_registered_exactly_when_the_installation_opted_in` accounts for them
# by building both kinds of installation.
FORBIDDEN_NEEDING_GMAIL = frozenset(
    {
        "discover_gmail_correspondents",
        "read_gmail_attachment",
        "list_drive_files",
        "read_drive_file",
        "import_drive_file",
    }
)

# The service methods behind them. Listed separately because a future tool could call one
# under an innocuous name -- `finalise_invoice` registering a tool that calls `issue`
# would pass a name check and defeat the point. A ban a bare name cannot express goes
# in `FORBIDDEN_QUALIFIED_CALLS` below instead.
FORBIDDEN_SERVICE_CALLS = (
    "issue",
    "annul",
    "mark_transmitted_externally",
    "export_xml",
    "recalculate_rates",
    "update_user_rates",
    "update_deal_rate",
    "create_cost_category",
    "update_cost_category",
    "archive_cost_category",
    "unarchive_cost_category",
    "close_period",
    "reopen_period",
    "bind_time_to_invoice",
    "get_fiscal_estimate",
    "discover",
    # `GmailAttachmentService.attachment_text`: scarica da Gmail, al momento, il file che
    # la sincronizzazione non ha mai salvato (spec 5.4 conserva nome, tipo e peso e
    # nient'altro). Stessa famiglia di `discover` -- quota e consenso del titolare, byte
    # scritti da un mittente esterno -- quindi stessa porta: esiste solo dove
    # l'installazione ha aperto `mcp_full_access`. Il nome e' unico in questo codice, ed
    # e' unico di proposito: vedi `documents/service.py::extract_text`.
    "attachment_text",
    "import_issued",
    "declare_gaps",
    "review_import",
    # `DriveReader`'s three reads and the two calls the Drive tools make around them
    # (slice 9 §4.2). Not a `*Service` receiver, which is why they are here as bare
    # names and why `test_mcp_surface_coverage.py` cannot carry them in `_VIETATE`
    # (its taxonomy sweeps classes whose name ends in `Service`, and `DriveReader` is
    # not one) -- see `_FUORI_DAL_SETACCIO` there, which records the reason for each.
    #
    # Each name is unique in this codebase, which is what makes the substring scan the
    # right instrument: no service has a `list_children`, a `read_text` or a
    # `describe_roots`, and `import_bytes` belongs to `DocumentService` alone.
    #
    # `DriveReader.describe` is deliberately **absent**, and its absence is the `upsert`
    # problem in a form this file's two instruments cannot solve: `TemplateService.
    # describe` and `FiscalProfileService.describe` are both on the MCP surface, so a
    # bare-name ban would fail the build with a message about Drive, and the qualified
    # scan cannot help either -- `_receivers_of` recognises a service by the `Service`
    # suffix, and `DriveReader` has none. What covers it instead is stronger than a name:
    # `test_no_unconditional_module_can_even_obtain_a_drive_reader` bans the only
    # constructor, so an unconditional module has nothing to call `describe` *on*.
    "list_children",
    "read_text",
    "read_bytes",
    "describe_roots",
    "import_bytes",
)

# The bans a bare method name cannot express, because the name is not the operation.
# Empty since ORB-188, and kept with its instrument (`_receivers_of`) because the lesson
# that filled it still holds: its one entry was `("FiscalProfileService", "upsert")`,
# and `upsert` is exactly the kind of name a second service carries too --
# `EmitterProfileService.upsert` -- so putting "upsert" in the tuple above would have
# banned the substring, and any call spelled that way, on any service, would have failed
# this file with a message about the fiscal profile. A ban on a name is not a ban on an
# operation, and the bare names above are safe only because each happens to be unique.
# The next qualified ban goes here as a `(service, method)` pair and is resolved to its
# receiver; the tests below iterate the tuple so that an empty one asserts nothing
# rather than parametrising into a skip.
FORBIDDEN_QUALIFIED_CALLS: tuple[tuple[str, str], ...] = ()

_REASON = (
    "e' un'operazione esclusa dalla superficie MCP per costruzione (slice 3 §11 per "
    "gli atti fiscali, slice 4 §11 per le tariffe, le "
    "categorie di costo e le chiusure di periodo), non per controllo di permessi: un "
    "PAT eredita il ruolo pieno del proprietario e non scade (residuo R10), quindi "
    "l'assenza del tool e' l'unico meccanismo che regge."
)


# Gmail (slice 5B). Nomi di strumenti, non metodi di servizio, e per due ragioni
# diverse fra loro.
#
# I primi due non hanno ancora un metodo dietro: l'invio arriva in 5B-2, e il divieto e'
# una decisione presa adesso -- come per `bind_time_to_invoice`, dichiarato qui prima
# che `AnalyticsService` esistesse. Un'email partita dalla tua casella non si richiama e
# il cliente la legge come parole tue; e un agente che tiene la *lettura* della posta e
# l'*invio* sulla stessa cintura ha la sorgente di injection e il canale di
# esfiltrazione sullo stesso canale. Il corpo di un'email che arriva a un agente e'
# testo scritto da qualcun altro: quello che impedisce che sia un buco e' che non ci sia
# niente con cui mandare fuori qualcosa. La persona preme Invia.
#
# Gli altri quattro hanno un metodo dietro, gia' escluso in `test_mcp_surface_coverage.
# py`, e sono ripetuti qui come *nomi* perche' le due liste rispondono a domande
# diverse: quella e' "questo metodo e' raggiungibile", questa e' "questo strumento
# esiste". Uno strumento chiamato `sync_gmail` che chiamasse qualcosa d'altro passerebbe
# la prima e fallirebbe questa.
#
# Non c'e' `FORBIDDEN_GMAIL_SERVICE_CALLS`: i metodi corrispondenti sono gia' coperti
# dal divieto per costruzione dell'altro file, e aggiungerli qui romperebbe l'uguaglianza
# fra `_VIETATE` e queste tuple che `test_the_forbidden_block_names_exactly_what_the_
# ban_test_bans` verifica -- due liste che si ripetono divergono, ed e' esattamente il
# difetto che quel test esiste per prendere.
FORBIDDEN_GMAIL = (
    "send_email",
    "send_payment_reminder",
    "sync_gmail",
    "backfill_gmail",
    "connect_google_account",
    "disconnect_google_account",
    "set_gmail_settings",
    "search_gmail",
)

# Il substring che nessun nome di strumento puo' contenere. Piu' forte dell'elenco
# sopra, che sa solo le grafie a cui qualcuno ha pensato: la garanzia e' che nessuno
# strumento invii, non che quattro nomi particolari siano assenti.
FORBIDDEN_TOOL_NAME_FRAGMENTS = ("send", "invia")

_REASON_GMAIL = (
    "e' escluso dalla superficie MCP di slice 5B per costruzione. La riga non e' "
    "lettura contro scrittura ma di chi e' la risorsa e di chi la decisione: l'agente "
    "legge la copia gia' archiviata in `gmail_messages` e lo stato della credenziale, "
    "mentre andare a prendere la posta (quota e consenso della persona), collegare o "
    "scollegare una casella, decidere se il CRM conserva i corpi, e inviare, "
    "appartengono alla persona. Come per gli atti fiscali, non e' un controllo di "
    "permessi: un PAT eredita il ruolo pieno del proprietario e non scade (residuo "
    "R10), quindi l'assenza del tool e' l'unico meccanismo che regge."
)


def _unconditional_paths(base: Path) -> list[Path]:
    """Every `.py` file under `base` that runs on a default installation, `prompts/`
    included when `base` is the real `tools/`.

    One helper for the three scans below (`_modules`, `_registered_tool_names`,
    `_tools_source`) so that widening the sweep is one edit rather than three -- three
    was how `prompts/` came to be missing from two of them while `server.py` registered
    it unconditionally.

    The `base == TOOLS_DIR` guard is for the scans' own tests, which point `base` at a
    `tmp_path`: a temporary directory has no `prompts/` sibling to find.
    """
    paths = list(base.rglob("*.py"))
    if base == TOOLS_DIR:
        paths += list((TOOLS_DIR.parent / "prompts").rglob("*.py"))
    return sorted(path for path in paths if path not in PRIVILEGED_MODULES)


def _modules(base: Path = TOOLS_DIR) -> list[ast.Module]:
    return [
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in _unconditional_paths(base)
    ]


def _registered_tool_names(base: Path = TOOLS_DIR) -> set[str]:
    """Every function carrying an `@mcp.tool()` decorator, at any nesting depth, in any
    module under `tools/`.

    Walks the whole tree rather than the top level: the registrations live inside
    `build_server`'s body and inside each `register(...)` function, so a top-level-only
    scan would report nothing and pass vacuously -- which is the failure mode this file
    exists to avoid in the code it guards.

    It used to be scoped to `tools/__init__.py`, on the true-at-the-time observation
    that every `@mcp.tool()` decorator lived there. Slice 5B's `tools/gmail.py` carries
    its own, and that is exactly the shape a future forbidden tool would take: a new
    domain module registering its own tools, invisible to a scan of one file. A ban that
    only reads the file the last author happened to use is not a ban.
    """
    names: set[str] = set()
    for module in _modules(base):
        for node in ast.walk(module):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Attribute) and target.attr == "tool":
                    names.add(node.name)
    return names


def _tools_source(base: Path = TOOLS_DIR) -> str:
    """Every `.py` file under `tools/` **and under `prompts/`**, concatenated.

    Whole-package, like `_registered_tool_names` above and for a second reason of its
    own: the actual call into a service lives one file over, in the
    domain-specific module a registered tool calls through (`tools/invoices.py`,
    `tools/timetracking.py`, ...). A forbidden method reached only from there --
    directly, or through a same-named wrapper function called under a different tool
    name -- must fail exactly as if it were called inline in `tools/__init__.py`
    itself; scanning the whole package is what makes that true regardless of which
    file the call physically sits in.

    `prompts/` is in for the same reason and was missing: `server.py` calls
    `register_prompts` **unconditionally**, so §10's four prompts are as reachable on a
    default installation as any tool in `tools/__init__.py`, and every ban below read
    right past them. A prompt is a function with a `context` -- it can call
    `InvoiceService.issue` exactly as a tool can -- and «un prompt» is a plausible place
    for somebody to put a convenience that ends in a fiscal write, precisely because it
    does not look like a tool.

    Which files those are is `_unconditional_paths`' answer, shared with the two scans
    above so the sweep is widened in one place.
    """
    return "\n".join(path.read_text(encoding="utf-8") for path in _unconditional_paths(base))


def _receivers_of(method: str, base: Path = TOOLS_DIR) -> list[str | None]:
    """For every call spelled `.method(` anywhere under `base`, the service class it
    lands on -- or `None` where this cannot tell.

    Exists for `FORBIDDEN_QUALIFIED_CALLS`, and only for it: the substring scan below is
    the right instrument for a method name that belongs to exactly one service, and the
    wrong one for `upsert`. Resolves the three call shapes this package actually uses --
    `ServiceClass(context.session).m(...)`, `svc = ServiceClass(...)` then `svc.m(...)`,
    and the module-private factories (`_invoices(context) -> InvoiceService`) that
    `tools/invoices.py` and `tools/documents.py` use -- recognising a service by this
    codebase's own naming rule, a class whose name ends in `Service`, so that this file
    stays readable without importing anything from `packages/core`.

    An unresolved receiver is reported as `None` and counted as an offence by the caller.
    That is the whole point of returning `None` rather than dropping the call: "we could
    not work out whose `upsert` this is" must not read as "it is not the forbidden one".
    A ban that fails open is not a ban.
    """
    receivers: list[str | None] = []
    for path in sorted(base.rglob("*.py")):
        if path in PRIVILEGED_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        factories: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                returns = node.returns
                if isinstance(returns, ast.Name) and returns.id.endswith("Service"):
                    factories[node.name] = returns.id

        def produced(func: ast.expr, factories: dict[str, str] = factories) -> str | None:
            if not isinstance(func, ast.Name):
                return None
            return func.id if func.id.endswith("Service") else factories.get(func.id)

        bindings: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                owner = produced(node.value.func)
                if owner is not None:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings[target.id] = owner

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != method:
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Call):
                receivers.append(produced(receiver.func))
            elif isinstance(receiver, ast.Name):
                receivers.append(bindings.get(receiver.id))
            else:
                receivers.append(None)
    return receivers


def test_the_ast_walk_actually_finds_the_registered_tools() -> None:
    """Guards the guard. If `_registered_tool_names` returned an empty set -- because the
    decorator spelling changed, or because the registrations moved -- every ban test
    below would pass while checking nothing. Anchored on tools that exist today and on a
    plausible count, so a silent break is a failure and not a green run."""
    names = _registered_tool_names()
    assert "list_invoices" in names
    assert "get_invoice" in names
    assert "create_customer" in names
    assert len(names) > 20


def test_the_source_scan_actually_reads_more_than_one_file() -> None:
    """Guards the *other* guard. If `_tools_source` only ever returned
    `tools/__init__.py`'s own text -- because `rglob` found nothing, or `TOOLS_DIR` was
    pointed at the wrong directory -- `test_no_registered_tool_calls_a_fiscal_write_
    under_another_name` would silently stop seeing `tools/invoices.py` and
    `tools/timetracking.py` at all, which is exactly the gap the whole-package scan
    exists to close."""
    source = _tools_source()
    assert "def search(" in source  # tools/invoices.py
    assert "def log_time(" in source  # tools/timetracking.py
    # And `prompts/`, which `server.py` registers unconditionally and which this sweep
    # read right past until the 9C final review. Anchored on two of the four so that a
    # sweep silently narrowing back to `tools/` fails here instead of leaving every ban
    # below green over a directory it no longer looks at.
    assert "def stato_cliente(" in source  # prompts/customer.py
    assert "def chiusura_mese(" in source  # prompts/dashboards.py


def test_the_registration_scan_reads_every_module_and_not_only_the_first() -> None:
    """Guards the widening. `tools/gmail.py` is the first module outside
    `tools/__init__.py` to register tools of its own, and it is the shape every future
    domain module will take. If the scan silently narrowed back to one file, the Gmail
    bans below would pass while checking a file that contains none of them."""
    names = _registered_tool_names()
    assert "list_gmail_messages" in names  # tools/gmail.py
    assert "create_customer" in names  # tools/__init__.py


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_no_tool_is_registered_for_a_forbidden_operation(forbidden: str) -> None:
    assert forbidden not in _registered_tool_names(), (
        f"'{forbidden}' e' registrato come tool MCP, ma {_REASON}"
    )


@pytest.mark.parametrize("forbidden", FORBIDDEN_GMAIL)
def test_no_tool_is_registered_for_a_forbidden_gmail_operation(forbidden: str) -> None:
    assert forbidden not in _registered_tool_names(), (
        f"'{forbidden}' e' registrato come tool MCP, ma {_REASON_GMAIL}"
    )


@pytest.mark.parametrize("fragment", FORBIDDEN_TOOL_NAME_FRAGMENTS)
def test_no_registered_tool_name_can_even_suggest_sending(fragment: str) -> None:
    """The stronger form of the two send bans above. A list of names knows only the
    spellings somebody thought of; this knows that nothing on this surface sends."""
    offenders = sorted(name for name in _registered_tool_names() if fragment in name)
    assert not offenders, f"{offenders} contengono '{fragment}', ma l'invio {_REASON_GMAIL}"


@pytest.mark.parametrize("method", FORBIDDEN_SERVICE_CALLS)
def test_no_tool_reaches_a_forbidden_operation_under_another_name(method: str) -> None:
    """The name check alone is not enough: a tool called `finalise_paperwork` that calls
    `.issue(...)`, or one called `tidy_up_rates` whose own call-through wrapper in
    `tools/timetracking.py` calls `.recalculate_rates(...)`, would both pass it. This
    looks at what the whole `tools/` package actually calls, not at any one tool's own
    name."""
    offenders_in_source = f".{method}(" in _tools_source()
    assert not offenders_in_source, (
        f"'.{method}(' compare in qualche modulo sotto tools/, ma quell'operazione "
        f"{_REASON} Il divieto vale sulla chiamata, non sul nome del tool che vi "
        "arriva: rinominare il tool o spostare la chiamata in un altro modulo non la "
        "rende ammissibile."
    )


def test_the_qualified_scan_tells_two_services_with_the_same_method_apart(tmp_path: Path) -> None:
    """Guards the guard, on the one property that makes it worth having. If
    `_receivers_of` collapsed to "some `.upsert(` exists", a qualified ban declared on
    one service's `upsert` would also ban every other service's -- `FiscalProfileService`
    and `EmitterProfileService` both have one, and both are tools today -- and the build
    would fail with a message about the wrong operation, which is how a policy stops
    being believed."""
    (tmp_path / "m.py").write_text(
        "def a(context):\n"
        "    return EmitterProfileService(context.session).upsert(data, context.actor)\n"
        "\n"
        "def b(context):\n"
        "    return FiscalProfileService(context.session).upsert(data, context.actor)\n",
        encoding="utf-8",
    )
    assert sorted(str(r) for r in _receivers_of("upsert", tmp_path)) == [
        "EmitterProfileService",
        "FiscalProfileService",
    ]


def test_the_qualified_scan_fails_closed_on_a_receiver_it_cannot_resolve(tmp_path: Path) -> None:
    """The other half, and the one that decides whether this mechanism is a ban or a
    suggestion: a call whose receiver the walk cannot attribute to a class is reported as
    unresolved, and the ban test below treats unresolved as forbidden. Anything else
    would let `some_container.fiscal.upsert(...)` through on the grounds that nobody
    could prove what it was."""
    (tmp_path / "m.py").write_text(
        "def a(context):\n    return context.services.fiscal.upsert(data)\n", encoding="utf-8"
    )
    assert _receivers_of("upsert", tmp_path) == [None]


def test_no_tool_reaches_a_forbidden_operation_on_the_service_that_owns_it() -> None:
    """The qualified half of `test_no_tool_reaches_a_forbidden_operation_under_another_
    name`. Same guarantee, expressed against the receiver instead of the bare name,
    because the bare name would either miss the operation or ban an unrelated one.
    A loop and not a parametrisation, so that the tuple being empty (as it is since
    ORB-188) asserts nothing instead of producing a skipped test."""
    for service, method in FORBIDDEN_QUALIFIED_CALLS:
        offenders = [
            owner or "un ricevitore non risolvibile"
            for owner in _receivers_of(method)
            if owner is None or owner == service
        ]
        assert not offenders, (
            f"'.{method}(' e' chiamato su {offenders} da qualche modulo sotto tools/, ma "
            f"'{service}.{method}' {_REASON} Il divieto vale su questa coppia "
            "servizio/metodo: un ricevitore che questo controllo non sa attribuire conta "
            "come violazione, perche' un divieto che si arrende all'ambiguita' non e' un "
            "divieto."
        )


def test_the_structural_ban_has_a_second_line_on_the_credential_itself() -> None:
    """The tool being absent is not, on its own, the guarantee this file claims.

    Everything above proves these operations are unreachable over *this transport*. A
    personal access token is not confined to it: `PatService.resolve` answers with
    `Actor(type="mcp", role=<the owner's role>)`, and `apps/api`'s `get_actor` accepts
    the same `Bearer pgc_...` header on every REST route. Until `AGENT_FORBIDDEN_ACTIONS`
    existed nothing in that package read `actor.type`, so a token handed to an agent on
    the written promise that it "may prepare but may not emit" could issue an invoice
    with one curl -- spending a register number and producing a FatturaPA.

    So the two lists have to stay one list. This test is the seam: `FORBIDDEN` names the
    tools that must not be registered, `AGENT_FORBIDDEN_ACTIONS` names the operations
    that must refuse an agent whatever transport it arrives on, and a name added to one
    and forgotten in the other is that gap coming back. The mapping is identity except
    for one pair, where the tool's advertised name and the action string the service
    passes to `require_admin` were chosen separately -- mapped here rather than renamed,
    because the tool name is the agent-facing contract and the action string is the
    audited one.

    `REST_ONLY_FORBIDDEN` is the union's other term, and it is what keeps this a
    statement rather than a loose fit: the ban list is keyed on the *operation*, so it
    can legitimately hold one this package offers no tool for -- Drive's three
    configuration operations, reachable only over REST and only by a person. Those three
    are enumerated there with their justification, so "banned but toolless" has three
    named answers instead of being slack in this assertion. A fourth, or one of these
    growing a tool and staying in that set, fails here.
    """
    from pigrocrm.core.actor import AGENT_FORBIDDEN_ACTIONS

    tool_name_to_action = {"mark_invoice_transmitted": "mark_transmitted_externally"}
    attesi = {tool_name_to_action.get(name, name) for name in FORBIDDEN}

    # The union would still balance if one of the three grew a tool and stayed in both
    # sets, so the disjointness is asserted on its own: `REST_ONLY_FORBIDDEN` claims
    # "no tool", and a claim nothing checks is a comment.
    assert not (REST_ONLY_FORBIDDEN & attesi), (
        "un'operazione elencata in REST_ONLY_FORBIDDEN ha ora un tool MCP: togliela da "
        f"quel set, la sua motivazione non vale piu' -- {sorted(REST_ONLY_FORBIDDEN & attesi)}"
    )
    assert attesi | REST_ONLY_FORBIDDEN == set(AGENT_FORBIDDEN_ACTIONS), (
        "le due meta' del divieto sono divergenti: FORBIDDEN vieta il tool, "
        "AGENT_FORBIDDEN_ACTIONS vieta l'operazione a qualunque credenziale di tipo "
        "agente. Un nome aggiunto a una sola delle due lascia proprio il buco che la "
        "seconda e' stata scritta per chiudere -- l'assenza del tool non impedisce la "
        "stessa chiamata via REST con lo stesso token. Un'operazione vietata che non ha "
        "nessun tool va elencata e motivata in REST_ONLY_FORBIDDEN, non lasciata come "
        "gioco in questa asserzione.\n"
        f"solo in FORBIDDEN: {sorted(attesi - set(AGENT_FORBIDDEN_ACTIONS))}\n"
        "solo in AGENT_FORBIDDEN_ACTIONS: "
        f"{sorted(set(AGENT_FORBIDDEN_ACTIONS) - attesi - REST_ONLY_FORBIDDEN)}\n"
        "in REST_ONLY_FORBIDDEN ma non vietate: "
        f"{sorted(REST_ONLY_FORBIDDEN - set(AGENT_FORBIDDEN_ACTIONS))}"
    )


async def test_they_are_registered_exactly_when_the_installation_opted_in(
    mcp_session: Session, tmp_path: Path
) -> None:
    """Both halves of the switch, in one assertion, because them disagreeing is the
    failure worth catching.

    Everything above proves the tools are absent on a default installation -- the
    guarantee for everyone who never touches the setting. This adds the other direction:
    with `mcp_full_access` on, every one of them is there.

    A half-open switch is worse than either honest state. Registered tools that all
    refuse waste an agent's turns and read as a broken product; open capabilities with no
    tool to reach them are a setting that does nothing. Worst is all but one: the
    operator believes the switch is on and the one refusal arrives at the moment
    somebody is issuing an invoice.

    This test asks the built server rather than reading the source, which is why it sits
    apart from its neighbours: registration is conditional at *runtime*, on a value no
    AST walk can see.

    `Settings(_env_file=None, ...)` and never the ambient settings -- this repository's
    own `.env` has the switch **on**, so a test that inherited it would assert the
    opposite of what it claims and pass anyway.
    """
    attore = Actor(id=None, type="mcp", role="admin")

    async def tool_names(full_access: bool, *, gmail: bool) -> set[str]:
        google = (
            {
                "google_client_id": "cid.apps.googleusercontent.com",
                "google_client_secret": "the-secret",
                "google_token_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
                "public_url": "https://crm.example.it",
            }
            if gmail
            else {}
        )
        server = build_server(
            lambda: mcp_session,
            lambda: attore,
            LocalFileStorage(str(tmp_path)),
            Settings(_env_file=None, mcp_full_access=full_access, **google),  # type: ignore[call-arg]
        )
        return {tool.name for tool in await server.list_tools()}

    # Without Google, the switch adds every forbidden tool except the ones that need a
    # mailbox to exist at all: those stay absent, not broken, exactly as `tools/gmail.py`
    # does on the same installation.
    chiusa = await tool_names(False, gmail=False)
    aperta = await tool_names(True, gmail=False)

    assert not (chiusa & set(FORBIDDEN)), "un'installazione chiusa non registra nessuno dei divieti"
    senza_google = set(FORBIDDEN) - FORBIDDEN_NEEDING_GMAIL
    mancanti = senza_google - aperta
    assert not mancanti, f"interruttore aperto ma questi tool non esistono: {sorted(mancanti)}"
    # And nothing else moved: opting in means exactly these more tools, not a different server.
    assert aperta - chiusa == senza_google

    # With Google configured as well, the switch adds all of them -- and still nothing
    # else.
    chiusa_google = await tool_names(False, gmail=True)
    aperta_google = await tool_names(True, gmail=True)

    assert not (chiusa_google & set(FORBIDDEN))
    assert aperta_google - chiusa_google == set(FORBIDDEN)


def test_the_privileged_module_is_the_only_place_they_appear() -> None:
    """Guards the exemption.

    The three scans above skip `PRIVILEGED_MODULES`, which is sound only while those
    files are genuinely the whole exemption and their calls are genuinely conditional.
    Both halves are checked here: every forbidden service call that appears anywhere
    under `tools/` must appear in one of them, and `server.py` must reach **each** of
    them only behind `mcp_full_access`.

    Without this, the exemption is a hole with a comment on it: a third file added to
    the skip list, or either module imported unconditionally, would leave every test
    above green while the forbidden operations became reachable on an installation that
    never opted in.

    Note what the second half does *not* claim. It proves each privileged import is
    *nested inside* the `if resolved_settings.mcp_full_access:` statement, which is what
    a structural check can prove about a nested import; that the three Drive tools
    additionally need Google is proved at runtime instead, by
    `test_drive_privileged_tools.py`'s four-way sweep over built servers -- the only
    place a *runtime* condition can be observed at all.
    """
    unconditional = _tools_source()
    privileged = "\n".join(path.read_text(encoding="utf-8") for path in PRIVILEGED_MODULES)

    for method in FORBIDDEN_SERVICE_CALLS:
        assert f".{method}(" not in unconditional, (
            f"'.{method}(' compare in un modulo che viene registrato sempre"
        )
        assert f".{method}(" in privileged, (
            f"'.{method}(' non compare in nessun modulo privilegiato: o il tool non "
            "esiste, o e' altrove, e in entrambi i casi l'esenzione qui sopra sta "
            "coprendo la cosa sbagliata"
        )

    dentro, fuori = _privileged_imports_of_server()
    assert dentro == {module.stem for module in PRIVILEGED_MODULES}, (
        "un modulo privilegiato non e' importato dentro `if resolved_settings."
        f"mcp_full_access:`: dentro la guardia ci sono {sorted(dentro)}"
    )
    assert not fuori, (
        f"{sorted(fuori)} sono importati fuori dalla guardia: verrebbero registrati "
        "sempre e l'esenzione delle scansioni diventerebbe un buco"
    )


def _privileged_imports_of_server() -> tuple[set[str], set[str]]:
    """The privileged modules `server.py` imports, split into those nested inside the
    `mcp_full_access` guard and those outside it.

    Structural rather than textual, because the textual version answered a weaker
    question than it looked like it did: "the import appears later in the file than the
    guard does" is also true of an import in a *sibling* block below the guard, or after
    it at module level -- both of which run unconditionally. This walks
    `build_server`'s body for the `ast.If` whose test is `resolved_settings.
    mcp_full_access` and asks whether each `ImportFrom` is a descendant of it.

    Two sets and not a boolean so the failure can say which module is on the wrong side;
    a module imported both inside and outside appears in both, and `fuori` being
    non-empty is what fails.
    """
    tree = ast.parse((TOOLS_DIR.parent / "server.py").read_text(encoding="utf-8"))

    def is_the_guard(node: ast.AST) -> bool:
        if not isinstance(node, ast.If):
            return False
        test = node.test
        return (
            isinstance(test, ast.Attribute)
            and test.attr == "mcp_full_access"
            and isinstance(test.value, ast.Name)
            and test.value.id == "resolved_settings"
        )

    guards = [node for node in ast.walk(tree) if is_the_guard(node)]
    assert guards, (
        "`if resolved_settings.mcp_full_access:` non esiste piu' in server.py: questo "
        "controllo non sta piu' guardando niente"
    )

    def imported_names(nodes: list[ast.AST]) -> set[str]:
        found: set[str] = set()
        for root in nodes:
            for node in ast.walk(root):
                if isinstance(node, ast.ImportFrom):
                    found |= {alias.name for alias in node.names}
        return found

    nomi = {module.stem for module in PRIVILEGED_MODULES}
    dentro = imported_names(list(guards)) & nomi
    tutti = imported_names([tree]) & nomi
    return dentro, tutti - dentro


def _agent_reachable_source() -> str:
    """Every line of `apps/mcp` an agent can reach on a default installation.

    Wider than `_tools_source()` on purpose, and the width is the point: agent-reachable
    code is not only `tools/`. `resources/entities.py` renders the `customer://`,
    `person://` and `deal://` templates, `prompts/` holds §10's four prompts, and
    `server.py` itself carries three inline `@mcp.resource` bodies plus
    `describe_schema`/`refresh_schema` -- all registered unconditionally, none of them
    scanned by a tools-only sweep. A `drive_reader_for` built inside a resource render or
    a prompt body would be exactly as reachable as one built in a tool, while leaving no
    trace under `tools/` at all.

    `prompts/` was the one missing, and it was missing from `_tools_source` too: nothing
    about it is conditional (`server.py` calls `register_prompts` outside every `if`), and
    a prompt takes the same `McpContext` a tool does, so «un prompt che riassume e poi
    emette» would have passed every ban in this file. It is now in both sweeps, through
    `_unconditional_paths`.

    `PRIVILEGED_MODULES` are subtracted, as everywhere else in this file: their whole
    existence is conditional.
    """
    paths = [
        path
        for base in (TOOLS_DIR, TOOLS_DIR.parent / "resources")
        for path in _unconditional_paths(base)
    ]
    paths.append(TOOLS_DIR.parent / "server.py")
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_the_wide_scan_really_reads_the_resources_the_prompts_and_the_server() -> None:
    """Guards the widening, the way this file guards every other scan. An
    `_agent_reachable_source` that silently collapsed back to `tools/` would leave the
    Drive-reader ban below green while no longer looking at the three places it was
    widened for."""
    source = _agent_reachable_source()
    assert "def render_customer(" in source  # resources/entities.py
    assert "def customer_resource(" in source  # server.py's inline @mcp.resource
    assert "def stato_cliente(" in source  # prompts/customer.py
    assert "def revisione_pipeline(" in source  # prompts/dashboards.py
    assert "def search_everything(" in source  # tools/__init__.py, i.e. the old width


def test_no_unconditional_module_can_even_obtain_a_drive_reader() -> None:
    """The complete form of the Drive half of the ban, and the reason
    `DriveReader.describe` needs no entry in `FORBIDDEN_SERVICE_CALLS`.

    A bare-name ban covers the methods whose names happen to be unique. This covers the
    *class*: `drive_reader_for` is the only way to obtain a `DriveReader` at all -- it
    is what runs `GoogleDriveAccountService.usable`, unseals the refresh token and reads
    `root_folder_ids` off the row, and a reader built any other way would be one whose
    roots nobody had checked. So a module that cannot name it has nothing to call
    `list_children`, `read_text` or `describe` *on*, whatever those methods are called
    next year.

    Stated as its own test rather than folded into the loop above because it is a
    different kind of statement: the ban list forbids calls, this forbids a capability.
    """
    unconditional = _agent_reachable_source()
    privileged = "\n".join(path.read_text(encoding="utf-8") for path in PRIVILEGED_MODULES)

    assert "drive_reader_for" not in unconditional, (
        "un modulo registrato sempre puo' costruire un DriveReader: da li' ogni "
        "metodo di lettura di Drive e' raggiungibile, anche quelli il cui nome non "
        "compare in FORBIDDEN_SERVICE_CALLS"
    )
    assert "drive_reader_for" in privileged, (
        "nessun modulo privilegiato costruisce un DriveReader: o i tool di Drive non "
        "esistono piu', o sono altrove, e in entrambi i casi questo divieto sta "
        "coprendo la cosa sbagliata"
    )
