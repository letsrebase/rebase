# Campaigns in the hub admin: build a list, send it, read what each mail led to

Date: 2026-09-25. Status: approach, lists, measured actions, unsubscribe, scheduling and
the admin screens approved in conversation by Ivan on 2026-09-25; the rest of this
record is written for his review. Tracker: REB-459 in `Campaigns in the hub admin: build
a list, send it, read the outcome` (P-REB-41). Follows the two outreach waves of
September (REB-425) and the login attribution of REB-426 and REB-455.

## 0. Why

Ivan, 2026-09-25: «nella parte di admin mi piacerebbe avere una sezione dedicata a
creare queste liste di email e inviarle e anche a vedere l'outcome».

Two waves went out in September. The first (21/09, 83 mails, 69 people) and the second
(25/09, 60 mails, one per person) were each built by hand from `list_talenti`,
`list_pigro_spaces` and a read-only count inside every CRM database. A script sent them
from inside `rebase-api-1`, with a systemd timer on the host for the scheduled one. Their
outcome was then pieced together from Resend's API, the hub's logins and card threads,
PostHog and `psql` in each CRM space. It worked: the second wave completed three
profiles in its first hour, where the first had completed two in three days. It also
needed a person at a terminal for every step, and the hub deploy wiped the scripts'
folder once, because it lived under `/opt/hub`.

This record moves that loop into the admin: a list, a mail, a test, a send (now or at a
set time) and a page that says, per person, what the mail led to.

## 1. The decisions, one paragraph each

**Everything lives in the hub (approach A).** Campaigns, their recipients and their
outcome are hub tables. Sending goes through the hub's own Resend seam
(`rebase_core.mail`), in the hub's mail frame (`_frame`, `_button`), from the address
the hub already uses. Resend's own Broadcasts and Audiences were weighed and left out
(approach B): per-person links such as a member's own Pigro space are awkward there, the
contacts would live in a second place, and the outcome that matters, what the person did
afterwards, has to be read from the hub anyway. A read-only outcome page with sending
kept in scripts (approach C) was also refused, since the point is that Ivan sends
without a terminal.

**A list comes from a ready journey state or from filters, and the text is edited per
campaign.** Ivan chose both (25/09): the states used by the two waves are ready-made
lists, and the Talenti and company filters build any other one. Every campaign carries
its own subject, text and button. A state proposes the text the second wave used, and
the admin changes it.

**One campaign is one list and one text.** A wave like the one of 25/09, seven groups
with seven texts, is seven campaigns scheduled for the same minute. The gap rule of § 5
keeps it to one mail per person.

**Six actions are measured (a to f), and replies are not.** Entered the area; uploaded
the CV; completed the card; created a profile; updated a company request; first
customer in PigroCRM. The sixth needs a usage endpoint in the CRM (§ 6.3). A reply to
the mail stays in the ciao@ inbox, read by a person, until the hub reads that mailbox
(§ 11).

**Every mail can be unsubscribed from, in one click.** It carries a footer link and the
`List-Unsubscribe` headers. An opt-out excludes that address from every later campaign,
and an admin can add an address to the same list by hand.

**A send can be now or at a set time,** in Italian time. Both go through the same loop
(§ 5.4), so «now» means within a minute.

## 2. Journey states and the action each one measures

Each state is a query over the hub's own tables, plus the CRM's usage for the Pigro one.
They are evaluated when the list is shown and again at send time (§ 5.3).

| State (UI label) | Who | Action measured |
|---|---|---|
| `lead` «Lead senza profilo» | a `signups` row whose lower(email) has no non-deleted freelancer card | d, profile created |
| `scheda_vuota_nuovi` «Scheda vuota, mai entrati» | a card that misses more than the CV among the four fields below, and no `logins` row | c, card completed |
| `scheda_vuota_entrati` «Scheda vuota, già entrati» | the same card state, with at least one login | c, card completed |
| `manca_cv` «Manca solo il CV» | a card whose only missing field of the four is the CV | b, CV uploaded |
| `completo` «Profilo completo» | a complete card | a, entered the area |
| `pigro_vuoto` «Spazio Pigro vuoto» | the owner of a PigroCRM space with no customer (one row per owner, their slugs attached) | f, first customer |
| `azienda_aperta` «Azienda con richiesta aperta» | the referente of one or more company requests in `nuovo`, `contattato` or `in_corso` (one row per person, every open request attached) | e, request updated |

