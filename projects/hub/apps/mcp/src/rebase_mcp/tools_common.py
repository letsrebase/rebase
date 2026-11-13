"""What every tool module shares: the dependencies a tool is handed and the parsers that
turn a tool's string parameters into values with an English refusal.

`Deps` is what `build_server` used to close over: the session factory, the admin behind
the call, the settings and the collaborators the transports build
(`rebase_core.dependencies`). Each `tools_*` module exposes `register(mcp, deps)` and
declares its tools on the server; none of them reaches for a global.

One session per tool call, closed whatever happened (`Deps.call`): the SDK dispatches
sync tools on a thread pool, and a session shared across calls is the defect PigroCRM's
MCP server measured as zero rows written under concurrency.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, get_args
from uuid import NAMESPACE_URL, UUID, uuid5

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.amounts import NotAnAmount, italian_amount
from rebase_core.audit import AdminActionService
from rebase_core.config import Settings
from rebase_core.crm_link import CrmLeadService
from rebase_core.errors import DomainError
from rebase_core.http import HttpCall, urllib_call
from rebase_core.llm import LlmCall
from rebase_core.mail import EmailSender
from rebase_core.pigro_door import FreelancerDoor, freelancer_door
from rebase_core.ratelimit import RateLimiter
from rebase_core.schema_common import Remote
from rebase_core.search import SEARCH_MAX_LENGTH
from rebase_core.validation import NOT_VALID, field_reason

_REMOTE_WORDS: tuple[Remote, ...] = get_args(Remote)
SessionFactory = sessionmaker[Session]
# Who is calling: resolved by the transport, read by the tools that sign something.
AdminProvider = Callable[[], AdminRead]
# What builds the service behind `link_company_to_pigro` (REB-869): it files a request in the CRM.
CrmLeadsFactory = Callable[[Session], CrmLeadService]
# A model seam asked on each tool call, `None` when no model is configured for the task.
LlmSource = Callable[[], LlmCall | None]

# The door every write of this server comes through, recorded on notes and status changes.
SOURCE = "mcp"

# What one admin may ask of the model through this server, per hour: a regeneration of
# a card, or a CRM draft for a new deal. The token of a leaked agent is otherwise a way
# to spend the daily euro cap on a loop (REB-803).
MODEL_CALLS_PER_WINDOW = 30
MODEL_CALLS_WINDOW_SECONDS = 3600.0
MODEL_CALLS_REFUSED = (
    "Too many requests that use the model: at most {limit} an hour per admin. Try again later."
)

# A request names a handful of skills; more than this many terms is a list nobody
# matches, and each term is one more `EXISTS` on every card.
SKILL_TERMS_MAX = 10

# The kinds an audit line of a read has, and where a read of a whole list is filed: the
# list belongs to no row, so its entity is fixed, as the AI configuration's is.
CV_READ = "cv_read"
EMAILS_READ = "emails_read"
RECIPIENTS_READ = "recipients_read"
READ_ENTITY = "mcp_read"
_READ_ENTITY_NAMESPACE = "https://letsrebase.com/hub/mcp_read/"


def read_entity_id(tool: str) -> UUID:
    """The fixed id a tool's list reads are filed under (`READ_ENTITY`)."""
    return uuid5(NAMESPACE_URL, _READ_ENTITY_NAMESPACE + tool)


def not_configured(what: str, env: str) -> ToolError:
    """The refusal of a tool whose service needs a token this environment does not have:
    «`what` is not configured in this environment: `env` is missing»."""
    return ToolError(f"{what} is not configured in this environment: {env} is missing.")


# `field_reason`'s two own words, shared with the web app and so Italian there; a tool's
# refusal says them in English. A validator's own sentence is the core's and is passed on.
_REASON_WORDS = {"manca": "missing", NOT_VALID: "not valid"}


