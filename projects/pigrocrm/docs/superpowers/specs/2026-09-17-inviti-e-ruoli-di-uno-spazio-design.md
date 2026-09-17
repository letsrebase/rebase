# A space gains people by invitation, not by an admin typing them a password

Date: 2026-09-17. Status: proposed, awaiting Lorenzo's decision on invitations as their
own table versus an inactive user row, on removing the password-based `POST /api/users`,
and on the exact reach of the one-active-admin rule. Tracker: REB-289 in `PigroCRM v3 - a
space has a team`. In English, per the repository rule; the product strings stay in
Italian.

## 0. Why

The only way a second person gets into a space today is `POST /api/users`
(`apps/api/src/pigrocrm_api/routers/users.py:18-20`), which requires a password at least
ten characters long (`MIN_PASSWORD_LENGTH`, `packages/core/src/pigrocrm/core/auth/schemas.py:10`)
that the admin types into the «Nuovo utente» dialog
(`apps/web/src/features/settings/UsersPanel.tsx:202-283`) and hands to the new person
however they can — the dialog's own copy says so: «Comunicala tu all'utente: il sistema
non invia email in questa versione» (`UsersPanel.tsx:207-209`). Lorenzo, 2026-09-17:
«dobbiamo aggiungere la possibilità di invitare altri utenti nella tua istanza con anche
una gestione dei permessi (direi ruoli fissi assegnabili agli utenti per ora)».

This is the second time this product removes a password handoff for exactly this reason.
The 2026-09-12 onboarding spec did it for the first person in a space: signup asks for no
password, `TenantService.provision` creates the first admin with `password=None`
(`packages/core/src/pigrocrm/core/tenants/service.py:148-153`) and mails a magic link
instead. Every person after the first still gets the old treatment. The machinery to fix
it already exists and only needs a second application: `MagicLinkService`
(`packages/core/src/pigrocrm/core/auth/magic_link.py`) hashes a token, expires it and
spends it exactly once; `pigrocrm.core.mail` (`packages/core/src/pigrocrm/core/mail.py`)
sends it; `User.ruolo` already carries `admin`, `collaboratore`, `readonly`
(`packages/core/src/pigrocrm/core/auth/models.py:22`, `packages/core/src/pigrocrm/core/actor.py:9`).
What is missing is the record that an invitation exists at all, independent of any user
row, so it can expire, be revoked, and be resent without ever having created an account
nobody asked for.

## 1. The decisions, one paragraph each

**Storage: a table of its own, `invitations`, in the space's own database.** Not a
half-formed `User` row. The alternative — a `users` row created at invite time with no
password and `attivo=False` — reads tidy until revoke: an admin who reconsiders an
invitation has to delete a user row that MCP tools, the timeline
(`GET /api/users/{id}/timeline`, `apps/api/src/pigrocrm_api/routers/users.py:43-64`) and
every foreign key touching `users.id` were written to assume is permanent, and the
address stays claimed by a soft-deleted row that a later, legitimate signup for the same
email has to work around. A dedicated table has no such row: `email` frees the moment the
invitation is revoked or expires, exactly as the card recommends, and nothing downstream
needs to learn that a "user" can be provisional. The cost is one join to answer «is this
email already invited or already a member», which `UserService.create`'s own conflict
check (`auth/service.py:87-88`) and a parallel check on `invitations` both already have to
do for the "already an active user" case regardless of which storage wins. This is the
one open question I have not resolved myself; see the last section.

**The token: the magic link's own shape, at seven days.** SHA-256 of a
`secrets.token_urlsafe(32)` value, spent with the same conditional `UPDATE` idiom
`MagicLinkService.enter` already uses
(`packages/core/src/pigrocrm/core/auth/magic_link.py:72-80`): `UPDATE invitations SET
accepted_at = now() WHERE id = :id AND accepted_at IS NULL AND revoked_at IS NULL
RETURNING id`, so two racing spends of the same raw token (a mail scanner's prefetch
against the real click) still create exactly one user. Seven days, not fifteen minutes:
an invitation is not a login, it is handed to someone who may not open their mail until
tomorrow, and revocation — not a short fuse — is what takes back an invitation sent to
the wrong address. Unlike the magic link, an invitation's three failure states are told
apart rather than folded into one sentence: «scaduto», «revocato» and «già usato» read
differently to an admin who just revoked one and to a person who let one sit for eight
days, and telling them apart costs nothing here — the oracle risk `INVALID_LINK`'s single
sentence (`apps/api/src/pigrocrm_api/routers/auth.py:197`) guards against is guessing a
password or an email, not distinguishing outcomes of a 32-byte token nobody can guess in
the first place. `invitations.revoked_at` and `.accepted_at` staying on the row (never
deleted) is what makes the distinction possible after the fact.

