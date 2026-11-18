# Signing in to the hub with GitHub or Google

Status: written and built on 2026-11-18. Lorenzo said on 2026-11-18 to go ahead without waiting
for a review of the spec, so this is the record of what shipped; the privacy paragraph of § 9
is a proposal and nothing public changes until he says yes to it. Card: REB-1137. Project:
«Let freelancers show how they work with a reviewed work profile» (P-REB-91), milestone «Sign in
to the hub with GitHub or Google». Pointed to by § 11 of
`2026-11-17-work-profile-intake-and-review-design.md`.

## Why

Lorenzo, 2026-11-17: the hub should offer «Accedi con GitHub / Google», with the magic link left
as the alternative. The people the hub serves are freelancers who already live on GitHub or in a
Google account, and the CLI sign-in of the work profile (`/hub/cli`) sends a person to the hub
from a terminal, where a mail round trip is the slowest part of an otherwise short flow.

It changes how anybody signs in, admins included, so the rule that decides who a provider's
answer is has to be written down before the code: the hub has one identity per person
(`2026-09-17-one-hub-one-identity-design.md`), and a provider is a new way to prove an address,
not a new way to create a person.

## What Lorenzo decided (2026-11-17 and 2026-11-18)

- **Link only on a verified login address.** A provider's answer opens a person only when it
  vouches for an address that is a login address (`users.email`) of that person. An address the
  provider has not verified never counts, and an extra contact point (REB-943) never decides who
  somebody is.
- **No account is created here.** An address the hub does not know gets a page that says so and
  offers the magic link and the sign-up chooser. A deleted person is as unknown as no row.
- **Admins sign in this way too**, under the same rule.
- **The magic link stays**, on the same page, under the two buttons.
- **Two providers**, GitHub and Google, each an OAuth app registered under letsrebase's own
  accounts, with the callback registered on `letsrebase.com` and `preview.letsrebase.com`.
- **Build now, release with the next hub tag.** Nothing reaches production before Lorenzo's ok on
  the tag.

## Goals

1. A known freelancer or admin clicks «Accedi con GitHub» or «Accedi con Google», and lands in
   their area with the session the magic link would have given them, and the login recorded the
   same way.
2. A person whose verified address matches nobody is told so, with the address named, and offered
   the two ways forward. Nothing is created and nothing about them is stored.
3. `/hub/cli` works for a person who reaches it through a provider, with the device code
   surviving the round trip.
4. A provider that is not configured on a deployment is not shown, and its routes say so.
5. The admin's «Accessi» screen says how each login happened.

## Non-goals

- **No sign-up with a provider.** A person with no card applies through the wizard, as today.
  GitHub's own sign-up is switched off in the authorize URL (`allow_signup=false`).
- **No linking of a provider account to a hub person.** The hub stores no provider account id,
  no token and no profile. Each sign-in is decided afresh by the verified address, so a changed
  GitHub address or a closed Google account needs no clean-up here.
- **No provider data in the card.** The sign-in reads addresses and nothing else; it feeds no
  card source (the GitHub source of the card is the public profile the freelancer typed,
  REB-837).
- **No second factor of our own.** A provider that offers one protects its own account; the
  hub's trust is the same mailbox trust the magic link already has (§ 7).
- **The magic link does not change**, including what it does for a person an admin deleted
  (§ 2 says why this is left alone).

## Anti-goals

It fails, even if it works, when: a person gets into somebody else's area because an address
was not really theirs; a stranger is given an account by signing in; an address a provider
called unverified opens anybody; the return path can be turned into an open redirect; a
provider's code or token is logged or stored; the page for an unknown address reads as a dead
end.

## 1. Approaches considered

How a provider's answer becomes a hub person.

| | How | Trade-off |
|---|---|---|
| A. The provider is the account | A provider account id is a person; the first sign-in creates the row | Creates people nobody vetted, and splits one freelancer into two the day they sign in with a second address; breaks one hub one identity |
| **B. The provider proves an address** | The provider says which addresses it verified; one that is a login address opens that person; nothing is created | One rule for three doors (link, GitHub, Google); a person whose provider address differs from their login address is sent to the magic link |
| C. Link once, remember the provider id | The first sign-in needs a magic link, then the provider account id is stored on the user | A second way in that never re-checks the address; a stored identifier per person; a table and a migration for no gain |

