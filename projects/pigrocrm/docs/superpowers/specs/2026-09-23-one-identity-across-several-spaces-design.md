# One identity, several spaces: proving who you are before any space opens

Date: 2026-09-23. Status: proposed, decision pending from Lorenzo on the session/cookie
model and on how a chooser learns which spaces someone may enter (§ Decision needed from
Lorenzo). Tracker: REB-345, project "Give every space its own team", milestone "Let one
identity run several spaces". Written in English, per the repository's current rule; the
two neighbour specs this one reads before writing, `2026-09-08-spazi-un-database-per-tenant-design.md`
and `2026-09-17-inviti-e-ruoli-di-uno-spazio-design.md`, are Italian only because they are
grandfathered pre-existing records (root `AGENTS.md:192-195`).

**No implementation issue is filed from this document.** The project this issue belongs
to states it explicitly: three design spikes, sign-off on each spike's entity mapping
first. This document's own "Done when" is the same gate, restated on the card. § Proposed
follow-up work below names the shape of the work, not tickets.

## 0. Why

A space is one Postgres database with its own `users` table and nothing that links two of
them: the registry, `pigrocrm_tenants.tenants`, carries `slug`, `db_name`, `owner_email`,
`created_at` and no more (`packages/core/src/pigrocrm/core/tenants/models.py:13-23`), and
`owner_email` is a plain string column with no unique or even case-normalising index —
nothing stops, or notices, two rows sharing one address. Session cookies are deliberately
scoped `path=/<slug>/` so that "two spaces in one browser never see each other's session"
(`apps/api/src/pigrocrm_api/tenancy.py:59-67`, and the load-bearing decision that made the
root's own cookie an exception at `path=/`, `docs/design/DECISIONS.md`, 2026-09-09, "Where
does the root installation log in"). The result: signing up twice with the same address
creates two admin accounts in two databases that have never heard of each other, with no
shared login, no "my spaces" list, and no way to invite that same person into a second
space as anything other than a fresh `User` row that starts from zero. Lorenzo, 2026-09-22:
«vorrei che un utente potesse creare più spazi e invitare utenti diversi in spazi diversi»,
confirming the direction is a real, unified identity — one login, one session, that then
lists and switches spaces without re-authenticating for each one.

The codebase already has the seed of this, twice, and both seeds stop short of a session:

- **`_owned_slugs`** (`apps/api/src/pigrocrm_api/routers/auth.py:224-238`), called from the
  root's own `POST /api/auth/link` (`auth.py:289-299`), already reads the registry across
  every space by `owner_email` and mails a magic link into each one it finds. This is
  cross-space *discovery* — the registry already answers "which spaces did this address
  open" — but it authenticates nothing on its own and produces N separate emails, each
  opening its own separate, single-space session. There is no "my spaces" page it lands on
  and no session that survives the click past the one space that link was for.
- **`MemberAnswer.spazi`** (`apps/api/src/pigrocrm_api/routers/tenants.py:88-97,105-120`,
  backed by `TenantService.count_for_owner`, `packages/core/src/pigrocrm/core/tenants/service.py:76-90`)
  already answers "how many spaces does this address own" during signup, before the
address is proven — but deliberately "A count and not the slugs: the caller has not
proven the address yet, and which spaces are whose is the mail's to tell"
(`tenants/service.py:80-82`). This
  is the existing precedent this design keeps: an unproven email learns a number, never a
  list; a list is something only a proven session may read.

Both existing mechanisms establish the shape this document proposes, and neither commits
to a session. That gap — proving identity once, then listing and entering spaces without
re-proving per space — is what REB-345 asks for.

## 1. Where cross-space identity lives

**A new table, `identities`, in the registry database (`pigrocrm_tenants`), one row per
lowercase email.** The registry is the only thing in this codebase that already sees more
than one space — its own module docstring says so directly, "nothing in the other
packages knows what a tenant is. This package owns the registry of spaces, the rules for
their names and the provisioning of a new database" (`tenants/__init__.py:3-5`), so it is
the one place a fact about "which spaces does this person reach" can
live without teaching a second module about tenancy. Two homes were considered and
rejected:

- **The root installation's own `users` table.** The root is an optional, grandfathered
  single-tenant deployment — "L'installazione di Ivan resta la radice"
  (`2026-09-08-spazi-un-database-per-tenant-design.md:22`), configured by `PIGROCRM_ROOT_SLUG`
  (`packages/core/src/pigrocrm/core/config.py:45-49`) and empty by default. Most spaces,
  including every space created on the production SaaS root at pigro.letsrebase.com, have
  no corresponding root account at all: tying cross-space identity to the root conflates a
  rare, self-hosted deployment mode with a concept every signup needs.
- **A per-space table, replicated by trigger or by a background sync.** Postgres has no
  transaction that spans two independent databases, and each space already *is* its own
independent database by design, specifically so that a forgetfulness of tenant scoping is
structurally impossible: "L'alternativa, una colonna `tenant_id` su trenta tabelle... una
dimenticanza è impossibile: la richiesta ha una sola connessione, a un solo database"
(`2026-09-08-spazi-un-database-per-tenant-design.md:18-20`). Fanning writes
  across every space's own database to keep a replica of "who else exists" in each of them
  would reintroduce exactly the cross-database coordination problem the one-database-per-space
  design exists to avoid, for a fact (a person's own identity) that has nothing to do with
  any one space's data.

`identities` therefore holds only what the registry needs to answer "is this a person we
have seen before, and how do we prove it is them again": `id` (UUID, PK), `email`
(`String(320)`, a functional unique index on `lower(email)`, exactly the shape
`uq_users_email_lower` already uses on `auth/models.py:47`), `created_at`. No name, no
password, no role: a display name is redundant with each space's own `users.nome` (the
"no duplication" principle, `projects/pigrocrm/AGENTS.md:27`), and a role is meaningless
outside a space — `identities` says only "this address exists as a person who has proven
it once," never anything about what that person may do anywhere.

New module, `pigrocrm.core.identity`, a sibling of `pigrocrm.core.tenants` and built the
same way: `Identity(TenantsBase, PrimaryKeyMixin)` in `identity/models.py`, on the exact
same `TenantsBase` declarative base `Tenant` already uses
(`tenants/models.py:9-10,13`), so it lives in the same `pigrocrm_tenants` database with no
new engine, no new connection setting, and no code change to how that database is opened.

## 2. Proving who someone is, before any space's database opens

Today, `deps.get_session` opens a space's database from the URL slug alone
(`apps/api/src/pigrocrm_api/deps.py:86-93`, via `_factory_for`, `deps.py:81-83`) with
nothing checked yet; authentication happens afterward, inside that already-open session,
by `get_actor` decoding the access-token cookie and reading `users` in that one database
(`deps.py:287-324`). This design adds a layer entirely *before* that one, which is what
makes it additive rather than a rewrite: the identity layer authenticates purely against
the registry's own `identities` table, on the registry's own session
(`TenantsRegistryDep`, `deps.py:63-74`), and never has to open a space's database to
answer "who is asking."

**The mechanism mirrors the magic link exactly, one layer up.** `MagicLinkService`
(`packages/core/src/pigrocrm/core/auth/magic_link.py:28-94`) already does precisely this
for one space: `request(email)` issues a hashed, time-limited, single-use token;
`enter(raw)` spends it with a conditional `UPDATE` so two racing clicks still open exactly
one session. `IdentityService` (`pigrocrm.core.identity.service`) does the same shape
against `identities` instead of `users`: a new `IdentityLinkToken` model
(mirroring `MagicLinkToken`, `auth/magic_models.py:10-23`, but `identity_id` foreign-keyed
to `identities.id` instead of `user_id` to `users.id`, and living on `TenantsBase` beside
`Identity` rather than on each space's own `Base`), `request(email)` and `enter(raw)`
methods identical in shape. Passwordless by construction, matching the direction this
product has taken twice already for exactly this reason — the first admin of a space
(spec `2026-09-12` §6.4) and every person invited into one since (§1 of the 2026-09-17
spec) both enter by proving a click, never a password somebody else chose. A third,
parallel password mechanism at the identity layer would be the "two-mechanism problem"
the 2026-09-17 invitations spec itself already names when it removes the password-based
`POST /api/users`: "keeping two ways to add a person, one that mails a token, one that
hands over a password by hallway, is the two-mechanism problem this repository's own
DECISIONS.md warns against everywhere else" (`2026-09-17-inviti-e-ruoli-di-uno-spazio-design.md:146-148`).

**A new token type, not a new secret.** `TokenPayload.type` is already a closed
`Literal["access", "refresh"]` (`packages/core/src/pigrocrm/core/auth/tokens.py:12,18`),
and `decode_token(..., expected_type=...)` already refuses a token minted for one purpose
presented for another (`tokens.py:97-121`). This design widens that literal to
`Literal["access", "refresh", "identity"]`: an identity token can never be replayed as a
space's access token at `get_actor` (`deps.py:287-324`, which calls `decode_token(...,
expected_type="access")`, `deps.py:314`), and a space's access token can never be replayed
as an identity token, for the same reason the two existing types already cannot be
confused with each other. The same `settings.jwt_secret` signs it (`config.py:64-65`).

**It is revocable, because it is long-lived.** A bare signed JWT with no server-side row
is exactly what `RefreshToken`'s own docstring warns against — "a bare signed token has no
server-side presence of its own, so without a row to mark consumed there is nothing to
stop it being replayed for its entire lifetime" (`refresh_models.py:11-15`) — and the
identity cookie is meant to live for months, the one artifact in this design built to
outlive any single space's own session. It carries a `jti`, like the refresh token
(`tokens.py:21`), pointing at a new `IdentitySession` row (`identity_id`, `jti`,
`expires_at`, `revoked_at`), the same shape as `RefreshToken`
(`auth/refresh_models.py:10-22`) but without that model's rotation machinery: an identity
token is read repeatedly by its rightful holder, the same way an access token already is
within its own window, rather than spent once like a refresh token or a magic link, so
there is no replay-of-a-spent-value case to guard against, only a checked-on-every-read
"is this `jti` still live" — one more `SELECT`, the same query `RefreshTokenService`'s own
revocation already performs. `settings.identity_token_days` (proposed default: 180, the
same as `refresh_token_days`, `config.py:70`) bounds it.

**Logout.** The existing `logout` route (`auth.py:447-476`) is space-scoped by design — it
walks the refresh cookies for *that* space and clears *that* space's cookie pair — and
this design leaves it untouched: signing out of one space must not silently end a session
in every other space someone happens to have open. A new, root-scoped
`POST /api/identity/logout` (§3) revokes every live `IdentitySession` row for that
identity — `UPDATE identity_sessions SET revoked_at = now() WHERE identity_id = :id AND
revoked_at IS NULL`, not only the one named by the presented `jti` — and clears
`pigrocrm_identity` at `path=/` in the browser that called it. That is genuinely "signs
out of the identity everywhere it was used," including a copy of the cookie left in
another browser; it is deliberately *not* "signs out of every space," for the same reason
today's `logout` does not reach into a *different* space's own cookie jar — once `enter`
hands out a space's ordinary access+refresh pair, that pair is that space's own session
from then on, governed only by that space's own `logout`, exactly as if the person had
logged in there directly. The two scopes are named separately on purpose: an identity
revocation the person asked for should not require them to also visit and log out of
every space they had open, and a space's own logout should not depend on a registry round
trip to complete.

**The cookie is `pigrocrm_identity`, at `path=/`, unconditionally.** This is the one place
this design deliberately breaks the "cookies live under a space's prefix" rule
(`tenancy.py:59-67`) — on purpose, because the whole point of this cookie is to survive
moving between slugs, which is the opposite of what a space-scoped cookie is for. It is
not a new kind of exception: the root's own session cookies already live at `path=/` "even
when the request wore `/<root_slug>/`" for exactly the same reason, decided
2026-09-09 (`DECISIONS.md`, "the root's session cookies are `Path=/`... one jar at `/` is
the only thing the bare login and the aliased app can both read"). `first_cookie`
(`tenancy.py:91-103`) already reads the most specific path first when two cookies of the
same name coexist, which is precisely why a per-space access cookie at `/<slug>/` and this
identity cookie at `/` never shadow each other: a space's own `get_actor` never reads the
identity cookie's name at all, and the identity endpoints never read the space cookie's.
The two systems share nothing but the browser they both live in.

**Three existing entry points that already prove an email by a click get one more,
harmless line beside where each already mints the space's own session pair**: `login`
(`apps/api/src/pigrocrm_api/routers/auth.py:157-195`), `enter_with_link`
(`auth.py:305-334`), and `accept_invite` (`auth.py:394-425`). Each calls one new function,
`IdentityService.upsert_and_issue(email)` — create the `identities` row if none exists,
mint the identity cookie either way — right beside the `_set_cookie(response,
ACCESS_COOKIE, ...)` / `_set_cookie(response, REFRESH_COOKIE, ...)` pair each of those
routes already writes. None of the three needs to branch on whether the identity table
has ever heard of this email before: the write is a pure side effect of a proof that
already happened, and its own failure must never turn a working space login into a 500.
The precedent for that discipline already exists in this exact file: `_space_link`
(`auth.py:241-252`) opens an ephemeral engine against a space's database and explicitly
catches `SQLAlchemyError`, answering `None` rather than raising, "which must not turn the
request into a 500 for the one address that owns it." `upsert_and_issue` follows the same
shape: a registry that is briefly unreachable costs the identity cookie for that one
request, never the space session the person actually asked for.

**Signup is deliberately not a fourth call site.** `TenantService.provision`
(`tenants/service.py:125-176`), reached through `POST /api/tenants`
(`tenants.py:142-202`), creates the space's first admin from whatever email `TenantSignup`
carries (`tenants/schemas.py:61-73`, a bare `EmailStr`, no password, no click) and mints
only that space's own *access* cookie, on purpose "for as long as an address nobody has
proven deserves" — its own docstring is explicit that "whoever typed somebody else's
email works for `access_token_minutes` and then stops, cannot mint a personal token and
cannot add a user" (`tenants.py:155-159`). Wiring `upsert_and_issue` in here would silently
undo that boundary at the identity layer: it would hand a durable, months-long
`pigrocrm_identity` cookie for an address the request has proven nothing about, and the
chooser (§3) would then let whoever typed it browse and enter every real space that
address actually owns — exactly the "unproven email learns a number, never a list"
precedent §0 opens with, broken from the other side. The address signup carries is proven
the same way every other address in this product is: the welcome mail's own link, spent
through `enter_with_link` (`tenants.py:192-201`, `MagicLinkService.enter`) — already one
of the three call sites above. A freshly signed-up space's admin gets an identity cookie
the first time they click that link, not at signup itself; nothing is lost, and the one
existing call site already covers it.

A password login proves the identity exactly as well as a magic-link click does for this
purpose: `require_verified_identity` (`auth/service.py:80-93`) already treats a
self-chosen password and a proven magic link as equally sufficient grounds to trust an
address for something more durable than one access token's lifetime ("every account with
a password or a verified address pass"), and this design borrows that same two-way
acceptance rather than inventing a third rule for what counts as proof.

## 3. The chooser: entering a space you already proved you can reach

Three new root-scoped routes (never registered under a space's own prefix, the same way
`POST /api/tenants` is root-only by convention rather than by a second registration,
`tenants.py:1-8`):

| Endpoint | Who | Behaviour |
|---|---|---|
| `GET /api/identity/spaces` | anyone holding a live identity cookie | The spaces this identity may enter: `{slug, ruolo}[]`. 401 with no cookie or an expired one — the chooser is never shown to an unproven visitor, the same "prove first, list second" precedent `MemberAnswer.spazi` already set (§0). |
| `POST /api/identity/enter/{slug}` | same | Opens that space's database (the same `tenant_database_url`/session-factory machinery `deps._tenant_session_factory` already builds by slug, `deps.py:77-79`, called directly rather than through a request's own prefix), looks up `users` by the identity's own email (`UserRepository.get_by_email`, `auth/repository.py:28-30`). No row, or a deactivated one: 404 — the concept `cookie_path`'s own docstring already reaches for on a stranger's cookie, "a root cookie that reaches a space's API names a user that space does not have, and `get_actor` answers 401 like for any stranger" (`tenancy.py:64-67`), applied here as a 404 rather than that 401, since `TenantService.get` already answers `NotFound` for a slug this caller has no standing on rather than inventing one (`tenants/service.py:100-104`). A live, active row: mints the ordinary access+refresh pair scoped `path=/<slug>/`, exactly as `login` does today (`auth.py:178-194`), and answers `UserRead` so the SPA can navigate the same way `homeAfterEntry()` already does after any other entry point. |
| `POST /api/identity/logout` | anyone holding a live identity cookie | Revokes every live `IdentitySession` for that identity (§2, not only the one presented) and clears `pigrocrm_identity` at `path=/` in this browser. Idempotent, the same goal-state discipline the existing `logout` already follows (`auth.py:447-456`: "an already-invalid or already-expired token has nothing left to invalidate... the goal state is already true"). |

No password, no magic-link click, no second proof at the point of entering a space: the
identity cookie already is the proof, and `enter` only ever *reads* a space's `users`
table, it never creates one — a person who has an identity but no account in a given space
is not a member of it, and this route answers exactly the 404 that fact deserves, on the
same reasoning `TenantService.get` already applies to an unknown slug (`tenants/service.py:100-104`).

**SPA.** A new route, `apps/web/src/routes/app/spazi.tsx`, beside `entra.tsx` and
`invite.tsx`. The bare `/app/login` page already probes an endpoint on mount to decide
what to render — the existing `isRoot`/`rootSlug` effect
(`apps/web/src/routes/app/login.tsx:34-60`) already asks whether this installation has a
root slug before deciding whether to offer "Crea il tuo spazio." Since the identity cookie
is `httponly` (`sessions.py:15-20`, `set_session_cookie`, unchanged), the SPA cannot read
it directly and must ask the same way: on mount, `login.tsx` calls
`GET /api/identity/spaces`; a 401 means show the email-first login form exactly as today,
and a 200 with at least one space means show the chooser instead, a one-click list wired
to `POST /api/identity/enter/{slug}` per row. A 200 with zero spaces is not expected in
practice — an identity is only ever created alongside a first successful login somewhere —
but is handled the same as a 401 (show the login form) rather than an error page, since an
identity that briefly outlives every space it once pointed at (a deleted tenant, though
this design does not add tenant deletion) is not a broken account, only one with nothing
to choose from yet.

## 4. Composability with invitations

The 2026-09-17 spec's own open question — is an invited person who already has an
identity linked automatically, or invited blind to other spaces as today — has a direct
answer under this design: **automatically, with no new step, because identity is anchored
purely by a proven email address, never by a shared numeric id or an explicit "connect
your accounts" action.** `accept_invite`
(`apps/api/src/pigrocrm_api/routers/auth.py:394-425`) already proves the invitee's email
by the same click every invitation has always required
(`InvitationService.accept`, `packages/core/src/pigrocrm/core/auth/invitations.py:228-292`);
adding the one `IdentityService.upsert_and_issue(row.email)` call there, in the same place
§2 adds it to `login` and `enter_with_link`, is the entire change this composition needs.
The moment that click happens, the new space appears in that person's own chooser next to
whatever spaces they already had — nothing about the invitation flow itself, the mail
copy, or the acceptance page needs to know or ask whether the address already runs other
spaces.

**What deliberately does not change.** An admin sending an invitation
(`InvitationService.create`, `invitations.py:108-171`) still only ever types an email and
a role; it stays exactly as blind as it is today to whether that address already has other
spaces. Surfacing "this address already runs 3 other spaces" to the *inviter* would leak
information about a person the inviter may not otherwise know anything about — a real
privacy question this design does not need to answer, because it never arises: the
inviter's own view (`UsersPanel.tsx`'s "Invita" dialog) is unaffected, and the only place
"my spaces" is ever listed is the invitee's own private chooser, in their own browser,
after they themselves have proven the address. `InvitationService.create`'s existing
duplicate check — "an address that already has an *active* user in the space"
(`invitations.py:109-110`) — stays exactly as written; this document adds no cross-space
concept of an invitation, matching the issue's own scope ("Not here": roles, invitations
and the team screen are already spec'd and this milestone is additive on top).

## 5. What changes for "no service learns what a tenant is"

The phrase is `projects/pigrocrm/AGENTS.md:28-32`'s fourth principle, and it is narrower
than it sounds: it has never meant "nothing in this codebase may know two spaces exist" —
`pigrocrm.core.tenants` is the standing exception, and has been since the 2026-09-08 spec,
precisely because *something* has to route a request and provision a database. What the
principle actually protects is that **a space's own services, models and queries stay
single-tenant** — `UserService`, `InvitationService`, `ActivityService`, every module
under `packages/core/src/pigrocrm/core/{auth,activities,invoices,...}` — so the same code
that ran before spaces existed still runs, unmodified, inside every one of them. This
design adds not one line to any of those: the entire cross-space concept lives in a new,
parallel module (`pigrocrm.core.identity`, beside `pigrocrm.core.tenants`) and in two new
root-scoped routes in `apps/api`, exactly where `pigrocrm.core.tenants` and
`routers/tenants.py` already live for the same reason. Every space's own database still
has no column, table, index or query that has ever heard of another space, and still never
will — `IdentityService` never opens more than one space's database at a time (`enter`
opens exactly the one being entered; a live-scan chooser, §7, opens each space in turn but
each one still runs the same tenant-blind `UserRepository.get_by_email` it always did).

What genuinely is new: the registry itself — already the one exception, already trusted to
route a request to the right database — is now also asked to **authenticate a person**,
not merely to look up a slug. `SpaceRegistry` and `TenantService`
(`tenants/registry.py:48-134`, `tenants/service.py:69-205`) exist today to answer "which
engine, which settings, which name is free"; `IdentityService` sits beside them answering
"who is this, and have we seen them before." That is a real widening of the registry's own
job, worth naming plainly rather than folding quietly into "it's just another table" — but
it is a widening of the one module already granted an exception, not a crack in the
invariant every other module still holds.

## 6. Data model and migration

```
identities                 identity_link_tokens              identity_sessions
├── id       UUID PK       ├── id            UUID PK          ├── id          UUID PK
├── email    String(320)   ├── identity_id   FK identities.id ├── jti         UUID, unique, indexed
│   unique on lower(email) ├── token_hash    String(64),      ├── identity_id FK identities.id
└── created_at DateTime(tz)│   unique, indexed                ├── expires_at  DateTime(tz)
                           ├── expires_at    DateTime(tz)      └── revoked_at  DateTime(tz), nullable
                           └── used_at       DateTime(tz),
                               nullable
```

All three on `TenantsBase` (`tenants/models.py:9-10`), in the same `pigrocrm_tenants`
database `Tenant` already lives in. **This is the whole migration story, and it needs no
Alembic revision.** The registry is provisioned as a *sidecar* database
(`packages/core/src/pigrocrm/core/db/sidecar.py:1-9,92-96`): `ensure_tenants_database`
calls `ensure_sidecar_database`, which runs `metadata.create_all(engine)` against
`TenantsBase.metadata` on every boot — idempotent, and additive only, since
`create_all` creates tables that do not exist yet and never alters ones that do. Defining
`Identity`, `IdentityLinkToken` and `IdentitySession` as three more classes on that same
base is the entire change this needs at the schema level: the next time any process calls
`ensure_tenants_database` (every API and MCP process boot already does), the three tables
exist, with zero downtime, zero migration file, and not one byte touched on `tenants`
itself or on any space's own Alembic-migrated schema
(`packages/core/migrations`, unaffected — this design adds nothing there).

**Nobody's login breaks, because nothing existing is touched.** Every route this design
changes (`login`, `enter_with_link`, `accept_invite`) keeps its existing behaviour
byte-for-byte and gains one additional, best-effort side effect that cannot fail the
request it rides on (§2); `signup` (`TenantService.provision`) is explicitly excluded, for
the reason §2 gives, and keeps its own existing behaviour untouched too. A person who
never uses the chooser never notices this design exists; a space that is never entered
through `/api/identity/enter` behaves exactly
as it does today. `identities` starts empty on every existing installation, and the very
next successful login of any kind, by anyone, lazily creates that person's row — "my
spaces" is complete from the first login after this ships, and grows to completeness for
everyone else exactly as they log back in, with no batch job required for correctness.

**A backfill for day one, not for correctness.** For an installation that wants "my
spaces" to be complete immediately rather than growing in, a CLI command,
`pigrocrm rebuild-identity-index`, follows the exact shape `pigrocrm ensure-space-defaults`
already uses (`packages/core/src/pigrocrm/core/cli.py:131-173`: read every `Tenant` row
from the registry, `select(Tenant)` ordered by `created_at`, then visit each space in
turn). For each space, it opens that space's database, reads every `users` row (not only
`owner_email`, since a space may already hold people invited after it was created), and
upserts one `identities` row per distinct email it finds. Idempotent and safe to re-run,
following the same "always answers 0, whatever happens" discipline
(`cli.py:146-151`) — one space that cannot be reached costs a line on stderr, never the
whole command.

## 7. Decision needed from Lorenzo

**A. The session/cookie model.** No option below is treated as decided; §§1-6 above are
written around A1 for concreteness, matching the reference specs' own style of writing one
coherent design while still flagging what is open, but the choice is Lorenzo's.

- **A1 (recommended).** A dedicated `pigrocrm_identity` cookie, carrying a new,
  revocable JWT token type (`"identity"`), at `path=/`, backed by a new passwordless
  `identities` + `identity_link_tokens` + `identity_sessions` set in the registry
  (§§1-3), wired into every entry point that already proves an email and explicitly kept
  out of signup (§2). Additive to every existing session mechanism; nothing about today's
  per-space login, magic link or invitation acceptance changes shape.
- **A2.** Make the root installation's own account the identity, requiring every space
  creator to also hold a row in a root `users` table. Not recommended: the root is
  optional and mostly absent (§1) — most spaces, including the production SaaS root's own
  population, have no root account to anchor to, so this does not generalise to the
  product's actual signup path.
- **A3.** No new cookie or session at all: formalise today's `_owned_slugs`/`request_link`
  fan-out (§0) into a real "my spaces" page, reached by requesting a fresh mail link on
  every visit rather than staying signed in. Not recommended: it does not meet Lorenzo's
  own stated bar, switching spaces "without re-authenticating" — A3 asks for a fresh proof
  every time, which is exactly the re-authentication the issue asks to remove.

Recommendation: A1. It is the only option that is both additive (A2 is not, because most
installations have nothing to attach it to) and meets the actual requirement, staying
signed in across a switch (A3 does not).

**B. How the chooser learns which spaces someone may enter, beyond the ones they
created.** `identities` alone answers "who is this," never "which spaces." Two shapes for
that second answer:

- **B1 (recommended).** A live scan at read time: `GET /api/identity/spaces` opens every
  tenant's own database in turn and checks its `users` table for the identity's email —
  exactly `_owned_slugs`'s existing technique (`auth.py:224-238`), widened from
  `Tenant.owner_email` to each space's own `users.email`, so an invited member is included
  and not only a space's original creator. No new persisted state, no second copy of "who
  belongs to this space" to keep in sync — the "no duplication" principle
  (`projects/pigrocrm/AGENTS.md:27`) taken at face value. Unlike `_owned_slugs`, which
  queries only the registry, this issues one connection per tenant, so it needs the bound
  `_space_link` already sets an example for: a short, fixed per-connection timeout, and one
  unreachable space caught and skipped rather than failing the whole response — the same
  "must not turn the request into a 500 for the one address that owns it" discipline
  `_space_link` states for exactly this shape of problem (`auth.py:241-252`). A space that
  times out or errors is silently absent from that one response; the chooser shows what it
  could reach, and the next visit tries again. Cost: one short-lived, bounded connection
  per tenant per chooser view, paid only when someone opens the chooser, never on a
  space's own hot path.
- **B2.** A persisted `space_memberships(identity_id, tenant_id, joined_at)` table in the
  registry, written as a best-effort third commit after the point of no return in
  `TenantService.provision` and `InvitationService.accept`, reconciled by a nightly job in
  the same cadence as the existing `gmail-sync`/`digest` crontab entries
  (`projects/pigrocrm/AGENTS.md:83-86`) for the rare crash-window miss. Answers in one
  query rather than N, at the cost of a second, denormalised copy of space membership that
  the registry now has to keep honest across two databases with no shared transaction —
  the exact cross-database consistency problem §1 rejected a replicated-membership home
  over.

Recommendation: B1, for the same reason `identities` itself stays minimal: this codebase's
own stated principle is "every fact is stored once," and B1 reads the one place that fact
already lives instead of duplicating it. If the number of spaces on the production SaaS
root ever makes a live per-chooser-view scan measurably slow, that is the concrete,
checkable trigger for revisiting toward B2 — not a cost worth paying up front for a
product that has, as of this writing, a number of spaces this scan handles without
difficulty.

## 8. Proposed follow-up work

Not filed as Linear issues: this milestone's own scope note is explicit that no
implementation issue is filed until Lorenzo has signed off on the session/cookie model
above. This is the order that sign-off would unlock, each line an issue-shaped title with
its own one-line "done when," for whoever files them once the decision lands.

- **Add the identity table and passwordless entry to the registry.** Done when
  `identities`, `identity_link_tokens` and `identity_sessions` exist on `TenantsBase`,
  `IdentityService` mirrors `MagicLinkService`'s `request`/`enter` shape against the
  first two, every existing login surface that already proves an email
  (`login`, `enter_with_link`, `accept_invite` — signup excluded, §2) also calls
  `upsert_and_issue` without changing its own response or cookies, and
  `POST /api/identity/logout` revokes an `identity_sessions` row. Depends on nothing;
  additive only, matching the invitations rollout's own "additive first" discipline
  (`2026-09-17-...:391-399`).
- **Serve the chooser.** Done when `GET /api/identity/spaces` and
  `POST /api/identity/enter/{slug}` exist per §3, and `login.tsx` shows the chooser instead
  of the email form whenever the identity cookie resolves to at least one space. Depends
  on the previous item.
- **Link an invitation to an existing identity automatically.** Done when accepting an
  invitation for an email that already holds an identity shows that space in the chooser on
  the very next visit, with no separate action taken by the invitee. Depends on the first
  item only; §4 is the whole change.
- **Backfill existing installations.** Done when `pigrocrm rebuild-identity-index` exists,
  follows `ensure-space-defaults`'s shape, and a run against a registry holding several
  spaces with overlapping owner emails produces one `identities` row per distinct address
  and, if B2 is chosen, one `space_memberships` row per membership found. Depends on the
  first item; not required for any of the others to be correct on day one (§6).