**The acceptance page, `/<slug>/app/invito?t=`.** Modelled on
`apps/web/src/routes/app/entra.tsx`, which is the existing page for the same family of
problem (spend a token from the query string, open a session, leave for the Home) but not
reused directly: `entra.tsx` auto-spends on mount with no visible state
(`EnterPage`, `entra.tsx:28-77|85-90`) because a magic link has nothing to show before
spending it. An invitation does — the space's name and who invited the person — so the
page needs a read before the spend: a `GET /api/auth/invito?t=` peek that answers
`{spazio, invitato_da, nome}` (`nome` is `null` when the invitation carried none) without
touching `accepted_at`, followed by a `POST /api/auth/invito` with `{t, nome}` (the second
`nome` required only when the peek's was `null`) that behaves like `enter_with_link`
(`auth.py:309-338`): spends the token, opens the session with the same cookie pair, and
the page then calls the same `homeAfterEntry()` this spec re-lands on
(`entra.tsx:12-16`). One button, «Entra nello spazio». A name field only when the peek
said none. A dead link — expired, revoked, already used — renders the matching sentence
and the same «Torna al login» affordance `entra.tsx:70-72` already draws, because asking
the admin to send another invitation is the actual next step, not asking again for a
magic link.

**The mail copy, in Italian, in the same voice `magic_link_mail` and `welcome_mail`
already use** (`packages/core/src/pigrocrm/core/mail.py:227-344`): no name in the
greeting when none is known, the token's window and its single use stated plainly, a
`PigroCRM` sign-off, nothing that could be mistaken for a password. Draft:

```
Oggetto: Sei stato invitato in <nome-spazio> su PigroCRM

Ciao,

<nome-invitante> ti ha invitato a entrare nello spazio PigroCRM di <nome-spazio>.
Basta un click, senza scegliere una password:

<url-invito>

Il link vale sette giorni e funziona una volta sola. Se non te lo aspettavi, ignora
questa mail: non succede niente.

PigroCRM
```

A resend sends the same mail again with a fresh token; there is no separate "reminder"
copy, the same way `MagicLinkService.request` does not distinguish a first request from a
repeat one (`magic_link.py:34-56`).

**Resend and revoke act on the one pending row, never append a second one.** Resend
overwrites `token_hash` and pushes `expires_at` out another seven days on the same
`invitations` row — the old raw value stops working the instant the hash it matched is
gone, which is «the old one dead» with no separate revocation bookkeeping. Revoke sets
`revoked_at` and nothing else; the row stays, because a revoked invitation is exactly the
record an admin needs to see was undone, and `accepted_at`/`revoked_at` both being `NULL`
is the one property that makes a row "pending" for every other check in this document.

**A space keeps one active admin, always.** The check belongs where the two ways to lose
one live: `UserService.update` (`auth/service.py:120-151`), the single method behind both
a role change and a deactivation (`PATCH /api/users/{id}`,
`apps/api/src/pigrocrm_api/routers/users.py:28-30`) — a write that would leave zero users
with `ruolo="admin"` and `attivo=True` in the space is refused before it is applied, with
an Italian sentence naming the reason («lo spazio deve avere almeno un amministratore
attivo»). Acceptance never has to defend against this: the invitation flow only ever adds
a user, through `UserService.create` (see below), and `create` cannot reduce anybody's
role or activation — the rule is stated here because REB-289 is where the product commits
to it, and REB-292 is where `update`'s guard is actually written; I list it in the
decisions section because the CLI recovery it leaves open (`pigrocrm createadmin`,
`packages/core/src/pigrocrm/core/cli.py:38-62`) needs SSH access to the server, which a
self-hosted operator locked out by their own click may not have at hand.

**The three roles hold exactly as they do today, and the matrix is not a table.**
`admin`, `collaboratore`, `readonly` (`Role`, `actor.py:9`); `WRITE_ROLES = ("admin",
"collaboratore")` and `ADMIN_ROLES = ("admin",)` (`actor.py:11-12`) are what
`Actor.require_write`/`Actor.require_admin` check (`actor.py:194-202`), called
service-by-service rather than read from one central table — which is exactly the gap
REB-293 exists to close with a generated route sweep. Nothing about the invitation flow
introduces a fourth role or a per-record permission; `POST /api/users/invites` and its
siblings are new call sites of the same two checks (`require_write`/`require_admin`), not
new authorization machinery.

**`POST /api/users` with a password is removed.** The endpoint stays open only to admin
identity questions no invitation needs to answer, and keeping two ways to add a person —
one that mails a token, one that hands over a password by hallway — is the two-mechanism
problem this repository's own DECISIONS.md warns against everywhere else (row
2026-09-09, "one deployable starts serving what another served"). `UserService.create`
itself (`auth/service.py:66-118`) is untouched: it already refuses `password=None` from
anyone but `Actor.system()` (`service.py:69-79`), which is exactly what
`TenantService.provision` and the invitation's own acceptance path (below) both rely on.
Only the REST route that lets an authenticated admin type a password for somebody else
goes. `pigrocrm createadmin` (`cli.py:38-62`) is unaffected: it calls `UserService.create`
directly, never the API, and exists for a different problem — bootstrapping the very
first account on an installation with nobody in it yet, and recovering a space with zero
active admins, neither of which an invitation (which needs an existing admin to send it)
can do. Eight test files call the endpoint today, purely as setup for a second logged-in
actor of a given role; they move to the core service or the fixture already used for
exactly that (see § Tests).

