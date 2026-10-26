# Runbook: the daily automation pass (`pigrocrm automations`)

The fourth job in the deploy user's crontab, after `gmail-sync`, `digest` and `reminders`
(their runbooks are `2026-09-09-gmail-cron-runbook.md` and `2026-10-26-reminders-cron-runbook.md`,
and everything they say about the four details of the line, `cd`, `--env-file ../../.env`,
`exec -T api` and `uv run --no-sync`, holds here word for word). There is no daemon and no
queue: the pass is a command that starts, walks the spaces, opens what is due and ends.

What it implements is slice 7 §6 (`specs/2026-09-03-slice-7-attivita-e-promemoria-design.md`),
the rules that fire with time rather than with a change: the cards are REB-727 (the pass) and
REB-728 (the three rules). In one sentence: for the root installation and then every space in
the registry, in the order they were created, it asks each switched-on rule which subjects
match today and opens one attività for each that has none open.

| Rule (`regola`) | It matches | The attività | Due | Assigned to |
|---|---|---|---|---|
| `offerta_inviata_richiama` | an offer in state `inviata` for at least 7 days and at most 37 | «Richiamare {cliente} sull'offerta {titolo}» | 7 days after the sending | the owner of the offer's deal |
| `deal_fermo` | an open deal in the same stage for at least the threshold (default 30 days, Impostazioni → Automazioni) | «Il deal {nome} è fermo da {n} giorni» | today | the deal's owner |
| `fattura_scaduta` | an issued, unpaid invoice strictly past its due date | «Sollecitare {cliente} per {numero}» | today | the owner of the invoice's deal |

Each rule has its switch in Impostazioni → Automazioni (admin only; the MCP surface only
reads them, `describe_automations`). A switched-off rule opens nothing and the log says
`disattivata` for it.

## 1. Installing the line (operator, on the server)

Once a day, in `crontab -e` of the user that owns the deploy. Production's `DEPLOY_PATH` is
`/opt/pigrocrm` and the preview's `/opt/pigrocrm-preview`, each with its own `.env`, so each
gets its own line and its own log:

```
0 6 * * * cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm automations >> /var/log/pigrocrm-automations.log 2>&1
0 6 * * * cd /opt/pigrocrm-preview/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm automations >> /var/log/pigrocrm-preview-automations.log 2>&1
```

**The crontab line does not change when a space is added.** The hour is only «before the
working day starts»: what counts as today is read from `PIGROCRM_TIMEZONE` (`db/clock.py`),
never from cron's clock, so the host's time zone changes when the attività appear and never
which ones. On a host in UTC `0 6` is 07:00 or 08:00 in Rome. Running it twice a day, or by
hand, is harmless: a second pass opens nothing for a subject that already has its attività
(§4).

**The command migrates nothing**, like the digest and for the same reason (ORB-189): a space
whose schema is behind the image answers `saltato (ProgrammingError)` until the API restarts
and its boot command migrates it. The first day after a deploy that ships a migration may
print that line for every space. It is not a fault.

### The dead-man's switch

Like the reminder pass, the line may ping a healthchecks.io check after every run that exits
0 (`docs/design/DECISIONS.md`, 2026-10-19). The ping URL is a secret and is never a literal in
the repository: put it in the crontab as a variable above the line and chain it with `&&`.
Period one day, grace period a few hours:

```
PIGROCRM_AUTOMATIONS_PING_URL=<the check's ping URL, as healthchecks.io shows it>
0 6 * * * cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm automations >> /var/log/pigrocrm-automations.log 2>&1 && curl -fsS -m 10 --retry 3 -o /dev/null "$PIGROCRM_AUTOMATIONS_PING_URL"
```

## 2. Trying it, before waiting for tomorrow

`--dry-run` runs the real pass inside a savepoint and rolls it back: the counters are the
ones a real run would print, nothing is written, and every line carries `(prova)`:

```
cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm automations --dry-run; echo "uscita: $?"
```

