# Brief: «Incarichi» and the pages of an engagement (PigroCRM)

Status: approved by Lorenzo on 2026-11-10, on the artifact `pigrocrm-engagement-pages`
(«Lorenzo, 2026-11-10, approved the recommendations»: A on 1, 2 and 6, B on 3, 4 and 5, no notes).
Card: REB-1020. Design:
`projects/pigrocrm/docs/superpowers/specs/2026-11-09-collaborators-in-the-crm-design.md`
(REB-963) § 1, § 3 to § 6 and § 9 (it has no section on screens, see Open points), and for the
payments the due-items design
`2026-11-09-due-items-bank-and-cash-schedule-design.md` § 1 and § 4; frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` § A2 to § A5 (modules, money
that falls due, proposals, the register of fiscal rules «to verify»), § A7 (every new surface
passes this gate) and § B4 to § B6 (navigation, Home, the phone shell). Code read for this brief:
`projects/pigrocrm/packages/core/src/pigrocrm/core/collaborators/` and `.../engagements/`, the
PR #827 (REB-1002, now merged: the live days report and the monthly «Rendiconto»),
`apps/web/src/components/nav.ts`, `lib/permissions.ts`, `lib/modules.ts`, the contracts and days
features, and the hub screens this replaces
(`projects/hub/apps/web/src/pages/admin/{Matches,MatchReport,Contracts,CreateMatch}.tsx`, the
member `Contracts.tsx`). The rule this brief creates is the last row of `docs/design/DECISIONS.md`
dated 2026-11-10; the build cards read this file and that row, never the artifact, which is closed.

Everything here is drawn on `shared/ui/tokens.css` and the primitives of `@rebase/ui`: squared,
1px ink lines, Outfit, Prussian sidebar, Watermelon Deep only on the primary action and on errors
(the application variant, DECISIONS 2026-09-18). It uses four of the five `StatusPill` tones,
`ink`, `muted`, `gold` and `danger`, and never `accent`. One light scheme. Strings are Italian,
amounts `1.464,00 €`, dates `02/11/2026`, tabular numerals in every column of figures, no emoji, no
illustration. Loading is `LoadingState` (`Loader` and a status sentence), never a skeleton. Every
name, amount and date in the mockups is invented example data, and no figure is a fiscal rule: the
register of the frame § A5 marks the «gestione separata» share and the stamp duty of an occasional
receipt «to verify», so a screen shows what the server returns (a payable amount, a due item, a
withholding stored on a purchase) and never computes a withholding or a net.

## Settled before asking

A question already answered is a defect to ask again. These were not in the questions.

| Question | Answer | Source |
|---|---|---|
| Density | Airy to read (the figures, the month rows), dense to fill in (the new-engagement dialog, the holding reason) | taste profile, axes 1 and 6 |
| Tables | Gridded and ruled, 1px column lines, a 2px rule under the header, figures right-aligned in tabular numerals | taste axis 5; application variant § 3 |
| Forms | Dense two-column, label beside the field, action bar bottom right; one column on the phone with a sticky bar | taste axis 6; inbox brief («Caselle email») |
| A list on desktop | A gridded table; a queue judged in place has the item in a 330 px panel (the inbox), a record with parts opens its own page (purchases, REB-996 question 2) | inbox brief, decision 1; REB-996 brief |
| A list on the phone | One column of cards, figures before lists, a sticky action bar, no bulk selection | DECISIONS 2026-11-09 (REB-956); inbox brief |
| Phone shell | Five-place bottom bar with the centre «+»; everything else in «Altro» | DECISIONS 2026-11-09 (REB-956); frame § B6 |
| Loading | `LoadingState`: `Loader` plus a `role="status"` sentence | DECISIONS 2026-11-09 and 2026-11-10 (REB-973, REB-981) |
| Empty and error | Empty: one sentence on what lives here and the one action that fills it. Error: `QueryErrorBanner`, what happened and «Riprova», nothing under it | taste axis 7; inbox brief |
| A status | A `StatusPill` (colour and text), one `Record` from the state to a tone beside the labels | taste 2026-10-21; `components/StatusPill.tsx` |
| The same element on every page | `PageHeader`, `StatusPill`, `MoneyCell`, `DateCell`, `LoadingState`, `QueryErrorBanner`, tabs as «Proposte» draws them | taste 2026-10-21; inbox brief |
| Italian strings | 20% longer than English: draw the long label | taste, standing rules |
| A module that is off | Gone from the sidebar, the phone bar, «Altro», the «+» and the registry; the route answers with a sentence naming the module; the data stays | frame § A2 |
| Who sees engagements | Administrators only, every read and write: a collaborator or a read-only member has no entry, no page, no count and no amount (widening it would expose the freelancer's hours and invoice numbers) | design § 6; `lib/permissions.ts:110-125` |
| Who approves a «Rendiconto» | A person, an administrator; no MCP tool exists for it, with or without `full_access` | design § 4 and § 6; frame § A4; PR #827 (`agent_actions.py`) |
| What makes a day payable | Only a day in an approved «Rendiconto» (decided by a person, or counted as approved when nobody objected within seven working days), inside the engagement's dates, counted by the freelancer's ledger and not held; a contested part is held while the rest is paid | design § 4; PR #827 (`ReportDay.payable`) |
| What a payment is | A due item out of source kind `engagement`: «Previsto» (forecast) until the freelancer's invoice, registered as a purchase, replaces it, never beside it; dated by the letter's rule; a date that follows the client's payment reads «circa» and «in attesa dell'incasso» | design § 4; due-items design § 1 (:167-176); cash brief (pills: `muted`, `gold`, `danger`) |
| Availability | From active engagements; a week an active engagement without `days_per_week` touches is «sconosciuta», never «libera»; shown on the freelancer's page and in the engagement form, with a warning when a new engagement overlaps | design § 3 (:181-187); `collaborators/service.py:437-445` |
| Where the freelancer works | In a space of their own, profile `solo`, where the door wrote a contract and a rate card from the letter and they record days; `collaborators` is off by default there, so no «Incarichi» | design § 3; frame § A2 and § B7 |
| What never reaches a freelancer | The client's price and the company's daily budget (and, through them, other engagements' figures) | design § 1 (:125-126) |
| The fiscal rules | Cited or «to verify»; a screen draws labels and data the server supplies, never a rule | frame § A5; DECISIONS 2026-11-09 (REB-952) |
| What the hub keeps | The member's «Contratti» page (letters, signing link, signed PDF, read through the door), the referral reward and talent search: not redrawn here | design § 5 (:307-380) |

## Questions for Lorenzo, answered

Six questions, each with exactly two options, drawn at the same scale on the artifact: the desktop
page and the phone for each option. Lorenzo, 2026-11-10: «approved the recommendations», so each
one below is decided as recommended.

| # | Question | A | B | Recommendation | Decision (Lorenzo, 2026-11-10) |
|---|---|---|---|---|---|
| 1 | What is the list behind the engagement entry: engagements, or freelancers? | «Incarichi» in «Amministrazione», one row per engagement, the count of what waits for a person on the entry. Optimises the work that recurs per engagement (send a letter, decide a month) across every freelancer, as the hub's «Match» list did. Costs two doors to one freelancer (this list and their page in the registry) and one row per engagement. | «Freelance» in «Amministrazione», a role view of the registry beside «Fornitori», one row per freelancer, with a strip of availability and a «Da fare» column. Optimises one record per organisation and the answer to «is she free?» in the row. Costs a list across freelancers of what waits for a person, and the engagement itself is one click deeper. | A: the recurring work is per engagement; availability stays one tab away on the freelancer's page (design § 3) | **A.** «Incarichi», one row per engagement |
| 2 | How is one engagement's page organised? | One page: figures, the letter as a block, then a table of months, each row holding days, «Rendiconto», amount and payment; a month opens in the 330 px panel. Optimises following one month from its days to the date the money leaves. Costs the letter and the rule as a block and not a page of their own. | Tabs «Lettera», «Giorni e rendiconti», «Pagamenti». Optimises one subject at a time and a full payments list with the rule and the invoices. Costs the same month on two tabs. | A: approving the month is what makes a day payable and dates the payment, so decision and consequence belong in one row | **A.** one page, a table of months |
| 3 | How is a «Rendiconto» approved when the client contests some days? | One decision: the days are in the panel with a box each, a shared reason, and a button that reads «Approva trattenendo 2 giorni». Optimises seeing what is held before deciding. Costs a column of boxes on every approval, and 18 tap targets on the phone. | Two verbs: «Approva il mese» is one click; «Contesta dei giorni…» opens a dialog (a screen on the phone) that holds the days with a reason and approves the rest. Optimises the common case being one deliberate click. Costs a second route to the same state, and the days to hold are one step away. | B: most months are approved whole; the phone gets a short list and a two-verb bar | **B.** «Approva il mese» and «Contesta dei giorni…» apart |
| 4 | Does a freelancer see their own month and payment state in their own space? | Nothing new: the contract, the rate card and the days the door already wrote. Optimises no new door read and nothing of rebase's cash timing or a client's objection leaving rebase's space. Costs the freelancer asking by mail whether the month was approved and when they are paid. | A read-only card «Il tuo incarico con rebase» on their contract: letter and signature, the months with «Rendiconto» state, held days, and the payment date once their invoice is registered (never a forecast). Optimises answering the two things they ask in the product where they record days. Costs one more door read, in the opposite direction to the existing one, and a timing to maintain. | B: it is the engagement's output for them, and the card shows only their own fee and dates | **B.** the read-only card «Il tuo incarico con rebase» |
| 5 | Does the phone's centre «+» get «Rendiconto ricevuto»? | Yes: an item of the «+» for an admin of a space with the module on; a sheet with the engagement, the month and the date received; a toast with the tacit date. Optimises recording the arrival from anywhere, in two taps. Costs a rare, admin-only item on the sheet for daily adds. | No: the arrival is recorded on the engagement's month card («Registra l'arrivo»), the same button as on the desktop; the date is typed. Optimises a stable «+». Costs four taps from elsewhere and the risk of forgetting while the clock runs. | B: it happens a few times a month and the date is typed, so a late entry keeps the true date | **B.** no «+» item; «Registra l'arrivo» on the month card |
| 6 | How is availability drawn, in the form and on the freelancer's page? | A strip: one cell per week, 12 weeks, the committed days as the figure, «?» hatched for «sconosciuta», the week's Monday under each cell. Optimises a glance in the little room a form or a registry row has. Costs the engagements behind a week being a step away. | A list: one row per week with status word, days and the engagement numbers. Optimises words and the engagements in view. Costs height: twelve rows do not fit a dialog or a table cell, so the form shows five. | A: the answer needed in a form is a shape (a free gap, an overlap); the list can live on the freelancer's page | **A.** a strip of 12 weeks |

Not asked, because a source answers it or there is nothing to draw: who sees what, the
two-step receipt and decision of a «Rendiconto» (the clock needs the receipt first), whether a
list row opens a page (a record with parts does), the status tones, the new-engagement dialog,
the sidebar section, and anything fiscal (all under «Settled» or «Inferred, not asked»).

## «Incarichi»: the list and where it sits

```
Subject      The engagements of the space: every letter that puts a freelancer on a client's
             work, from draft to signature to the last month, with what waits for a person.
             The page of the `collaborators` module, and the freelancer's tab in the registry.