A complete card is what `_is_complete` says (`rebase_core/schemas.py`): CV, rate,
position and work mode all there. It is the one definition the admin's «Da
completare», the member area's notice and the welcome mail already read, and a campaign
never derives its own. `manca_cv` is a card where the CV is the only one of the four
missing; the card states take everything else.

«Wizard iniziato», a group of the second wave, is not a state: the hub never learns
that a wizard was started, since only the browser knows (§ 11).

The filters are the ones Talenti already offers (`rebase_core.talenti`: text,
`posizione`, `remoto`, rate range, `origine`, `utm_source`, `has_cv`, `con_accessi`,
creation dates, `stato`) and the company list's (text, `stato`, budget, period, origin,
dates). A filtered campaign picks its action from a to f, and the UI proposes the one
that fits the filter.

A third source, a fixed list, is never typed by hand. It is what «Riscrivi a chi non ha
fatto niente» produces (§ 4.3): the recipients of an earlier campaign who did not do its
action.

## 3. Data model

Migration 0020, one commit of its own.

`campaigns`
- `id`, `created_at`, `updated_at` (the usual mixins); `created_by` → `users.id`.
- `nome`: the admin's label. `slug`: unique, lowercase, the `utm_campaign` of its links
  (`c-2026-09-25-manca-cv`), derived from the date and name and editable while `bozza`.
- `fonte`: `stato`, `filtri` or `lista`. `stato_percorso` (one of § 2, when `fonte =
  stato`), `filtri` (JSONB, when `fonte = filtri`), `segue_id` → `campaigns.id` (the
  campaign a `lista` follows up).
