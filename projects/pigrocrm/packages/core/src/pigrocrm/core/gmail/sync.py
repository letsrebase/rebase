"""The incremental cycle.

No daemon: the sync runs on request -- a button, `POST /api/gmail/sync`, or a
`docker compose run` from cron installed by the operator, the same scheme already
documented for the MCP server. This project has no worker process, and adding a queue
for one job is complexity that does not pay for itself today -- the same reasoning that
made slice 2's PDF render synchronous.

Three properties this module exists to hold:

* **Every listing carries an address filter.** Not by convention: the cycle's queries
  come from `build_list_queries`, which is fed the roster and nothing else, and the URL
  can only be built by `messages_list_url`, which refuses a `q` without an address
  clause. An empty roster therefore issues no request at all -- not one unfiltered
  request. Discovery (a customer's domain) and the customer proposals (the mailbox's
  own sent mail, `sent_since_query`) are the two listings built from something else,
  and both still name an address.
* **A conversation is stored whole.** The listing finds *which* threads are relevant;
  the thread endpoint then supplies all of their messages, including the ones from
  people the CRM has never heard of. A thread read halfway is worse than one not read:
  if the client writes, a colleague answers in copy and the client confirms, keeping
  only the first and the third produces a thread that lies. The addresses met that way
  do **not** enter the roster (spec 4.3), or relevance would widen by itself on every
  cycle.
* **Re-running is free.** The watermark is rolled back by the configured overlap on
  every cycle, so each run deliberately re-reads a day of stored mail; the unique
  constraint on `(google_account_id, gmail_message_id)` is what makes that cost one
  refused insert instead of a duplicate.

Nothing here logs, and no message body, subject or address is put into an exception. A
failure carries the counters and Google's own status, which is all a caller can act on.
"""

import re
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, decode_google_token_key, require_gmail_configured
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail.account import GoogleAccountService
from pigrocrm.core.gmail.crypto import unseal
from pigrocrm.core.gmail.errors import CredentialRevoked, GmailUnavailable, GoogleCallFailed
from pigrocrm.core.gmail.models import GmailMessage, GoogleAccount
from pigrocrm.core.gmail.parse import ParsedMessage, parse_message
from pigrocrm.core.gmail.query import (
    build_list_queries,
    customer_domain,
    discovery_query,
    is_provider_domain,
    messages_list_url,
    sent_since_query,
    thread_get_url,
    thread_metadata_url,
)
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.roster import AddressRoster, EntityRef
from pigrocrm.core.gmail.schemas import (
    SCOPE_READONLY,
    DiscoveredCorrespondent,
    DiscoveryReport,
    SuggestedCustomer,
    SuggestedPerson,
    SyncReport,
)
from pigrocrm.core.gmail.send import reconcile_only
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.people.models import Person

# How far back the first cycle looks when there is no watermark yet.
_FIRST_CYCLE_DAYS = 30

_SYNC_ACTION = "sincronizzare Gmail"
_BACKFILL_ACTION = "sincronizzare lo storico Gmail"
# Also the name under which `AGENT_FORBIDDEN_ACTIONS` lists it: an agent gets this only
# on an installation that opened `mcp_full_access`, like every other spend of the
# owner's own resources.
_DISCOVER_ACTION = "discover_gmail_correspondents"
_WHAT_LIST = "elenco dei messaggi"
_WHAT_THREAD = "lettura di una conversazione"

# The customer proposals (spec 2026-09-16 §5, REB-223). The action is also the name
# `AGENT_FORBIDDEN_ACTIONS` lists, for the reason `discover` is there: it spends the
# owner's Gmail quota under the owner's consent.
_SUGGEST_ACTION = "suggest_customers_from_gmail"
SUGGEST_MAX_MONTHS = 24
# How many of the most recent conversations one proposal reads, each one Gmail call for
# its headers. A year of a freelancer's sent mail is a few hundred threads, and the
# proposals are read while somebody waits on the Home, so the most recent ones stand
# for the period; a customer who wrote once, long ago, is not the one to import first.
SUGGEST_MAX_THREADS = 200
# Header reads in flight at once. This bounds concurrency, not rate: Gmail allows a user
# 250 quota units a second and a `threads.get` costs 10, and two reads of about a tenth
# of a second each stay near 200. `GmailTransport` retries a 429 with backoff anyway.
_SUGGEST_FETCH_WORKERS = 2
# How long the header reads may take before the proposals answer with what they have.
# Under the 60 seconds nginx gives an `/api` request, with the listing's own time and
# the answer on top: a slow Gmail gives a shorter list, not a 504 while the reads go on.
SUGGEST_READ_BUDGET_SECONDS = 35.0
# Gmail's own ceiling for one page of `messages.list`: fewer pages for a long year.
_SUGGEST_PAGE_SIZE = 500
SUGGEST_MAX_CUSTOMERS = 50
# Addresses that answer for a system, not a person: nobody to put on a customer.
_ROBOT_LOCAL_PART = re.compile(
    r"^(?:no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounces?|notifications?)(?:[+.\-].*)?$"
)
# Second-level labels that come before the organisation's own in a country domain
# (`acme.co.uk`), so the proposed name is the organisation's and not "Co".
_SECOND_LEVEL_LABELS = frozenset({"co", "com", "org", "net", "gov", "ac", "edu"})


