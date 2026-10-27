# Runbook: reminders by email and push

What `pigrocrm reminders` does besides filling the in-app list (its own runbook is
`2026-10-26-reminders-cron-runbook.md`, and the crontab line is the same one: there is no
second job). The decisions are `specs/2026-10-26-promemoria-design.md` §6 and §8.3; the cards
are REB-737 (email) and REB-738 (web push). Nothing here sends anything to a customer.

## 1. What a pass sends

The in-app list is the source of truth and every other channel is a copy of it, so a channel
is offered only what the person has not acknowledged or snoozed, and only on the channels
they (or the space) have switched on for that type. In order, in each space:

1. the reads: the reminders, the people, the preferences, the ledger, the push subscriptions;
2. the read transaction ends, and only then does anything touch the network;
3. **push**: one notification per reminder, at once, to every browser the person turned push
   on in;
4. **email**: one mail per person with what is **urgent** (at or above the type's urgent step,
   `critical` unless somebody lowered it), at once; everything else waits for the **daily
   summary**, one mail per person per space, in the first pass at or after their summary hour,
   severest first. A day with nothing to say sends nothing;
5. what the providers accepted is written to `reminder_delivery` in the one transaction of the
   pass, with the summary markers (`reminder_summary_run`) and the removal of push
   subscriptions the push service reported gone.

**A failed send writes nothing**, so the next pass, a quarter of an hour later, sends it
again; the failure that remains is a duplicate message after a crash between the send and the
write, never a missing one (spec §8.3). **Quiet hours hold both channels**, urgent mail
included: a reminder that falls in the window (20:00 to 08:00 and the whole weekend, in
`PIGROCRM_TIMEZONE`, unless somebody changed it) stays undelivered and the first pass after
the window sends it. Never the in-app list.

A browser announces what reached the person's list **after it subscribed**, and in the last
day (what a push service would hold it for), never the backlog: turning push on, or on again
for a type, with thirty reminders open sends no notification for those. A browser that
changes hands (somebody else signs in on it and turns push on) starts over for its new
person, and signing out turns push off for that browser. A person keeps at most ten browsers
(the oldest goes), and only the browser vendors' own push services are accepted as an
endpoint (`fcm.googleapis.com`, `push.services.mozilla.com`, `notify.windows.com`,
`push.apple.com`), so a person cannot make the server call an address of their choosing. A
pass gives the push phase two minutes in all; what is not sent by then goes in the next one.

## 2. Settings of the installation

Both go in the host's `.env` (`${DEPLOY_PATH}/.env`, never in the repository), which the
compose file forwards to the `api` container, the one the cron line runs in.

| Setting | What | Where it comes from |
| --- | --- | --- |
| `PIGROCRM_RESEND_API_KEY`, `PIGROCRM_MAIL_FROM` | The mail transport. The same key the login link, the invitations and the weekly digest already use: nothing new to install | Already set on a host that mails |
| `PIGROCRM_VAPID_PRIVATE_KEY` | The push identity of this environment: the private half of a P-256 key pair, base64url. **A secret.** The public half is derived from it and handed to the browser, so a pair that does not match cannot be set | `pigrocrm vapid-keys` (below) |
| `PIGROCRM_VAPID_SUBJECT` | Who a push service may write to about this server, a `mailto:` or an `https:` address. Default `mailto:ciao@letsrebase.com` | Usually left alone |

**The summary hour and the quiet hours are not settings of the host.** They are reminder
preferences, at two levels (spec §7): the space's defaults in Impostazioni, Promemoria
(admins), and each person's own in Impostazioni, Notifiche. The built-in defaults are summary
hour 8 and quiet hours 20:00 to 08:00 and the weekend. Nothing about them is in `.env`, and
changing them needs no deploy.

### Generating and installing the VAPID key (operator, once per environment)

One key pair per environment (production and the preview each get their own, so a
subscription made on the preview never answers to production's key). The command writes
nothing: it prints the line for the `.env`, and a comment with the public half for reference.

```
cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm vapid-keys
```

Put `PIGROCRM_VAPID_PRIVATE_KEY=<the value>` in that environment's `.env`, then recreate the
container so it reads it (a restart does not re-read a changed `.env`):

```
cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env up -d api
```

**Check it took:** open Impostazioni, Notifiche on a deployed space. «Notifiche su questo
browser» offers **Attiva** when the key is valid, and says «Le notifiche push non sono attive
su questa installazione» when it is empty or malformed (a malformed key is logged as «push non
disponibile: la chiave VAPID privata non è valida» and treated as not configured, never a 500).

**Rotating the key** makes every browser's subscription stale: a push service refuses a send
signed with a key the subscription was not made with, so the page reads such a subscription
as off, and **Attiva** drops it and subscribes again. People turn push on again; nothing else is lost.
Never rotate it to «tidy up».

## 3. The first pass after turning a channel on

- **Email.** A host that already mails has the key, so the first pass after the deploy mails
  every **urgent** reminder open at that moment, one mail per person, and the summary goes out
  at each person's summary hour. That is the design (urgent at once), and on a space with a
  long list of old overdue invoices it is a first mail with all of them. Try it first:

  ```
  cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm reminders --dry-run
  ```

  The line ends with `, email 3 (prova)` when three mails would go, and writes nothing.
  Read it for **today**: `--data` evaluates another day at its last minute, which is inside
  the default quiet hours, so the email and push counters of a rehearsal of another day say
  0 whatever would go. A rehearsal counts push as one per browser, like the real line.
- **Push.** Nobody has a subscription until they press **Attiva**, so the first pass sends
  none.

## 4. Reading the log

`push N` and `email N` at the end of a space's line count the messages the providers
accepted, and appear only when not zero; on `--dry-run` they are what would go. A mail that
carries five reminders is one.

| Symptom | Meaning | What to do |
| --- | --- | --- |
| No `email` and no `push` ever, with reminders open | The channel has no transport (no Resend key, no valid VAPID key), or everyone is inside quiet hours, or nobody chose it | Check `.env`; check Impostazioni, Notifiche; `--dry-run` outside quiet hours |
| `mail non inviata: il provider ha risposto 403` (or another status) in the API log | Resend refused (an unverified sender, a revoked key). **Nothing was recorded**, so every pass tries again | Fix the key or the sender; the next pass sends what waited |
| `mail non inviata: la chiamata al provider non ha risposto` | Resend did not answer in ten seconds | Nothing: the next pass tries again |
| `push non inviato: il servizio ha risposto 4xx/5xx` | A push service refused one send. Status only, never the endpoint (it is a capability) | Nothing for one; for every send, check the key and the clock of the host |
| `push non inviato: l'indirizzo del servizio non è pubblico` | A stored endpoint resolves to a private address and was refused, never called | Nothing: it was not a push service |

A subscription a push service reports gone (404 or 410) is deleted in the same pass, and the
person sees push as off in Notifiche until they turn it on again. A send that fails any other
way keeps the subscription.

## 5. Proof on a deployed environment

The proof of REB-737 and REB-738 on the preview needs the deploy, the VAPID key installed on
the preview's `.env` and, for push, a phone with the installed app (a browser's own push
service is the one thing a CI run cannot stand in for):

1. an invoice that turns urgent reaches the inbox at the next pass, and not twice;
2. an attività due that day arrives in the next summary only;
3. on a phone with the app installed, a critical reminder arrives as a notification and a tap
   opens its record.
