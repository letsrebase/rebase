# Brief: «Tasse» and the Home's tax dates (PigroCRM)

Status: awaiting Lorenzo's answers on the artifact `pigrocrm-taxes`; draft 2026-11-10.
Card: REB-1007. Design: `projects/pigrocrm/docs/superpowers/specs/2026-11-09-taxes-and-contributions-design.md`
§§ 3 and 6 (REB-964), with § 1 (law in the pack, parameters in the profile), § 2 (the three
readings, due items, plans, credits, F24) and § 5 (who may read); frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` § A1 (taxes are never
summed across legal entities), § A2 (the `taxes` module), § A5 (the register of rules «to
verify»), § A7 (every new surface passes this gate), § B5 (Home per profile) and § B6 (the
phone shell); due items:
`2026-11-09-due-items-bank-and-cash-schedule-design.md` § 1 (the four states of a due item).
This brief creates no rule yet. Once Lorenzo answers, the orchestrator writes the rows of
`docs/design/DECISIONS.md` and the build cards read this file and those rows, never the artifact.

Everything here is drawn on `shared/ui/tokens.css` and the primitives of `@rebase/ui`:
squared, 1px ink lines, Outfit, Prussian sidebar, Watermelon Deep only on the primary action
and on errors (the application variant, DECISIONS 2026-09-18). It uses four of the five
`StatusPill` tones, `ink`, `muted`, `gold` and `danger`, and never `accent`. One light scheme.
Strings are Italian, amounts `1.464,00 €`, dates `02/11/2026`, tabular numerals in every column
of figures, no emoji, no illustration. Loading is `LoadingState` (the `Loader` plus a status
sentence), never a skeleton.

**Every amount, date and name in the artifact is invented.** Most fiscal rules this surface
touches are still «to verify» in the frame's register (A5). The brief never states one as fact
and no screen computes one: a screen shows the readings, dates and labels the pack, the
accountant and the due items supply.

## Settled before asking

A question already answered is a defect to ask again.

| # | Question | Answer | Source |
|---|---|---|---|
| 1 | Reading against entry | Airy reading, dense entry: ruled tables on the `Table` primitive's own rows, forms dense and two-column, action bar bottom right | Taste profile axes 1, 5, 6; application variant § 1 (middle density, 48 px rows) |
| 2 | Table or cards | A list whose job is «did X happen» is a gridded table, numbers right-aligned in tabular numerals; on the phone one column of cards | Taste profile 2026-10-06; inbox brief, «In arrivo» |
| 3 | Table with detail | A table with the open row's detail in a 330 px panel beside it on desktop; parts of a page are tabs, a segmented control on the phone, a full-screen detail on the phone | DECISIONS 2026-11-09 (REB-973, answer 1); inbox brief |
| 4 | Empty and error states | One sentence on what lives here and the single action; an error says what happened and «Riprova», with nothing under it | Taste profile axis 7; `QueryErrorBanner`; inbox brief |
| 5 | Loading | `LoadingState`: `Loader` plus a `role="status"` sentence, never a skeleton and never a new spinner | DECISIONS 2026-11-09 and 2026-11-10 (REB-973, REB-981) |
| 6 | Status | A pill with colour and text; tones `ink`, `muted`, `gold`, `danger` kept in a `Record` beside the labels | `StatusPill.tsx`; taste profile 2026-10-21 |
| 7 | The same thing looks the same | The reading words and the pills are the same on the Home, on «Tasse» and on the cards that exist today | Taste profile 2026-10-21 |
| 8 | The phone | Five-place bar, centre «+», figures before lists, every other place in «Altro» (so «Tasse» is reached from «Altro» and from the Home) | DECISIONS 2026-11-09 (REB-956); `nav.ts` `phoneMoreItems` |
| 9 | A missing figure | «non calcolabile», never `0,00 €`; a missing parameter gives no figure, never a guess | `FiscalPanel.tsx` (`NOT_COMPUTABLE`); design § 1 |
| 10 | The warning sentence | The server's own sentence stands above the figures, verbatim, before the decision is made | `FiscalPanel.tsx` docstring; `FiscalEstimate.warning` |
| 11 | No number is born in the browser | Totals, nets, remainders and shares come from the server; the page formats strings | `src/test/no-browser-arithmetic.test.ts`; design § 2 (the F24 net is matched whole) |
| 12 | Several legal entities | Taxes are one entity's and never summed across entities; in «Tutte le aziende» one block per entity under its name | Frame § A1; `YearTab.tsx` and `FiscalPanel.tsx` (`title`, `quiet`) |
| 13 | Who may read | Tax figures, dates and due items are an administrator's whose scope covers the entity; the entry still shows for every role and the page explains in one sentence | Design § 5; `permissions.ts` (`get_fiscal_estimate: 'admin'`, and the standing record at line 249); `nav.ts` lines 36 to 38 |
| 14 | A module that is off | The entry, the tab, the Home block and the route are gone, the route answers with the sentence naming the module; data stays | Frame § A2; `ROUTE_MODULES`, `TAB_MODULES` in `lib/modules.ts` |
| 15 | Which reading counts | Paid over declared over estimate, by stage and never by time; a newer estimate never replaces a declared figure | Design § 2 |
| 16 | Where the dates come from | The pack, keyed by tax year, «salvo proroghe»; never a branch in a service or a component | Design § 1; frame § A5 |
| 17 | A declared figure read from a document | A proposal a person accepts, in «Proposte»; accepting is never an agent's act | Design § 2 and § 5; frame § A4 |
| 18 | Strings | Italian, about 20% longer than English, designed for the long label | Taste profile, standing rules |

## Questions for Lorenzo

Five, each a choice the sources above do not answer. Each is drawn on the artifact for the
desktop and the phone, A and B at the same scale.

| # | Question | A | B | Recommendation |
|---|---|---|---|---|
| 1 | How does a tax show its three readings? | Three columns, «Stima», «Dichiarato», «Pagato», the one that counts in bold; a total per column | One figure with a pill naming its reading; the other readings in the side panel | A. The point of the readings is to see where the accountant's figure replaced the estimate (frame § A6), which only a side-by-side shows; the empty «Dichiarato» column of a year in progress is honest, because that figure does not exist yet. |
| 2 | What does «Tasse» open on (company, ordinary solo)? | «Imposte» (the year's taxes and their readings), then «Scadenze», «Rate» | «Scadenze» (what falls due and when), then «Imposte», «Rate» | A. The Home already carries the next dates (question 5), so the page's first reading should be what the Home cannot carry; the dates are one tab away. |
| 3 | How do the calendar and the F24s relate? | No F24 tab: an F24 is what a date becomes, with its chip («da creare», «pagato · ricevuta») on the date's row | Two lists: «Scadenze» by tax line and «F24» by document, four tabs | A. An F24 is the group of the lines of one date (design § 2), so one row per date says everything a second list would, with three tabs instead of four on a 390 px screen. |
| 4 | Does the page change shape for a solo flat-rate space? | The same three tabs for every profile | A lighter page: no tabs, sections («Le tue tasse», «Scadenze», «Rate») that appear when they have rows; ordinary solo and company keep the tabs | B. The solo Home is already its own layout (REB-958) and two taxes do not need three tabs; the cost, two layouts, is contained to one profile and the readings component is the same in both. |
| 5 | Where do the next tax dates sit on the Home? | A block of their own with the money (solo), a band above the tabs (company), a card under the first figure (phone) | With the tax figures: last on the solo Home, top of «Economica» for a company, the tax card on the phone | A. A tax date is the kind of thing that asks for a person, and Lorenzo already chose a band above the tabs for what is late or due today (2026-11-02); the phone card keeps the figures-first order. |

Question 2 is drawn on the readings of question 1 option A and the date rows of question 3
option A, and question 4 on the structure of question 3 option A. If question 3 is answered B,
an «F24» tab joins option A of question 4 and a section joins its option B.

## «Tasse»

```
Subject      The page of a legal entity's taxes and contributions: for each tax of the year its
             reading (estimate, declared, paid), the calendar of what falls due and when, the
             plans of instalments, and the F24s.
