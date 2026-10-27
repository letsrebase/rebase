"""The MCP surface of slice 5B, and the shape of its asymmetry.

An agent that reads a mailbox is a different proposition from one that creates a
customer, and an agent that *fetches* from a mailbox is a different proposition again.
This file asserts where the line was drawn:

**Granted, reading.** What is already in the CRM -- the stored mirror in
`gmail_messages`, the state of the credential, and the list of invoices worth chasing.
None of them makes a Google call, spends anybody's Gmail quota, or exercises anybody's
consent.

**Granted, writing: `draft_email`, and nothing else.** A draft is inert -- a row in this
CRM that no tool on this surface can send -- and the half that is missing is a person
reading the text before it leaves. That is the design, not a gap: the indefensible
pairing would be the inverse, a surface that could send but not draft.

**Refused, structurally.** Fetching (`sync`, `backfill`), connecting, disconnecting,
changing what the CRM keeps, preparing a payment reminder, and sending. None of them is a
permission check inside a registered tool, because a PAT inherits its owner's full role
and never expires (residuo R10): the only mechanism that holds is that the tool does not
exist. The durable list lives in `test_mcp_invoice_ban.py`, which owns the structural
bans; what is asserted here is the positive half -- exactly five tools, and no sixth,
computed as the difference between the configured and the unconfigured server so that no
tool can hide behind a name the filter does not match.

The proof that the read tools never reach Google is the repository-root socket guard:
it fails any test that opens one, so a read tool that grew a fetch would fail this file
rather than pass it quietly.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from mcp import Client
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.gmail.models import EmailDraft, GmailMessage, GmailMessageLink, GoogleAccount
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

# 32 bytes of "k", base64. Written out rather than imported from
# `fakes.gmail_fixtures`: that package lives under `packages/core/tests`, which is only
# on `sys.path` once a test from that root has been collected, so importing it here
# would make this file pass in a full run and fail when run on its own.
TOKEN_KEY_B64 = "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s="

MAILBOX = "io@example.it"
CLIENT = "ada@acme.it"


def _payload(result: Any) -> Any:
    return result.structured_content or json.loads(result.content[0].text)


def gmail_settings() -> Settings:
    return Settings(
        google_client_id="cid.apps.googleusercontent.com",
        google_client_secret="the-secret",
        google_token_key=TOKEN_KEY_B64,
        public_url="https://crm.example.it",
        # `_env_file=None` so a developer's own .env cannot change what is asserted.
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def gmail_actor(mcp_session: Session) -> Actor:
    """An `mcp` actor with a real `users.id`.

    The conftest's `ADMIN` has `id=None`, and every Gmail service resolves the account
    through `repo.account_for_user(actor.id)` -- one mailbox per user -- so an actor
    without an id has no mailbox by construction.
    """
    user = User(
        email="agent-owner@example.test",
        password_hash="x",
        nome="Owner",
        ruolo="admin",
        attivo=True,
    )
    mcp_session.add(user)
    mcp_session.flush()
    return Actor(id=user.id, type="mcp", role="admin")


@pytest.fixture
def gmail_server(mcp_session: Session, gmail_actor: Actor, tmp_path: Path) -> Any:
    return build_server(
        lambda: mcp_session,
        lambda: gmail_actor,
        LocalFileStorage(tmp_path),
        settings=gmail_settings(),
    )


@pytest.fixture
def connected_account(mcp_session: Session, gmail_actor: Actor) -> GoogleAccount:
    account = GoogleAccount(
        user_id=gmail_actor.id,
        google_sub="sub-123",
        email_address=MAILBOX,
        # Opaque on purpose: nothing in this file decrypts them, and no tool may show
        # them.
        refresh_token_ciphertext=b"\x01\x02ciphertext",
        refresh_token_nonce=b"\x03\x04nonce",
        scopes_granted=list(REQUESTED_SCOPES),
        status="active",
    )
    mcp_session.add(account)
    mcp_session.flush()
    return account


@pytest.fixture
def customer(mcp_session: Session) -> Customer:
    """A real `customers` row.

    `filed_message` predates this and files its link against a bare `uuid4()`, which is
    enough for the read tools -- `gmail_message_links` carries no foreign key, because
    `entity_id` points at one of three tables. `draft_email` is the first tool on this
    surface that *validates* the entity against the right table, so it needs a row that
    exists.
    """
    row = Customer(ragione_sociale="Acme S.r.l.", email=CLIENT)
    mcp_session.add(row)
    mcp_session.flush()
    return row


@pytest.fixture
def filed_message(
    mcp_session: Session, connected_account: GoogleAccount, customer: Customer
) -> Iterator[tuple[UUID, UUID]]:
    """One inbound message filed against one customer. Returns (customer id, message
    id) -- the two identifiers an agent would hold."""
    customer_id = customer.id
    message = GmailMessage(
        google_account_id=connected_account.id,
        gmail_message_id="m-1",
        gmail_thread_id="t-1",
        direction="inbound",
        from_address=CLIENT,
        to_addresses=[MAILBOX],
        cc_addresses=[],
        subject="Preventivo per il rifacimento del sito",
        snippet="Ciao, ci mandi un preventivo",
        internal_date=datetime.now(UTC) - timedelta(days=2),
        body_text="Ciao, ci mandi un preventivo per il rifacimento del sito?",
        attachments=[{"name": "brief.pdf", "mime": "application/pdf", "size": 1024}],
    )
    mcp_session.add(message)
    mcp_session.flush()
    mcp_session.add(
        GmailMessageLink(gmail_message_id=message.id, entity_type="customer", entity_id=customer_id)
    )
    mcp_session.flush()
    yield customer_id, message.id


# --- what exists, and what does not ---------------------------------------------------


# Every tool slice 5B adds, and no sixth. B2-10 decided this list -- the way B1-14
# decided the read half of it -- and the two writes are the whole of the decision:
# `draft_email` is granted because a draft is inert and its reviewer is a person, and
# nothing that *sends* or that writes a demand for money in the owner's name is here.
# See `tools/gmail.py`'s docstring for the reasoning, and `test_mcp_surface_coverage.py`
# for the refusals stated against the service methods they exclude.
SLICE_5B_TOOLS = {
    "list_gmail_messages",
    "get_gmail_message",
    "describe_gmail_account",
    "draft_email",
    "list_payment_reminder_candidates",
    # `describe_drive_account` (`tools/drive.py`) is not a slice 5B tool -- it is 9B's
    # Drive diagnosis, registered right after `gmail_tools.register` in the same
    # `gmail_configured` block because one Google OAuth client serves both credentials
    # (slice 9 §5.5). It belongs in this set anyway: the test right below computes the
    # difference between the configured and the unconfigured server, and that
    # difference is now six tools, not five.
    "describe_drive_account",
}


async def _tool_names(server: Any) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in (await client.list_tools()).tools}


async def test_the_gmail_surface_is_exactly_these_five_tools(
    gmail_server: Any, server: Any
) -> None:
    """The positive half of the asymmetry, asserted as an equality rather than a subset:
    "and no others" is the claim, and a subset assertion would pass with a `sync_gmail`
    sitting next to them.

    Computed as the *difference* between the configured and the unconfigured server
    rather than by filtering names on the substring "gmail". The substring filter was
    what this test used while the surface was three read tools, and it would have gone
    on passing when `draft_email` and `list_payment_reminder_candidates` arrived --
    neither name contains it. A difference cannot miss a tool because of what it is
    called.
    """
    assert await _tool_names(gmail_server) - await _tool_names(server) == SLICE_5B_TOOLS
    # And nothing disappeared when Gmail was configured: "absent" must mean the Gmail
    # tools are added, never that the rest of the surface was rebuilt differently.
    assert await _tool_names(server) - await _tool_names(gmail_server) == set()


async def test_no_tool_can_send_anything(gmail_server: Any) -> None:
    """Spec 8.2 and 13, criterion 16. An email sent from your mailbox cannot be
    recalled and the client reads it as your words; an agent holding *read* of the mail
    and *send* in the same belt has the injection source and the exfiltration channel on
    one channel. The human presses Invia.

    Asserted on the substring and not only on a list of names: the point is that no tool
    sends, not that four particular spellings are absent."""
    async with Client(gmail_server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert not [name for name in names if "send" in name or "invia" in name]


async def test_no_tool_schema_accepts_a_gmail_search_string(gmail_server: Any) -> None:
    """Verified by schema and not only by name: a parameter called `filtro` that took a
    Gmail expression would be the same hole with better manners. Every guarantee in spec
    4 -- the address filter, the roster, "an empty roster issues no request" -- is one
    free-text parameter away from being nothing.

    `entity_type` is the only free-form string allowed, and it is a closed enum. Every
    identifier is a UUID, which carries `format` in its schema and is checked here for
    exactly that."""
    # `entity_type` is a closed enum. The three that follow are `draft_email`'s, and they
    # are admissible for a mechanical reason rather than a judgement: each is validated
    # by `EmailDraftCreate` (`SafeStr`, `max_length`) and none of them ever reaches a
    # Gmail query -- they become the headers and the body of a local row and stop there.
    # Named one by one, and never widened to "the tool's own parameters", so the next
    # free-text parameter added anywhere on this surface still has to argue for itself.
    allowed_strings = {"entity_type", "to_addresses", "subject", "body_markdown"}
    async with Client(gmail_server) as client:
        tools = (await client.list_tools()).tools

    checked = 0
    for tool in tools:
        if tool.name not in SLICE_5B_TOOLS:
            continue
        for name, schema in (tool.input_schema or {}).get("properties", {}).items():
            checked += 1
            # An array of strings is checked through its `items`, not skipped: a
            # parameter called `filtri: list[str]` carrying a Gmail expression would be
            # the same hole with an extra pair of brackets round it.
            inner = schema.get("items", {}) if schema.get("type") == "array" else schema
            if inner.get("type") == "string" and "format" not in inner:
                assert name in allowed_strings, f"{tool.name}.{name} accetta testo libero"
    # A sweep over zero properties is a sweep that cannot fail.
    assert checked >= 8, checked


async def test_the_gmail_tools_are_absent_when_gmail_is_not_configured(server: Any) -> None:
    """Absent, not broken (spec 5.3). The `server` fixture builds with the process's own
    settings, and this repository has no `.env`, so this is the unconfigured
    installation -- the same one the whole suite runs under (spec 13, criterion 8)."""
    names = await _tool_names(server)

    assert not (names & SLICE_5B_TOOLS)
    # And the rest of the surface is untouched: "absent" must mean the Gmail tools, not
    # a server that failed to build.
    assert "create_customer" in names


# --- what the three tools actually answer ----------------------------------------------


async def test_list_gmail_messages_reads_the_stored_mirror(
    gmail_server: Any, filed_message: tuple[UUID, UUID]
) -> None:
    """The whole reason this tool may exist while `sync_gmail` may not: it reads rows
    this CRM already holds. No Google call, no quota, no consent exercised -- and the
    repository-root socket guard is what turns that from a claim into a test, because it
    fails any test that opens a socket."""
    customer_id, message_id = filed_message
    async with Client(gmail_server) as client:
        result = await client.call_tool(
            "list_gmail_messages",
            {"entity_type": "customer", "entity_id": str(customer_id)},
        )

    rows = _payload(result)
    rows = rows["result"] if isinstance(rows, dict) and "result" in rows else rows
    assert [row["id"] for row in rows] == [str(message_id)]
    assert rows[0]["da"] == CLIENT
    assert rows[0]["oggetto"] == "Preventivo per il rifacimento del sito"
    # The listing is a listing: the body belongs to `get_gmail_message`, so an agent
    # that only needs to know a conversation exists does not pull a customer's whole
    # correspondence into its context to find out.
    assert "corpo" not in rows[0]


async def test_get_gmail_message_returns_the_body_marked_as_untrusted(
    gmail_server: Any, filed_message: tuple[UUID, UUID]
) -> None:
    """The body is what makes this tool worth having -- an agent cannot answer "what did
    the client actually ask for" from a snippet -- and it is also the one place text
    written by somebody outside this CRM enters an agent's context. It is returned with
    a provenance marker for that reason, and the surface holds no send tool, so the
    injection source has no exfiltration channel next to it."""
    _customer_id, message_id = filed_message
    async with Client(gmail_server) as client:
        result = await client.call_tool("get_gmail_message", {"message_id": str(message_id)})

    body = _payload(result)
    assert body["corpo"] == "Ciao, ci mandi un preventivo per il rifacimento del sito?"
    assert body["allegati"][0]["name"] == "brief.pdf"
    assert "non attendibile" in body["provenienza"]


async def test_get_gmail_message_marks_our_own_mail_as_ours(
    gmail_server: Any, mcp_session: Session, connected_account: GoogleAccount
) -> None:
    """The other side of the marker, and the reason it is a field rather than a constant
    banner: text the owner of this CRM wrote is not somebody else's instruction, and
    labelling it as untrusted would train an agent to discount the one half of a thread
    it can rely on."""
    message = GmailMessage(
        google_account_id=connected_account.id,
        gmail_message_id="m-2",
        gmail_thread_id="t-1",
        direction="outbound",
        from_address=MAILBOX,
        to_addresses=[CLIENT],
        cc_addresses=[],
        subject="Re: Preventivo",
        snippet="Eccolo",
        internal_date=datetime.now(UTC),
        body_text="Eccolo, in allegato.",
        attachments=[],
    )
    mcp_session.add(message)
    mcp_session.flush()

    async with Client(gmail_server) as client:
        result = await client.call_tool("get_gmail_message", {"message_id": str(message.id)})

    assert "non attendibile" not in _payload(result)["provenienza"]


async def test_a_message_id_nobody_stored_is_guidance_not_a_stack_trace(
    gmail_server: Any,
) -> None:
    async with Client(gmail_server) as client:
        result = await client.call_tool("get_gmail_message", {"message_id": str(uuid4())})

    assert result.is_error
    rendered = result.content[0].text
    # `_guard` turns the `NotFound` into `to_agent_message`'s rendered guidance. What
    # must never appear is the raw pydantic/SDK shape with its English footer.
    assert "errors.pydantic.dev" not in rendered
    assert "gmail_message" in rendered


async def test_describe_gmail_account_tells_an_agent_to_stop_retrying(
    gmail_server: Any, mcp_session: Session, connected_account: GoogleAccount
) -> None:
    """The reason this tool is granted while `sync_gmail` is not. An agent that cannot
    see the credential's state answers a stale mirror as if it were current, or retries
    a fetch that will never succeed. This is the diagnosis; pressing Sincronizza is the
    person's half."""
    connected_account.status = "revoked"
    connected_account.last_error = "revocato da Google"
    mcp_session.flush()

    async with Client(gmail_server) as client:
        result = await client.call_tool("describe_gmail_account", {})

    health = _payload(result)
    assert health["banner"] == "revoked"
    assert health["account"]["email_address"] == MAILBOX
    assert "ricollegh" in health["banner_text"]


