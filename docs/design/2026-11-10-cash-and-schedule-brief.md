# Brief: «Cassa» and «Scadenziario» (PigroCRM)

Status: approved by Lorenzo on 2026-11-10, on the artifact `pigrocrm-cash-and-schedule`
(«possiamo andare con i tuoi consigli per tutto»: option A on all six questions, the
recommended ones). Card: REB-1000. Design:
`projects/pigrocrm/docs/superpowers/specs/2026-11-09-due-items-bank-and-cash-schedule-design.md`
§ 4 (the cash schedule), § 6 (surfaces) and what they cite (§ 1 due items, § 2 bank
accounts and statements, § 3 matching, § 5 permissions); frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` § A3 (the due item
states), § A5 (fiscal rules are cited or marked), § A7 (every new surface passes this gate)
and § B5, § B6 (Home per profile, the phone shell). The rule this brief creates is the last row of
`docs/design/DECISIONS.md` dated 2026-11-10; the build cards read this file and that row,
never the artifact, which is closed once the answers are recorded.

Everything here is drawn on `shared/ui/tokens.css` and the primitives of `@rebase/ui`:
squared, 1px ink lines, Outfit, Prussian sidebar, Watermelon Deep only on the primary action
and on errors (the application variant, DECISIONS 2026-09-18). It uses four of the five
`StatusPill` tones, `ink`, `muted`, `gold` and `danger`, and never `accent`. One light
scheme. Strings are Italian, amounts `1.464,00 €`, dates `02/11/2026`, tabular numerals in
every column of figures, no emoji, no illustration. A wait is `LoadingState` (the `Loader`
plus a status sentence), never a skeleton (DECISIONS 2026-11-09 and 2026-11-10, REB-973 and
REB-981). Every name, amount, date and IBAN in the mockups is invented example data; the
fiscal amounts and dates in them are examples only, and no screen computes a fiscal figure
(frame § A5 marks most of the tax calendar «to verify»): the screens show what the server and
the fiscal pack return.

## Settled before asking

A question already answered is a defect to ask again. These were not in the questions.

| Point | Settled as | Source |
|---|---|---|
| Density | Airy to read, dense to fill. The tables use the `Table` primitive's own rows (48 px), the entry forms are dense two-column with the action bar bottom right. | Taste profile axes 1 and 6; application variant § 1 and § 3; `2026-11-09-inbox-and-mailboxes-brief.md` (Density lines) |
| Lists | A gridded, ruled table: 2 px rule under the header, figures right-aligned in tabular numerals. | Taste axis 5; application variant § 3 «Tables» |
| Desktop list plus detail | A table with the open record in a side panel (about 300 px) from 1024 px, a full-screen sheet below. | DECISIONS 2026-11-09 (REB-973, row 1); `features/inbox/InboxPage.tsx` (`INLINE_PANEL`) |
| Phone | One column of cards, figures before lists, a five-place bottom bar with the centre «+». «Cassa» is not one of the five, so it sits in «Altro» (the places `visibleSections` lists that the bar does not draw). | DECISIONS 2026-11-09 (REB-956); `components/nav.ts:105-113` |
| Status | A pill carries colour and a word, in one of four tones; the tone of a state lives in a `Record` beside its labels. | Taste profile 2026-10-21; `components/StatusPill.tsx` |
| Loading, empty, error | `LoadingState` and a `role="status"` sentence in the place of the content; empty is one sentence and the one action that fills it; an error is `QueryErrorBanner` with nothing under it. | DECISIONS 2026-11-10 (REB-981); taste axis 7; `components/QueryErrorBanner.tsx` |
| One schedule, two doors | The tab «Scadenziario» and «Cassa» show the same view, from one component. | Design § 4, § 6 |
| Legal entity filter | The sidebar's selector («Tutte le aziende» or one azienda, from the second entity on) filters the schedule, as it filters every dashboard; under «Tutte» a row names its entity. The page has no second filter. | Design § 4 (frame § A1); `lib/legalEntity.tsx` |
| Forecast and estimated | Drawn lighter than expected, and also named by a word. | Design § 4; taste 2026-10-21 (colour never alone) |
| Receivables buckets | The six ageing bands stay for receivables («Scaduto», «Entro 30 giorni», «Da 31 a 60 giorni», «Da 61 a 90 giorni», «Oltre 90 giorni», «Senza scadenza») and get their mirror for payables. | Design § 4; `dashboard/service.py:109-113`, `invoices/receivables.py:77-84` |
| Solo profile | Today's balance, money in and its own taxes; no projected balance and no outgoing column. | Design § 4; frame § A3 |
| Modules | `cash` owns accounts, statements, movements, matching, «Cassa» and the projected balance. With `cash` off the tab «Scadenziario» stays (it is core, as `TAB_MODULES` says) with due items in both directions and the overdue reminders, and shows no balance. A source kind appears only when its module exists and is on. | Design «Scope»; frame § A2; `lib/modules.ts:80-84` |
| Figures | Every figure, total, difference and share is the server's string; the browser adds, subtracts and compares nothing. | `features/dashboard/charts.tsx:23`; `test/no-browser-arithmetic.test.ts`; `lib/no-float-money.test.ts` |
| One strong action | One button, top right of the page header. A role that may not press a control never sees it. | `components/PageHeader.tsx`; `lib/permissions.ts` (REB-294) |
| Payments by hand | Marking a due item paid stays possible; a client's payment is a collaborator's, money going out and every tax, IVA, withholding or payroll item an administrator's. | Design § 3, § 5; DECISIONS 2026-11-09 (REB-967); `lib/permissions.ts:104-106` |
| The accounts | Listing accounts, statements and movements is an administrator's (the balance discloses the same fact). | Design § 5 |
| The month lock | Does not apply to a settlement. | Design § 3 |
| Cross-link, never sum | «Cassa» and the economic tab link to each other and are never added together; one month can read differently on them by design. | Design § 4 |
| Freshness | The schedule keeps the `Freshness` line («Aggiornato alle 11:42», «Aggiorna») the receivables tab has. | `features/dashboard/ReceivablesTab.tsx:73` |

## What Lorenzo decided

Lorenzo, 2026-11-10: «possiamo andare con i tuoi consigli per tutto», that is option A on
every question, as recommended on the artifact. The options B are not built.

| # | Question | Decision |
|---|---|---|
| 1 | How is the week-by-week schedule drawn? | A weekly table; the items of the selected week open in the side panel. On the phone, one card per week. |
| 2 | Does the balance at the end of each week include the forecasts? | One balance with the forecasts in; the forecast part of each flow is drawn lighter above it. No «Saldo certo» column. |
| 3 | What does the projected balance do with money already overdue? | It leaves it out. Two figures above the weeks, «In ritardo da incassare» and «In ritardo da pagare», and a note under the table name what it does not count. |
| 4 | How does a solo space see its own taxes? | A small table «Le tue tasse» under the weeks, each tax with date, state and amount, marked «non tolte dal saldo». |
| 5 | Where is the work on bank movements done? | On the account: one queue of movements across statements, under the tabs «Movimenti» and «Estratti conto». There is no statement page. |
| 6 | What do the money figures at the top of the phone Home lead with? | Four figures of now: «Saldo oggi», «Da incassare» and «Da pagare» (each with its late part), «Prossimi 7 giorni». |

Two points to read with the decisions:

- Decision 3 is a rule the server follows (what the projected balance contains), not only a
  drawing: the schedule's read returns the two late figures apart and a projected balance
  that does not contain late items.
- Decisions 2 and 3 were drawn on the weeks table of decision 1; they hold there.

## «Cassa» and the tab «Scadenziario»

```
Subject      The cash schedule: when money comes in and goes out, week by week, with
             today's balance per account, what is late both ways, and the balance each
             week ends on. One view under «Cassa» (sidebar, «Amministrazione») and under
             the Home tab «Scadenziario».
