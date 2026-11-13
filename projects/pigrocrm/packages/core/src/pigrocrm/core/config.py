from functools import lru_cache
from typing import Literal
from zoneinfo import available_timezones

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pigrocrm.core.crypto import decode_aes256_key
from pigrocrm.core.errors import Conflict

# RFC 7518 Section 3.2: an HS256 key shorter than 32 bytes is weaker than the
# algorithm's own output size. PyJWT already warns about this; validating here turns
# a silent warning (easy to miss in production logs) into a startup failure instead.
MIN_JWT_SECRET_LENGTH = 32

# The bearers other services present to the registry and engagements doors are shared
# secrets between two deployments: as long as the signing key, for the same reason.
MIN_SERVICE_TOKEN_LENGTH = 32

# What `jwt_secret` is when nothing configures it. Public, so a signing key that still
# holds it is forgeable: `require_production_secrets` refuses it at API boot, and the
# default itself stays so that a bare `Settings()` (a CLI command, a test) builds.
DEFAULT_JWT_SECRET = "change-me-in-production-please-set-a-real-secret"
# The placeholders a copied `.env.example` or a default carries: none of them is a secret.
KNOWN_PLACEHOLDER_JWT_SECRETS = frozenset(
    {DEFAULT_JWT_SECRET, "cambia-questo-con-un-segreto-vero-di-almeno-32-caratteri"}
)