## 2. Data model and migration

`invitations`, next open revision after `0035_digest_settimanale.py` (`0036` as of this
writing; whoever implements REB-290 must recheck `packages/core/migrations/versions`
against `origin/main` first, the same rule the 2026-09-12 plan states for its own
migration number). Modelled on `MagicLinkToken`
(`packages/core/src/pigrocrm/core/auth/magic_models.py`) and its migration
(`migrations/versions/0034_magic_links.py:36-63`):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | |
| `email` | `String(320)` | Lowercased on write, like `User.email` (`auth/models.py:17`). |
| `nome` | `String(200)`, nullable | Carried only when the admin typed one; `NOME_MAX_LENGTH` (`auth/schemas.py:29`). |
| `ruolo` | `String(20)` | One of `Role`'s three values. |
| `token_hash` | `String(64)`, unique, indexed | SHA-256 hex, same function as `magic_link.py:24-25`. |
| `invited_by` | UUID, FK `users.id` | The admin who sent it; shown on the acceptance page and in «Inviti in attesa». |
| `expires_at` | `DateTime(timezone=True)` | `created_at + 7 days`; reset on resend. |
| `accepted_at` | `DateTime(timezone=True)`, nullable | Set once, by the conditional `UPDATE`. |
| `revoked_at` | `DateTime(timezone=True)`, nullable | Set by the revoke action. |
| `created_at`, `updated_at` | via `TimestampMixin`, like every other model | |

Indexes: `token_hash` unique (lookup key); a **partial** unique index on `lower(email)`
`WHERE accepted_at IS NULL AND revoked_at IS NULL`, the same functional-index shape
`uq_users_email_lower` already uses (`auth/models.py:42`), so at most one *pending*
invitation exists per address at a time — a second `POST /api/users/invites` for an email
already pending hits this index and comes back as the same `Conflict`→409 pattern
`UserService.create`'s own `IntegrityError` handler already follows
(`auth/service.py:99-117`). The predicate matters: it excludes exactly the two terminal
states, so the same address can be invited again once its earlier invitation was revoked
or accepted (an accepted invitation's email is by then also a real user, caught by the
separate "already an active user" check below, not by this index). This is the one place
in the design where a partial index's predicate has to be checked against every state the
caller means to exclude, and it is: pending only, both terminal states let a retry
through.

An email that already owns an **active user** in the space is a 409 raised by the service
before any row is written, in Italian, as REB-290's own body specifies — a plain
`self.repo.get_by_email` read against `users`, the same call `UserService.create` already
makes (`service.py:87`).

## 3. API

New service, `pigrocrm.core.auth.invitations.InvitationService`, alongside
`MagicLinkService` and `PatService`, both of which it imitates:

