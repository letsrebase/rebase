# Brief: «Esporta per il commercialista» (PigroCRM)

Status: approved by Lorenzo on 2026-11-10, on the artifact `pigrocrm-accountant-export`
(the recommended option on all six questions, below). Card: REB-1013. Design:
`projects/pigrocrm/docs/superpowers/specs/2026-11-09-accountant-export-design.md`
§§ 1 to 4 (REB-966); frame: `2026-11-09-space-profiles-modules-and-the-money-picture-design.md`
§ A2 (modules), § A5 (the fiscal register, «to verify»), § A7 (this gate) and § B6 (the phone
shell). The rule this brief creates is the last row of `docs/design/DECISIONS.md` dated
2026-11-10; the build cards read this file and that row, never the artifact. The open points at
the end are inherited by the page cards.

Everything here is drawn on `shared/ui/tokens.css` and the primitives of `@rebase/ui`:
squared, 1px ink lines, Outfit, Prussian sidebar, Watermelon Deep only on the primary action
and on errors (the application variant, DECISIONS 2026-09-18). The `StatusPill` tones used are
`ink`, `muted`, `gold` and `danger`, never `accent`. One light scheme. Strings are Italian,
amounts `1.464,00 €`, dates `02/11/2026`, tabular numerals in every column of figures, no
emoji, no illustration. Loading is `LoadingState` (`Loader` plus a `role="status"` sentence),
never a skeleton. Every name, number and address in the mockups is invented example data; what
each part holds for a regime is the fiscal pack's to say (frame § A5), so no screen computes or
states a fiscal rule.

## Settled before asking

| Question | Settled answer | Source |
|---|---|---|
| Who makes and delivers an export | An admin whose scope covers the company. No accountant role, no invitation path: a `readonly` member sees more than an export | design § 3, § 5 |
| Share link: lifetime, count, audit, revoke | 7 days by default, 30 at most; a download count and a row per opening (when, from which address); one-click revoke; the link is the only credential | design § 3 |
| Payroll parts and the payroll export | Opt-in per export, admin only, never on a plain link: Drive, or a link that also asks a one-time code sent to the recipient's address the admin names | design § 3, § 4 |
| Drive delivery | Only when the space's storage is Google Drive, under `commercialista/<entity-slug>/<period>/<timestamp>/`; with local storage the page says so. Writing to Drive is a person's act | design § 3, § 5 |
| Open points | An export with open points is allowed and says so; the checklist snapshot goes in the ZIP's index | design § 2 |
| What exists in an export | The parts follow the modules and the company's fiscal pack; a part whose module is off is omitted. A module that is not built yet is shown nowhere | design § 1; frame § A2 |
| Storage and retention | One stored row per export, files timestamped, a rebuild never overwrites, split into several ZIP files when large, kept 13 months then deleted leaving a trace | design § 3 |
| Sending by mail | Not part of the export | design § 7 |
| The year register CSV | Stays as it is, on the «Anno» tab | design § 1; DECISIONS 2026-10-26 |
| Density | Airy to read, dense to fill: a dense two-column form with the action bar bottom right, lists on the `Table` rows (48 px, 14 px cells) | taste profile axes 1 and 6; application variant brief |
| Desktop list | A gridded table with the open item in a side panel | DECISIONS 2026-11-09 (REB-973, question 1); taste 2026-10-06 |
| Phone | One column of cards, a sticky action bar, the page reachable from «Altro» | DECISIONS 2026-11-09 (REB-956); `nav.ts` `phoneMoreItems`; frame § B6 |
| Status pill | Colour plus text, one `Record` from status to tone beside the labels | taste 2026-10-21; `StatusPill.tsx` |
| Loading, error, empty | `LoadingState` with a sentence; `QueryErrorBanner` saying what happened; an empty state is one sentence and the one action, no illustration | DECISIONS 2026-11-09 (REB-973) and 2026-11-10 (REB-981); taste axis 7 |
| The same element looks the same everywhere | Bands, pills, the selector, the form layout follow the shared pattern | taste 2026-10-21 |
| Not the books | The page shows labels, counts and states the server sends; it computes no figure | frame § A5, § A6 |
| Strings | Italian, 20% longer than English: the long label is drawn | taste standing rules |

## What Lorenzo decided

Lorenzo, 2026-11-10, on the artifact `pigrocrm-accountant-export`: «possiamo andare con i tuoi
consigli per tutto», so each question takes the recommendation.