# The default ceiling on the text of one file, wherever it was read from: a file on
# Drive (`drive/reader.py::read_text`) or a document archived in the CRM
# (`documents/service.py::read_text`). One number for both, because it answers one
# question -- how much of somebody else's document a single answer may carry.
# A module constant as well as a field default because `DriveReader` takes the number,
# not a `Settings`: it is a reader of Drive, not of this installation's configuration,
# and `drive_reader_for` is the one place the two meet. Same 256 KB as
# `gmail_body_max_bytes`, and for the same reason -- a generous limit that only bites
# on the anomalous, so that what a person reads is the document and not a policy.
TEXT_MAX_BYTES_DEFAULT = 262_144


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PIGROCRM_", env_file=".env", extra="ignore")

    # Which of these an installation chooses: the ones `docker-compose.yml` forwards in
    # `x-api-environment`. The rest hold their default on every deploy, and
    # `tests/test_compose_environment.py::NOT_FORWARDED` records why for each one; that test
    # fails a new field that is in neither place.
    #
    # A `Settings` reaches logs, tracebacks and debugger frames, so every credential in it
    # (the database URLs, which carry passwords, the signing key, the bearer tokens, the
    # client secrets, the API keys) is `repr=False`. Ids and public URLs are not secrets and
    # stay visible: hiding them would only make a misconfiguration harder to read.

    database_url: str = Field(
        default="postgresql+psycopg://pigrocrm:pigrocrm@localhost:5432/pigrocrm", repr=False
    )
    # The owner's URL, for what the application role may not do: `alembic upgrade`,
    # `ensure-space-defaults`, `CREATE DATABASE` and the grants themselves (REB-634, spec
    # 2026-10-03 §4 «The role»). `database_url` above is the role every request runs as,
    # `pigrocrm_app` in the compose stack, which Postgres keeps inside the row-level
    # policies; the owner and a superuser are not kept there. Empty means one URL for
    # both, which is what a development checkout and the test suite run with: then no
    # second role exists, nothing is created at boot, and a superuser sees every row.
    admin_database_url: str = Field(default="", repr=False)
    # The registry of spaces -- which slug maps to which database; see
    # docs/superpowers/specs/2026-09-08-spazi-un-database-per-tenant-design.md. Empty
    # means the CRM's own server and credentials, database `pigrocrm_tenants`. Each
    # space's own database is created beside it by `tenants.service.TenantService.provision`.
    tenants_database_url: str = Field(default="", repr=False)
    # Where `alembic.ini` lives, for migrating a freshly created space's database with
    # the same env.py production runs. Empty resolves to packages/core/alembic.ini next
    # to this package, which is where both the checkout and the API image keep it.
    tenants_alembic_ini: str = ""
    # The root installation's own name as a space, e.g. `studiorossi`: `/<root_slug>/app`
    # and `/<root_slug>/api` are the root itself -- same database, same Gmail and Drive,
    # same cookies -- so the owner's CRM has an address shaped like everyone else's.
    # Empty means the root answers only without a prefix. Reserved for signups when set.
    root_slug: str = ""
    # The most spaces the registry may hold: the public signup (`POST /api/tenants/`) refuses
    # once it does, because each space is a database and a migration run on the same server.
    max_spaces: int = Field(default=1000, ge=1)
    # Lets another service read the registry of spaces: `GET /api/tenants/` answers a
    # caller presenting it as a bearer token with every row (slug, owner, date), which is
    # how the rebase hub shows which spaces exist and whose they are (ORB-142). Empty,
    # the default, means the route does not exist: a self-hosted installation exposes
    # nothing new. Read from the environment only, never from `space_settings`. A secret
    # the same way `google_client_secret` is, so `repr=False` keeps it out of logs.
    registry_token: str = Field(default="", repr=False)
    # The bearer the engagements door checks (`/api/rebase/engagements/...`, milestone A,
    # REB-490): the hub presents it to set up a freelancer's space, customer and deal for
    # a signed engagement, and to read its hours back. Empty, the default, means the
    # routes do not exist -- the same shape as `registry_token` above. Read from the
    # environment only, never from `space_settings`, and `repr=False` for the same reason.
    engagements_token: str = Field(default="", repr=False)
    # The slug of rebase's own space, the only one the leads route
    # (`PUT /api/rebase/leads/{request_id}`, REB-866, under `engagements_token`) writes to:
    # the hub files each completed company request there as customer, contact and deal.
    # The route never takes a slug from the caller. Empty, the default, means the route
    # answers 503 «Spazio dei lead non configurato».
    leads_space: str = ""
    # Where the rebase hub answers, for the one question the signup asks it: whether an
    # address belongs to a community member (`tenants/hub.py`, ORB-173). The bearer is
    # `registry_token` above, the same value the hub reads as
    # `REBASE_PIGRO_REGISTRY_TOKEN`: since ORB-173 it travels in both directions. Empty
    # token, unreachable hub, anything but a 200: the answer is «not a member» and the
    # signup goes on. The community is the fast lane, never a gate.
    hub_url: str = "https://letsrebase.com"
    # The bearer the CRM presents to the hub for what it tells it about engagements: the notice
    # outbox's deliveries (`POST {hub_url}/api/hub/engagement-notices`) and the referrer's name
    # a letter prints (`POST {hub_url}/api/hub/referrer-name`, REB-1106, collaborators design
    # § 5). Its own value, never `engagements_token` (which opens the CRM to the hub) and never
    # `registry_token`: set identically as `REBASE_PIGRO_NOTICES_TOKEN` in the hub's `.env`. It
    # travels only over HTTPS, plain HTTP only to this machine. Empty, the default, means the
    # CRM tells the hub nothing: the outbox keeps its rows for the day the token exists, and a
    # letter prints «nessuno» for the referrer. Environment only, `repr=False` like the others.
    hub_notices_token: str = Field(default="", repr=False)
    # --- Signing: Documenso (REB-1019, collaborators design § 2). One Documenso serves the
    # whole installation, configured here and never from a screen: the URL as this process
    # reaches it, the API token of the Documenso user and team the installation signs through
    # (a token reads and cancels every envelope of its user's teams), and the secret typed into
    # Documenso's webhook, which Documenso sends verbatim as `X-Documenso-Secret`. The URL and
    # the token together switch signing on; without them a letter is downloaded, signed by hand
    # and its signed PDF uploaded, and nothing in this installation calls a Documenso. The
    # secret alone switches the webhook route on: empty, it answers 503. Every one is empty by
    # default and the compose file forwards them empty, so an installation that does not sign
    # boots exactly as before. The token and the secret are credentials, so `repr=False`.
    documenso_url: str = ""
    documenso_api_token: str = Field(default="", repr=False)
    documenso_webhook_secret: str = Field(default="", repr=False)
    # The HS256 signing key of every token. Public until an operator sets it: the default
    # builds a `Settings` for the CLI and the tests, and `require_production_secrets` is what
    # refuses to serve with it. Compose requires the variable (`:?`).
    jwt_secret: str = Field(default=DEFAULT_JWT_SECRET, repr=False)
    # The access token's lifetime is the revocation window (see `refresh_token_days`); a
    # day is already far more than a revocation window should be.
    access_token_minutes: int = Field(default=15, ge=1, le=1440)
    # Six months, sliding: `/api/auth/refresh` consumes the old jti and issues a new row
    # whose expiry is measured from now, so anyone who uses the CRM never sees the login
    # again and anyone who leaves it for six months does (spec 9 §5.6). The access token
    # stays at fifteen minutes: that is the revocation window, not the session length.
    refresh_token_days: int = Field(default=180, ge=1, le=3650)
    # The cross-space identity cookie's own lifetime (design 2026-09-23 §2, REB-376):
    # deliberately the same 180 days as refresh_token_days, because it is meant to
    # outlive any single space's own session -- the one artifact in that design built
    # to survive a switch between spaces without asking for a fresh proof each time.
    # Revocable through `IdentitySession.jti`, the same shape `refresh_token_days`
    # already has through `RefreshToken.jti`.
    identity_token_days: int = Field(default=180, ge=1, le=3650)
    # Must stay True in production: it is what stops the auth cookies from ever being
    # sent over plain HTTP. It exists as a *setting* rather than a hardcoded True only
    # because of one browser: Chrome and Firefox treat "localhost" as a secure context
    # and accept a `Secure` cookie over plain HTTP there, but Safari does not and has
    # no plan to. Local development (slice 1B's Vite proxy) serves the API over
    # http://localhost with no TLS, so without an escape hatch, login on Safari in dev
    # would return 200 while the browser silently discarded the cookie -- every
    # request after that looks unauthenticated with no error anywhere to explain why.
    # Set PIGROCRM_COOKIE_SECURE=false for that one case. Anyone tempted to flip this
    # in production because "it's just a flag" should re-read this paragraph first.
    cookie_secure: bool = True
    # Browser origins, comma-separated, allowed to call the API across origins (the CORS
    # middleware of `pigrocrm_api`). Empty, the default: none, the API answers only the
    # origin that serves the app. Read through `cors_origin_list`.
    cors_origins: str = ""
    # How many `POST /api/auth/login` attempts one client address gets per minute: a
    # token bucket of its own (`pigrocrm_api.ratelimit.spend_one`), never a share of
    # the signup routes' five, because a failed login is a full argon2id verify at the
    # library's own defaults (64 MiB, time cost 3, `auth/passwords.py`), the one cost
    # no other unauthenticated route pays on every failed attempt, so a script guessing
    # a password must not burn
    # the tokens a person mistyping a signup field needs, nor the reverse. Ten, not
    # five: a person locked out by a typo gets two tries at recovering their own
    # password before the wait, still nowhere near enough attempts a minute to make
    # guessing worthwhile (REB-270). A setting, since REB-662, because the e2e suite is
    # one address logging in once per spec, forty-one times in under three minutes at
    # the preview server's speed, and declares its own budget in
    # `apps/web/scripts/e2e-env.sh` the way it declares `cookie_secure`. Nothing else
    # has a reason to move it. `ge=1` because a bucket that starts below one token
    # answers 429 to every login for good, and a misconfiguration that locks everyone
    # out should fail at boot, not at the first person's login; `le` is two orders of
    # magnitude above the e2e suite's budget, so a typo cannot switch the limit off.
    login_requests_per_minute: int = Field(default=10, ge=1, le=10_000)

    # Whether a personal access token may perform the operations listed in
    # `agent_actions.FULL_ACCESS_ACTIONS` -- issuing and annulling invoices, rates, cost
    # categories, period locks, the hours-to-invoice bridge, the annual estimate and the
    # Drive and Gmail reads. (Not the fiscal profile nor the emitter: an admin's token
    # writes those on every installation, ORB-188. Sending mail is a person's, never a
    # token's, whatever this says.)
    #
    # `False` by default, and the default is the one nobody has to think about. Those
    # operations are not merely privileged, they are **irreversible in ways the rest of
    # the product is not**: issuing consumes a number from a gap-free fiscal register
    # that cannot be handed back, and a sent email is in somebody's client's inbox. The
    # worst outcome is not "the agent made a mistake" but "the agent made a mistake and
    # nobody can undo it".
    #
    # Turning it on is a decision about a *specific installation*, which is why it is a
    # setting and not a code change: this product is single-tenant and self-hosted, and
    # the person running it may reasonably want their own agent to do everything they
    # can. Everyone else keeps the closed default without having to know it exists.
    #
    # This is the ceiling (REB-810): the privileged tools are registered only where it is
    # on, and a token reaches them only if its own `full_access` flag, chosen by its owner,
    # is on as well. `PatService.resolve` reads both once and stamps the result onto the
    # `Actor` it builds -- so the capability travels on the credential rather than living
    # in a global that a caller could be unaware of. That is deliberate: it means a REST
    # request presenting the same token is treated exactly like the MCP transport (see the
    # reasoning in `actor.py`).
    mcp_full_access: bool = False

    # The space's shape (spec 2026-11-09, B1/B2): the profile and one switch per built
    # module, all `None` while a space has not decided. They are laid over a `Settings` from
    # the `space_settings` rows and written only by the actions `set_space_profile` and
    # `set_space_modules` (never through `SpaceSettingsUpdate`), so the confirmation they
    # ask for cannot be bypassed from the generic settings page. `core/modules.py` reads
    # them: an absent switch is the profile's default, and with no profile, on.
    space_profile: Literal["solo", "company"] | None = None
    onboarding_skipped: bool | None = None
    module_deals: bool | None = None
    module_contracts: bool | None = None
    module_time: bool | None = None
    module_purchases: bool | None = None
    module_taxes: bool | None = None
    module_staff: bool | None = None
    module_collaborators: bool | None = None
    module_cash: bool | None = None

    # The emitter's timezone, and the only one. Every `Date` column in the product is a
    # calendar day in *this* zone: `invoices.issue_date` (slice 3 §6.2),
    # `purchases.date` and `time_entries.date` (slice 4), `deals.closed_at` and
    # `documents.status_since` (slice 6 §4.1). Deriving any of them from
    # `datetime.now(UTC).date()` moves everything after 23:00 CET by a day and everything
    # on 31 December by a year -- the exact defect slice 3 §6.2 names. Single-tenant, so
    # one zone: a per-user zone would mean the same invoice falling in two fiscal years
    # depending on who looked at it.
    #
    # It is a setting rather than a constant because `db/clock.py` is now the *only*
    # clock -- `clock.today_local()` delegates to it -- so changing this changes the
    # fiscal calendar too, deliberately and in one place. An operator who moves it off
    # Europe/Rome is telling the product where the invoices are issued from, which is
    # the only reading under which one clock and one answer stay true.
    timezone: str = "Europe/Rome"

    # Storage. `local` by default: no external dependency is what makes the product
    # genuinely self-hostable (spec 5). Switching to `gdrive` moves *new* bytes only
    # -- the ones already written stay where they are, and moving them is an explicit
    # migration, not a side effect of an environment variable.
    #
    # `gdrive` is finished by one of two routes, and the environment chooses which:
    # either the two service-account variables below, or -- with neither of them set --
    # the Google account the owner connects from the Drive settings screen, writing into
    # the folder they pick there. The second route has nothing to configure here: it
    # lives in a row, so `storage_from_settings` returns a storage that resolves it at
    # the first upload, and the API boots before Drive is connected. Naming either
    # service-account variable selects the first route and makes the other one required,
    # so a half-finished setup is refused by name instead of quietly using somebody's
    # personal credential -- when the MCP adapter starts, or at the API's first document
    # operation, which is when each of them builds its backend.
    storage_backend: Literal["local", "gdrive"] = "local"
    storage_local_root: str = "./var/documents"
    # The service account's JSON key, inline. `PIGROCRM_GDRIVE_ROOT_FOLDER_ID` must
    # name a folder on a Shared Drive (or one shared with the service account): a
    # service account has no Drive quota of its own and `files.create` otherwise
    # fails with storageQuotaExceeded. `storage_from_settings` checks it
    # (`GDriveStorage.verify_root_accessible`) while it builds the backend, rather than
    # leaving it to fail mid-upload: that is start-up on the MCP adapter and the first
    # document operation on the API, which is where `get_storage` resolves.
    gdrive_service_account_json: str = Field(default="", repr=False)
    gdrive_root_folder_id: str = ""
    # Rendering. The image installs Pandoc and Typst at these names (Dockerfile.api).
    pandoc_binary: str = "pandoc"
    typst_binary: str = "typst"

    # --- Gmail (slice 5). Absent, not broken: if `google_client_id` is unset, Gmail
    # does not exist on this installation. The UI hides the section, the endpoints
    # answer Conflict, and the MCP tools are never registered. That is what lets
    # someone who self-hosts precisely in order not to have Google not have Google.
    google_client_id: str = ""
    # The two secrets below are halves of a credential to a *third-party* account (the
    # note at the top of the class says why every credential is `repr=False`).
    google_client_secret: str = Field(default="", repr=False)
    # 32 raw bytes, base64-encoded. Encrypts `google_accounts.refresh_token_ciphertext`
    # at rest. The key lives outside the database on purpose: a dump, a backup or a
    # pg_dump attached to a bug report are different exposure surfaces from the running
    # system, and this credential opens a *third-party* account, not just this app.
    google_token_key: str = Field(default="", repr=False)
    # The public origin, used to build the one redirect_uri Google compares exactly:
    # {public_url}/api/gmail/oauth/callback.
    public_url: str = ""
    # Google exposes no API for "is my OAuth client verified", so the operator states
    # it. True means Testing, which means a consumer refresh token expires 7 days after
    # consent -- so `consent_expires_at` gets set and the UI warns 48 hours ahead.
    google_app_unverified: bool = False
    # Whether the root lends its Google client to every space that has none of its own
    # (spec 2026-09-16 §5, REB-394). Off, the default: a space has no Google unless it
    # configures a client in the space settings screen, which is what a self-hosted
    # installation with no spaces expects. On: `space_base_settings` hands each space
    # the root's client and secret, a token key derived for that space alone, and the
    # root's callback, which relays the consent to the space named in the `state`
    # (`tenants/google.py`). Read from the environment only, never per space.
    google_shared_client: bool = False
    # The two below are never read from the environment in practice: `space_base_settings`
    # computes them for a space that borrows the root's client, and they are empty for
    # the root and for a space with a client of its own. Fields rather than arguments
    # because both OAuth flows (Gmail and Drive) and the settings page read them from the
    # one `Settings` they already hold, and no service has to learn what a space is.
    #
    # Where Google sends the browser back: `{google_callback_base_url}/api/gmail/oauth/
    # callback`. Empty means `public_url`, which is every installation's own address.
    google_callback_base_url: str = ""
    # Written ahead of the jti in the `state`, so the root's callback can tell which
    # space started the flow. The space's own callback refuses a state that does not
    # carry its own prefix before it looks anything up.
    google_oauth_state_prefix: str = ""

    # --- Slack (REB-739, REB-740; spec 2026-10-26 §6.4). Absent, not broken, like Gmail: with
    # no `slack_client_id` the Slack section says so and nothing is offered. One Slack app
    # serves the whole installation, installed once per space: a space never configures a
    # client of its own, it borrows the root's (`space_base_settings`), which is why there
    # is no row in `space_settings` for any of these. The two secrets are `repr=False` for
    # the reason `google_client_secret` is.
    slack_client_id: str = ""
    slack_client_secret: str = Field(default="", repr=False)
    # 32 raw bytes, base64: seals each space's bot token at rest (AES-256-GCM, the same
    # `seal` as the Gmail and Drive refresh tokens). Its own variable and not
    # `google_token_key`: a Slack token is a different credential, and a space without
    # Google must still be able to hold one. A space is sealed with a key derived from this
    # one and its slug (`tenants/google.py::space_slack_token_key`).
    slack_token_key: str = Field(default="", repr=False)

    # --- Mailboxes (REB-1015; spec 2026-11-09 § 2.2). 32 raw bytes, base64: seals the password of
    # an IMAP or PEC mailbox at rest (AES-256-GCM). Its own variable: never `jwt_secret`, whose
    # rotation would make sealed settings unreadable, and not the Google key, since a space with
    # no Google must still be able to connect a mailbox. A registry space is sealed with a key
    # derived from this one and its slug (`tenants/google.py::space_mailbox_token_key`); the root
    # installation uses it undivided.
    mailbox_token_key: str = Field(default="", repr=False)
    # Slack's own origin. Never changed in production; a setting because the capture and
    # the tests point it at a fake, and because GovSlack answers on `slack-gov.com`. The
    # authorize page is `{slack_url}/oauth/v2/authorize`, the Web API `{slack_url}/api/`.
    slack_url: str = "https://slack.com"
    # Where Slack sends the browser back: `{slack_callback_base_url}/api/slack/oauth/
    # callback`. Empty means `public_url`. Set for a space (by `space_base_settings`) to the
    # root's address, because Slack compares the redirect URL with the list registered on
    # the app, and one address per space would be one more to register for every signup.
    slack_callback_base_url: str = ""
    # Written ahead of the jti in the `state`, so the root's callback can relay the browser
    # to the space that started the flow (`<slug>.<jti>`, `tenants/google.py`).
    slack_oauth_state_prefix: str = ""

    # --- Outbound mail (spec 2026-09-12 §6.1). Resend sends the login link and the
    # welcome mail. Empty key: no sender, and the endpoints that would mail answer 503
    # with a sentence rather than pretend. `repr=False` for the same reason as the Google
    # secret above: a Settings object reaches logs and tracebacks.
    resend_api_key: str = Field(default="", repr=False)
    # letsrebase.com already carries SPF and DKIM for Resend (the hub sends from it).
    mail_from: str = "PigroCRM <ciao@letsrebase.com>"
    # How long a link by mail is good for. Fifteen, like the hub's.
    magic_link_minutes: int = Field(default=15, ge=1, le=120)

    # --- Web push (spec 2026-10-26 §6.1, REB-738). The environment's VAPID identity: the
    # private half of a P-256 key pair, base64url, from `pigrocrm vapid-keys`. Only the
    # private half is configured: the public half the browser subscribes with is derived
    # from it, so a pair that does not match cannot be set. Empty: push is not available,
    # the settings say so, and the reminders go by the other channels. A secret, so
    # `repr=False`, for the reason the Resend key's is.
    vapid_private_key: str = Field(default="", repr=False)
    # Who the push services may write to about this server (RFC 8292 `sub`): a `mailto:` or
    # an `https:` URL.
    vapid_subject: str = "mailto:ciao@letsrebase.com"

    # --- Product analytics (docs/design/2026-09-12-posthog-analytics-design.md). The
    # MCP server reports every tool call to PostHog when this holds the project key,
    # the same public `phc_` key the browsers carry in `shared/analytics`. Empty: the
    # installation measures nothing. Read by `pigrocrm_mcp.analytics` and by
    # `core/telemetry.py` (the weekly digest's own event, REB-221). Not a secret, but it
    # lets anyone holding it write events into the project, so it stays out of reprs too.
    posthog_key: str = Field(default="", repr=False)
    posthog_host: str = "https://eu.i.posthog.com"

    # The Gmail numbers below are plain integers that reach no service-level range check,
    # so each carries the bound a legitimate value cannot cross: a batch, a window or a
    # ceiling of zero would sync nothing, fetch no history or refuse every message, and a
    # typo of a few orders of magnitude would fetch the mailbox or hold it in memory.
    gmail_sync_address_batch_size: int = Field(default=20, ge=1, le=100)
    # The same range as `SpaceSettingsUpdate.gmail_backfill_days`, which a space's admin
    # may move from Impostazioni.
    gmail_backfill_days: int = Field(default=90, ge=1, le=3650)
    # `0` is a legitimate value: no overlap, at the price of the boundary message.
    gmail_watermark_overlap_hours: int = Field(default=24, ge=0, le=720)
    gmail_body_max_bytes: int = Field(default=262_144, ge=1, le=10_485_760)
    # Gmail's own ceiling for a message is 25 MiB to send and 50 MiB to receive.
    gmail_attachment_max_bytes: int = Field(default=20_971_520, ge=1, le=52_428_800)
    gmail_send_grace_minutes: int = Field(default=15, ge=1, le=1440)
    # --- Drive (slice 9C). The text of one file the owner pointed at, capped.
    # `ge=1` because a ceiling of zero would return an empty document for every file
    # and read as "this file has no text"; the upper bound is what an agent's context
    # and an HTTP response can carry without the cap being a fiction.
    drive_text_max_bytes: int = Field(default=TEXT_MAX_BYTES_DEFAULT, ge=1, le=10_485_760)
    # --- Documenti archiviati. The same ceiling for the text of a document already in
    # the CRM's own store. A separate field and not a reuse of the Drive one: an
    # installation that reads long contracts out of its archive has no reason to also
    # widen what a Drive folder may pour into an answer, and the two ceilings guard
    # bytes that arrive by different doors.
    document_text_max_bytes: int = Field(default=TEXT_MAX_BYTES_DEFAULT, ge=1, le=10_485_760)
    # Whether reconciliation first asks Gmail for the Message-ID we supplied
    # (`rfc822msgid:`, exact) before the approximate match of spec 6.3. Whether Gmail keeps
    # a client-supplied Message-ID is **UNVERIFIED**: nobody has run the check against a
    # real Gmail (`docs/superpowers/notes/2026-08-20-gmail-message-id-verification.md`),
    # and Google's own issue tracker (446748944, 2025-09-22) shows a send stored under a
    # `...@mail.gmail.com` id instead. So the default does not bet on it: `_find_sent` runs
    # the approximate match whenever the exact lookup finds nothing, whatever this says,
    # which makes `true` the choice that cannot lose. If Gmail keeps the id, the lookup
    # is exact; if it replaces it, the lookup answers nothing and costs one request per
    # unresolved send. `false` only saves that request: it never changes a verdict, so it
    # is not a remedy for delivered mail reported as `failed`. The setting follows what
    # the note records (`tests/test_gmail_message_id_contract.py`).
    gmail_reconcile_by_message_id: bool = True

    # The three numbers that decide whether a reminder is legitimate, bearable and
    # finite. Each carries `ge`/`le` because they are plain integers that reach no
    # service-level range check: a `payment_reminders_grace_days` of `0` would chase the day
    # after the due date while the transfer is still in flight, and a
    # `payment_reminders_min_interval_days` of `0` would remove the layer that stops the double
    # send hours apart. The upper bounds are not tidiness either -- an operator who typed
    # a year into the grace period would silently switch the feature off, and one who
    # typed 50 into the ceiling would have configured a persecution.
    payment_reminders_grace_days: int = Field(default=7, ge=1, le=365)
    payment_reminders_min_interval_days: int = Field(default=14, ge=1, le=365)
    # 3 is also `MAX_PAYMENT_REMINDER_LEVEL` in `gmail/payment_reminders_template.py`, which is
    # where the
    # wording stops escalating: above it a fourth register would have to be a legal
    # threat, which is not a sentence this project has standing to put in a freelancer's
    # name. The `le` therefore matches the template rather than being a round number.
    payment_reminders_max_reminders: int = Field(default=3, ge=1, le=3)

    # The whole-practice, calendar-year concentration cap (§1.5, §3 and §5 item 2 of
    # docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md):
    # the maximum share of a calendar year's invoiced revenue any one customer should
    # represent before `OperationalDashboard.signals` flags it. A fraction in (0, 1],
    # the same range `AnalyticsRepository.RevenueByCustomerRow.share` already reports
    # in -- never a percentage, so the two compare directly with no conversion at the
    # call site. `gt=0.0` because a threshold of zero would flag every customer with
    # any revenue at all, which is not a preference, and `le=1.0` because a share
    # cannot exceed the whole.
    #
    # 0.30 is a starting default, not a rule of law the way `stamp_duty_threshold` is: a
    # freelance practice with one dominant client for a season is not wrong, only
    # worth naming. That is why this is a per-space `Settings` field an operator can
    # raise or lower exactly the way the three `payment_reminders_*` thresholds above already
    # are, rather than a column on `FiscalProfile` -- nothing here is a legal fact the
    # way the stamp-duty threshold is.
    preferred_concentration_threshold: float = Field(default=0.30, gt=0.0, le=1.0)

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_must_be_long_enough(cls, value: str) -> str:
        if len(value) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"jwt_secret must be at least {MIN_JWT_SECRET_LENGTH} characters long "
                "(a short HS256 key is weaker than the algorithm itself, RFC 7518 "
                "Section 3.2)"
            )
        return value

    @field_validator(
        "registry_token", "engagements_token", "documenso_webhook_secret", "hub_notices_token"
    )
    @classmethod
    def _service_token_must_be_long_enough(cls, value: str, info: ValidationInfo) -> str:
        # Empty is legal and means the door does not exist; a token that is set is a shared
        # secret between two deployments, so a short one fails the boot instead of guarding
        # the door with something a script can guess.
        if value and len(value) < MIN_SERVICE_TOKEN_LENGTH:
            raise ValueError(
                f"{info.field_name} must be at least {MIN_SERVICE_TOKEN_LENGTH} characters "
                "long when it is set, or empty to switch the route off"
            )
        return value

    @field_validator("documenso_url")
    @classmethod
    def _documenso_url_must_be_a_web_address(cls, value: str) -> str:
        # Empty is legal and means signing is off. One that is set is a base URL the API token
        # is sent to: anything but http(s) fails the boot rather than the first send.
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            raise ValueError(
                "documenso_url must start with http:// or https://, or be empty to switch "
                "signing off"
            )
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone_must_be_a_real_zone(cls, value: str) -> str:
        # Checked against the tz database at construction, not at first use: a typo in
        # PIGROCRM_TIMEZONE must fail at start-up rather than shift every date in the
        # product by an hour for the life of the deployment. `ZoneInfo` itself would
        # raise on first use instead -- deep inside a repository, on whichever request
        # happened to need a date first.
        if value not in available_timezones():
            raise ValueError(
                f"timezone {value!r} is not in the IANA tz database (examples: Europe/Rome, UTC)"
            )
        return value

    @property
    def owner_database_url(self) -> str:
        """`admin_database_url`, or `database_url` when none was configured."""
        return self.admin_database_url or self.database_url

    @property
    def cors_origin_list(self) -> list[str]:
        """`cors_origins` as a list: split on commas, stripped, empties dropped."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require_production_secrets(settings: Settings) -> None:
    """Raises `ValueError` when the signing key is still a published placeholder on an
    installation that serves over TLS.

    The API calls it at boot, and only the API: the key signs sessions, and nothing else
    that builds a `Settings` (the CLI, the tests) needs a real one. `cookie_secure=False`
    is the documented switch for plain-HTTP development, so that is what lets a
    placeholder through. Names the variable and never quotes the value."""
    if settings.cookie_secure and settings.jwt_secret in KNOWN_PLACEHOLDER_JWT_SECRETS:
        raise ValueError(
            "PIGROCRM_JWT_SECRET is still a placeholder, which anyone can use to forge a "
            "session: set a real secret (for example `openssl rand -hex 32`)"
        )


def gmail_configured(settings: Settings) -> bool:
    """All four or none. A client id with no token key would connect an account and
    then be unable to store its refresh token, which is a worse failure than not
    offering the feature."""
    return bool(
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_token_key
        and settings.public_url
    )


def require_gmail_configured(settings: Settings) -> None:
    if not gmail_configured(settings):
        raise Conflict("gmail", "Gmail non è configurato su questa installazione")


def decode_google_token_key(settings: Settings) -> bytes:
    """The AES-256-GCM key of the Gmail and Drive refresh tokens. Raises `ValueError`
    naming the variable, never quoting the value (`crypto.decode_aes256_key`).

    Called at startup when `google_accounts` has at least one row (apps/api deps),
    so a missing or malformed key fails the boot rather than the first sync -- the
    same discipline as `_jwt_secret_must_be_long_enough`. Not a pydantic validator,
    because the empty default has to stay legal for every install that has no Gmail.
    """
    return decode_aes256_key(settings.google_token_key, "PIGROCRM_GOOGLE_TOKEN_KEY")


def mailbox_connect_configured(settings: Settings) -> bool:
    """Whether this installation can seal the password of an IMAP mailbox."""
    return bool(settings.mailbox_token_key)


def decode_mailbox_token_key(settings: Settings) -> bytes:
    """The AES-256-GCM key of the mailbox passwords. Raises `ValueError` naming the variable
    and never quoting the value, as `decode_google_token_key` does and for the same reasons."""
    return decode_aes256_key(settings.mailbox_token_key, "PIGROCRM_MAILBOX_TOKEN_KEY")


def slack_configured(settings: Settings) -> bool:
    """All four or none, for the reason `gmail_configured` gives: a client id with no key
    would connect a workspace and then be unable to store its bot token. The address is
    the callback's, a space's own or the root's."""
    return bool(
        settings.slack_client_id
        and settings.slack_client_secret
        and settings.slack_token_key
        and (settings.slack_callback_base_url or settings.public_url)
    )


def require_slack_configured(settings: Settings) -> None:
    if not slack_configured(settings):
        raise Conflict("slack", "Slack non è configurato su questa installazione")


def decode_slack_token_key(settings: Settings) -> bytes:
    """The AES-256-GCM key of the bot tokens. Raises `ValueError` naming the variable and
    never quoting the value, as `decode_google_token_key` does and for the same reasons."""
    return decode_aes256_key(settings.slack_token_key, "PIGROCRM_SLACK_TOKEN_KEY")
