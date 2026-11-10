# Runbook: organizations that share a VAT number (`pigrocrm duplicate-organizations`)

REB-970, design `2026-11-09-suppliers-purchases-and-iva-design.md` § 1. One organization per
company, whatever it is to us: a unique index on the country and the VAT number (compared
upper case, letters and digits only) over live rows keeps two rows of one company from existing.
A space that already holds two cannot get the index until a person decides which row is real.

## What the deploy does

Revision 0078 builds the index only when no live rows collide. When some do, it prints one line
on stderr, builds nothing and **does not fail**: the space runs without the index, and
`OrganizationService` refuses a new duplicate by itself (the Italian «esiste gia' un'anagrafica
con la partita IVA ...») in the meantime. `ensure-space-defaults` retries at every boot and says
`unique VAT number index built` once the last duplicate is gone, or `waiting for duplicates to
be resolved` while one remains.

## Finding them (operator)

```
cd /opt/pigrocrm/projects/pigrocrm
docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm duplicate-organizations --slug <space>
```

One line per row, grouped by country and number, oldest first, with its roles. Exit 0 when there
are none, 2 when there are, 1 when the space cannot be read. `--slug root` is the root
installation. An administrator of the whole space reads the same groups with
`GET /api/organizations/duplicates` or the `list_duplicate_organizations` tool.

## Resolving them (a person)

Nothing here merges or deletes: the merge (rewriting every foreign key and the polymorphic rows)
is its own card. Read each group by the `legal_entity_id` of its rows, which the report prints,
and decide what the pair is:

- **One company that two legal entities bill** (the rows sit under different legal entities):
  do not archive either. **Share it**: an administrator sets the row's `legal_entity_id` to
  `null` (`update_organization` with `legal_entity_id: null`, or `share_organization` in an
  invoice import), which makes the organization visible to every legal entity. Then move what
  hangs on the other row by hand until the merge tool exists, and archive that one (it refuses
  while it has active deals). One row per company is the rule the index holds; two rows for
  one company used by two legal entities is the state to leave.
- **A real duplicate under one legal entity**: keep the row that carries the history, move what
  belongs on it by hand, and **archive** the other (`archive_organization`).
- **A typo**: correct the number on the wrong row.

Give the surviving row the roles of both (`update_organization`, `roles`). The next boot
builds the index.

A shared organization has no legal entity of its own, so what is created under it (a deal, a
contract, a document) takes the legal entity its own request names, the default one otherwise.
