# Runbook: giving existing customers and deals an owner (`pigrocrm owner-backfill`)

A one-off, REB-797. Customers (REB-773) and deals (REB-780) created before owners existed
carry none. This command gives every live one the user in its earliest `created` entry of the
activity log, in one space, and lists what it will not guess. The rule and its refusals are in
`packages/core/src/pigrocrm/core/owner_backfill.py`.

| A live record is... | The command |
|---|---|
| without an owner, created by an active user (a `user`, or an `mcp` actor carrying the token owner's id) | sets that user as owner |
| created by `system`, `rebase`, a job, or with no actor id | lists it, `no_user` |
| created by an account named with `--generic-account` | lists it, `generic_account`, with the account's name |
| created by a deactivated user | lists it, `inactive_creator` |
| without a `created` entry | lists it, `no_creation_entry` |
| already owned, or archived | not touched (the already-owned ones are counted) |

Each record it sets gets one `updated` entry on its timeline, under the system actor, with
`{"changed": ["owner_id"], "source": "REB-797"}`, so the timeline says the owner came from the
backfill and not from a person. **Idempotent**: only a record whose owner is still empty is
written, so a second run sets nothing and an owner somebody chose in between is never
overwritten.

## Running it (operator, on the server)

Same four details as the cron lines (`cd`, `--env-file ../../.env`, `exec -T api`,
`uv run --no-sync`, see `2026-09-09-gmail-cron-runbook.md`). **Without `--apply` it only
prints**: the plan is the same lines both times.

```
cd /opt/pigrocrm/projects/pigrocrm
docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm owner-backfill --slug <space> [--generic-account <email> ...]
```

Read the plan, then run the same line again with `--apply`. `--slug root` is the root
installation. A space not in the registry is one line on stderr and exit 1.

Run it after the release that carries the deal owner (REB-780), so the deal pages show the
owners the moment they exist.
