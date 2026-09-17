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
(`deps.py:72`) — two dependency types resolving two cookies against two tables — and
the SPA mirrors it with two shells, `AdminLayout.tsx` and `pages/member/Guard.tsx`'s
`MemberGuard`, each with its own `useAdmin`/`useMember` query and its own redirect to
its own login screen.

This record answers what one identity looks like before REB-278 writes the migration
that merges the two tables into one.

## 1. The decisions, one paragraph each

**Identity model.** `freelancers` gains a `role` column (`VARCHAR(10)`, `NOT NULL`,
`DEFAULT 'member'`, values `member`/`admin`, validated in the service layer the way
`stato` and `compilata_da` already are — no `CHECK` constraint exists anywhere else in
this schema, and two values on a couple of rows do not need one). `freelancers` is the
identity table, not a new `persons` table beside it: the project's own name for this
milestone is "an admin is a member with one more section," `freelancers` already holds
every field a magic link needs (`nome`, `cognome`, `email` unique case-insensitively)
and every field the wizard needs is nullable since migration 0007, so an admin who
never applied gets a row with the application fields empty rather than a second table
and a second set of foreign keys to reason about (`member_sessions.freelancer_id`,
`magic_link_tokens.freelancer_id`, `comments.entity_id`, `guide_downloads.freelancer_id`,
`member_logins.freelancer_id` all keep pointing at the one table they already point
at). `admin_users` is retired: its two active rows are matched into `freelancers` by
lowercased email where one already exists, and inserted as new rows where none does,
with `role='admin'` either way (§3 has the exact migration). `AdminSession` is retired
in favour of `member_sessions`, renamed in spirit if not in the schema (§3).

