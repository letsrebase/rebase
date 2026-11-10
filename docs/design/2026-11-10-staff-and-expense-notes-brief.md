# Brief: «Persone dell'azienda» and «Note spese» (PigroCRM)

Status: awaiting Lorenzo's answers on the artifact `pigrocrm-staff-and-expenses`; draft
2026-11-10. Card: REB-1010. Design:
`projects/pigrocrm/docs/superpowers/specs/2026-11-09-staff-payroll-and-expenses-design.md`
§ 1 to § 4 and § 6 (REB-965); frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` § A2 to § A5 (modules,
money that falls due, proposals, the register of fiscal rules «to verify»), § A7 (every new
surface passes this gate) and § B6 (the phone shell). Code read for this brief: the staff
service and model that REB-977 put on main
(`projects/pigrocrm/packages/core/src/pigrocrm/core/staff/`), `apps/web/src/lib/permissions.ts`,
`apps/web/src/components/nav.ts` and `apps/web/src/lib/modules.ts`. When Lorenzo has answered,
the orchestrator writes the rows of `docs/design/DECISIONS.md`; the build cards read this file
and those rows, never the artifact.

Everything here is drawn on `shared/ui/tokens.css` and the primitives of `@rebase/ui`:
squared, 1px ink lines, Outfit, Prussian sidebar, Watermelon Deep only on the primary action
and on errors (the application variant, DECISIONS 2026-09-18). It uses four of the five
`StatusPill` tones, `ink`, `muted`, `gold` and `danger`, and never `accent`, as the inbox brief
did. One light scheme. Strings are Italian, amounts `1.464,00 €`, dates `02/11/2026`, tabular
numerals in every column of figures, no emoji, no illustration. Loading is `LoadingState`
(`Loader` and a status sentence), never a skeleton. Every name and figure in the mockups is
invented example data, and no figure is a fiscal rule: the register of the frame § A5 says
which payroll rules are «to verify» (payroll withholdings, the 13th and 14th payments, TFR,
TFM, INAIL, the travel allowance thresholds, the ACI mileage tables), so a screen shows what the
service returns and never computes one.

## Settled before asking

| Question | Answer | Source |
|---|---|---|
| Density | Airy to read (lists and the month's figures), dense to fill in (the pay-terms form, the note form) | taste profile, axes 1 and 6 |
| Tables | Gridded and ruled, 1px column lines, a 2px rule under the header, figures right-aligned in tabular numerals | taste axis 5; application variant § 3 |
| Forms | Dense two-column, label beside the field, action bar bottom right; one column on the phone with a sticky bar | taste axis 6; inbox brief («Caselle email») |
| Desktop list | A table with the open item in a 330 px panel beside it | inbox brief, decision 1 (DECISIONS 2026-11-09, REB-973) |
| Phone list | One column of cards, figures before lists, a sticky action bar, no bulk selection | DECISIONS 2026-11-09 (REB-956); inbox brief |
| Phone shell | Five-place bottom bar with the centre «+»; everything else in «Altro» | DECISIONS 2026-11-09 (REB-956); frame § B6 |
| Loading | `LoadingState`: `Loader` plus a `role="status"` sentence | DECISIONS 2026-11-09 and 2026-11-10 (REB-973, REB-981) |
| Empty and error | Empty: one sentence on what lives here and the one action that fills it. Error: `QueryErrorBanner`, what happened and «Riprova», nothing under it | taste axis 7; inbox brief |
| A status | A `StatusPill` (colour and text), one `Record` from the state to a tone beside the labels | taste 2026-10-21; `components/StatusPill.tsx` |
| The same element on every page | `PageHeader`, `StatusPill`, `MoneyCell`, `DateCell`, `LoadingState`, `QueryErrorBanner`, tabs as «Proposte» draws them | taste 2026-10-21; inbox brief |
| Italian strings | 20% longer than English: draw the long label | taste, standing rules |
| A module that is off | Gone from the sidebar, the phone bar, «Altro» and the «+»; the route answers with a sentence naming the module; the data stays | frame § A2 |
| Who reads pay | Admins whose scope covers the legal entity only; never a member with the collaborator or readonly role; other roles see one aggregate «Stipendi» line per date in the schedule and the Home, never on these pages | design § 4 |
| Estimate against real | PigroCRM estimates, the payroll consultant computes; an accepted payslip replaces that person's estimate; an accepted payslip or F24 is a person's act | design decision 1, § 2; frame § A4 |
| A person with no accepted payslip | Has neither a net pay estimate nor an IRPEF withholding line, so no partial F24 forecast appears | design § 2 and § 7 |
| Who may have staff | `it-standard`, `it-company`, `it-flat-rate` (employees only); directors only on `it-company`; never `non-it` | design § 1; `staff/models.py` (`EMPLOYEE_PACKS`, `DIRECTOR_PACKS`) |
| Who submits and approves a note | The staff member (a user with the collaborator role) or an admin for them; an admin approves, not on their own note | design § 3 and § 4 |
| The fiscal rules | Cited or «to verify»; a screen draws labels and data the pack supplies, never a rule | frame § A5; DECISIONS 2026-11-09 (REB-952) |

## Questions for Lorenzo

Six questions, each with two options drawn at the same scale on the artifact, desktop and
phone. Each answer is one letter.

| # | Question | A | B | Recommendation |
|---|---|---|---|---|
| 1 | Is «Persone dell'azienda» one table for the month, or a roster and a month apart? | One table for the month (cost, cash, state) with the pay terms in the panel | Two tabs: «Persone» (the roster and its terms) and «Paghe di ottobre» (the month) | A: the card asks for cost, cash and state in one list, and the month is what is opened for; B puts terms nobody reads daily first |
| 2 | Which month does the page's month mean? | The month worked: cost, state and the dates its cash leaves (net on the 10th, F24 on the 16th) | The month of cash: what leaves in November, of whatever payroll month | A: the payslip, its state and the closing all belong to the month worked, and cash by calendar month is the schedule's job (C4); B shows two months in one row |
| 3 | Where is a received payslip accepted? | In the person's panel, the estimate beside the payslip, with «Accetta il cedolino» | Only in «Proposte»; the staff page links to it | A: the only thing to judge the payslip against is the estimate; B sends the admin to a page that does not have it |
| 4 | On the phone, how does a note start? | Photo, then one screen with the fields, «Invia»; no drafts | The photo alone saves a draft «Da completare», to finish later | A: the design's states start at «Inviata»; B adds a state and a list of half-notes for a few seconds saved |
| 5 | When is the way of reimbursing chosen? | At approval: «Approva e rimborsa con bonifico» or «in busta paga» | Approval is yes or no; a tab «Da rimborsare» chooses the way, several notes of one person together | B: approving judges the expense, reimbursing decides when money leaves and in which payslip, each where its facts are |
| 6 | Mileage and a trip: kinds of note, or lines in one note? | Three kinds after «Nota spese»: «Una spesa», «Chilometri», «Una trasferta» | One kind: a note is a list of lines (receipt, kilometres, allowance) | A: the common case is a lunch, one receipt and one short form; the trip, which needs lines, gets its own form |

## «Persone dell'azienda»

```
Subject      The people the company pays: employees and directors, what each costs for a
             payroll month, when that money leaves the company, and where the month stands
             (estimated, received, accepted, closed). The page of the staff module.
