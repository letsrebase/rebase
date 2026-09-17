# The hub becomes one identity: a magic link for everyone, a role that says admin

Date: 2026-09-17. Status: proposed, awaiting Lorenzo's decision on whether the admin
password login survives next to the magic link, and on the four other lettered
questions in § Open decisions for the lead. Tracker: REB-277 in `Hub v2 - one hub, and
an admin is a member with one more section`.

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

This record answers what one identity looks like before REB-278 writes the migration
that merges the two tables into one.

## 1. The decisions, one paragraph each

**Identity model.** `freelancers` gains a `role` column (`VARCHAR(10)`, `NOT NULL`,
`DEFAULT 'member'`, values `member`/`admin`, validated in the service layer the way
`stato` and `compilata_da` already are, no `CHECK` constraint exists anywhere else in
this schema, and two values on a couple of rows do not need one). `freelancers` is the
identity table, not a new `persons` table beside it: the project's own name for this
milestone is "an admin is a member with one more section," `freelancers` already holds
every field a magic link needs (`nome`, `cognome`, `email` unique case-insensitively)
and every field the wizard needs is nullable since migration 0007, so an admin who
never applied gets a row with the application fields empty rather than a second table
and a second set of foreign keys to reason about (`member_sessions.freelancer_id`,
`magic_link_tokens.freelancer_id`, `guide_downloads.freelancer_id` and
`member_logins.freelancer_id` all keep pointing at the one table they already point
at; `comments.entity_id` is a bare UUID by design, `models.py:189-196`, and needs
nothing). `admin_users` is retired: its two active rows are matched into `freelancers` by
lowercased email where one already exists, and inserted as new rows where none does,
with `role='admin'` either way (§3 has the exact migration). `AdminSession` is retired
in favour of `member_sessions`, renamed in spirit if not in the schema (§3).

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

**One session table, one cookie.** `member_sessions` survives unchanged in shape
(opaque token, sha256 at rest, sliding expiry, `packages/core/src/rebase_core/members.py:128-148`)
and becomes the one session table for every role; `admin_sessions` is dropped.
`orbiters_user` survives as the one cookie; `orbiters_admin` is dropped. This is the
direction that costs nothing on the wire: every member's browser already carries
`orbiters_user`, so a member's existing session, and every bookmark to `/hub/io`, keeps
working across the deploy with no re-authentication, while the two admins log in again
once through the magic link. The other direction would force every member to
re-authenticate to save two people a login. `Settings.admin_session_days` is dropped;
`member_session_days` (still 30) becomes the one sliding window, admin included.

