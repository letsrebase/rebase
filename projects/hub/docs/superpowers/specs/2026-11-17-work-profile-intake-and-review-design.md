# Work profiles: receiving them in the hub and reviewing them before they count

Status: approved by Lorenzo on 2026-11-17, the four surfaces as § 10 and §§ 2, 4, 5 and 7
describe them, and every call of § 12 as written. Card: REB-1122. Project: «Let freelancers show
how they work with a reviewed work profile» (P-REB-91). Decision row:
`docs/design/DECISIONS.md`, 2026-11-17 (REB-1121).

## Why

A CV says what somebody did. It does not say how they work with coding agents, which is
most of what a client asks rebase about today. Play New's `apply-new` (MIT) reads that from
the person's own agent logs, on their laptop, redacts it there and writes a profile of how
they work: fields of work, how they reason, how they work with the model, how deep and how
spread their practice is, how fluent they are in the agentic stack, plus three to five
representative projects. Nothing leaves the machine until the person sends it.

rebase forked it as `letsrebase/work-profile` (REB-1121). This design is the hub's half:
how a profile reaches the hub, how a person in the lab reviews it, and what an approved
profile changes for the freelancer. The aim, in Lorenzo's words of 2026-11-17: profiles
already selected before anyone has to contact them, used in our own searches and in the
team builder, and a reason for the freelancer to send one (a badge, more visibility).

## What Lorenzo decided (2026-11-17, in the session that filed P-REB-91)

- **A draft until a person approves it.** A received profile counts for nothing (no badge,
  no search effect, no card source) until an admin approves it in the hub.
- **Approving the profile vets the freelancer.** Approval sets the existing «Verificato da
  rebase» flag (`freelancers.vetted_at`, REB-509) when it is not set already. There is one
  badge, not two.
- **Companies in the talent cloud see the approved profile's anonymous summary**, beside
  the card they already see.
- **The approved profile becomes a source of the anonymous card**, so the team builder,
  which reads cards, uses it without reading profiles directly.
- **The CLI signs in from the browser**, like `gh auth login`: no code to copy from a mail,
  no password. Signing in to the hub keeps the magic link, and «Accedi con GitHub / Google»
  comes as a separate design (§ 11), with the magic link left as the alternative.
- **Bands before scores.** Per-technology usage bands computed without a model are a later
  milestone (REB-1129); a score about a person waits for REB-1132.

## Goals

1. A freelancer runs `rebase-verify` (called `work-profile` until REB-1140), reviews the profile, approves the sign-in in their
   browser, and the profile is stored on their hub card as a draft.
2. The hub rechecks what it received (schema, structure, groundedness) and refuses a
   profile whose numbers or prose do not hold, the way upstream's intake does.
3. An admin reads the profile beside what the recheck flagged and approves or rejects it,
   with a note the freelancer reads on rejection.
4. Approval vets the freelancer, puts the summary in the cloud and the profile in the card.
5. The freelancer sees the status in their area and can delete the profile at any time.
6. A profile is kept at most 12 months from its submission.

## Non-goals

- **No score and no ranking on profile fields.** The only search effect is the one the
  vetted flag already has (the cloud lists vetted talents first). No sort, filter or
  threshold reads a profile number, in the admin, the cloud or the team builder.
- **No profile without the person.** An admin cannot create or edit a profile; the hub
  stores exactly what the freelancer sent, minus what § 4 strips.
- **No file artifacts.** Upstream lets a candidate attach files to a project; the hub
  refuses them. A URL artifact is kept as text.
- **No model call in the hub for profiles.** The narrative was written by the person's own
  model on their machine; the hub's recheck is deterministic.
- **Social sign-in** is not in this design (§ 11).

## Anti-goals