Audience     The person who has to know when to pay whom and when to collect from whom
             (Lorenzo's words: «una ui comoda per capire quando pagare chi e quando
             incassare da chi»): an admin in a company space; a freelancer in a solo
             space who wants to know when money arrives. They know an invoice and a
             bank account, not due rules or reader formats.
The job      See at once whether there is a gap in the next 13 weeks and what is late;
             open a week to see what is in it; mark an item paid. A statement that is
             missing or a balance gone stale is said on the page, not discovered.
Palette      Paper ground, white cards, Prussian Blue for ink and lines, Charcoal Blue for
             secondary text and for the forecast, Royal Gold as the «waiting» dot,
             Watermelon only on the primary action and on the red of a balance below
             zero and a late item. Chart bars use `--chart-2` (in) and `--chart-4` (out),
             never the Watermelon tint, so that direction is not read as good or bad.
Type         Outfit only. 24 px title, 14 px row text, 12.5 px meta, tabular numerals,
             figures right-aligned; the balance of the page one step up (as `BigNumber`).
Density      Middle (48 px rows) for the weeks and the items, airy for the balance cards.
Signature    Forecast against expected inside one figure: the expected amount in ink, the
             forecast under it in Charcoal italics with a hatched swatch and the word
             «previsti». A date that follows another item reads «circa» and carries the
             sentence «in attesa dell'incasso» or «in attesa del pagamento» (design § 1.3).