@dataclass
class _Correspondent:
    """A running tally for one address while discovery reads threads."""

    count: int = 0
    last: datetime | None = None
    name: str = ""


@dataclass
class _ProposedDomain:
    """A running tally for one domain while the proposals read threads."""

    threads: set[str] = field(default_factory=set)
    last: datetime | None = None
    people: dict[str, str] = field(default_factory=dict)


def participants(parsed: ParsedMessage) -> list[str]:
    """Everybody a message names, each once, in header order: the sender, then To and
    Cc. What both discovery and the proposals tally, so the two cannot disagree on who
    took part in a conversation."""
    return list(dict.fromkeys([parsed.from_address, *parsed.to_addresses, *parsed.cc_addresses]))


def company_name_from_domain(domain: str) -> str:
    """A first guess at the company behind a domain, for a person to correct:
    `studio-rossi.it` is «Studio Rossi», `acme.co.uk` is «Acme»."""
    labels = [label for label in domain.lower().split(".") if label]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL_LABELS:
        core = labels[-3]
    elif len(labels) >= 2:
        core = labels[-2]
    else:
        core = labels[0] if labels else domain
    words = [word for word in re.split(r"[-_]+", core) if word]
    return " ".join(word[:1].upper() + word[1:] for word in words) or domain


class GmailSyncService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        transport: GmailTransport,
        tokens: GoogleTokenClient,
    ) -> None:
        self.session = session
        self.settings = settings
        self.transport = transport
        self.tokens = tokens
        self.repo = GmailRepository(session)
        self.roster = AddressRoster(session)
        self.activities = ActivityService(session)
        self.accounts = GoogleAccountService(session, settings=settings)

    def sync(self, actor: Actor) -> SyncReport:
        require_gmail_configured(self.settings)
        actor.require_write(_SYNC_ACTION)
        account = self._account(actor)
        # Before the lock, deliberately. A credential that is revoked, expired or was
        # never granted `gmail.readonly` cannot produce a cycle, and refusing it here
        # means the refusal costs nothing and -- because no lock was taken -- cannot
        # make the *next* call answer "già in corso".
        self.accounts.usable(actor, scope=SCOPE_READONLY, feature="la sincronizzazione")
        started_at = datetime.now(UTC)

        if not self.repo.try_sync_lock(account.id):
            # A cron every fifteen minutes and a human pressing the button is not a
            # hypothetical. Not an error and not a wait: the second caller gets a report
            # that says what is happening and since when, and spends nothing -- no token
            # refresh, no listing, no thread fetch.
            return SyncReport(
                started_at=started_at,
                already_running=True,
                running_since=self.repo.sync_started_at(account.id),
            )
        try:
            return self._run_cycle(account, actor, started_at)
        finally:
            # `finally`, so a raised cycle does not leak the lock. A leaked advisory lock
            # travels back into the connection pool with its connection and makes every
            # later sync for this mailbox answer "already running" for the life of the
            # process, which is indistinguishable from a hung job.
            self.repo.release_sync_lock(account.id)

    def _run_cycle(self, account: GoogleAccount, actor: Actor, started_at: datetime) -> SyncReport:
        """One whole cycle, holding the mailbox's lock from the first statement to the
        commit. Never called except through `sync`, which owns that lock."""
        report = SyncReport(started_at=started_at)

        report.states_pruned = self.repo.prune_states(started_at)

        # Before anything else: resolve any send whose outcome we do not know (spec 6.3
        # point 3, «la riconciliazione gira all'inizio di ogni sync»). First, and not
        # last, for two reasons. A draft left `incerto` because a human never pressed
        # «verifica» is a state that stays wrong, and this is the only thing that
        # resolves it without one; and doing it here means the outbound row for an
        # adopted message exists *before* this cycle's own listing reaches the same
        # conversation, so the two agree instead of racing over the unique constraint.
        report.reconciled = reconcile_only(
            self.session, settings=self.settings, transport=self.transport, tokens=self.tokens
        ).reconcile_all(account.id, actor)

        # Two windows, not one. An address the roster gained since the last cycle has a
        # history behind it that no watermark has ever covered, so it is searched over
        # the backfill horizon; an established one is searched only from the watermark,
        # because re-reading three months of it every fifteen minutes would spend the
        # user's Gmail quota to learn nothing. The difference is read from
        # `gmail_known_addresses` and not guessed from `created_at`: a person may have
        # been in the CRM for a year before this mailbox was connected.
        addresses = self.roster.known_addresses()
        seen = self.repo.seen_addresses(account.id)
        fresh = [address for address in addresses if address not in seen]
        established = [address for address in addresses if address in seen]

        queries: list[str] = []
        if established:
            queries.extend(
                build_list_queries(
                    established,
                    after_epoch=int(self._window_start(account).timestamp()),
                    batch_size=self.settings.gmail_sync_address_batch_size,
                )
            )
        if fresh:
            queries.extend(
                build_list_queries(
                    fresh,
                    after_epoch=self._backfill_epoch(full=False),
                    batch_size=self.settings.gmail_sync_address_batch_size,
                )
            )
        report.queries_issued = len(queries)

        if queries:
            # The token is fetched here and not above it: an empty roster must produce
            # no request to Google at all, and a refresh is a request.
            token = self._access_token(account)
            self._ingest(account, actor, tuple(queries), token, report)

        if fresh:
            # After the ingest, deliberately: an address is "already looked for" only
            # once the looking has happened. Recording it first and then failing on the
            # token refresh would mark a backfill done that never ran, and nothing would
            # ever try it again.
            self.repo.remember_addresses(account.id, fresh)
            # Recorded, because a backfill is a wider and slower cycle than the user
            # asked for and its result reads differently: "nothing new" and "we looked
            # back three months and there was nothing" are two different answers. Only
            # counts go in -- no address, for the same reason `SyncReport` has no room
            # for one.
            self.activities.record(
                "google_account",
                account.id,
                "gmail.backfill_eseguito",
                actor,
                {"addresses": len(fresh), "days": self.settings.gmail_backfill_days},
            )

        account.last_sync_at = started_at
        # The watermark is rolled back by the configured overlap on every cycle. It is
        # free because the unique constraint makes re-insertion idempotent, and it is
        # what absorbs a message that arrived across the boundary of two runs.
        account.sync_watermark = started_at - timedelta(
            hours=self.settings.gmail_watermark_overlap_hours
        )
        # Last, deliberately: `ActivityService.record` flushes into the caller's
        # transaction and its docstring forbids anything that commits on its own behalf
        # from running after it. Everything below this line is the commit itself.
        #
        # Only the counters go in. There is no subject, no address and no body in a
        # `SyncReport`, which is what makes this entry safe to show and to hand to an
        # agent -- the cycle is recorded, not its contents.
        self.activities.record(
            "google_account",
            account.id,
            "gmail.sync_eseguito",
            actor,
            {
                "queries_issued": report.queries_issued,
                "threads_fetched": report.threads_fetched,
                "messages_stored": report.messages_stored,
                "messages_skipped": report.messages_skipped,
                "links_created": report.links_created,
            },
        )
        self.session.commit()
        return report

    def backfill(
        self, entity_type: str, entity_id: UUID, *, full: bool, actor: Actor
    ) -> SyncReport:
        """One entity's correspondence, on demand.

        `full=True` drops the time horizon and keeps the address filter: "no horizon" is
        about time, never about relevance. It is explicit and human-initiated because on
        a ten-year mailbox it is slow, and nobody wants it by accident -- which is why
        it is not what the automatic path of `_run_cycle` does.

        It reads *backwards*, and therefore deliberately touches neither `last_sync_at`
        nor `sync_watermark`. Advancing the watermark here would make the next ordinary
        cycle skip everything that arrived while the backfill was running: a hole in the
        one direction nobody would think to look.
        """
        require_gmail_configured(self.settings)
        actor.require_write(_BACKFILL_ACTION)
        account = self._account(actor)
        # Before the lock and before the entity lookup: a dead credential is the more
        # actionable of the two problems, and answering it costs nothing.
        self.accounts.usable(actor, scope=SCOPE_READONLY, feature="la sincronizzazione")
        addresses = self._addresses_of(entity_type, entity_id)
        if not addresses:
            # A report of zero would be indistinguishable from "we looked and there was
            # nothing", which is the answer to a different question entirely.
            raise Conflict(
                "gmail_backfill",
                f"{entity_type} {entity_id} non ha nessun indirizzo email da sincronizzare",
            )

        started_at = datetime.now(UTC)
        if not self.repo.try_sync_lock(account.id):
            return SyncReport(
                started_at=started_at,
                already_running=True,
                running_since=self.repo.sync_started_at(account.id),
            )
        try:
            report = SyncReport(started_at=started_at)
            queries = build_list_queries(
                addresses,
                after_epoch=self._backfill_epoch(full=full),
                batch_size=self.settings.gmail_sync_address_batch_size,
            )
            report.queries_issued = len(queries)
            token = self._access_token(account)
            self._ingest(account, actor, queries, token, report)
            # These addresses have now been searched at least as far back as the
            # automatic backfill would have gone, so the next cycle must not do it
            # again -- and on `full=True` it went further still.
            self.repo.remember_addresses(account.id, addresses)
            self.activities.record(
                "google_account",
                account.id,
                "gmail.backfill_eseguito",
                actor,
                {
                    "entity_type": entity_type,
                    "entity_id": str(entity_id),
                    "full": full,
                    "messages_stored": report.messages_stored,
                },
            )
            self.session.commit()
            return report
        finally:
            self.repo.release_sync_lock(account.id)

    def discover(self, customer_id: UUID, *, actor: Actor) -> DiscoveryReport:
        """Who at this customer's domain the connected mailbox has actually written to.

        The step *before* the roster: a new customer with a website and no people gives
        the backfill nothing to ask about, and the alternative was guessing addresses
        one at a time. One query, scoped to the domain taken from the customer record --
        `discovery_query` refuses anything else, and nothing typed by the caller reaches
        the `q` -- and the answer is a list of addresses for a person to add.

        Stores nothing and moves no watermark. Spec 4.2 is kept whole: the mirror
        widens when somebody puts an address on a Person, not when this finds one.
        Threads are read in full like everywhere else, so that a colleague who only
        ever appeared in copy is found too.
        """
        require_gmail_configured(self.settings)
        actor.require_write(_DISCOVER_ACTION)
        account = self._account(actor)
        self.accounts.usable(actor, scope=SCOPE_READONLY, feature="la ricerca dei corrispondenti")
        customer = self.session.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None:
            raise NotFound("customer", customer_id)
        domain = customer_domain(sito_web=customer.sito_web, email=customer.email)
        if domain is None:
            raise Conflict(
                "gmail_discovery",
                f"il cliente {customer_id} non ha un dominio da cui partire: indica il sito "
                "web o un'email aziendale (non webmail) sulla scheda cliente",
            )
        report = DiscoveryReport(started_at=datetime.now(UTC), dominio=domain)
        token = self._access_token(account)
        mailbox = account.email_address.strip().lower()
        suffix = f"@{domain}"
        tally: dict[str, _Correspondent] = {}
        for thread_id in self._relevant_threads((discovery_query(domain),), token):
            report.threads_scanned += 1
            payload = self.transport.json(
                "GET", thread_get_url(thread_id), token=token, what=_WHAT_THREAD
            )
            for raw in payload.get("messages") or []:
                if not isinstance(raw, dict):
                    continue
                # Bodies are never decoded here: a name and an address are all this
                # needs, and reading the text of mail nobody chose to store is not
                # what discovery is for.
                parsed = parse_message(raw, body_max_bytes=0, store_bodies=False)
                report.messages_seen += 1
                for address in participants(parsed):
                    if not address.endswith(suffix) or address == mailbox:
                        continue
                    entry = tally.setdefault(address, _Correspondent())
                    entry.count += 1
                    if entry.last is None or parsed.internal_date > entry.last:
                        entry.last = parsed.internal_date
                    if not entry.name:
                        entry.name = parsed.display_names.get(address, "")
        known = set(self.roster.known_addresses())
        report.corrispondenti = [
            DiscoveredCorrespondent(
                indirizzo=address,
                nome=entry.name,
                messaggi=entry.count,
                ultimo_messaggio=entry.last,
                gia_in_anagrafica=address in known,
            )
            for address, entry in sorted(tally.items(), key=lambda item: (-item[1].count, item[0]))
        ]
        return report

    def suggest_customers(self, *, actor: Actor, mesi: int = 12) -> list[SuggestedCustomer]:
        """The organisations the connected mailbox has corresponded with over the last
        `mesi` months, proposed as customers (spec 2026-09-16 §5, REB-223).

        Read from the mail the owner sent (`sent_since_query`): each conversation the
        owner wrote in, as its headers only, and every address it names grouped by
        domain, with the number of conversations, the last message and the people seen.
        Left out: the mailbox's own domain, mail providers and PEC domains
        (`is_provider_domain`), addresses that answer for a system, and domains the CRM
        already files under a customer (`AddressRoster.customer_domains`). The busiest
        come first, at most `SUGGEST_MAX_CUSTOMERS`.

        Stores nothing and moves no watermark, like `discover`: a proposal becomes a
        customer only through `CustomerService.create_from_suggestions`, and the mirror
        widens only when those people are in the address book.
        """
        require_gmail_configured(self.settings)
        actor.require_write(_SUGGEST_ACTION)
        if not 1 <= mesi <= SUGGEST_MAX_MONTHS:
            raise ValidationFailed(
                "gmail_suggestions",
                "mesi",
                f"le proposte leggono da 1 a {SUGGEST_MAX_MONTHS} mesi di posta",
                expected=f"1-{SUGGEST_MAX_MONTHS}",
            )
        account = self._account(actor)
        self.accounts.usable(actor, scope=SCOPE_READONLY, feature="i clienti proposti")
        mailbox = account.email_address.strip().lower()
        own_domain = mailbox.rpartition("@")[2]
        since = datetime.now(UTC) - timedelta(days=round(mesi * 365 / 12))
        token = self._access_token(account)

        try:
            thread_ids = self._sent_threads(mailbox, since, token)
            payloads = self._read_headers(thread_ids, token)
        except GoogleCallFailed as failed:
            # Gmail failing after every retry is the person's to retry, not a server
            # error: the same `GmailUnavailable` a failed token refresh answers.
            raise GmailUnavailable(
                "la lettura della posta inviata", failed.failure.status
            ) from failed

        skip = self.roster.customer_domains() | {own_domain}
        tally: dict[str, _ProposedDomain] = {}
        for thread_id, payload in payloads:
            for raw in payload.get("messages") or []:
                if not isinstance(raw, dict):
                    continue
                parsed = parse_message(raw, body_max_bytes=0, store_bodies=False)
                for address in participants(parsed):
                    local, _, domain = address.rpartition("@")
                    if (
                        not local
                        or address == mailbox
                        or domain in skip
                        or is_provider_domain(domain)
                        or _ROBOT_LOCAL_PART.match(local)
                    ):
                        continue
                    entry = tally.setdefault(domain, _ProposedDomain())
                    entry.threads.add(thread_id)
                    if entry.last is None or parsed.internal_date > entry.last:
                        entry.last = parsed.internal_date
                    name = parsed.display_names.get(address, "")
                    if not entry.people.get(address):
                        entry.people[address] = name

        known = set(self.roster.known_addresses())
        proposals = [
            SuggestedCustomer(
                dominio=domain,
                nome=company_name_from_domain(domain),
                conversazioni=len(entry.threads),
                ultimo_messaggio=entry.last,
                persone=[
                    SuggestedPerson(
                        indirizzo=address, nome=name, gia_in_anagrafica=address in known
                    )
                    for address, name in sorted(entry.people.items())
                ],
            )
            for domain, entry in tally.items()
        ]
        proposals.sort(
            key=lambda proposal: (
                -proposal.conversazioni,
                -(proposal.ultimo_messaggio.timestamp() if proposal.ultimo_messaggio else 0),
                proposal.dominio,
            )
        )
        return proposals[:SUGGEST_MAX_CUSTOMERS]

    # ---- internals ---------------------------------------------------------------

    def _sent_threads(self, mailbox: str, since: datetime, token: str) -> list[str]:
        """The conversations the mailbox wrote in since `since`, newest first as Gmail
        lists them, each once, at most `SUGGEST_MAX_THREADS`: the listing stops paging
        as soon as that many are known."""
        query = sent_since_query(mailbox, after_epoch=max(1, int(since.timestamp())))
        thread_ids: list[str] = []
        seen: set[str] = set()
        page_token: str | None = None
        while True:
            payload = self.transport.json(
                "GET",
                messages_list_url(query, page_token=page_token, max_results=_SUGGEST_PAGE_SIZE),
                token=token,
                what=_WHAT_LIST,
            )
            for entry in payload.get("messages") or []:
                thread_id = str(entry.get("threadId") or "") if isinstance(entry, dict) else ""
                if thread_id and thread_id not in seen:
                    seen.add(thread_id)
                    thread_ids.append(thread_id)
                    if len(thread_ids) >= SUGGEST_MAX_THREADS:
                        return thread_ids
            next_token = payload.get("nextPageToken")
            # The same guard as `_list_all`: only a *new* page token advances the loop.
            page_token = str(next_token) if next_token and str(next_token) != page_token else None
            if page_token is None:
                return thread_ids

    def _read_headers(self, thread_ids: list[str], token: str) -> list[tuple[str, dict[str, Any]]]:
        """Each conversation's headers, in the listing's order, two reads at a time.

        Every read that finished within `SUGGEST_READ_BUDGET_SECONDS` is used, whatever
        its place in the listing; the reads still queued at the budget are cancelled. A
        conversation deleted between the listing and its read (404) is skipped; any
        other failure cancels what is queued and is raised. A read already on the wire
        cannot be recalled, so at most `_SUGGEST_FETCH_WORKERS` of them finish after the
        answer, each within the transport's own timeout. The worker threads touch the
        transport only, never the session."""
        deadline = time.monotonic() + SUGGEST_READ_BUDGET_SECONDS
        pool = ThreadPoolExecutor(max_workers=_SUGGEST_FETCH_WORKERS)
        futures = [
            (thread_id, pool.submit(self._thread_headers, thread_id, token))
            for thread_id in thread_ids
        ]
        pending = {future for _, future in futures}
        try:
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(pending, timeout=remaining, return_when=FIRST_EXCEPTION)
                for future in done:
                    failure = future.exception()
                    if failure is None:
                        continue
                    if isinstance(failure, GoogleCallFailed) and failure.failure.status == 404:
                        continue
                    raise failure
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return [
            (thread_id, future.result())
            for thread_id, future in futures
            if future.done() and not future.cancelled() and future.exception() is None
        ]

    def _thread_headers(self, thread_id: str, token: str) -> dict[str, Any]:
        return self.transport.json(
            "GET", thread_metadata_url(thread_id), token=token, what=_WHAT_THREAD
        )

    def _backfill_epoch(self, *, full: bool) -> int:
        """`after:` for a backfill. `0` is the beginning of the mailbox, which
        `build_list_queries` accepts and a negative number would not."""
        if full:
            return 0
        horizon = datetime.now(UTC) - timedelta(days=self.settings.gmail_backfill_days)
        return max(0, int(horizon.timestamp()))

    def _addresses_of(self, entity_type: str, entity_id: UUID) -> list[str]:
        """The addresses one entity is reachable at.

        Raises `NotFound` for an entity that does not exist or has been archived, so a
        typo in an id is a 404 rather than a silent backfill of nothing -- and so that
        archiving somebody is enough to stop the CRM going off to read their mail.

        A customer brings its people with it: a client is an organisation, and
        backfilling "Acme" while ignoring the person one actually writes to would return
        an empty result that looks like an answer.
        """
        if entity_type == "person":
            person = self.session.get(Person, entity_id)
            if person is None or person.deleted_at is not None:
                raise NotFound("person", entity_id)
            return [person.email.strip().lower()] if person.email else []
        if entity_type == "customer":
            customer = self.session.get(Customer, entity_id)
            if customer is None or customer.deleted_at is not None:
                raise NotFound("customer", entity_id)
            addresses = [customer.email.strip().lower()] if customer.email else []
            addresses.extend(
                email.strip().lower()
                for email in self.session.execute(
                    select(Person.email).where(
                        Person.customer_id == entity_id,
                        Person.email.is_not(None),
                        Person.deleted_at.is_(None),
                    )
                )
                .scalars()
                .all()
                if email
            )
            return list(dict.fromkeys(addresses))
        # A deal has no address of its own -- it borrows its customer's -- so the answer
        # names the two types that do rather than returning an empty list.
        raise ValidationFailed(
            "gmail_backfill",
            "entity_type",
            "il backfill si esegue su una persona o su un cliente",
            expected="person | customer",
        )

    def _ingest(
        self,
        account: GoogleAccount,
        actor: Actor,
        queries: tuple[str, ...],
        token: str,
        report: SyncReport,
    ) -> None:
        """List, then fetch each thread whole, then file it.

        One code path for the ordinary cycle and for the explicit backfill, which is
        what keeps the guarantees of spec 4 single-sourced: the two differ only in the
        `after:` of their queries, and every query either builds came out of
        `build_list_queries` with an address list. A second copy of this loop would be a
        second place for an unfiltered listing to appear.
        """
        for thread_id in self._relevant_threads(queries, token):
            report.threads_fetched += 1
            self._store_thread(account, thread_id, token, report, actor)

    def _account(self, actor: Actor) -> GoogleAccount:
        if actor.id is None:
            raise Conflict("google_account", "solo un utente può sincronizzare una casella")
        account = self.repo.account_for_user(actor.id)
        if account is None:
            raise Conflict("google_account", "nessuna casella Google collegata")
        return account

    def _access_token(self, account: GoogleAccount) -> str:
        refresh_token = unseal(
            account.refresh_token_ciphertext,
            account.refresh_token_nonce,
            decode_google_token_key(self.settings),
        )
        try:
            return self.tokens.access_token(
                account_id=account.id,
                email_address=account.email_address,
                refresh_token=refresh_token,
            )
        except CredentialRevoked:
            # The one place in the system that can *learn* this: only a refresh gets
            # `invalid_grant` back. Recorded here, therefore, and not swallowed --
            # `mark_revoked` commits the state and the timeline entry on its own behalf
            # so the fact outlives this cycle's rollback, and then the exception
            # continues so the caller stops rather than carrying on against a dead
            # credential.
            #
            # The sentence is written here rather than derived from the exception: it is
            # what the user reads, so it carries no error code, no upstream prose and no
            # token.
            GoogleAccountService(self.session, settings=self.settings).mark_revoked(
                account,
                Actor.system(),
                f"Il consenso Google per {account.email_address} è stato revocato: "
                "ricollega la casella da Impostazioni → Gmail.",
            )
            raise

    def _window_start(self, account: GoogleAccount) -> datetime:
        if account.sync_watermark is not None:
            return account.sync_watermark
        return datetime.now(UTC) - timedelta(days=_FIRST_CYCLE_DAYS)

    def _relevant_threads(self, queries: tuple[str, ...], token: str) -> list[str]:
        """The thread ids the address filter found, deduplicated in order.

        Deduplicated because two addresses of the same customer routinely appear in one
        conversation, and fetching that thread twice would double the cost of the
        cycle for nothing.
        """
        thread_ids: list[str] = []
        seen: set[str] = set()
        for query in queries:
            for entry in self._list_all(query, token):
                thread_id = str(entry.get("threadId") or "")
                if thread_id and thread_id not in seen:
                    seen.add(thread_id)
                    thread_ids.append(thread_id)
        return thread_ids

    def _list_all(self, query: str, token: str) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        page_token: str | None = None
        while True:
            payload = self.transport.json(
                "GET",
                messages_list_url(query, page_token=page_token),
                token=token,
                what=_WHAT_LIST,
            )
            entries.extend(
                entry for entry in (payload.get("messages") or []) if isinstance(entry, dict)
            )
            next_token = payload.get("nextPageToken")
            # A page token that repeats itself would loop forever against a broken or
            # hostile server, so the loop advances only on a *new* one.
            page_token = str(next_token) if next_token and str(next_token) != page_token else None
            if page_token is None:
                return entries

    def _store_thread(
        self,
        account: GoogleAccount,
        thread_id: str,
        token: str,
        report: SyncReport,
        actor: Actor,
    ) -> None:
        payload = self.transport.json(
            "GET", thread_get_url(thread_id), token=token, what=_WHAT_THREAD
        )
        raw_messages = [raw for raw in (payload.get("messages") or []) if isinstance(raw, dict)]
        parsed_messages = [
            parse_message(
                raw,
                body_max_bytes=self.settings.gmail_body_max_bytes,
                store_bodies=account.gmail_store_bodies,
            )
            for raw in raw_messages
        ]
        thread_refs = self._thread_refs(parsed_messages)
        # One membership query for the whole conversation: after the first cycle almost
        # every thread comes back entirely known, and asking that per message would
        # spend a round trip apiece to learn nothing.
        present = self.repo.message_ids_present(
            account.id, [parsed.gmail_message_id for parsed in parsed_messages]
        )
        for parsed in parsed_messages:
            if not parsed.gmail_message_id or parsed.gmail_message_id in present:
                report.messages_skipped += 1
                continue
            row = self._store(account, parsed)
            if row is None:
                report.messages_skipped += 1
                continue
            report.messages_stored += 1
            self._file(account, parsed, row, thread_refs, actor, report)

    def _thread_refs(self, parsed_messages: list[ParsedMessage]) -> list[EntityRef]:
        """Every CRM entity the *conversation* touches, in the order it was met.

        Computed once for the thread and not per message, which is the whole of spec
        4.3 in one place: a sibling message from somebody nobody registered is filed
        against the same customer as the rest of the exchange, because it is part of
        that exchange. A per-message resolution would file half the thread and leave the
        other half invisible, which is the conversation that lies.

        Resolving does not widen relevance: `AddressRoster.resolve` only ever answers
        with entities that already exist, and an address met inside a thread still does
        not join `known_addresses`.
        """
        refs: list[EntityRef] = []
        for parsed in parsed_messages:
            for address in [parsed.from_address, *parsed.to_addresses, *parsed.cc_addresses]:
                for ref in self.roster.resolve(address):
                    if ref not in refs:
                        refs.append(ref)
        return refs

    def _file(
        self,
        account: GoogleAccount,
        parsed: ParsedMessage,
        row: GmailMessage,
        thread_refs: list[EntityRef],
        actor: Actor,
        report: SyncReport,
    ) -> None:
        """Links one stored message to the thread's entities, and puts an inbound one on
        their timelines.

        Only inbound: `gmail.messaggio_ricevuto` is a claim about direction, and an
        entry written for our own reply would tell the user their message had arrived.

        The payload carries the subject and the sender and stops there. That is the case
        `activities/sanitize.py` names in its own docstring -- "recording, say, an
        inbound email's subject line" -- and it is also the limit: a body in a timeline
        entry is published to the UI, to the REST timeline route and to `get_timeline`.
        """
        for ref in thread_refs:
            if self.repo.add_link(row.id, ref):
                report.links_created += 1
        if not parsed.direction_is_inbound(account.email_address):
            return
        for ref in thread_refs:
            self.activities.record(
                ref.entity_type,
                ref.entity_id,
                "gmail.messaggio_ricevuto",
                actor,
                {
                    "subject": parsed.subject,
                    "from_address": parsed.from_address,
                    "gmail_message_id": parsed.gmail_message_id,
                    "gmail_thread_id": parsed.gmail_thread_id,
                },
            )

    def _store(self, account: GoogleAccount, parsed: ParsedMessage) -> GmailMessage | None:
        """Returns the stored row, or `None` when the constraint refused it. A duplicate
        is not an error: it is the overlap doing its job, or a second cycle running at
        the same time.

        The row and not a flag, because the caller has to file it: a link needs the
        primary key the insert has just assigned.

        The `message_ids_present` check above never replaces the constraint -- two
        overlapping cycles both pass it, and only the database can arbitrate -- which is
        why the insert itself answers rather than raising.
        """
        row = GmailMessage(
            google_account_id=account.id,
            gmail_message_id=parsed.gmail_message_id,
            gmail_thread_id=parsed.gmail_thread_id,
            message_id_header=parsed.message_id_header[:998],
            in_reply_to=parsed.in_reply_to[:998],
            references=parsed.references,
            direction=(
                "inbound" if parsed.direction_is_inbound(account.email_address) else "outbound"
            ),
            from_address=parsed.from_address,
            to_addresses=list(parsed.to_addresses),
            cc_addresses=list(parsed.cc_addresses),
            subject=parsed.subject[:998],
            snippet=parsed.snippet[:500],
            internal_date=parsed.internal_date,
            body_text=parsed.body_text,
            body_truncated=parsed.body_truncated,
            body_html_scartato=parsed.body_html_scartato,
            attachments=[
                {"filename": a.filename, "mime": a.mime, "size": a.size} for a in parsed.attachments
            ],
        )
        return row if self.repo.add_message_if_absent(row) else None