async def test_no_gmail_tool_answer_carries_a_credential(
    gmail_server: Any, filed_message: tuple[UUID, UUID]
) -> None:
    """The refresh token is a credential to a *third-party* account. It may not reach a
    tool result any more than it may reach a log line -- and a tool result is the one
    channel that ends up verbatim in a model's context and, from there, wherever that
    context is stored."""
    customer_id, message_id = filed_message
    async with Client(gmail_server) as client:
        answers = [
            await client.call_tool(
                "list_gmail_messages",
                {"entity_type": "customer", "entity_id": str(customer_id)},
            ),
            await client.call_tool("get_gmail_message", {"message_id": str(message_id)}),
            await client.call_tool("describe_gmail_account", {}),
        ]

    rendered = json.dumps([_payload(answer) for answer in answers], default=str)
    for forbidden in ("ciphertext", "nonce", "refresh_token", "the-secret", TOKEN_KEY_B64):
        assert forbidden not in rendered, forbidden


# --- the one writing tool, and what it deliberately cannot do ---------------------------


async def test_draft_email_is_the_one_writing_tool_an_agent_gets(gmail_server: Any) -> None:
    """Spec 8.1: this is where an agent is worth the most -- «prepara l'email di
    accompagnamento all'offerta» -- and it writes a local draft, nothing more.

    The pairing is the decision. A surface with `send_email` and no `draft_email` would
    be the indefensible one; this is its inverse, and what makes it safe is that the
    missing half is a person reading the text before it leaves.
    """
    names = await _tool_names(gmail_server)

    assert "draft_email" in names
    assert "send_email" not in names