def field_refusal(exc: PydanticValidationError) -> ToolError:
    """A tool's input refused as «field: reason»: `field_reason`'s words, never Pydantic's
    own text, which repeats the value typed -- a tax identifier does not come back in an answer
    this way -- and for a text past its limit the limit itself, the way the note tools say it
    («at most 4000 characters»). An optional text's limit is Pydantic's `too_long`, a plain
    one's `string_too_long`: both carry `max_length`. The company and team tools refuse
    through this."""
    error = exc.errors()[0]
    field = ".".join(str(part) for part in error["loc"])
    if error["type"] in {"string_too_long", "too_long"}:
        return ToolError(f"{field}: at most {error['ctx']['max_length']} characters")
    reason = field_reason(error)
    return ToolError(f"{field}: {_REASON_WORDS.get(reason, reason)}")


def number(value: str | None, field: str) -> Decimal | None:
    """An amount passed as a string, so a rate or a budget keeps its two decimals
    exactly (a JSON number would not), and read the Italian way the web app reads it:
    «1.500» is 1500, «1.234,50» is 1234.50 (`rebase_core.amounts`, REB-485)."""
    if value is None or not value.strip():
        return None
    try:
        return italian_amount(value)
    except NotAnAmount:
        raise ToolError(f"{field}: «{value}» is not a number") from None


def integer(value: str | None, field: str) -> int | None:
    """A whole number passed as a string, the same convention `number` gives a
    decimal one: `headcount` and `onsite_days` (REB-380)."""
    if value is None or not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError:
        raise ToolError(f"{field}: «{value}» is not a whole number") from None


def day(value: str | None, field: str) -> date | None:
    """An ISO 8601 calendar date (`YYYY-MM-DD`), parsed here so the refusal is an
    English sentence and not a traceback from the service."""
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{field}: «{value}» is not an ISO 8601 date (YYYY-MM-DD)") from None


def moment(value: str | None, field: str) -> datetime | None:
    """An ISO 8601 timestamp (a plain date is accepted too, at midnight)."""
    if value is None or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{field}: «{value}» is not an ISO 8601 date and time") from None


# What of a bad id is echoed back: an agent that pasted a paragraph does not get it
# repeated in the error.
_ID_ECHO_MAX = 40


def to_uuid(value: str, field: str) -> UUID:
    """An id passed as a string, refused with a sentence naming the parameter:
    a malformed one would otherwise surface as a bare `ValueError` from `UUID(...)`."""
    try:
        return UUID(value.strip())
    except (ValueError, AttributeError):
        shown = value if len(value) <= _ID_ECHO_MAX else value[:_ID_ECHO_MAX] + "…"
        raise ToolError(f"{field}: «{shown}» is not a valid id") from None


def as_remote(value: str | None) -> Remote | None:
    """`remote` as the schema's own closed set (`remote`, `hybrid`, `on_site`), refused
    with the three words when it is another."""
    if value is None:
        return None
    for word in _REMOTE_WORDS:
        if value == word:
            return word
    raise ToolError(f"remote: «{value}» is not allowed: {', '.join(_REMOTE_WORDS)}")


def search_term(value: str | None) -> str | None:
    """`q` bounded the way the API's `SearchQ` bounds it: an unbounded term reaching
    `ILIKE` and `similarity()` on every row is a denial of service with extra steps.
    Every free-text filter of a list tool (`q`, `location`, `job_title`, `utm_source`,
    `origin`, `origin_page`) goes through this."""
    if value is not None and len(value) > SEARCH_MAX_LENGTH:
        raise ToolError(f"the search must fit in {SEARCH_MAX_LENGTH} characters")
    return value


def bounded(value: str | None, field: str) -> str | None:
    """A free-text or closed-set filter within `SEARCH_MAX_LENGTH`, so a filter nobody
    would type at that length never reaches a query or an error message
    (`search_term` is the same bound for `q`, in its own words)."""
    if value is not None and len(value) > SEARCH_MAX_LENGTH:
        raise ToolError(f"{field}: at most {SEARCH_MAX_LENGTH} characters")
    return value


def skill_terms(values: list[str] | None, field: str = "skills") -> list[str] | None:
    """`skills` (or `industries`, `languages`, `seniority`: `field` names the one being
    read) bounded like `q`: at most `SKILL_TERMS_MAX` terms, each within
    `SEARCH_MAX_LENGTH`."""
    if values is None:
        return None
    if len(values) > SKILL_TERMS_MAX:
        raise ToolError(f"{field}: at most {SKILL_TERMS_MAX} terms")
    for value in values:
        search_term(value)
    return values