Audience     An admin of the space: a founder or the person who talks to the payroll
             consultant. They know a payslip and an F24; they do not want the product to
             compute either. Nobody else: a collaborator never sees the entry (inferred).
The job      Know what the month costs and when it leaves, see which payslips arrived, accept
             them, and close the month. On the phone: the same check in a minute, figures
             first.
Palette      As the inbox brief. Pills: muted «Stimato», gold «Ricevuto» (waits for a
             person), ink «Accettato», ink «Chiuso» (inferred). Tokens, never a hex.
Type         Outfit only. 24 px title, 14 px row text, 12.5 px meta, tabular numerals, amounts
             right-aligned; the KPI figures at the existing `kpi` step.
Density      Middle: the `Table` primitive's 48 px rows grown by the two-line cell (name over
             «Dipendente, Commercio, livello 4»); phone cards airy; the panel's terms block is
             the one dense block. (inferred)
Signature    Every amount says what it is: «Stima» (dotted underline) or «Cedolino» (ink
             weight), and where nothing can be said a dash and the reason («Serve il primo
             cedolino accettato»). The month's pill is the least advanced person's state, with
             the line under it naming who set it. (inferred)
States       See the table below.
Constraints  Italian 20% longer; `shared/ui` primitives and tokens only; phone first, proven by
             renders at 390 and 1280 px; admin-only enforced in the service and mirrored in the
             web (`apps/web/src/lib/permissions.ts`).