Audience     The space's administrator: a freelancer who is their own administrator, or the
             owner of a company. They know what an F24 is and that the accountant's figure
             replaces PigroCRM's estimate (frame § A6); they do not know the pack. The surface
             never says «pacchetto» without the regime beside it.
The job      Know at a glance where each tax stands and what is due next. Afterwards, enter the
             accountant's figure, choose or enter a plan, group the day's lines in an F24.
Palette      Paper ground, white cards, Prussian Blue for ink and every line, Charcoal Blue for
             secondary text, Watermelon Deep for the one primary action and errors only, Royal
             Gold as the «Da pagare» dot. Tokens, never a hex.
Type         Outfit only. 24 px title, 14 px row text, 12.5 px meta, tabular numerals, amounts
             right-aligned.
Density      Middle, as the application variant says: the `Table` primitive's own rows, grown by
             the two-line cell (date over «tra 6 giorni»). No custom row height.
Signature    Every amount says which reading it is and under which law: «Stima», «Dichiarato»,
             «Pagato», and «Pacchetto fiscale it-company, versione 1, legge in vigore dal 2025».
States       See the table below.
Constraints  Italian runs about 20% longer: the long labels («Diritto annuale CCIAA»,
             «Contributi e ritenute del personale») are in the drawing. `@rebase/ui` primitives
             and tokens only. Phone first, proven by renders at 390 and 1440 px. Every amount
             is a string the server sent.