**Password login.** Dropped. One way in, the magic link, for a member and an admin
alike; `password_hash` disappears with the `admin_users` table it lives in. A second
way in is a second thing to secure, and REB-270 exists because an argon2 verify (64
MiB, time-cost 3) is expensive enough that a rate limit has to run before it, not after
— the same cost this hub's own `AdminService.authenticate` pays on every attempt
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
then Istanze Pigro, La guida, Accessi, Amministratori, Agenti — Lorenzo's own
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
reversible, since nothing is deleted — a real improvement on today's page, which has
no deactivation at all (`Admins.tsx:38`, "No deactivation and no deletion here, on
purpose").

**Admin creation from now on.** `rebase createadmin` (`cli.py:23-45`, prompts for a
password) is replaced by `rebase setrole --email a@b.it --role admin` (and
`--role member` to demote), the shape of `createtoken`
(`cli.py:48-68`): looks up `freelancers` by lowercased email, creates a minimal row
(`nome`/`cognome` from `--nome`/`--cognome`, prompted if missing) when none exists,
sets `role`, and — for a promotion of a brand-new row — calls
`MemberService.request_link` so the new admin's first entrance is the same magic link
everyone else gets, rather than a password printed to a terminal. `createadmin` is
removed outright, not aliased: nothing needs a password-creating path once none of the
API accepts one.

**MCP tokens.** `admin_tokens.admin_id` (`models.py:229-247`, FK to `admin_users.id`)
is repointed to `freelancers.id` in the same migration that retires `admin_users`
(§3), renamed `person_id` for clarity, so the table survives `admin_users`'s drop with
every row intact — REB-287 does not need a migration of its own for that. What REB-287
still needs to do: `AdminTokenService.resolve()` (`admin_tokens.py:94-108`) today
proves a token belongs to a row in a table that, by construction, only ever held
admins; once that table is `freelancers`, a token pointing at a row whose `role` has
been demoted to `member` must fail the same way a revoked or unknown token does
(`INVALID_TOKEN`, `admin_tokens.py:28`), checked at resolve time and not only at mint
time — so demoting an admin kills their running agents on the next call without a
separate revoke. Minting and listing (`routers/tokens.py`, all three routes already
behind `AdminDep`) keep working once `AdminDep` means "role is admin" instead of
"exists in `admin_users`"; the «Agenti» page moves under the merged shell unchanged in
function. No expiry, as REB-213 already decided and REB-287's own card repeats.

**PostHog identify.** `useIdentifyAdmin`/`useIdentifyMember`
(`apps/web/src/lib/analytics.ts:52-74`) collapse into one `useIdentify` call from the
single shell (REB-279), fired once per signed-in person with `role` as the property it
already supports (`analytics.ts:54,62`, the `ruolo` argument exists and is unused for
a member today only because nothing calls it that way). Admins keep being marked
internal (`initAnalytics`'s `setInternalOrTestUser`, `browser.ts`) so the wizard
funnels do not count them; `resetUser()` on the one logout path clears the one
identity. No new events: this is REB-280's whole scope, wiring what already exists to
fire once instead of twice.

**Rollout order.** 278, then 279, then 280, then 281, each its own PR and each safe to
run in production alone — no feature flag, because a flag would have to gate the
identity model itself and there is nowhere to hide half a login. §5 has the reasoning
per card; the short version is that 278 adds the new mechanism beside the old one
without removing anything the still-unmigrated SPA calls, so the old admin login,
`AdminLayout` and `orbiters_admin` keep working until 279 replaces their only caller,
and only 281, last, deletes what by then has none. The first deploy (278) needs
nothing on the host beyond the migration that already runs in the API image's `CMD`
(`projects/hub/Dockerfile.api:28,31`, "Migrations at start-up, one instance"); the
only manual step is optional and cosmetic, fixing the placeholder `cognome` the
migration leaves on an admin row it had to invent from scratch (§3).

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

Two migrations, in two PRs, expand then contract — the same discipline
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
INSERT INTO freelancers (id, nome, cognome, email, links, stato, compilata_da, role,
                          created_at, updated_at)
SELECT gen_random_uuid(), au.nome, '', au.email, '[]'::jsonb, 'nuovo', 'persona',
       'admin', au.created_at, au.updated_at
FROM admin_users au
WHERE au.attivo
  AND NOT EXISTS (SELECT 1 FROM freelancers f WHERE lower(f.email) = lower(au.email));

ALTER TABLE admin_tokens ADD COLUMN person_id UUID REFERENCES freelancers(id);
UPDATE admin_tokens t SET person_id = f.id
FROM admin_users au JOIN freelancers f ON lower(f.email) = lower(au.email)
WHERE t.admin_id = au.id;
ALTER TABLE admin_tokens ALTER COLUMN person_id SET NOT NULL;
DROP INDEX ix_admin_tokens_admin_id;
ALTER TABLE admin_tokens DROP COLUMN admin_id;
CREATE INDEX ix_admin_tokens_person_id ON admin_tokens (person_id);
```

`admin_users` and `admin_sessions` are **not** touched by this migration: they keep
existing, unindexed by any new code, purely so the still-unmigrated SPA (until REB-279
lands) can keep logging admins in the old way while 278's API already offers the new
one beside it. Rollback within this same deploy window (before an admin has logged in
through the new path) is a straight reverse of the SQL above; past that window — once
a real session or a freshly minted token exists only in the new shape — the honest
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

`deps.py` (`apps/api/src/rebase_api/deps.py`) drops `ADMIN_COOKIE`, `get_admin` and
`AdminDep` in their current shape and gains, per REB-278's own wording:

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

def get_admin(me: MeDep) -> MemberProfile:
    """`me`, refused with 403 when the role is not admin: a signed-in non-admin is a
    real identity hitting the wrong door, not an anonymous one."""
    if me.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Serve il ruolo di amministratore")
    return me

AdminDep = Annotated[MemberProfile, Depends(get_admin)]
```

`MemberProfile` (`packages/core/src/rebase_core/schemas.py:480-503`) gains a `role`
field so the SPA can draw its nav from one response. `routers/admin.py`'s `login`
(lines 80-104), `LoginRequest`, `AdminCreate`, `AdminUpdate` and the argon2 import are
deleted once REB-279 has moved the SPA off them (§1's rollout order); every other
route in that file, `pigro.py` and the guide/login-stats routes swaps `AdminDep`'s
import for the new one with no change to the route body, since the dependency's name
is unchanged and its type is now `MemberProfile` instead of `AdminRead` (which is
deleted; nothing outside `admin.py` constructed one directly).

Two "who am I" routes exist today: `GET /api/hub/auth/me` (`admin.py:115-117`,
answers `AdminRead`) and `GET /api/hub/me` (`members.py:113-115`, answers
`MemberProfile`). One survives: `GET /api/hub/auth/me`, answering the unified
`MemberProfile` with `role`, since that is the name REB-278's own card uses for the
route the SPA reads to draw the nav. `GET /api/hub/me` is deleted; the mutation routes
that share its prefix (`PATCH /me`, `PUT /me/cv`, `GET /me/cv`, `GET /me/guida`) are
unaffected, since they change or read the caller's own row rather than answer "who is
this," and stay on `MeDep`. `POST /api/hub/auth/logout` (today's admin logout) becomes
the one logout route; `POST /api/hub/me/logout` is deleted. `GET /api/hub/admins`
(`admin.py:127-129`) keeps its path and becomes a filter on `role == 'admin'` instead
of a full table; its create/update counterparts (`admin.py:132-157`) become the
promote/demote pair described in §1, taking an email and, for a promotion of an
address with no existing row, `nome`/`cognome`.

## 5. SPA

`router.tsx` (`apps/web/src/router.tsx`) drops `adminLogin` (line 93) and its import;
`adminArea` (line 94, today `getParentRoute: () => root`) becomes a child of the same
parent the member routes already use, guarded once. `AdminLayout.tsx` and
`pages/member/Guard.tsx`'s `MemberGuard` collapse into one guard component reading
`useMe()` (the merge of today's `useAdmin`, `auth.tsx:9-23`, and `useMember`,
`member.tsx:15-29`, into one hook backed by `GET /api/hub/auth/me`): renders nothing
while pending, redirects to `/accedi` when signed out, and otherwise renders the nav
described in §1 plus an `<Outlet />` — `/hub/io`'s own guard becomes redundant and is
removed, since the outer guard already proves a session exists by the time any child
route renders. `useIdentifyAdmin`/`useIdentifyMember` (`analytics.ts:67-74`) collapse
into the one `useIdentify` call already underneath them (`analytics.ts:52-64`), called
once from the new shell with the profile's `role`. `lib/auth.tsx`'s `useLogout` and
`lib/member.tsx`'s `useMemberLogout` collapse into one mutation against
`POST /api/hub/auth/logout`. `AdminLogin.tsx` is deleted; `Admins.tsx` is rewritten to
the promote/demote list in §1, dropping `AdminCreate`'s `password` field and the
`PASSWORD_MIN_LENGTH` mirror it carries today (`Admins.tsx:22-23`).

## 6. What each child card must do

- **REB-278** (session/dep merge): migration A (§3); `MeDep`/`AdminDep` in `deps.py`
  (§4); the unified `GET /api/hub/auth/me`; `rebase setrole`; `admin_tokens` repointed
  to `person_id` in the same migration, so REB-287 inherits a clean FK. Its own card
  text says "the admin password path removed if the spec says so" — this record says
  not yet: the password route and `admin_sessions`/`admin_users` stay reachable and
  untouched through this PR, since the SPA (REB-279) has not moved off them yet, and
  removing a still-called route is the one thing "safe to deploy alone" rules out.
  Its card should also gain, explicitly, the `Admins.tsx` rewrite (§5): today's card
  text does not name that page, and without the rewrite the create form keeps posting
  a `password` field an API that no longer defines `AdminCreate.password` will 422 on.
- **REB-279** (SPA shells/guards): the one guard, the one shell, the route changes in
  §5, `/admin/login` removed, the empty-frame bug (REB-106) closed as a side effect of
  there being one guard instead of two.
- **REB-280** (PostHog): the one `useIdentify` call, wired from the new shell REB-279
  built; genuinely needs 279 to land first, since there is no single shell to call it
  from before that.
- **REB-281** (dead code): migration B (§3); deletes `AdminLogin.tsx`,
  `AdminService.resolve()`'s cookie path, `ADMIN_COOKIE`, `admin_session_days`,
  `rebase createadmin`; updates `AGENTS.md` and `README.md` (`README.md:25-27,107-113`
  today describe `/hub/admin/login` and a password prompt).
- **REB-287** (later milestone; MCP tokens): no migration of its own (§1); resolve()
  checks `role` at call time; the mint/list/revoke routes keep `AdminDep`'s new
  meaning for free.

## Open decisions for the lead

**(a) Does the admin password login survive next to the magic link?**
Options: (a1) magic link only, `password_hash` gone — this record's recommendation,
for the reason in §1. (a2) Both, the password an emergency door for when Resend is
down. Recommend **a1**: an emergency door that is exercised only during an outage of a
different system is one that has never been tested when it is needed, and the cost of
keeping it (the CLI, the form, the argon2 budget, a second thing to rotate) is paid on
every day nothing is down.

**(b) Does `admin_tokens` get repointed to `freelancers` in REB-278's migration, or
does it wait for REB-287?**
Options: (b1) repoint now (this record, §3) — REB-287 becomes purely behavioural.
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
currently points at `freelancers.id` (five tables, §1) for a separation nothing in
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
Options: (e1) one survives, `/auth/me` (this record, §4), since that is the name
REB-278's card already uses. (e2) keep both, `/me` re-exporting `/auth/me`'s answer.
Recommend **e1**: two routes answering the same question is the exact shape this whole
record exists to remove; a client migration inside one company's own SPA is not the
external-compatibility case that would justify keeping a second name.