Non-goals    Computing payroll, withholding or an F24; the cash schedule's own page and the
             total by calendar month (C4); TFR and TFM accrual screens beyond a person's terms
             (their cards); the hourly-cost proposal's logic (design § 5: the page only has
             the button); sending anything to the consultant (C8).
```

**Desktop.** `PageHeader` with the `IdCard` icon, the title, one sentence, a month stepper and
the one strong button «Aggiungi una persona» (a dense two-column dialog: the kind first, then
the fields of that kind, as `EmployeeCreate` and `DirectorCreate` differ; the legal entity is
asked only from the second one on, as the invoices list names it; a director is offered only on
a legal entity whose pack allows it). Under it three figures: «Costo di ottobre» (and how many
of the people come from a payslip), «Esce a novembre, alle persone» (the net pay that leaves,
and what is missing from it), «Il mese» (the aggregate pill, who set it, and «Chiudi il mese»).
Then the table: «Persona» (name over kind, agreement and level, part-time share, start date),
«Costo di ottobre» and «Esce a novembre» (each with its «Stima» or «Cedolino» and, for the cash,
the date), «Stato della paga» (the pill and one line). The month's F24 is one row at the foot,
not one per person (design § 2: grouped in the month's F24), with its date and, while any person
lacks an accepted payslip, no amount and the reason. The panel (330 px) opens on a row: the
month's figures for that person, «Condizioni» tagged «Solo amministratori» (the contract, gross
pay, payments, part-time, the employer rates marked «digitate», the pay day and whether it is the
legal entity's or an override), a «Costo orario» block (design § 5: the monthly cost over the
working hours, «Non viene scritto finché non lo adotti», and the button «Adotta il costo orario»
over REST), and «Modifica condizioni». The filter «Mostra anche chi non c'è più» is the
service's `include_ended`; 50 rows at a time with «Mostra altri». Closing a month asks first and
names who is still «Stimato»: closing locks accepted payslips and estimates alike (design § 4).

**Phone.** In «Altro», the phone's list (frame § B6). One column: the month stepper, two figures
side by side («Costo di ottobre», «Esce a novembre»), the card of the month (pill and who set
it), then one card per person (name and cost, kind, the pill, «esce 1.742,00 € il 10/11»). A card
opens full screen with the month's figures, the terms and sticky actions. The strong action is
the one of the state: «Accetta il cedolino» on a «Ricevuto» person, otherwise «Modifica
condizioni». No bulk action, no table.

| State | What is drawn |
|---|---|
| Empty | «Nessuna persona ancora.» One sentence (qui vedi quanto costano dipendenti e amministratori, mese per mese) and «Aggiungi una persona». No illustration. |
| Loading | `LoadingState`, «Leggo le persone…», in the place of the figures and the rows. |
| Error | `QueryErrorBanner` and nothing under it. |
| Many | 50 at a time with «Mostra altri (N in tutto)»; a 60-character name is ellipsised in the cell with the full value on hover. |
| Stimato | Muted pill; the cost with «Stima». With an earlier accepted payslip, the net is a «Stima» computed from the ratios of the last accepted one (design § 2). |
| Ricevuto | Gold pill and «Da accettare». The estimate stays until the payslip is accepted. A payslip that could not be read stays «Ricevuto» with «Non riesco a leggerlo: scrivi le cifre a mano» (design § 2: or a person types the figures). |
| Accettato | Ink pill and «Cedolino accettato il 07/11/2026»; the figures say «Cedolino». |
| Chiuso | Ink pill and a lock; the month's figures are fixed, with no «Accetta» and no edit. The closing and who closed it are one line. |
| First month of a person | Cost with «Stima»; net «-» and «Serve il primo cedolino accettato»; the month's F24 row has no amount and says whose payslip it waits for (design § 2 and § 7). |
| Module off | The route's sentence of frame § A2: «Il modulo Persone dell'azienda è spento: un amministratore può accenderlo in Impostazioni → Moduli». The entry is gone from the sidebar and «Altro». |
| Profile not applicable | A legal entity without a fiscal profile or on `non-it` takes no staff: the dialog shows the service's own sentence beside the legal entity. A flat-rate entity offers «Dipendente» only; «Amministratore» appears only on a company pack. A `solo` space has the module off by default (frame § A2). |
| Permissions | Admin whose scope covers the entity: everything. An admin with a narrower scope: only the legal entities in scope (the row policy), and the entity filter says so. A collaborator or readonly member: no entry; a typed URL answers «I compensi li vedono solo gli amministratori.» and shows no figure (inferred; the `toProblem` of `lib/api.ts` already phrases a 403 this way). |

## «Note spese»

```
Subject      The expense notes of the staff module: a receipt with its date, amount and
             category, from submission to approval and reimbursement.