def supplied_text(raw: str) -> str | None:
    """A tool parameter's own contract for `override_freelancer`/`override_company`:
    the caller already checked `is not None` before calling this, so an empty string
    is the one thing left it can mean -- clear the field, the same convention every
    optional override takes. A nullable column accepts it;
    `rebase_core.audit.reject_cleared_columns` refuses it on one that is not, naming the
    field rather than leaving it silently ignored."""
    return raw or None


@dataclass(frozen=True)
class Deps:
    """Everything a tool reads from the server that built it. `admin` answers the admin
    behind the current call; `settings` and `http` are what reaches the CRM, for
    `list_pigro_spaces` and for the `pigro_slug` of `get_talent`, and without them the
    registry's tools answer the same sentence the admin area shows when it is not
    configured while the slug stays `None`."""

    factory: SessionFactory
    admin: AdminProvider
    settings: Settings | None
    http: HttpCall | None
    card_llm: LlmSource | None
    sender: EmailSender | None
    crm_leads: CrmLeadsFactory | None
    # The per-admin cap on what spends the model (`spend_model_call`).
    model_calls: RateLimiter

    def call[T](self, call: Callable[[Session], T]) -> T:
        """One session per call, closed whatever happened, and a domain error rendered
        as its own sentence rather than a stack trace. Only `DomainError` is mapped: a
        malformed parameter is refused earlier by `to_uuid` and the other parsers."""
        session = self.factory()
        try:
            return call(session)
        except DomainError as exc:
            raise ToolError(exc.message) from exc
        finally:
            session.close()

    def run(self, call: Callable[[Session], BaseModel]) -> dict[str, Any]:
        """`call` for a tool that answers one row as the service reads it, dumped while
        its session is still open."""
        return self.call(lambda session: call(session).model_dump(mode="json"))

    def run_list(self, call: Callable[[Session], Sequence[BaseModel]]) -> list[dict[str, Any]]:
        """`run`'s own shape for a tool that answers several rows, like
        `get_freelancer_audit`."""
        return self.call(lambda session: [item.model_dump(mode="json") for item in call(session)])

    @property
    def freelancer_door(self) -> FreelancerDoor:
        """The CRM's reads of a freelancer (REB-1107), for the tools that decide on what the
        CRM holds (the merge). With no settings handed in the token is empty, so every read
        answers that this environment is not configured, and the tool refuses."""
        settings = self.settings if self.settings is not None else Settings(_env_file=None)  # type: ignore[call-arg]
        return freelancer_door(settings, self.http if self.http is not None else urllib_call)

    @property
    def campaign_settings(self) -> Settings:
        """The campaign tools only read; with no settings handed in the defaults do."""
        if self.settings is not None:
            return self.settings
        return Settings(_env_file=None)  # type: ignore[call-arg]

    def team_settings(self) -> Settings:
        """The team requests, the AI configuration and the cloud all need real settings
        (a hub URL, a contracts mailbox). A server built for tools that never touch this
        family (most of this suite's tests) still needs none."""
        if self.settings is None:
            raise ToolError("the team builder and cloud tools require `settings`")
        return self.settings

    def spend_model_call(self) -> None:
        """Counts one call that will ask the model against the calling admin, or refuses
        with a sentence once they have used the hour's allowance. Called before anything
        is written, so a refusal leaves nothing half done."""
        if not self.model_calls.spend(str(self.admin().id)):
            raise ToolError(MODEL_CALLS_REFUSED.format(limit=self.model_calls.limit))

    def record_read(
        self,
        session: Session,
        entity_type: str,
        entity_id: UUID,
        kind: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """An audit line for a read of personal data (REB-803): who read what through
        this door. `AdminActionService.record` never raises, so a read is not refused
        because the trail could not keep up."""
        AdminActionService(session).record(
            entity_type, entity_id, kind, self.admin().id, {"source": SOURCE, **(payload or {})}
        )