Audience     An administrator of the space: a founder or the person who puts freelancers on
             projects. They know a framework agreement and a letter; they do not know Documenso
             or the hub's ids. Nobody else: no other role sees the entry (design § 6).
The job      See which engagement needs me (a letter to send, a «Rendiconto» to decide, a field
             to fill in), open it, or start a new one. On the phone: the same check in a minute.
Palette      As the inbox brief. Engagement pills: muted «Da inviare», gold «In attesa di firma»,
             ink «Attivo», muted «Concluso», danger «Annullato» (inferred); a gold dot on what
             waits for a person. Tokens, never a hex.
Type         Outfit only. 24 px title, 14 px row text, 12.5 px meta, tabular numerals, the fee
             right-aligned.
Density      Middle: the `Table` primitive's 48 px rows grown by the two-line cell (name over
             «ruolo · cliente»); phone cards airy; the new-engagement dialog dense entry. (inferred)
Signature    Every row says what it waits for in a sentence, not only a pill: «Rendiconto di
             ottobre ricevuto: tacito dal 13/11/2026», «Lettera pronta, non inviata». (inferred)
States       See the table below.
Constraints  Italian runs about 20% longer: draw «Backend developer · Nordest Meccanica S.p.A.»
             and a 60-character role. `shared/ui` primitives and tokens only. Phone first,
             proven by renders at 390 and 1280 px. Admin-only enforced in the service and mirrored
             in the web (`permissions.ts`).
