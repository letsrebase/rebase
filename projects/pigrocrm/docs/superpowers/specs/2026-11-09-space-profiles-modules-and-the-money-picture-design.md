# Space profiles, modules and the money picture

Status: approved by Lorenzo on 2026-11-09. Card: REB-952. Project: «Shape PigroCRM around
how each space works» (P-REB-80).

This document does two things. Part A fixes the frame that eight projects share. Part B
designs the first of them in full: the space profile, switchable modules, the onboarding
and the mobile shell. Part C outlines the other seven, each to get its own design and its
own project, one at a time, in the order given there.

## Why

PigroCRM shows every space the same product: one sidebar (`apps/web/src/components/nav.ts`),
one Home, one set of MCP tools. A freelancer who wants to know which contract days to
invoice and a company that runs a pipeline, pays suppliers and freelancers and owes IVA
every quarter read the same screens. Lorenzo, 2026-11-09: «sono due esperienze
completamente diverse: dobbiamo pensarle bene entrambe».

Two facts from the code make the gap concrete:

- **The supplier side does not exist.** A cost is a single row with a free-text `supplier`
  (`core/timetracking/models.py:253`), no due date, no supplier entity, no passive invoice,
  no payment schedule. The receivable side has due dates and a due-date schedule
  (`invoices/models.py:92`, `dashboard/service.py`), the payable side has nothing.
- **rebase's relationship with its freelancers lives in the hub.** Match, letter, signature
  and hours report are hub-only (`projects/hub/packages/core/src/rebase_core/models/matches.py`);
  the CRM sees a deal and, for the freelancer, a space of their own. Lorenzo has to open the
  hub to manage a match, have documents signed, and check that a freelancer is not already
  busy before offering them another project.

## Decisions taken in the conversation (Lorenzo, 2026-11-09)

1. **Two profiles for now**: a solo professional (flat-rate or ordinary regime) and a company
   (srl). Other company forms come later as new fiscal packs, not as new profiles.
2. **Solo**: the focus is active contracts, worked days, what to invoice and when money
   comes in. **Company**: deals and pipeline, customers and people, active and passive
   invoicing, paying suppliers and freelancers on time.
3. **The relationship with freelancers moves into PigroCRM** as a module switched on in the
   settings. The hub keeps the freelancer's own side: signup, card, CV, and access to
   resources such as their PigroCRM space. Where exactly the line falls is designed in C5.
4. **Payment terms to a freelancer depend on each freelancer's contract**: a fixed term
   (for example 30 days end of month from the invoice), or paid when the client pays.
5. **Passive invoices arrive by every road**: XML upload, PDF upload, email attachments,
   later the SdI. A space needs **several mailboxes, not only Gmail**, each with a purpose
   (personal ones for activities, billing ones for invoices and due dates).
6. **Bank data**: statements from email or manual upload first, with alerts when something
   that should be there is missing; banking APIs on standard protocols later, for as many
   accounts as possible.
7. **The company side covers everything that moves cash**: payroll and directors, TFR and
   TFM, travel and mileage reimbursements, employee expenses, tax instalment plans, the full
   IVA cycle, foreign active and passive invoices.
8. **The accountant keeps the books.** «Pigro serve a noi per avere un'idea in tempo
   reale di tutto». Double entry only if precision truly needs it, otherwise not (A6).
9. **The UI is redone mobile first.** On the phone Lorenzo checks the economic figures and
   the deals and works his activities (see what to do, mark done, add new); the rest is
   secondary but must be reachable.
10. **One space can keep several legal entities** (the 2026-10-03 design stays). Lorenzo
    asked whether one space should be one company; the answer kept is: a space is one
    business, a legal entity is who invoices (A1).
11. **Everything here is manageable over MCP**, with permissions that follow the user, and a
    feature that moves from one product to the other takes its MCP tools with it (A8).

## Part A: the shared frame

### A1. Space, legal entity, profile

- **A space is one business**: one customer base, one pipeline, one set of mailboxes, one
  team. That is why several legal entities live inside one space
  (`2026-10-03-several-aziende-in-one-space-design.md` § 0: the humancraft case, a
  flat-rate professional, an srl and a foreign company working the same customers).
- **A legal entity is who invoices**: its own register, fiscal pack and regime
  (`fiscal/pack.py`: `it-flat-rate`, `it-standard`, `it-company`, `non-it`), its own taxes. Taxes are
  never summed across entities; money in and out is summed, with a filter per entity.
- **The profile belongs to the space**: `solo` or `company`, or not chosen yet. It decides
  the default modules and the layout of Home and of the phone. It is not a fiscal fact.
- A space with one legal entity, which is nearly every space, never sees the word: the
  second entity appears only once somebody adds one in the settings, as today.

### A2. Modules

A module is a group of features that is on or off for the whole space. Off means:

- gone from the sidebar, the phone bar, the Home and the search results;
- its REST routes answer 404 with a sentence naming the module («Il modulo Deal è spento:
  un amministratore può accenderlo in Impostazioni → Moduli»), so a stale link explains
  itself;
