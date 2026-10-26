# PigroCRM reminders: what fires, through which channel, for whom (REB-729)

Date: 2026-10-26. Status: to be approved. Lorenzo signs the spec off, defaults included,
before any of the cards in § 11 starts.

Prerequisites: slice 7 (attività, `2026-09-03-slice-7-attivita-e-promemoria-design.md`),
the digest (`2026-09-16-la-settimana-e-la-home-design.md`), one identity across several
spaces (`2026-09-23-one-identity-across-several-spaces-design.md`), the ledger and the
fiscal pack already in `main`.

**What this covers.** What the CRM raises on its own when something falls due or goes
wrong, who is told, by which channel and when, how a person tunes it, and how one pass
walks every space. It does not cover the screens' layout (the Avvisi page and the
settings are briefs of their own, REB-734, REB-735, REB-770), Slack's OAuth mechanics
(REB-739) or the push transport (REB-738).

## 1. Why this exists, and what it reverses

Slice 7 § 8 said: «Nessuna notifica push, nessuna email di promemoria». The reason was
that sending mail on someone's behalf is what slice 5 chose never to automate. Lorenzo
reversed that on 2026-10-26 (REB-729, row of that date in `docs/design/DECISIONS.md`).
The reason it holds up is that the reversed line was about the wrong thing: a reminder
is not a mail to a customer, it is a notice to a person who already uses the space. The
sollecito (`payment_reminders`, slice 7 § 9) stays exactly what it was: an act, three
per invoice, never raised or sent by this feature.

The reference is Mastro's alert engine (`src/lib/server/alerts/` in the mastro repo,
read-only): alerts are **recomputed on every pass** from the data, never stored as rows
of their own; what is stored is bookkeeping about a detected alert, keyed by a stable
key. This spec keeps that shape because it is what makes a reminder disappear by itself
when the cause is fixed, with no job to clean up after it.

## 2. What is already decided and what is a proposal

The first table lists what is **Lorenzo's**, decided on 2026-10-26 and quoted from the
cards. Everything else in this spec, the second table and anything the text does not
list in the first, is **Proposal**: mine, and what he confirms or changes when he signs
this spec off.

| Decided by Lorenzo, 2026-10-26 | Where |
|---|---|
| Slice 7 § 8 is reversed: reminders exist, with push, Slack and email | § 1 |
| Recipients: the assignee plus the owner of the linked customer or deal; space-wide reminders go to admins only | § 4 |
| Every type applies only where it makes sense for the space (the ceiling only under the forfettario, period reminders only if the space closes periods) | § 3 |
| Every timing, threshold, channel, urgency and quiet hours is configurable, with defaults that make a working CRM | § 5, § 6 |
| In-app always; push and Slack at once; email at once only for urgent reminders, the rest in one daily summary, severest first, in Italian | § 6 |
| Quiet hours hold urgent mail too | § 6.3 |
| Two levels of preference: admins set the space's defaults, each user overrides their own, with a way back to the default | § 7 |
| Slack is a Slack app per space with OAuth, direct messages per user matched by email, an optional channel | § 6.4 |
| A failed send stays undelivered for the next pass, as Mastro did | § 8.3 |
| The pass runs across one database per space | § 9 |
| The catalogue of § 3, as a draft, with the «New» types to be confirmed or dropped | § 3 |