Non-goals    Filing a return or paying an F24 (design § 8); computing any figure (the server
             does); the revenue ceiling and its simulator (revenue, not tax: they stay where
             they are); the cash schedule and the balance («Cassa», C4); payroll, which C7 owns,
             beyond the F24 lines it writes; IVA settlements beyond their dates and F24 lines (C3).
```

**Desktop.** `PageHeader` with the icon, the title «Tasse», one sentence («Imposte e contributi
dell'azienda: cosa è stimato, cosa è dichiarato, cosa è già pagato.»), a select «Anno d'imposta»
and the one strong button, which follows the tab: «Inserisci il dichiarato» on «Imposte»,
«Nuovo piano» on «Rate», none on «Scadenze». Tabs as «Proposte» does. Under them the server's
warning sentence, then the tab.
- «Imposte» (the readings): one row per tax of the pack and year. Columns and the way the three
  readings sit depend on question 1. The row opens a panel of 330 px: the pack and its
  version, the law's year, the entity's fiscal year end, the advance method («Storico» or
  «Previsionale»), who typed the declared figure and when, the F24 that paid it, and the actions
  «Inserisci il dichiarato» and «Cambia il metodo dell'acconto». The forms open in a dialog, dense and
  two-column, action bar bottom right.
- «Scadenze» (the calendar): one row per date of the selected year, past ones muted, each with
  «tra 6 giorni», the lines it holds, the net amount the server gives, a state pill, and the F24
  chip (question 3). A row opens to its lines, credits as negative lines.
- «Rate»: one row per plan, chosen for a year or entered by hand for a notice from the tax
  office: total with interest, instalments paid, next instalment, state; the panel lists the
  instalments with their states.
- «F24» only if question 3 is answered B.
Where a declared figure was read from a document and waits as a proposal, a line above the table
says so and links to «Proposte»; accepting stays there.

**Phone.** The same page on the phone is for reading: the administrator checks where a tax
stands and what is due next, away from the desk. A segmented control of at most four short labels
(«Imposte», «Scadenze», «Rate», «F24») under the title and the year, one column of cards, figures
before lists. A tax opens full screen with its readings and provenance; a date opens full screen
with its lines, its F24 and a sticky «Segna come pagato». The forms of the desktop are the same in
one column with their actions in a sticky bar. Nothing is added to the centre «+» (the quick add
is for activities, notes, days and hours, frame § B6). There is no strong action on the list
screens.

| State | What is drawn |
|---|---|
| Empty | One sentence, «Qui compaiono le imposte e i contributi dell'anno, con stima, dichiarato e pagato.», and the single action that fills it: «Imposta i parametri fiscali» when the entity has no fiscal profile. No illustration. |
| Loading | `LoadingState` with «Leggo le tasse…», in the place of the table or the cards. |
| Error | `QueryErrorBanner`: what happened and «Riprova». No table under it, and no zero in its place. |
| No fiscal profile | The sentence the estimate card has today: «Profilo fiscale non configurato: senza il coefficiente di redditività e le due aliquote non c'è niente da stimare.» with the link to Impostazioni. |
| Missing parameter | The row reads «non calcolabile» and one line under the table names the parameter's place: «Alcune righe sono «non calcolabile» perché manca un parametro: imposta i parametri fiscali in Impostazioni.» A dash in a reading cell means «no reading yet», never zero. |
| Pack has no calendar | A sentence on «Scadenze»: «Il pacchetto fiscale di questo regime non porta ancora le date del 2026: qui non c'è niente da mostrare. Non vuol dire che non ci sia niente da pagare.» This is today's state of the flat-rate pack (see «Gaps»). The readings stay. |
| Regime with no taxes (`non-it`) | One sentence: «Per questo regime fiscale non si calcolano imposte: qui non c'è niente da mostrare.» No tabs. |
| Frozen year | `FiscalEstimate.frozen` is true when the entity's pack no longer carries the estimate: a line says «Cifre dell'anno com'erano: il pacchetto attuale non le calcola più.» |
| Declared figure missing | Past the date the pack gives for the return, a gold band: «Manca il dichiarato di un'imposta, oltre la data prevista dal pacchetto fiscale. Per ora vale la stima.» with «Inserisci il dichiarato». The estimate stays labelled «Stima»; it never reads as declared. |
| Declared figure waiting | A line: «Una proposta aspetta: un dichiarato letto da «dichiarazione-2025.pdf». Si accetta in Proposte.» with «Apri Proposte». |
| Advance changed by a declared figure | The difference left on the next item of that tax is shown on its row as «Da confermare» for a person; the legal treatment is «to verify» (design § 2) and the screen states no rule about it. |
| Partly paid | A tax stands as «Dichiarato» with the paid part beside it, and as «Pagato» only when the paid figure covers the declared one (inferred, see below). |
| Overdue | The item's pill is `danger`, «Scaduto», with «da 3 giorni» under the date. |
| Many | Dates are listed in time order and a year holds a few dozen rows at most; past 25 rows, «Mostra altri». In «Tutte le aziende» one block per entity, each under its name, each with its own tabs' content; no total crosses blocks. |
| Module off | The entry is gone from the sidebar and «Altro»; a stale link answers «Il modulo Tasse è spento: un amministratore può accenderlo in Impostazioni → Moduli». |
| Not an administrator | The page draws one sentence and makes no request for figures: «Il tuo ruolo in questo spazio è «collaboratore»: le tasse sono riservate all'«amministratore».» (the role word from `roleLabel`). A scoped administrator sees only the entities in their scope. |

## The Home's tax dates

```
Subject      The next two tax dates of the legal entity, with the amount of each, on the Home.
Audience     The administrator opening the product: they want to know whether something falls
             due before they look at anything else.