| Endpoint | Who | Behaviour |
|---|---|---|
| `POST /api/users/invites` | admin, verified identity | Creates the row, mails the invitation, writes the audit trail. 409 on a pending duplicate or an existing active user for that email. |
| `GET /api/users/invites` | admin | Pending invitations only (`accepted_at IS NULL AND revoked_at IS NULL`), newest first — the list «Inviti in attesa» reads. |
| `POST /api/users/invites/{id}/resend` | admin | New token, `expires_at` reset, mail sent again. 404 for another space's or an already-terminal invitation. |
| `DELETE /api/users/invites/{id}` | admin | Sets `revoked_at`; 204; 404 if already terminal or not found. |
| `GET /api/auth/invito` | anyone, unauthenticated | Peek: `{spazio, invitato_da, nome}` or one of three problem sentences. Never spends the token. |
| `POST /api/auth/invito` | anyone holding a valid token | Spends it, creates the user, opens the session, answers `UserRead` like `enter_with_link`. |

The four `/api/users/invites` routes belong in the existing
`apps/api/src/pigrocrm_api/routers/users.py`, which already owns the `/api/users` prefix
(`users.py:15`) — a second `APIRouter` under the same prefix would only add ceremony. The
two `/api/auth/invito` routes belong in `apps/api/src/pigrocrm_api/routers/auth.py`,
directly beside `request_link`/`enter_with_link` (`auth.py:187-338`), which are the same
shape: unauthenticated, space-scoped by the same `TenantPrefixMiddleware`
(`apps/api/src/pigrocrm_api/tenancy.py:30-50`) every other `/<slug>/api/...` route already
goes through, so neither route needs to know it is under a prefix at all.

`require_verified_identity` (`auth/service.py:44-58`) gates
`POST /api/users/invites` and its resend exactly as it already gates `UserService.create`
and `PatService.create` (`service.py:68`, `pat_service.py:77`): an admin who has never
proven their own address — the system-created first admin of a space that has not yet
opened its welcome mail — cannot mint a durable credential for somebody else either. An
invitation is at least as durable as a personal access token in this sense, and the
existing helper's own reasoning applies unchanged; nothing new is written, only a second
caller of it (action name `"invite_user"`).

Rate limits: the two `/api/auth/invito` routes are the only new anonymous surface,
`spend_one` (`apps/api/src/pigrocrm_api/ratelimit.py:97-119`) with its own scopes — the
peek at a generous per-minute ceiling like `disponibile`'s (a page reload or the browser's
own retry must not lock someone out of reading their own invitation), the accept at the
same five-per-minute default `/api/auth/link` already uses. The four admin routes carry
no bucket, like every other authenticated write in this API: pacing an admin's own clicks
is not this limiter's job.

## 4. SPA

