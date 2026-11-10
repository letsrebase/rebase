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

REB-995 added the merge: `POST /api/organizations/{survivor_id}/merge` with `{"loser_id": ...}`
or the `merge_organizations` tool (an administrator of the whole space; for a token,
`full_access`). It moves every deal, contract, invoice, person, contact point, document, task,
note, timeline entry, Gmail link, draft, due item and engagement of the loser to the survivor,
joins the roles, fills what the survivor left empty, and archives the loser with a «merged»
entry on the survivor's timeline and a «merged into» entry on the loser's.

**Preview first.** `dry_run` is true unless sent as false: the answer lists what would move per
kind (`moved`), what the survivor already has an equal of and is not carried (`dropped`: the
same contact point, the same Gmail message link, an open automation task of the same rule), the
joined `roles`, the fields filled from the loser, and `shares_survivor`. Nothing is written.
Then send the same call with `dry_run: false`.

Read each group by the `legal_entity_id` of its rows, which the report prints, and decide what
the pair is:

- **One company, two rows**: merge them. Keep as the survivor the row that carries the history
  (usually the older one). When the rows sit under different legal entities, the survivor
  becomes **shared** (`legal_entity_id` null) so both entities still see it; the preview says
  `shares_survivor: true`. A freelancer's hub id passes to the survivor, and two rows that each
  carry a different one are refused: they are two people.
- **Two companies that happen to share a number** (a typo): correct the number on the wrong
  row and do not merge.

The merge is not undone by restoring the loser: what moved stays on the survivor, and restore
refuses while the survivor holds the same VAT number. The next boot builds the index once the
last duplicate is gone.

A shared organization has no legal entity of its own, so what is created under it (a deal, a
contract, a document) takes the legal entity its own request names, the default one otherwise.