| # | Question | Decision |
|---|---|---|
| 1 | Build then deliver, or build and deliver in one press? | A: «Crea esportazione», then deliver (link, download, Drive) from the export's panel; no delivery choice in the form |
| 2 | Does the checklist show every check or only the open points? | A: every check, one row each, «A posto» or «Da vedere» |
| 3 | Which company does the export follow? | B: a field on the form, preset from the sidebar; «Tutte le aziende» is a separate option, never preselected |
| 4 | Does the parts list name the parts a company does not get? | A: only the parts that apply; the ZIP's index names the omissions |
| 5 | Where does an admin see the live share links? | B: also a third tab «Link condivisi», every link with openings and «Revoca» |
| 6 | Where does the «variazioni paghe» export live? | A: a «Tipo» switch in the same form, one list for both kinds, a «Tipo» chip in the list |

## «Esporta per il commercialista»

```
Subject      The page under «Amministrazione» where an admin prepares the archive of one
             company and one period (a month, a quarter or a year), sees whether the period is
             ready, and hands it to the accountant: a ZIP with an index, delivered by
             download, a share link or the space's Drive folder. Also the «variazioni paghe»
             file for the payroll consultant.
Audience     The admin of the space: the founder or whoever talks to the accountant. They know
             what closing a period means, not what a hash or a stored artefact is: the page
             says «link», «scade», «aperture», never «token». A scoped admin (one company)
             uses it too.
The job      Hand over a complete period without collecting files by hand: choose, read what
             is missing, create, deliver. On the phone: send an archive already made (a link
             in two taps) or check what is missing before the accountant asks.
Palette      Paper ground, white cards, Prussian Blue ink and every line, Charcoal Blue
             secondary text, Watermelon Deep for «Crea esportazione» / «Crea il link» and for
             errors only, Royal Gold as the «waiting» dot of an open point. Tokens, never a hex.
Type         Outfit only. 24 px title, 14 px cell text, 12.5 px meta, tabular numerals,
             counts and sizes right-aligned in tables.
Density      Middle, as the application variant says: the `Table` primitive's own rows. The
             form is the dense block (two columns, label beside field, action bar bottom
             right). Phone cards airy, about 96 px.
Signature    The checklist frozen with the export: every export says how many open points it
             was made with, and the ZIP's index says the same. (inferred)
States       See the table below.
Constraints  Italian runs about 20% longer: the sidebar label «Esporta per il commercialista»
             is drawn at the real 17 rem width and in the collapsed rail. `shared/ui`
             primitives and tokens only. Phone first, proven by renders at 390 and 1280 px.
Non-goals    Sending the export by mail; importers for specific accounting software; a
             double-entry journal (design § 7); changing the year register CSV; the MCP tools
             (design § 5).
```