B, as Lorenzo decided. It adds no credential the hub holds: the session cookie is the one the
magic link sets, and the provider's token lives for the length of one request.

## 2. The rule: which person a sign-in opens

`UserService.enter_with_provider(addresses, method, utm)` takes the addresses the provider has
verified and decides, in this order:

1. For each address, `by_email` (lower-cased, the `users.email` column and nothing else),
   followed through a merge (`survivor`), exactly as the magic link does: an address merged into
   somebody opens the person it was merged into (REB-800).
2. A person that `is_removed` is dropped. The hub has no hard delete, so a deleted person is a
   `users` row that is not an admin, has no live freelancer card and no live company request,
   and has at least one deleted card or request.
3. No person left: `no_account`, § 5. More than one distinct person left: the sign-in is refused
   as a failure and logged without an address; the hub does not pick (two cards of one human
   that nobody merged yet is the case, and the admin's duplicate review is where it is settled).
4. Exactly one person: the session is opened (§ 4).

**What the provider must say.**

- **GitHub** (OAuth app, scope `user:email`): the access token reads `GET /user/emails`. Only
  entries with `verified: true` count, and any of them may match: a GitHub account holds several,
  and the freelancer's hub address is often not the primary. `…@users.noreply.github.com` is
  dropped, since no hub user holds one and it is never an address to show a person. The primary
  verified address, then the others, are the order the page names them in.
- **Google** (OpenID Connect, scopes `openid email`): the access token reads the
  userinfo endpoint and `email_verified` must be true (Google has answered it as a boolean and,
  on some paths, as the string `"true"`; both mean verified).

**Userinfo, not the ID token, and why.** The ID token comes from Google's token endpoint over
TLS, which OpenID Connect Core § 3.1.3.7 lets a client trust without checking the signature, but
`iss`, `aud` and `exp` are then ours to check by hand on a JWT we would have to parse. The
userinfo endpoint, called with the access token this client has just obtained, answers `email`
and `email_verified` from the same host with no parsing and no key. It costs one more request
in a flow that already makes two and has nothing to cache. The scopes are `openid email` and
nothing more (Lorenzo, 2026-11-18, dropping `profile`, which nothing reads): the name, the
picture and the Google account id are never requested, and the privacy paragraph of § 9 can say so.

**What never decides.** An extra address (`user_contact_points`) is unverified and never a way
in, whichever door asks (spec 2026-11-04 § 4, `promote` and `set_role` already refuse to create a
login under one). A provider's display name and its account id are not read at all.

**Amending decision (a) of 2026-09-17.** That decision kept one door into the hub, the magic
link, on the argument that a second credential is a second thing to rotate, to keep accessible
and to branch on. A provider is a second door but not a second credential: the hub still holds
no password, and the session, the `get_admin` check and the cookie are the same code. The row
`docs/design/DECISIONS.md` for 2026-11-18 records the amendment.

**A deleted person and the magic link.** `request_link` has never refused a person an admin
deleted: it mails them, and the area opens on an empty card (`has_card` false). The provider path
refuses them, as Lorenzo decided. Making the magic link refuse as well is a change to the
meaning of every login link already sent, so it is not made here; it is a question for him
(§ 10).

## 3. The flow

All of it under `/api/hub/auth`, none of it authenticated by design. The two navigations answer
only with redirects (the reader is a browser mid sign-in, and a JSON error would be a dead page);
a provider name that is neither `github` nor `google` is a 422 like any bad path parameter.

1. **`GET /auth/providers`** answers `{ "github": bool, "google": bool }`: a provider is on
   when its OAuth client id and secret are both set. The login page reads it to decide which
   buttons to draw. A failed read draws none.
2. **`GET /auth/oauth/{provider}/start`** (query: the login page's campaign keys and `next`).
   Off provider: redirect to `/hub/login?oauth=off`. Otherwise it mints `state` (32 random
   bytes) and a PKCE verifier (48 random bytes), seals them with the return path and the
   campaign into the `rebase_oauth` cookie and redirects to the provider's authorize URL with
   the S256 challenge, the client id, the scope and `redirect_uri`.
3. **The provider** asks the person, and redirects the browser to
   `/api/hub/auth/oauth/{provider}/callback?code=…&state=…`.
4. **`GET /auth/oauth/{provider}/callback`**, in this order, and the attempt cookie is dropped
   on every answer from the third step on:
   1. provider off: `/hub/login?oauth=off`; budget spent: `/hub/login?oauth=limited` (the cookie
      is kept, so a retry inside the window still works);
   2. no cookie, a cookie for the other provider, no `state` or one that differs (compared in
      constant time): `oauth=expired`;
   3. the provider's `error` (`access_denied` is `denied`, anything else `failed`);
   4. the code is exchanged at the token endpoint with the secret and the verifier; the access
      token reads the verified addresses (§ 2); a refusal, a bad answer or a network failure is
      `failed`, no verified address at all is `unverified`;
   5. `enter_with_provider`: `entered` redirects to the return path or the person's home
      (`/admin` for an admin, `/me` otherwise) with the session cookie; `no_account` redirects
      to `/hub/login/no-account` and leaves the `rebase_oauth_miss` cookie; two people is
      `failed`.
5. **`GET /auth/oauth/no-account`** reads that second cookie and answers `{ provider, email }`,
   or 404 when the browser holds none. `Cache-Control: no-store`.

**The attempt cookie.** `rebase_oauth`, `HttpOnly`, `SameSite=Lax` (the callback is a top-level
navigation from the provider, which Lax lets through), `Secure` outside local development,
`Path=/api/hub/auth/oauth` and `Max-Age=600`. It holds the provider, `state`, the verifier, the
return path and the campaign, as base64url JSON. It is not signed, on purpose: only this browser
ever holds it, `state` must equal the one the provider hands back, and a forged value fails only
its forger. Its single use is the callback dropping it; a replay that puts the cookie back
still stops at the provider, which spends a code once (the API test does exactly that).
`Referrer-Policy: no-referrer` and `Cache-Control: no-store` go on every redirect, since the
callback URL carries the provider's one-time code.

**`redirect_uri`.** Sent explicitly at the authorize URL and again at the token exchange, built
from the origin of `REBASE_HUB_URL` (`https://letsrebase.com/hub` in production, the preview's
on the preview), never from the request's `Host`: one app serves both hosts and a header must
not choose where the code is sent. The two URLs registered at each provider are
`https://letsrebase.com/api/hub/auth/oauth/<provider>/callback` and the same on
`https://preview.letsrebase.com`.

**The return path** (`next`) is limited by `safe_next` to a page of the hub's own SPA: letters,
digits, `-`, `_` and `/`, no `//`, no `..`, at most 64 characters; `/cli` alone may carry a query,
and only `code=XXXX-XXXX` in the device code's alphabet. Anything else is dropped and the
person lands at home. It is appended to `hub_url`, so no value can leave the hub's origin, and
the check runs again when the cookie is opened, so a forged cookie gains nothing.

**`/hub/cli`.** A signed-out visitor of `/hub/cli?code=…` is sent to the login, which already
remembers the code (`lib/returnTo.ts`, 30 minutes). The two buttons carry it as
`next=/cli?code=…`, so the provider's redirect comes back to `/hub/cli?code=…` signed in. The
stored copy is left in place until `/hub/cli` itself opens and forgets it: a provider that says no
or a retry still needs it, and so does the magic link, which brings the visitor to the same place.

**Budget.** `start` and `callback` each spend one event from the public per-client limiter
(`spend_one`, five a minute per `X-Real-IP`, an IPv6 address as its /64), the same budget
`/auth/link` spends from, on top of nginx's `rebase_auth` zone, which already covers
`^~ /api/hub/auth/`. A whole sign-in spends two, so two sign-ins fit in a minute; the answer to
an overspend is the login page with a sentence, not a 429 body.

## 4. The session and the login record

A provider sign-in opens exactly what `POST /auth/enter` opens: `UserService._open_session`
is the one place both call, and it adds the `UserSession`, the `Login` row and the referral
proof (`verify_on_login`, REB-658: the person has just proved the address, by a mail or by a
provider that verified it) to one transaction. The cookie is `set_member_cookie`, so the
30-day sliding window and every guard behind `get_me` are unchanged.

`logins` gains `method` (`link`, `github`, `google`, `NOT NULL`, default `link`, a `CHECK`):
**migration 0058**, conditional like its neighbours, which backfills every earlier row as `link`
(they all were). The campaign keys the login page carried ride in the attempt cookie and are
written on the login as the link's token writes them. «Accessi» in the admin gets a «Come»
column. `logins` is read by PostHog's warehouse: the new column is one of three short labels and
no identifier of the person at the provider, so it needs no new decision there. A campaign
that measures `signed_in` (`campaigns.action`) counts a provider login like a link login, and so
does the proof of an address that REB-911 reads off any `logins` row: both are a `logins` row.

## 5. The pages

Brief, in the shape of § 10 of the 2026-11-17 spec.

```
Subject      signing in with a provider, on the existing login page, and the page for an
             address no account uses
Audience     a freelancer or an admin who wants one click; a person who signed up with another
             address; a visitor who is not in yet
The job      login: pick the quickest way in; no-account: understand why it did not work and
             take the next step without starting over
Palette      the hub's tokens (shared/ui/tokens.css) and nothing else
Type         the hub's face from tokens; the address in the no-account page set in the medium
             weight, broken at any character so a long one never scrolls the page sideways
Density      the login page's own: one narrow column; two outline buttons, a ruled «oppure»,
             then the form that was there
Signature    the address named in the no-account page
States       login: neither provider on (the page as it was), one on, both on, each ?oauth=
             reason (denied, expired, unverified, failed, limited, off); no-account: with the
             address, with none (a cookie that expired), long address
Constraints  Italian copy, 390px and 1440px, existing @rebase/ui primitives only, no brand
             mark drawn: the two buttons are text
Non-goals    no provider icons (the Google mark is four raw colours, which the token rule
             refuses), no «ricordami», no account chooser of our own
```

Copy, as built. Login, with a provider on: «Con il tuo account GitHub o Google, oppure con un
link per mail, senza password.», the buttons «Accedi con GitHub» and «Accedi con Google», «Funziona
se l’indirizzo verificato del tuo account è quello che ci hai dato.», then «oppure» and the form.
No-account: «Non troviamo il tuo account», «GitHub ci ha detto che il tuo indirizzo verificato è
ada@example.it, e nessun account rebase lo usa.», «Ti sei iscritto con un altro indirizzo? Scrivilo
nel modulo di accesso: ti mandiamo un link per entrare.» with «Entra con il link via mail», and
«Non sei ancora dentro? Scegli come vuoi iniziare.» with «Raccontaci chi sei» (the chooser).

## 6. Configuration and deploy

Four settings, read from the environment as every other (`rebase_core.config.Settings`):
`REBASE_GITHUB_OAUTH_CLIENT_ID`, `REBASE_GITHUB_OAUTH_CLIENT_SECRET`,
`REBASE_GOOGLE_OAUTH_CLIENT_ID`, `REBASE_GOOGLE_OAUTH_CLIENT_SECRET`. The secrets are
`repr=False`. A provider is on only with both of its values; half a pair is off.

They are GitHub Environment secrets of `hub-production` and `hub-preview`. They join
`optional-env-secrets` of both jobs of `deploy-hub.yml` (a stack without them deploys with the
buttons off) and the secret-sync step of `_deploy-compose.yml` (`S_<NAME>`), and the compose file
forwards them to `api` with the rest of the `REBASE_` block. The OAuth apps are the two
Lorenzo registered under letsrebase's accounts; their callbacks are in § 3.

## 7. Threats, and what answers each

| Threat | Answer |
|---|---|
| Login CSRF: an attacker finishes a flow on their own account and sends the victim the callback | The callback needs the `rebase_oauth` cookie of the browser that started it, and `state` must equal it; the attacker cannot set the victim's cookie |
| Code interception | PKCE S256 and the client secret: a stolen code alone exchanges for nothing |
| Taking over a person by an unverified address | Only `verified` addresses are read (GitHub) and `email_verified` is required (Google); the tests cover an unverified address that equals a login address |
| An address that is not the person's any more (a left company's Google Workspace address, a released GitHub address) | The same exposure as a magic link mailed to that address: the hub trusts the mailbox; the session is no stronger or weaker than the link's. An admin who leaves is demoted as today |
| Open redirect through `next` | `safe_next` at the start and again when the cookie is opened; the path is appended to `hub_url`; tests with five hostile shapes |
| `redirect_uri` steered by a header | Built from `REBASE_HUB_URL`; a test sends a hostile `Host` and `X-Forwarded-Host` |
| A provider's token or code in a log, a cookie or the database | The token is a local of one call and never stored; log lines name the step and the status, never a value; the redirects set `no-store` and `no-referrer`, and a log filter on `uvicorn.access` replaces the callback's query with `?...` (nginx already logs no query) |
| Enumerating which addresses have an account | The no-account page names the address the provider vouched for, which the visitor already holds; it says nothing about any other address |
| Flooding the callback | The public budget of § 3 and nginx's zone |
| Two people behind one provider account | Refused, not chosen between |

## 8. What is not read, stored or changed

The hub reads: the verified addresses. It stores: one `logins` row with a method label. It
keeps for ten minutes in the browser only: the attempt, and, for an unknown address, the
provider and that address. It creates no user, no contact point, no note and no audit entry
(a login is not an audit event today). It changes nothing for the magic link, `/auth/link`,
`/auth/enter`, the CLI device flow or the MCP's tokens.

## 9. Privacy: a paragraph for Lorenzo to approve

`projects/website/src/privacy.html` says nothing about signing in with a provider, and a
public text is Lorenzo's to approve; this change does not touch it. The proposal, a new section
after «Il profilo di lavoro», in the page's own voice:

> **L'accesso con GitHub o Google.** Puoi entrare nella tua area con il tuo account GitHub o
> Google, in alternativa al link che ti mandiamo per mail. Ti mandiamo da GitHub o da Google
> alla loro pagina di accesso e, quando torni, chiediamo loro una cosa sola: gli indirizzi
> email che hanno verificato per te. Se uno è l'indirizzo con cui sei iscritto a rebase, entri;
> se nessuno lo è, non creiamo nessun account e ti diciamo che non lo troviamo. Non leggiamo il
> tuo nome, la tua foto né altro dal tuo account, non salviamo l'identificativo del tuo account
> né il codice che ci dà il fornitore, e di ogni accesso conserviamo solo la data e il modo
> (link, GitHub o Google), come per il link. Per qualche minuto il tuo browser tiene un cookie
> tecnico che serve a completare l'accesso; GitHub e Google trattano i tuoi dati secondo le
> loro informative, come titolari autonomi. Base giuridica: la tua richiesta di accedere.

If Lorenzo agrees, it also earns a line in «Cookie e misurazione» for the technical cookie, and
the two OAuth consent screens link to the notice (Google's verification asks for it).

## 10. Calls made in this design

- Linking is by verified address equal to `users.email`, GitHub's several verified addresses
  all counting, Google's one (§ 2).
- Google is read through userinfo, not the ID token (§ 2).
- A deleted person is unknown to the provider path, and not to the magic link (§ 2).
- No new table: the attempt lives in a ten-minute cookie, which is what Lorenzo asked for, at
  the price of single use being the cookie's drop plus the provider's own single-use code.
- No provider mark on the buttons (§ 5).
- Google is asked for `openid email` only; `profile` was in the first decision and was dropped on 2026-11-18 because nothing reads it (§ 2).
- Every failure of the two navigations is a redirect to the login with a reason, never a JSON error (§ 3); an exception from the HTTP seam (a timeout, a reset) is one of them.

Questions that are Lorenzo's:

1. **The magic link and a deleted person.** Leave it (Lorenzo, 2026-11-18: leave it as it is), or refuse them there too?
2. **The privacy paragraph** of § 9, and the line for «Cookie e misurazione».

## 11. Cards

REB-1137 (this card: the design and the build). Neighbours that are not touched: REB-1130
(usage bands, owns migration 0059), REB-1131 (the invitation campaign). A card for the privacy
text follows Lorenzo's answer to question 2.