async def test_draft_email_leaves_the_draft_unsent_and_asks_google_nothing(
    gmail_server: Any, mcp_session: Session, filed_message: tuple[UUID, UUID]
) -> None:
    """`bozza` is a state only `EmailSendService` can leave, and this surface holds no
    tool that could. The silence is proved by the repository-root socket guard, which
    fails any test that opens one."""
    customer_id, _message_id = filed_message

    async with Client(gmail_server) as client:
        result = await client.call_tool(
            "draft_email",
            {
                "entity_type": "customer",
                "entity_id": str(customer_id),
                "to_addresses": [CLIENT],
                "subject": "Accompagnamento offerta",
                "body_markdown": "Gentile Ada,\n\nin allegato l'offerta.",
            },
        )

    answer = _payload(result)
    assert answer["stato"] == "bozza"
    assert "persona" in answer["nota"]

    draft = mcp_session.execute(select(EmailDraft)).scalars().one()
    assert draft.send_state == "bozza"
    assert draft.subject == "Accompagnamento offerta"
    assert draft.sent_gmail_message_id is None
    assert draft.google_account_id is None
    # Attaching is a separate decision, taken by somebody who can see what they are
    # attaching -- so the tool takes no attachment parameter and writes none.
    assert draft.attachment_version_ids == []


