# The hub becomes one identity: a `users` table, and freelancer, company and admin as things a person may have

Date: 2026-09-17, revised 2026-09-18. Status: the identity model is decided (Lorenzo,
2026-09-18, § 0): a `users` table, not a `role` column on `freelancers`. Still proposed,
awaiting Lorenzo's letter on whether the admin password login survives next to the
magic link and on the four other lettered questions in § Open decisions for the lead,
now five with the company wizard's referente field. Tracker: REB-277 in `Hub v2 - one
hub, and an admin is a member with one more section`.

## 0. Why

Lorenzo, 2026-09-17: «in questo momento il pannello admin e l'area utente sono due cose
separate, invece io vorrei una cosa unica con il fatto che gli admin vedano la sezione
admin e gli altri utenti ovviamente no».

Today the hub is two logins wearing one nginx vhost. An admin posts an email and a
password to `POST /api/hub/auth/login` (`apps/api/src/rebase_api/routers/admin.py:80-104`),
which calls `AdminService.authenticate` (argon2, `packages/core/src/rebase_core/admin.py:123-133`)
and sets the `orbiters_admin` cookie (`deps.py:20`), sliding for
`settings.admin_session_days` (`config.py:56`, 30 days) against the `admin_sessions`
table (`models.py:250-265`). A member asks for a link at `POST /api/hub/auth/link`
(`apps/api/src/rebase_api/routers/members.py:65-83`), which mails a one-time token
through `MemberService.request_link` (`packages/core/src/rebase_core/members.py:59-85`)
and, once spent by `POST /api/hub/auth/enter`, sets `orbiters_user`
(`deps.py:60`) against `member_sessions` (`models.py:326-340`), sliding for
`settings.member_session_days` (`config.py:69`, also 30 days). `admin_users`
(`models.py:211-223`) and `freelancers` (`models.py:114-152`) are unrelated tables: no
row can be both, so an admin who is also a community member holds two accounts. Every
admin route depends on `AdminDep` (`deps.py:58`), every member route on `MemberDep`
(`deps.py:72`), two dependency types resolving two cookies against two tables, and
the SPA mirrors it with two shells, `AdminLayout.tsx` and `pages/member/Guard.tsx`'s
`MemberGuard`, each with its own `useAdmin`/`useMember` query and its own redirect to
its own login screen.

This record first answered what one identity looks like with `freelancers` gaining a
`role` column: a freelancer card and an admin grant on the same row. Lorenzo, 2026-09-18,
answering that draft's own § Open decisions, overturned it: «per hub però io farei una
tabella users o persons, perche ad esempio io e ivan potremmo essere dei freelancers e
anche aziende in certi contesti, ma siamo anche admin. ripensiamola bene, pulita e
ordinata. siamo ancora in tempo per stravolgere lo schema se serve». A freelancer card,
a company's own request and an admin grant are three things a person may hold at the
same time, not three values competing for one row's identity fields: Lorenzo and Ivan
are the concrete case, freelancers on some engagements and a company's own referente on
others, and admins throughout. The identity moves to its own table, `users`; a
freelancer card and a company request become things a `users` row may have, zero or
more of each. This record now describes that schema and the migration that gets there
before REB-278 writes it; § 1 explains each decision, § 3 the two migrations.

## 1. The decisions, one paragraph each

**Identity model.** A new table, `users`, one row per person, one per `lower(email)`:
`id`, `email` (unique case-insensitively, as `freelancers.email` is today), `nome`
`NOT NULL`, `cognome` `NOT NULL`, `linkedin_url` (nullable), `role` (`VARCHAR(10)`,
`NOT NULL`, `DEFAULT 'member'`, values `member`/`admin`, validated in the service layer
the way `stato` and `compilata_da` already are: two values on a handful of rows do not
buy a `user_roles` table, and this record offers that alternative in § Open decisions
rather than picking it), `attivo` (`BOOLEAN`, `NOT NULL`, `DEFAULT true`, carried over
from `admin_users.attivo` so a deactivated admin's tokens keep failing the same way; no
route flips it for anyone else yet, matching today, where nothing deactivates a
freelancer either), `created_at`, `updated_at`. `freelancers` keeps everything that is
the card, not the person: `cv_*`, `tariffa_giornaliera`, `posizione`, `remoto`, `links`,
`stato`, `note`, `compilata_da`, the UTM columns (`models.py:133-152`), and gains
`user_id` `NOT NULL UNIQUE`, at most one card per person. `companies` keeps
`nome_azienda`, `progetto`, `periodo_da`, `durata`, `budget_giornaliero`, `stato`,
`note`, the UTM columns (`models.py:162-172`), and gains `user_id` `NOT NULL`, not
unique: a person files several requests over time (several projects), same as today.
`admin_users` is retired: its rows are matched into `users` by lowercased email where
one already exists (from a freelancer card or a company request) and inserted as new
rows where none does, with `role='admin'` either way (§3 has the exact migration).
`AdminSession` is retired in favour of the one session table (§1, One session table).

The trick the first draft of this decision needed, inventing a `freelancers` row with
`stato='scartato'` for an admin with no card so every admin was, literally, a row with
a profile, is gone: an admin is a `users` row with no freelancer card and no company
request, and that is exactly the point Lorenzo's answer makes. Nothing invents a card
for anyone anymore; § 1 (Nav) and § 6 (REB-279) say what that costs the SPA.

**Password login.** Dropped. One way in, the magic link, for a member and an admin
alike; `password_hash` disappears with the `admin_users` table it lives in. A second
way in is a second thing to secure, and REB-270 exists because an argon2 verify (64
MiB, time-cost 3) is expensive enough that a rate limit has to run before it, not after,
the same cost this hub's own `AdminService.authenticate` pays on every attempt
(`admin.py:8,20`, `PasswordHasher()`). Two admins is not enough traffic to justify
carrying that cost, the CLI prompt (`cli.py:23-45`), the reset flow nobody built, and
the «Amministratori» form's password field (`Admins.tsx:22-25,35`) once the magic link
already exists for everyone. Option (b), keeping the password as an emergency door, is
argued fairly in § Open decisions: it buys a way in when Resend is down, at the cost of
every item above staying alive for a path that would be exercised only during an
outage of a different system, and of testing a fallback most logins never take.

**One session table, one cookie.** `member_sessions` gains `user_id` in migration A
(§3), keeping its shape (opaque token, sha256 at rest, sliding expiry,
`packages/core/src/rebase_core/members.py:128-148`) and becomes, once migration B
renames it `sessions` and drops its old `freelancer_id`, the one session table for
every role; `admin_sessions` is dropped in the same migration. `orbiters_user` survives
as the one cookie; `orbiters_admin` is dropped. Renaming rather than leaving
`member_sessions` as the name a session of any role now lives in follows the same rule
`REB-207`-`REB-212`/`REB-214` already applied to this codebase (DECISIONS.md,
2026-09-15): an identifier that actively misleads gets renamed, one that merely could
be clearer does not, and "member" no longer describes who is in this table once an
admin's own session lives there too. This is the direction that costs nothing on the
wire: `user_id` is backfilled from the same `freelancer_id` a session row already
carries, so the cookie a member's browser already holds resolves to the same person
through the new column the moment migration A runs, and every bookmark to `/hub/io`
keeps working across the deploy with no re-authentication, while the two admins log in
again once through the magic link, since `admin_sessions` is dropped rather than merged.
`Settings.admin_session_days` is dropped; `member_session_days` (still 30) becomes the
one sliding window, admin included.

**Nav and Amministratori.** The admin's sidebar (`AdminLayout.tsx:9-18`, eight flat
entries) becomes two visual groups behind one divider: Talenti and Aziende first (the
work REB-282/283 rename and merge; today "Developer e CTO", "Aziende", "Iscrizioni"),
then Istanze Pigro, La guida, Accessi, Amministratori, Agenti, Lorenzo's own
recommendation in the card, kept rather than losing five pages nobody asked to remove.
A "La tua area" entry to `/hub/io` sits above both groups for anyone signed in, admin
or not, since every signed-in person is now, literally, a `users` row; unlike the first
draft of this decision, that row is not guaranteed to carry a freelancer card, so the
page behind it renders the card only when one exists (§ 5 and REB-279 in § 6). Only the
two admin groups are conditional on `role === 'admin'`. «Amministratori» stops being a
create-with-password form (`Admins.tsx`, `AdminCreate` with `password`,
`routers/admin.py:52-64,132-136`) and becomes a promote/demote list: type an email, and
whatever `users` row already answers to it, whether it already carries a freelancer
card, a company's own request, or neither, is promoted with one click and no form; an
address with no `users` row prompts for `nome` and `cognome` only and creates a bare
row with `role='admin'` before promoting it, no freelancer card invented for it. Demoting
sets `role='member'` and is now fully reversible, since nothing is deleted, a real
improvement on today's page, which has no deactivation at all (`Admins.tsx:38`, "No
deactivation and no deletion here, on purpose").

**Admin creation from now on.** `rebase createadmin` (`cli.py:23-45`, prompts for a
password) is replaced by `rebase setrole --email a@b.it --role admin` (and
`--role member` to demote), the shape of `createtoken` (`cli.py:48-68`): looks up
`users` by lowercased email, creates a minimal row (`nome`/`cognome` from
`--nome`/`--cognome`, prompted if missing) when none exists, sets `role`, and, for a
promotion of a brand-new row, sends the same magic link everyone else gets rather than
a password printed to a terminal. `createadmin` is removed outright, not aliased:
nothing needs a password-creating path once none of the API accepts one.

**MCP tokens.** `admin_tokens.admin_id` (`models.py:240`, FK to `admin_users.id`) is
repointed to `users.id` in the same migration that retires `admin_users` (§3), renamed
`user_id`, so the table survives `admin_users`'s drop with every row intact. The code
that reads that column moves in the same PR, REB-278, not in REB-287:
`AdminTokenService.create` looks the owner up in `admin_users` and checks `attivo`
(`admin_tokens.py:67-69`), `revoke` and `list` filter on `AdminToken.admin_id`
(`admin_tokens.py:85,117`), `resolve` does `session.get(AdminUser, row.admin_id)`
(`admin_tokens.py:103-104`), and the MCP server ships from the same image as the API
(`Dockerfile.api:20-25`, `docker-compose.yml:67-76`), so a migration that renamed the
column under code still reading it would take every MCP call and all three
`/api/hub/tokens` routes down the moment it ran. REB-278 therefore repoints
`AdminTokenService` to `User` (owner exists, `role == 'admin'`), and the type the
chain is built on: `AdminRead` is constructed in `admin_tokens.py:108`, imported in
`deps.py:11`, and is what the MCP server is typed on end to end (`AdminProvider =
Callable[[], AdminRead]`, `apps/mcp/src/rebase_mcp/server.py:42`; `_CURRENT_ADMIN` and
the `isinstance(admin, AdminRead)` gate, `apps/mcp/src/rebase_mcp/actor.py:19,44`;
`_authenticate`, `apps/mcp/src/rebase_mcp/http.py:168-171`), so those four modules keep
typing on `AdminRead`, now a thin read of a `users` row (`id`, `email`, `nome`,
`attivo`, `created_at`, no card fields, no join) rather than of `admin_users`. What is
left for REB-287 is the behaviour: `resolve()` today proves a token belongs to a row in
a table that only ever held admins; once that table is `users`, a token pointing at a
row whose `role` has been demoted to `member` must fail the same way a revoked or
unknown token does (`INVALID_TOKEN`, `admin_tokens.py:28`), checked at resolve time and
not only at mint time, so demoting an admin kills their running agents on the next call
without a separate revoke. The «Agenti» page moves under the merged shell unchanged in
function. No expiry, as REB-213 already decided and REB-287's own card repeats.

**PostHog identify.** `useIdentifyAdmin`/`useIdentifyMember`
(`apps/web/src/lib/analytics.ts:52-74`) collapse into one `useIdentify` call from the
single shell (REB-279), fired once per signed-in person with `role` as the property.
`useIdentify` already takes a `ruolo` argument (`analytics.ts:54,62`) but types it
`'admin'` only and drops a falsy value, so REB-280 widens it to `'admin' | 'member'`
and always sends it; unaffected by moving the identity table, since the property was
always meant to read straight off the signed-in person, and now it can, because
`role` lives on the one row every signed-in person has (`users`) instead of only on
the freelancer row a `MemberProfile` happened to be. Admins keep being marked
internal (`initAnalytics`'s `setInternalOrTestUser`, `browser.ts`) so the wizard
funnels do not count them; `resetUser()` on the one logout path clears the one
identity. No new events: this is REB-280's whole scope, wiring what already exists to
fire once instead of twice.

**Rollout order.** 278, then 279, then 280, then 281, each its own PR and each safe to
run in production alone, no feature flag, because a flag would have to gate the
identity model itself and there is nowhere to hide half a login. "Safe alone" has to be
earned by what 278 leaves untouched, since the old SPA keeps calling the old routes
until 279 replaces it: `POST /api/hub/auth/login`, `GET /api/hub/auth/me` (which the old
`useAdmin` reads, `lib/api.ts:294`, `auth.tsx:9-23`, and whose 401 sends `AdminLayout`
back to `/admin/login`, `AdminLayout.tsx:28-30`), `POST /api/hub/auth/logout` and the
`/admins` create/update pair (`Admins.tsx` still posts a `password`) keep their bodies
in 278. The unified identity lands beside them: `GET /api/hub/me`, answering the new
`MeRead` (§4) off `orbiters_user`; the new `get_admin` accepts, until 281, either
`orbiters_user` with `role == 'admin'` or a live `orbiters_admin` session, so the admin
routes serve the old SPA and the new one alike; the promote/demote routes are added as
new paths next to the old pair. 279 moves the SPA to `/api/hub/me`, the one guard and
the promote/demote page; 281 deletes the password routes, the legacy cookie branch,
`admin_sessions` and `admin_users`, which by then have no caller. §5 has the detail per
card. The first deploy (278) needs nothing on the host beyond the migration that
already runs in the API image's `CMD` (`projects/hub/Dockerfile.api:28,31`, "Migrations
at start-up, one instance"); the one manual step after it is fixing the `cognome` of
whichever `users` rows decision (d) below leaves blank: every row backfilled from
`admin_users.nome` alone and every row backfilled from a `companies.referente` that was
a single word, however many that turns out to be once the migration runs against the
real data (§3).

## 2. The route map

| Route | Today | After this milestone |
|---|---|---|
| `/hub/accedi` | Asks for the email, mails a member a link | Unchanged; asks for anyone's email, admin included |
| `/hub/entra?t=` | Spends the token, opens a member session | Unchanged; opens the one session for anyone |
| `/hub/io` | Member's own profile, `MemberGuard` | Unchanged path and guard shape; "La tua area" for every signed-in person, admin included, the card shown only where one exists (§1, §5) |
| `/hub/io/modifica` | Edit the profile | Unchanged; reachable only for a person with a card |
| `/hub/admin/login` | Email + password form | Removed (REB-281), once REB-279 has moved its only caller off it |
| `/hub/admin/*` | `AdminLayout`, its own guard | Same paths, same components, now children of the one shell's guard; reachable only from the nav when `role === 'admin'` |

A member's bookmark to `/hub/io` on their phone needs nothing from them: the path does
not move, the guard's shape does not move, and the cookie it depends on
(`orbiters_user`) is the one that survives the migration. A direct hit on `/hub/admin/*`
by a signed-in non-admin lands on `/hub/io` with an Italian sentence, never on a login
form (REB-279's own acceptance line); a signed-out visitor to any of the above still
lands on `/hub/accedi`, exactly as a signed-out member does today.

## 3. Data model and migration

Two migrations, in two PRs, expand then contract, the same discipline
`projects/hub/AGENTS.md` § The database was inherited already asks of every migration
here, because production rows are on the line.

**Migration A (REB-278, next number after 0010 at the time it lands; called `0011`
below).**

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(320) NOT NULL,
    nome VARCHAR(120) NOT NULL,
    cognome VARCHAR(120) NOT NULL,
    linkedin_url VARCHAR(300),
    role VARCHAR(10) NOT NULL DEFAULT 'member',
    attivo BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email));

-- 1. Every freelancer is a user; the card already wrote both names.
INSERT INTO users (id, email, nome, cognome, linkedin_url, role, attivo, created_at, updated_at)
SELECT gen_random_uuid(), lower(f.email), f.nome, f.cognome, f.linkedin_url,
       'member', true, f.created_at, f.updated_at
FROM freelancers f;

-- 2. Every admin, active or not, who is not already a user from step 1: `au.nome` is
--    one field, not two, so `cognome` is left blank on purpose (decision (d) below is
--    the one manual statement that fixes it, once, for however many rows this inserts).
INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, updated_at)
SELECT gen_random_uuid(), lower(au.email), au.nome, '', 'admin', au.attivo,
       au.created_at, au.updated_at
FROM admin_users au
WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.email = lower(au.email));

-- Promote whoever already got a row from step 1 and is also an active admin. A
-- deactivated admin who is also a freelancer stays 'member' and attivo=true: `attivo`
-- is the person's own flag now (§1), and nothing deactivates a freelancer today.
UPDATE users u SET role = 'admin'
FROM admin_users au
WHERE u.email = lower(au.email) AND au.attivo;

-- 3. Every company referente with no row from steps 1-2: the request is the only trace
--    of that address. `referente` is one string (decision (f) asks the wizard to
--    collect two from now on); `cognome` is blank here too, same reasoning as step 2.
--    A repeated referente across several requests from the same address picks one
--    arbitrarily (`DISTINCT ON`, oldest request): the two rarely disagree in practice,
--    and where they do, decision (d)'s manual pass is where it gets fixed.
INSERT INTO users (id, email, nome, cognome, created_at, updated_at)
SELECT DISTINCT ON (lower(c.email))
       gen_random_uuid(), lower(c.email), c.referente, '', c.created_at, c.created_at
FROM companies c
WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.email = lower(c.email))
ORDER BY lower(c.email), c.created_at;

ALTER TABLE freelancers ADD COLUMN user_id UUID REFERENCES users(id);
UPDATE freelancers f SET user_id = u.id FROM users u WHERE u.email = lower(f.email);
ALTER TABLE freelancers ALTER COLUMN user_id SET NOT NULL;
CREATE UNIQUE INDEX uq_freelancers_user_id ON freelancers (user_id);

ALTER TABLE companies ADD COLUMN user_id UUID REFERENCES users(id);
UPDATE companies c SET user_id = u.id FROM users u WHERE u.email = lower(c.email);
ALTER TABLE companies ALTER COLUMN user_id SET NOT NULL;
CREATE INDEX ix_companies_user_id ON companies (user_id);

ALTER TABLE admin_tokens ADD COLUMN user_id UUID REFERENCES users(id);
UPDATE admin_tokens t SET user_id = u.id
FROM admin_users au JOIN users u ON u.email = lower(au.email)
WHERE t.admin_id = au.id;
-- A token whose owner matched no user cannot happen after steps 1-2 above (every
-- admin_users row becomes a users row), but the statement stays: `NULL` here would
-- abort the `SET NOT NULL` below, which inside the API image's `CMD` is a crash loop,
-- and a defensive `DELETE` costs one line against a table that must never grow this way.
DELETE FROM admin_tokens WHERE user_id IS NULL;
ALTER TABLE admin_tokens ALTER COLUMN user_id SET NOT NULL;
CREATE INDEX ix_admin_tokens_user_id ON admin_tokens (user_id);

ALTER TABLE member_sessions ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE CASCADE;
UPDATE member_sessions s SET user_id = f.user_id
FROM freelancers f WHERE s.freelancer_id = f.id;
ALTER TABLE member_sessions ALTER COLUMN user_id SET NOT NULL;
CREATE INDEX ix_member_sessions_user_id ON member_sessions (user_id);

ALTER TABLE magic_link_tokens ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE CASCADE;
UPDATE magic_link_tokens t SET user_id = f.user_id
FROM freelancers f WHERE t.freelancer_id = f.id;
ALTER TABLE magic_link_tokens ALTER COLUMN user_id SET NOT NULL;
CREATE INDEX ix_magic_link_tokens_user_id ON magic_link_tokens (user_id);

ALTER TABLE member_logins ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE CASCADE;
UPDATE member_logins l SET user_id = f.user_id
FROM freelancers f WHERE l.freelancer_id = f.id;
ALTER TABLE member_logins ALTER COLUMN user_id SET NOT NULL;
CREATE INDEX ix_member_logins_user_id ON member_logins (user_id);

ALTER TABLE guide_downloads ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE CASCADE;
UPDATE guide_downloads d SET user_id = f.user_id
FROM freelancers f WHERE d.freelancer_id = f.id;
ALTER TABLE guide_downloads ALTER COLUMN user_id SET NOT NULL;
CREATE INDEX ix_guide_downloads_user_id ON guide_downloads (user_id);
```

`admin_users` and `admin_sessions` are **not** touched by this migration beyond being
read from: they keep existing, unindexed by any new code, purely so the
still-unmigrated SPA (until REB-279 lands) can keep logging admins in the old way while
278's API already offers the new one beside it.

**Every writer and reader of the columns this migration moves off, so REB-278 knows
what to change in the same PR** (dual-write is not wanted: the code below moves to
reading and writing `users`/`user_id` in this PR, and the old columns are simply left
untouched, neither read nor written, until migration B drops them, so a rollback within
this deploy window is a straight reverse of the SQL above):

| Column | Where it is written or read today |
|---|---|
| `freelancers.email/nome/cognome/linkedin_url` | `models.py:133-136` (columns); `freelancers.py:151,161-163` (`apply` writes them on a new row); `freelancers.py:199,201-203` (`draft_from_signup` writes them); `freelancers.py:236-237,262,271,289,321-322` (every email lookup and join: `list_recent`, `_leads`, `get`, `_find`); `members.py:85` (`request_link` mails `row.email`); `members.py:171` (`lookup` answers `row.nome`/`row.cognome`); `members.py:229` (`_comment`'s author string); `members.py:241-242` (`_by_email`); `schemas.py:546-568` (`FreelancerRead`); `schemas.py:480-490` (`MemberProfile`) |
| `companies.referente/email` | `models.py:163-164` (columns); `companies.py:26-29` (`request` writes them on a new row); `schemas.py:610-616` (`CompanyRead`); `schemas.py:410-415` (`CompanyCreate` collects `referente` as one field, decision (f)) |
| `admin_tokens.admin_id` | `models.py:240` (column, FK `admin_users.id`); `admin_tokens.py:67,72` (`create`); `admin_tokens.py:85` (`revoke`); `admin_tokens.py:103-104` (`resolve`); `admin_tokens.py:117` (`list`); `apps/api/src/rebase_api/routers/tokens.py:31,36-37,42,44` (the three routes); `cli.py:56-61` (`createtoken`) |
| `member_sessions.freelancer_id` | `models.py:333-335` (column); `members.py:118-119` (`enter` opens a session); `members.py:143` (`resolve` reads the owner) |
| `magic_link_tokens.freelancer_id` | `models.py:281-283` (column); `members.py:71` (`request_link`'s sweep-delete); `members.py:78` (the insert); `members.py:104` (`enter` reads the owner) |
| `member_logins.freelancer_id` | `models.py:301-303` (column); `members.py:124` (`enter` writes the login); `freelancers.py:83-87,236,284-286` (`_logins_per_card`, `list_recent`, `get`); `logins.py:28,40-41,53` (`LoginService.stats`) |
| `guide_downloads.freelancer_id` | `models.py:318-320` (column); `apps/api/src/rebase_api/routers/members.py:160` (the route calls `record_guide_download`); `perks.py:48-51` (the insert); `perks.py:57-59,71-72,84` (`guide_stats`) |

Every one of these moves in REB-278: the service functions above stop constructing
`Freelancer`/`Company`/`AdminToken`/`MemberSession`/`MagicLinkToken`/`MemberLogin`/
`GuideDownload` rows from `nome`/`cognome`/`email`/`referente`/`admin_id` and instead
get-or-create the owning `users` row first (by lowercased email) and use its `id`;
every read that today does `func.lower(Freelancer.email)` or reads `row.nome` off a
`Freelancer`/`AdminUser` row joins `users` instead. `FreelancerService`/`CompanyService`
gain a dependency on the new identity module (`rebase_core.users`, § 4) that owns that
get-or-create, so the two do not duplicate the email-matching logic `MemberService`
already has today. Rollback past this deploy window, once a real session or a freshly
minted token exists only in the new shape, is the honest restore-from-backup this
record already recommended for the freelancers-based draft, not an automatic
`downgrade()`, which is why the migration's own `downgrade()` should refuse with a
clear message rather than pretend to be safe.

**Migration B (REB-281, `0012`).**

```sql
ALTER TABLE freelancers DROP COLUMN nome, DROP COLUMN cognome, DROP COLUMN email,
    DROP COLUMN linkedin_url;
DROP INDEX uq_freelancers_email_lower;
ALTER TABLE companies DROP COLUMN referente, DROP COLUMN email;
ALTER TABLE admin_tokens DROP COLUMN admin_id;
ALTER TABLE member_sessions DROP COLUMN freelancer_id;
ALTER TABLE magic_link_tokens DROP COLUMN freelancer_id;
ALTER TABLE member_logins DROP COLUMN freelancer_id;
ALTER TABLE guide_downloads DROP COLUMN freelancer_id;
ALTER TABLE member_sessions RENAME TO sessions;
ALTER TABLE member_logins RENAME TO logins;
DROP TABLE admin_sessions;
DROP TABLE admin_users;
```

`member_sessions`/`member_logins` rename to `sessions`/`logins` here rather than in A,
because the old and new FK coexist through the whole A-to-B window and a table named
for the FK it no longer solely serves (`freelancer_id` beside `user_id`) is already
accurate enough to ship; the rename only pays for itself once `freelancer_id` is gone
and "member" would otherwise be the one word left implying a table an admin's own
session also lives in. Nothing else references `admin_users`/`admin_sessions`/the
dropped columns by the time this runs (REB-281's own acceptance: "a grep for
`orbiters_admin`, `AdminDep`, `admin_sessions` in the tree answers only migrations and
the spec"), and `password_hash` goes with `admin_users` in one statement. Downgrade
recreates every dropped table and column empty (structure only): the rows are gone,
which is correct, since every one of them was already carried into `users` by migration
A, and dropping empty tables back in is only there so `alembic downgrade` does not
error on a missing table if something else in the chain needs to go further back.

`compare_metadata` (`test_migrations.py`, both after A and after B) is the proof each
migration matches the model it is paired with.

## 4. API

`deps.py` (`apps/api/src/rebase_api/deps.py`) gains, per REB-278's own wording, one
dependency for "whoever is signed in" and one for "signed in and admin", both now
resolving a `users` row through a new identity service (`rebase_core.users`, replacing
the parts of `MemberService` and `AdminService` that resolved cookies and matched
addresses, `members.py:52-242` and `admin.py:40-181`, with the card-specific and
admin-list-specific pieces staying in place):

```python
MEMBER_COOKIE = "orbiters_user"  # unchanged name and value

def get_me(request: Request, session: SessionDep, settings: SettingsDep) -> MeRead:
    """Whoever the cookie resolves to, member or admin: the one dependency every signed-in
    route may depend on."""
    me = UserService(session, settings).resolve(request.cookies.get(MEMBER_COOKIE))
    if me is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    return me

MeDep = Annotated[MeRead, Depends(get_me)]

def get_admin(request: Request, session: SessionDep, settings: SettingsDep) -> AdminRead:
    """A signed-in person whose role is admin. Until REB-281 a live `orbiters_admin`
    session is accepted too, so the SPA that still logs in with a password keeps
    working across the 278 deploy; that branch is deleted with the password login."""
    users = UserService(session, settings)
    me = users.resolve_admin(request.cookies.get(MEMBER_COOKIE))
    if me is not None:
        return me
    legacy = AdminService(session, settings).resolve(request.cookies.get(ADMIN_COOKIE))
    if legacy is not None:
        # Migration A guarantees every active admin_users row became a users row,
        # matched by email; a brand-new password admin created between 278 and 279 is
        # the one gap this legacy branch cannot close, same as the freelancers-based
        # draft of this record already accepted.
        return users.by_email(legacy.email)
    if users.resolve(request.cookies.get(MEMBER_COOKIE)) is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Serve il ruolo di amministratore")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")

AdminDep = Annotated[AdminRead, Depends(get_admin)]
```

A signed-in non-admin is a real identity hitting the wrong door, so it gets 403, not
401. `GET /api/hub/me` answers a new `MeRead` (replacing `MemberProfile`, since a
signed-in person is no longer necessarily an applicant): `id`, `nome`, `cognome`,
`email`, `linkedin_url`, `role`, `created_at`, `updated_at` off `users`, plus `ha_scheda`
(`bool`) and, when it is true, the freelancer card's own fields (`cv_filename`,
`cv_size`, `tariffa_giornaliera`, `posizione`, `remoto`, `links`, `completa`), all
`None`/`false`/empty when it is false, which is the shape a signed-in admin with no
card now answers with. `AdminRead` (`admin.py:23-33`) keeps its current four fields
(`id`, `email`, `nome`, `attivo`, `created_at`, no card, no join) and is what
`AdminDep`, `GET /api/hub/admins` and the MCP chain (§1, MCP tokens) type on, sourced
by `select(User).where(User.role == 'admin')` instead of `admin_users`.
`FreelancerRead` (`schemas.py:546-590`) keeps every field it has today; `nome`,
`cognome`, `email` and `linkedin_url` are read off the joined `users` row from 278 on,
the way `_read_with_logins` (`freelancers.py:98-105`) already joins `MemberLogin` and
`Signup` in. `CompanyRead` (`schemas.py:610-633`) gains the same join for `referente`
(or its replacement per decision (f)) and `email`.

`MemberLookup` (`schemas.py:520-528`) is unchanged in shape: `membro`, `nome`,
`cognome`. What changes is the query behind `POST /members/lookup`
(`apps/api/src/rebase_api/routers/members.py:164-188`): today it is `_by_email` on
`Freelancer.email` (`members.py:241-242`); once identity moves to `users`, answering
`membro=True` for any address with a `users` row would be wrong, since a company's own
referente or a card-less admin is not "a member" in the sense PigroCRM's onboarding
means (ORB-173, "whether a freelancer with it exists"). The lookup therefore becomes an
inner join, `users` matched by lowercased email **and** a `freelancers` row on
`user_id`, so it keeps answering exactly what it answers today, now through two tables
instead of one. PigroCRM's own caller (`pigrocrm/core/tenants/hub.py`) reads only
`{membro, nome, cognome}` and is untouched by this record: the shape it depends on does
not move.

Every route that depends on `AdminDep` today (`routers/admin.py`, `pigro.py`,
`tokens.py`, the guide and login-stats routes) keeps its body, since the dependency's
name is unchanged and the only attributes those bodies read, `id` and `nome`
(`admin.py:242,267,283`, `tokens.py:32,37,44`), exist on the new `AdminRead` exactly as
they did on the old one.

Two "who am I" routes exist today: `GET /api/hub/auth/me` (`admin.py:115-117`, answers
`AdminRead` off `orbiters_admin`) and `GET /api/hub/me` (`members.py:113-115`, answers
`MemberProfile` off `orbiters_user`). The second is already the unified one in shape,
so it survives and answers `MeRead` in 278, and `GET /api/hub/auth/me` goes in 281 with
the password login it belongs to; REB-278's card names `/auth/me` as the route the SPA
reads and needs the one-word correction. The same split applies to logout:
`POST /api/hub/me/logout` survives, `POST /api/hub/auth/logout` goes in 281. The
mutation routes under `/me` (`PATCH /me`, `PUT /me/cv`, `GET /me/cv`, `GET /me/guida`)
are unaffected in path and stay on `MeDep`, refusing with a clear sentence for a
signed-in person with no card where a card is what the route needs (`PATCH /me` and
`PUT /me/cv` today assume one exists; an admin with no card hitting them is a new case
this record's card-less admin introduces, and it is a 404 named "scheda", the same
sentence `NotFound(ENTITY, ...)` already gives a wrong id). `GET /api/hub/admins`
(`admin.py:127-129`) keeps its path and becomes a filter on `role == 'admin'` instead
of a full table; the promote/demote pair described in §1 lands in 278 as two new routes
(`POST /api/hub/admins/promote` with `email` and, for an address with no row,
`nome`/`cognome`; `POST /api/hub/admins/{id}/demote`), beside today's create/update
pair (`admin.py:132-157`), which the old `Admins.tsx` still posts a `password` to and
which 281 deletes once 279 has moved the page.

## 5. SPA

`router.tsx` (`apps/web/src/router.tsx`) drops `adminLogin` (line 93) and its import,
and gains one layout route under `root`, `signedInLayout`, whose component is the one
guard: `AdminLayout.tsx` and `pages/member/Guard.tsx`'s `MemberGuard` collapse into it,
reading `useMe()` (the merge of today's `useAdmin`, `auth.tsx:9-23`, and `useMember`,
`member.tsx:15-29`, into one hook backed by `GET /api/hub/me`): renders nothing while
pending, redirects to `/accedi` when signed out, and otherwise renders the nav described
in §1 plus an `<Outlet />`. `adminArea` (line 94, today `getParentRoute: () => root`)
and `io` (line 89, today a child of `publicLayout`) both move under `signedInLayout`;
`io` leaves the public marketing `Shell` (`router.tsx:40-48`, the one `publicLayout`
wraps its children in), since the merged shell is its shell from now on, and `/hub/io`'s
own guard becomes redundant and is removed. `/accedi`, `/entra`, the wizards and
`/grazie` stay under `publicLayout` exactly as today. `useIdentifyAdmin`/`useIdentifyMember`
(`analytics.ts:67-74`) collapse into the one `useIdentify` call already underneath them
(`analytics.ts:52-64`), called once from the new shell with the profile's `role`.
`lib/auth.tsx`'s `useLogout` and `lib/member.tsx`'s `useMemberLogout` collapse into one
mutation against `POST /api/hub/me/logout`. `AdminLogin.tsx` is deleted; `Admins.tsx`
is rewritten to the promote/demote list in §1, dropping `AdminCreate`'s `password`
field and the `PASSWORD_MIN_LENGTH` mirror it carries today (`Admins.tsx:22-23`).

`pages/member/Area.tsx` (`/hub/io`'s content) unconditionally calls
`toApplication(profile)` and renders `FREELANCER_FIELDS` (`Area.tsx:19-24`), which
assumes a freelancer card exists: correct for every member today, wrong for a
card-less admin once one can sign in with no card at all. Once `MeRead` carries
`ha_scheda`, `Area.tsx` renders the card section only when it is true and, when it is
not, shows the person's name, email and role alone with no wizard-shaped content to
read from `null` fields.

The company wizard's `CompanyCreate` (`schemas.py:410-421`) collects `referente` as one
field; decision (f) below asks it to collect two, `nome`/`cognome`, the shape
`FreelancerCreate` already asks for one screen earlier in the freelancer wizard. The
form change is REB-279's, since 279 is already the PR touching every wizard-adjacent
page for the merged shell.

## 6. What each child card must do

- **REB-278** (session/dep merge): migration A (§3); the `User` model and a new
  `rebase_core.users` module owning `_by_email`/get-or-create/`resolve`/session
  open-close/the magic link's request-and-enter, moved out of `MemberService`
  (`members.py:52-242`) and `AdminService` (`admin.py:40-181`), which keep only what is
  specific to a freelancer card and to the admin list/CRUD respectively; `MeDep`/
  `AdminDep` in `deps.py` resolving `users` rows, with the transitional `orbiters_admin`
  branch (§4); the new `MeRead` on `GET /api/hub/me`; the promote/demote routes beside
  the old create/update pair; `rebase setrole`; `rebase createtoken` repointed
  (`cli.py:48-68`, since today it resolves its owner through `AdminService.list()` over
  `admin_users` and would mint against a dropped column); `admin_tokens` repointed to
  `user_id` and `AdminTokenService`, `deps.py`, `apps/mcp`'s `server.py`, `actor.py` and
  `http.py` moved to `User`-backed `AdminRead` in the same PR (§1, MCP tokens);
  `FreelancerService.apply`/`draft_from_signup` (`freelancers.py:112-223`) and
  `CompanyService.request` (`companies.py:22-38`) rewritten to get-or-create a `users`
  row instead of writing `freelancers.nome/cognome/email/linkedin_url` and
  `companies.referente/email` directly (§3's table). Its own card text says "the admin
  password path removed if the spec says so", and this record says not yet: the
  password route, `GET /api/hub/auth/me`, its logout and `admin_sessions`/`admin_users`
  stay reachable and untouched through this PR, since the SPA (REB-279) has not moved
  off them yet, and removing a still-called route is the one thing "safe to deploy
  alone" rules out. The card's `/auth/me` reads `/me` after this record (§4).
- **REB-279** (SPA shells/guards): `signedInLayout`, the one guard, the one shell, the
  route moves in §5, `/admin/login` removed from the router, the SPA on `GET /api/hub/me`
  and `POST /api/hub/me/logout`, `Admins.tsx` rewritten to promote/demote (today's card
  text does not name that page, and without the rewrite the create form keeps posting a
  `password` to a route 281 deletes), the empty-frame bug (REB-106) closed as a side
  effect of there being one guard instead of two, `pages/member/Area.tsx` gated on
  `ha_scheda` so a card-less admin's "La tua area" does not read from a card that is not
  there (§5), and the company wizard's `referente` field split into `nome`/`cognome`
  (decision (f)).
- **REB-280** (PostHog): the one `useIdentify` call, wired from the new shell REB-279
  built, with `ruolo` widened to `'admin' | 'member'`, sourced from `users.role`;
  genuinely needs 279 to land first, since there is no single shell to call it from
  before that.
- **REB-281** (dead code): migration B (§3); deletes `POST /api/hub/auth/login`,
  `GET /api/hub/auth/me`, `POST /api/hub/auth/logout`, the `/admins` create/update pair,
  `AdminLogin.tsx`, `AdminService.resolve()`'s cookie path, the legacy
  branch of `get_admin`, `ADMIN_COOKIE`, `admin_session_days`, `rebase createadmin`;
  updates `AGENTS.md` and `README.md` (`README.md:25-27,107-122` today describe
  `/hub/admin/login`, a password prompt and `rebase createtoken`).
- **REB-287** (later milestone; MCP tokens): no migration and no repoint of its own
  (§1); `resolve()` checks `role` on the `users` row at call time; the mint/list/revoke
  routes keep `AdminDep`'s new meaning for free.

## Open decisions for the lead

**(a) Does the admin password login survive next to the magic link?**
Options: (a1) magic link only, `password_hash` gone, this record's recommendation,
for the reason in §1. (a2) Both, the password an emergency door for when Resend is
down. Recommend **a1**: an emergency door that is exercised only during an outage of a
different system is one that has never been tested when it is needed, and the cost of
keeping it (the CLI, the form, the argon2 budget, a second thing to rotate) is paid on
every day nothing is down. Unaffected by the identity table: `admin_users` and
`password_hash` still exist until migration B whichever way the identity question
answered.

**(b) Does `admin_tokens` get repointed to `users` in REB-278's migration, or does it
wait for REB-287?**
Options: (b1) repoint now, `admin_tokens.user_id → users.id` (this record, §3),
REB-287 becomes purely behavioural. (b2) leave `admin_tokens.admin_id → admin_users.id`
until REB-287, which means `admin_users` cannot be dropped in REB-281 and survives as a
table with no other purpose until milestone 4 ships. Recommend **b1**: a table kept
alive for one foreign key, for a milestone whose own estimate (3 points) says it is not
imminent, is exactly the debt REB-287's card describes finding.

**(c) Is the identity table `freelancers` with a `role` column, a new `persons` table,
or a `users` table a freelancer card and a company request both hang off?**
Decided. Lorenzo, 2026-09-18: «per hub però io farei una tabella users o persons,
perche ad esempio io e ivan potremmo essere dei freelancers e anche aziende in certi
contesti, ma siamo anche admin. ripensiamola bene, pulita e ordinata. siamo ancora in
tempo per stravolgere lo schema se serve» (§0). A `users` table: `freelancers` and
`companies` become things a `users` row may have, zero or one freelancer card and any
number of company requests, and an admin is a `users` row with neither, not a
freelancer row wearing an extra column. This record's first draft recommended c1
(`freelancers` gains `role`) over a `persons` table on the grounds that the separation
"touches every foreign key that currently points at `freelancers.id`... for a
separation nothing in this milestone or the next three needs"; Lorenzo's answer is that
a company request is exactly that separation, needed now, since he and Ivan are
freelancers on some engagements and a company's own referente on others, at the same
time, today. §1 and §3 describe the schema this decides.

**(d) The migrated admin's and company referente's blank `cognome`: fixed by hand, or
does the migration ask for names up front?**
Options: (d1) insert with `cognome=''` wherever the only source is a single string
(`admin_users.nome` or `companies.referente`), fixed by one `UPDATE` after the deploy
(this record, §3). (d2) the migration takes a small hardcoded mapping of email →
nome/cognome for the known rows. Recommend **d1**: d2 puts a real person's name in a
migration file, reviewed and merged before anyone has confirmed the spelling, and now
covers more than the two admins the first draft counted, since every company referente
whose name is one word backfills the same way; d1 costs one manual statement, once, for
however many rows that turns out to be.

**(e) Does `GET /api/hub/me` survive alongside `GET /api/hub/auth/me`, or does one of
them go?**
Options: (e1) one survives, `/me` (this record, §4), because it already answers the
unified shape off the cookie that survives, so 278 only widens it to `MeRead` and the
old SPA keeps `/auth/me` untouched until 279 moves; REB-278's card, which names
`/auth/me`, gets the one-word correction. (e2) keep both, `/auth/me` re-exporting
`/me`'s answer. Recommend **e1**: two routes answering the same question is the exact
shape this whole record exists to remove; a client migration inside one company's own
SPA is not the external-compatibility case that would justify keeping a second name.

**(f) The company wizard collects `referente` as one string; `users.nome`/`cognome` are
`NOT NULL`. Does the wizard split it into two fields, or does the whole string go into
`nome`?**
Options: (f1) split `referente` into `referente_nome`/`referente_cognome` on the wizard
(`CompanyCreate`, `schemas.py:410-421`), matching what the freelancer wizard already
asks a screen earlier. (f2) keep one field, store it whole in `users.nome`, leave
`cognome=''` the way a migration-only backfill does, indefinitely, for every company
request from now on. Recommend **f1**: f2 makes every future company contact a
permanent instance of decision (d)'s stopgap rather than a one-time migration cost, and
the two-field form is not new work invented for this milestone, it is the freelancer
wizard's own layout copied one page over.
