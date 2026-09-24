from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.validation import SafeStr

# Mirror the column widths in gmail/models.py exactly.
GOOGLE_SUB_MAX_LENGTH = 255
EMAIL_ADDRESS_MAX_LENGTH = 320
STATUS_MAX_LENGTH = 20
LAST_ERROR_MAX_LENGTH = 500
JTI_MAX_LENGTH = 64
CODE_VERIFIER_MAX_LENGTH = 128

# Four states, and every one of them calls for something different from the person:
# nothing, wait-and-retry, re-consent, and re-connect. See `GoogleAccount.status`.
GmailStatus = Literal["active", "expired", "revoked", "disconnected"]

SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
# `openid` + `email` identify *which* mailbox was connected: without the stable `sub`
# there is no way to refuse a reconnection that points at a different mailbox by
# mistake and silently relabels the entire history.
REQUESTED_SCOPES: tuple[str, ...] = ("openid", "email", SCOPE_READONLY, SCOPE_SEND)


class GoogleAccountRead(BaseModel):
    """What the settings page and `describe_gmail_account` show. No token, in either
    form: not the plaintext, not the ciphertext, not the nonce."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email_address: str
    scopes_granted: list[str]
    status: GmailStatus
    consent_expires_at: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    last_sync_at: datetime | None
    sync_watermark: datetime | None
    gmail_store_bodies: bool
    connected_at: datetime
    disconnected_at: datetime | None


class SyncReport(BaseModel):
    """What one cycle did. Returned by the REST endpoint and by the MCP tool, so an
    agent can diagnose instead of retrying.

    Deliberately not `frozen`: `sync()` fills the counters in as the cycle runs, so that
    a report exists -- and is truthful about how far the cycle got -- at every point
    rather than only at the end.

    Every field is a number, a timestamp or a flag. There is no room in it for a
    subject, an address or a body, which is what makes it safe to log and to hand to an
    agent.
    """

    started_at: datetime
    already_running: bool = False
    running_since: datetime | None = None
    queries_issued: int = 0
    threads_fetched: int = 0
    messages_stored: int = 0
    messages_skipped: int = 0
    links_created: int = 0
    states_pruned: int = 0
    # How many sends of unknown outcome this cycle settled, in either direction. A
    # number and not a list of drafts, for the same reason as everything else here: a
    # draft id would be the first thing in this model that names somebody's unsent
    # correspondence.
    reconciled: int = 0


class GmailMessageRead(BaseModel):
    """One stored message, as the API and the MCP surface show it.

    Read-only, so no `max_length` and no `SafeStr` anywhere: nothing here is ever an
    input. `body_text` is empty when the account has `gmail_store_bodies` off, which is
    the declared degradation of spec 5.4 and not a missing value.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    gmail_message_id: str
    gmail_thread_id: str
    direction: Literal["inbound", "outbound"]
    from_address: str
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str
    snippet: str
    internal_date: datetime
    body_text: str
    body_truncated: bool
    body_html_scartato: bool
    attachments: list[dict[str, object]]


# The four things that can be wrong, and `None` for "nothing is". `disconnected` is
# deliberately absent: a mailbox the user unhooked on purpose is not a fault to warn
# about, and a banner there would be an error message for a decision they made.
GmailBannerReason = Literal["revoked", "expiring", "expired", "scope_missing"] | None


class GmailHealth(BaseModel):
    """Everything the shell banner needs, in one response.

    A cause *and* its own sentence, because a single banner reading "problema con
    Gmail" helps nobody: re-consenting, waiting, and re-authorising for one missing
    scope are three different actions, and the banner is where the person finds out
    which one is theirs.

    `missing_scopes` is reported even when `banner` is `None`. A credential missing only
    `gmail.send` is healthy and syncing, and the settings page still has to be able to
    say that sending is off -- which is the same "status is the credential, capability
    is the scopes" split the whole module turns on.

    `configured` is what tells *absent* from *not yet connected*, and it exists because
    `account is None` alone cannot: an installation with no Google client and one whose
    owner simply has not consented yet both answer with no account, and they ask
    opposite things of the person -- "this feature does not exist here" versus "press
    Collega". Without it the settings page has to choose one sentence and be wrong half
    the time, which is the same collapse of two states into one that `disconnected` and
    `revoked` were split apart to avoid.
    """

    account: GoogleAccountRead | None
    banner: GmailBannerReason
    banner_text: str | None
    missing_scopes: list[str]
    configured: bool


class DiscoveredCorrespondent(BaseModel):
    """One address at the customer's domain that the connected mailbox has actually
    exchanged mail with. A suggestion, not a record: nothing is written until a person
    puts the address on a Person or the Customer."""

    model_config = ConfigDict(frozen=True)

    indirizzo: str
    # The display name the headers gave, or "" when they never did.
    nome: str
    messaggi: int
    ultimo_messaggio: datetime | None
    gia_in_anagrafica: bool