async def test_draft_email_refuses_an_entity_that_does_not_exist(gmail_server: Any) -> None:
    """A draft filed against nothing is a trap with a Send button. The refusal has to be
    rendered guidance, not the SDK's raw pydantic dump."""
    async with Client(gmail_server) as client:
        result = await client.call_tool(
            "draft_email",
            {
                "entity_type": "customer",
                "entity_id": str(uuid4()),
                "to_addresses": [CLIENT],
                "subject": "Offerta",
                "body_markdown": "Testo",
            },
        )

    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_a_readonly_agent_cannot_even_draft(
    mcp_session: Session, gmail_actor: Actor, tmp_path: Path, filed_message: tuple[UUID, UUID]
) -> None:
    """A PAT inherits its owner's role, and a `readonly` owner's token must not write.
    That is a role check and not the structural ban -- the structural ban is that there
    is no send tool at all, whatever the role."""
    customer_id, _message_id = filed_message
    readonly = Actor(id=gmail_actor.id, type="mcp", role="readonly")
    server = build_server(
        lambda: mcp_session,
        lambda: readonly,
        LocalFileStorage(tmp_path),
        settings=gmail_settings(),
    )

    async with Client(server) as client:
        result = await client.call_tool(
            "draft_email",
            {
                "entity_type": "customer",
                "entity_id": str(customer_id),
                "to_addresses": [CLIENT],
                "subject": "Offerta",
                "body_markdown": "Testo",
            },
        )

    assert result.is_error
    assert mcp_session.execute(select(EmailDraft)).scalars().all() == []