`--slug studio-rossi` visits one space (`--slug root` the root installation alone, without
opening the registry), and `--data 2026-11-02` evaluates that day instead of today. `--data`
is accepted only with `--dry-run` (exit 2 otherwise): a real pass of another day would open
attività due on days that are not today.

## 3. Reading the log

One line per rule per space, **counters only**: never a title, a customer's name or an
address, which is what makes it safe to append to a file on the host. Cron adds no time, so
the file's modification time answers «when did it last run».

| Line | What happened | What to do |
| --- | --- | --- |
| `root: fattura_scaduta aperte 2, già aperte 5` | Two attività opened now; five matching invoices already had theirs open | Nothing |
| `root: fattura_scaduta aperte 0, già aperte 7` | Nothing new. This is what a second run on the same day prints | Nothing |
| `root: deal_fermo disattivata` | The rule is switched off in that space | Nothing, unless it should be on |
| `studio-rossi: deal_fermo aperte 1, già aperte 0 (prova)` | `--dry-run`: it would have opened one | Nothing: no row written |
| `studio-rossi: saltato (ProgrammingError)` on `stderr` | That space's schema is behind the image, see §1 | Restart the API, which migrates it, and run the line by hand |
| `studio-rossi: saltato (OperationalError)` on `stderr` | That space's database does not answer. The other spaces were visited | Check that database |
| `studio-rossi: saltato (<Type>)` on `stderr` | A failure nobody foresaw, named by its **type** and never its message (a psycopg error can carry the URL, password included) | Reproduce it from a Python shell in the container, where the message reaches only your terminal |
| `registro degli spazi non raggiungibile (<Type>)` on `stderr` | The registry does not answer: no space was visited, the root was | Check the database and the `PIGROCRM_*` of the `.env` |
| `non-esiste: non nel registro` on `stderr` | `--slug` named nothing | Fix the line |

**Exit status.** `0` when every line went to `stdout`, `1` when any line went to `stderr`,
like the reminder pass and unlike the digest, because the exit status is what the ping above
reads. Every line is printed either way, and one space's failure never stops the next.

**Rotation.** Three lines of about forty characters per space per day: a year of ten spaces
is some 400 KB, and doing nothing about it is fine.

## 4. Why a second run opens nothing, and when the next one does

`uq_attivita_regola_aperta` is a unique index over the open, live attività of a rule and its
subject (migration `0055`): the deal, the invoice or the customer the attività hangs on. The
pass reads which subjects already have one and skips them (`già aperte`), and still inserts
under a savepoint and treats the index's violation as the same answer, so two passes at the
same instant, a cron overlapping a hand-run line, open one (proved on two connections in
`test_automation_daily.py`).

When somebody **completes or cancels** the attività, or archives it, the subject is free and
the next pass opens a new one if the rule still matches: «sollecitare Rossi» closed without
Rossi paying means «I spoke to him», and a month on the reminder is needed again.

Each opened attività leaves a timeline entry on itself (`automazione.attivita_aperta`, actor
`system`) and appears in «Ultime esecuzioni» of Impostazioni → Automazioni.

## 5. How it sits with the reminders

A reminder (`pigrocrm reminders`) is a notice about a cause, recomputed on every pass; an
attività is a to-do with a state. Where they would say the same thing twice:

- `fattura_scaduta` and the reminder `invoice_overdue` have one cause. The reminder keeps
  climbing its own ladder, and the attività reminders (`attivita_due`, `attivita_overdue`)
  skip the attività this rule opened.
- `deal_fermo` and the reminder `deal_stale` measure different things (no mail, attività or
  hours on the deal, against no change of stage). The attività this rule opens is itself an
  attività created on the deal, so it restarts the reminder's clock.
- `offerta_inviata_richiama` has no reminder of its own: its attività, and the reminder for
  the attività falling due, are the only notice.

## 6. What it does not do

It sends no mail, no push and no Slack message, and nothing to a customer: the sollecito
stays an act (slice 7 §9), reached with one click from the attività, never raised by a rule.
It does not cancel an attività when its cause goes away (an invoice paid, an offer accepted):
closing it is the person's decision, and the reminders, which are recomputed, are what
disappear by themselves.