class SuggestedPerson(BaseModel):
    """Somebody at a proposed customer's domain who took part in a conversation with
    the connected mailbox."""

    model_config = ConfigDict(frozen=True)

    indirizzo: str
    # The display name the headers gave, or "" when they never did.
    nome: str
    # Already a Person in this CRM: importing leaves them alone.
    gia_in_anagrafica: bool


class SuggestedCustomer(BaseModel):
    """One domain the connected mailbox has corresponded with, proposed as a customer
    (spec 2026-09-16 §5, REB-223). A proposal, not a record: nothing is written until a
    person ticks it and `POST /api/customers/from-suggestions` creates it."""

    model_config = ConfigDict(frozen=True)

    dominio: str
    # A company name guessed from the domain, for the person to correct before importing.
    nome: str
    # Conversations (Gmail threads) the mailbox wrote in with somebody at this domain.
    conversazioni: int
    ultimo_messaggio: datetime | None
    persone: list[SuggestedPerson]


class DiscoveryReport(BaseModel):
    """What one discovery found. Addresses and names are the *point* of this report,
    which is what separates it from `SyncReport`: it goes back to whoever asked and to
    nobody else, and it is never logged."""

    started_at: datetime
    dominio: str
    threads_scanned: int = 0
    messages_seen: int = 0
    corrispondenti: list[DiscoveredCorrespondent] = []


class GmailBackfillRequest(BaseModel):
    """One entity's history, on demand (spec 4.4).

    No free-text field, so no `SafeStr` anywhere -- and that absence is the point:
    nothing a caller can type reaches Gmail. The addresses searched are read from the
    entity by `GmailSyncService._addresses_of`, never supplied by the request.

    `deal` is deliberately absent from `entity_type` even though messages are *filed*
    against deals: a deal has no address of its own, and the only sensible reading of
    "backfill this deal" is "backfill its customer", which the caller can ask for
    directly and unambiguously.
    """

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["person", "customer"]
    entity_id: UUID
    full: bool = False


class GmailSettingsUpdate(BaseModel):
    """Whether the CRM keeps the body of the correspondence it reads.

    One field, and it is the whole payload: `status`, `scopes_granted` and the
    watermarks are facts about the credential and the cycle, not settings, so a wider
    update model would offer to write things nothing may write.
    """

    model_config = ConfigDict(extra="forbid")

    gmail_store_bodies: bool


# Mirror `EmailDraft`'s own column widths, the same way the constants at the top of this
# module mirror `GoogleAccount`'s. 998 is RFC 5322's maximum line length minus the field
# name -- the real bound, not a guessed one.
SUBJECT_MAX_LENGTH = 998
# Generous for an email, so it bites only on the anomalous: the same discipline as the
# 256 KB inbound body limit, and for the same reason -- a limit that fires on ordinary
# work teaches people to route around the product.
BODY_MAX_LENGTH = 100_000

# Five states, and none of them is a synonym for another. `incerto` is the whole of spec
# 6.3(b): the send neither succeeded nor failed, and the interface says «esito da
# verificare» rather than guessing. Collapsing it into either neighbour is the previous system's
# defect -- «il CRM crede una cosa diversa da quella che è successa».
SendState = Literal["bozza", "in_invio", "inviato", "incerto", "fallito"]

# The states in which the text is still the user's -- and therefore, necessarily, the
# states from which it can be sent. One set and not two: "you may edit this" and "you may
# send this" are the same claim about a draft that has not left, and two frozensets
# drifting apart would produce a draft the composer offers to edit and the send path
# refuses, or the reverse.
#
# `fallito` is in it on purpose. Spec 6.3(a) says a refused send leaves «la bozza intatta
# con l'errore accanto, il composer si riapre con il testo dentro»; a draft frozen by its
# own failure would leave "write it again" as the only recovery, which is exactly the
# loss the `email_drafts` table exists to prevent.
EDITABLE_SEND_STATES: frozenset[str] = frozenset({"bozza", "fallito"})


class EmailDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["customer", "person", "deal"]
    entity_id: UUID
    to_addresses: list[Annotated[SafeStr, Field(max_length=EMAIL_ADDRESS_MAX_LENGTH)]]
    cc_addresses: list[Annotated[SafeStr, Field(max_length=EMAIL_ADDRESS_MAX_LENGTH)]] = []
    subject: SafeStr = Field(max_length=SUBJECT_MAX_LENGTH)
    body_markdown: SafeStr = Field(max_length=BODY_MAX_LENGTH)
    attachment_version_ids: list[UUID] = []
    in_reply_to_message_id: UUID | None = None


class EmailDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_addresses: list[Annotated[SafeStr, Field(max_length=EMAIL_ADDRESS_MAX_LENGTH)]] | None = None
    cc_addresses: list[Annotated[SafeStr, Field(max_length=EMAIL_ADDRESS_MAX_LENGTH)]] | None = None
    subject: SafeStr | None = Field(default=None, max_length=SUBJECT_MAX_LENGTH)
    body_markdown: SafeStr | None = Field(default=None, max_length=BODY_MAX_LENGTH)
    attachment_version_ids: list[UUID] | None = None