- its MCP tools are not registered, the way `build_server` already leaves out tools whose
  settings are off (`apps/mcp/src/pigrocrm_mcp/server.py:252`, `http.py:16`);
- its scheduled jobs and reminders skip the space;
- **its data stays**, untouched, and comes back when the module is switched on.

What is always on (the core): customers, people, notes, activities, calendar, reminders,
documents and templates, proposals, active invoices, legal entities and fiscal profile,
agents, settings.

The modules, the ones that exist today first:

| Module | What it holds | Exists |
|---|---|---|
| `deals` | pipeline, deals, value lines, offers | yes |
| `contracts` | contracts, rate cards, worked days, approvals, the day register | yes |
| `time` | hours on a deal, timer, rates (a time entry needs a deal: `timetracking/models.py:152`) | yes |
| `costs` | costs and cost categories, replaced by `purchases` in C3 | yes |
| `purchases` | suppliers, passive invoices, the IVA cycle (C3) | no |
| `cash` | the cash schedule, bank accounts, statements, matching (C4) | no |
| `collaborators` | engagements with freelancers, availability, letters and signature, payments (C5) | no |
| `taxes` | today: the fiscal estimate, the ceiling panel and simulator, the year overview; later the due dates of C6. The fiscal profile stays in the core, since invoicing reads it | partly |
| `staff` | employees and directors, payroll estimates, TFR and TFM, expenses, travel (C7) | no |

The rows marked «no» are listed for reference only: a module that is not built yet is not
switchable, not stored and not shown anywhere. Each later project makes its row live when it
ships, with its default from the table below.

