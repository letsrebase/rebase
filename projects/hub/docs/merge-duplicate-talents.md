# Merging the same person's two cards

REB-800. One person who signed up twice, with two addresses and one LinkedIn profile, is two
`users` rows and two cards. Since this change a LinkedIn profile is an identity beside the
address (the rule and why: `docs/design/DECISIONS.md`, 2026-10-27), a form can no longer make
the second one, and this page is how the pairs that got in before are merged, on production,
and how a merge is put back. Everything runs inside the API container; nothing needs a
terminal on the database.

```
ssh pigrocrm
docker exec rebase-api-1 uv run --no-sync rebase talent-duplicates          # dry run
```

## What the command is

`rebase talent-duplicates` reads and prints, and writes nothing, until `--apply`.

| Flags | Does |
|---|---|
| none | The dry run: how many pairs share a LinkedIn profile (`unibili`, with what a merge would move, by relation and count), how many only share a name or a telephone number (`da rivedere`), the sign-ups that repeat a profile, and whether the two unique indexes exist. Prints internal ids and counts only: never a name, an address or a profile, so the output can go on a Linear card. |
| `--apply --admin EMAIL` | Merges every unibile pair, the card with the **newest CV** surviving, then creates each LinkedIn unique index whose profiles are no longer shared; one still blocked (two sign-ups, or two users that are not cards, on one profile: nothing a merge settles) is reported with how many and left for an admin. `EMAIL` is the admin the merge is signed with. A pair a merge refuses (the sentence is printed) is skipped, and the exit code is 1. Only a personal profile (`linkedin.com/in/<name>`) identifies anybody: a company page or a bare `linkedin.com` that two people typed is never a pair and never blocks an index. |
| `--apply --admin EMAIL --survivor ID --absorbed ID` | Merges that one pair of freelancer ids, whichever signals it has: an admin who read both cards chose. Also the way to merge a `da rivedere` pair. |
| `--undo ACTION_ID --admin EMAIL` | Puts one merge back (below). |

The same merge is «Unisci» on the admin's «Duplicati» page, `POST /api/hub/freelancers/{id}/merge`
and the MCP tool `merge_freelancers`; `GET /api/hub/talent/duplicati` and `list_duplicati` list the
pairs. A name or a telephone number never merges on its own: only a profile does.

## What a merge does

The survivor is the card with the newest CV (`freelancers.cv_uploaded_at`), unless an admin names
the other with `--survivor`. Into it moves everything of the other person, **by id**: logins,
sessions and magic links, company requests, team requests, proposals and talent-cloud grants,
campaign rows, referrals made and received, matches, contract documents, tax data, the notes
thread, and the audit entries. Where the survivor already has the thing (a referral for the same card,
a talent already on the same team request, a live grant for the same company, a tax record) the other's
stays where it was and is counted as `lasciati`. The absorbed card's notes move into the survivor's thread by id, whole, beside one `sistema` note that says the cards were merged; a refusal to be mailed given under the absorbed address is given to the survivor's address too; a referral code of the absorbed person credits the survivor.

The CV and its anonymous card travel together and the newest stays on the survivor; if the absorbed
card had the newer one the two swap, and the older file and card stay on the absorbed card. A survivor
left with a CV and no card, or with a card written from another CV, is flagged
(`scheda_da_rigenerare`, `absorbed` entry) and a failure recorded for its CV is cleared: the hourly
`cards-refresh` writes it, or an admin's «Rigenera scheda». A merge never calls Claude.

**Nothing is deleted.** The absorbed card is soft-deleted, its `users` row keeps its address and points at
the survivor (`merged_into_id`): a login link asked for that address opens the survivor, and the wizard
and the sign-up form that type it land on the survivor. The absorbed row gives up its LinkedIn profile
(one holder per profile). The survivor's trail gets an `absorbed` entry, the absorbed card a `merged`
entry whose payload lists every row that moved.

A merge is **refused**, with a sentence, when the absorbed card is an admin's, when the two have different
tax codes, when both have a live framework agreement of the same regime (`inviato`, `in_attesa` or
`firmato` and not yet terminated), or when both have an active match (`in_firma`, `attivo`) with the
same company.

## Production, in order

1. **Dry run**, and read it: the pair count must be what the card says, every pair `sposterebbe: ...` and none
   `bloccata`.
   ```
   docker exec rebase-api-1 uv run --no-sync rebase talent-duplicates
   ```
2. **Dump the database first**, readable by root only, and note the path on the card:
   ```
   sudo sh -c 'umask 077; docker exec rebase-db-1 pg_dump -U rebase -Fc rebase \
     > /root/hub-before-talent-merge-$(date -u +%Y%m%dT%H%MZ).dump'
   sudo ls -l /root/hub-before-talent-merge-*.dump
   ```
   The nightly backups are encrypted to a key no server holds (`restore-from-backup.md`), so they are the
   fallback, not the quick way back.
3. **Merge**, after Lorenzo has seen the plan:
   ```
   docker exec rebase-api-1 uv run --no-sync rebase talent-duplicates --apply --admin lorenzo@...
   ```
   It prints one line per pair with the `azione` id (keep those: they are what undo takes), then
   «indici unici creati: uq_users_linkedin_lower, uq_signups_linkedin_lower». A second dry run must say
   `0 coppie unibili` and `indici unici mancanti: nessuno`.
4. **The cards**: the survivor's card is flagged. With `REBASE_CARDS_LOOP=on` the next hourly run writes it;
   otherwise run `docker exec rebase-api-1 uv run --no-sync rebase cards-refresh --limit 50` by hand once
   (`projects/hub/AGENTS.md`, «The backlog is `rebase cards-refresh`»; never while the loop is on).

Migration 0035 itself needs none of this: on a database that still holds a pair it rewrites every LinkedIn
address to the stored shape, adds the two columns, and **leaves the unique indexes out** (they would be
refused while two rows share a profile); step 3 creates them. Every other database gets them from the
migration.

## Undoing a merge

```
docker exec rebase-api-1 uv run --no-sync rebase talent-duplicates --undo ACTION_ID --admin lorenzo@...
```

`ACTION_ID` is the `azione` the merge printed, or the `id` of the `merged` entry in the absorbed card's
trail (`GET /api/hub/freelancers/{absorbed id}/audit`, `get_freelancer_audit`). It moves every row the entry
names back to the absorbed card and person (only the rows still on the survivor: a login or a note the
survivor got since stays with the survivor), gives back the CV and card if they swapped, restores the
survivor's filled fields and a cleared card failure, takes away the refusal-to-be-mailed the merge copied, makes the absorbed card live and its address its own
again, and writes an `unmerged` entry on both cards. A merge is undone once. A field the survivor changed since (a phone number, a position, a profile) keeps its new value and is counted in `lasciati`; undo is refused, with a sentence, once the survivor was itself merged into somebody or deleted: undo that merge first.

One thing undo cannot give back while the unique index exists: a LinkedIn profile that **both** cards held
(the pair this exists for). The survivor keeps it and the card that comes back has none; an admin sets
another with `override_freelancer`, or drops the index first
(`DROP INDEX uq_users_linkedin_lower;` in `psql`, and `rebase talent-duplicates --apply` creates it again).

If the dump is the only way (a merge was undone by hand, rows were edited since): restore the dump into a
scratch Postgres as `restore-from-backup.md` does and copy back the rows by id; do not restore over the live
database while members are signing in.