States       See the table below.
Constraints  Italian runs about 20% longer. Phone first (390 px). No browser arithmetic.
             The 13-week horizon is a default, not a constant in a component. Each source
             kind (design § 1) appears only when its module is on.
Non-goals    Paying from PigroCRM; banking APIs; the scenario switch for deals (its own
             card, design «Cards»); drawing a fiscal rule; the economic tab (cross-linked,
             not redrawn); the accountant's export.
```

**Desktop.** The page header carries the icon, «Cassa», one sentence and the one strong
button «Carica estratto conto» (an administrator's, see Permissions). Under the header, from
the top: a band, only when something needs a person, one line, the worst first, «e 1 altra»
when there are more («Il saldo previsto scende sotto zero nella settimana dal 30/11 al
06/12/2026», «Manca l'estratto conto di ottobre per Conto operativo»), with no close control,
as the mailbox band of REB-973 has none. Then the balances: a total card «Saldo oggi, tutte
le aziende» and one card per account (name, bank, entity under «Tutte», the amount in the
account's own currency, and the date it is true as of: «al 30/09/2026», with a gold dot and
«da 41 giorni» once it is older than the account's cadence says). Then the horizon select
(13 weeks by default), the legend of expected and forecast, and the weeks (a table, decision 1), with
the `Freshness` line. Below, as today and still on the same page: «Da incassare» and «Da
pagare», each a big number, the line «Di cui scaduto», the six bands and its table of late
items (the receivables one keeps its «Solleciti» column; the payables one has none), side
by side from 1024 px and stacked below it. Above the weeks, two figures, «In ritardo da incassare» and «In ritardo da pagare» (count, total,
how old the oldest is), and under the table «Il saldo non conta ciò che è già scaduto» with the
two totals and a link to the late items (decision 3). A line says how «Cassa» and the economic tab differ and
links to the tab «Economica». The tab is the same body under the Home header: the Home keeps
its title and its tabs, and the horizon and the period picker stay as they are today (none on
this tab).

**Phone.** Under «Altro», first the band, then the figures: a card with «Saldo oggi» and its
accounts (each with its date), then the weeks as one card each (decision 1), then «Da
incassare» and «Da pagare» as cards. The strong action is the same, as a sticky button on the
account page rather than here: on the phone a statement is added through «Carica un
documento» in the centre «+» (REB-973 classifies it as a bank statement, § 2 of the incoming
documents design, and it waits in «In arrivo») or from an account page. Tapping a week opens its items full screen with a sticky
«Segna come pagato» or «Segna come incassato» on an open item.

**Items.** An item row says who and what in two lines (counterparty over «Fattura 2026/44»),
its date, its state pill and its amount in the «Entrate» or «Uscite» column, with the entity
name under it on «Tutte». The panel adds the source and the actions: «Segna come incassato»
or «Segna come pagato», «Apri la fattura» (the document, when the source has a page). A
sales item's date never moves (design § 1.1), so the panel offers no way to change it.

| State | What is drawn |
|---|---|
| Empty (module on, no account) | The schedule of invoices as it is; where the balances would be: «Nessun conto collegato. Aggiungi un conto per vedere il saldo di oggi e quello delle prossime settimane. Le scadenze delle fatture si vedono già.» and «Aggiungi un conto» (an administrator's button). No illustration. |
| Empty (no open items) | «Niente da incassare né da pagare nelle prossime 13 settimane.» and no action: nothing fills it from here. |
| Loading | `LoadingState` («Leggo le scadenze…») in the place of the balances and of the weeks, one wait for the page. |
| Error | `QueryErrorBanner` and nothing under it, so no total is shown that never arrived. A balances read that fails while the schedule loads shows the error in the balances place only. |
| Forecast, estimated | The lighter figure and the word «previsti»; the pill «Previsto» (`muted`); a date that follows another item reads «circa» and the sentence of the rule. |
| Below zero | The balance in `danger` with its sign and the words «sotto zero» (not colour alone); the band names the first such week. A floor the space sets reads the same. |
| Stale balance | The account card says «al 30/09/2026, da 41 giorni» with the gold dot, and the missing-statement band follows. The projected balance is still shown, from the stale figure, with the account named in the band. |
| Overdue | The `danger` pill «Scaduto da 12 giorni» on the item, the first band of «Da incassare» or «Da pagare», and, in the schedule, the two late figures above the weeks and the note under them (decision 3). |
| Many | The weeks are 13 rows and never page; the items of a week open in the panel, up to 50 with «Mostra altri». A long counterparty wraps in the panel and ellipsises in a cell with the full name on hover. Many accounts: four cards, then «Tutti i conti (7)». |
| Several entities | The selector in the sidebar filters; under «Tutte le aziende» the account cards name their entity and the items a muted line; with one entity neither exists. An account in another currency is shown in it («1.350,00 USD») and the total says it is converted. |
| Module `cash` off | No «Cassa» in the sidebar or in «Altro», no balances, no band about statements, no projected balance; the tab «Scadenziario» shows the due items in both directions, the two bands and the late lists. |
| Solo | No «Uscite» column, no balance by week, no «Saldo previsto»; «Saldo oggi» for the accounts, the weeks with «Entrate» only (expected and forecast), the late receivables, and the table «Le tue tasse» (decision 4). |
| Permissions | Admin: everything. Collaborator and read-only: the items of sales invoices, purchases and recurring expenses, and no balance, no account card and no projected balance (§ 5 makes the accounts an administrator's); the tax, IVA, withholding and payroll kinds are not in their list and not in their totals. Read-only sees no button. A balance the viewer may not read is a missing block, never «0,00 €». |

## The Home's money figures on the phone

```
Subject      The first figures of the phone Home: how the money stands today, read from
             due items.