class EmailDraftAttachment(BaseModel):
    """One file the send will attach, named the way the recipient will see it.

    `filename` is built by the same function the send composes with
    (`attach.attachment_filename`), so the name a person reads before pressing Invia is
    the name that leaves. `None` means the version id no longer resolves to a file the
    send could attach -- the send would refuse it -- and the interface says so rather
    than printing an id nobody can read.
    """

    version_id: UUID
    filename: str | None
    dimensione: int | None


class EmailDraftRead(BaseModel):
    """Every column of `EmailDraft`, and the attachments by name.

    `send_state`, `sent_gmail_message_id` and `message_id_header` are on the way out and
    never on the way in: they are facts about what happened, and a Create schema that
    accepted them would let a caller declare a message sent that never left.

    `attachments` is the one field that is not a column. `attachment_version_ids` is what
    the row stores, and an id tells the person reviewing a draft nothing about what their
    client is about to receive (REB-415). It is required rather than defaulted, and the
    row is never validated straight into this model: `drafts.read_drafts` is the one
    builder, so a response that forgot to name the files fails instead of claiming there
    are none.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str
    entity_id: UUID
    # Which mailbox sent it, `None` until somebody presses Invia. An account id and
    # nothing else: it names a row of this installation's own, not an address.
    google_account_id: UUID | None
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str
    body_markdown: str
    attachment_version_ids: list[UUID]
    message_id_header: str
    in_reply_to_message_id: UUID | None
    send_state: SendState
    send_attempted_at: datetime | None
    last_error: str | None
    sent_gmail_message_id: str | None
    payment_reminder_id: UUID | None
    created_at: datetime
    updated_at: datetime
    attachments: list[EmailDraftAttachment]


class EmailDraftListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: Literal["customer", "person", "deal"] | None = None
    entity_id: UUID | None = None
    send_state: SendState | None = None
    limit: int = Field(default=50, ge=1, le=200)


class EmailDraftPage(BaseModel):
    items: list[EmailDraftRead]
    total: int


class SollecitoCandidate(BaseModel):
    """One invoice worth chasing, with everything the person needs to decide.

    Read-only, so no `SafeStr` and no `max_length`: nothing here is ever an input. It is
    what `SollecitiService.candidates` answers, and building this list -- crossing due
    dates against payments against what has already been sent -- is the part of the job
    that was actually laborious. Pressing a button never was.

    `importo` is the invoice's own frozen `totale`, a `Decimal` at two places, never a
    sum recomputed at reminder time: a demand for payment that names a figure the
    client's copy of the invoice does not carry is a demand they are right to ignore.

    `ultima_risposta_il` is the signal the previous system could not have had, and it is `None` on
    an installation with no mailbox connected -- the honest degradation, not a claim that
    nobody replied.
    """

    model_config = ConfigDict(from_attributes=True)

    invoice_id: UUID
    # `{anno}/{numero}`, through `invoices.naming.numero_completo`. The register keeps
    # two integers; this is the string printed on the document the client is holding.
    numero: str
    data_fattura: date
    data_scadenza: date
    giorni_di_ritardo: int
    importo: Decimal = Field(max_digits=12, decimal_places=2)
    cliente: str
    customer_id: UUID
    # How many reminders actually *left*, which is not how many rows exist: a reminder
    # prepared and never sent occupies a position in the sequence without having been
    # received by anybody.
    solleciti_inviati: int
    ultimo_sollecito_il: date | None
    # The tone the next reminder would carry, derived from `solleciti_inviati` and not
    # from the row count, so an unsent draft can never make the next letter open with
    # «nonostante il precedente sollecito».
    prossimo_livello: int
    ultima_risposta_il: date | None


class SollecitiPage(BaseModel):
    items: list[SollecitoCandidate]
    total: int


class PaymentReminderCreate(BaseModel):
    """The whole of the request: which invoice to chase.

    One field, and no room for a second. Everything the letter says -- the number, the
    two dates, the frozen `totale`, the IBAN, the tone of the sequence -- is derived from
    the register by `SollecitiService.create_reminder`, and a field here that let a caller
    override any of them would be a demand for payment naming a figure the client's own
    copy of the invoice does not carry. `extra="forbid"` is what makes that a refusal
    rather than a silently ignored key.
    """

    model_config = ConfigDict(extra="forbid")

    invoice_id: UUID


class PaymentReminderRead(BaseModel):
    """A reminder row, as the API and the composer see it.

    `email_draft_id` is the whole point of returning this: the caller sends through
    `/api/email-drafts/{id}/send` -- the one send path in the slice -- and nowhere else.
    `sent_at` is `None` on everything this endpoint creates, because creating a reminder
    sends nothing.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invoice_id: UUID
    sequence: int
    sent_at: datetime | None
    email_draft_id: UUID | None
    created_at: datetime