The job      See the next date and its amount without opening a page, and one tap to «Tasse».
Palette      As «Tasse»: white block, ink lines, `gold` for «Da pagare», `danger` for «Scaduto»,
             muted for «Previsto». Tokens only.
Type         Outfit only, as the Home's other blocks: 13 px rows, amount right-aligned in
             tabular numerals, 12 px meta under the date.
Density      Middle, a row per date with the lines named under it, at most two rows.
Signature    A date and its distance: «30/11/2026 · tra 20 giorni», the amount at the right.
States       See the table below.
Constraints  Phone first: figures lead, the dates are a card among the figures (DECISIONS
             2026-11-09, REB-956). The amount of a date is the server's net, never a sum drawn
             here. One block per profile, not per tab.
Non-goals    The ceiling and the estimate card (unchanged); a reminder (design § 4 owns its
             text, and a notification never carries an amount); settling from the Home; the
             cash schedule's own due items (C4).
```

**Desktop.** The two answers of question 5 differ only in place; the block is the same.
- *Solo.* `SoloHome` has four blocks («Da fatturare ora», «Quando arrivano i soldi», «Contratti
  attivi», the flat-rate figures). The flat-rate figures keep the ceiling panel and the estimate
  card; the dates are two rows of the next dates with «Apri Tasse».
- *Company.* The Home keeps «Da fare» and the five tabs (frame § B5). The dates are two rows
  with the date, the number of lines and the amount; with several entities each row names its
  entity and none is merged into another.
**Phone.** One column: the first figure («Da incassare»), then a card «Prossime tasse» of two
rows (date, what, amount), then the rest of the figures, then «Da fare oggi» (question 5
option A), or the same two rows inside a card «Tasse» with the estimate (option B). The card
opens «Tasse». The company's phone Home reads the same card.

| State | What is drawn |
|---|---|
| Nothing falls due | One line, «Nessuna scadenza fiscale in vista.», not a card with a zero. |
| Pack has no dates | The block is not drawn on the Home; «Tasse» says why (the Home is not where a gap in the law is explained). |
| Loading | `LoadingState` in the block's place, as `SoloHome`'s `Loading` does. |
| Error | `QueryErrorBanner` in the block's place; a failure is never an empty block. |
| Amount not known | «non calcolabile» beside the date (an ordinary solo has dates from the pack and no estimate until a declared figure is typed). |
| Overdue | The row is first and its pill is `danger`. |
| Module off | The block is not drawn and nothing is requested. |
| Not an administrator | The block is not drawn and nothing is requested: the Home is every role's start page and «Tasse» explains. |
| Several entities | In «Tutte le aziende» each row names its entity; in one entity no name appears (frame § A1: a one-entity space never sees the word). |

## What each profile sees

From design § 1 (table), § 3 and the frame § B5. The last column is the state of the code today,
which decides what the first build shows.

| Profile | «Tasse» holds | The Home shows | Today |
|---|---|---|---|
| Solo, flat-rate (`it-flat-rate`) | Substitute tax and INPS contributions, each with its readings; the dates | The ceiling distance as today, the estimate, the next two dates with amounts | The pack carries no advance rule and no dates (`pack.py` lines 235 and 236, A5 «to verify»): the first build shows the readings and the «Pack has no calendar» sentence |
| Solo, ordinary (`it-standard`) | IRPEF and INPS, the withholdings already suffered, IVA settlements (C3), the dates | The next dates (frame § B5 names the estimate only for the flat-rate pack, so the estimate stays on the page) | The pack carries the income-tax advance dates and no estimate (`pack.py` lines 257 to 290): amounts read «non calcolabile» until a declared figure is typed |
| Company (`it-company`) | IRES, IRAP, the fee to the chamber of commerce; the IVA and withholding lines in the calendar and the F24s; payroll lines in F24s | «Da fare», the next two dates, the five tabs | The pack does not exist yet (design § 1): the page waits for it |
| Foreign (`non-it`) | Nothing computed | Nothing | The sentence above |

## What a component can violate

Each rule is phrased so a reviewer can check it. The ones that depend on an answer say which.

- The reading words are «Stima», «Dichiarato» and «Pagato», each from one locale key, on every
  screen; an estimate is never drawn in a cell, pill or line named «Dichiarato».
- The reading that counts is chosen by the server by stage, never by the date or by a comparison
  in the browser. (Question 1: bold in a column in A, the pill in B.)
- No component adds, subtracts or divides an API amount: a column total, a date's net, a
  remainder and a count of days owed come as the server's strings, and the folder is under
  `no-browser-arithmetic.test.ts`. «tra 6 giorni» is a difference of two dates, as `leftOf` in
  `SoloHome` already does, not an amount.
- A figure that cannot be computed reads «non calcolabile» and a reading not yet there reads
  as a dash; neither is ever `0,00 €`.
- The server's warning sentence is drawn above the figures of «Imposte» and of the Home's tax
  card, verbatim.
- No rate, threshold, coefficient, date or code is a literal in a component. A date is the pack's
  with «salvo proroghe» said once per calendar; an F24 line's code is a label the pack supplies.
- No total, date row or chart spans two legal entities: in «Tutte le aziende» each block is one
  entity's under its name.
- Every state is a `StatusPill` whose tone comes from a `Record` beside its labels: «Previsto»
  `muted`, «Da pagare» `gold`, «Pagato» `ink`, «Scaduto» `danger`; never `accent`.
- Loading is `LoadingState` with a sentence; no `Skeleton` and no new spinner.
- A non-administrator's page and Home draw no figure, no date and no row, and issue no request
  for them; the entry stays and the page explains.
- «Tasse» never has an «Accetta» for a figure read from a document: it links to «Proposte».
- With `taxes` off the entry, the tab of the Home, the block and the route are gone and the route
  guard answers with the module's sentence; `ROUTE_MODULES` gains `/app/taxes`.
- The phone has no horizontal table; its segmented control holds at most four labels that fit
  390 px («Rate», not «Rateizzazioni»). (Question 3: four labels only in B.)
- Question 4: in B the flat-rate page has no tabs and a section appears only when it has rows;
  ordinary solo and company keep the tabs in both answers.
- Question 5: in A the dates block is above the tabs for a company and in B it is inside
  «Economica» only.

## Inferred, not asked

These follow from the design and the code and were not shown to Lorenzo; a reviewer should push
back on any.

- The icon is `Landmark` of lucide, the route `/app/taxes`, the module `taxes` (`lib/modules.ts`
  already labels it «Tasse»), and the entry is last in «Amministrazione», after «Giornate».
  «Cassa» (C4), the purchases entry (C3) and «Persone dell'azienda» (C7) belong to their own
  briefs and their order beside «Tasse» is decided there.
- The page's year is the **tax year**, defaulting to the current one. «Imposte» reads that year;
  «Scadenze» lists the dates that fall in that calendar year, each naming the tax year it is for
  («Saldo 2025», «Secondo acconto 2026»), because the June date serves two tax years. The design
  keys the pack by tax year and says nothing of the page's year.
- The words of an item's state: «Previsto» for `forecast`, «Da pagare» for `expected`, «Pagato»
  for `settled`, «Scaduto» for an `expected` item past its date, «Non più dovuto» for `dropped`.
  They must match the cash schedule's words (C4's brief).
- A tax stands as «Pagato» only when the paid figure covers the declared one; a partly paid tax
  stands as «Dichiarato» with the paid part beside it. The design says only that paid ranks over
  declared (see «Gaps»).
- The one strong button follows the tab. The forms open in a dialog from the panel, not inside the
  330 px panel, which is too narrow for two columns.
- «Segna come pagato» is the sticky action of a date's full screen on the phone: marking a due
  item paid by hand stays possible (due items § 3) and is an administrator's (`settle_outgoing_due_item`).
- The Home block shows at most two dates for any profile with `taxes` on and an administrator
  reading, including the ordinary solo (frame § B5 names only the flat-rate's estimate); it draws
  nothing for a role that may not read or a pack with no dates, where the estimate card
  explains itself today. The Home is every role's start page, so a daily sentence there would be
  noise.
- The existing cards stay as they are: the ceiling panel and simulator, the «Anno» tab (the year
  register of invoices) and the «Stima fiscale» card. They read the same figures and use the same
  reading words; moving them is a later card.
- The loading sentence is «Leggo le tasse…».
- The proposal line on «Imposte» and the «Da confermare» mark on a changed advance are new
  wording, not in the design.
- «Rate» is the tab's label, short for 390 px; the table's heading says «Piani di rate».

## Gaps found in the design and the code

Reported, not papered over.

- **The flat-rate Home has no dates to show today.** Design § 3 says the flat-rate Home carries
  «the next two tax dates with their amounts», while the frame's register lists the flat-rate
  advances (dates, split, thresholds) and the day of the month of income-tax dates as «to verify»
  and `fiscal/pack.py` lines 235 and 236 say the flat-rate pack carries no advance rule. The first
  build shows the «Pack has no calendar» state on that profile.
- **The ordinary pack has dates and no estimate.** `IT_STANDARD_PACK` (`pack.py` lines 257 to 290)
  carries the advance dates and no income estimate (IRPEF brackets «to verify»), so an ordinary
  solo sees dates with «non calcolabile» amounts until a declared figure exists.
- **IVA and withholdings have no reading.** Design § 2 gives every tax three readings, but § 3
  puts IVA and withholdings on the page and the frame (§ A6) says IVA is exact from the registers
  and C3 writes their due items directly. This brief shows them in the calendar and the F24s and
  not in the readings table; the design should say which reading word an IVA settlement carries.
- **A partly paid tax.** Design § 2 ranks paid over declared and does not say what stands when
  only part is paid.
- **Who sees payroll on the Home.** Design § 5 says every read of a tax due item, «the schedule
  and the Home» included, is for administrators and token holders with `full_access`; the staff
  design (§ 4, `2026-11-09-staff-payroll-and-expenses-design.md`) says other roles see one
  aggregate «Stipendi» line per date in the schedule and the Home. This brief follows § 5 for tax
  dates and leaves the aggregate to C4's and C7's briefs.
- **The `taxes` module also owns the ceiling.** Frame § A2 puts the ceiling panel and simulator
  in `taxes`, while design § 5 calls the ceiling revenue, open to every role. Switching `taxes`
  off hides the ceiling with the taxes; the solo Home's ceiling goes with it.
- **«The calendar» and «the F24s» overlap** (design § 6 against § 2): question 3.

## Not in this brief

- «Cassa» and the cash schedule, which list tax due items too and are cross-linked and never
  summed with this page (C4's own brief).
- The «Calendario» page: tax dates are not added to it; they are administrator-only reads and the
  calendar is every role's.
- Reminders (`tax_payment_due`, the missing-figure reminder) and their text (design § 4).
- Payroll, staff, IVA settlement and purchases screens (C3, C7).
- Any markup, route or component, and the DECISIONS rows, which follow Lorenzo's answers.
