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
| Conditions of the forecast method: interest and penalty do not apply to an advance that turns out short when (a) an omitted instalment's tax per the current year's return, net of deductions, credits and withholdings, is not above 100,000 lire (€51.65) for IRPEF or 40,000 lire (€20.66) for IRES and IRAP, (b) the first instalment paid is at least 40% of what the current year's return would make it, (c) for the second instalment, the first alone or both together reach what the current year's return would make the whole advance | verified 2026-11-11 (REB-1067) | art. 4 c. 2 D.L. 69/1989 (L. 154/1989), Normattiva, read 2026-11-11; Agenzia, Redditi PF 2026 fascicolo 1 rigo RN62 and IRAP 2026 istruzioni § 5.2 («ferma restando, in tal caso, l'applicazione, ai fini del regime sanzionatorio, delle disposizioni dell'articolo 4, comma 2, del D.L. n. 69 del 1989»). The article says «interessi e sopratassa»: the sopratassa is today the penalty below |
| Penalty on an advance, a balance or any payment not made in full: 25% of the amount not paid; half of that (12.5%) for a delay of up to 90 days; for a delay of up to 15 days, one fifteenth of that half for each day. Violations from 1 September 2024 | verified 2026-11-11 (REB-1067) | art. 13 c. 1 D.Lgs. 471/1997 as replaced by D.Lgs. 87/2024 (art. 5 c. 1: violations from 1 September 2024), Normattiva, read 2026-11-11 |
| Interest on a late or short payment that the taxpayer regularises: the legal rate, day by day; 1.60% a year from 1 January 2026 (2% in 2025) | verified 2026-11-11 (REB-1067) | art. 13 c. 2 D.Lgs. 472/1997, Normattiva, read 2026-11-11; D.M. 10 December 2025 (MEF), GU n. 289 of 13-12-2025, art. 1 |
| The further reductions of the penalty by ravvedimento (art. 13 D.Lgs. 472/1997), and the interest on a shortfall found by the office (art. 36-bis D.P.R. 600/1973) instead of regularised by the taxpayer | to verify | not in the pack; for REB-975's accountant list |
| Income-tax dates: 30 June for the balance and the first advance (or within 30 days with 0.40%), 30 November for the second or only advance, for a person | verified 2026-11-10 | Agenzia, «Imposte sui redditi (Irpef, Ires)» (updated 30 September 2026), «I versamenti delle persone fisiche» |
| Flat-rate substitute tax: balance and first advance on 30 June, second or only advance on 30 November (2026) | verified 2026-11-10 | Agenzia, Scadenzario fiscale, «Regime forfetario agevolato: versamento imposta sostitutiva» (30 June and 30 November 2026) |
| Advances of the flat-rate regime's substitute tax: the payment rules are those of personal income tax (L. 190/2014 art. 1 c. 64, last sentence); 100% of the tax declared for the previous year, no advance at or below €51.65; two instalments of 50% each, on 30 June and 30 November, and a single payment by 30 November when the total does not exceed €206; 0.40% for paying within 30 days after a deadline | verified 2026-11-11 (REB-1066), from tax year 2026 | Agenzia, «Come si paga l'Irpef» (updated 12-3-2026): «I soggetti che devono applicare gli Indici sintetici di affidabilità (Isa) e i soggetti che rientrano nel regime forfetario effettuano un unico versamento entro il 30 novembre se l'importo totale dovuto non supera 206 euro; l'acconto in due rate di pari importo entro le stesse scadenze previste per gli altri contribuenti (30 giugno e 30 novembre)»; art. 58 D.L. 124/2019 («in due rate ciascuna nella misura del 50 per cento», Normattiva). The 100% base and the €51.65 threshold are stated on the same page for Irpef and reach the substitute tax by c. 64's reference, not by a sentence about it |
| Instalment plan (rateazione) of the flat-rate regime's June balance and first advance | to verify | The Agenzia's scadenzario says «in unica soluzione o come prima rata» and no source read states the plan for the substitute tax itself; the pack's `payment_rules` (REB-1067) applies the personal instalment rule to it through c. 64's reference to the payment rules for personal income tax; for REB-975 |
| Instalment rule of an srl: the balance and the first advance of IRES and IRAP may be paid in monthly instalments, each on the 16th (20 August when it falls from 1 to 20 August), by 16 December of the year the return is filed; the second or only advance may not | verified 2026-11-11 (REB-1067) | Agenzia, Redditi SC 2026 «Parte generale» § 4.4 («Sono esclusi dalla rateazione solo gli importi a titolo di seconda o unica rata di acconto IRES»); IRAP 2026 istruzioni § 5 «Rateazione»; art. 20 c. 1 and 4 D.Lgs. 241/1997 (art. 10 D.Lgs. 33/2025 from 1 January 2027), Normattiva and GU n. 71 of 26-3-2025, read 2026-11-11 |
| Interest on instalments, for a person and an srl: 4% a year by the commercial method from the day after the first instalment's due day to the second's, then 0.33% for each later instalment; the Agenzia's table for a first payment on 30 June 2026 is 0.00, 0.18, 0.51, 0.84, 1.17, 1.50, 1.83 | verified 2026-11-11 (REB-1067) | D.M. 21 May 2009 art. 5 c. 1 (GU n. 136 of 15-6-2009); Agenzia, Redditi PF 2026 fascicolo 1 «Rateazione», Redditi SC 2026 «Parte generale» § 4.4 and IRAP 2026 istruzioni § 5 (same table) |
| The last instalment of an srl whose fiscal year is not the calendar year: the law says 16 December «dello stesso anno di presentazione della dichiarazione», which for a year ending 30 June 2026 (balance due 31 December 2026, return filed by 30 April 2027) reads as 16 December 2027, and no page says so. And how the commercial method counts the second instalment's interest when the first payment falls on 28 or 29 February (a year ending 31 August): the Agenzia's tables start on the 30th only | to verify | the pack's `instalment_schedule` takes the filing year from its caller and counts a start on the last day of February by its own number; for REB-975's accountant list |
| IRES and IRAP of a company whose fiscal year is the calendar year and whose financial statements are approved within 120 days: balance and first advance by 30 June, second or only advance by 30 November (2026); IRAP on the same two days (the instalment split and the €103 threshold are the rows above and not in the calendar) | verified 2026-11-10 (REB-1033) | Agenzia, «Imposte sui redditi (Irpef, Ires)» (updated 30 September 2026), società di capitali; Scadenzario fiscale, «Soggetti Ires: versamento saldo 2025 e primo acconto 2026 dell'Ires» and «... dell'Irap» (30 June 2026); Fisco Oggi, Scadenzario, «Versamenti da dichiarazioni – Acconto 2026» (30 November 2026; Ires and Irap codes 2002 and 3813). The 30 June date is the ordinary term of a company that approves its financial statements within 120 days; the Agenzia's item itself adds that ISA subjects under the revenue limit pay by 20 July 2026 without surcharge (decree-law 63/2026 art. 1-sexies, L. 113/2026), a decree postponement that the dates' «salvo proroghe» covers as it does the persone fisiche's 30 June. A later approval of the financial statements (31 July with 180 days) and a year end other than 31 December are not modelled: the pack lists the dates for a calendar-year company only |
| 5% substitute rate on start-up conditions, 15% otherwise | verified 2026-11-11 (REB-1066) | L. 190/2014 art. 1 c. 64 («un'imposta sostitutiva dell'imposta sui redditi, delle addizionali regionali e comunali e dell'imposta regionale sulle attività produttive [...] pari al 15 per cento») and c. 65 as in force from 1-1-2016, art. 1 c. 111 L. 208/2015 («per il periodo d'imposta in cui l'attività è iniziata e per i quattro successivi, l'aliquota di cui al comma 64 è stabilita nella misura del 5 per cento, a condizione che» a) no artistic, professional or business activity in the three years before, b) no mere continuation of earlier work as an employee or self-employed, c) a continued activity's revenue within the c. 54 limit), Normattiva; Agenzia, Redditi PF 2026 instructions, quadro LM |
| Start-up relief in tax year 2015: not a 5% rate, an income reduced by a third for the year of start and the two after it, at 15% | verified 2026-11-11 (REB-1066) | L. 190/2014 art. 1 c. 65 as in force from 1-1-2015 to 31-12-2015: «per il periodo d'imposta in cui l'attività è iniziata e per i due successivi, il reddito determinato ai sensi del comma 64 è ridotto di un terzo», Normattiva. This is why the pack has an entry for 2015 and one from 2016 |
| Profitability coefficients: annex 4 of L. 190/2014, nine groups of ATECO 2007 codes: 40% (food and beverage industries; wholesale and retail trade; street trade in food and beverages; accommodation and food service), 54% (street trade in other products), 62% (trade intermediaries), 67% (other economic activities), 78% (professional, scientific, technical, health, education, finance and insurance), 86% (construction and real estate) | verified 2026-11-11 (REB-1066) | Normattiva, L. 190/2014 «Allegato 4» «(articolo 1, comma 64) (Regime forfetario dei contribuenti minimi)», text in force from 1-1-2019, and the texts in force from 1-1-2015 and 1-1-2016: same coefficients and same code lists in all three (the earlier two add turnover thresholds the 2019 text dropped); Agenzia, Redditi PF 2026 instructions, quadro LM, the same table |
| Profitability coefficients keep the ATECO 2007 classification until new ones are approved on ATECO 2025 | verified 2026-11-11 (REB-1066) | D.Lgs. 12 giugno 2025 n. 81 art. 1 c. 1: «Fino alla approvazione dei nuovi coefficienti di redditività elaborati sulla base della classificazione delle attività economiche ATECO 2025, i soggetti di cui all'articolo 1, comma 54, [...] continuano a determinare il reddito imponibile applicando [...] il coefficiente di redditività previsto nell'allegato 4 [...] secondo la classificazione ATECO 2007» (Normattiva); same in the Agenzia's Redditi PF 2026 instructions. No approval of ATECO 2025 coefficients was found on 2026-11-11: re-read before the 2027 return |
| Flat-rate contribution rule: INPS contributions are deducted from the flat-rate income before the substitute tax rate applies; they are owed on that income, not on the income less the substitute tax | verified 2026-11-11 (REB-1066) | L. 190/2014 art. 1 c. 64: «I contributi previdenziali versati in ottemperanza a disposizioni di legge [...] si deducono dal reddito determinato ai sensi del presente comma; l'eventuale eccedenza è deducibile dal reddito complessivo». Agenzia, Redditi PF 2026 instructions, quadro LM: row LM35 «l'ammontare dei contributi previdenziali e assistenziali versati nel presente periodo d'imposta in ottemperanza a disposizioni di legge. Dal reddito così determinato, si devono dedurre per intero i contributi previdenziali», LM36 «la differenza tra l'importo di rigo LM34, colonna 3, e l'importo di rigo LM35, colonna 2», LM39 the substitute tax «pari al 15 per cento (ovvero pari al 5 per cento [...]) dell'importo di rigo LM38». The gross flat-rate income is the contribution base per fund (LM34 columns 1 and 2; art. 1 c. 77 for artigiani and commercianti: «Il reddito forfettario determinato ai sensi dei precedenti commi costituisce base imponibile»; INPS circolare 27 of 30-1-2025 for the gestione separata: the rate applies to the income «determinato con gli stessi criteri stabiliti ai fini dell'individuazione della base imponibile dell'imposta sul reddito delle persone fisiche»). The code before REB-1066 took the contributions on the income less the substitute tax, which no source supports: the total to set aside was the same, the split was not |
| The contributions deducted are those paid in the tax period (the balance of the year before and the advances of the year), not those accrued by it | verified 2026-11-11 (REB-1066); the estimate approximates it | Agenzia, Redditi PF 2026 instructions, row LM35, same quotation. The estimate deducts the year's own computed contributions, which an accountant's declared figure replaces; for REB-975 if the difference matters |
| The flat-rate regime exists from 1 January 2015 (L. 190/2014, art. 1, commi 54-89); the income estimate's arithmetic is the same in every year except the start-up relief (the 2015 row above) | verified 2026-11-11 (REB-1066) | Normattiva, L. 190/2014 art. 1 c. 54-89, «Entrata in vigore del provvedimento: 1/1/2015», and the texts of c. 64 in force from 1-1-2015, 1-1-2016 and today: the same wording |
| «Gestione separata» 2026 (a professional with no fund and a VAT position): rate 26.07% (25.00 IVS + 0.72 + 0.35 ISCRO) with no other mandatory insurance and no pension, 24% for a pensioner or a person insured elsewhere; ceiling («massimale») 122,295.00; minimum for crediting the whole year 18,808.00 (contribution 4,903.25 at 26.07%, 4,513.92 at 24%): no floor of what is owed | verified 2026-11-11 (REB-1064) | INPS, circolare n. 8 of 3 February 2026, § 2 («26,07% (25,00 IVS + 0,72 aliquota aggiuntiva + 0,35 ISCRO)»; «24%»), § 6.1 («il massimale [...] è pari a 122.295,00 euro»), § 6.2 («18.808,00 euro»; «4.903,25 euro (di cui 4.702,00 euro ai fini pensionistici)»; «4.513,92 euro»). In `fiscal/law_inps.py`, keyed by year |
| «Artigiani e commercianti» 2026: minimum income 18,808.00; IVS 24% (artisans) and 24.48% (traders, with the 0.48 point of the indemnity fund) up to 56,224.00 and 25% / 25.48% above; ceiling 93,707.00 (insured by 31 December 1995) or 122,295.00 (everyone else); maternity 0.62 a month (7.44 a year); contribution on the minimum 4,521.36 (artisans) and 4,611.64 (traders); maximum IVS 22,864.51 / 23,314.31 and 30,011.51 / 30,598.53, each band rounded to the cent | verified 2026-11-11 (REB-1064) | INPS, circolare n. 14 of 9 February 2026, § 1 (the rate table), § 2 («18.808,00 euro»; «€ 4.521,36 (4.513,92 IVS + 7,44 maternità)», «€ 4611,64»), § 3 («fino al limite della prima fascia [...] 56.224,00 euro»; «aumento dell'aliquota di un punto percentuale»), § 4 («93.707,00 euro (56.224,00 euro più 37.483,00 euro)»; «122.295,00 euro»; the four maxima) |
| Quarterly dates of the fixed part of an artisan or trader (the minimum): 18 May 2026, 20 August 2026, 16 November 2026, 16 February 2027; the part above the minimum follows the income tax (30 June and 30 November 2026 for the balance of 2025 and the advances of 2026; 0.40% surcharge up to 30 July; instalments up to 16 December; the instalments cover only what is above the minimum) | verified 2026-11-11 (REB-1064) | INPS, circolare n. 14/2026 § 9 («18 maggio 2026, 20 agosto 2026, 16 novembre 2026 e 16 febbraio 2027, per il versamento delle quattro rate dei contributi dovuti sul minimale di reddito»); circolare n. 62 of 27 May 2026, §§ 3 and 4 («entro il 30 giugno 2026 [...] saldo 2025 e primo acconto 2026 ed entro il 30 novembre 2026 per il secondo acconto 2026», «entro la data del 16 dicembre 2026», «maggiorazione dello 0,40%», «esclusione [...] dei contributi dovuti sul minimale»). The year's dates only: a later year has its own circular and no entry yet |
| Flat-rate regime's INPS reduction for artisans and traders: 35% of the contribution due on the flat-rate income, by request, with no minimum income and no fixed part, paid at the income tax's dates, the maternity contribution (7.44) in two equal shares at the advances | verified 2026-11-11 (REB-1064) | L. 190/2014 art. 1 c. 77 («Su tale reddito si applica la contribuzione dovuta ai fini previdenziali, ridotta del 35 per cento»), Normattiva text in force on 11 November 2026; INPS, circolare n. 29 of 10 February 2015, § 1 («senza applicazione del livello minimo imponibile [...] Pertanto il contribuente non è obbligato a versare la c.d. quota fissa»), which circolare n. 14/2026 § 8 still refers to for the regime's rules; the 2026 application is circolare n. 14/2026 § 8 («riduzione contributiva del 35%»). The gestione separata has no such reduction |
| How the 35% reduction rounds, whether it also reduces the 0.48 point of the traders' indemnity fund (the pack reduces the whole contribution due, once, to the cent), and the advance split of these contributions (the percentage each advance is) | to verify | the circulars read print no worked example and no split; for the accountant list (REB-975) |
| The 50% reduction of an artisan or trader over 65 who is a pensioner, and of a first enrolment in 2025 (art. 59 c. 15 L. 449/1997; art. 1 c. 186 L. 207/2024); which gestione an `artigiani_commercianti` profile is (artisans or traders), whether a gestione separata professional is insured elsewhere, and whether an artisan or trader had contributions on 31 December 1995 | stated by the circular, not modelled | circolare 14/2026 § 1 states the reductions; the profile does not carry age, history, gestione or other insurance, so the pack's lookup takes them as arguments (REB-1064) |
| IRES rate: 24% of the net total income | verified | D.P.R. 917/1986 (TUIR), art. 77 c. 1, Normattiva, read 2026-11-10 (REB-1005) |
| IRAP ordinary rate: 3.90% of the net value of production; a region may vary it by up to 0.92 points (art. 16 c. 3); other rates apply to the subjects of c. 1-bis and to those the D.L. 20 February 2026 n. 21 names. Normattiva's printed text of art. 16 c. 1 reads 3,50 under notes that say D.L. 66/2014's reduction was repealed by L. 190/2014 art. 1 c. 22, so it is not the rate | verified | Dipartimento delle Finanze, «IRAP: disciplina del tributo» (3,90% and the 0.92 points), and the regions' 2026 rate tables (Emilia-Romagna: 3,90 ordinary), read 2026-11-10 (REB-1005) |
| IRAP base of a company that is not a bank, a financial company or an insurer: the difference between sections A and B of art. 2425 c.c., leaving out items B9 (staff cost), B10 c and d, B12 and B13 and the extraordinary components of a transfer of a business; staff cost booked in other items, losses on receivables, IMU and the interest part of leasing instalments are not deductible | verified 2026-11-11 (REB-1065) | D.Lgs. 446/1997 art. 5 c. 1 and c. 3, Normattiva (act updated 11 August 2026); Agenzia delle Entrate, «Istruzioni IRAP 2026», quadro IC section I (rows IC1 to IC14). PigroCRM keeps no ledger (A6): the estimate starts from revenue, purchases and staff cost, and `fiscal/law_irap.py` names that simplification |
| IRAP deductions: the whole cost of permanent employees and up to 70% of that of seasonal workers (art. 11 c. 4-octies); 1,850 euro for each employee who is not permanent, up to five, if the positive components do not exceed 400,000 euro (c. 4-bis.1); 8,000, 6,000, 4,000 or 2,000 euro up to the base itself if the base does not exceed 180,759.91, 180,839.91, 180,919.91 or 180,999.91 (c. 4-bis, applied to the value net of the other deductions) | verified 2026-11-11 (REB-1065) | D.Lgs. 446/1997 art. 11 c. 4-octies, 4-bis, 4-bis.1, 4-bis.2 and 4-septies, Normattiva; Agenzia, «Istruzioni IRAP 2026», rows IC65 to IC76 (order of the deductions and the 180,999.91 limit). Not modelled: pro-rating of a period that is not twelve months and of part-time work, the INAIL, apprentice, disabled, training-contract and research-staff deductions of c. 1 lett. a, the cap on the amortisation of trademarks and goodwill and the property gains and losses of art. 5 c. 3 |
| IRAP territory: a company with one site pays the rate of the region of its registered office; with sites in several regions the value is split by the pay of the staff assigned to each for at least three months, and activity carried on elsewhere without staff for that time goes to the region of the domicile | verified 2026-11-11 (REB-1065) | D.Lgs. 446/1997 art. 4 c. 1 and c. 2, Normattiva. The split needs the pay of each region, which PigroCRM does not hold: the estimate assumes one site |
| IRAP ordinary rate of a company in 2026 by region, in percent: Piemonte, Valle d'Aosta, Lombardia, Bolzano, Friuli-Venezia Giulia, Liguria, Emilia-Romagna, Toscana, Basilicata, Sicilia 3.90; Trento 2.68; Veneto 4.08; Umbria 4.30; Marche 4.73; Lazio, Abruzzo, Puglia, Calabria 4.82; Molise, Campania 4.97; Sardegna 2.93 | verified 2026-11-11 (REB-1065) | Dipartimento delle Finanze, «Fiscalità regionale e locale - IRAP - Aliquote applicabili», «Elenchi generali», file `irp2026.csv` (publications in force, the latest dated 12 October 2026): the region's row «Aliquota ordinaria» (code 001; for Veneto code 001 is «Maggiorazione di aliquota generalizzata», 4.08, and the dataset has no row called ordinary; for Trento code 001 is the reduced 2.68). Each row's publication id and date are in `fiscal/law_irap.py` |
| IRAP rates of a subject other than an ordinary company (concessionaires, banks and financial companies, insurers, the activities of art. 3 c. 1 D.L. 21/2026) and regional reductions that depend on the company (new, young or women founders, small municipalities, collective agreements, small and medium companies of Sicilia, companies of Friuli-Venezia Giulia with up to five employees, Bolzano and Trento agreements) | not modelled | the 2026 dataset above lists them per region; the estimate uses the ordinary rate and says so. For the accountant list (REB-975) |
| The denomination of an srl contains «società a responsabilità limitata» | verified | Codice civile art. 2463 n. 2, Gazzetta Ufficiale text, read 2026-11-10 (REB-1005) |
| Municipal IRPEF surcharge; the IRAP region rules are the four IRAP rows above, verified | to verify | |
| IRPEF brackets for tax year 2026: 23% up to 28,000 euro, 33% from 28,000 to 50,000, 43% above, applied by slice to the income net of deductible charges | verified 2026-11-11 (REB-1062) | D.P.R. 917/1986 (TUIR) art. 11 c. 1, Normattiva text in force on 2026-11-11 («a) fino a 28.000 euro, 23 per cento; b) oltre 28.000 euro e fino a 50.000 euro, 33 per cento; c) oltre 50.000 euro, 43 per cento»); letter b) is 33 by L. 199/2025 art. 1 c. 3 («le parole: «35 per cento» sono sostituite dalle seguenti: «33 per cento»»); Agenzia, «Aliquote e calcolo dell'Irpef» (updated 13 January 2026): 13,700 euro plus 43% of the excess over 50,000. The 2025 brackets (35% in the second) are not in the pack: its other 2025 sections are not verified |
| For an income above 200,000 euro the detractions at 19% (health expenses apart), the donations to political parties and the premiums against natural disasters fall by 440 euro, a cut of the detractions and not a bracket | verified 2026-11-11 (REB-1062) | L. 199/2025 art. 1 c. 4 (new art. 16-ter c. 5-bis TUIR), Normattiva; Agenzia, «Aliquote e calcolo dell'Irpef». Not in the pack's brackets; the estimate card computes detractions |
| Regional IRPEF surcharge for 2026, per region and autonomous province: the rates by bracket (15,000, 28,000, 50,000), how each region applies them (by slice, or on the whole income in Friuli Venezia Giulia; Valle d'Aosta has one rate and an exemption up to 15,000), the income thresholds and detractions that depend on income alone (Lazio, Umbria, Trento, Valle d'Aosta, Bolzano) | verified 2026-11-11 (REB-1062) | Dipartimento delle Finanze, «Addizionale regionale all'IRPEF», aliquote applicabili, the 21 pages (published 19 January to 19 June 2026), and «Disciplina del tributo» (rates follow the IRPEF brackets and rise with them: D.Lgs. 68/2011 art. 6; L. 207/2024 art. 1 c. 727-728 as amended by L. 199/2025 art. 1 c. 649). Molise and Puglia carry two versions, the later (19 June and 29 May 2026) is entered. Keys and per-region sources in `fiscal/irpef_2026.py` |
| The surcharge is due only if the IRPEF net of detractions is due; the flat-rate regime is excluded; the base is the income net of deductible charges; due to the region of the tax domicile on 1 January | verified 2026-11-11 (REB-1062) | Finanze, «Disciplina del tributo» (art. 50 D.Lgs. 446/1997; L. 190/2014 art. 1 c. 54-89). The lookup does not test the first two: the estimate card does |
| Regional detractions that depend on the household or a disability (per child, per disabled child, reduced rates: Piemonte, Veneto, Marche, Campania, Puglia, Sardegna, Trento, Bolzano) | to verify | Not computed (the profile holds no household); written as text per region in `fiscal/irpef_2026.py`. For the accountant list (REB-975) |
| Bolzano: the detraction of 125 euro times (income minus 50,000) over 25,000, at most 125 euro, for an income above 50,000 | to verify | The Finanze page prints it so, a formula that grows with income, and no second source was read; not computed. For REB-975 |
| The list of Italian municipalities with their ISTAT code (six digits), cadastral code, province abbreviation and region (7,894 municipalities from 21 February 2026), and that a province belongs to exactly one region | verified 2026-11-11 (REB-1069) | ISTAT, «Codici delle unità amministrative» (istat.it/classificazione/codici-dei-comuni-delle-province-e-delle-regioni, «Aggiornamento al 21 febbraio 2026», 7,894 municipalities), the «Permalink aggiornato» workbook `Elenco-comuni-italiani.xlsx`, sheet «CODICI al 21_02_2026» (last modified 26 February 2026), downloaded 2026-11-11. The `.csv` at the same address is a frozen copy of January 2024 (it still has the pre-2026 Sardinian provinces and the two municipalities merged into Castegnero Nanto) and is not used. Rebuilt by `projects/pigrocrm/scripts/import_istat_municipalities.py` into `core/places/datasets/`. A legal entity's region and municipality are read from its city and province against this list (REB-1069), never stored; a Sardinian address with a retired province code (SU) matches nothing until it is rewritten with a current one |
| Annual chamber of commerce fee: the date | verified 2026-11-10 (REB-1033) | art. 17 c. 3 lett. a DPR 435/2001, as the Camere di commercio's «Termini di versamento» (Brindisi Taranto, updated 11 August 2026) and dirittoannuale.camcom.it («Scadenza ordinaria 30/06/2026»; «La scadenza del pagamento segue sempre il primo acconto delle imposte sui redditi») say: in one payment on the day of the first income-tax advance, plus 0.40% within 30 days; a company with another year end or a late approval follows that advance's date. The 2026 postponement to 20 July for ISA subjects is by decree-law 63/2026 art. 1-sexies (L. 113/2026) |
| Annual chamber of commerce fee of an srl, 2026: the turnover of the year before (2025) through the tiers of D.I. 21 April 2011 (200 euro up to 100,000; then 0.015% to 250,000, 0.013% to 500,000, 0.010% to 1,000,000, 0.009% to 10,000,000, 0.005% to 35,000,000, 0.003% to 50,000,000, 0.001% above; ceiling 40,000), cut by 50% (art. 28 c. 1 D.L. 90/2014: 100 euro up to 100,000 of turnover, at most 20,000), plus the chamber's increase, rounded once at the end to the cent and the euro, half up, keeping five decimals between steps (the note says it for the tier sum, the chambers' guides for the intermediate results; keeping them after the cut and the increase is an interpretation that matters only within 0.00001 of a cent boundary) | verified 2026-11-11 (REB-1068) | Ministero delle Imprese e del Made in Italy, nota n. 9347 of 16 January 2026, «Misure del diritto annuale anno 2026» (tiers, cut, sequence and rounding of the MISE circular n. 19230 of 2009); checked against the amounts the chambers of Firenze, Brindisi-Taranto and Sud Est Sicilia print (120, 120 and 170 euro for the first tier) |
| Annual chamber of commerce fee 2026: 61 chambers raise it by 20% (art. 18 c. 10 L. 580/1993), listed in Allegato A of the D.M. 17 March 2026 (in force 28 April 2026, for 2026, 2027 and 2028), and the six Sicilian chambers (Agrigento, Caltanissetta, Messina, Palermo-Enna, Sud Est Sicilia, Trapani) add 50% (art. 1 c. 784 L. 205/2017, D.M. 2 May 2025 art. 1 c. 1, for 2025 to 2027), the two adding up to 70% of the cut amount; Napoli applies none | verified 2026-11-11 (REB-1068) | Ministero delle Imprese e del Made in Italy, D.M. 17 March 2026 and Allegato A (read as the published image: «TOTALE 61 CAMERE di COMMERCIO», each at 20%); D.M. 2 May 2025; Camera di commercio di Napoli, «Diritto annuale 2026» (resolution n. 10 of 30 October 2025: no increase); Camera di commercio del Sud Est Sicilia, «Diritto Annuale 2026» (170 euro, reduced by 50% and increased by 20% and 50%) |
| Which chamber covers a province (Unioncamere's list of the chambers and their provinces, 62 chambers, 105 provinces) | verified 2026-11-11 (REB-1068) | Unioncamere, «Elenco delle Camere di commercio italiane» (page undated, read 2026-11-11) |
| Which chamber covers the province of Sud Sardegna (SU) | to verify | Cagliari-Oristano's «Chi siamo» names its four territories (Cagliari, Medio Campidano, Sulcis Iglesiente, Oristano) and the Unioncamere list has no other chamber for it, but no source names SU: the pack has no figure for SU. For the accountant list (REB-975) |
| Which chamber covers the province of Barletta-Andria-Trani (BT) | to verify | no primary source read says it (the Unioncamere list has no chamber for it, Bari has an office in Barletta): the pack has no figure for BT. For the accountant list (REB-975) |
| The 2027 and 2028 amounts of the chamber fee and increases | to verify | the 20% is authorised to 2028 and the Sicilian 50% to 2027 (and D.M. 2 May 2025 art. 1 c. 2 revokes it from the year after the new Sicilian chambers are constituted), but the Ministry's note for each year is read when it is published; the lookup answers nothing for a year other than 2026 |
| Cash or accrual basis of the ordinary person's and the srl's income: a person's income from an art or a profession is cash; an srl's is accrual | verified 2026-11-11 (REB-1066) | TUIR art. 54 c. 1 («la differenza tra tutte le somme e i valori in genere a qualunque titolo percepiti nel periodo di imposta [...] e l'ammontare delle spese sostenute nel periodo stesso»; Normattiva, in force from 12-8-2026); art. 83 c. 1 («all'utile o alla perdita risultante dal conto economico, relativo all'esercizio chiuso nel periodo d'imposta») and art. 109 c. 1 («concorrono a formare il reddito nell'esercizio di competenza»; Normattiva, in force from 4-7-2026); for IRAP, art. 5 c. 1 D.Lgs. 446/1997 (the difference between the value and the costs of production of letters A) and B) of art. 2425 of the civil code, from the income statement). The pack reads an ordinary-regime person as one who practises an art or a profession |
| An ordinary-regime person whose income is business income: cash under simplified accounting (art. 66 c. 1 TUIR, «percepiti nel periodo d'imposta»), accrual under ordinary accounting (art. 56 c. 1 sends it to art. 109) | verified 2026-11-11 (REB-1066) for the law; to verify for what the product does | Normattiva, TUIR art. 66 c. 1 (in force from 1-1-2017) and art. 56 c. 1. The fiscal profile does not say whether the person is a professional or runs a business, or which accounting applies, so the pack states the cash basis of art. 54 and does not guess the others: for REB-975 |
| Flat-rate basis: revenue and fees collected, whether the income is a profession's or a business's (cash principle) | verified 2026-11-11 (REB-1066) | L. 190/2014 art. 1 c. 64 («ammontare dei ricavi o dei compensi percepiti»); Agenzia, Redditi PF 2026 instructions: «i ricavi ed i compensi vengono imputati al periodo d'imposta sulla base del cd. "principio di cassa" [...] tanto in caso di reddito derivante dall'esercizio di arti o professione, quanto in caso di reddito d'impresa»; Agenzia, «I quadri LM, RR, RU, RP, RL» («ai regimi di vantaggio e forfetario si applica il principio di cassa»). The code still sums issued invoices; the one-basis revenue function is REB-1071 |
| Flat-rate basis: whether the INPS surcharge lines (the 4% «rivalsa») are in or out of the collected revenue | to verify | The Agenzia's guide to the quadro LM (a statement about the precompiled return, not a statute) says a professional bound to a professional fund has the contributions charged to the client in the invoice excluded from the income components, and a professional in the gestione separata has them included («i contributi addebitati al committente in fattura sono inclusi nell'importo dei componenti positivi di reddito precompilati»). The code takes the surcharge lines out for everybody (`count_by_the_coefficient`). For REB-1071 and REB-975 |
| Year in which a withholding suffered counts as a credit, for a natural person: the tax year the income is taxed in (a professional's, the year it is collected, art. 54 c. 1 TUIR); one operated in the year after and before the return is filed may go to either year; one operated after the return is filed goes to the year it was operated | verified 2026-11-11 (REB-1067) | art. 22 c. 1 lett. c) TUIR, Normattiva, read 2026-11-11; art. 24 c. 1 lett. c) of the testo unico of D.Lgs. 19 June 2026 n. 117 (GU n. 152 of 3-7-2026), which replaces it from 1 January 2027 in the same words |
| The same year rule for the withholdings an srl suffers (IRES) | to verify | not in the pack; for REB-975's accountant list |
| Legal treatment of a declared figure that changes an advance already settled | to verify | |
| F24 tax codes per line: IRPEF 4001 balance, 4033 first advance, 4034 second or only advance; regional surcharge 3801; municipal surcharge 3844 balance and 3843 advance; flat-rate substitute tax 1792 balance, 1790 and 1791 advances; IRES 2003 balance, 2001 and 2002; IRAP 3800 balance, 3812 and 3813; chamber fee 3850; interest on instalments 1668 (Erario), 3805 (regional taxes), 3857 (municipal surcharge; 3804 abrogated from 1 January 2008). The year column is the tax year the payment is for, the instalment column «NNRR»; the 0.40% of a deferral is added to the tax's own line | verified 2026-11-11 (REB-1067) | Agenzia delle Entrate, «Ricerca codici tributo» (each code) and Redditi PF 2026 fascicolo 1 «Principali codici tributo», rigo RN38 and «Rateazione»; risoluzioni 368/E of 12 December 2007, 59/E of 11 June 2015, 76/E of 27 May 2004 and 46 of 10 April 2001 |
| INPS causali: gestione separata PXX (P10 for a professional who is retired or insured elsewhere), PXXR and P10R for an instalment, DPPI for the 0.4% of a deferral and the interest of an instalment, a line of its own; artigiani AF and commercianti CF on the minimum income, AP and CP above it, APR and CPR for an instalment of that | verified 2026-11-11 (REB-1067) | INPS, «F24 per professionisti iscritti alla Gestione Separata» and «F24 per artigiani e commercianti» (both updated 8 July 2025), «Causali di versamento» |
| The causale for the interest of an instalment of artigiani and commercianti contributions | to verify | INPS's page gives none; for REB-975's accountant list |
| Filing date of the income-tax returns, online: Redditi PF by 31 October of the year after the tax year (2 November 2026, 31 October being a Saturday); Redditi SC and the IRAP return of a company by the last day of the tenth month after the period closes (2 November 2026 for a calendar-year company, 30 April 2027 for 1 July 2025 - 30 June 2026) | verified 2026-11-11 (REB-1067) | art. 2 c. 1 and 2 D.P.R. 322/1998, Normattiva, read 2026-11-11; Agenzia, Redditi PF 2026 fascicolo 1 § 6, «Quando e come si presenta il modello Redditi SC», IRAP 2026 istruzioni § 3.1 and «Come e quando si presenta l'Irap 2026» |
| The filing date of the IRAP return of a natural person | to verify | the Agenzia's IRAP page and instructions give the dates of partnerships and IRES subjects only; not in the pack; for REB-975's accountant list |
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
| Withholding on pay to others (professionals, code 1040) is paid to the tax office by the 16th of the month after the payment, moved to the next working day when it falls on a Saturday or a holiday; the scadenzario lists the family of withholding payments of July's operations on 20 August 2026, as it does IVA | verified 2026-11-10 (REB-1033) | Agenzia, «F24 Ritenute reddito lavoro autonomo - Come e quando si versa»; Scadenzario fiscale, «Sostituti d'imposta: versamento ritenute operate nel mese precedente» (20 August 2026). The scadenzario item for code 1040 (20 August 2026) lists it with the same 20 August date, and adds that a payment of €100 or less may be deferred to the next month and at the latest to 16 December (not modelled). That the August date holds every year is not read: the same row as IVA's «August window» |
| A flat-rate buyer owes VAT on a reverse-charge purchase from abroad, and cannot deduct it | verified | L. 190/2014, art. 1 c. 58 lett. e-bis (pays the VAT on purchases under art. 17 DPR 633/72 «compresi quelli intracomunitari», by the 16th of the second month after each quarter; no deduction), normattiva.it, read 2026-11-10 (REB-993) |
| ECB rate reference date for a foreign-currency document | to verify | |
| TD16 integrates a domestic reverse-charge purchase | to verify | |
| Quarterly settlement surcharge of 1%, and the 100 euro below which a periodic payment is carried forward to the next month (by 16 December at the latest) | verified 2026-11-10 | Agenzia, «F24 IVA Periodica - Come e quando si versa»; Scadenzario fiscale, scadenze per Iva del 20-08-2026 |
| The result of a periodic settlement: IVA due on the sales of the period minus IVA deducted on the purchases registered in it, minus the credit of the period before, plus the debit of the period before that did not exceed 100 euro; a debit is paid when it exceeds 100 euro, otherwise it is added to the next period; a credit is carried to the next period | verified 2026-11-10 (REB-1021) | Agenzia, «F24 IVA Periodica - Che cos'è» (updated 30 July 2026); D.P.R. 100/1998 art. 1 c. 1 and c. 4, text in force from 6 August 2024 (D.Lgs. 108/2024 art. 2), read on def.finanze.it |
| Which periods a debit of 100 euro or less may wait: monthly January to October (the November debit is paid by 16 December and December's by 16 January whatever it is); quarterly the first two quarters (the third is paid by 16 November whatever it is, the fourth goes with the annual return). The 1% interest is added to the sums of a quarterly settlement (art. 7 c. 3) | verified 2026-11-10 (REB-1021) | D.P.R. 100/1998 art. 1 c. 4 («comunque entro il 16 dicembre»; «il versamento relativo al mese di dicembre e' effettuato entro il giorno 16 del mese successivo») and D.P.R. 542/1999 art. 7 c. 1 lett. a) («primi tre trimestri solari ... comunque entro il 16 novembre») and c. 3, text in force from 6 August 2024, read on def.finanze.it |
| A debit of exactly 100 euro: the statute says it is carried («non supera il limite di euro 100»), the Agenzia page says it is paid («pari o superiore a 100 euro») | to verify | The pack follows the statute: exactly 100 euro waits. For REB-975's accountant list |
| Whether the 100 euro limit of a quarterly debit is compared with or without its 1% interest, and whether the fourth quarter carries the 1% | to verify | The pack compares with the interest included and adds the 1% to the first three quarters only (art. 7 c. 1 lett. a) names those; the texts read are silent on the fourth). For REB-975's accountant list |
| The December advance (acconto) and its offset against the December or fourth-quarter payment | to verify | Not computed by the settlement (REB-1021): the Q4 and December items state what the period owes before any advance paid by 27 December |
| Eligibility thresholds for quarterly settlement: 500,000 euro of turnover for services and professionals, 800,000 for other activities, in the previous year | verified 2026-11-10 | Agenzia, «F24 IVA Periodica - Come e quando si versa» |
| Split payment is excluded for services whose fees are subject to withholding, by way of advance or as a final tax (art. 25 DPR 600/73) | verified (REB-994, 2026-11-10) | DPR 633/72 art. 17-ter c. 1-sexies, added by DL 87/2018 art. 12 (Gazzetta Ufficiale 11 August 2018), text read on Normattiva «in vigore dal 2-8-2025 al 31-12-2026»: re-read it against the VAT code that takes over from 1 January 2027 |
| Split payment applies to a sale to the public administrations of art. 1 c. 2 L. 196/2009 (a customer of kind public body) and exists only where there is VAT to split | verified (REB-994, 2026-11-10) | DPR 633/72 art. 17-ter c. 1, same text. The bodies and companies of c. 1-bis (controlled or participated companies, public economic bodies) are not a customer kind yet: a customer of kind business is never split |
| Cash-basis IVA conditions: eligibility ceiling, one-year backstop, buyer-side deduction timing, invoice annotation | to verify | |
| VAT on a retail receipt is not deductible without an invoice | verified | DPR 633/72, art. 19 c. 1 (only the tax «addebitata a titolo di rivalsa» is deductible) and art. 22 (in retail an invoice is not compulsory unless the customer asks before the operation), normattiva.it, read 2026-11-10 (REB-993). An invoice, a simplified one (art. 21-bis) included, is a purchase of kind `invoice`, not a receipt |
| 40% deductibility of VAT on a car's running costs | to verify | art. 19-bis1 DPR 633/72 |
| A service to a taxable person established abroad is not territorial in Italy and its invoice carries `N2.1` and the annotation «inversione contabile» (EU) or «operazione non soggetta» (outside the EU) | verified (REB-994, 2026-11-10) | DPR 633/72 art. 7-ter c. 1 (a service is territorial in Italy when rendered to a taxable person established there) and art. 21 c. 6-bis lett. a) and b), Normattiva; `N2.1` from the technical specifications v1.4 |
| Services to a consumer abroad (art. 7-ter c. 1 lett. b) can be taxed in Italy; the nature and the exceptions | to verify | art. 7-ter DPR 633/72; the foreign consumer has its own card |
| Reverse charge on a foreign supplier depends on the place of supply; TD17, TD18, TD19 by EU or extra-EU and goods or services | to verify | |
| IVA period date for sales: the register is kept by the month of the operation (art. 23 c. 1 DPR 633/72 until 31 December 2026; art. 86 c. 1 of the IVA testo unico, D.Lgs. 19 January 2026 n. 10, in force from 1 January 2027, the same words; the invoices art. 72 c. 4 lett. b of the testo unico names are registered by their issue month, a category not read here) | verified 2026-11-10 (REB-1021) for the rule; to verify for what the code does | Normattiva and def.giustiziatributaria.gov.it, «Testo unico del 19/01/2026 n. 10», art. 86, read 2026-11-10. An invoice stores no operation date, so the settlement places a sale by its issue date, which puts in a later period an invoice issued after the month of its operation (a deferred invoice). For REB-975's accountant list; the accountant bundle places a sale the same way and does not call it an IVA period |
| A purchase invoice is annotated in the register before the periodic settlement in which the deduction is exercised, and at the latest by the annual return of the second year after the year of receipt | verified | DPR 633/72, art. 25 c. 1 and art. 19 c. 1, as amended by D.Lgs. 7 August 2026 n. 148, art. 12 c. 1 (in force 12 August 2026; the earlier text said «all'anno di ricezione della fattura e con riferimento al medesimo anno»), normattiva.it and gazzettaufficiale.it GU n. 185 of 11-8-2026, read 2026-11-10 (REB-993). The code stores a day a person chooses and defaults it to the day of receipt; it does not enforce the term |
| Which term governs a purchase invoice received before 12 August 2026 (the 2026 amendment of art. 25 c. 1 has no transitional rule in art. 12) | to verify | for the accountant list (REB-975); the IVA register card enforces the term |
| Numbering of integration documents TD16 to TD19 | to verify | |
| `FPA12` for an invoice to a public body, its 6-character IPA office code as `CodiceDestinatario` and no PEC; the Sistema di Interscambio refuses a 7-character code beside `FPA12` (and a 6-character one beside `FPR12`) | verified (REB-994, 2026-11-10) | Agenzia delle Entrate, FatturaPA technical specifications v1.4, § 1.1 and § 3.2, and «Elenco dei controlli» v1.9, control 00427 (and 00428 for the format agreeing with `versione`) |
| Deferred IVA for a public body | to verify | |
| Exempt thresholds for travel allowances, traceable payment of reimbursements | to verify | |
| ACI tables for mileage | to verify | |
| Income tax withheld on employment pay is paid by F24 by the 16th of the month after the **payment** | verified 2026-11-10 | Agenzia, «F24 Ritenute reddito lavoro dipendente - Come e quando si versa». From 1 January 2027: art. 32 c. 1 lett. a D.Lgs. 33/2025 («entro il giorno 16 del mese successivo a quello in cui sono state operate le ritenute previste dagli articoli 33, 34, ...») and the F24 term of art. 8 c. 1, read on portalenormativo.it on 2026-11-10; they replace art. 18 D.Lgs. 241/1997 and art. 8 DPR 602/1973 without changing the dates |
| INPS contributions of an employee (employer's and employee's share, paid together by the employer) are paid by the 16th of the month after the month the work was done, whether or not the worker was paid | verified 2026-11-10 | INPS, «Pagamento dei contributi previdenziali» (updated 15 October 2024) |
| The employee's INPS share and the employer's INPS rate | to verify | typed per staff member (`employee_inps_rate`, `employer_inps_rate`); no primary source states one rate for every employer, so the estimate writes no F24 line without both (REB-975) |
| Months of the 13th and 14th payments, by collective agreement | to verify | typed per staff member (`thirteenth_month`, `fourteenth_month`); a payment with no month is not scheduled and the estimate says so (REB-975) |
| TFR accrual and its payment rules (end of employment, advances, pension funds) | to verify | |
| TFM accrual and its deductibility conditions (a written deed of earlier date) | to verify | |
| INAIL's yearly premium (the advance of the year and the regularisation of the one before, together) is paid by F24 by 16 February; the employer may elect four instalments (16 February, then dates fixed each year) | verified 2026-11-10 | INAIL, «Autoliquidazione 2025/2026 - Premi e contributi associativi», version of 8 January 2026, «Informazioni generali» and «Pagamento in quattro rate». The estimate dates the single settlement; the instalment election and each year's later dates are not modelled |
| INAIL premium rate and risk class, and the premium base for a part-time or minimum-wage case | to verify | typed per staff member (`employer_inail_rate`); the accountant gives the rate (REB-975) |
| IRPEF withholding rate and «gestione separata» treatment of a director's fee, and whether a fee is income assimilated to employment pay (art. 34 D.Lgs. 33/2025, whose F24 term is the same 16th of the month after) | to verify | not estimated: a director has a cost and a net fee from the last payslip's ratio, and no F24 line (REB-975) |
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