**Nav and Amministratori.** The admin's sidebar (`AdminLayout.tsx:9-18`, eight flat
entries) becomes two visual groups behind one divider: Talenti and Aziende first (the
work REB-282/283 rename and merge; today "Developer e CTO", "Aziende", "Iscrizioni"),
then Istanze Pigro, La guida, Accessi, Amministratori, Agenti, Lorenzo's own
recommendation in the card, kept rather than losing five pages nobody asked to remove.
A "La tua area" entry to `/hub/io` sits above both groups for anyone signed in,
admin or not, since an admin is now also, literally, a row with a profile. Only the
two admin groups are conditional on `role === 'admin'`. «Amministratori» stops being a
create-with-password form (`Admins.tsx`, `AdminCreate` with `password`,
`routers/admin.py:52-64,132-136`) and becomes a promote/demote list: type an email:
an existing freelancer with that address is promoted with one click and no form; an
address with no row prompts for `nome` and `cognome` only (the two fields
`freelancers` requires that `admin_users.nome` alone did not) and creates the row with
`role='admin'` before promoting it. Demoting sets `role='member'` and is now fully
reversible, since nothing is deleted, a real improvement on today's page, which has
no deactivation at all (`Admins.tsx:38`, "No deactivation and no deletion here, on
purpose").

**Admin creation from now on.** `rebase createadmin` (`cli.py:23-45`, prompts for a
password) is replaced by `rebase setrole --email a@b.it --role admin` (and
`--role member` to demote), the shape of `createtoken`
(`cli.py:48-68`): looks up `freelancers` by lowercased email, creates a minimal row
(`nome`/`cognome` from `--nome`/`--cognome`, prompted if missing) when none exists,
sets `role`, and, for a promotion of a brand-new row, calls
`MemberService.request_link` so the new admin's first entrance is the same magic link
everyone else gets, rather than a password printed to a terminal. `createadmin` is
removed outright, not aliased: nothing needs a password-creating path once none of the
API accepts one.

**MCP tokens.** `admin_tokens.admin_id` (`models.py:229-247`, FK to `admin_users.id`)
is repointed to `freelancers.id` in the same migration that retires `admin_users`
(§3), renamed `person_id`, so the table survives `admin_users`'s drop with every row
intact. The code that reads that column moves in the same PR, REB-278, not in REB-287:
`AdminTokenService.create` looks the owner up in `admin_users` and checks `attivo`
(`admin_tokens.py:67-69`), `revoke` and `list` filter on `AdminToken.admin_id`
(`admin_tokens.py:85,117`), `resolve` does `session.get(AdminUser, row.admin_id)`
(`admin_tokens.py:103-104`), and the MCP server ships from the same image as the API
(`Dockerfile.api:20-25`, `docker-compose.yml:67-76`), so a migration that renamed the
column under code still reading it would take every MCP call and all three
`/api/hub/tokens` routes down the moment it ran. REB-278 therefore repoints
`AdminTokenService` to `Freelancer` (owner exists, `role == 'admin'`), and the type the
chain is built on: `AdminRead` is constructed in `admin_tokens.py:108`, imported in
`deps.py:11`, and is what the MCP server is typed on end to end (`AdminProvider =
Callable[[], AdminRead]`, `apps/mcp/src/rebase_mcp/server.py:42`; `_CURRENT_ADMIN` and
the `isinstance(admin, AdminRead)` gate, `apps/mcp/src/rebase_mcp/actor.py:19,44`;
`_authenticate`, `apps/mcp/src/rebase_mcp/http.py:168-171`), so those four modules
swap `AdminRead` for `MemberProfile` in the same cutover. What is left for REB-287 is
the behaviour: `resolve()` today proves a token belongs to a row in a table that only
ever held admins; once that table is `freelancers`, a token pointing at a row whose
`role` has been demoted to `member` must fail the same way a revoked or unknown token
does (`INVALID_TOKEN`, `admin_tokens.py:28`), checked at resolve time and not only at
mint time, so demoting an admin kills their running agents on the next call without a
separate revoke. The «Agenti» page moves under the merged shell unchanged in function.
No expiry, as REB-213 already decided and REB-287's own card repeats.

**PostHog identify.** `useIdentifyAdmin`/`useIdentifyMember`
(`apps/web/src/lib/analytics.ts:52-74`) collapse into one `useIdentify` call from the
single shell (REB-279), fired once per signed-in person with `role` as the property.
`useIdentify` already takes a `ruolo` argument (`analytics.ts:54,62`) but types it
`'admin'` only and drops a falsy value, so REB-280 widens it to `'admin' | 'member'`
and always sends it. Admins keep being marked
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
in 278. The unified identity lands beside them: `GET /api/hub/me`, which already answers
`MemberProfile` off `orbiters_user` (`members.py:113-115`), gains `role`; the new
`get_admin` accepts, until 281, either `orbiters_user` with `role == 'admin'` or a live
`orbiters_admin` session, so the admin routes serve the old SPA and the new one alike;
the promote/demote routes are added as new paths next to the old pair. 279 moves the
SPA to `/api/hub/me`, the one guard and the promote/demote page; 281 deletes the
password routes, the legacy cookie branch, `admin_sessions` and `admin_users`, which by
then have no caller. §5 has the detail per card. The first deploy (278) needs nothing
on the host beyond the migration that already runs in the API image's `CMD`
(`projects/hub/Dockerfile.api:28,31`, "Migrations at start-up, one instance"); the one
manual step after it is fixing the `cognome` and the `stato` of an admin row the
migration had to invent from scratch (§3).

## 2. The route map

| Route | Today | After this milestone |
|---|---|---|
| `/hub/accedi` | Asks for the email, mails a member a link | Unchanged; asks for anyone's email, admin included |
| `/hub/entra?t=` | Spends the token, opens a member session | Unchanged; opens the one session for anyone |
| `/hub/io` | Member's own profile, `MemberGuard` | Unchanged path and guard shape; "La tua area" for every signed-in person, admin included |
| `/hub/io/modifica` | Edit the profile | Unchanged |
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
ALTER TABLE freelancers ADD COLUMN role VARCHAR(10) NOT NULL DEFAULT 'member';

-- Promote whoever already has both rows.
UPDATE freelancers f SET role = 'admin'
FROM admin_users au
WHERE lower(f.email) = lower(au.email) AND au.attivo;

-- Whoever is an active admin with no freelancer row gets a minimal one. `cognome` has
-- no source in admin_users (one `nome` field, not two) and is left blank on purpose:
-- an operator fixes it by hand once, for however many rows this inserts (two, today).
-- `stato='scartato'` and `compilata_da='admin'` keep an invented admin row out of the
-- «Developer e CTO» list and its totals (`FreelancerService.list_recent`,
-- `freelancers.py:225-237`) and leave the ORB-155 research path free to fill the card
-- later, which `compilata_da='persona'` would block for good (`freelancers.py:194`).
INSERT INTO freelancers (id, nome, cognome, email, links, stato, compilata_da, role,
                          created_at, updated_at)
SELECT gen_random_uuid(), au.nome, '', au.email, '[]'::jsonb, 'scartato', 'admin',
       'admin', au.created_at, au.updated_at
FROM admin_users au
WHERE au.attivo
  AND NOT EXISTS (SELECT 1 FROM freelancers f WHERE lower(f.email) = lower(au.email));

ALTER TABLE admin_tokens ADD COLUMN person_id UUID REFERENCES freelancers(id);
UPDATE admin_tokens t SET person_id = f.id
FROM admin_users au JOIN freelancers f ON lower(f.email) = lower(au.email)
WHERE t.admin_id = au.id;
-- A token whose owner is deactivated already resolves to nothing
-- (`admin_tokens.py:103-105`); it has no person to belong to, and left NULL it would
-- abort the SET NOT NULL below, which inside the API image's CMD is a crash loop.
DELETE FROM admin_tokens WHERE person_id IS NULL;
ALTER TABLE admin_tokens ALTER COLUMN person_id SET NOT NULL;
DROP INDEX ix_admin_tokens_admin_id;
ALTER TABLE admin_tokens DROP COLUMN admin_id;
CREATE INDEX ix_admin_tokens_person_id ON admin_tokens (person_id);
```

`admin_users` and `admin_sessions` are **not** touched by this migration: they keep
existing, unindexed by any new code, purely so the still-unmigrated SPA (until REB-279
lands) can keep logging admins in the old way while 278's API already offers the new
one beside it. Rollback within this same deploy window (before an admin has logged in
through the new path) is a straight reverse of the SQL above; past that window, once
a real session or a freshly minted token exists only in the new shape, the honest
answer is restoring `admin_users`/`admin_sessions` from the pre-migration backup, not
an automatic `downgrade()`, which is why the migration's own `downgrade()` should
refuse with a clear message rather than pretend to be safe.

**Migration B (REB-281, `0012`).**

```sql
DROP TABLE admin_sessions;
DROP TABLE admin_users;
```

Nothing else references either table by the time this runs (that is REB-281's own
acceptance: "a grep for `orbiters_admin`, `AdminDep`, `admin_sessions` in the tree
answers only migrations and the spec"), and `password_hash` goes with `admin_users` in
one statement rather than a separate `ALTER TABLE ... DROP COLUMN`. Downgrade recreates
both tables empty (structure only, `0003`'s definitions): the rows are gone, which is
correct, since every one of them was already carried into `freelancers` by migration A
and dropping empty tables back in is only there so `alembic downgrade` does not error
on a missing table if something else in the chain needs to go further back.

`compare_metadata` (`test_migrations.py`, both after A and after B) is the proof each
migration matches the model it is paired with.

## 4. API

`deps.py` (`apps/api/src/rebase_api/deps.py`) gains, per REB-278's own wording, one
dependency for "whoever is signed in" and one for "signed in and admin":

```python
MEMBER_COOKIE = "orbiters_user"  # unchanged name and value

def get_me(request: Request, session: SessionDep, settings: SettingsDep) -> MemberProfile:
    """Whoever the cookie resolves to, member or admin: the one dependency every signed-in
    route may depend on."""
    me = MemberService(session, settings).resolve(request.cookies.get(MEMBER_COOKIE))
    if me is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    return me

MeDep = Annotated[MemberProfile, Depends(get_me)]

def get_admin(request: Request, session: SessionDep, settings: SettingsDep) -> MemberProfile:
    """A signed-in person whose role is admin. Until REB-281 a live `orbiters_admin`
    session is accepted too, so the SPA that still logs in with a password keeps
    working across the 278 deploy; that branch is deleted with the password login."""
    me = MemberService(session, settings).resolve(request.cookies.get(MEMBER_COOKIE))
    if me is not None and me.role == "admin":
        return me
    if me is None:
        legacy = AdminService(session, settings).resolve(request.cookies.get(ADMIN_COOKIE))
        if legacy is not None:
            # Migration A guarantees a freelancers row per active admin, matched by email.
            members = MemberService(session, settings)
            return members.profile(members._by_email(legacy.email).id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticazione richiesta")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Serve il ruolo di amministratore")

AdminDep = Annotated[MemberProfile, Depends(get_admin)]
```

A signed-in non-admin is a real identity hitting the wrong door, so it gets 403, not
401. `MemberProfile` (`packages/core/src/rebase_core/schemas.py:480-503`) gains a `role`
field so the SPA can draw its nav from one response. Every route that depends on
`AdminDep` today (`routers/admin.py`, `pigro.py`, `tokens.py`, the guide and login-stats
routes) keeps its body, since the dependency's name is unchanged and the only
attributes those bodies read, `id` and `nome` (`admin.py:242,267,283`,
`tokens.py:32,37,44`), exist on `MemberProfile` as they did on `AdminRead`. `AdminRead`
itself is replaced by `MemberProfile` in the four places that construct or type on it
(§1, MCP tokens), in REB-278, and deleted in REB-281 with the password login that
answers it.

Two "who am I" routes exist today: `GET /api/hub/auth/me` (`admin.py:115-117`, answers
`AdminRead` off `orbiters_admin`) and `GET /api/hub/me` (`members.py:113-115`, answers
`MemberProfile` off `orbiters_user`). The second is already the unified one in shape,
so it survives and gains `role` in 278, and `GET /api/hub/auth/me` goes in 281 with the
password login it belongs to; REB-278's card names `/auth/me` as the route the SPA
reads and needs the one-word correction. The same split applies to logout:
`POST /api/hub/me/logout` survives, `POST /api/hub/auth/logout` goes in 281. The
mutation routes under `/me` (`PATCH /me`, `PUT /me/cv`, `GET /me/cv`, `GET /me/guida`)
are unaffected and stay on `MeDep`. `GET /api/hub/admins` (`admin.py:127-129`) keeps
its path and becomes a filter on `role == 'admin'` instead of a full table; the
promote/demote pair described in §1 lands in 278 as two new routes
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

## 6. What each child card must do

- **REB-278** (session/dep merge): migration A (§3); `MeDep`/`AdminDep` in `deps.py`
  with the transitional `orbiters_admin` branch (§4); `role` on `GET /api/hub/me`; the
  promote/demote routes beside the old create/update pair; `rebase setrole`; `rebase
  createtoken` repointed, since today it resolves its owner through `AdminService.list()`
  over `admin_users` (`cli.py:56-61`) and would mint against a dropped column;
  `admin_tokens` repointed to `person_id` in the same migration and `AdminTokenService`,
  `deps.py`, `apps/mcp`'s `server.py`, `actor.py` and `http.py` moved to `Freelancer`
  and `MemberProfile` in the same PR (§1, MCP tokens). Its own card text says "the admin
  password path removed if the spec says so", and this record says not yet: the password
  route, `GET /api/hub/auth/me`, its logout and `admin_sessions`/`admin_users` stay
  reachable and untouched through this PR, since the SPA (REB-279) has not moved off
  them yet, and removing a still-called route is the one thing "safe to deploy alone"
  rules out. The card's `/auth/me` reads `/me` after this record (§4).
- **REB-279** (SPA shells/guards): `signedInLayout`, the one guard, the one shell, the
  route moves in §5, `/admin/login` removed from the router, the SPA on `GET /api/hub/me`
  and `POST /api/hub/me/logout`, `Admins.tsx` rewritten to promote/demote (today's card
  text does not name that page, and without the rewrite the create form keeps posting a
  `password` to a route 281 deletes), the empty-frame bug (REB-106) closed as a side
  effect of there being one guard instead of two.
- **REB-280** (PostHog): the one `useIdentify` call, wired from the new shell REB-279
  built, with `ruolo` widened to `'admin' | 'member'`; genuinely needs 279 to land
  first, since there is no single shell to call it from before that.
- **REB-281** (dead code): migration B (§3); deletes `POST /api/hub/auth/login`,
  `GET /api/hub/auth/me`, `POST /api/hub/auth/logout`, the `/admins` create/update pair,
  `AdminRead`, `AdminLogin.tsx`, `AdminService.resolve()`'s cookie path, the legacy
  branch of `get_admin`, `ADMIN_COOKIE`, `admin_session_days`, `rebase createadmin`;
  updates `AGENTS.md` and `README.md` (`README.md:25-27,107-122` today describe
  `/hub/admin/login`, a password prompt and `rebase createtoken`).
- **REB-287** (later milestone; MCP tokens): no migration and no repoint of its own
  (§1); `resolve()` checks `role` at call time; the mint/list/revoke routes keep
  `AdminDep`'s new meaning for free.

## Open decisions for the lead

**(a) Does the admin password login survive next to the magic link?**
Options: (a1) magic link only, `password_hash` gone, this record's recommendation,
for the reason in §1. (a2) Both, the password an emergency door for when Resend is
down. Recommend **a1**: an emergency door that is exercised only during an outage of a
different system is one that has never been tested when it is needed, and the cost of
keeping it (the CLI, the form, the argon2 budget, a second thing to rotate) is paid on
every day nothing is down.

**(b) Does `admin_tokens` get repointed to `freelancers` in REB-278's migration, or
does it wait for REB-287?**
Options: (b1) repoint now (this record, §3), REB-287 becomes purely behavioural.
(b2) leave `admin_tokens.admin_id → admin_users.id` until REB-287, which means
`admin_users` cannot be dropped in REB-281 and survives as a table with no other
purpose until milestone 4 ships. Recommend **b1**: a table kept alive for one foreign
key, for a milestone whose own estimate (3 points) says it is not imminent, is exactly
the debt REB-287's card describes finding.

**(c) Is the identity table `freelancers` with a `role` column, or a new `persons`
table?**
Options: (c1) `freelancers` gains `role` (this record). (c2) a `persons` table holding
`id`/`email`/`nome`/`cognome`/`role`, with `freelancers` gaining a `person_id` and
losing `nome`/`cognome`/`email`. Recommend **c1**: c2 touches every foreign key that
currently points at `freelancers.id` (four tables, §1) for a separation nothing in
this milestone or the next three needs; c2 would be worth it the day a company needs a
login of its own, which nobody has asked for (member area spec, "Deliberately not in
this step").

**(d) The migrated admin's blank `cognome`: fixed by hand, or does the migration ask
for names up front?**
Options: (d1) insert with `cognome=''`, fixed by one `UPDATE` after the deploy (this
record). (d2) the migration takes a small hardcoded mapping of email → nome/cognome
for the two known admins. Recommend **d1**: d2 puts a real person's name in a
migration file, reviewed and merged before anyone has confirmed the spelling; d1 costs
one manual statement, once, for two rows.

**(e) Does `GET /api/hub/me` survive alongside `GET /api/hub/auth/me`, or does one of
them go?**
Options: (e1) one survives, `/me` (this record, §4), because it already answers
`MemberProfile` off the cookie that survives, so 278 only adds `role` to it and the old
SPA keeps `/auth/me` untouched until 279 moves; REB-278's card, which names `/auth/me`,
gets the one-word correction. (e2) keep both, `/auth/me` re-exporting `/me`'s answer.
Recommend **e1**: two routes answering the same question is the exact shape this whole
record exists to remove; a client migration inside one company's own SPA is not the
external-compatibility case that would justify keeping a second name.