Non-goals    The hub's referral columns and its «Pigro» link state (the hub keeps referrals);
             searching talents for a free week (the hub's talent search reads availability
             through the door); sending anything to Documenso (its own card); the hub's read
             model.
```

**Desktop.** The entry «Incarichi» (icon `BriefcaseBusiness`, which `nav.ts` does not use) sits in
«Amministrazione» after «Giornate», with the count of engagements that wait for a person. `PageHeader` with the icon, the title, one sentence («Le lettere di incarico dei freelance:
dalla bozza alla firma, i giorni e i pagamenti.») and the one strong button, «Nuovo incarico»: a
dense two-column dialog with the freelancer, the client and the deal or contract it serves, the
role, the dates, «Giorni a settimana», the fee and its unit, the expected days, the letter type
(from the freelancer's fiscal kind), the payment rule (a fixed term with its days and the month-end
option, or «a incasso» with its days), then the server's overlap and over-capacity sentences in a
band and the availability block (a strip of 12 weeks, one cell per week, the committed days as the figure, «?» hatched for a week that cannot be said, the week's Monday under each cell), and «Crea la bozza». It opens prefilled when the hub's
«Crea match» sends the request, with the freelancer chosen by the hub id (design § 5). Tabs, as
«Proposte» draws them, disjoint: «Da fare» (with its count: a draft whose letter is not created or
not sent, a month received and undecided, a missing `days_per_week` or client link), «In corso» (out
for signature, or active with nothing waiting) and «Chiusi» (concluded and cancelled). Filters:
«Freelance», «Cliente», «Stato». Columns: «Freelance e incarico» (name over «ruolo · cliente»),
«Lettera» (number over the dates), «Compenso» (fee over «al giorno · 4 giorni a settimana»),
«Stato» (the pill over its sentence). A row opens the engagement's own page; 50 at a time with
«Mostra altri». The freelancer's page in the registry gains a
tab «Incarichi» (the same columns without the name), a block «Contratto quadro» (its state, «Crea
il contratto quadro», «Invia per la firma»: it goes first, design § 2) and the «Disponibilità»
block.

**Phone.** In «Altro», «Incarichi» with its count. One column of cards under a segmented «Da fare /
In corso / Chiusi»; a card holds the name and the pill, «ruolo · cliente» and what it waits for; it
opens full screen. «Nuovo incarico» is the sticky button of the list: it is not on the «+», because
an engagement is born from a request or at a desk, and the dialog becomes one column. No bulk
action.

| State | What is drawn |
|---|---|
| Empty | «Nessun incarico.» One sentence («Qui vedi le lettere di incarico dei freelance, dalla bozza alla firma.») and «Nuovo incarico». «Da fare» empty: «Niente da fare: nessun incarico aspetta te.» No illustration. |
| Loading | `LoadingState`, «Leggo gli incarichi…», in the place of the rows. |
| Error | `QueryErrorBanner` and nothing under it, so no count is shown that never arrived. |
| Many / long | 50 at a time with «Mostra altri (N in tutto)»; a 60-character role is ellipsised in the cell with the full text on hover, and wraps on the phone card. |
| Draft, out for signature, active, closed | The pills of the `Record`; the sentence under each says what it waits for or how it ended. A closed one shows its end date. |
| A field missing | «Mancano i giorni a settimana» or «Manca il cliente» (design § 1: what an import leaves), a gold dot, and the engagement is in «Da fare». |
| Hidden client | A scoped administrator who cannot see the engagement's client reads the row with role, fee and client as «Nascosto» (`EngagementRead.redacted`, `collaborators/schemas.py:114-118`); the page still opens, without amounts. |
| Module `collaborators` off | No entry in the sidebar or «Altro», no tab «Incarichi» on a freelancer, no registry view; the route answers «Il modulo Collaboratori è spento: un amministratore può accenderlo in Impostazioni → Moduli». The data stays (frame § A2). |
| Permissions | Administrator: everything, within the legal entities in scope. Collaborator or read-only member: no entry, no count in «Altro», no tab; a typed URL answers «Gli incarichi dei freelance li vedono solo gli amministratori.» and shows no figure (inferred). |

## The engagement page

```
Subject      One engagement: the letter and where its signature stands, the days the freelancer
             worked each month, the month's «Rendiconto» that decides what is payable, and the
             payments that follow, until the engagement is closed.