- `oggetto`, `testo` (plain text, paragraphs split on blank lines, `{nome}` the only
  placeholder), `bottone_testo`, `bottone_meta`: `area`, `wizard`, `pigro` (the
  recipient's own space) or `richiesta` (the company request, reached through the area).
- `azione`: `entrato`, `cv`, `scheda_completa`, `profilo_creato`,
  `richiesta_aggiornata` or `pigro_cliente`.
- `stato`: `bozza`, `programmata`, `in_invio`, `inviata`, `annullata`.
  `programmata_per` (timestamptz), `prova_inviata_at`, `inviata_at`.
- `pigro_letto_at`, `pigro_errore_at`: the last time the tick read the CRM's usage for
  this campaign successfully, and the last time it failed (§ 6.3).

`campaign_recipients`, one row per person per campaign, unique `(campaign_id, email)`
- `email` (lowercase), `nome`, `tipo` (`freelancer`, `lead`, `azienda`, `proprietario`),
  and nullable links to `freelancers`, `signups`, `companies`; `pigro_slugs` (text array).
- `codice`: the 8-hex person code the waves used (sha1 of the address), so a person
  reads the same across campaigns and PostHog.
- `prima`: JSONB, the state the action is measured against, taken when the row is
  written: has a card, has a CV, card complete, Pigro customers, and for a referente
  every open request's id with its `updated_at`.
- `stato`: `in_coda`, `inviata`, `saltata`, `fallita`; `motivo` (why skipped or failed);
  `tentativi`.
- `resend_id` (unique, nullable), `inviata_at`, `consegnata_at`, `rimbalzata_at`,
  `primo_clic_at`, `reclamo_at`: first times only, never counts, so a webhook event
  delivered twice changes nothing.
- `entrato_at`, `azione_at`: when the person entered and did the action, written once
  (§ 6.2).
- `disiscrizione_hash`: the sha256 of the one-click token (§ 7), the raw value only in
  the mail, as the magic links do.

`campaign_optouts`
- `email` (lowercase, primary key), `created_at`, `fonte` (`link`, `reclamo`, `admin`),
  `campaign_id` (nullable).

No events table. The webhook (§ 6.1) writes the first delivery, bounce, click and
complaint onto the recipient row, and repeating an event changes nothing. The page
shows «clicked», never a number of clicks. The actions (§ 6.2) are read from the
tables that already record them, and only their first time is kept.

## 4. Admin screens

A menu entry «Campagne», admin only, like the other admin pages
(`apps/web/src/pages/admin/`).

### 4.1 The list

One row per campaign: name, state, when (scheduled or sent), and the numbers sent,
delivered, clicked, entered and action done. Newest first; a draft reads «bozza».

### 4.2 A new campaign, in four steps (the «Crea match» pattern)

1. **Chi.** Pick a journey state, or switch to filters. The page shows the list name by
   name. Each row can be unticked, and ticked rows are the list. Rows the rules
   exclude stay visible, greyed out, with the reason: admin or «non scrivere mai»
   (§ 7), opted out, a hard bounce on an earlier campaign, or another campaign in the
   last `campaign_gap_days` (§ 5.3).
2. **Cosa.** Subject, text and button. A state fills them with the second wave's mail
   for it, which the admin edits; filters start empty. The button's destination is
   chosen, never typed, and the link and its utm are the hub's (§ 5.1). The action to
   measure is preset by a state and chosen for filters.
3. **Prova.** A rendered preview for the first person of the list, and «Mandami una
   prova» to the signed-in admin's own address. «Invia» and «Programma» stay disabled
   until a test has left since the last edit (`prova_inviata_at` later than
   `updated_at`).
4. **Quando.** «Invia adesso», or a day and a time in Europe/Rome. Confirming writes the
   recipient rows (the list is frozen here) and moves the campaign to `programmata`.
   Until it starts sending it can be cancelled or moved back to `bozza`.

### 4.3 The campaign page

- **Header**: sent, skipped, failed, delivered, bounced, clicked, entered, and action
  done, each also as a share of sent.
- **Table**, one row per person: sent or skipped (with the reason), delivered or
  bounced, first click, entered (and «dalla mail» when the login carries this
  campaign's `utm_campaign` and the person's code, REB-426), action done and when.
  Filters: all, «ha fatto l'azione», «non ha fatto niente».
- **«Riscrivi a chi non ha fatto niente»** creates a `bozza` with `fonte = lista`,
  `segue_id` set, the same action and a copy of the text to rewrite. Its list is the
  sent recipients with no action, minus whoever is excluded today.
- **«Non scrivere mai»**: from any row, adds the address to `campaign_optouts` with
  `fonte = admin`, for the team (Lorenzo, 24/09) and for anyone who asked by mail.

## 5. Sending

### 5.1 The mail

`rebase_core.campaigns.render(campaign, recipient) -> Mail`:
- The subject as written. The body is the text's paragraphs, then the button, then the
  signature «Ivan / rebase». `{nome}` becomes the recipient's first name, or the
  greeting drops it («Ciao,»).
- Everything goes in `_frame` with `_button`, and the fine-print fallback link is the
  same as in the waves.
- The button's URL is the destination plus `utm_source=email`, `utm_medium=campagna`,
  `utm_campaign=<slug>`, `utm_content=<azione>`, `utm_term=<codice>`. For `pigro` the
  destination is `https://pigro.letsrebase.com/<slug>/app/get-started`, with the
  owner's first space.
- The footer: «Non vuoi più ricevere queste mail? Disiscriviti» (§ 7).
- The headers: `List-Unsubscribe: <https://letsrebase.com/api/hub/campagne/disiscrizione?t=...>`
  and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`.
- Resend tags `campaign=<slug>`, `azione=<azione>`, `kind=real|test` and
  `r=<recipient id>`, the last so the webhook finds its row without waiting for the
  `resend_id` to be committed (§ 6.1).
- Text is escaped. No HTML is accepted from the admin.

### 5.2 The test

To the signed-in admin's address, with the subject prefixed «[prova]» and tagged
`kind=test`. It is rendered for the admin, never for a recipient: the admin's own first
name and code, and for a `pigro` button the first space the admin owns, or PigroCRM's
front page when there is none, so no test link opens a member's space. The preview on
screen shows the first recipient instead, and links nowhere. A test never writes a
recipient row and never counts in the outcome.

### 5.3 The checks at send time

For each `in_coda` row, just before sending, the row becomes `saltata` with a reason
when:
- the address opted out or was added to «non scrivere mai» since the list was frozen;
- it hard-bounced in any campaign;
- another campaign reached it in the last `campaign_gap_days` (default 3);
- it already did this campaign's action since `prima` was taken («già fatto»), the rule
  the second wave's script applied;
- for `fonte = stato` or `filtri`, it no longer belongs to the state or the filters
  («non più in lista»): a rate cleared after a `manca_cv` list was frozen means the
  mail would be wrong, so it does not leave. A `lista` is not re-checked this way,
  since the person was chosen by name.

### 5.4 The loop

A compose service `campaigns`, the same image as the API and the same shape as `sweep`
(REB-391): `while :; do sleep 60; uv run --no-sync rebase campaigns-tick; done`, with
`init: true`. Each tick:
- takes, under `SELECT ... FOR UPDATE SKIP LOCKED`, the campaigns `programmata` with
  `programmata_per <= now()`, and moves them to `in_invio`. It also takes the ones
  already `in_invio` with rows still `in_coda`, so a send cut short by a deploy or a
  restart resumes on the next tick instead of staying stuck;
- sends their `in_coda` rows one call per mail, at Resend's two a second, each with an
  `Idempotency-Key` equal to the recipient row's id. The key never changes, however the
  rows around it change between two ticks. If a tick dies after Resend accepted a mail
  and before the commit, the next tick sends the same key and Resend answers with the
  mail it already accepted instead of sending it again. Resend keeps keys for 24 hours,
  far longer than a tick's retry;
- records the `resend_id` per row; a failed call leaves its row `in_coda` with
  `tentativi + 1`, and the third failure marks it `fallita`;
- moves the campaign to `inviata` when no row is left `in_coda`;
- stamps the outcome of campaigns sent in the last 30 days (§ 6.2).

One call per mail is also what the two waves did: 60 mails take half a minute, and the
batch endpoint's saving is not worth a key that depends on which rows share a batch.

«Invia adesso» only sets `programmata_per` to now. An environment with no Resend key
refuses «Invia», «Programma» and «Mandami una prova» with a message, as the magic link
does today on the preview.

## 6. The outcome

### 6.1 Delivery, bounces, clicks: Resend's webhook

`POST /api/hub/webhooks/resend`:
- Verifies Resend's Svix signature (`svix-id`, `svix-timestamp`, `svix-signature`,
  HMAC-SHA256 over `id.timestamp.body` with the base64 secret after `whsec_`) against
  `REBASE_RESEND_WEBHOOK_SECRET`. It refuses a timestamp more than five minutes off
  with 401, and a bad signature with 401. An unset secret answers 503, like the
  Documenso webhook.
- `email.delivered`, `email.bounced`, `email.clicked` and `email.complained` find their
  row by the `r` tag the mail was sent with (§ 5.1), and by `resend_id` when the payload
  carries no tags. So an event that reaches the hub before the tick has committed the
  `resend_id` still finds its row.
- Each event writes its first time only (`consegnata_at`, `rimbalzata_at`,
  `primo_clic_at`, `reclamo_at`). A complaint also writes an opt-out
  (`fonte = reclamo`).
- An event tagged with a campaign whose row cannot be found answers 503, so Resend
  retries it on its own schedule. It never answers 200 and loses a bounce.
- An event tagged `kind=test` answers 200 and changes nothing: a test has no row.
- An untagged event with an unknown id (a magic link, a welcome mail) answers 200 and
  changes nothing. So does any other event type.

That Resend's event payload carries the mail's tags gets checked against its reference
when the plan is written. If it does not, an event with an unknown id is kept for a day
in a small `campaign_unmatched_events` table. The tick matches it as soon as it commits
the `resend_id` it belongs to, so nothing that arrives early is lost.

The webhook is created once per environment in Resend, pointing at that environment's
URL (§ 8).

### 6.2 Actions a to e: read from the hub, stamped once

For a sent row, with `t0 = inviata_at`:
- **a, `entrato`**: a `logins` row of the person's user after `t0`. «Dalla mail» when
  it carries `utm_campaign = slug` and `utm_term = codice`.
- **b, `cv`**: the card has a CV now and did not in `prima`.
- **c, `scheda_completa`**: the card is complete now and was not in `prima`.
- **d, `profilo_creato`**: a card exists for the address, created after `t0`. «Dalla
  mail» when its stored `utm_campaign` is the slug.
- **e, `richiesta_aggiornata`**: any of the open requests listed in `prima` has an
  `updated_at` later than its value there and than `t0`. One row per referente
  measures all of that person's requests.

The tick (§ 5.4) evaluates the sent rows of campaigns sent in the last 30 days. It
writes `entrato_at` and `azione_at` the first time it finds them, and never again.
Where the source has its own time, that time is written:
- a, the login's `logged_at`;
- b, the «CV caricato dalla persona» comment the member service writes;
- d, the card's `created_at`;
- e, the earliest qualifying `updated_at`;
- f, the CRM's `first_customer_at`.

Only c, a card becoming complete, has no record of the edit that completed it (the
thread lists every self-edit, not the one that tipped the card). Its `azione_at` is
the tick that first saw the card complete, so it is precise to the minute. The page
reads the stamped columns and runs no action query itself.

### 6.3 Action f: the CRM's usage endpoint

The CRM gains `GET /api/tenants/usage` behind the registry token the hub already uses
for `/api/tenants/` (hub `routers/pigro.py`). It returns, per space: `slug`,
`owner_email`, `customers`, `invoices`, `first_customer_at` and `last_login_at` (the
newest refresh token). It reads each tenant database, the same count the second wave
ran by hand. The hub asks it:
- for the `pigro_vuoto` state;
- for `prima`;
- for the send-time check;
- for the tick's stamping, at most once every ten minutes.

**f, `pigro_cliente`**: `customers > 0` now, with `first_customer_at` after `t0`. When
the CRM does not answer, the tick leaves f unstamped, writes `pigro_errore_at` and tries
again on the next read. While `pigro_errore_at` is later than `pigro_letto_at`, the
page heads the f column with «Pigro non raggiungibile dalle <ora>», and an unstamped row
there reads «da verificare» instead of «no». An outage never looks like a person who
did nothing.

## 7. Unsubscribe and «non scrivere mai»

- Every recipient row gets a random token. The mail carries it; the row keeps its hash.
- `GET /hub/disiscrizione?t=` is a page of the hub web app: one sentence and a button
  «Non scrivermi più». The button posts the token.
- `POST /api/hub/campagne/disiscrizione?t=` writes the opt-out and answers the page.
  It is also the target of the one-click `List-Unsubscribe-Post`, which mail clients
  send without a page.
- A token that matches no row answers the same page as a good one, so a guess learns
  nothing.
- An opt-out applies to campaigns only. Magic links, the welcome mail and contract mails
  are the service a member asked for, and they keep going.
- Admins (`users.role = admin`) are excluded from every list without an opt-out row.
  Anyone else on the team goes in by «Non scrivere mai».

## 8. Settings and deployment

- `REBASE_RESEND_WEBHOOK_SECRET`: the `whsec_` value Resend shows when the webhook is
  created, in each environment's `.env`.
- `REBASE_CAMPAIGN_GAP_DAYS` (default 3). Each new variable needs the three edits
  (`config.py`, `.env.example`, the compose `x-api-environment` anchor), as the PostHog
  skill records.
- The `campaigns` service joins both stacks' compose file. A container that is not
  `running` fails the deploy, hence the loop.
- The Resend webhook is created once per environment, for `email.delivered`,
  `email.bounced`, `email.clicked` and `email.complained`, at
  `https://letsrebase.com/api/hub/webhooks/resend` and the preview's own host. It is a
  manual step with the steps written in the runbook, like Documenso's.
- Click tracking is already on for the domain since 21/09 (`links.letsrebase.com`).
- The preview has no Resend key today, so it renders and lists but does not send. It
  is proven with `RecordingSender`.
- Release is by `hub-v*` tag, which ships everything on main. On 25/09 main carries
  unreleased Documenso signing, so the first campaigns tag waits for, or goes out with,
  that rollout (REB-408).

## 9. Testing

- **Core**:
  - each state's query on fixtures, including the edges: a lead whose card is
    soft-deleted, an admin, a card with a rate but no work mode;
  - `render`: escaping, `{nome}` with and without a name, each destination's URL and
    utm, the footer and headers;
  - the send-time checks, each reason;
  - the tick: a failed call and its retry, the third failure, a tick that dies after
    Resend accepted a mail (the same key, no second mail), a stuck `in_invio`, a
    campaign moving to `inviata`;
  - each action a to f against `prima`.
- **API**:
  - admin-only routes refuse a member and an anonymous caller;
  - the webhook with a good signature, a bad one, a stale timestamp, an unknown
    `email_id`, a repeated event, a complaint, a `kind=test` event and an early event;
  - unsubscribe with a good token, an unknown one and the one-click POST;
  - the CRM usage client with the CRM down.
- **CRM API**: `/api/tenants/usage` refuses without the registry token and counts a
  space with and without customers.
- **Web (vitest)**:
  - the four steps;
  - «Invia» disabled until a test left after the last edit;
  - the excluded rows and their reasons;
  - the campaign page's filters;
  - «Riscrivi a chi non ha fatto niente».
- **Playwright**: one flow, from creating a campaign to its page after a recorded send,
  the video the PR needs.

## 10. Phases

1. **Send a campaign.**
   - Migration 0020 and the states except `pigro_vuoto`.
   - Filters, the four steps, the test, «Invia adesso» and «Programma».
   - The `campaigns` loop, the send-time checks, unsubscribe and «non scrivere mai».
   - The Resend webhook for delivery, bounces and complaints, here and not later: the
     hard-bounce exclusion of § 5.3 needs it from the first campaign on.
   - The list page, and the campaign page with sent, skipped, failed, delivered and
     bounced.
2. **Read the outcome.**
   - Clicks, and actions a to e with the tick's stamping.
   - The full campaign page and «Riscrivi a chi non ha fatto niente».
   - Two read-only hub MCP tools, `list_campagne` and `get_campagna`, so an agent
     reports without a terminal.
3. **PigroCRM.**
   - `GET /api/tenants/usage` in the CRM, its own card with `area:api`.
   - The `pigro_vuoto` state, the `pigro` destination and action f.

Each phase is a milestone of P-REB-41 and ends in a usable admin: after phase 1 Ivan
sends, after phase 2 he reads the outcome, after phase 3 Pigro joins in.

## 11. Not here

- **Replies.** Reading them means the hub reading the ciao@ mailbox (Gmail API or an
  inbound route), a project of its own.
- **Opens.** Open tracking stays off: Apple Mail's privacy proxy makes it noise, and
  the actions are the signal.
- **«Wizard iniziato».** Only the browser knows a wizard was started. A server-side
  record of a started draft would be a change to the wizard, not to campaigns.
- **A/B subjects, sequences that send by themselves after N days, rich HTML
  templates.** The follow-up is a click on «Riscrivi a chi non ha fatto niente», on
  purpose.
- **The two September waves.** Their logs stay on the host (`/opt/outreach/r1`, `r2`)
  and are not imported. The gap rule does not know about them, which only matters until
  3 days after 25/09.
- **Mail to a company's other contacts.** A campaign reaches the referente's user and
  nobody else.