**Dependencies.** A module can need another: `time` needs `deals`, because a time entry
belongs to a deal. A module cannot be on while one it needs is off; switching off `deals`
with `time` on lists `time` in the same confirmation (B1). A tool, route or screen that
spans two modules (`convert_deal_to_contract`, `bind_time_to_invoice`, `get_deal_pnl`)
exists only when all of its modules are on. A core page that points into an off module
(the deals on a customer page, a task's link to a deal, a search hit) hides that part
rather than linking to a 404. The builder maps every route, tool, tab and job to its
module; this rule settles the edges.

Defaults per profile:

| Module | `solo` | `company` |
|---|---|---|
| `deals` | off | on |
| `contracts` | on | on |
| `time` | off: a solo records work on contracts, as days or hourly figures (`work_units`, `work_units/models.py:158`) | on |
| `costs` / `purchases` | on | on |
| `cash` | on (money in only, A3) | on |
| `collaborators` | off | on when the onboarding says so |
| `taxes` | on | on |
| `staff` | off | on when the onboarding says so |

### A3. One model for money that falls due

Money in, money out and taxes are the same kind of thing on a calendar: an amount, a
direction, a counterparty (customer, supplier, collaborator, employee, the tax office), a
legal entity, a date, and the document or rule it comes from. Every later project writes
these, and the cash schedule (C4) reads them. Each moves through four states:

1. **forecast**: computed from a rule before any document exists (the next monthly
   invoice of a contract, the November tax instalment, a freelancer's expected invoice
   after the month's hours report);
2. **expected**: a document exists and fixes amount and date (an issued invoice, a received
   invoice, a payslip, an F24);
3. **settled**: matched with a bank movement, or marked paid by a person;
4. **dropped**: no longer owed (a credit note, a contract that ended).

A forecast is replaced by its document, never duplicated by it. A due date that depends on
another one (a freelancer paid when the client pays, decision 4) points at it and moves
with it. The solo profile shows only money in and its own taxes; the company profile shows
both directions and the projected balance.

This section fixes the concept. The tables are designed in C4, which is also where the
existing receivables (`invoices.due_date`, `payment_status`) are folded in.

### A4. Proposals and certain data

What a model reads from free text is a **proposal** a person accepts, through the proposals
module that already exists (`propose_contract`, `propose_day`; accepting is the person's
act, never an agent's). That covers an invoice read from a PDF or an email body, a contract
clause, a receipt photo, a PDF bank statement.

What arrives already structured and checkable is **certain** and enters directly, with a
trace of where it came from and a way to undo it: a FatturaPA XML whose parties match a
legal entity of the space, a bank statement in a standard format (CSV the bank exports,
CAMT.053). The same document arriving by two roads (an email and later the SdI) is
recognised as one, by issuer VAT number, number and date.

What should be there and is not is an alert, through the reminders module: a month with no
statement, two statements whose balances do not join, a payment with no document, a
supplier invoice expected after an hours report that never came.

### A5. Fiscal rules are cited or marked

Every fiscal rule a later design relies on names its source (law, Agenzia delle Entrate
page, circular), or is written «to verify» and verified before the code. A rule is data in
the fiscal pack where it can be, not a branch in a service: rates, thresholds, deadlines
and instalment splits change by law, the pack is versioned (`fiscal/pack.py`), and a new
legal form is a new pack.

The register as of today, from the conversation:

| Rule | State | Source |
|---|---|---|
| Withholding of 20% on professional and occasional work paid by a withholding agent | verified | Agenzia, F24 ritenute lavoro autonomo, «Aliquote» |
| An invoice's `DatiRitenuta` is type `RT01` (natural person), payment reason `A` (work in the exercise of an art or profession, habitual) and the 20% above, on every line (`Ritenuta` `SI`); the amount is the total minus the withholding in `ImportoPagamento`, the total as printed in `ImportoTotaleDocumento` | verified (REB-994, 2026-11-10) | Technical specifications v1.4 (`TipoRitenuta`, `CausalePagamento`, § 3.6); Agenzia, Certificazione Unica 2026 instructions (reason `A`). That the invoice's payment amount is the net is the common practice of the format, not a rule the specifications state |
| The contribution of a pension fund or the INPS surcharge on an ordinary-regime invoice (`DatiCassaPrevidenziale`): that it follows the VAT of the service, joins the taxable amount, and the effect of D.Lgs. 192/2024 on the INPS surcharge's base | to verify (REB-994) | The format side is verified (a contribution carries a VAT rate and the taxable amount of a rate is the lines plus the contributions of that rate: Elenco dei controlli v1.9, control 00422; `TC22` is INPS: specifications v1.4), the legal side is not, and the sources that discuss it disagree. Goes to the accountant list of REB-975; not computed, not exported |
| The withholding base includes the 4% INPS rivalsa and expenses refunded at cost, excludes the professional fund contribution | verified | Agenzia, same scheda, «Importi su cui si applica la ritenuta» |
| 30% final withholding on non-residents for work done in Italy | verified | same, «Aliquote» |
| Flat-rate professionals are not withheld, on their written declaration | verified | L. 190/2014, art. 1 c. 67 |
| A flat-rate taxpayer is not a withholding agent, except on employees | verified | L. 190/2014, art. 1 c. 69 |
| Income tax advance 100%, in 40%/60% (50%/50% under ISA), June and November | verified | Agenzia, «Imposte sui redditi (Irpef, Ires)» |
| Balance and first advance payable in monthly instalments up to 16 December; November advance in one payment | verified | same |
| IRPEF: no advance due at or below €51.65; a single November payment below €257.52 | verified | Agenzia, «Imposte sui redditi (Irpef, Ires)» |
| IRES: the advance is split in two instalments only above €103 | verified | same |
| The advance base is the declared tax net of deductions, credits, withholdings and excesses | verified | same |
| 0.40% added for paying within 30 days after the deadline (deferral) | verified | same |
| Income-tax dates are «salvo proroghe»: a year's dates can move by decree | verified | same |
| Company dates: balance and first advance on the last day of the sixth month after the fiscal year end (or of the month after a late approval of the financial statements); second advance in the eleventh month | verified | same |
| Forecast (previsionale) advance method exists | verified | same |
| Conditions of the forecast method; interest and penalties on a shortfall | to verify | |
| Income-tax dates: 30 June for the balance and the first advance (or within 30 days with 0.40%), 30 November for the second or only advance, for a person | verified 2026-11-10 | Agenzia, «Imposte sui redditi (Irpef, Ires)» (updated 30 September 2026), «I versamenti delle persone fisiche» |
| Flat-rate substitute tax: balance and first advance on 30 June, second or only advance on 30 November (2026) | verified 2026-11-10 | Agenzia, Scadenzario fiscale, «Regime forfetario agevolato: versamento imposta sostitutiva» (30 June and 30 November 2026) |
| Advances of the flat-rate regime's substitute tax: split and thresholds, and instalments (the dates are verified above) | to verify | |
| Instalment rule and interest for an srl | to verify | |
| 5% substitute rate on start-up conditions, 15% otherwise; profitability coefficients | to verify | |
| Flat-rate contribution base: whether INPS contributions are deducted before the substitute tax | to verify | |
| The flat-rate regime exists from 1 January 2015 (L. 190/2014, art. 1, commi 54-89) and the income estimate's arithmetic is the same in every year | to verify | |
| «Gestione separata» and «artigiani e commercianti» rates, minimums and quarterly dates | to verify | |
| IRES rate: 24% of the net total income | verified | D.P.R. 917/1986 (TUIR), art. 77 c. 1, Normattiva, read 2026-11-10 (REB-1005) |
| IRAP ordinary rate: 3.90% of the net value of production; a region may vary it by up to 0.92 points (art. 16 c. 3); other rates apply to the subjects of c. 1-bis and to those the D.L. 20 February 2026 n. 21 names. Normattiva's printed text of art. 16 c. 1 reads 3,50 under notes that say D.L. 66/2014's reduction was repealed by L. 190/2014 art. 1 c. 22, so it is not the rate | verified | Dipartimento delle Finanze, «IRAP: disciplina del tributo» (3,90% and the 0.92 points), and the regions' 2026 rate tables (Emilia-Romagna: 3,90 ordinary), read 2026-11-10 (REB-1005) |
| The denomination of an srl contains «società a responsabilità limitata» | verified | Codice civile art. 2463 n. 2, Gazzetta Ufficiale text, read 2026-11-10 (REB-1005) |
| IRPEF brackets, regional and municipal surcharges; IRAP region rules (which rate a company's region and activity put it under) | to verify | |
| Annual chamber of commerce fee | to verify | |
| Cash or accrual basis of the ordinary person's and the srl's income | to verify | |
| Flat-rate basis: collected revenue minus the INPS surcharge lines (cash principle) | to verify | |
| Year in which a withholding suffered counts as a credit | to verify | |
| Legal treatment of a declared figure that changes an advance already settled | to verify | |
| F24 tax codes per line | to verify | |
| Filing date of the tax return | to verify | |
| TD17, TD18, TD19 integrate foreign purchases through the SdI | verified | Agenzia, FatturaPA guide v1.10, April 2025 |
| Legal deadline of the 15th of the following month for TD17, TD18, TD19 | to verify | |
| Intrastat for services | to verify | |
| Monthly IVA: settlement and payment by the 16th of the following month | verified 2026-11-10 | Agenzia, «F24 IVA Periodica - Come e quando si versa» and «Come pagare l'Iva» (updated 20 September 2024) |
| Quarterly IVA: payment on 16 May, 20 August and 16 November for the first three quarters; the fourth with the annual settlement by 16 March | verified 2026-11-10 | Agenzia, «F24 IVA Periodica - Come e quando si versa» |
| The July monthly IVA payment falls on 20 August (2026) | verified 2026-11-10 | Agenzia, Scadenzario fiscale, scadenze per Iva del 20-08-2026 |
| December IVA advance by 27 December, for the December payment (monthly) or the fourth quarter (quarterly) | verified 2026-11-10 | Agenzia, «Come pagare l'Iva», «Acconto Iva» |
| A deadline that falls on a Saturday or a holiday moves to the next working day | verified 2026-11-10 | decree-law 70/2011 art. 7 comma 1 lett. h) and l), text published by the Agenzia |
| National holidays: Sundays, 1 and 6 January, Easter Monday, 25 April, 1 May, 2 June, 15 August, 1 November, 8, 25 and 26 December; patron saints are local and excluded | verified 2026-11-10 | legge 260/1949 art. 2 as amended by legge 54/1977, DPR 792/1985 and legge 336/2000, Camera dei deputati, «Le festività nazionali» |
| LIPE: the communication of periodic settlements, its dates | to verify | |
| Deadlines in the August window (1 to 20 August) move to 20 August every year, not only in 2026 | to verify | |
| Withholding paid to the tax office by the 16th of the month after the payment | to verify | |
| A flat-rate buyer owes VAT on a reverse-charge purchase from abroad, and cannot deduct it | verified | L. 190/2014, art. 1 c. 58 lett. e-bis (pays the VAT on purchases under art. 17 DPR 633/72 «compresi quelli intracomunitari», by the 16th of the second month after each quarter; no deduction), normattiva.it, read 2026-11-10 (REB-993) |
| ECB rate reference date for a foreign-currency document | to verify | |
| TD16 integrates a domestic reverse-charge purchase | to verify | |
| Quarterly settlement surcharge of 1%, and the 100 euro below which a periodic payment is carried forward to the next month (by 16 December at the latest) | verified 2026-11-10 | Agenzia, «F24 IVA Periodica - Come e quando si versa»; Scadenzario fiscale, scadenze per Iva del 20-08-2026 |
| Eligibility thresholds for quarterly settlement: 500,000 euro of turnover for services and professionals, 800,000 for other activities, in the previous year | verified 2026-11-10 | Agenzia, «F24 IVA Periodica - Come e quando si versa» |
| Split payment is excluded for services whose fees are subject to withholding, by way of advance or as a final tax (art. 25 DPR 600/73) | verified (REB-994, 2026-11-10) | DPR 633/72 art. 17-ter c. 1-sexies, added by DL 87/2018 art. 12 (Gazzetta Ufficiale 11 August 2018), text read on Normattiva «in vigore dal 2-8-2025 al 31-12-2026»: re-read it against the VAT code that takes over from 1 January 2027 |
| Split payment applies to a sale to the public administrations of art. 1 c. 2 L. 196/2009 (a customer of kind public body) and exists only where there is VAT to split | verified (REB-994, 2026-11-10) | DPR 633/72 art. 17-ter c. 1, same text. The bodies and companies of c. 1-bis (controlled or participated companies, public economic bodies) are not a customer kind yet: a customer of kind business is never split |
| Cash-basis IVA conditions: eligibility ceiling, one-year backstop, buyer-side deduction timing, invoice annotation | to verify | |
| VAT on a retail receipt is not deductible without an invoice | verified | DPR 633/72, art. 19 c. 1 (only the tax «addebitata a titolo di rivalsa» is deductible) and art. 22 (in retail an invoice is not compulsory unless the customer asks before the operation), normattiva.it, read 2026-11-10 (REB-993). An invoice, a simplified one (art. 21-bis) included, is a purchase of kind `invoice`, not a receipt |
| 40% deductibility of VAT on a car's running costs | to verify | art. 19-bis1 DPR 633/72 |
| A service to a taxable person established abroad is not territorial in Italy and its invoice carries `N2.1` and the annotation «inversione contabile» (EU) or «operazione non soggetta» (outside the EU) | verified (REB-994, 2026-11-10) | DPR 633/72 art. 7-ter c. 1 (a service is territorial in Italy when rendered to a taxable person established there) and art. 21 c. 6-bis lett. a) and b), Normattiva; `N2.1` from the technical specifications v1.4 |
| Services to a consumer abroad (art. 7-ter c. 1 lett. b) can be taxed in Italy; the nature and the exceptions | to verify | art. 7-ter DPR 633/72; the foreign consumer has its own card |
| Reverse charge on a foreign supplier depends on the place of supply; TD17, TD18, TD19 by EU or extra-EU and goods or services | to verify | |
| IVA period date for sales: issue or operation date | to verify | art. 23 DPR 633/72 (the sales register and its date) is repealed by D.Lgs. 19 January 2026 n. 10, the IVA testo unico (normattiva.it, read 2026-11-10, REB-1011), whose new text is not read yet. The accountant bundle places a sale by its issue date and does not call it an IVA period; for REB-975's accountant list |
| A purchase invoice is annotated in the register before the periodic settlement in which the deduction is exercised, and at the latest by the annual return of the second year after the year of receipt | verified | DPR 633/72, art. 25 c. 1 and art. 19 c. 1, as amended by D.Lgs. 7 August 2026 n. 148, art. 12 c. 1 (in force 12 August 2026; the earlier text said «all'anno di ricezione della fattura e con riferimento al medesimo anno»), normattiva.it and gazzettaufficiale.it GU n. 185 of 11-8-2026, read 2026-11-10 (REB-993). The code stores a day a person chooses and defaults it to the day of receipt; it does not enforce the term |
| Which term governs a purchase invoice received before 12 August 2026 (the 2026 amendment of art. 25 c. 1 has no transitional rule in art. 12) | to verify | for the accountant list (REB-975); the IVA register card enforces the term |
| Numbering of integration documents TD16 to TD19 | to verify | |
| `FPA12` for an invoice to a public body, its 6-character IPA office code as `CodiceDestinatario` and no PEC; the Sistema di Interscambio refuses a 7-character code beside `FPA12` (and a 6-character one beside `FPR12`) | verified (REB-994, 2026-11-10) | Agenzia delle Entrate, FatturaPA technical specifications v1.4, § 1.1 and § 3.2, and «Elenco dei controlli» v1.9, control 00427 (and 00428 for the format agreeing with `versione`) |
| Deferred IVA for a public body | to verify | |
| Exempt thresholds for travel allowances, traceable payment of reimbursements | to verify | |
| ACI tables for mileage | to verify | |
| Payroll withholdings and contributions paid by F24 on the 16th of the following month | to verify | |
| Months of the 13th and 14th payments, by collective agreement | to verify | |
| TFR accrual and its payment rules (end of employment, advances, pension funds) | to verify | |
| TFM accrual and its deductibility conditions (a written deed of earlier date) | to verify | |
| INAIL yearly settlement and its dates | to verify | |
| IRPEF withholding and «gestione separata» treatment of a director's fee | to verify | |
| CBI Globe as the Italian access to banking APIs | to verify | |
| Qonto and Revolut Business CSV export formats | to verify | |
| Banco BPM business banking export format (it may be «YouBusiness Web», not «YouWeb») | to verify | |
| CAMT.053 availability per bank | to verify | |
| ECB reference rate as the source for converting balances | to verify | |
| A client may not agree a payment term past 60 days from receiving the freelancer's invoice or request for payment: such a clause is abusive and void (law 81/2017 art. 3 c. 1, in force; the text read on normattiva.it on 2026-11-10). A term counted from the document date is within it, since receipt is never earlier; 30 days counted from month end never passes it either (the longest case, 30 January, lands on 31 March, 60 days) | verified | normattiva.it, L. 22 maggio 2017 n. 81 art. 3 c. 1, checked 2026-11-10 (REB-1003) |
| INPS «gestione separata» share on occasional work above 5,000 euro a year, withheld by the payer (decree-law 269/2003 art. 44 c. 2, as clause 5.7 of the occasional framework prints it) | to verify | the hub's occasional framework text only |
| Stamp duty of 2 euro on an occasional receipt above 77.47 euro (clause 6.3 of the occasional framework) | to verify | the hub's occasional framework text only |

### A6. Not the books

PigroCRM keeps no double-entry ledger. The cash picture is exact from documents matched to
bank movements; the IVA settlement is exact from the purchase and sales registers, which
are lists of documents. The one estimate is profit-based tax: PigroCRM estimates profit
from revenue, costs, simple depreciation and deductibility per cost category, and when the
accountant gives the real figure (financial statements, tax return), that figure
replaces the estimate and the following advances are recomputed from it. The accountant
receives a clean export (C8). Double entry comes back as a question only if the gap between
estimate and real figure proves too large to plan with.

### A7. Every surface, phone first

Everything a module does is on the web app, over REST and over MCP, as the PigroCRM
initiative requires. Every new screen is drawn for the phone first. Every new surface
passes the repository's UI gate before markup: a brief approved by Lorenzo, then mockups as
an artifact, then code, then a render as proof (`docs/design/DECISIONS.md` for rules).

### A8. Every feature over MCP, and tools move with their feature

Lorenzo, 2026-11-09: everything in this document is manageable over MCP, with the
permissions of the user behind the token. Concretely, for every later project:

- **Each write a person can make in the web app has an MCP tool and a REST route**, checked
  by the same role checks the services already run (`require_write` and the admin-only
  steps that `apps/web/src/lib/permissions.ts` mirrors): a collaborator's token does what a
  collaborator does, an admin's what an admin does.
- **What cannot be undone stays behind the two existing locks**: the space's
  `mcp_full_access` ceiling and the token's own `full_access` (REB-810), as issuing and
  annulling an invoice already are. Each later design names, tool by tool, which of its new
  writes fall there (candidates: confirming a passive invoice into the register, marking an
  F24 or a supplier payment paid, closing a payroll month).
- **Accepting a proposal stays a person's act** (A4, REB-806), whatever the token.
- **A module that is off registers no tools** (A2).
- **A feature that moves between products takes its tools with it.** When matches,
  contracts and signature move from the hub to PigroCRM (C5), their hub MCP tools become
  PigroCRM tools and the hub's are removed in the same release, with no alias left behind:
  an agent never finds two tools for one action in two servers. The same holds for any
  later move in either direction.

## Part B: profiles, modules, onboarding and the mobile shell

Part B is project 1 of the eight; Part C numbers the other seven C2 to C8.

### B1. The profile

- A space setting `space_profile`: `solo`, `company`, or absent. Space settings are the
  existing key and value table merged into `Settings` (`core/space_settings/service.py`),
  which drops any key not in `OVERRIDABLE_KEYS` (`service.py:94`, `:131`): the new keys
  are declared on `Settings` as `str | None` and `bool | None`, `None` meaning absent, and
  added to `OVERRIDABLE_KEYS` (`space_settings/schemas.py:9`), or they are silently
  ignored. They are written only by `set_space_profile` and `set_space_modules`, never
  through `SpaceSettingsUpdate` (`schemas.py:26`), so the generic settings page cannot
  bypass the confirmation.
- Changed only by an admin, from «Impostazioni → Spazio», at any time. Changing it shows the
  modules the new profile would switch on and off as a list the admin confirms or edits;
  nothing switches silently, and no data is touched.
- Readable by every agent (a `describe_space` tool, or the profile added to an existing
  describing tool, the builder's call). Writable over MCP and REST by a token whose owner is
  an admin and which carries `full_access` (A8): changing the profile changes what the whole
  team sees. That needs **actions of their own** (`set_space_profile`, `set_space_modules`),
  classified in `core/agent_actions.py` as admin and full-access: the generic
  `update_space_settings` stays in `PERSON_ONLY_ACTIONS` (`agent_actions.py:206`, the
  2026-11-02 row on REB-880), so the ceiling `mcp_full_access` and every other space setting
  remain a person's act.
- The confirmation exists over MCP and REST too. `set_space_profile` answers the module
  diff and applies nothing unless the call confirms it; `set_space_modules` takes the
  explicit list of modules to switch, and refuses to switch off a module that holds data
  unless the call names that module in a confirmation field.

### B2. The modules

- One setting per module (`module_deals`, `module_contracts`, ...), `bool | None`, the same
  table. Choosing a profile (in the onboarding or later) **writes a row for every built
  module**, so the stored set is what the space sees and a later profile change is always a
  visible diff, never a reinterpretation of absent keys. An absent key means only that the
  module shipped after the space chose: it reads as the profile's default, and with no
  profile as on. A module whose default depends on an onboarding answer (`collaborators`,
  `staff`) reads as off when the answer was skipped.
- «Impostazioni» gains a «Moduli» section in the «Spazio» group, admin only: one switch per
  built module, its one-line description, and, on a module that holds data, the sentence
  «I dati restano: riaccendendolo li ritrovi».
- The MCP server rebuilds when a space's settings change (`http.py:251-266`), and space
  settings are read through a cache of `OVERRIDES_TTL_SECONDS` (10 s,
  `tenants/registry.py:31`) that REST shares. A switch therefore reaches every surface within
  ten seconds, with no restart; B8 tests that window, not the next request.
- The module switches have their MCP tools (list, switch on, switch off) under the same
  permission as the profile.
- Visibility is checked once, in one place per surface: a guard on the API routers of each
  module, the registration step of the MCP server, the navigation tables of the web app,
  the job runner. Never a scattered `if`.

### B3. The onboarding

- **When**: an admin enters a space whose profile is absent. It is a full screen with three
  steps and «Decido dopo» on each. Skipped, it leaves every module on and stays in «Primi
  passi» (`features/get-started/`) as the first item until it is done.
- **The steps**:
  1. «Come lavori?»: da solo, con partita IVA / come azienda.
  2. The regime of the first legal entity: flat-rate or ordinary for `solo`, srl for
     `company` (other forms marked «presto»). This writes the fiscal pack of the first
     legal entity, the step «Primi passi» already asks for first (2026-09-12 row). An ordinary
     `solo` maps to `it-standard` (RF01) and an srl to `it-company` (RF01 too, REB-1005),
     which invoices the same way and owes the company's own taxes (C6).
  3. For `company` only: «Lavori con freelance o collaboratori esterni?» and «Hai
     dipendenti o amministratori con un compenso?», which switch `collaborators` and
     `staff` once those modules exist. Until then the step asks nothing it cannot act on.
- **After**: the space opens on its Home for the chosen profile, with «Primi passi» showing
  what is left (fiscal identity, first customer, first contract or deal, connect an
  assistant).
- When the space already holds data (an existing space, B7), picking a profile goes through
  the same confirmation as a profile change (B1): the modules that would switch off are
  listed with how many records each holds, and the admin keeps any of them on. A module
  with data never switches off without that confirmation.
- Every answer can be changed later in Impostazioni; the onboarding never runs twice by
  itself.

### B4. Desktop navigation

The sidebar keeps its three sections (`nav.ts`), and an entry disappears when its module is
off. The order of entries per profile comes from the brief (B6). The settings groups are
untouched except for the new «Moduli» section.

### B5. Home per profile

- **solo**: what to invoice now (contract days approved and not invoiced, recurring fees
  due), when money comes in (the receivables schedule), active contracts and their days
  left, and, on the flat-rate pack, the distance to the revenue ceiling and the tax
  estimate.
- **company**: today's five dashboard tabs (`features/dashboard/search.ts`): «Economica»,
  «Commerciale», «Scadenziario», «Anno» and «Giornate», until C4 turns «Scadenziario» into
  the two-way cash schedule. «Anno» belongs to `taxes` and «Giornate» to `contracts`, and
  each hides with its module; «Economica» shows what its switched-on modules feed it.

### B6. The mobile shell

- A bottom bar with five places: «Home» (the figures), «Attività» (today, overdue, mark
  done with one gesture), «Deal» (for `solo`: «Giornate»), «+», «Altro». The third place
  shows the first switched-on of `deals` and `contracts`, the profile deciding the order;
  with both off the bar has four places.
- **+** adds without navigating: an activity, a note on a customer or deal, a worked day or
  hours on a contract, hours on a deal when `time` is on; an expense once `purchases` or
  `staff` exists.
- «Altro» lists everything else the space has switched on: customers, people, invoices,
  contracts, hours, calendar, settings.
- The desktop sidebar is unchanged in structure; the phone stops being the desktop layout
  squeezed.
- The exact layout, the gestures, and which figures Home leads with on a 390-pixel screen
  are the brief's, approved before any markup.

### B7. Existing spaces

- rebase's own space, the one `PIGROCRM_LEADS_SPACE` names (the 2026-10-30 design):
  `company`, every module of today on. `collaborators` is not a module of today: the C5
  release writes it on here (`ensure-space-defaults`), or B2 would read it as off.
- Spaces the hub provisions for freelancers: the registry spaces whose
  `rebase_engagements` row has `space_created` true (`core/engagements/service.py`).
  `solo`, with `deals` **kept on** where the space already holds the deal its match created
  (today a customer «rebase» and a deal), so nobody loses it. From this release the door
  writes `space_profile = solo` and `module_deals = on` when it creates a space; from the C5
  release it writes `space_profile = solo` and `module_contracts = on` and a contract with a
  rate card instead of a deal, and `module_deals` only for a space that already holds the old
  deal.
- Every other space: profile absent. Everything stays on and the admin sees the onboarding
  at the next login, with the confirmation of B3.
- A new space from the signup: profile absent, onboarding on the first login.
- **Where it runs**: in `pigrocrm ensure-space-defaults`, which walks every registry space
  at boot (`cli/spaces.py:84`) and today leaves the root untouched. It gains a profile step
  that visits the root too, so rebase's space gets its profile wherever it lives, and reads
  `rebase_engagements.space_created` from the registry side of the walk. It writes a profile
  only where none is stored, so it is idempotent and never overrides an admin's choice.

### B8. What is tested

The guard per surface for a module that is off (REST 404 with the sentence, MCP tool not
listed and «Unknown tool» by name, nav entry gone, job skipped) and on again with its data;
the defaults per profile and the precedence of an explicit setting over the default; the
onboarding's three exits (completed, skipped, abandoned midway); the profile change
proposing and applying a module diff; every agent reading the profile; `set_space_profile`
and `set_space_modules` refused for a token whose owner is not an admin or that lacks
`full_access`, and accepted for one that has both; `set_space_profile` answering the diff
and applying nothing without the confirmation; `set_space_modules` refusing to switch off a
module with data that the call does not name; the generic `update_space_settings` still
refused to every token; the migration of B7 on each kind of space.

### B9. Not in Part B

The modules marked «no» in A2; per-user module visibility (roles already decide what a
person may do inside a module); profiles beyond the two; a pricing or plan tier per module.

## Part C: the seven projects after this one

Each gets its own design document, its own Linear project and its own backlog, in this
order, because each uses the ones before it.

### C2. Incoming documents

Several mailboxes per space, not only Gmail: generic IMAP beside Gmail, Microsoft 365 later.
A mailbox belongs to a person or to the space and has a purpose (activities, billing; `billing` only on a space-owned mailbox);
the purpose decides what PigroCRM looks for in it. Today a mailbox is one per user and
Gmail only (`core/gmail/models.py:58`, `unique=True` on `user_id`). Manual upload of XML
and PDF invoices and of bank statements. Everything follows A4. The SdI channel is designed
here as a road, built when it is supported in full.

### C3. Suppliers, passive invoices and the IVA cycle

A supplier entity (a person or a company, with fiscal type and payment terms) replacing the
free-text `Cost.supplier`; passive invoices from every road of C2; purchase and sales
registers; periodic IVA settlement, December advance, annual return figures, credit and
offsetting, deferred and cash-basis IVA, split payment, reverse charge, partial
deductibility. Foreign invoices both ways: the right IVA nature and `XXXXXXX` recipient
code on active ones, TD17, TD18 and TD19 on passive ones, currency at the ECB rate.
Withholding, the professional fund and split payment are today named out of scope in
`fiscal/regime.py:10`; this project brings them in.

### C4. The cash schedule

The tables of A3; the existing receivables folded in; bank accounts, statements and matching;
the projected balance week by week; alerts for a gap. A due date linked to another one
(paid when paid). Banking APIs later, on the standards Italian banks expose (to verify, A5).

### C5. Collaborators, and the line with the hub

An engagement with a freelancer as a CRM object: dates, days per week, rate, project, the
letter and its signature, the payment rule (fixed term or when the client pays, decision
4), the freelancer's fiscal type deciding withholding and the net to pay (A5). Availability
visible before contacting someone for another project. The hub keeps signup, the card, the
CV and the member area, and also the marketplace pieces (talent search, talent cloud, team
requests, campaigns, referrals); the engagements door writes a contract and a rate card in a
freelancer's own space, not a deal. This is designed in
`2026-11-09-collaborators-in-the-crm-design.md`, which supersedes, in part, the `DECISIONS.md`
row of 2026-10-30 that kept the supply side in the hub.

The hub's MCP tools for that side move with it: matches, contracts, signature, hours report
and notices (today `create_match`, `preview_match`, `send_match_for_signature`,
`resend_signing_mail`, `refresh_contract`, `cancel_contract`, `cancel_match`,
`close_match`, `record_notice`, `list_matches`, `get_match`, `get_match_report`, and
`set_freelancer_tax_data`) become PigroCRM tools under the `collaborators` module, and the
hub's copies are removed in the same release (A8). `link_match_to_pigro` is removed with no
replacement, since the engagement lives in the CRM. `cancel_contract` and `record_notice` act
on a document, so they return as `cancel_framework_agreement` and `record_framework_notice`.

### C6. Taxes

Estimates and due dates per legal entity and pack: flat-rate substitute tax and INPS
advances, ordinary IRPEF and INPS, srl IRES and IRAP, IVA from C3, withholdings to pay
from C5 and C7; advances and balances on their dates; instalment plans with interest (the
June balance spread to December, notices and payment plans). Real figures from the
accountant replace estimates (A6).

### C7. Staff

Employees and directors with their compensation; payroll cost estimated from standard
coefficients (net pay, INPS and INAIL, TFR, extra monthly payments where the contract has
them, withholdings on the 16th), directors' fees with INPS gestione separata and TFM;
employee expenses with category deductibility, approval and reimbursement; travel at cost
or flat rate; mileage. PigroCRM estimates and plans, the payroll consultant computes:
payslips and paid F24s, uploaded, replace the estimate.

### C8. Export for the accountant

Registers, documents and matched movements for a period, in the formats the accountant asks
for. Designed last, when the data exists.

## Rules recorded in `docs/design/DECISIONS.md` (three rows, 2026-11-09)

- Row 1: a space is one business; a legal entity is who invoices; the profile belongs to
  the space and the regime to the legal entity. PigroCRM is shaped by a space profile and
  switchable modules; a module that is off hides everywhere and keeps its data.
- Row 2: PigroCRM is not the books: no double entry, estimates replaced by the accountant's
  figures. What a model reads is a proposal; structured, checkable documents are certain
  data.
- Row 3: every feature is manageable over MCP with the user's permissions; a feature that
  moves between products moves its MCP tools and removes the old ones in the same release.

## Cards

REB-952 this record. «Switch modules on and off for each space»: REB-953 the profile, the
module switches and the guard on every surface · REB-954 the Moduli section · REB-955 the
profile of existing spaces. «Ask each new space how it works»: REB-956 the brief for the
onboarding, the Home per profile and the phone shell · REB-957 the onboarding · REB-958 the
Home per profile. «Make the CRM work on the phone first»: REB-959 the phone shell.