Audience     Lorenzo on a phone, between two things: he checks figures and deals and works
             his activities there (REB-956).
The job      Answer «how do we stand today» in one look, then reach «Cassa» in one tap.
Palette      As the page above; the band is the same one-line band.
Type         Outfit; a figure one step up (22 px, 26 px for the balance), labels 12.5 px.
Density      Airy: cards of about 56 px, two columns for the two paired figures.
Signature    The late part named under each owed figure («di cui scaduto 3.660,00 €»).
States       Loading, error, solo, module off, role (table below).
Constraints  Figures before lists (DECISIONS 2026-11-09). 390 px: a paired figure must not
             overflow its card. The rest of the Home is not redrawn.
Non-goals    The company Home's other tabs, the onboarding and the solo Home beyond its
             money block, which the REB-956 decision left as drawn.
```

**Phone** (decision 6). The band when it applies; «Saldo oggi»,
large; two cards side by side, «Da incassare» and «Da pagare», each with its late part;
«Prossimi 7 giorni» with «entrano» over «escono»; then today's activities, as REB-956
decided. A tap on any figure opens «Cassa» (the weeks), or the invoices list for the late
part (`/app/invoices?overdue=true` already exists). A solo space has «Saldo oggi», «Da
incassare», «Di cui scaduto» and «Prossimi 7 giorni» with «entrano» only. **Desktop.** The
Home does not change shape: its figures are the tab «Scadenziario» above.

| State | What is drawn |
|---|---|
| Loading | `LoadingState` in the place of the figures, the activities below it unaffected. |
| Error | `QueryErrorBanner` in the place of the figures. |
| No account or `cash` off | No «Saldo oggi» and no band; «Da incassare» and «Da pagare» remain (core). |
| Solo | No «Da pagare», no «escono», no projected figure. |
| Role | A collaborator or read-only person has no «Saldo oggi»; the owed figures leave out the restricted kinds. |
| Late | The `danger` text of the late part carries its words, not only its colour. |

## The account page

```
Subject      One bank account: its balance and the date it is true as of, how its
             statements arrive, whether they all arrived, and what was read from them.
Audience     An administrator who keeps the books in step with the bank, and the
             accountant's questions behind them.
The job      Know that the account is complete (a statement for every month it should
             have), upload the one that is missing, and clear what is unmatched.
Palette      As above; a missing month is `danger`, a coverage month that is in is ink.
Type         Outfit; the IBAN in tabular numerals in groups of four, as printed.
Density      Middle for the tables, dense two-column for the account form.
Signature    The coverage strip: the last 12 months as 12 cells, a missing one in dashed
             `danger` with the word «manca».