Audience     An administrator. They know the framework agreement's month report loosely; the
             page says «tacito» in plain words and gives the date, never the clause.
The job      Decide a month in a minute with its days in front of them, and know when the
             freelancer is paid and why that date. Before the signature: check the letter and
             send it.
Palette      As above. «Rendiconto»: muted «Non ancora» (the month is not over) and «Non
             ricevuto», gold «Ricevuto», ink «Approvato», ink «Approvazione tacita» (inferred).
             Payments: muted «Previsto», gold «Da pagare», danger «Scaduto da N giorni», ink
             «Pagato» (the cash brief's tones).
Type         Outfit only. The figures at the existing `kpi` step; amounts right-aligned and
             tabular; a «Previsto» amount lighter with a dotted underline, as in the cash brief.
Density      Airy to read (figures, month rows); dense where a person types (the holding reason,
             the dialogs). (inferred)
Signature    Every month row ends in a date and its reason: «19/11/2026 · Fattura 12/2026» and
             «circa 30/12/2026 · in attesa dell'incasso di Fattura 2026/71»: the page says why
             the freelancer is paid when they are. (inferred)
States       See the table below.
Constraints  No screen computes: the payable amount, the tacit date, the due dates, the
             withholding and the net all come from the server; the only arithmetic is a count of
             the days a person selected. The days' descriptions and the objection are text other
             people wrote: rendered as text, never as markup. `shared/ui` primitives; phone
             first, 390 and 1280 px.
Non-goals    Configuring Documenso (deploy work, design § 2); editing the letter's template (an
             administrator's, elsewhere); the hub's day and week views of the report; paying from
             PigroCRM (due-items design § 8); computing a withholding or a net; the referral.
```

**Desktop.** `PageHeader`: the icon, «Giulia Martini · Backend developer», one
line («Alfa Logistica S.r.l. · lettera n. 2026-014») and the pill; the actions «Modifica» (a
draft changes freely, a signed one only the days a week and the client links, design § 1) and
«Chiudi l'incarico…» (it asks for the end date; before the start date the action is «Annulla
l'incarico…», design § 1). Under it three figures, in the order of the phone: «Giorni lavorati»,
«Pagabili», «Da approvare». The block «Lettera e firma»: the letter's number, when it was signed,
the framework agreement's state (it goes first, design § 2), the terms in one line (fee and unit,
days a week, the dates, the expected days) and the payment rule in the words the letter prints,
with «Apri il PDF» and the one action of the state: «Anteprima» and «Crea la lettera» on a draft
(the letter's own free-text fields, `variables`, are typed there; the server lists the blanks the
page prints), «Invia per la firma», then «Rimanda la mail» and «Aggiorna lo stato» while it waits
(design § 6). The table «I mesi»: «Mese», «Giorni» (or «Ore» on an hourly letter), «Rendiconto»
(the pill over a line), «Importo» (a registered invoice's amount, or the lighter forecast) and
«Pagamento al freelance» (the pill over date and reason). The months are the freelancer's, oldest
first, from the start to the current month, then one row «Da dicembre 2026 a febbraio 2027: 3 mesi
previsti». A row opens the month in the 330 px panel: the «Rendiconto» (received on, the tacit date,
decided by and on), the days (date, quantity, what, and «Trattenuto» on a held one), the actions of
the decision (two verbs: «Approva il mese» is one click, «Contesta dei giorni…» opens a dialog that holds the days with a reason and approves the rest; the button «Registra l'arrivo» when a
finished month has no «Rendiconto»), and the month's payments: each item with its state, its date
and its sentence, linking to the purchase of the freelancer's invoice and to the due item in
«Cassa» when it is on. The page settles nothing: marking a payment paid is the due item's and the
purchase's, an administrator's (`settle_outgoing_due_item`, due-items design § 5). Where the server
returns the withholding and the net they are two lines of the item; until it does the item says
«Ritenuta e netto: da definire».

**Phone.** A full-screen page opened from the list: the name and pill, two figures side by side
(«Pagabili», «Da approvare»), the letter in one card, one card per month (days, pill, the date of
payment); the sticky bar holds the strong action of the state: «Decidi il rendiconto di ottobre»
(opens the month: «Contesta…» and «Approva il mese»), or «Invia per la firma» on a draft, or
«Registra l'arrivo» on a finished month with no «Rendiconto». No table.

| State | What is drawn |
|---|---|
| Empty | A new draft has no months: the letter block leads with «Anteprima» and «Crea la lettera», and under it «I mesi compaiono quando l'incarico parte». A signed engagement before its first month ends shows the current month with «Non ancora». |
| Loading | `LoadingState`, «Leggo l'incarico…», in the place of the figures and the months; the days read live can take longer, so the months table has its own `LoadingState`, «Leggo i giorni…». |
| Error | `QueryErrorBanner` and nothing under it; a failed days read errors the months table only, so the letter and the actions stay. |
| Many / long | The panel lists 50 days at most and then «Mostra altri»; a 120-character description is ellipsised with the full text on hover and wraps on the phone. |
| Draft | Letter block only; the server's overlap and over-capacity sentences in a band; the missing fields («Mancano i giorni a settimana», «Manca il cliente») in one band with «Completa». |
| Out for signature | «Aspetta la firma dal 06/11/2026», «Rimanda la mail», «Aggiorna lo stato»; no months. |
| Days not available | The report answers `available` false (the freelancer's space is not set up yet) with a note, or is refused in any space other than the one `PIGROCRM_LEADS_SPACE` names (a conflict naming it): the «Giorni» column reads «-» and one line says what the server says; the letter, the receipt, the decision and the payments stay. |
| «Rendiconto» | «Non ancora» (the month is not over), «Non ricevuto» with «Registra l'arrivo», «Ricevuto» with the tacit date, «Approvato» with who and when, «Approvazione tacita» with «Nessuna obiezione entro il 12/11/2026» (no day can be newly held), and on any of the last two the held days with the reason. |
| Payment | «Previsto» with the lighter amount and a «circa» date, «Da pagare» once the freelancer's invoice is a purchase, «Scaduto da N giorni» when late, «Pagato» with the date. A month with no approved «Rendiconto» shows «Pagabile: -» and «dopo l'approvazione». |
| Flat fee | One row for the billing month; «Giorni» reads «-»; the amount is the letter's fee as the due item carries it (the report prices no flat fee). |
| Hidden client | Role, fee and every amount read «Nascosto»; the days and the decision stay. |
| Closed or cancelled | Read-only: no «Modifica», no letter action; the months and their payments stay until settled. |
| Module off | The route answers the sentence of the frame § A2; the data stays. |
| Permissions | Administrator within the scope. Everyone else: the route's sentence and no figure. «Approva» and «Contesta» exist only as a person's buttons; no agent has them. |

## The freelancer's own view

```
Subject      The freelancer's contract with rebase, in the freelancer's own PigroCRM space (a
             `solo` space the door opened): the contract page they already have, with a
             read-only card on it with the state of their engagement.
Audience     A freelancer, alone in their own space, who records days and invoices rebase. They
             do not know the engagement, the due items or the cash schedule of rebase.
The job      Know whether the month was received and approved, and when they will be paid,
             where they already record the days.
Palette      As above; the freelancer's own pills only: ink «Approvato», gold «Ricevuto da
             rebase», muted «In corso». Tokens, never a hex.
Type         Outfit only; amounts right-aligned and tabular.
Density      Airy: three facts and a three-row table. (inferred)
Signature    A payment date appears only once their own invoice is registered at rebase; before
             that a dash, never a forecast. (inferred)
States       See the table below.
Constraints  Reads only the freelancer's own engagement, through the door; shows their own fee
             and dates; never the client's price or budget, another freelancer, another
             engagement, a forecast, or the reason of an objection (inferred). It is read-only: no
             button approves, contests or edits anything.
Non-goals    Any edit of the engagement; the hub's member «Contratti» page (it stays, design § 5);
             recording the days (the existing «Giornate»); the freelancer's own tax data.
```

**Desktop.** The card «Il tuo incarico con rebase» sits at the top of their contract page
above «Termini», «Tariffe» and «Giorni da fare». Three facts (the letter's number and
signature, the fee with the days a week, the payment rule in the words the letter prints) and a
table of months: «Mese», «Rendiconto» (the pill and the date; a held day is named by date), «Pagamento»
(the date and «Fattura 12/2026»). **Phone.** The same facts as one card at the top of the contract
screen of the `solo` bar («Giornate» in the third place, `phoneThirdPlace`), the months as two
lines each.

| State | What is drawn |
|---|---|
| Empty | A contract that did not come from an engagement has no card. A freelancer whose engagement is still a draft or out for signature has the contract only once it is signed (the door writes it then). |
| Loading | `LoadingState`, «Leggo il tuo incarico…», in the card's place only; the contract's own sections load without it. |
| Error | `QueryErrorBanner` in the card's place: «Non riesco a leggere lo stato dell'incarico. I tuoi giorni e la tariffa si vedono lo stesso.» |
| Many / long | A long engagement shows the last six months and «Mostra i mesi precedenti». |
| «Ricevuto da rebase» | Gold pill, the date rebase received the month and the last day for the answer («risposta entro il 12/11/2026»). |
| Held days | «Approvato, 2 giorni non riconosciuti» with the dates; the reason stays in rebase's space. |
| Module off | Not applicable: the card belongs to the contract and is not a module's page; in a space where the door never wrote an engagement there is no card. |
| Permissions | The freelancer, as the admin of their own space, sees their own engagement only. No other space and no other freelancer's data reaches the card; the door answers by the hub freelancer id of the space's owner. |

## What a component can violate

Each rule is checkable by a test or a render. The last six follow Lorenzo's answers of 2026-11-10
(see the questions table).

- No engagement figure (a fee, a rate, an amount, a payable quantity, a due item) is rendered for
  a collaborator or a read-only member, in a page, a panel, a toast or a count; the entry,
  the tab and the count are absent for them and the route answers a sentence. Checkable: render
  as each role and assert no amount.
- A hidden client reads «Nascosto» and no amount is shown for that engagement.
- No component computes a payable amount, a due date, a tacit date, a withholding or a net; the
  only arithmetic is a count of selected days.
- A day is payable only when the server says so; a held day always carries the reason in the
  panel; no agent and no tool approves a «Rendiconto» and the button is a person's.
- A «Rendiconto» state and an engagement state each have one `Record` to a tone beside their
  labels; «Approvazione tacita» is a state of the month and no row is written for it.
- A forecast never looks like an expected payment: «Previsto», the lighter amount, the dotted
  underline and «circa» on a date that follows the client's payment (cash brief rule).
- «sconosciuta» never reads as «libera»; a week's status is a word and a figure, never colour alone.
- The freelancer's card shows only their own engagement, their own fee and dates, a payment date
  only once their invoice is registered, and nothing about the client's price or budget, another
  freelancer, a forecast or an objection's reason.
- The days' descriptions and the objection are rendered as text.
- The page settles no payment: it links to the purchase and the due item.
- A list is a gridded table on desktop and a column of cards on the phone, with no bulk action
  there; a loading state is `LoadingState`, never a `Skeleton`.
- The list behind «Incarichi» is one row per engagement, with the count of what waits for a person on the
  entry and a tab «Da fare»; a freelancer is a row only on the registry's own page (question 1).
- An engagement's page is one page with a table of months: each row holds days, «Rendiconto», amount and
  payment, and the month opens in the 330 px panel; the letter is a block above the table (question 2).
- «Approva il mese» is one deliberate action; contesting is a separate «Contesta dei giorni…» that asks
  for the reason and holds the days; on the phone it is a screen of its own (question 3).
- A freelancer's own space shows a read-only card «Il tuo incarico con rebase» on their contract, with
  their own months and a payment date only once their invoice is registered; nothing else of rebase's
  (question 4).
- The phone's «+» has no item for a received «Rendiconto»: it is recorded on the month card with a typed
  date, the same button as on the desktop (question 5).
- Availability is a strip of 12 weeks, the committed days as the figure in each cell, «?» for «sconosciuta»
  with a legend; a list of weeks may live on the freelancer's page (question 6).
- No emoji, no hex, no illustration; money `1.464,00 €`, dates `02/11/2026`, strings from locale keys
  with `en` and `it` identical.

## Inferred, not asked

These follow from the design and the code and were not shown to Lorenzo; a reviewer should push
back on any.

- Routes `/app/engagements` and `/app/engagements/$engagementId` (English, as `/app/inbox`);
  `ROUTE_MODULES` gains `/app/engagements` for `collaborators`; the entry is «Incarichi» while the
  module's label is «Collaboratori» (`lib/modules.ts:37`); icon `BriefcaseBusiness`.
- The entry sits in «Amministrazione» after «Giornate» and is the first sidebar entry
  gated by role: `nav.ts` says none is (`nav.ts:39`), so the build adds the role to the entry table.
- The engagement's state labels are the hub's (`Da inviare`, `In attesa di firma`, `Attivo`,
  `Concluso`, `Annullato`, `projects/hub/apps/web/src/lib/format.ts:86-92`), with the tones above.
- «Rendiconto» tones: `muted` for «Non ancora» and «Non ricevuto», `gold` «Ricevuto», `ink` «Approvato» and
  «Approvazione tacita». Availability: `muted` «libera», `ink` «impegnata», `gold` «sconosciuta».
- The list's row opens the page and there is no panel on the list: four parts do not fit 330 px.
  The month, which is one thing to judge, opens in the panel (the inbox's shape).
- The months axis is the report's (`ReportMonth`, one per calendar month); the column reads «Ore» on an
  hourly letter. The hub's per-day and per-week report is not redrawn: the days sit in the month's
  panel.
- The new-engagement form is a dialog, as the staff dialogs are; the letter's own free-text fields are
  typed in the letter block of the page, once the draft exists (`LetterRequest`).
- The freelancer's registry page gains the tab «Incarichi», the block «Contratto quadro» and
  «Disponibilità» (design § 2 and § 3 place them on the freelancer).
- The words and pieces the design does not name: «Registra l'arrivo», «Approva il mese», «Contesta dei
  giorni…», «Approva trattenendo 2 giorni», «Trattieni 2 giorni e approva il resto», «Il tuo incarico con
  rebase», «Non ancora», «Non ricevuto», «Approvazione tacita», «Ritenuta e netto: da definire», «Chiudi
  l'incarico…», the sentences of the state tables and the tab names.
- The count on the entry and the tab «Da fare» are the engagements that wait for a person: a draft
  (letter not created or not sent), a month received and undecided, a missing `days_per_week` or client.
- A held day's reason is not shown to the freelancer, only its date: the reason can carry the client's
  words, and rebase tells the freelancer the outcome itself (framework 6.2).
- No payment action on the engagement page: settling is the due item's and the purchase's (an
  administrator's, `settle_outgoing_due_item`).
- A payment date is shown to the freelancer only for an expected item (their invoice registered), never a
  forecast, so a «circa» date, which can move with the client, is not a promise.

## Open points

Gaps, contradictions and unresolved items found while writing the brief, so that the cards for
the pages inherit them. None was shown to Lorenzo as a question. File and line references are to main
at the rebase of this brief (PR #827 and PR #834 merged).

1. The design has no section on screens: its headings are «Why», «Decisions taken», § 1 to § 9, the
   rows and the cards (`2026-11-09-collaborators-in-the-crm-design.md:14-526`). What it says about the
   UI is scattered: availability on the freelancer's page and in the form (`:181-187`), the
   «Rendiconto» as an approval request (`:193-203`), the reserved area (`:158-161`), and self-service
   out of scope (`:503-505`); the last card line names «the brief for the engagement pages; those
   pages» (`:526`). The card REB-1020 itself could not be read when this brief was written (no Linear
   tool in the session): the scope is the assignment's.
2. The freelancer's card (question 4 B) needs a read of rebase's space from the freelancer's space, the
   opposite direction to the in-process read of the days report, which is refused outside the space
   `PIGROCRM_LEADS_SPACE` names (design `:174-180`; `collaborators/rules.py:372-390`); § 9 excludes
   self-service beyond recording days (`:503-505`) and § 5 lists the door's reads, all the hub's
   (`:355-370`). The design needs a paragraph for it.
3. Fixed in #834: outside the leads space the days read was refused while the routes that record and
   approve a month had no such check; `require_leads_space` now guards both
   (`collaborators/monthly_reports.py:100` and `:176`).
4. Fixed in #834: the source kind `engagement` was not in `RESTRICTED_SOURCE_KINDS`, so `list_due_items`
   served a collaborator or a read-only member the freelancer's name, amount and date, against design
   `:409-411`; it is restricted now (`core/due_items/models.py:73-85`).
5. No withholding or net exists on any API: the forecast amount is the gross fee times days
   (`collaborators/payments.py:78-110`), `payable_amount` is quantity times fee
   (`collaborators/days_report.py:326-331`), and the withholding's second due item and the net (design
   `:287-302`) are not written yet; the «gestione separata» share and the stamp duty are «to verify»
   (`:298-302`). The page draws «Ritenuta e netto: da definire» until the server returns them.
6. A flat fee: `payable_amount` is none (`days_report.py:326-331`) and the forecast bills once, at the end
   of the month `ends_on` falls in (`payments.py:82-87`); the design does not say what a «Rendiconto»
   approves for a flat fee (framework 6.1 speaks of the month's report). The page draws one row and the
   same decision, which is a reading.
7. Who tells the CRM that a «Rendiconto» arrived: framework 6.1 has the freelancer send it, nothing in
   the CRM carries it (§ 9 excludes self-service), so `POST .../monthly-reports` is an administrator's
   note of a mail (question 5 B records it on the month card); nothing alerts when a month ended and no
   report came (frame § A4 names alerts for missing documents, not this one).
8. `lib/permissions.ts:113-125` has the rows for the engagement reads and writes («No screen consults
   them yet»), `ROUTE_MODULES` has no row for the route (`lib/modules.ts:50`), the module's label is
   «Collaboratori» where the entry is «Incarichi» (`lib/modules.ts:37`), and a role-gated sidebar entry
   has no mechanism (`nav.ts:39`, `visibleSections` at `:82` takes the module access only): the build
   adds all of them, as the staff brief's cards do.
9. «Giorno lavorativo» is not defined in the framework agreement; the tacit date counts without
   Saturdays, Sundays and the national holidays of `fiscal.calendar` (`collaborators/rules.py:315`),
   flagged in PR #827 for the contracts' legal review. The page shows the server's date; «entro il terzo
   giorno lavorativo» (framework 6.1) is text without a computed date.
10. The hub list shows referral columns and the «Pigro» link state (`Matches.tsx`); the CRM list drops
    both (the hub keeps referrals, design `:340-347`). The hub's day and week hours views
    (`MatchReport.tsx`) become month rows and a days list.
11. Not verified: no `axe` run or dark-theme render of the artifact (one light scheme); the desktop frames
    scroll sideways inside their box at 390 px; the Italian copy had no native read; the states
    «Concluso», «Nascosto», «Ore», a flat fee and a held day on the freelancer's card are described here
    and not drawn on the artifact; every figure in the mockups is invented.

### Needs code before the page can be built

Each of these is something the pages read that no API on main returns yet; the build card for the
page waits for them or adds them first.

- **Names in the engagements list API.** `EngagementRead` carries ids and no freelancer or client name
  (`collaborators/schemas.py:105-139`), so the list cannot draw «Freelance e incarico».
- **The «waits for a person» count and filter.** `EngagementListQuery` has no search, no such filter and
  no count (`schemas.py:142-149`), and the monthly reports are listed per engagement only
  (`GET /api/engagements/{id}/monthly-reports`, `routers/collaborators.py`): the entry's count and the
  tab «Da fare» need a server query.
- **A `source_id` filter on due items.** `DueItemListQuery` filters by organisation, source kind and
  dates (`core/due_items/schemas.py:232-244`) while an engagement forecast stores the engagement as
  `source_id` (`collaborators/forecast.py:106-107`): the page's «Pagamenti» need
  `source_kind=engagement` with a `source_id`.
- **A signed date on an engagement or its letter.** `EngagementRead` has no signed date or document
  state (`schemas.py:105-139`), `LetterRead` only the document (`:247-256`), and no Documenso code
  exists on main; the states move by hand (`routers/collaborators.py:110-118`). «Firmata il 28/08/2026»
  and «La firma aspetta dal 06/11/2026» have no source until the signature card.
- **Availability and the overlap warning counting the same engagements.** Availability reads active
  engagements only (`collaborators/service.py:437-445`, the `("active",)` read at `:488`) while the
  warning counts draft and out-for-signature ones too (`:344-386`, `LIVE_STATES` in `models.py`): the
  strip in the form can show 4 days a week while the band says 6, until the new engagement is active.
- **A read for the freelancer's card** (open point 2): the door answers the freelancer's own engagement
  in their space, with the fields of the card and nothing else.
- **The withholding and the net** (open point 5): the page draws none until the server returns them.

## Not in this brief

The Documenso integration and the signature webhook; the framework agreement's own creation flow beyond
its block on the freelancer's page; the cash schedule and the purchase of the freelancer's invoice
(C3, C4); the withholding and the net to pay; the hub's «Crea match» and read model; the import of
existing matches and the classification of fiscal kinds (design § 7); the member area's «Contratti»; the
settings «Moduli» section (REB-954).