`apps/web/src/routes/app/invito.tsx`, a new route beside `entra.tsx`: reads `t` from the
search params the same way `entra.tsx:80-90` does, calls the peek on mount, and renders
one of: a name field plus «Entra nello spazio» button (peek's `nome` was `null`); a
confirmation with the space's name and the inviter's, one button (peek's `nome` existed);
or one of the three dead-link states, each with its own sentence and the same «Torna al
login» link `entra.tsx:70-72` uses. On success, the same `homeAfterEntry()`
(`entra.tsx:12-16`) and full navigation `entra.tsx:48` performs — the invited person's
session is exactly a signed-in session, nothing about it is provisional once accepted.

`UsersPanel.tsx` (`apps/web/src/features/settings/UsersPanel.tsx`): the «Nuovo utente»
dialog (lines 202-283) becomes «Invita», keeping `email` and `ruolo` and dropping
`password` and its ten-character copy (lines 206-209, 245-257) entirely — `nome` stays,
optional, since the invitation may carry none. A new «Inviti in attesa» list beside the
members table, reading `GET /api/users/invites`, each row with a resend and a revoke
action through `RowActions` (the same component the existing table already uses at
`UsersPanel.tsx:154-172`).

## 5. Mail

`invitation_mail(to, spazio, invitato_da, nome, url, *, giorni) -> Mail` in
`packages/core/src/pigrocrm/core/mail.py`, next to `magic_link_mail`/`welcome_mail`
(`mail.py:227-344`), same construction: `_frame`, `_button`, `_quiet_link`
(`mail.py:143-224`), every external value through `html_escape.escape`. Text body per the
draft in § 1; the HTML mirrors `welcome_mail`'s shape (`mail.py:317-342`) with one button
and the same footnote about ignoring an unexpected mail. Sent in `BackgroundTasks` after
the row commits, exactly like `request_link` (`auth.py:280-306`) sends the magic link
after its own commit — never before, and never inside the transaction that could still
roll back.

## 6. Tests

Core: token spent once (race, mirroring `test_magic_link.py`'s own race test); expired,
revoked and already-used answer three distinct sentences; the pending-per-email partial
index refuses a duplicate; the active-user 409; a `collaboratore` refused at
`require_write`/`require_admin`; resend kills the old token and the mail carries the new
one; revoke stops acceptance. API: the six routes' statuses and problem documents; the
peek never mutates `accepted_at`; the accept route sets the session cookies exactly like
`enter_with_link`'s own test does. Web: the invite dialog has no password field; the three
dead-link renders; the token never reaches PostHog (the same assertion
`entra.test.tsx` already makes for `/app/entra`, since `shared/analytics/browser.ts`'s
`scrubTrackingToken` strips any `?t=` unconditionally — `shared/analytics/browser.ts:14-18|96`
— and needs no per-page opt-in).

**Tests that change when `POST /api/users` goes**, all in `apps/api/tests/`, all using the
endpoint only as setup for a second logged-in actor (never asserting anything about
invitations, so they move to the core service or the `readonly_client`/
`collaborator_client` fixtures `conftest.py:131-152` already builds that way):

- `test_analytics_api.py`: `_second_actor` (35-48) and `_seed_deal_and_user` (90-130).
- `test_audit_api.py`: `_create_collaborator` (15-22).
- `test_costs_api.py`: `_second_actor` (9-23).
- `test_drive_api.py`: its own copy of `_second_actor` (80-83).
- `test_invoices_api.py`: `_second_actor` (25-52) — the copy every sibling's docstring
  names as the original.
- `test_invoices_import_api.py`: its own copy of `_second_actor` (24-27).
- `test_time_entries_api.py`: `_second_actor` (12-26) and `_seed_deal_and_user` (33-51).
- `test_input_bounds_sweep.py`: two tests that assert the endpoint's own body validation
  rather than use it as setup — the NUL-byte `nome` rejection (113-116) and the
  over-length `nome` rejection (155-161). These move to `PATCH /api/users/{id}`, which
  carries the identical `SafeStr`/`NOME_MAX_LENGTH` bound on `UserUpdate.nome`
  (`auth/schemas.py:54`) against `admin_user` itself, rather than to
  `POST /api/users/invites`, whose own `nome` is optional and would leave one of the two
  tests with nothing to send.

No test asserts `POST /api/users`'s own contract as a feature — there is no
`test_users_api.py` in this repository — so none of the above is a loss of coverage, only
a change of which fixture builds the second actor.

## 7. PostHog

Three events, in the project's own convention (Italian, snake_case, a past participle —
`.claude/skills/posthog-analytics/SKILL.md` § "An event's shape"), reusing the exact name
the entry point for a magic link already earns rather than inventing a fourth spelling:

| What the card calls it | Event name | Fired by |
|---|---|---|
| invite sent | `invito_inviato` | `apps/web/src/lib/analytics.ts`'s `EVENTS` table (`analytics.ts:32-43`), two new rows: `POST /api/users/invites` and `POST /api/users/invites/{id}/resend` — a resend is "sent" again, the same reuse `documento_creato` already gets for two routes (`analytics.ts:37-38`). |
| invite accepted | `entrato_con_invito` | A third row, `POST /api/auth/invito`, named after the existing `entrato_con_link` for `POST /api/auth/entra` (`analytics.ts:34`) — the same action, a different door. |
| invite revoked | `invito_revocato` | A fourth row, `DELETE /api/users/invites/{id}`. |

All three are captured by the existing browser middleware
(`analyticsMiddleware`, `analytics.ts:83-91`) on a successful response; none needs a
server-side `Tracker` event like the weekly digest's `digest_inviato`
(`packages/core/src/pigrocrm/core/telemetry.py`, referenced in
`docs/superpowers/plans/2026-09-16-reb-221-il-resoconto-settimanale.md:454-469`), because
every one of these three actions is always triggered by a live browser request — the
admin's for send/resend/revoke, the invitee's own for accept — unlike the digest, which a
cron sends with nobody's browser open. No properties beyond the event name: nothing here
needs a `tipo` or a `via`, and the identity is whichever distinct id the two people's
sessions already carry (an anonymous one for the not-yet-signed-in invitee, exactly as
`entrato_con_link` already works pre-login).

## 8. Activity audit

Two entities, both through the existing `ActivityService.record`
(`packages/core/src/pigrocrm/core/activities/service.py:38-68`):

- **`invitation`**, keyed on the row's own `id`: kinds `created` (`{email, ruolo}`),
  `resent` (`{email}`), `revoked` (`{email}`), `accepted` (`{email}`) — the invitation's
  own lifecycle, complete even though no screen reads it yet (REB-296/297 send an admin
  to the accepted user's own timeline instead, not a per-invitation one).
- **`user`**, keyed on the created user's `id`, `ENTITY = "user"` (`auth/service.py:25`):
  `InvitationService.accept` calls `UserService(session).create` with `Actor.system()`
  and `password=None` — the exact call `TenantService.provision` already makes
  (`tenants/service.py:148-153`) — which writes the existing `"created"` entry
  unmodified, then sets `email_verificata_il` on the row directly (the same thing
  `MagicLinkService.enter` does at `magic_link.py:87-89`, since `UserCreate` has no such
  field) and writes one more entry, kind `invited`, payload `{invited_by}`, in the same
  transaction. `UserService.create`'s own audited shape is never touched, so nothing that
  asserts its exact payload (several tests do) needs to change.

## 9. Rollout — the order the chain lands in

1. **REB-290** (API + core): the table, the migration, `InvitationService`, the six
   routes, `PostHog`'s three events' server-visible surface (the routes themselves), the
   removal of `POST /api/users`. Everything else depends on this existing.
2. **REB-291** (web): `/app/invito`, the «Invita» dialog, «Inviti in attesa». Depends on
   REB-290's routes and wire shapes.
3. **REB-292** (last-admin guard): can land independently of 290/291 — it only touches
   `UserService.update`, which exists today — but reads this document for the rule's
   exact wording and scope.
4. **REB-293** (readonly sweep): independent of the invitation work; touches the same
   `actor.py` vocabulary this document cites in § "the three roles hold... today".
5. **REB-294** (SPA `can()` helper): independent; reads `roles.ts`
   (`apps/web/src/lib/roles.ts`), which today holds only display labels.
6. **REB-295** (PAT role freshness): independent of the invitation work; the note under
   "deliberately not done" that a revoked invitation "has nothing to revoke" is this
   document's answer to that card's own open question.
7. **REB-296** (Team screen brief): reads §4 of this document for what the pending-
   invitations state must show, plus REB-290/291's actual wire shapes once they exist.
8. **REB-297** (Team screen build): depends on REB-296's approved brief and REB-290/291's
   routes; also the card that adds the "last access" column neither this document nor any
   prior one records.

## Open decisions for the lead

**A. Where do invitations live?**
- A1 (recommended). Their own table, `invitations`, as designed in §§ 1-2.
- A2. A `users` row created inactive, with no password, deleted on revoke.

Recommendation: A1. A2 makes every table and every service that already assumes a `User`
row is permanent (the timeline, MCP resources, `activities.actor_id`) learn a transient
state that A1 never introduces, for the sole benefit of not adding one table.

**B. Does `POST /api/users` (password) stay, alongside invitations, or go?**
- B1 (recommended). It goes. The eight test files listed in § 6 move to the core service
  or the fixtures `conftest.py` already has for exactly this.
- B2. It stays, for scripted or bulk creation an admin might want to automate without a
  mail round-trip.

Recommendation: B1. Nobody has asked for B2's scripted case, and a second, parallel way to
create a person defeats the reason invitations exist — an address proven by its own
click, never a password somebody else chose.

**C. How far does the one-active-admin rule reach?**
- C1 (recommended). A hard refusal, synchronous, inside `UserService.update` only —
  exactly REB-292's own scope. Recovery from a space that already has zero active admins
  (should one exist from before this rule) stays `pigrocrm createadmin` at the server.
- C2. The same refusal, but with an admin-only override that requires typing a
  confirmation phrase, for an operator who is certain and has no other admin to ask.

Recommendation: C1. `createadmin` already needs SSH access to the server that runs the
container; the confirmation phrase in C2 protects nothing that costs less than the refusal
itself, and a self-hosted operator locked out by their own click is precisely who the hard
floor protects.
