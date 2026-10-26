# Runbook: the reminder pass (`pigrocrm reminders`)

The third job in the deploy user's crontab, after `gmail-sync` and `digest` (their runbook
is `2026-09-09-gmail-cron-runbook.md`, and everything it says about the four details of the
line, `cd`, `--env-file ../../.env`, `exec -T api` and `uv run --no-sync`, holds here word
for word). There is no daemon and no queue: the pass is a command that starts, walks the
spaces, does its work and ends, and the fifteen minutes are cron's.

The decision it implements is `specs/2026-10-26-promemoria-design.md` §9, and the card is
REB-736. What a pass does in one sentence: for the root installation and then every space
in the registry, in the order they were created, it recomputes the reminders from the data,
puts each one in the in-app list of every person it is for (once per severity, and once a
day for an overdue attività), and ends the occurrence of everything whose cause has gone.

## 1. Installing the line (operator, on the server)

One line per environment, in `crontab -e` of the user that owns the deploy. Production's
`DEPLOY_PATH` is `/opt/pigrocrm` and the preview's `/opt/pigrocrm-preview`, each with its
own `.env` and its own compose project, so each gets its own line and its own log:

```
*/15 * * * * cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm reminders >> /var/log/pigrocrm-reminders.log 2>&1
*/15 * * * * cd /opt/pigrocrm-preview/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm reminders >> /var/log/pigrocrm-preview-reminders.log 2>&1
```

**The crontab line does not change when a space is added.** Fifteen minutes is the
reminders spec's interval: it is what delivers an urgent reminder within a quarter of an
hour and what ends quiet hours promptly. Nothing here depends on the host's time zone: the
pass reads the day from `PIGROCRM_TIMEZONE` (`db/clock.py`), never from cron's clock.

**The command migrates nothing**, like the digest and for the same reason (ORB-189): a
space whose schema is behind the image answers `saltato (ProgrammingError)` until the API
restarts and its boot command (`ensure-space-defaults`) migrates it. The reminder tables
(`0054`) reach the spaces at the first boot after the deploy that ships them, so the first
quarter of an hour after a deploy may print that line for every space. It is not a fault.

### The dead-man's switch

A pass that never runs cannot tell anybody, and the reminders' own `job_late` alert
(spec §3.5) watches the pass from inside the space, which is no use when cron is the thing
that stopped. So the host watches it from outside, with a healthchecks.io check
(`docs/design/DECISIONS.md`, 2026-10-19), the way the password manager's backup is watched:
the line pings the check after every run **that exits 0**, and the check mails when a
couple of quarters of an hour pass with no ping.

The ping URL is a secret and is never a literal in the repository. Put it in the crontab
itself, as a variable above the line, and chain the ping with `&&` so that a run that
failed (exit `1`, below) sends none:

```
PIGROCRM_REMINDERS_PING_URL=<the check's ping URL, as healthchecks.io shows it>
*/15 * * * * cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm reminders >> /var/log/pigrocrm-reminders.log 2>&1 && curl -fsS -m 10 --retry 3 -o /dev/null "$PIGROCRM_REMINDERS_PING_URL"
```

The check is created by hand on the account `ciao@letsrebase.com`; its period is fifteen
minutes and its grace period an hour, so a single missed run mails nobody.

## 2. Trying it, before waiting a quarter of an hour

`--dry-run` evaluates every space and says what it **would** put in people's lists. It
writes nothing, not even the job clock, and every line carries `(prova)` so the log cannot
confuse it with a real pass:

```
cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm reminders --dry-run; echo "uscita: $?"
```

`--slug studio-rossi` visits one space (`--slug root` the root installation alone, without
opening the registry), and `--data 2026-11-02` evaluates that day instead of today: the
attività due that morning, the overdue ones that day, the timers that had run by its last
minute. A rehearsal and a re-run are calls to the same code as the cron, never a copy of it.

## 3. Reading the log

One line per space, **counters only**: never a title, a customer's name or an address, which
is what makes it safe to append to a file on the host. Cron adds no time, so the lines carry
none either; the file's modification time and the job clock below answer «when did it last
run».