**Desktop.** `PageHeader` with the `PackageOpen` icon (inferred: no other entry uses it), the
title, one sentence («Un archivio ZIP per azienda e periodo: registri, documenti, movimenti e
imposte, con l'elenco di ciò che manca.») and no button: the page's job is a form, so its one
strong button sits bottom right of the form (taste axis 6), not top right. Tabs as «In arrivo»
has them: «Prepara», «Esportazioni» with its count and «Link condivisi» (answer 5: every link of
the space, with openings and «Revoca»; the export's panel keeps «Crea un link» and its own links). The tab is in the URL, with the open export, like
`/app/inbox?tab=&item=` (inferred).

«Prepara» is a dense form. Fields: «Tipo» (the accountant's export or «Variazioni paghe», answer 6; its form has no checklist block and a monthly period), «Azienda» (only from the second company on, answer 3: a field of its own, preset from the sidebar, with «Tutte le aziende» a separate option never preselected and, for the all-companies export, a band that says it includes the purchases with no company),
«Periodo» as a segmented «Mese / Trimestre / Anno» and a select of the periods, with the
range written beside it («dal 01/07/2026 al 30/09/2026»). «Cosa contiene» is the list of parts
the server says apply (design § 1: the two registers, the IVA settlement, integration
documents, the original invoice files, receipts and expenses, bank movements, open items,
taxes and F24, withholdings paid for others), each with where it comes from and its format.
It is read-only: the admin chooses no part except the opt-in «Parti paghe» block (three
checkboxes, off by default: «Cedolini», «F24 delle paghe», «Ritenute sulle paghe»), drawn only
when the staff module is on, with the sentence that says they never travel on a plain link.
Then «Il periodo è pronto?»: every check of design § 2 as a row, the fine ones included, each with a state and,
where there is something to do, a link to the place that fixes it («Apri In arrivo», «Apri
Fatture»). A check of a module that is off is not drawn. The action bar says that open points
do not stop the export, and ends in «Crea esportazione».

«Esportazioni» is a gridded table with the open export in a panel beside it (330 px, as the
inbox): columns «Azienda e periodo» (the company and «3° trimestre 2026 · 10 parti»), «Creata»
(date, time, who), «Controllo» (a pill: «Completa» or «N aperti»), «Stato». Filters «Azienda»
(when several) and «Anno»; 50 rows at a time with «Mostra altri». The panel holds the export's
index («Cosa c'è nello ZIP», each part with its row count and each omitted part named), the
checklist as it was when the export was made, «Consegna» and, under it, the links (below). The
panel's one strong button is «Crea un link per il commercialista», since the link is how the
file reaches the accountant (design § 3); «Scarica lo ZIP» (with the size) and «Scrivi su Drive»
are quiet buttons. It ends with the retention line («Si conserva fino al 10/12/2027, poi viene
eliminata e ne resta traccia»). A «Tipo» chip («Commercialista» or «Variazioni paghe») sits under the period in the first column when the space has the staff module.

«Link condivisi» is a gridded table of every link of the space with the same side panel: columns «Esportazione» (company, period, creation date), «Scade», «Aperture» (right-aligned), «Stato». The panel has the link's openings (date, time, address) and «Revoca». Expired and revoked links stay in the list as muted rows.

The link dialog has «Scade tra» (a select: 1, 3, 7 by default, 14, 30 days; inferred
presets) and «Crea il link». With payroll parts in the export the dialog becomes «Link con
codice»: «Destinatario» is required and the sentence says the recipient receives a code; a plain
link is not offered. After creation the link is shown once, with «Copia il link» and the
sentence «Lo vedi solo ora: non lo conserviamo. Se lo perdi, revocalo e creane un altro.» (the
link is stored only as a hash, design § 3). Each link in the list shows the date, the expiry,
the count of openings, the pill «Attivo» / «Scaduto» / «Revocato», the openings (date, time,
address) and «Revoca», which acts at once with a toast and no confirmation (design § 3:
one-click).

**Phone.** The page is in «Altro», after «Fatture», «Ore» and «Giornate», for an admin only.
A segmented «Prepara / Esportazioni / Link» over one
column of cards: «Azienda» and «Periodo» cards, a «Cosa contiene» card that says «10 parti»
and opens the list, the checklist as one card per check, and a sticky «Crea esportazione». An
export opens full screen with its state, «Consegna» as three full-width buttons (the link one
strong) and its links as cards. The admin sends a link or checks the period here; downloading
a large ZIP is possible but is not the phone's job. No new bottom-bar place and no «+»
entry: an export is not a quick add.

| State | What is drawn |
|---|---|
| Empty | «Nessuna esportazione» and one sentence («Qui trovi gli archivi creati per il commercialista: da qui li scarichi, li condividi con un link o li scrivi su Drive.»), with the single action «Prepara un'esportazione». «Prepara» has no empty state: it is the form. No illustration |
| Loading | `LoadingState` with a sentence in the place of what is read: «Leggo le esportazioni…», «Controllo il periodo…». A build in progress is a row with the pill «In preparazione», `Loader` and «Preparo l'esportazione…»; the admin may leave the page and find it done |
| Error | `QueryErrorBanner` and nothing under it. A checklist that could not be read is that banner in its place and never «Tutto in ordine»; «Crea esportazione» stays, since the build reads the period itself (inferred). A failed build is a danger pill «Non riuscita» with the reason in one line and «Riprova», which makes a new export |
| Many / long content | 50 rows at a time. A company name of 60 characters is ellipsised with the full name on hover. A check that lists invoices shows the first five numbers and «e altre 12», the full list being in the index. A split export says «3 file ZIP» and offers each file |
| Open points | Gold, never danger: the export is allowed. The row says «4 aperti», the panel keeps the snapshot as of its creation, and a later fix does not rewrite it |
| Module off | The parts and checks of a module that is off are not drawn, and nothing on the page names the module (answer 4). The «Parti paghe» block is absent while the staff module is off |
| Profile or pack not applicable | A flat-rate company has no IVA settlement and a `non-it` one no IVA register (design § 1): those parts are absent from the list, and the index in the ZIP names each omission. A `solo` space with few modules simply has a shorter list; a one-company space never sees the word «Azienda» |
| Nothing to show | A period with no documents is still exportable; the panel shows each part with 0 rows. The checklist has no row for a check that does not apply |
| Drive | With Google Drive storage: «Scrivi su Drive» and, once done, «Scritta il 10/11/2026 10:40 in commercialista/lumen-studio/2026-t3/20261110t1040/». With local storage: one sentence («Questo spazio salva i file sul server, non su Google Drive: la consegna su Drive non è disponibile.») and no button. With Drive unreachable: a red band and the link «Apri Google Drive» |
| Share link | Created (shown once), `Attivo`, `Scaduto` (kept in the list as a muted row), `Revocato`. A payroll export offers only the code link and Drive |
| Retention | An export past 13 months is a muted row «Eliminata il 10/11/2026: oltre i 13 mesi» with no delivery buttons (the trace, inferred) |
| Permissions | See below |

Permissions, from `apps/web/src/lib/permissions.ts` and design § 3, § 5:

| Who | Sees the entry | On the page |
|---|---|---|
| Admin, scope all | Yes | Everything; «Tutte le aziende» as a separate option (answer 3) |
| Admin scoped to some companies | Yes | Only their companies, no «Tutte le aziende»; one company is a pinned label, as `LegalEntitySelect` does |
| Collaborator, readonly | No | A stale link gets the sentence `toProblem` writes for a 403, naming the role |
| Agent with `full_access` | n/a | Has the tools of design § 5; «Scrivi su Drive» is a person's act and is on no tool |
| A person holding a link | n/a | The recipient page below, until the link expires or is revoked |

**What the accountant opens** (inferred; the card names no recipient page). A minimal page on
the same tokens, never a bare download, so a link in a mail says what it is before it
downloads: «Esportazione per il commercialista», the company and period, who made it and the
expiry, and «Scarica lo ZIP» with its size (one button per file when split). For a link with a
code, «Ti abbiamo scritto un codice a p…@studio-esempio.it» and six boxes. It carries no
navigation and no account prompt.

## What a component can violate

- The entry and every action are admin only: the sidebar and «Altro» entry is hidden for a
  collaborator and a readonly member, and `lib/permissions.ts` gets rows at `admin` for the
  export actions, pinned against the core's `require_admin` strings by its test. Hiding the
  entry alone is not the rule.
- With any payroll part ticked, or for the «variazioni paghe» export, no control creates a
  plain link: the link dialog asks for the recipient and says a code is sent. Checkable: a
  test renders the dialog with a payroll export and finds no recipient-less submit.
- The link's address is shown once, at creation, and never from a list: no «Copia» on a stored
  link. A list row shows state, expiry, count, openings and «Revoca» only.
- «Revoca» acts on one click, with a toast that says what happened and no confirmation dialog.
- The expiry offers at most 30 days and defaults to 7.
- Open points never disable «Crea esportazione» and are gold, never danger. The snapshot on an
  export is never recomputed after it was made.
- An export is never replaced: a rebuild is a new row. No «Sostituisci».
- The page draws no part, check or sentence for a module that is off or not built, and never a
  muted row naming one (answer 4). Which parts apply comes from the server: the page holds no
  list of fiscal packs, and the index in the ZIP is where omissions are named.
- Every link of the space is listed in the tab «Link condivisi», with its export, expiry,
  openings and state, and «Revoca» is there as in the export's panel (answer 5).
- The «variazioni paghe» export is a «Tipo» of the form and a chip in the list; it has no tab
  of its own (answer 6).
- Delivery is done on a stored export, from its panel: no delivery choice in the form and no
  «Crea e scarica» (answer 1). Every check is a row with its state, the fine ones included
  (answer 2).
- The page computes no fiscal figure: counts, sizes and states are the server's.
- Every status goes through `StatusPill` with a `Record` from status to tone beside the labels
  (`building` gold, `ready` ink, `failed` danger, `deleted` muted; link `active` ink,
  `expired` and `revoked` muted); no inline tone.
- A wait is `LoadingState` or the row's `Loader` with a sentence; no `Skeleton`, no new
  spinner.
- «Scrivi su Drive» is drawn only when the storage backend is Google Drive; with local storage
  the sentence stands in its place, never a disabled button without a reason.
- «Tutte le aziende» is never the preselected value of the form (answer 3): it is a separate
  option, named in a band the moment it is the page's subject, and absent for a scoped admin.
- Dates `10/11/2026`, sizes `38,4 MiB`, tabular numerals in every column of figures. Italian
  strings are the long ones: the entry label fits at 17 rem and in the rail.
- One strong button per tab; Watermelon Deep on it and on errors only.
- No horizontal scroll at 390 px; the phone's action bar is sticky above the bottom bar.

## Inferred, not asked

These follow from the design and the code and were not shown to Lorenzo; a reviewer should push
back on any.

- The route is `/app/export`, with `tab` and `export` in the URL like `/app/inbox`. The page
  belongs to no module (the registers it reads are core), so `ROUTE_MODULES` has no row for it;
  what it contains follows the modules.
- The entry is last in «Amministrazione». It is the first entry of the sidebar to be hidden by
  role: `nav.ts` says today that none of its entries is admin gated, so the build adds a role
  filter next to the module filter of `visibleSections` and `phoneMoreItems`.
- The header has no button; the strong action is the form's. This departs from `PageHeader`'s
  own note («one strong thing per screen, top right») because the page's job is a form.
- The period picker defaults to the last complete period of the entity's IVA periodicity (a
  quarter for a quarterly entity, otherwise a month) and also lists the current one tagged
  «(in corso)». The periodicity is C3's data; if the build cannot read it, the default is the
  last complete month. «Anno» is read as the calendar year here; see the open points.
- The parts list is read-only and carries no row counts before the build; counts live in the
  index shown in the panel.
- The six checks use the design's own words; their states are «A posto» (ink), «Da vedere»
  (gold), «Non richiesto» (muted). The payroll check shows only when payroll parts are ticked.
- The link dialog's expiry presets, the one-time code's six boxes and the recipient page.
- Any admin whose scope covers the company sees and can revoke every link of an export. Design
  § 3 says the controls are «shown to the admin who made it», read here as the admin side, not
  as «only the maker».
- «variazioni paghe» is drawn monthly and without a checklist block, because design § 4 gives it
  neither; its parts are the staff changes and the expense notes to reimburse (absences only if
  C7 adds them). It is a «Tipo» of the same form and list (answer 6), not a tab.
- An export that was deleted at 13 months stays as a muted row (the «trace» of design § 3).
- The Drive wording and the sentence for a Drive that cannot be reached, reusing the
  `space_storage` reading the Drive settings tab already has.

## Open points

Gaps and contradictions in the sources, reported when the brief was written and not settled by
Lorenzo's answers. The build cards inherit them; each needs an answer in the design or in the
card that builds the piece. Paths are from the repository root.

- **«Write to Drive» against «a storage prefix of its own».**
  `projects/pigrocrm/docs/superpowers/specs/2026-11-09-accountant-export-design.md:101` says
  «write to Drive» means the space's storage when it is Google Drive, while `:106` to `:108` say
  an export is a row with its files under a storage prefix of its own. With Drive storage both
  are Drive, so the page cannot tell an admin whether the export is already there or whether
  «Scrivi su Drive» copies it to `commercialista/...`. Drawn as a copy; the delivery card
  should confirm.
- **A year.** The same design says «a year» (`:20`); DECISIONS 2026-11-09 (REB-964,
  `docs/design/DECISIONS.md:280`) puts the fiscal year end on the legal entity. The picker is
  drawn on the calendar year; if a fiscal year can differ, its label needs the dates, as the
  quarter's does.
- **The recipient page.** Design `:91` says the link is «opened without an account» and `:98`
  that a payroll link asks a code; it designs no page for either, and the card names none.
  Drawn minimal in this brief (inferred); it is a surface the share-link card builds.
- **«variazioni paghe».** Design `:113` to `:118` gives it no period and no checklist; drawn
  monthly with no checklist block.
- **Who sees a link.** Design `:94` says the openings are «shown to the admin who made it».
  Answer 5 B (a tab of every link) reads this as the admin side, any admin whose scope covers
  the company; if only the maker may see them, the tab lists the maker's links.
- **Frame § A2 and the page.** `2026-11-09-space-profiles-modules-and-the-money-picture-design.md:81`
  to `:83` says an off module is gone from the sidebar, the phone bar, the Home and the search;
  it does not say whether a page that lists parts may name one. Lorenzo's answer to question 4
  settles it for this page (never named), and the index names the omissions.
- **The sidebar's role gate.** `projects/pigrocrm/apps/web/src/components/nav.ts:38` says no
  entry is admin gated; this entry is the first, so the build adds a role filter beside the
  module filter (see «Inferred, not asked»).
- **`PageHeader` and the form's button.** `projects/pigrocrm/apps/web/src/components/PageHeader.tsx:17`
  says a page's primary button goes top right; this page puts its strong button at the foot of
  the form (taste axis 6). Flagged for the review of the build, not asked.
- **Not verified in this brief.** The entry label «Esporta per il commercialista» at the real
  17 rem sidebar width and in the collapsed rail: the artifact draws the sidebar wider than the
  kit's; the build proves it by a render.

## Not in this brief

The build of the bundle, the registers, the storage and the retention job (the other cards of
REB-966); the MCP tools (design § 5); importers for specific accounting software and sending by
mail (design § 7); the year register CSV on «Anno»; the module and profile settings
(REB-954); the readiness checks' own sources (inbox, purchases, bank, IVA periods), which this
page only reads.