It fails, even if it works, when: a freelancer is shown a promise the hub does not keep
(the fork's texts still saying Play New, or «nothing ranks you» when approval vets you); a
profile counts before a person read it; a client name or repository name reaches a company
through the cloud summary; a profile outlives its 12 months.

## 1. Approaches considered

How the profile reaches the hub and is tied to the right person.

| | How | Trade-off |
|---|---|---|
| A. One-time code | The freelancer mints a code in their area, the CLI sends with `submit --code` | Simple to build; one more thing to copy by hand |
| B. Upload | The freelancer uploads `candidate.json` in their area, like the CV | Simplest, no auth work; the CLI's `submit` and its last-minute re-derivation go unused |
| **C. Browser sign-in for the CLI** | `submit` starts a device authorization, the browser opens the hub, the signed-in person approves, the CLI gets a single-use token | One flow the person already knows from `gh` and `claude`; a little more to build |

Lorenzo chose C (2026-11-17), with social sign-in to the hub as a separate design. B is not
built: one way in keeps one set of checks.

## 2. The CLI's sign-in (device authorization)

The shape of RFC 8628, kept as small as the one use needs: the token can only send one
profile, so it is never written to disk and there is nothing to revoke later.

1. `submit` calls `POST /api/hub/cli/device` with `{ "scope": "work_profile" }`. The hub
   answers `device_code` (32 random bytes, base64url, stored as its sha256), `user_code`
   (8 characters as `XXXX-XXXX`, from an alphabet without `0 O 1 I L`), `verification_uri`
   (`https://letsrebase.com/hub/cli`, the preview host on the preview), the same with
   `?code=` as `verification_uri_complete`, `expires_in` 600 and `interval` 5.
2. The CLI prints the code, opens `verification_uri_complete` in the browser (prints it
   when it cannot open one), and polls `POST /api/hub/cli/token` with the `device_code`
   every `interval` seconds.
3. `/hub/cli` is a signed-in page. A visitor who is not signed in gets the login page and
   comes back to it after the magic link. The page shows the code, what the CLI will be
   able to do («inviare il tuo profilo di lavoro, nient'altro») and two buttons,
   «Autorizza» and «Annulla». It never shows a device name or an address: the CLI sends
   none.
4. Polling answers `authorization_pending` until the person decides, `slow_down` when the
   CLI polls faster than `interval` (the interval grows by 5 seconds), `access_denied` on
   «Annulla», `expired_token` after 600 seconds, and on «Autorizza» `access_token` with
   `expires_in` 900. The token is shown once, stored as its sha256, bound to the user, the
   scope and the device authorization, and spent by the first submission.
5. Only a user with a live freelancer card can authorize the `work_profile` scope; anyone
   else sees the reason on the page and no button. A freelancer always has a `users` row
   (`freelancers.user_id`), which is what the authorization binds to: the device flow
   creates no user, and somebody with no card applies through the wizard first.

**Table** `cli_authorizations`: `id`, `device_code_hash` (unique), `user_code` (unique
while pending), `scope`, `user_id` (null until decided), `state` (`pending`, `approved`,
`denied`, `spent`, `expired`), `token_hash` (unique, null until approved), `created_at`,
`expires_at`, `decided_at`, `last_polled_at`, `spent_at`. Rows hang on the user with
`ON DELETE CASCADE`, and the sweep deletes rows a day after they expire or are spent.

**Limits**, answered 429 with a sentence: 10 device starts an hour per client address
(`X-Real-IP`, an IPv6 address as its /64, the MCP transport's rule); 5 wrong codes typed on
`/hub/cli` per user in 10 minutes. With 31^8 codes (the alphabet without `0 O 1 I L`) alive for 10 minutes, guessing one is
not a practical attack; the per-user limit is for typos and scripts.

## 3. The intake

`POST /api/hub/work-profiles`, `Authorization: Bearer <access_token>`, multipart with one
part `profile` (`candidate.json`, at most 512 KB). Any other part is refused (422,
«gli allegati non sono accettati: indica un link»).

In order, before anything is stored:

1. **The token.** Unknown, expired or spent: 401. It is marked spent in the same
   transaction as the store, so a retry after a network error needs a new sign-in (the
   CLI says so).
2. **The schema.** `schema` is `playnew-profile/v1` (the fork keeps upstream's schema id,
   so profiles stay readable by both). Pydantic models mirror the fields the hub reads;
   unknown fields are kept, since upstream adds optional ones (its CONTRIBUTING requires
   them nullable).
3. **What must not be there.** A `repoLabel` anywhere, or a project artifact of type
   `file`: 422. `contact` is dropped: the hub knows who sent it from the token, and does not
   store the email and city a second time.
4. **The structure.** Upstream's intake checks structural invariants in Play New's private
   dashboard, so these are ours: counts are non-negative
   integers; the domains' summed `products` and `sessions` do not exceed the profile's
   totals; there is at least one representative project and at most ten (the CLI picks
   three to five, fewer when the logs hold fewer, `--top` forces another count), each with
   an id the narrative uses; dates parse and the window's start is not after its end.
5. **Groundedness, recomputed.** `rebase_core.work_profiles.groundedness` ports
   `src/groundedness.mjs`: it extracts the anchors from the prose fields (numbers,
   technology names from the vendored `tech-lexicon.json`, type tags, year-months) and
   checks each against the structured data. The CLI already blocks under 60%; the hub
   refuses under 60% too (422, with the score), and stores the score and the unsupported
   anchors of what it accepts. The port carries upstream's own test vectors, so a change
   upstream that moves a score shows up as a failing test when the fork merges it.

Stored as a `work_profiles` row: `id`, `freelancer_id` (unique, `ON DELETE CASCADE`),
`payload` (JSONB, as received minus `contact`), `tool_version`, `groundedness` (0-100),
`unsupported_anchors` (JSONB list), `state` (`draft`, `approved`, `rejected`),
`submitted_at`, `expires_at` (`submitted_at` + 12 months), `decided_at`, `decided_by` (FK
users), `decision_note`. One row per freelancer: a new submission replaces the previous
one and goes back to `draft`; the replaced payload is not kept.

Answer: 201 with `{ "state": "draft", "review_url": "https://letsrebase.com/hub/me" }`.
The CLI prints that a person at rebase will read it and where to see its status.

## 4. Review

On the talent's admin page (`/hub/admin/freelance/<id>`), a «Profilo di lavoro» section,
shown when a row exists:

- the state, the submission date, the expiry date, the tool version;
- what the recheck flagged: the groundedness score and the unsupported anchors, each with
  the field it came from;
- the profile, rendered from the payload like upstream's `profile.md`: summary, domains,
  the lenses' narratives and counts, the trajectory, the representative projects with
  their URL artifacts;
- «Approva» and «Rifiuta». Rejecting requires a note, which the freelancer reads in their
  area. Approving takes an optional note, kept for the admins.

**Approving** (`WorkProfileService.approve`) sets `state`, `decided_at` and `decided_by`,
and writes its own audit entry of kind `work_profile_decided` (20 characters, the column's
limit) every time, with the decision and whether it vetted. Only when `vetted_at` is null
does it also call `FreelancerService.set_vetted`, which writes the `vetted` entry as today;
that method records nothing when the flag already matches, which is why the decision has
an entry of its own. Approval marks the card stale (§ 6).

**Rejecting** sets the state and the note; the profile shows nowhere outside the admin.
`vetted_at` is not touched.

**Finding the ones waiting.** «Talenti» gets a filter «Profilo di lavoro» (`da rivedere`,
`approvato`, `nessuno`) and a pill «Da rivedere» on a row whose profile is a draft. The
same filter on `GET /api/hub/talent` and on `list_talents` (`work_profile`). These filter
on the review state, never on a profile field.

**Over MCP**, read only: `get_talent` carries `work_profile_state`, and `get_work_profile`
returns the rendered profile, the score and the anchors. There is no approve or reject
tool: the decision is a person's, made in the app, as PigroCRM does with proposals. Each
`get_work_profile` leaves an `AdminAction` of kind `work_profile_read`, the payload naming
the tool, never a value.

## 5. What an approved profile changes

- **The badge.** The vetted badge, which already shows in «Talenti», on the talent's page
  and in the cloud.
- **The cloud.** `CloudTalentRead` gains `work_profile: CloudWorkProfile | null`, filled
  only when the row is approved: the summary, the domains' labels with their counts, and
  the representative projects' abstract domains. Not the narratives, the trajectory, the
  agentic-literacy counts or the artifacts, which stay in the admin. The cloud's order is
  unchanged (vetted first, then by name), so the only search effect is through the flag.
  `CloudTalentRead`'s docstring, which lists what a company may read, names the summary.
  The hub's deterministic anonymity guards (`work_profiles/guard.py`) read those three fields when the
  profile is stored, at approval and at every cloud read: approval is refused while a hit stands unless
  an admin overrides with a note, so the admin's reading is not the only guard (REB-1127).
- **The card** (§ 6).
- **The freelancer's area** shows «Approvato» and the date it expires.

## 6. The profile as a card source

The approved profile is the hub's own data, like the hub facts (`hub_facts.py`), not a page
fetched from outside: the card writer reads it from `work_profiles` when it composes the
card, and nothing is copied into `freelancer_sources` (whose per-row kinds,
`SOURCE_KINDS = ("github", "link")`, stay as they are). What it reads: the approved
profile's summary, domains, the representative projects' domain and stack, and the
agentic-literacy narrative, as text with its provenance (`work_profile`). It gets a switch
in `ai_sources` beside `github` and `links` (`AI_SOURCE_KINDS` and `ck_ai_sources_kind`
gain `work_profile`), off until the privacy notice covers it (§ 9). Its hash is part of the
card's staleness material, so an approval, a replacement or a deletion makes the card stale
and the normal regeneration picks it up. The anonymity guard runs on it as on every
source. A draft or rejected profile is never read.

## 7. The freelancer's side

In the member area (`/hub/me`), a «Profilo di lavoro» section below «La tua scheda»:

- before any submission: what the tool is (fork of Play New's `apply-new`), what it reads,
  what stays on the computer, what reaches rebase, that a person reads it before it
  counts and that approval means «Verificato da rebase»; the three commands to run; a link
  to the fork's README and to the privacy notice;
- after: the state (`In revisione`, `Approvato fino al <data>`, `Non approvato` with the
  admin's note), the submission date, and «Elimina il profilo».

Deleting (`DELETE /api/hub/me/work-profile`) deletes the row at once and marks the card
stale. It does not remove the vetted flag: that is the admins' judgement, and an admin
removes it with «Togli la verifica» if they want to. Lorenzo confirmed this on 2026-11-17.

A mail on each decision, through `mail_layout.py`: approved (with the expiry date) and not
approved (with the note). Its Italian copy is reviewed with the campaign's (REB-1131).

## 8. Retention

A new command, `rebase work-profile-sweep`, deletes `work_profiles` rows past `expires_at`
and `cli_authorizations` rows a day after they expire or are spent. It runs in the existing
`sweep` service's loop, after `rebase crm-sweep`, so both stacks carry its schedule
without a new container. Neither table joins `WAREHOUSE_TABLES`
(`packages/core/tests/test_warehouse_contract.py`), so neither reaches the warehouse. A soft-deleted
freelancer loses the profile at once, like their sources (`delete_all`), and a merge keeps
the newer submission of the two.

## 9. Texts and privacy

- **The fork** (REB-1123): every text read before consenting names rebase as the
  recipient, the purpose (being reviewed and, once approved, verified and shown to
  companies in the talent cloud through the summary) and the 12 months; the contact
  questions are removed, since the sign-in identifies the person; `submit` runs the sign-in
  of § 2.
- **The privacy notice** on the site gains a work profile section and the card-source
  line. Legal basis: consent, given by `submit` after the payload preview.
- Both texts are public and the controller they name is Lorenzo's to approve before they
  merge, and the source switch of § 6 stays off until then.

## 10. Brief for the new surfaces

Three new surfaces (the member section, `/hub/cli`, the admin section) and two small edits
inside existing ones (the cloud card's summary, the «Talenti» filter and pill).

```
Subject      the work profile: what the freelancer sent, its review, its effect
Audience     member section and /hub/cli: a freelancer, technical, who just ran a CLI;
             admin section: Lorenzo or Ivan deciding whether to vouch for somebody
The job      member: know what happens and run it, then see the outcome;
             /hub/cli: say yes to one CLI, knowing what it can do;
             admin: decide approve or reject with the evidence in view
Palette      the hub's tokens (shared/ui/tokens.css) and nothing else; the state pills use
             the semantic hues as pale surface plus saturated text (taste axis 4) [inferred]
Type         the hub's face from tokens; tabular numerals for counts and the score [inferred]
Density      airy reading (taste axis 1): the profile reads as rows of a ruled list, like
             «La tua scheda»; the two admin buttons sit bottom-right of the section
Signature    the unsupported anchors listed next to the prose they came from
States       empty (no profile: the explanation and the commands), draft, approved,
             rejected with note, expired; /hub/cli: pending, signed out, no card, wrong or
             expired code, authorized, cancelled; long content (a five-project profile)
Constraints  Italian copy, mobile width for /hub/cli and the member section, existing
             @rebase/ui primitives only, no new primitive
Non-goals    no chart of the counts, no comparison with other people, no editing a profile
```

## 11. Signing in to the hub with GitHub or Google (not here)

Lorenzo wants «Accedi con GitHub / Google» on the hub, with the magic link left as the
alternative. It changes how anybody signs in, admins included, so it has its own design,
`2026-11-18-sign-in-with-github-or-google-design.md` (REB-1137): account linking only on an
address the provider says is verified and that is a login address (spec 2026-09-17, one hub
one identity), no account created for an address nobody here has, and the two OAuth clients,
which Lorenzo creates under letsrebase's accounts. `/hub/cli` works with whichever sign-in
the hub has.

## 12. Calls made in this design, confirmed by Lorenzo on 2026-11-17

- Deleting the profile, or its expiry, leaves the vetted flag in place (§ 7).
- A rejection note is shown to the freelancer; an approval note is not.
- A new submission replaces the previous profile and goes back to review, hiding the cloud
  summary until it is approved again.
- File artifacts are refused; URL artifacts are kept and shown only in the admin.
- No approve or reject tool over MCP.
- The token lives 15 minutes, sends one profile, and is never written to disk.

## 13. Cards

Existing, reworded to this design: REB-1123 (the fork sends with the browser sign-in),
REB-1124 (the intake of § 3), REB-1126 (the review of § 4, approval vets), REB-1127 (§ 5
and § 7). New: the CLI sign-in in the hub (§ 2), the profile as a card source (§ 6), and
the design for social sign-in (§ 11, `2026-11-18-sign-in-with-github-or-google-design.md`).