States       See the table below.
Constraints  Reached from the balance card on «Cassa», there is no list page of accounts
             (inferred). The IBAN is an administrator's to read. The statement formats
             and the bank names the readers cover are not fixed here (frame § A5: «to
             verify» for each bank); the screen shows the format name the reader returns.
Non-goals    Banking APIs and their credential flow; reading a statement; the counterparty
             IBANs of organisations (their own page, § 2).
```

**Desktop.** The page header carries the account name, the line «Cassa · Banco BPM · Alfa
Servizi srl», «Modifica il conto» and the one strong button «Carica estratto conto». Three
cards: the balance with its date, the account (bank, entity, IBAN) and how statements arrive
(the mailbox of purpose `statements` or a manual upload, the cadence, the sender
addresses). Then the coverage strip. Then the tabs «Movimenti» (the default, filtered to «Da abbinare»)
and «Estratti conto» (decision 5); a statement row filters «Movimenti» to that statement and
has no page of its own. «Modifica il conto» and
a new account use a dense two-column dialog: name, bank, IBAN, currency, opening balance and
its date, how statements arrive, the cadence, the senders; «Archivia il conto» in the same
dialog, with the sentence «I movimenti restano». «Carica estratto conto» is a dialog with the
account, the file and, when the file does not state it, the covered period («Il file non
dichiara il periodo: va indicato, non si indovina»), and afterwards «41 movimenti, 2 già
presenti».

**Phone.** One column: the balance card with its date, the coverage strip (12 thin cells),
then the movements as cards under a segmented control («Da abbinare / Tutti /
Estratti»). Upload is the sticky button of the page.

| State | What is drawn |
|---|---|
| Empty | A new account with no statement: the balance reads the opening balance with its date, the strip is empty and the one sentence is «Carica il primo estratto conto per vedere i movimenti.» with «Carica estratto conto». |
| Loading, error | `LoadingState` («Leggo il conto…»); `QueryErrorBanner` alone. |
| Missing statement | The month in the strip dashed `danger`; in the statements table a row «Ottobre 2026» with the pill «Manca» and «Carica». It appears once the cadence's period has ended plus the grace the design sets (7 days by default, read from the server). |
| Statement checks | A statement with a gap or a reconcile difference says it on its row or page: «Il saldo dichiarato non torna: differenza 50,00 €» (the difference is the server's). |
| Proposed statement (PDF) | The pill «Da confermare» (`gold`), the computed totals beside the stated ones, and «Conferma» (a person's act, never an agent's; design § 2). |
| Reverted | The pill «Annullato» (`muted`); «Annulla l'importazione» is offered on an imported statement and says «Rimuove i suoi movimenti e gli abbinamenti automatici». |
| Archived | Not listed on «Cassa»; reachable from «Impostazioni» (inferred) with its rows kept. |
| Many | Statements and movements 50 at a time with «Mostra altri». The strip stays 12 months; older months are the statements table. |
| Permissions | Admin only. Anyone else gets no account page and no card that links to one. |

## A statement's movements

```
Subject      The lines of a statement (read as a queue across the account's statements, decision 5): date,
             description, counterparty, amount, and what each is matched to.
Audience     As the account page.
The job      Get every movement to a state: matched, a giroconto, ignored; never left
             unmatched past the days the design gives.
Palette      As above; the pills: `ink` «Abbinato», `gold` «Da abbinare», `danger` «Da
             abbinare da N giorni» after the threshold, `muted` «Giroconto» and
             «Ignorato».
Type         Outfit; amounts signed, right-aligned, tabular; the bank's description
             verbatim (it is free text written by others, shown as written).
Density      Middle; a two-line description cell (text over counterparty and what is
             proposed).
Signature    The line under the description that says what is proposed or why it was
             matched: «2 voci aperte tornano: Fattura 2026/35 e Fattura 2026/36»,
             «in automatico: numero fattura nella causale».
States       See the table below.
Constraints  An automatic match is shown with its reason and can be undone.
Non-goals    Editing the bank's data; reading formats.
```

**Desktop.** A table with the matching panel beside it (below). Filter chips «Da abbinare»
(with its count) and «Tutti», and a select of the statement when the list spans several.
Columns: «Data», «Descrizione» (the bank's text, with the counterparty and the proposal
under it), «Importo», «Stato». **Phone.** One card per movement, the amount on the first
line, the date and the state under it; a card opens the matching sheet.

| State | What is drawn |
|---|---|
| Empty | «Nessun movimento da abbinare.» when the filter is «Da abbinare»; a statement with no lines says so. |
| Loading, error | `LoadingState` («Leggo i movimenti…»); `QueryErrorBanner` alone. |
| Unmatched, young | `gold` «Da abbinare». |
| Unmatched, old | `danger` «Da abbinare da 42 giorni»; the threshold (default 14 days, design § 3) is the server's flag, not a constant of the component. |
| Matched | `ink` «Abbinato» and the document it settled; an automatic one says why and offers «Annulla l'abbinamento». |
| Giroconto | A proposed one is `gold` «Da abbinare» with «giroconto proposto: +2.000,00 € sul Conto Qonto il 26/09»; a confirmed one is `muted` «Giroconto». |
| Ignored | `muted` «Ignorato» and the reason, kept on the row («uso personale»). |
| Other currency | A movement in a currency other than the document's is never matched automatically; it is proposed (design § 2), and the row says so. |
| Many | 50 at a time. Two identical same-day payments are two rows (design § 2). |
| Permissions | Admin. A collaborator never reaches these rows (see «Open points»). |

## The matching panel

```
Subject      One movement and the open items it could settle.
Audience     An administrator who knows the supplier or the customer by name.
The job      Pick the right items, or say what the movement is, in as few taps as the
             case allows: one proposal accepted with one press.
Palette      As above; reasons as outlined chips, never colour.
Type         Outfit; the amount of the movement large.
Density      Dense: the panel is the one dense block, as «Cosa è stato letto» is in
             «In arrivo».
Signature    The best proposal first, with the reasons it was ranked first as chips
             («importo totale coincide», «numeri nella causale», «IBAN noto») and a
             line that says there is no difference left, from the server.
States       See the table below.
Constraints  A known counterparty IBAN only ranks proposals; nothing settles on it
             alone (design § 3). A statement received by mail settles nothing
             automatically until a person has confirmed the first one from that sender
             (design § 2); the panel says so on such a movement.
Non-goals    Creating a due item by hand (design § 1: the writers own that); editing a
             document; the match learned for next time beyond the counterparty IBAN the
             design describes.
```

**Desktop.** A 280 to 330 px panel beside the table, as «In arrivo». It shows the movement
(amount, date, counterparty, the bank's text), then «La proposta migliore: 2 voci» with a
checked row per item (document, state, amount) and the reasons as chips, then «Selezionato
3.660,00 € di 3.660,00 €: nessuna differenza» (the sums are the server's). The primary
button is «Abbina»; «Cerca un'altra voce» opens a search of the open items of the same
entity and direction. Under a rule, «Altro»: «Giroconto…», «Ignora…» (asks the reason),
«Nuova spesa» (for a movement with no document, a bank fee: a purchase born settled by the
movement, refused in a closed month with the same message a cost gets, design § 3).
**Phone.** A full-screen sheet with the same blocks in one column and a sticky bar with
«Abbina» and «Scegli altre voci».

| State | What is drawn |
|---|---|
| One exact proposal | The panel opens with it checked and «Abbina» ready; if it is certain enough to have settled by itself it is not here but «Abbinato», with «Annulla l'abbinamento». |
| Several candidates | Up to three ranked, the best checked, the others unchecked; «Cerca un'altra voce» beyond them. |
| A split | Checked rows add up to the movement and the panel says «nessuna differenza»; a short or long selection says the difference and keeps «Abbina» disabled until it is zero or a person confirms a partial payment (the partial state is the item's own, design § 1). |
| No candidate | «Nessuna voce aperta corrisponde.» with «Cerca un'altra voce» and the three «Altro» actions. |
| Loading, error | `LoadingState` («Cerco le voci aperte…») in the place of the proposals; `QueryErrorBanner` and the actions unavailable. |
| A closed month | «Nuova spesa» refuses with the cost message and the panel stays. |
| Unconfirmed sender | A line: «Questo estratto arriva da un mittente che non hai ancora confermato: niente si abbina da solo.» |
| Permissions | Admin only; settling an outgoing item is an administrator's act (`settle_outgoing_due_item`). |

## What a component can violate

The rules the build must keep, each checkable; those that follow from a decision name it.

- The schedule is one component mounted by the tab «Scadenziario» and by «Cassa»; the Home
  tab id stays `receivables` (bookmarks) and only its content changes.
- The browser sums nothing: every balance, total, difference, «nessuna differenza», share
  and count is read from the API; a test with the solo and company fixtures asserts no
  arithmetic in the schedule's folder (`no-browser-arithmetic`).
- Forecast and expected never share a look: any figure or row that comes from a forecast, and
  any date that follows another item, carries the lighter treatment and a word, in the table,
  the panel, the phone card and the Home figure.
- There is one projected balance per week, forecasts included (decision 2): no «Saldo certo»
  column, on the table or on the phone card. A balance below zero is `danger` with its sign
  and the words «sotto zero».
- The projected balance contains no overdue item, and the two late figures are drawn above the
  weeks with the note under them (decision 3); no «Già scaduto» row is drawn.
- The weeks are a table with the selected week's items in the panel, and the strip of 13
  columns is not drawn (decision 1).
- The solo profile has no «Uscite» column, no balance by week, no «Saldo previsto» and no
  outgoing figure on the phone Home, with a solo fixture test; its taxes are the
  table «Le tue tasse» (decision 4), marked «non tolte dal saldo», and are never subtracted from
  a balance.
- A balance the viewer may not read is absent, never zero or a dash; no card, link or count
  leads a non-admin to an account, a statement or a movement.
- Every balance names the date it is true as of; one older than the account's cadence says
  so in words and with the `gold` dot.
- The status of an item, a statement and a movement is a `Record` to a tone beside its
  labels: items «Da incassare» and «Da pagare» `gold`, «Previsto» `muted`, «Scaduto» `danger`;
  statements «Importato» `ink`, «Da confermare» `gold`, «Annullato» `muted`, «Manca»
  `danger`; movements as above. No `accent`.
- A loading state is `LoadingState` and a sentence, an error is `QueryErrorBanner` and
  nothing under it; no `Skeleton`.
- Under one entity there is no entity column, name or selector.
- The movements are one queue per account across its statements; a statement has no page of
  its own and a movement present in two statements is one row (decision 5).
- The phone Home leads with the four figures of decision 6, each tappable to «Cassa», and no
  ledger.
- An automatic match shows its reason and is undoable; a movement from a statement not yet
  confirmed is never shown as matched automatically; a known IBAN alone never checks a row.
- The «da N giorni» turning `danger`, the 7-day grace of a missing statement and the horizon
  are the server's values.
- A statement read from a PDF has «Conferma» for a person only, with the computed totals
  beside the stated ones.
- The phone has no hover-only control; «Segna come pagato» and «Abbina» are visible buttons,
  and a paired figure card never overflows at 390 px.
- A source kind of a module that is off or not built is absent from the schedule, the
  totals and the legend.

## Inferred, not asked

These follow from the design and the code and were not shown to Lorenzo; a reviewer should
push back on any.

- «Cassa» is the last entry of «Amministrazione» (after «Giornate», the order the money
  moves: register, hours, days, bank), with its own icon, and in «Altro» it follows «In
  arrivo» and the other entries in section order. Its route is `/app/cash`, the account
  `/app/cash/accounts/$accountId` and the movements and statements are
  tabs of the account page (decision 5), so there is no statement route.
- The tab id `receivables` and its label «Scadenziario» stay; the tab keeps the six bands and
  the late-invoice table of today and gains the payables mirror and the weeks.
- The weeks start on Monday, the current week is whole (today and the days after it count,
  earlier days are overdue), a week is labelled «09/11 al 15/11», and the horizon select
  offers 4, 13 and 26 weeks. These are the server's reading; the screen draws what returns.
- An item state shown to a person: an expected receivable is «Da incassare» (`gold`, as
  `PAYMENT_STATE_TONE` says `unpaid` is), an expected payable «Da pagare» (`gold`), a
  forecast «Previsto» (`muted`), an overdue one «Scaduto da N giorni» (`danger`), a partly
  paid one is `gold` with «Incassati 1.000,00 €, restano 464,00 €».
- The one band at the head of the schedule: worst first (below zero, then a missing
  statement, then a stale balance), «e N altre», no close control.
- «Carica estratto conto» is an administrator's button, although design § 5 lists
  `import_statement` as a collaborator's act: the account list it needs is an
  administrator's read (see below).
- The panel's «Apri la fattura» and the supplier invoice's page exist only where the source
  has a page; a forecast has none.
- The words the design does not name: «Saldo oggi», «Saldo a fine settimana», «Saldo
  certo», «Saldo con previsioni», «Già scaduto», «In ritardo da incassare», «In ritardo da
  pagare», «Le tue tasse», «Prossimi 7 giorni», «Giroconto», «Ignora…», «Nuova spesa»,
  «Annulla l'importazione», «Annulla l'abbinamento», «Carica estratto conto» and the
  sentences in the state tables.
- The stale-balance wording and the coverage strip's 12 months; the account cards on «Cassa»
  as the only list of accounts (an account is created from the empty state or from a link on
  the balances); archiving from the account's dialog.
- The Home tab keeps its period picker hidden on this tab, as today.
- The phone's «Altro» reaches «Cassa»; tapping a Home figure opens it. No new place in the
  bottom bar and no new entry in the «+».

## Open points

Gaps and contradictions found in the design and the code while drawing this, and what is
unresolved. The page cards inherit them; none is papered over. Paths are in the repository.

1. **Overdue money in the projected balance.** The design said «overdue both ways» and a
   projected balance per week (`projects/pigrocrm/docs/superpowers/specs/2026-11-09-due-items-bank-and-cash-schedule-design.md`
   lines 293-298) and not whether late money is in the balance. Decided (decision 3): left out
   and named. The server read has to return the two late figures apart; the design text of §
   4 should say it.
2. **The solo's taxes.** Design § 4 lines 302-304 give the solo profile «its own taxes» and
   «no outgoing column»; a tax is an outgoing item. Decided (decision 4): a separate table,
   not subtracted. The read needs the solo's own tax items, which are restricted kinds
   (`due_items/models.py`, `RESTRICTED_SOURCE_KINDS`), so a solo space with one administrator
   sees them and a solo collaborator would not.
3. **Who may see what.** Design § 5 lines 317-327 make `list_bank_accounts`,
   `list_statements` and `list_bank_movements` administrator reads, but `import_statement`,
   `match_movement`, `unmatch_movement` and `mark_movement` collaborator acts; a collaborator
   who cannot list the accounts cannot pick one. `match_movement` on an outgoing item would
   also settle it, which `settle_outgoing_due_item` makes an administrator's act
   (`apps/web/src/lib/permissions.ts:104-106`, DECISIONS 2026-11-09 row on REB-967). The
   design does not say whether `get_cash_schedule` returns the balance to a non-admin. The
   brief draws every balance, account, statement, movement and matching screen for an
   administrator only. **Open:** if collaborators are to import and match, the account read
   and the outgoing-settlement rule have to change first.
4. **The `cash` module and the tab.** Frame § A2 (line 105 of the frame) lists «the cash
   schedule» under `cash`; frame § B5 (lines 377-386), `apps/web/src/lib/modules.ts:80-84`
   (`TAB_MODULES`) and design «Scope» keep the tab «Scadenziario» in the core. The brief
   follows the second reading: with `cash` off the tab shows due items without balances.
   **Open:** the frame's A2 wording should be corrected.
5. **Currency for the total.** Design § 4 line 300 converts another currency «as in § 2»;
   § 2 lines 256-258 and the frame's register (line 249) mark the ECB rate «to verify». The
   mockup's converted figure is an example, and the screen says only that the total is
   converted. **Open:** the source and date of the rate, before the build.
6. **Week boundaries.** «13 weeks ahead» gives no first day of the week or rule for the
   current week. The brief infers Monday and a whole current week, and the server owns it.
7. **Import from «In arrivo».** The inbox classifies a statement and waits (the inbox brief,
   «Module not built yet» row); the action that files it into an account is not drawn in
   either brief. **Open:** the card that builds the `statements` purpose or the import
   cards draws «Importa nel conto…» on the inbox panel.
8. **Alert thresholds and texts.** The balance floor, the 14-day unmatched threshold and the
   7-day grace are the server's (design § 2, § 3, § 4); who sets them and where (design § 5,
   «set the alert thresholds and the balance floor», an administrator) has no screen.
   **Open:** a settings section for them.
9. **Not verifiable yet.** The statement formats and the banks' export formats are «to
   verify» (frame register lines 246-248), so no reader-specific screen is drawn beyond the
   format name the reader returns; the matching and statement screens are drawn on invented
   data only.

## Not in this brief

Paying from PigroCRM and banking APIs (design § 8); the scenario switch that adds open deals
weighted by probability (its own card); the reminder texts and thresholds the alerts use
(the reminders catalogue); the supplier and purchase screens (the purchases design); the
«In arrivo» action that files a statement into an account (the `statements` purpose and
that panel are REB-973's and the import cards; this brief only draws the import dialog the
account page opens); the economic tab's own view; the accountant's export.