Audience     Two people. The staff member: a collaborator linked to a staff member, with a
             receipt in their hand. The approver: an admin who is not the one who spent.
The job      Staff member: photograph a receipt and send it in a few taps, and know where it
             stands. Approver: decide, then reimburse, and see the money land in the cash
             schedule.
Palette      As above. Pills: gold «Inviata», gold «Approvata» (waits to be paid), danger
             «Rifiutata» with its reason, ink «Rimborsata». (inferred)
Type         Outfit only; the amount right-aligned and tabular; the receipt photo is a plain
             bordered box with its file name until the file is drawn by the build.
Density      Table rows at 48 px on the desktop; phone cards airy; the note form is dense
             entry (one column on the phone).
Signature    The receipt: the photo is the first thing in the panel and on the phone card, with
             the state and, once decided, who decided and when. (inferred)
States       See the table below.
Constraints  Italian 20% longer; the same note looks the same on both sides; phone first;
             `readonly` cannot write (design § 3); an admin cannot approve their own note.
Non-goals    The deductibility values of a category (settings, C3); the purchase an approved
             note becomes (C3); the «variazioni paghe» export (C8); company-card expenses (a
             purchase, design § 3); the reader that proposes an expense from an inbox item
             (the `expense` proposal is an admin's, design § 4).
```

**Desktop.** `PageHeader` with the `Wallet` icon and «Note spese». For an admin: tabs «Da
approvare» (with its count), «Da rimborsare» (only if Lorenzo answers B on question 5) and
«Chiuse»; filters «Persona», «Categoria», «Azienda» (the last only from the second legal entity
on). The table: «Nota» (description over person and date), «Categoria», «Importo», «Stato». The
panel: the receipt, the details, and the actions: «Approva» (primary) and «Rifiuta…» (asks for a
reason, as `ProposalDetail` does). On a «Da rimborsare» row of option B, «Rimborsa…» per person: one
bank transfer for the notes of one person, or «In busta paga di novembre», which tells the
consultant through the «variazioni paghe» export and creates no second payment (design § 3). For
a staff member: their own notes only, no «Persona» column, the status filter, and «Nuova nota
spese» as the one strong button (a dense dialog).

**Phone.** For the staff member, the centre «+» gains «Nota spese» between «Nota» and «Carica un
documento» (frame § B6; shown only to a user linked to a staff member, inferred). The form is
full screen: the photo (the camera or a file, as the inbox's «+» offers), the date (today), the
amount, the category, what it is, and a sticky «Invia». After it, the list with a toast. The list
leads with a figure («In attesa di rimborso») and then one card per note. For the approver, the
list is under «Altro» (the entry carries the count of notes waiting): a segmented «Da approvare /
Chiuse» (with «Da rimborsare» on option B of question 5), a card opens full screen with the
receipt and a sticky «Rifiuta…» and «Approva». The strong action is «Invia» for the one, «Approva» for
the other.

| State | What is drawn |
|---|---|
| Empty | Staff member: «Non hai ancora inviato note spese.» and «Nuova nota spese». Approver: «Nessuna nota da approvare.» No illustration. |
| Loading | `LoadingState`, «Leggo le note spese…». |
| Error | `QueryErrorBanner`, nothing under it. |
| Many / long | 50 at a time with «Mostra altri»; a 120-character description is ellipsised with the full text in the panel; a long category name wraps on the phone card. |
| Inviata | Gold pill. For the staff member: «Aspetta un amministratore». |
| Approvata | Gold pill and how it will be paid once decided: «Bonifico del 10/11/2026» or «In busta paga di novembre». Reimbursed in a payslip, it becomes «Rimborsata» when that payslip is accepted (design § 3). |
| Rifiutata | Danger pill and the approver's reason, in one line, on the staff member's card too. Final: a corrected note is a new note (inferred). |
| Rimborsata | Ink pill, the date and the way. |
| Own note | An admin who is also a staff member: «Non puoi approvare una tua nota: la approva un altro amministratore.» In a space with one admin: «Sei l'unico amministratore: la approvi tu e resta segnata come autoapprovata nell'esportazione.» (design § 3). The sentence is shown, never only a disabled button. |
| Module off | `staff` off: the entry and the «+» item are gone. `purchases` off (the module the notes become purchases in): «Note spese» is gone and «Persone dell'azienda» stays (frame § A2: a surface spanning two modules exists only when both are on). |
| Not linked | A collaborator whose user is not linked to a staff member has no entry; a typed URL answers «Non risulti tra le persone dell'azienda: chiedi a un amministratore di collegarti.» |
| Readonly | A readonly member linked to a staff member reads their own notes and has no «+» and no «Nuova nota spese» (design § 3: readonly cannot write). |
| Permissions | Staff member: their own notes, never another's, never a pay figure (not in the panel, not in the toast). Admin: the notes of the legal entities in scope, and may enter a note for a staff member who is not a user («Per chi?» in the dialog). Approve, reject and reimburse: an admin, not on their own note. |
| Travel and mileage | Amounts are typed or the product of typed values (kilometres times the rate the person types); the line «La tariffa la scrivi tu: la tabella ACI non è ancora verificata» stands until the table is verified (design § 3, frame § A5). No exempt threshold and no deductibility is shown. |

## What a component can violate

Proposed, as every rule below is, until Lorenzo has answered; the ones that depend on a
question say which.

- No pay figure (an amount, a rate, a gross or net pay, a fee, an F24 line) is rendered for a
  member with the collaborator or readonly role, in a page, a panel, a toast or a count. The
  entry «Persone dell'azienda» is absent for them and the route answers a sentence. Checkable:
  render the page as each role and assert no amount.
- Every amount on «Persone dell'azienda» carries «Stima» or «Cedolino»; where there is none, a
  dash and the reason. No bare figure.
- No component computes a tax, a contribution or a withholding. The only arithmetic is a
  column total and kilometres times a typed rate; the monthly cost over the hours is the
  service's proposal, shown.
- The month's pill is the least advanced person's state and the line under it names who; the
  four states have one `Record` to a tone beside their labels (muted, gold, ink, ink).
- While any person of a month lacks an accepted payslip, the month's F24 row has no amount.
- A closed month shows no «Accetta», no edit and a lock; «Chiudi il mese» asks first and names
  the people still «Stimato».
- The loading state of both pages is `LoadingState` with their sentence; no `Skeleton`, no new
  spinner (DECISIONS 2026-11-10).
- Desktop is a table with a side panel and phone is a column of cards; no bulk action on the
  phone.
- A staff member's own «Note spese» never lists another person's note. A rejected note always
  shows its reason, on the staff member's side too.
- An admin never gets «Approva» on their own note without the sentence of design § 3.
- A note the staff member has not sent does not exist in the list (depends on question 4:
  option A).
- Approving a note does not choose the way to reimburse it (depends on question 5: option B);
  under option A, the way is part of the one act and never a default the approver did not see.
- The staff page shows the month worked as its axis and gives the date its cash leaves
  (depends on question 2: option A).
- A payslip is accepted by the same service call and the same decision component wherever the
  button is drawn (depends on question 3: option A), and only by a person.
- No emoji, no hex, no `Skeleton`; money `1.464,00 €`, dates `02/11/2026`, strings from locale
  keys with `en` and `it` identical.

## Inferred, not asked

These follow from the design and the code and were not shown to Lorenzo; a reviewer should push
back on any.

- Routes `/app/staff` and `/app/expense-notes` (English, as `/app/inbox`); `ROUTE_MODULES` gains
  `/app/staff` for `staff` and `/app/expense-notes` for `staff` and `purchases`. Icons `IdCard`
  and `Wallet`, neither used in `nav.ts` today.
- Both entries sit in «Amministrazione» after «Giornate»: «Persone dell'azienda», then «Note
  spese». The count of notes waiting is drawn on «Note spese» for an admin, as «In arrivo» does.
- «Persone dell'azienda» is hidden from a non-admin, where the Home fiscal card stays visible
  and explains itself (`permissions.ts:248`): the staff service refuses before it looks anything
  up so a non-admin «learns neither pay nor whether a staff member exists»
  (`staff/service.py:4`), so even the entry would say more than it should.
- «Note spese» is visible to admins and to a user linked to a staff member; a collaborator not
  linked sees no entry.
- The month is the month worked. The page opens on the most recent month that is not closed.
- The month's state is the least advanced person's (estimated, received, accepted), closed
  only by the admin's act, and the line names who set it, as the standing rule on aggregate
  health states asks (taste profile, 2026-09-08). Design § 6 names four states for «the month»
  and § 2 gives a payslip to a person; this reading joins them.
- The cash column holds the payroll due items (net pay, a 13th or 14th payment, a fee). A
  reimbursement by transfer is a purchase's due item (design § 3) and shows under «Note spese»
  and in the schedule, not in «Esce a novembre». The month's F24 is one grouped row.
- «No partial F24 forecast appears» (design § 2 and § 7) is read as: the F24 row shows no
  amount while any person of the month lacks an accepted payslip.
- A director follows the same states as an employee (design § 2 speaks of a payslip per
  person). A director with no payslip history has no net estimate, like a new employee.
- A rejected note is final; a new one is made from scratch. No draft state exists unless
  Lorenzo answers B on question 4.
- The «+» item reads «Nota spese» (frame § B6 says «an expense once `purchases` or `staff`
  exists»); a purchase receipt of a space with no staff goes through «Carica un documento».
- The words and pieces the design does not name: «Chiudi il mese», «Adotta il costo orario»,
  «digitate», «Mostra anche chi non c'è più», «In attesa di rimborso», «Per chi?», the
  sentences of the state tables, the receipt placeholder, and the segmented controls.
- The photo of a receipt is stored as the inbox does it (design § 3: the note references an
  inbox item), and the staff member's own form is typed: the reader's `expense` proposal is an
  admin's (design § 4), so the form does not claim «Letto dal modello».
- «Approvata, in busta paga di novembre» assumes the next open payroll month of that person.

## Gaps in the design and the code

Reported, not papered over.

1. Design § 6 names the month's four states; § 2 gives a payslip to a person and § 4 closes a
   month. Whether the state is per person or per month is not written.
2. «No partial F24 forecast appears» (§ 2) can mean the whole month's F24 waits or only that
   person's lines do; the brief draws the first, the safer one.
3. `reimburse_expense_note` (§ 4, tools table) does not say whether it records the way (bank
   transfer or payslip) or marks the money paid. Question 5 asks where the way is chosen, but
   the service's meaning of the act is for the build card.
4. Frame § B6 puts an expense on the «+» when `purchases` or `staff` exists; the staff design
   needs both. The «+» item shows only to a linked user of a space with both.
5. `StaffMember` has current values and no effective date (`staff/models.py:68`): a change of
   gross pay moves every open month's estimate, and only a closed month keeps its figures.
6. Design § 3 has a «flat daily allowance» and says its exempt thresholds are pack data, but not
   who sets the amount a company grants. The mockup types it; the build card needs the answer.
7. `apps/web/src/lib/permissions.ts` has no row for the staff actions (`list_staff`,
   `get_staff_member`, `create_staff_member`, `update_staff_member`, `set_payroll_pay_day`),
   which the engagements rows (`read_engagements`, `permissions.ts:111`) show how to add, and
   `ROUTE_MODULES` (`lib/modules.ts:50`) has no row for either route. Nothing consults them yet.
8. The sidebar already has «Persone» (the CRM's contacts, `nav.ts`) in «Vendite», and the module
   label is «Persone dell'azienda» (`modules.ts:36`): two entries two sections apart with
   nearly the same word. The design names the second; flagged for Lorenzo's eye.
9. A collaborator's «am I linked to a staff member» has no endpoint that does not disclose staff
   (`StaffService` refuses non-admins before anything else): the web needs one, or it learns it
   from the notes' own answer.

## Not in this brief

The cash schedule and «Cassa» (C4); the tax view and the F24 grouping (C6); purchases and
their category deductibility (C3); the inbox's payslip kind (C2, `payslip` and
`tax_document`); the accountant and consultant exports (C8); TFR and TFM screens; a Home
figure for the personnel line; the settings «Moduli» section (REB-954).