# --- the laborious read -----------------------------------------------------------------


async def test_list_payment_reminder_candidates_reads_the_register_and_calls_nobody(
    gmail_server: Any, mcp_session: Session, customer: Customer
) -> None:
    """The one method of 5B-2 that genuinely earns a tool: crossing due dates against
    payments against what has already gone out is the laborious part of chasing money.

    `importo` travels as the invoice's own frozen decimal, as a string. A float here
    would be a demand for payment naming a figure the client's copy does not carry.
    """
    scadenza = oggi_in_italia() - timedelta(days=40)
    invoice = Invoice(
        customer_id=customer.id,
        tipo="fattura",
        stato="emessa",
        anno=scadenza.year,
        numero=4321,
        data_emissione=scadenza - timedelta(days=30),
        data_scadenza=scadenza,
        imponibile=Decimal("1000.00"),
        imposta=Decimal("220.00"),
        totale=Decimal("1220.00"),
    )
    mcp_session.add(invoice)
    mcp_session.flush()

    async with Client(gmail_server) as client:
        result = await client.call_tool("list_payment_reminder_candidates", {})

    rows = _payload(result)
    rows = rows["result"] if isinstance(rows, dict) and "result" in rows else rows
    mine = [row for row in rows if row["invoice_id"] == str(invoice.id)]
    assert len(mine) == 1
    assert mine[0]["importo"] == "1220.00"
    assert mine[0]["cliente"] == "Acme S.r.l."
    assert mine[0]["giorni_di_ritardo"] == 40
    assert mine[0]["prossimo_livello"] == 1


async def test_no_tool_prepares_a_payment_reminder(gmail_server: Any) -> None:
    """The line between `draft_email` and this one is not read-versus-write and not
    caution. A covering email is neutral text somebody asked for; a reminder is a demand
    for money in the owner's name, carrying the owner's IBAN, and preparing one
    *consumes* one of the three positions the register allows per invoice -- so an agent
    that could prepare reminders would spend the ceiling that stops a disputed invoice
    becoming an automated persecution, with nobody having decided to chase it at all."""
    names = await _tool_names(gmail_server)

    assert not [name for name in names if "reminder" in name and not name.startswith("list_")]
    assert not [name for name in names if "sollecit" in name]


async def test_a_malformed_gmail_message_id_gets_the_same_guidance_as_every_other_id(
    gmail_server, connected_account
) -> None:
    """REB-808: `get_gmail_message` and `list_gmail_messages` took a `UUID`, which the SDK
    checked ahead of the guard and answered in pydantic's English."""
    async with Client(gmail_server) as client:
        message = await client.call_tool("get_gmail_message", {"message_id": "non-un-uuid"})
        listing = await client.call_tool(
            "list_gmail_messages", {"entity_type": "customer", "entity_id": "non-un-uuid"}
        )

    for result in (message, listing):
        assert result.is_error
        text = result.content[0].text
        assert "errors.pydantic.dev" not in text
        assert "non è un identificativo valido" in text
        assert "Valore atteso" in text