| Line | What happened | What to do |
| --- | --- | --- |
| `root: promemoria 4, nuovi 1, chiusi 0` | The root installation has 4 reminders raised for its people, 1 of them put in a list by this pass, and no occurrence ended | Nothing |
| `studio-rossi: promemoria 4, nuovi 0, chiusi 2` | Nothing new, and two reminders whose cause has gone had their acknowledge, snooze and delivery rows ended | Nothing |
| `studio-rossi: promemoria 4, nuovi 1, chiusi 0 (prova)` | `--dry-run`: it would have put one in a list | Nothing: no row, no clock |
| `studio-rossi: saltato (in_corso)` | Another pass was running on that space (a slow one, or a hand-run line). The lock did its work, and the line goes to `stdout`, exit `0` | Nothing. If it repeats on every run, a pass is stuck: see §5 |
| `studio-rossi: saltato (ProgrammingError)` on `stderr` | That space's schema is behind the image, see §1 | Restart the API, which migrates it, and run the line by hand |
| `studio-rossi: saltato (OperationalError)` on `stderr` | That space's database does not answer. The other spaces were visited | Check that database |
| `studio-rossi: saltato (<Type>)` on `stderr` | A failure nobody foresaw, named by its **type** and never its message (a psycopg error can carry the URL, password included) | Reproduce it from a Python shell in the container, where the message reaches only your terminal |
| `registro degli spazi non raggiungibile (<Type>)` on `stderr` | The registry does not answer: no space was visited, the root was | Check the database and the `PIGROCRM_*` of the `.env` |
| `non-esiste: non nel registro` on `stderr` | `--slug` named nothing | Fix the line |

**Exit status.** `0` when every line went to `stdout`, `1` when any line went to `stderr`.
Unlike the digest, which exits `0` whatever happens, this one exits `1`, because the exit
status is what the ping above reads: a space that could not be walked has to be able to
stop the ping. Every line is printed either way, and one space's failure never stops the
next.

**Rotation.** About forty characters per space per run: a year of ten spaces is some 14 MB.
A weekly `logrotate` rule with `rotate 8` is more than enough; doing nothing for a year is
fine too.

## 4. Where a pass leaves its marks

All of it in the space's own database, nothing across spaces (spec §9.3):

- `reminder_delivery`: one row per reminder, person and channel, written after the channel
  accepted it. A reminder at `serious` that climbs to `critical` upserts the same row, which
  is why a pass that finds nothing new prints `nuovi 0`. The pass delivers to the in-app
  list only; the other channels (email, push, Slack) plug into the same ledger with their
  own cards.
- `reminder_ack`, `reminder_snooze`: what each person did, ended with the occurrence.
- `reminder_job_run`: one row, `reminders`, rewritten at the end of every real pass (not by
  a rehearsal, and not by a pass that failed, since it is written in the same transaction
  as the ledger). It is what `job_late` will read, and what answers «did it run»:

```
docker compose --env-file ../../.env exec -T db psql -U pigrocrm -d <the space's database> -c "select job, ran_at from reminder_job_run"
```

## 5. When a pass looks stuck

The pass takes a Postgres advisory lock on the space's database (`pg_try_advisory_lock`,
namespace `0x7093`) before it reads any delivery state and releases it when the ledger
writes are committed. A second pass that finds it held prints `saltato (in_corso)` and
moves on, so a run that really is stuck shows as that line on every quarter of an hour, and
nothing else for that space. The lock belongs to a connection and is released when it
closes, so the cure for a hung process is to end it, never to delete anything. The `api`
container has no `pkill`, and restarting it ends every process `exec` started in it, the
stuck pass included, and releases its connections (the API is back in seconds, and its
boot command migrates what is behind):

```
cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env restart api
```

## 6. What it does not do

It sends no mail, no push and no Slack message: those are REB-737, REB-738 and REB-740,
which plug into the same pass after the reads and before the ledger writes. It never sends
anything to a customer: the sollecito stays an act (slice 7 §9). It runs no detector of the
catalogue's other groups until their cards land (REB-767, REB-768, REB-769).