| Proposal, not yet confirmed | Where |
|---|---|
| Every number in the catalogue: lead times, day thresholds, ratios, the severity ladder of each type | § 3 |
| Quiet hours 20:00 to 08:00 and weekends as the default | § 6.3 |
| The summary hour (08:00), the pass interval (15 minutes), the snooze set | § 6.2, § 9, § 8.2 |
| The shape of the stored state: what is stored, its keys, the severity-rank rule, acknowledge and snooze being per user | § 8 |
| The recipient fallbacks (archived or mail-less users dropped, an empty set goes to the space's owner) and the customer owner's resolution through deals | § 4 |
| The `email_mode` setting, refusing a threshold save that breaks the ladder's order, one summary mail per user per space, the Slack channel receiving a post in addition to the admins' messages | § 5, § 6.4, § 9.3 |
| The urgent step of each type (the severity from which a reminder is urgent) | § 3 |

## 3. The catalogue

### 3.1 Vocabulary

A **type** is one kind of reminder (`invoice_overdue`). A **subject** is the record it is
about (an invoice). The pair is the reminder's **key**: `type:subject-id`. A reminder has
a **severity**, one of `warning`, `serious`, `critical` (Mastro's three, ranked 1, 2, 3),
and a type climbs its ladder as days pass. **Urgent** means severity at or above the
type's *urgent step*; the default urgent step of every type is `critical` unless the
tables say otherwise, and it is configurable per type (§ 5). A type with no `critical`
rung is never urgent unless an admin lowers its step.

«Applies when» is a condition on the **space**, evaluated before the detector runs: a
detector that does not apply is skipped, not run and discarded, and a user sees neither
the reminder nor a switch for it in settings.

### 3.2 Attività and deals

Recipients: the attività' assignee, plus the owner of the linked customer or deal. An
attività with neither linked nor assigned goes to the space's owner.

| Type | Raised when | Applies when | Ladder (all numbers Proposal) |
|---|---|---|---|
| `attivita_due` | The morning of an open attività' `scadenza`, or `lead` days before | Always | `warning` on the day; lead time 0 days |
| `attivita_overdue` | Every morning while an open attività is past its `scadenza`, until closed or snoozed | Always | `serious` from day 1; **`critical` from 14 days** |
| `deal_past_close` (New) | An open deal past its `chiusura_prevista` | Always | `warning` from day 1; `serious` from 14 days; no urgent step |
| `deal_stale` (New) | An open deal with no mail, attività or hours for 21 days | Always | `warning` at 21 days; no urgent step |
| `timer_running_long` (New) | A timer still running after 10 hours | Always | `serious` at 10 hours; **`critical` at 24 hours** |

### 3.3 Invoices and billing

Recipient: the owner of the customer (and the assignee of the linked deal when it has one).

| Type | Raised when | Applies when | Ladder (Proposal) |
|---|---|---|---|
| `invoice_overdue` | An issued invoice past its `scadenza` and not paid | Always | `warning` from day 1; `serious` from 8 days; **`critical` from 30 days** (the draft catalogue's «urgent from 30 days»; Mastro uses 31) |
| `invoice_not_transmitted` | An issued invoice not transmitted after 2 days | The space exports FatturaPA | `warning` at 2 days; `serious` at 3; **`critical` at 7** |
| `invoice_rejected_sdi` | SDI rejected the invoice | The space exports FatturaPA **and** an SDI outcome is recorded | **`critical` at once.** Blocked: PigroCRM records no SDI outcome today, so this needs that field first and is not built by the cards in § 11 |
| `billable_period_closed` | A contract's billing period ended with days not invoiced | The contract bills from worked days | `warning` at the period's end; `serious` at 30 days past it; **`critical` at 60** |
| `fixed_fee_period_unbilled` | A fixed-fee contract's period ended with the fee not invoiced | The contract has a fixed fee | Same ladder as `billable_period_closed` |
| `on_completion_unbilled` | An on-completion fee not invoiced | The contract bills on completion | Same ladder, from the completion date |
| `invoice_days_unlinked` | An issued invoice whose days are not linked to the contract's days | The contract bills from worked days | `serious` at once; no urgent step |
| `proforma_not_invoiced` (New) | A confirmed proforma not invoiced after 7 days | Always | `warning` at 7 days; `serious` at 21; no urgent step |
| `hours_unbilled` (New) | Billable hours not invoiced for more than 30 days | Always | `warning` at 30 days; `serious` at 60; no urgent step |

The four contract types are Mastro's `billable_period_closed`, `fixed_fee_period_unbilled`,
`on_completion_unbilled` and `invoice_days_unlinked`; their 30 and 60 day steps and the
8 days of `invoice_overdue` are Mastro's `thresholds.ts` numbers, kept so a port does not
change what «seriously late» means; its critical step is 30 here against Mastro's 31, a
change the catalogue asks for.

### 3.4 Contracts, days and approvals

Recipient: the owner of the customer.

| Type | Raised when | Applies when | Ladder (Proposal) |
|---|---|---|---|
| `contract_expiring` | A contract ends in 60 days | The contract has an end date | `warning` at 60 days; `serious` at 30; **`critical` at 14** |
| `renewal_window_open` | The notice or renewal window opens | The contract has a renewal or notice rule | `warning` when it opens; `serious` 14 days before it closes; **`critical` 7 days before** |
| `worked_without_approval` | Days worked without the approval the contract requires | The contract requires approvals | `serious` at once; **`critical` after 3 days** (Mastro's figure) |
| `approval_unanswered` | An approval asked and unanswered after 5 days | The contract requires approvals | `warning` at 5 days; `serious` at 10; **`critical` at 20** |
| `approval_coverage_expiring` | Approval coverage about to run out | The contract requires approvals | `warning` 14 days before the coverage ends; `serious` 7 days before; no urgent step |
| `approval_requirement_unmet` | A requirement of the contract's approvals is not met | The contract requires approvals | `serious` at once; no urgent step |
| `work_unit_outside_term` | A day recorded outside the contract's term | The contract has a term | `serious` at once; no urgent step |
| `register_publication_failed` | The contract's register failed to publish, or is stale | Once REB-765 (publishing the register to a Google Sheet) exists and the contract publishes one | `serious` at once, `critical` after 3 days (Proposal; Mastro's failure and stale detectors are the reference) |

### 3.5 Space-wide

Recipients: **admins only**.

| Type | Raised when | Applies when | Ladder (Proposal) |
|---|---|---|---|
| `ceiling_approaching` | The year's revenue nears the forfettario ceiling | The fiscal profile is the forfettario | `warning` at 80% of the ceiling; `serious` at 90% (Mastro's `CEILING_SERIOUS_RATIO`); **`critical` at 100%** |
| `year_end_overrun_projected` | The projection to year end exceeds the ceiling | The fiscal profile is the forfettario | `serious` at 105% of the ceiling; **`critical` at 120%** (Mastro's ratios) |
| `tax_payment_due` (New) | A tax payment date (saldo or acconto) with its estimate | The fiscal pack knows the dates | `warning` 14 days before; `serious` 3 days before; no urgent step |
| `month_not_closed` (New) | A month ended and is not closed after 10 days | The space closes periods | `warning` at 10 days; `serious` at 20; no urgent step |
| `proposal_pending` | Agent proposals waiting more than 3 days | Always | `warning` at 3 days; `serious` at 7; **`critical` at 14** (Mastro's ladder) |
| `connection_failing` | The Gmail or Drive connection is broken or its sync is failing | The space has connected it | `serious` once the sync has been stale for 3 hours; **`critical` for a broken credential** (Mastro's 3 hour figure) |
| `job_late` | A scheduled job is late or failing: sync, digest, automations, the reminder pass itself | Always | `serious` when a job is later than twice its interval; **`critical` when the reminder pass itself has not run for 3 hours** |

### 3.6 Left out

Mastro's backup and scheduler-heartbeat alerts belong to the host's monitoring (the
healthchecks.io rows of `docs/design/DECISIONS.md` on 2026-10-19 are where the
infrastructure tells a person), not to a space's admins. `recorded_day_contradicted`
needs the evidence Mastro read from mail and is asked again with the dispute bundle
(REB-759). A type may be added by a new row in this file; none is added by being easy.

## 4. Recipients

A reminder is raised for a **set of users**, computed by its detector from the subject:

1. The assignee (the attività' `assegnata_a`, the deal's owner) when the subject has one.
2. The owner of the linked customer or deal.
3. For the space-wide types, every user whose role is admin.

**The customer has no owner today.** `Customer` and the contract models carry no owner
column; the only owner in the code is `Deal.owner_id`. Lorenzo's rule («the owner of the
linked customer or deal») therefore needs a resolution, and the proposal is: for a
subject linked to a deal, that deal's owner; for a subject linked only to a customer, the
owners of that customer's **open deals**; and when there are none, the space's owner (the
empty-set rule below). A card that gives the customer an owner of its own would replace
this resolution; it is not in § 11 and is Lorenzo's to ask for when he signs the spec.

The set is deduplicated: one person who is both assignee and owner gets one reminder. A
user who is archived or has no email address is dropped from the set, and the reminder
keeps going to the others. A reminder whose set is empty goes to the space's owner, so a
type never fires into a void.

Acknowledge, snooze and delivery are **per user** (§ 8): the reminder is one object, and
one person acknowledging it does not hide it from the other (this is the reverse of
Mastro, which stored one `acknowledgedBy` per key).

## 5. What is configurable

Per type, at both levels of § 7:

| Setting | Meaning | Default |
|---|---|---|
| `enabled` | Raise this type at all | On, where it applies |
| `channels` | Which of in-app, push, Slack, email carry it | § 6.1 |
| `timing` | Lead time and step days of the ladder (§ 3) | § 3 |
| `thresholds` | Ratios and hours of the ladder (§ 3) | § 3 |
| `urgent_from` | The severity from which the type is urgent | `critical` |
| `email_mode` | `immediate` or `summary` | `immediate` if urgent, else `summary` |

Per space and per user, not per type: quiet hours (§ 6.3), the daily summary's hour
(§ 6.2) and the time zone, which is the installation's single one, `Settings.timezone`, the one `today_local()` reads. There is no per-space zone in the product today and this spec does not add one.

**Severity is resolved per recipient.** A detector run once per space emits the raw
measure (days late, a ratio, hours) and the recipients; the resolver of § 7 turns the
measure into a severity from each recipient's own ladder. That is what lets a user move
their thresholds without a detector run per person, and it is why acknowledge, snooze and
the ledger are keyed by user (§ 8): two users may see the same reminder at different
ranks. `enabled` is the exception to override symmetry: an admin turning a type off for
the space stops it being raised for everyone, and a user can switch a type off for
themselves but cannot switch back on one the space has off.

A threshold edit changes what the **next pass** raises; it never rewrites the past.
Thresholds must stay ordered (warning before serious before critical, for «days since»
ladders; the reverse for «days before»), and the settings refuse a save that breaks the
order, with the reason in Italian.

## 6. Channels

### 6.1 The four, and what each does by default

| Channel | Default | Notes |
|---|---|---|
| In-app | Always on; cannot be switched off | The Avvisi page and the sidebar's unread count (REB-734). It is the source of truth: every other channel is a copy. |
| Web push | At once | Needs the installable app (REB-731) and VAPID keys (REB-738). Mastro's states (off, unsupported, waiting for an iOS install, on) carry over. |
| Slack | At once | A direct message to the user (§ 6.4). |
| Email | At once **only for urgent**; everything else in one daily summary | § 6.2. |

A user who has not connected a channel simply does not receive on it; the reminder is
never held back by a channel that cannot carry it.

### 6.2 Email: urgent at once, the rest in a summary

An urgent reminder is mailed in the first pass after it becomes urgent (and after quiet
hours, § 6.3). Everything else is collected into **one daily summary per user**, sent at
the summary hour (default **08:00** in the installation's zone, Proposal), severest first, in
Italian, one mail per space (§ 9.3). A day with nothing
undelivered sends nothing. The weekly digest (`pigrocrm digest`) is a different mail
about the week and is untouched.

### 6.3 Quiet hours

Quiet hours hold **push, Slack and email, urgent email included**, and never the in-app
list. The default window, a **Proposal**, is **20:00 to 08:00 and the whole weekend**
(Saturday and Sunday), in the installation's zone. A reminder that falls in the window is not
dropped: it stays undelivered and the first pass after the window delivers it. So a
Friday 21:00 urgent invoice is mailed on Monday morning unless its user turned weekends
off. The window is configurable per space and per user, and can be switched off.

### 6.4 Slack

Decided on 2026-10-26 (REB-730): a **Slack app installed per space with OAuth**, not an
incoming webhook (a channel post would reach the wrong people for something personal) and
not nothing.

- The bot token is a per-space secret, sealed like the Gmail and Drive credentials, never
  logged and never returned by the API or MCP.
- Each PigroCRM user is matched to a Slack user **by email**; a mismatch is fixed by hand
  in the space's Slack settings (REB-739). A user with no match receives nothing on Slack,
  and the settings say so.
- A reminder goes to the user as a **direct message**. A space may name **one channel**
  for the space-wide reminders (admins only types), in addition to the admins' messages.
- Buttons to complete or snooze from Slack are a later card, not this spec.
- Slack's distribution rules for an app installed in several workspaces are REB-739's to
  read and write down before it builds; nothing here depends on them.

## 7. Two levels of preference

1. **Space defaults**, set by admins in Impostazioni: every setting of § 5, plus the
   space's quiet hours and summary hour.
2. **User overrides**, set by each user in their profile: the same settings, for them
   only, each with a **way back to the space default** (a control that deletes the
   override, not one that copies the current default into it, so a later change to the
   default still reaches the user who never overrode it).

A user's effective setting for a type is, in order: their override, the space default,
the built-in default of § 3 to § 6. The resolution is one function in core, called by the
detectors, the dispatcher, the API and the MCP reads alike, so no surface computes it a
second way. Where the settings live is the Impostazioni reorganisation's decision
(REB-770, REB-771); this spec only says there are two levels.

An admin turning a type off for the space stops it being **raised**: nobody sees it,
Avvisi included, and no user override brings it back. An admin turning a **channel** off by default only changes the default:
the user who turned it back on for themselves still receives it (REB-735's «Done when»).

## 8. The stored state, acknowledge, snooze, and the delivery ledger

**Proposal**, for REB-732 to confirm against the code. The principle is Mastro's: a
reminder itself is **derived** by the detectors on every pass and never stored; only the
bookkeeping about it is.

### 8.1 What is stored

| Table | Key | Holds |
|---|---|---|
| `reminder_preference` | space or user, type | The § 5 settings as overrides (a null column is «inherit») |
| `reminder_ack` | reminder key, user | The severity rank acknowledged, who and when |
| `reminder_snooze` | reminder key, user | The severity rank, the date it lasts until |
| `reminder_delivery` | reminder key, user, channel, mode (`immediate` or `summary`), period | The severity rank delivered and when; unique on the key, user, channel, mode and period. `period` is null, except for the types that repeat daily (`attivita_overdue`), where it is that day |
| `reminder_summary_run` | user, day | That the user's daily summary for that day was sent, so an urgent mail at 09:00 is not mistaken for it |
| `reminder_job_run` | job name | When each scheduled job last ran, for `job_late`, the reminder pass included |

All of them live in the space's own database (§ 9) and carry its audit columns.

### 8.2 Acknowledge and snooze

**Acknowledge** marks a reminder seen, per user, at the severity it had. A later
detection at a **strictly higher** rank is not covered and reappears (and is delivered
again): this is Mastro's `covers` rule, and it is what stops «seen at warning» from
silencing «critical» forever. **Snooze** is the same with a date: covered until that
calendar day in the installation's zone, compared as dates, never instants. The offered set
(Proposal, Mastro's): tomorrow, next Monday, in a week, in a month; never a free date
picker. A cleared cause removes the reminder from the next pass whether or not anyone
acknowledged it; the stored rows are inert bookkeeping and are swept when their subject
is gone.

### 8.3 The delivery ledger

A delivery row is written **after** the channel accepted the message. A failed send
writes nothing, so the next pass finds the reminder undelivered and tries again, as
Mastro did. The unique key makes a duplicate **row** impossible, not a duplicate message: as with the
digest (`digest/run.py`, «the concurrency window this leaves open»), the order is send,
then record, so two passes awake at the same instant could both send and the loser's
insert would fail. What closes that window is the per-space lock of § 9.2, so that the
failure that can remain is a duplicate message after a crash between the send and the
write, never a missing one. The loser of such an insert rolls back only its own row and
carries on with the next user. A delivery covers its severity rank and below: the check is «a row exists with a rank at
or above this severity, for the same period», so a reminder delivered at `serious` is not
sent again at `serious`, is sent again when it climbs to `critical`, and a threshold edit
that lowers a severity does not resend. `attivita_overdue` is delivered once a day, as
its catalogue line says («every morning»), through the `period` column; every other type
is delivered once per rank. The summary email writes one row per reminder it carried.

## 9. How a pass runs

### 9.1 One command, one database per space

Like `pigrocrm digest` and `pigrocrm gmail-sync` (`packages/core/src/pigrocrm/core/digest/run.py`
and the runbook `docs/superpowers/notes/2026-09-09-gmail-cron-runbook.md`), the pass is a
**command run from the host's crontab**, not a daemon and not a queue: `pigrocrm
reminders`, one line, `docker compose exec -T api uv run --no-sync pigrocrm reminders`.
It walks the root installation first and then every space in the registry, in the order
they were created, each with its own settings and its own database session. The crontab
line does not change when a space is added.

For each space the pass: reads the space's preferences and context (does it export
FatturaPA, is it a forfettario, does it close periods), runs the detectors that apply,
resolves each user's effective settings (§ 7), drops what the ledger and the
acknowledgements and snoozes cover, and hands the rest to the channels (§ 6). Reads come
first, then the sends, then the ledger writes: the same order as the digest, so a read
transaction is not held across a network call.

### 9.2 Isolation and concurrency

- **One space's failure never stops the next.** An exception in a space is one line on
  `stderr` (the space's slug and the exception's name, never its message, which may
  quote an address), and the pass goes on. The exit status is `0` when every space ran,
  `1` when any line went to `stderr`.
- **This command migrates nothing**, like the digest: a space behind the image answers
  `saltato` with the exception's name until the API migrates it.
- **One pass per space at a time.** The pass takes a Postgres advisory lock on the space's
  database before it sends (`pg_try_advisory_lock`); a pass that does not get it prints
  one line `saltato (in_corso)` for that space and moves on. Detection is read-only, so
  this guards the sends, and the ledger's unique key (§ 8.3) is the backstop.
- The interval is **15 minutes** (Proposal), the gmail-sync cadence: it is what delivers
  an urgent reminder within a quarter of an hour and ends quiet hours promptly. The
  daily summary does not need its own cron line: the pass sends it in the first run
  at or after the user's summary hour that has no `reminder_summary_run` row for that day.
- `job_late` (§ 3.5) watches the pass itself: each run writes its time to `reminder_job_run`, and the next
  run raises the reminder if the gap exceeds the threshold. A pass that never runs cannot
  tell anyone; that is the host monitoring's job and is the reason for the healthchecks
  ping in the runbook REB-736 writes.

### 9.3 The summary across spaces

Several spaces may share one person (2026-09-23), but each space has its own database
and its own ledger. The summary is therefore **one mail per user per space**, which needs
no read across databases. Merging a person's spaces into one mail is a later card if
anyone asks for it. Section 6.2's «one mail» means one per space.

### 9.4 Flags the command carries

`--dry-run` (what would go, written nowhere), `--slug` (one space), and `--data` (the
date to evaluate, as the digest's), so a rehearsal and a resend are calls to the same
decision as the cron, never a second copy of it.

## 10. What this deliberately does not do

- It does not send anything to a customer. The sollecito remains the act (slice 7 § 9).
- It does not store a reminder as a row: a cleared cause removes it with no cleanup job.
- It does not add recurrence, assignees or priorities to attività (slice 7 § 8 stands for
  those three lines).
- It does not build Mastro's backup and heartbeat alerts (§ 3.6).
- It does not start any of the cards of § 11 before Lorenzo signs the defaults off.

## 11. The cards

All in project «Remind people of what falls due» (P-REB-61) unless said otherwise.

| Card | What it builds from this spec |
|---|---|
| REB-732 | The stored state of § 8: preferences, acknowledge, snooze, the delivery ledger, API and MCP reads and writes |
| REB-733 | The detector framework (one pure detector per type, testable on a date, skipped where the type does not apply) and § 3.2 |
| REB-767 | § 3.3, invoices, proformas and unbilled work |
| REB-768 | § 3.4, contracts, days and approvals |
| REB-769 | § 3.5, space-wide reminders for admins |
| REB-734 | The Avvisi page and the sidebar's unread count; acknowledge and snooze in the app |
| REB-735 | § 7: the space defaults in Impostazioni and each user's overrides |
| REB-736 | `pigrocrm reminders` over every space, the crontab line, the runbook (§ 9) |
| REB-737 | § 6.2: email, urgent at once and the daily summary |
| REB-738 | Web push (with REB-731, the installable app, as its prerequisite) |
| REB-739 | § 6.4: connecting a Slack workspace to a space |
| REB-740 | Slack delivery by direct message |
| REB-770, REB-771 | Where the settings and the personal Notifiche section live: the brief for regrouping Impostazioni and the regrouping itself |

The catalogue's contract-side types depend on REB-765 (register to a Google Sheet) for
`register_publication_failed`, and `invoice_rejected_sdi` depends on a card that records
the SDI outcome, which does not exist yet.

## 12. Acceptance of this spec

1. Lorenzo confirms or changes every **Proposal** of § 2 and every «New» type of § 3.
2. The row of `docs/design/DECISIONS.md` of 2026-10-26 on reminders and the notes in slice
   7's §§ 7, 8 and 10.7 point here, and nothing of slice 7 was rewritten.
3. No card of § 11 starts before 1.
