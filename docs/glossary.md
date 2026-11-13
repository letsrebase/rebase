# Glossary: Italian domain terms and their English identifiers

The code of this repository is English in every identifier: database tables, columns, enum values, constraint and index names, RLS policy and function names, REST paths, JSON field names and query parameters, MCP tool and parameter names, CLI commands, Python modules, TypeScript files and components, test names and comments. Since 2026-11-03 (REB-908) the prose the code publishes to a developer or an agent is English too: the OpenAPI document, MCP instructions, tool, prompt, resource and parameter descriptions, and the CLI's help and output. The product itself speaks Italian to its users, so the words a person reads inside it stay Italian: UI copy, error messages the web apps show, emails, the content of document templates, the template variables users type (`{{cliente.provincia}}`), the keys of users' own custom fields, and the element names of the FatturaPA XML (fixed by the SdI specification). The dated design records under `projects/pigrocrm/docs/superpowers/` are history and are not edited.

This file is the single mapping from the Italian word to the English identifier. One Italian term has one English identifier everywhere; the few exceptions are listed under "Terms that need context" and are decided by project or by whether the record is a person. The tables below list every renamed name. The source locations that the first version of this file carried were those of the tree at the time (origin/main e9074163) and went stale when modules were split, so they are not kept.

`tooling/english-lint` holds the rule: it fails a pull request on an Italian word in an identifier, a file name, a comment or a docstring (string literals are never read). Its vocabulary is the Italian side of this file and its exceptions, each with a reason, are `tooling/english-lint/allowlist.py`.

## How names are built

- Python, SQL and JSON use `snake_case`; TypeScript uses `camelCase` for values and `PascalCase` for components and types; URLs and file names under `routes/` use `kebab-case`.
- Compounds are rewritten in English word order, not translated word by word: `ore_preventivate` is `budgeted_hours`, `ricavi_cliente_attuali` is `current_customer_revenue`.
- A timestamp suffix `_il` or `_at` becomes `_at` (`chiuso_il` is `closed_at`). A suffix `_da` is `_by` after a participle that names a person (`creato_da` is `created_by`) and `_from` otherwise (`valido_da` is `valid_from`). `_a` is `_to`.
- Status words are the English past participle: `bozza` is `draft`, `emessa` is `issued`, `annullata` is `cancelled`. Every column called `stato` is `status`, every `tipo` is `type` (`kind` in the hub, which already uses `kind`), every `origine` is `origin`, every `fonte` is `source`.
- Plural collections are plural (`aziende` is `legal_entities`, `attivita` as a collection is `tasks`); a single record is singular.
- A name that is already English stays as it is. Brand and product names stay (`Pigro` is the CRM's nickname, `Rebase`, `Documenso`).

## Terms that need context

| Italian | English | Where and why |
|---|---|---|
| azienda | `legal_entity` (PigroCRM), `company` (hub) | In PigroCRM an azienda is one of the user's own issuing businesses (class `LegalEntity`, table `emitter_profile`, join `user_aziende`). In the hub an azienda is a client company that asks for freelancers (class `Company`, table `companies`). The two never meet in one table; where the hub sends its company to the CRM (`/api/rebase/leads`) the key is `company`. |
| emittente / emitter | `legal_entity` | The old name of the same concept as azienda in PigroCRM (`core/emitter`, `emitter_profile`, route `settings/issuer`). One English name, `legal_entity`, so nothing can be confused with `customer`. |
| attivita | `tasks` (PigroCRM), `activities` (hub letters) | The user's to-do item (table `attivita`, `/api/activities`) becomes `tasks`. `activities` keeps meaning the append-only timeline (table and package `core/activities`, `ActivityRead`). In the hub contract letter, `attivita` is the free text of what the freelancer does, so `activities`. |
| cliente | `customer` | The role of who is billed, everywhere, including the hub fields that copy the client's details onto a match (`cliente_ragione_sociale` is `customer_legal_name`). The registry entry that holds a customer is an `organization` (see anagrafica below); the foreign keys keep the role's name (`deals.customer_id`, `invoices.customer_id`). |
| nome | `name`, or `first_name` when the record also has `cognome` | People have `first_name` and `last_name`; everything else with a label has `name`. |
| ruolo | `role` | A permission role on users (`admin`, `collaborator`, `readonly`) and a job role in letters. `people.ruolo` is also `role`. |
| posizione | `position` (ordering index) or `job_title` (hub freelancer) | Pipeline stages and cost categories keep an ordering `position`; the hub freelancer's professional position is `job_title`. |
| competenza / competenze | `accrual` (billing) or `skills` (hub) | Fiscal accrual period of an invoice (`competenza_da` is `accrual_from`) versus a talent's skills (`competenze` is `skills`, filter `competenza` is `skill`). |
| anni / anno | `years` (unit), `experience_years` (hub), `year` | `anno` is always `year`. The plural is `years` as a rate-card unit and `experience_years` on hub talents and cards. |
| da / a (date range) | `from` / `to` | Query parameters `?da=&a=` and the attribution parameter `?da=` are `from` and `to`. |
| riassunto / sintesi / riepilogo | `summary` / `profile_summary` / `recap` | Three different Italian words: a team request's summary, a card's synopsis of the person, a digest recap. |
| incassato / pagato | `paid` | Both mean the customer paid: invoice payment status and work-unit state. `da_incassare` is `unpaid`. |
| proposta | `proposal` | The `Proposal` entity. The CRM tool `propose_azienda` suggests a legal entity without creating a proposal, so it becomes `suggest_legal_entity`. |
| giornata | `work_unit` | A contract day (package `work_units` already exists); the unit of measure `giorno` is `day`. |
| data | `date` | Column and field `data` on time entries, costs, work units, contract expenses and reports. |

## Domain terms (PigroCRM)

| Italian | English | Meaning |
|---|---|---|
| cliente | customer | Who is billed |
| anagrafica | organization | A registry entry (company or person-as-company) with roles `customer`, `supplier`, `freelancer`. Since REB-969 it is the entity: table `organizations`, class `Organization`, `OrganizationService`, `/api/organizations`, MCP `create_organization` and `organization://`, web `/app/organizations/$organizationId` for the record and `/app/customers` and `/app/suppliers` for the lists. The roles are an array column (`organizations.roles`), one record per company whatever it is to us (REB-970); `freelancer` always comes with `supplier` (REB-1061), so no organization is on neither of «Clienti» and «Fornitori» |
| fornitore | supplier (role) | An organisation we pay; a role of an `organization`, not a FatturaPA party |
| tipo fiscale del fornitore | `supplier_kind` | What a payment to a supplier involves: `company` (società), `professional` (professionista in regime ordinario), `flat_rate` (forfettario), `occasional` (prestazione occasionale), `foreign` (estero, with `supplier_area` `eu` or `extra_eu` and `supplier_supplies` `goods` or `services`) |
| tipo del cliente | `customer_kind` | What a sale to a customer involves: `business`, `consumer`, `public_body` (ente pubblico, whose `sdi_code` is its 6-character codice IPA) |
| sostituto d'imposta | withholding agent (`is_withholding_agent`) | A customer that withholds tax on our professional invoices |
| persona / persone | person / people | A contact |
| recapito | contact_point | One way to reach a person, a customer or a hub user: kind (email, phone, linkedin), value, label (lavoro, personale, amministrazione, altro → work, personal, billing, other), note, primary flag |
| deal | deal | Already English |
| spazio | space | A tenant |
| utente | user | A member of a space |
| collaboratore | collaborator | Role between admin and readonly |
| contratto | contract | A contract with a customer |
| offerta | offer | An offer or quote document |
| fattura | invoice | A tax invoice |
| proforma | proforma | Kept: a pre-invoice |
| documento | document |  |
| verbale | minutes | Meeting minutes document type |
| rapporto ore | hours_report | Timesheet report document type |
| ore | hours | Time entries are logged in hours |
| voce | entry | A time or invoice entry |
| tariffa | rate | Price per unit; `tariffa_oraria` is `hourly_rate`, `tariffa_giornaliera` is `daily_rate` |
| listino | list_price | Contract price list |
| costo | cost | What a purchase or an hour costs: `cost_amount` on a purchase (frozen from the buyer's pack), `hourly_cost` for an hour. A `costs` row is, since REB-993, a purchase of kind `expense`; the category table keeps its name (`cost_categories`) |
| acquisto | purchase | Anything the space pays for, with its document: table `purchases`, `PurchaseService`, `/api/purchases`, MCP `create_purchase`, module `purchases` (REB-993). Kinds: `invoice` (fattura), `credit_note` (nota di credito), `receipt` (ricevuta, scontrino) and `expense` (spesa) |
| spesa | expense | A purchase of kind `expense` (anything paid with no invoice), or a contract expense (`contract_expenses`) |
| periodo IVA | `iva_period` | A day inside the IVA register period a purchase invoice is annotated in |
| ritenuta | withholding | `withholding_rate`, `withholding_base`, `withholding_amount` on a purchase |
| esercente | `merchant_name` | The shop or payee of a purchase that is not a supplier of the space (a till receipt); «Esercente» on the phone «Spesa» sheet and on a receipt or expense |
| quota detraibile | `deductible_share` | The percent of a VAT line the buyer may deduct, as the pack and the category decide; shown, never computed, on a purchase's page |
| costo imputato | `cost_amount` | What a purchase costs once the VAT it recovers is out, frozen when it is registered |
| inversione contabile | `reverse_charge` | The buyer accounts for the VAT; a person decides it for a foreign supplier |
| scissione dei pagamenti | `split_payment` | The VAT is paid to the tax office and not to the supplier |
| liquidazione IVA | `settlement` (`IvaSettlement`, `iva_settlements`) | The period's IVA computed from its two registers: sales VAT less deductible purchase VAT, less the credit carried in |
| esigibilità | `exigibility` (`immediate`, `split_payment`) | When a sale's VAT falls due; on a split-payment sale it is not owed by the seller |
| chiusura del periodo IVA | `IvaPeriodLock` (`iva_period_locks`, `close_iva_period`, `reopen_iva_period`) | A period a person closed after settling it: no document that changes its registers is written until it is reopened (REB-1036); not the monthly `period_locks` of hours and costs |
| registro vendite, registro acquisti | sales register, purchase register (`IvaRegisterService`) | The documents behind a settlement line, read by period and legal entity, never a second copy |
| ricavi | revenue |  |
| preventivato | budgeted | Estimated in the offer (`ore_preventivate` is `budgeted_hours`) |
| previsto | expected | Forecast (`valore_previsto` is `expected_value`) |
| voce di valore | value_line | One priced term of a deal's value (`deal_value_lines`): unit, delivery period, price, quantity |
| canone | recurring_fee | A fee paid every period: a `fixed_recurring` rate card, or a `flat` value line with a monthly or annual `delivery_period` |
| consuntivo | actuals | What really happened |
| scadenza | due_date | A deadline; invoice `data_scadenza` is also `due_date` |
| scadenza (an amount owed or to be received) | due_item | One amount that falls due, in or out, with its counterparty, state and rule (`due_items`); its date is `due_date` |
| pagamento registrato su una scadenza | settlement | A payment against a due item, possibly partial; reversed, never deleted (`due_item_settlements`) |
| dipendente | employee | A staff member on a contract (`staff_members.kind`): collective agreement, level, gross annual pay, monthly payments, part-time share, the employer's INPS and INAIL rates |
| amministratore (con compenso) | director | A staff member paid a fee, on a company only (`staff_members.kind`): annual fee, how it is paid, TFM, INPS split |
| persone dell'azienda | staff | Employees and directors of a legal entity (`staff_members`, module `staff`); their pay is admin-only |
| retribuzione annua lorda (RAL) | gross_annual_pay | An employee's gross pay for a full year, before the part-time share |
| giorno di paga | pay_day | The day of the month net pay leaves, with `pay_month_offset` 0 (month worked) or 1 (the month after); one per legal entity, overridden per staff member |
| cedolino | payslip | The figures of an accepted payslip of one staff member and month: gross, net, income tax withheld (`staff_payslips`); the estimate reads two ratios from the latest. A recorded one is corrected in place by a person (`correct_staff_payslip`, «Correggi il cedolino»). One the inbox read and nobody accepted is a `payslip` proposal and reads «Ricevuto»; once its month is closed (`close_payroll_month`, «Chiudi il mese») it reads «Chiuso» and takes no payslip |
| variazioni paghe | `payroll_changes` | The payroll consultant's own export, an `accountant_exports.kind` beside the accountant's period bundle (`accountant`): hires, leavers, people whose record was edited and the approved expense notes reimbursed in a payslip. It carries pay (`payroll`), so its share link asks a one-time code (`accountant_export_link_codes`). «Il periodo è pronto?» is `get_export_checklist`, the readiness checklist an export stores under `readiness` |
| nota spese | expense note | What a staff member spent for the company and asks to be paid back (`expense_notes`, `ExpenseNoteService`, module `staff` with `purchases`): `submitted`, `approved`, `rejected`, `reimbursed`; approving writes a purchase of kind `expense_note` and one due item to the person |
| nota chilometrica | mileage note | An expense note of kind `mileage`: kilometres times the rate per kilometre the person types, with the route |
| autoapprovata | self_approved | An expense note approved by the space's only administrator, who is its author (`expense_notes.self_approved`); the accountant export says so |
| rimborso in busta paga | reimbursement in a payslip | `reimbursement_method = 'payslip'`: the accepted payslip of `payroll_month` settles the note's due item, which is the only one it has |
| Stipendi | payroll aggregate | The one line a reader who may not see pay gets in place of the payroll due items: one amount per due date, no person (`list_payroll_totals`) |
| mensilità aggiuntiva | extra payment | The 13th or 14th payment, in the month typed on the staff member (`thirteenth_month`, `fourteenth_month`) |
| quota INPS del lavoratore | employee_inps_rate | The employee's own share of the INPS contribution, in percent, typed per staff member |
| promemoria | reminder | A reminder raised by the CRM (also the UI "Avvisi") |
| sollecito | payment_reminder | An overdue-invoice dunning mail |
| registro | register | The invoice register; `buchi` are `gaps` |
| periodo | period | A month that can be locked (`period_locks`) |
| giornata | work_unit | Contract day |
| approvazione | approval |  |
| rendiconto | monthly_report | The month's report of an engagement, an approval request (`engagement_monthly_reports`, `MonthlyReport`); framework agreement 6.1 to 6.2. «Rendiconto» stays in the words a person reads. Approving it is a person's act (`approve_monthly_report`, person-only) |
| giorno lavorativo | working day | Counted without Saturdays, Sundays and national holidays (`fiscal.calendar`); the tacit date of a monthly report is the seventh after receipt |
| trattenuto (giorno) | held (day) | A day of a monthly report the client contested: `held_dates`, not payable while the rest of the month is |
| regola / automazione | rule / automation | Automation rules a1 to a5 |
| campo (personalizzato) | field | Custom fields: users' own keys stay as written |
| rinnovo | renewal |  |
| preavviso | notice | Renewal or termination notice period |
| clausola | clause |  |
| modello / template | template | Document and email templates |
| allegato | attachment |  |
| casella | mailbox | A connected Gmail inbox |
| bozza | draft |  |
| cassa (the module and its page) | cash | The space's money position: bank accounts, statements, movements and the schedule (module key `cash`, `/app/cash`). Not `cassa previdenziale` (`pension_fund`) |
| conto (bancario) | bank_account | One of the space's bank accounts (`bank_accounts`, `BankAccount`), always a legal entity's; archived, never deleted |
| estratto conto | bank_statement | One file read for an account (`bank_statements`, `BankStatement`): the period it covers, the balances it states, its `state` (`proposed`, `imported`, `reverted`) |
| movimento (bancario) | bank_movement | One line of an account (`bank_movements`, `BankMovement`), held once per account whatever the number of statements that carry it |
| abbinamento (di un movimento) | movement match (`MovementMatch`, `movement_matches`, `MovementMatchService`) | A movement proposed to, confirmed against or undone from the due items it pays; the payment it writes carries the movement as `bank_movement_id`, its evidence |
| saldo (di un conto) | balance | What an account holds, computed by the server from the latest closing balance (or the opening one) and the movements booked after it; never summed by the browser |

## Fiscal and legal terms

The English identifier is the usual English rendering; the Italian term is kept in this note and in the Italian UI.

| Italian | English identifier | Note |
|---|---|---|
| partita IVA (`partita_iva`, `piva`) | vat_number | Italian VAT number, 11 digits; foreign VAT numbers share the field |
| codice fiscale | tax_code | Personal or company tax code |
| codice SDI / codice destinatario | sdi_code | SdI recipient code for e-invoices |
| PEC | certified_email | Certified email address (posta elettronica certificata) |
| ragione sociale | legal_name | Registered company name |
| regime fiscale | tax_regime | Tax regime of a legal entity |
| regime forfettario | flat_rate_regime | Italian flat-rate regime; `forfettario` is `flat_rate` |
| bollo | stamp_duty | Stamp duty on invoices; `bollo virtuale` is `virtual` |
| imposta sostitutiva | substitute_tax | Substitute tax of the flat-rate regime |
| aliquota IVA | vat_rate | VAT percentage |
| natura (IVA) | vat_nature | Reason code for zero VAT (N1 to N7) |
| imponibile | taxable_amount |  |
| imposta | vat_amount | VAT amount of an invoice |
| competenza | accrual | Accrual period of an invoice (`competenza_da/a` are `accrual_from/to`) |
| cassa previdenziale | pension_fund | Professional pension fund contribution |
| rivalsa INPS | social_security_surcharge | Surcharge for the social security contribution |
| INPS | social_security (inps in compounds) | Italian social security institute; `aliquota_inps` is `social_security_rate` |
| coefficiente di redditivita | profitability_coefficient | Flat-rate regime coefficient |
| tetto / soglia | cap / threshold | Revenue ceilings |
| CAP (codice di avviamento postale) | postal_code | The postal code: column, JSON key, REST/MCP parameter (migration 0069). `cap` stays English for a limit (`contract_cap`, `customer_share_cap`), the template variable `{{cliente.cap}}` keeps its Italian key, and so does FatturaPA's `CAP` element |
| irrevocabilita | irrevocability | Contract irrevocability window |
| riferimento normativo | legal_reference | Legal reference printed on the line |
| tipo documento | sdi_document_type | SdI TipoDocumento (TD01 and so on) |
| CIG / CUP | tender_code / project_code | Public procurement codes on purchase orders |
| ordine (di acquisto) | purchase_order | `ordine_numero` is `purchase_order_number` |
| divisa | currency |  |
| incasso | collection | Payment received; `data_incasso` is `payment_date` |
| acconto | advance | Advance invoice |
| nota di credito | credit_note |  |
| trasmissione | transmission | Delivery of the XML to the intermediary |
| cedente/prestatore, cessionario/committente | supplier, buyer | FatturaPA roles; XML element names stay Italian. `supplier` here is the issuer of any invoice, ours included (`ParsedInvoice.supplier`); it is not the `supplier` role of an organisation, which is who we pay |
| ritardo / scaduta | overdue | Overdue invoice |
| forfait | flat | Flat-fee rate unit |

## Domain terms (hub, website)

| Italian | English | Meaning |
|---|---|---|
| freelance | freelancer | A community member who offers work |
| azienda (richiesta) | company | A client company's request |
| talento | talent | A freelancer seen from the admin search; `/api/hub/talent` already exists |
| scheda | card | The anonymous card written from a CV; `scheda anonima` is `anonymous_card`, `cv anonimo` is `anonymous_cv` |
| iscrizione | signup |  |
| match | match | Already English |
| contratto quadro | framework_agreement | `quadro` is `framework` (`framework.py` exists) |
| lettera di incarico | engagement_letter | `lettera` is `letter`, `incarico` is `engagement` |
| incarico | engagement | The CRM's name for the hub's match with a freelancer (REB-976): table `engagements`, `EngagementService`, `/api/engagements`, MCP `create_engagement`, module `collaborators`. Not the engagements door (`rebase_engagements`, `/api/rebase/engagements`), which sets up a freelancer's own space |
| rappresentante legale | legal_representative | The person who signs for a legal entity (REB-1018): column `legal_entities.legal_representative`, template key `emittente.rappresentante` |
| giorni a settimana | days_per_week | How much of the week an engagement takes, from 0.5 to 5 in steps of 0.5; availability is computed from it |
| disponibilita (di un freelance) | availability | The days a week a freelancer is committed to, by ISO week (`libera` is `free`, `impegnata` is `committed`, `sconosciuta` is `unknown`); read from active engagements only |
| pagamento a incasso | on_collection | The payment rule of a freelancer paid a number of days (`collection_days`) after the client has paid; the other rule is `fixed_term` |
| disdetta | notice | `record_notice` already exists; `disdetto` is `notice_served` |
| campagna | campaign | Email campaign |
| disiscrizione | unsubscribe |  |
| bottone | button | Call-to-action button of a campaign |
| percorso | journey | Funnel path of a campaign (`stato_percorso` is `journey_status`) |
| richiesta team | team_request | A request filed from the team builder |
| proposta team | team_proposal |  |
| referente | contact_person | Company contact |
| compenso | fee |  |
| luogo | location |  |
| settore | industry |  |
| lingue | languages |  |
| remoto / ibrido / in sede | remote / hybrid / on_site |  |
| rif | ref (referral) | Referral code parameter |
| accessi | logins | Login history (table `logins` already exists) |
| agenti | agents | Admin page of API tokens for agents |
| duplicati | duplicates |  |
| guida | guide | First-steps guide download |
| consuntivo | actuals (page: `MatchReport`) | A match's hours against its deal on the CRM |

## Status and enum values

Persisted values get English values. The most common ones:

| Italian | English | Where |
|---|---|---|
| bozza / emessa / annullata / confermata / consumata | draft / issued / cancelled / confirmed / consumed | invoice and proforma status |
| da_incassare / incassato | unpaid / paid | invoice payment status |
| aperta / completata / annullata | open / completed / cancelled | task status |
| manuale / automazione / sollecito | manual / automation / payment_reminder | task origin |
| proposto / approvato / lavorato / fatturato / pagato / contestato / revocato / rifiutato / non_fatturabile | proposed / approved / worked / invoiced / paid / disputed / revoked / rejected / non_billable | work unit status |
| bozza / attivo / terminato / scaduto | draft / active / terminated / expired | contract status |
| nessuno / esplicito / opzione_controparte / tacito | none / explicit / counterparty_option / tacit | renewal type |
| ricorrente_fisso / giornaliero / orario / una_tantum | fixed_recurring / daily / hourly / one_off | rate card type |
| ora / giorno / mese / anno / forfait | hour / day / month / year / flat | rate card unit |
| mensile / trimestrale / annuale | monthly / quarterly / annual | billing cadence |
| una_tantum / mensile / trimestrale / annuale | one_off / monthly / quarterly / annual | delivery period of a rate card and of a deal value line (no quarterly on a line) |
| admin / collaboratore / readonly | admin / collaborator / readonly | user role |
| web / mcp / sistema / rebase | web / mcp / system / rebase | note source |
| in_attesa / accettata / rifiutata | pending / accepted / rejected | proposal status |
| contratto / giornata | contract / work_unit | proposal target type |
| citato / trascritto | quoted / transcribed | proposal excerpt type |
| bozza / in_invio / inviato / incerto / fallito | draft / sending / sent / uncertain / failed | email draft send state |
| nuovo / contattato / attivo / scartato | new / contacted / active / discarded | hub freelancer status |
| nuovo / contattato / in_corso / chiuso | new / contacted / in_progress / closed | hub company status |
| bozza / in_firma / attivo / concluso / annullato | draft / awaiting_signature / active / concluded / cancelled | hub match status |
| generato / in_attesa / inviato / firmato / annullato / disdetto | generated / pending / sent / signed / cancelled / notice_served | hub contract document status |
| stato della firma / busta / copia firmata a mano | `signature_state` / `envelope_id` / `upload_signed_copy` | REB-1019: the hub's contract document status, now columns of `documents` for a framework agreement or an engagement letter; `signed_by_hand` says the signed PDF was uploaded and not sealed by Documenso. |
| da_collegare / collegato / errore / rifiutato | to_link / linked / error / rejected | hub CRM and Pigro link status |
| bozza / programmata / in_invio / inviata / annullata | draft / scheduled / sending / sent / cancelled | hub campaign status |
| in_coda / inviata / saltata / fallita | queued / sent / skipped / failed | campaign recipient status |
| da_confermare / confermato / pagato | to_confirm / confirmed / paid | referral reward status |
| nuova / contattata / chiusa | new / contacted / closed | team request status |
| pubblico / cloud / admin | public / cloud / admin | team proposal origin |
| persona | person | value of `filled_by` and note source |

## Per-kind lists

Entries are per project: `pigrocrm`, `hub`, `website`, `shared`.

### Tables

**pigrocrm** (3)

| Italian | English | Note |
|---|---|---|
| `attivita` | `tasks` | User to-do items (states aperta/completata/annullata). `activities` (append-only timeline) keeps its name; `core/activities` and `tasks` are different things. |
| `emitter_profile` | `legal_entities` | The azienda rows (class LegalEntity already). Table keeps the old singleton name today; rename to legal_entities. Partial unique indexes uq_emitter_profile_* and FK names... |
| `user_aziende` | `user_legal_entities` | Join table user <-> azienda (class UserLegalEntity already). |

### Columns

**pigrocrm** (249)

- `approvals`: `canale` -> `channel`, `estratto` -> `excerpt`, `mittente` -> `sender`, `origine` -> `origin`, `ricevuto_il` -> `received_at`
- `attivita` (table becomes `tasks`): `assegnata_a` -> `assigned_to`, `completata_il` -> `completed_at`, `origine` -> `origin`, `regola` -> `rule`, `scadenza` -> `due_date`, `stato` -> `status`, `titolo` -> `title`
- `automation_config`: `a1_offerta_accettata_vince_deal` -> `a1_wins_accepted_offer_deal`, `a2_offerta_inviata_avanza_deal` -> `a2_advances_sent_offer_deal`, `a3_offerta_inviata_richiama` -> `a3_sent_offer_follow_up`, `a4_deal_fermo` -> `a4_stalled_deal`, `a4_deal_fermo_giorni` -> `a4_stalled_deal_days`, `a5_fattura_scaduta` -> `a5_overdue_invoice`
- `clause_notes`: `interpretazione_adottata` -> `adopted_interpretation`, `riferimento_clausola` -> `clause_reference`, `testo_letterale` -> `literal_text`
- `contract_email_templates`: `allegati` -> `attachments`, `attivo` -> `active`, `corpo` -> `body`, `nome` -> `name`, `oggetto` -> `subject`
- `contract_expenses`: `data` -> `date`, `descrizione` -> `description`, `importo` -> `amount`, `pre_autorizzata` -> `pre_authorized`, `riferimento_autorizzazione` -> `authorization_reference`, `rimborsabile` -> `reimbursable`
- `contracts`: `azienda_id` -> `legal_entity_id`, `cadenza_fatturazione` -> `billing_cadence`, `contratto_precedente_id` -> `previous_contract_id`, `divisa` -> `currency`, `fine` -> `end`, `giorni_pagamento` -> `payment_days`, `inizio` -> `start`, `pagamento_fine_mese` -> `payment_end_of_month`, `politica_spese` -> `expense_policy`, `preavviso_disdetta_giorni` -> `termination_notice_days`, `preavviso_rinnovo_giorni` -> `renewal_notice_days`, `stato` -> `status`, `tetto_quota_cliente` -> `customer_share_cap`, `tipo_rinnovo` -> `renewal_type`, `titolo` -> `title`
- `cost_categories`: `archiviata` -> `archived`, `nome` -> `name`, `posizione` -> `position`
- `costs` (now `purchases`, REB-993): `azienda_id` -> `legal_entity_id`, `data` -> `date`, `descrizione` -> `description`, `fornitore` -> `supplier` (now `merchant_name`), `importo` -> `amount`
- `customers`: `azienda_id` -> `legal_entity_id`, `codice_fiscale` -> `tax_code`, `codice_sdi` -> `sdi_code`, `comune` -> `city`, `giorni_pagamento` -> `payment_days`, `indirizzo` -> `address`, `nazione` -> `country`, `pagamento_fine_mese` -> `payment_end_of_month`, `partita_iva` -> `vat_number`, `pec` -> `certified_email`, `provincia` -> `province`, `ragione_sociale` -> `legal_name`, `sito_web` -> `website`, `stato` -> `status`, `telefono` -> `phone`
- `deals`: `azienda_id` -> `legal_entity_id`, `chiuso_il` -> `closed_at`, `data_chiusura_prevista` -> `expected_close_date`, `nome` -> `name`, `ore_preventivate` -> `budgeted_hours`, `probabilita` -> `probability`, `tariffa_oraria` -> `hourly_rate`, `valore_preventivato` -> `budgeted_value`, `valore_previsto` -> `expected_value`
- `digests`: `inviato_a` -> `sent_to`, `settimana` -> `week`
- `document_versions`: `creato_da` -> `created_by`, `dimensione` -> `size`, `numero` -> `number`, `sorgente_markdown` -> `markdown_source`, `variabili` -> `variables`
- `documents`: `azienda_id` -> `legal_entity_id`, `stato` -> `status`, `stato_dal` -> `status_since`, `tipo` -> `type`, `titolo` -> `title`, `versione_corrente` -> `current_version`
- `emitter_profile` (table becomes `legal_entities`): `attiva` -> `active`, `codice_fiscale` -> `tax_code`, `codice_sdi` -> `sdi_code`, `comune` -> `city`, `firma_email` -> `email_signature`, `firma_key` -> `signature_key`, `indirizzo` -> `address`, `nazione` -> `country`, `nome` -> `name`, `partita_iva` -> `vat_number`, `pec` -> `certified_email`, `predefinita` -> `is_default`, `provincia` -> `province`, `ragione_sociale` -> `legal_name`, `regime_fiscale` -> `tax_regime`, `sito_web` -> `website`, `telefono` -> `phone`
- `fiscal_profile`: `aliquota_imposta_sostitutiva` -> `substitute_tax_rate`, `aliquota_inps` -> `social_security_rate`, `aliquota_iva_default` -> `default_vat_rate`, `applica_bollo` -> `apply_stamp_duty`, `azienda_id` -> `legal_entity_id`, `codice_regime` -> `regime_code`, `coefficiente_redditivita` -> `profitability_coefficient`, `condizioni_pagamento` -> `payment_terms`, `giorni_scadenza` -> `due_days`, `importo_bollo` -> `stamp_duty_amount`, `modalita_pagamento` -> `payment_method`, `natura_default` -> `default_vat_nature`, `riferimento_normativo` -> `legal_reference`, `soglia_bollo` -> `stamp_duty_threshold`
- `gmail_messages`: `body_html_scartato` -> `body_html_discarded`
- `invitations`: `aziende` -> `legal_entities`, `nome` -> `name`, `ruolo` -> `role`
- `invoice_counters`: `anno` -> `year`, `azienda_id` -> `legal_entity_id`, `ultimo_numero` -> `last_number`
- `invoice_lines`: `aliquota_iva` -> `vat_rate`, `descrizione` -> `description`, `natura` -> `vat_nature`, `numero_linea` -> `line_number`, `prezzo_totale` -> `total_price`, `prezzo_unitario` -> `unit_price`, `quantita` -> `quantity`, `riferimento_normativo` -> `legal_reference`, `sconto_importo` -> `discount_amount`, `sconto_percentuale` -> `discount_percentage`, `unita_misura` -> `unit_of_measure`
- `invoice_register_gaps`: `anno` -> `year`, `azienda_id` -> `legal_entity_id`, `dichiarato_da` -> `declared_by`, `dichiarato_il` -> `declared_at`, `motivo` -> `reason`, `numero` -> `number`
- `invoices`: `anno` -> `year`, `annullata_il` -> `cancelled_at`, `azienda_id` -> `legal_entity_id`, `bollo` -> `stamp_duty`, `causale` -> `memo`, `competenza_a` -> `accrual_to`, `competenza_da` -> `accrual_from`, `data_emissione` -> `issue_date`, `data_incasso` -> `payment_date`, `data_scadenza` -> `due_date`, `divisa` -> `currency`, `imponibile` -> `taxable_amount`, `importata_da` -> `imported_from`, `imposta` -> `vat_amount`, `motivo_annullamento` -> `cancellation_reason`, `numero` -> `number`, `ordine_cig` -> `purchase_order_tender_code`, `ordine_cup` -> `purchase_order_project_code`, `ordine_data` -> `purchase_order_date`, `ordine_numero` -> `purchase_order_number`, `origine_proforma_id` -> `origin_proforma_id`, `riferimento` -> `reference`, `stato` -> `status`, `stato_pagamento` -> `payment_status`, `tipo` -> `type`, `tipo_documento` -> `sdi_document_type`, `totale` -> `total`, `trasmessa_esternamente_il` -> `transmitted_externally_at`
- `note_revisions`: `fonte` -> `source`, `testo` -> `text`
- `notes`: `autore` -> `author`, `autore_id` -> `author_id`, `fonte` -> `source`, `modificata_at` -> `edited_at`, `modificata_da_id` -> `edited_by_id`, `testo` -> `text`
- `people`: `cognome` -> `last_name`, `nome` -> `first_name`, `ruolo` -> `role`, `telefono` -> `phone`
- `period_locks`: `anno` -> `year`, `chiuso_da` -> `closed_by`, `chiuso_il` -> `closed_at`, `mese` -> `month`
- `personal_access_tokens`: `nome` -> `name`
- `pipeline_stages`: `nome` -> `name`, `posizione` -> `position`, `probabilita_default` -> `default_probability`, `tipo` -> `type`
- `proposals`: `campi_accettati` -> `accepted_fields`, `campi_proposti` -> `proposed_fields`, `confidenza` -> `confidence`, `deciso_da` -> `decided_by`, `deciso_il` -> `decided_at`, `estratto` -> `excerpt`, `id_risultato` -> `result_id`, `motivo_confidenza` -> `confidence_reason`, `stato` -> `status`, `tipo_estratto` -> `excerpt_type`
- `rate_cards`: `frazioni_ammesse` -> `allowed_fractions`, `importo` -> `amount`, `ore_minime` -> `minimum_hours`, `periodo_erogazione` -> `delivery_period`, `tipo` -> `type`, `unita` -> `unit`, `valido_a` -> `valid_to`, `valido_da` -> `valid_from`
- `renewal_assumptions`: `orizzonte_al` -> `horizon_until`, `probabilita` -> `probability`, `volume_atteso` -> `expected_volume`
- `templates`: `attivo` -> `active`, `corpo_markdown` -> `body_markdown`, `nome` -> `name`, `tipo` -> `type`, `variabili_dichiarate` -> `declared_variables`
- `time_entries`: `costo_applicato` -> `applied_cost`, `costo_origine` -> `cost_source`, `data` -> `date`, `descrizione` -> `description`, `fatturabile` -> `billable`, `ore` -> `hours`, `tariffa_applicata` -> `applied_rate`, `tariffa_origine` -> `rate_source`
- `time_timers`: `descrizione` -> `description`, `fatturabile` -> `billable`
- `user_aziende` (table becomes `user_legal_entities`): `azienda_id` -> `legal_entity_id`
- `users`: `ambito_limitato` -> `limited_scope`, `attivo` -> `active`, `costo_orario_default` -> `default_hourly_cost`, `digest_settimanale` -> `weekly_digest`, `email_verificata_il` -> `email_verified_at`, `nome` -> `name`, `ruolo` -> `role`, `tariffa_oraria_default` -> `default_hourly_rate`
- `work_unit_transitions`: `attore` -> `actor`, `motivo` -> `reason`, `stato_nuovo` -> `new_status`, `stato_precedente` -> `previous_status`
- `work_units`: `data` -> `date`, `descrizione` -> `description`, `quantita` -> `quantity`, `stato` -> `status`

**hub** (107)

- `admin_tokens`: `nome` -> `name`
- `campaign_optouts`: `fonte` -> `source`
- `campaign_recipients`: `azione_at` -> `action_at`, `codice` -> `code`, `consegnata_at` -> `delivered_at`, `disiscrizione_token` -> `unsubscribe_token`, `entrato_at` -> `signed_in_at`, `inviata_at` -> `sent_at`, `motivo` -> `reason`, `nome` -> `name`, `prima` -> `before`, `primo_clic_at` -> `first_click_at`, `reclamo_at` -> `complaint_at`, `rimbalzata_at` -> `bounced_at`, `stato` -> `status`, `tentativi` -> `attempts`, `tipo` -> `kind`
- `campaigns`: `azione` -> `action`, `bottone_meta` -> `button_meta`, `bottone_testo` -> `button_text`, `bottone_url` -> `button_url`, `contenuto_at` -> `content_at`, `fermo_at` -> `stalled_at`, `fermo_motivo` -> `stalled_reason`, `filtri` -> `filters`, `fonte` -> `source`, `inviata_at` -> `sent_at`, `nome` -> `name`, `oggetto` -> `subject`, `pigro_errore_at` -> `pigro_error_at`, `pigro_letto_at` -> `pigro_read_at`, `programmata_per` -> `scheduled_for`, `prova_inviata_at` -> `test_sent_at`, `stato` -> `status`, `stato_percorso` -> `journey_status`, `testo` -> `text`
- `card_eval_results`: `anni_ok` -> `experience_years_ok`
- `companies`: `budget_giornaliero` -> `daily_budget`, `crm_bozza` -> `crm_draft`, `crm_errore` -> `crm_error`, `crm_stato` -> `crm_status`, `durata` -> `duration`, `figura_richiesta` -> `requested_role`, `giorni_presenza` -> `onsite_days`, `nome_azienda` -> `company_name`, `numero_risorse` -> `headcount`, `periodo_da` -> `period_from`, `progetto` -> `project`, `remoto` -> `remote`, `stato` -> `status`
- `contract_documents`: `numero` -> `number`, `stato` -> `status`, `testo_bozza` -> `draft_text`
- `contract_letter_counters`: `anno` -> `year`, `ultimo` -> `last`
- `freelancer_cards`: `fonti` -> `sources`, `persona` -> `person`
- `freelancer_fiscal`: `codice_fiscale` -> `tax_code`, `domicilio` -> `domicile`, `partita_iva` -> `vat_number`, `pec` -> `certified_email`
- `freelancers`: `anni_admin` -> `experience_years_admin`, `posizione` -> `job_title`, `remoto` -> `remote`, `stato` -> `status`, `tariffa_giornaliera` -> `daily_rate`
- `matches`: `cliente_piva` -> `customer_vat_number`, `cliente_ragione_sociale` -> `customer_legal_name`, `cliente_sede` -> `customer_registered_office`, `company_budget_giornaliero` -> `company_daily_budget`, `giorni_previsti` -> `expected_days`, `lettera_compenso` -> `letter_fee`, `lettera_data_fine` -> `letter_end_date`, `lettera_data_inizio` -> `letter_start_date`, `pigro_errore` -> `pigro_error`, `pigro_stato` -> `pigro_status`, `stato` -> `status`
- `notes`: `autore` -> `author`, `autore_id` -> `author_id`, `eliminata_at` -> `deleted_at`, `eliminata_da_id` -> `deleted_by_id`, `fonte` -> `source`, `modificata_at` -> `edited_at`, `modificata_da_id` -> `edited_by_id`, `testo` -> `text`
- `referral_rewards`: `stato` -> `status`
- `referrals`: `stato` -> `status`
- `signups`: `cognome` -> `last_name`, `nome` -> `first_name`
- `team_proposals`: `descrizione` -> `description`, `economia` -> `economics`, `errore` -> `error`, `luogo` -> `location`, `origine` -> `origin`, `persone` -> `people`, `riassunto` -> `summary`
- `team_request_talents`: `risposta` -> `answer`, `risposta_at` -> `answered_at`, `ruolo` -> `role`
- `team_requests`: `azienda` -> `company`, `origine` -> `origin`, `stato` -> `status`, `telefono` -> `phone`
- `users`: `attivo` -> `active`, `cognome` -> `last_name`, `nome` -> `first_name`, `telefono` -> `phone`

### Enum and persisted string values

**pigrocrm** (147)

`accettata` -> `accepted`, `acconto_fattura` -> `invoice_advance`, `acconto_parcella` -> `fee_advance`, `anni` -> `years`, `anno` -> `year`, `annuale` -> `annual`, `annullata` -> `cancelled`, `aperta` -> `open`, `approvato` -> `approved`, `arrivo` -> `arrival`, `assente` -> `absent`, `attivita` -> `tasks`, `attivita_due` -> `task_due`, `attivita_due / attivita_overdue` -> `task_due / task_overdue`, `attivita_overdue` -> `task_overdue`, `attivo` -> `active`, `automazione` -> `automation`, `bozza` -> `draft`, `calcolata` -> `calculated`, `chiuso` -> `closed`, `citato` -> `quoted`, `collaboratore` -> `collaborator`, `collegato` -> `connected`, `competenza` -> `accrual`, `completata` -> `completed`, `concentrazione_soglia_preferita` -> `preferred_concentration_threshold`, `confermata` -> `confirmed`, `consumata` -> `consumed`, `contestato` -> `disputed`, `contratto` -> `contract`, `contratto_precedente_id` -> `previous_contract_id`, `conversazioni` -> `conversations`, `corriere` -> `courier`, `da fatturare` -> `to_invoice`, `da_incassare` -> `unpaid`, `deal_fermo` -> `stalled_deal`, `decisione` -> `decision`, `documento` -> `document`, `dopo_la_fine` -> `after_end`, `drive.account_collegato` -> `drive.account_connected`, `drive.account_scollegato` -> `drive.account_disconnected`, `drive.credenziale_revocata` -> `drive.credential_revoked`, `drive.radici_impostate` -> `drive.roots_set`, `emessa` -> `issued`, `emissione` -> `issue`, `errore` -> `error`, `esce_dall_anno_successivo` -> `leaves_next_year`, `esce_immediatamente` -> `leaves_immediately`, `eseguito` -> `run`, `esplicito` -> `explicit`, `esterno` -> `external`, `fallito` -> `failed`, `fattura` -> `invoice`, `fattura_pdf` -> `invoice_pdf`, `fattura_scaduta` -> `overdue_invoice`, `fattura_xml` -> `invoice_xml`, `fatturato` -> `invoiced`, `fine` -> `end`, `fine_irrevocabilita` -> `irrevocability_end`, `firma` -> `signature`, `forfait` -> `flat`, `gia_inviato` -> `already_sent`, `gia_nello_stato` -> `already_in_state`, `giornaliero` -> `daily`, `giornata` -> `work_unit`, `giorni` -> `days`, `giorno` -> `day`, `gmail.account_collegato` -> `gmail.account_connected`, `gmail.account_scollegato` -> `gmail.account_disconnected`, `gmail.backfill_eseguito` -> `gmail.backfill_run`, `gmail.bozza_creata` -> `gmail.draft_created`, `gmail.bozza_eliminata` -> `gmail.draft_deleted`, `gmail.bozza_modificata` -> `gmail.draft_edited`, `gmail.credenziale_revocata` -> `gmail.credential_revoked`, `gmail.impostazioni_modificate` -> `gmail.settings_changed`, `gmail.messaggio_inviato` -> `gmail.message_sent`, `gmail.messaggio_ricevuto` -> `gmail.message_received`, `gmail.sollecito_preparato` -> `gmail.payment_reminder_prepared`, `gmail.sync_eseguito` -> `gmail.sync_run`, `in corso` -> `in_progress`, `in_attesa` -> `pending`, `in_corso` -> `in_progress`, `in_invio` -> `sending`, `incassato` -> `paid`, `incasso` -> `collection`, `incerto` -> `uncertain`, `inizio` -> `start`, `inviata` -> `sent`, `inviato` -> `sent`, `lavorato` -> `worked`, `lavorato_senza_approvazione` -> `worked_without_approval`, `manuale` -> `manual`, `mensile` -> `monthly`, `mese` -> `month`, `mesi` -> `months`, `negato` -> `denied`, `nessun_destinatario` -> `no_recipient`, `nessuno` -> `none`, `non_fatturabile` -> `non_billable`, `offerta` -> `offer`, `offerta_inviata_richiama` -> `sent_offer_follow_up`, `oggi` -> `today`, `opzione_controparte` -> `counterparty_option`, `orario` -> `hourly`, `pagato` -> `paid`, `parcella` -> `fee_note`, `posta_certificata` -> `certified_mail`, `prima_dell_inizio` -> `before_start`, `proposto` -> `proposed`, `raccomandata` -> `registered_mail`, `rapporto_ore` -> `hours_report`, `recenti` -> `recent`, `registrati` -> `register`, `registro_pdf` -> `register_pdf`, `regola_disattivata` -> `rule_disabled`, `revocato` -> `revoked`, `ricorrente_fisso` -> `fixed_recurring`, `rifiutata` -> `rejected`, `rifiutato` -> `rejected`, `saltato` -> `skipped`, `scadenza_rinnovo` -> `renewal_due`, `scaduto` -> `expired`, `sessione` -> `session_expired`, `settimane` -> `weeks`, `sistema` -> `system`, `slack.abbinamenti_rifatti` -> `slack.matches_rebuilt`, `slack.abbinamento_manuale` -> `slack.manual_match`, `slack.abbinamento_rimosso` -> `slack.match_removed`, `slack.canale_impostato` -> `slack.channel_set`, `slack.canale_rimosso` -> `slack.channel_removed`, `slack.workspace_collegato` -> `slack.workspace_connected`, `slack.workspace_scollegato` -> `slack.workspace_disconnected`, `solleciti_grace_days` -> `payment_reminders_grace_days`, `solleciti_max_reminders` -> `payment_reminders_max_reminders`, `solleciti_min_interval_days` -> `payment_reminders_min_interval_days`, `sollecito` -> `payment_reminder`, `stage_bersaglio_ambiguo` -> `target_stage_ambiguous`, `stage_bersaglio_assente` -> `target_stage_missing`, `stato` -> `status`, `tacito` -> `tacit`, `terminato` -> `terminated`, `trascritto` -> `transcribed`, `trimestrale` -> `quarterly`, `una_tantum` -> `one_off`, `utente` -> `user`, `verbale` -> `minutes`, `vuoto` -> `empty`

**hub** (107)

`a corpo` -> `lump_sum`, `a giornata` -> `to_work_unit`, `accettata` -> `accepted`, `aggiorna_stato` -> `status_refresh`, `anni` -> `experience_years`, `anni_asc` -> `experience_years_asc`, `anni_desc` -> `experience_years_desc`, `anni_diversi` -> `different_experience_years`, `anni_scartata` -> `discarded_experience_years`, `annulla` -> `cancel`, `annullata` -> `cancelled`, `annullato` -> `cancelled`, `applicato` -> `applied`, `assente` -> `absent`, `attivo` -> `active`, `azienda` -> `company`, `aziende` -> `companies`, `azione` -> `action`, `bottone_meta` -> `button_meta`, `bottone_testo` -> `button_text`, `bozza` -> `draft`, `chiudi` -> `close`, `chiusa` -> `closed`, `chiuso` -> `closed`, `chiuso prima` -> `closed_before`, `clic` -> `click`, `cognome` -> `last_name`, `collegato` -> `linked`, `completato` -> `completed`, `completo` -> `complete`, `concluso` -> `concluded`, `confermato` -> `confirmed`, `contattata` -> `contacted`, `contattato` -> `contacted`, `cv_anonimo` -> `anonymous_cv`, `da_collegare` -> `to_link`, `da_confermare` -> `to_confirm`, `da_inviare` -> `to_send`, `da_riprovare` -> `retry`, `da_verificare` -> `to_verify`, `disdetto` -> `notice_served`, `entrato` -> `signed_in`, `errore` -> `error`, `fallita` -> `failed`, `fermati` -> `stop`, `filtri` -> `filters`, `firmato` -> `signed`, `fonte` -> `source`, `generato` -> `generated`, `giorno` -> `day`, `ibrido` -> `hybrid`, `ignorato` -> `ignored`, `in corso` -> `in_progress`, `in_attesa` -> `pending`, `in_corso` -> `in_progress`, `in_firma` -> `awaiting_signature`, `in_invio` -> `sending`, `in_sede` -> `on_site`, `invia` -> `send`, `inviata` -> `sent`, `inviato` -> `sent`, `lettera` -> `letter`, `lista` -> `list`, `manca_cv` -> `missing_cv`, `mese` -> `month`, `nome` -> `name`, `nuova` -> `new`, `nuovo` -> `new`, `occasionale` -> `occasional`, `oggetto` -> `subject`, `pagato` -> `paid`, `partita_iva` -> `vat_registered`, `persona` -> `person`, `pigro_cliente` -> `pigro_customer`, `pigro_vuoto` -> `pigro_empty`, `presente` -> `present`, `profilo_creato` -> `profile_created`, `programmata` -> `scheduled`, `proprietario` -> `owner`, `prova` -> `test`, `pubblico` -> `public`, `quadro` -> `framework`, `recenti` -> `recent`, `reclamo` -> `complaint`, `registra_disdetta` -> `record_notice`, `remoto` -> `remote`, `riassunto` -> `summary`, `richiesta` -> `request`, `richiesta_aggiornata` -> `request_updated`, `rifiutata` -> `rejected`, `rifiutato` -> `rejected`, `riprova` -> `retry`, `riprova_pigro` -> `retry_pigro`, `saltata` -> `skipped`, `scartato` -> `discarded`, `scheda` -> `card`, `scheda_completa` -> `card_complete`, `sistema` -> `system`, `stato` -> `status`, `talenti` -> `talents`, `tariffa` -> `rate`, `tariffa_asc` -> `rate_asc`, `tariffa_desc` -> `rate_desc`, `telefono` -> `phone`, `testo` -> `text`, `tutto` -> `all`, `verificato` -> `verified`

### Python modules and packages

**pigrocrm** (38)

| Italian | English | Note |
|---|---|---|
| `pigrocrm/apps/api/tests/aziende_helpers.py` | `pigrocrm/apps/api/tests/legal_entities_helpers.py` |  |
| `pigrocrm/apps/api/tests/test_azienda_chain_api.py` | `pigrocrm/apps/api/tests/test_legal_entity_chain_api.py` |  |
| `pigrocrm/apps/api/tests/test_azienda_scope_api.py` | `pigrocrm/apps/api/tests/test_legal_entity_scope_api.py` |  |
| `pigrocrm/apps/api/tests/test_aziende_api.py` | `pigrocrm/apps/api/tests/test_legal_entities_api.py` |  |
| `pigrocrm/apps/api/tests/test_aziende_assets_api.py` | `pigrocrm/apps/api/tests/test_legal_entities_assets_api.py` |  |
| `pigrocrm/apps/api/tests/test_calendario_api.py` | `pigrocrm/apps/api/tests/test_calendar_api.py` |  |
| `pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/calendario.py` | `pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/calendar.py` |  |
| `pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/families/attivita.py` | `pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/families/tasks.py` |  |
| `pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/families/aziende.py` | `pigrocrm/apps/mcp/src/pigrocrm_mcp/tools/families/legal_entities.py` |  |
| `pigrocrm/apps/mcp/tests/test_azienda_chain_tools.py` | `pigrocrm/apps/mcp/tests/test_legal_entity_chain_tools.py` |  |
| `pigrocrm/apps/mcp/tests/test_azienda_scope_mcp.py` | `pigrocrm/apps/mcp/tests/test_legal_entity_scope_mcp.py` |  |
| `pigrocrm/apps/mcp/tests/test_calendario_tools.py` | `pigrocrm/apps/mcp/tests/test_calendar_tools.py` |  |
| `pigrocrm/packages/core/src/pigrocrm/core/attivita/` | `pigrocrm/packages/core/src/pigrocrm/core/tasks/` | directory Package for tasks; `core/activities` (timeline) is a different package and keeps its name. |
| `pigrocrm/packages/core/src/pigrocrm/core/calendario/` | `pigrocrm/packages/core/src/pigrocrm/core/calendar/` | directory |
| `pigrocrm/packages/core/src/pigrocrm/core/emitter/` | `pigrocrm/packages/core/src/pigrocrm/core/legal_entities/` | directory Package for legal entities (class LegalEntity already); `routers/legal_entities.py` already exists beside it. |
| `pigrocrm/packages/core/src/pigrocrm/core/gmail/solleciti.py` | `pigrocrm/packages/core/src/pigrocrm/core/gmail/payment_reminders.py` |  |
| `pigrocrm/packages/core/src/pigrocrm/core/gmail/solleciti_template.py` | `pigrocrm/packages/core/src/pigrocrm/core/gmail/payment_reminders_template.py` |  |
| `pigrocrm/packages/core/src/pigrocrm/core/invoices/ordine.py` | `pigrocrm/packages/core/src/pigrocrm/core/invoices/purchase_order.py` |  |
| `pigrocrm/packages/core/src/pigrocrm/core/invoices/scadenza.py` | `pigrocrm/packages/core/src/pigrocrm/core/invoices/due_date.py` |  |
| `pigrocrm/packages/core/src/pigrocrm/core/reminders/detectors/attivita.py` | `pigrocrm/packages/core/src/pigrocrm/core/reminders/detectors/tasks.py` |  |
| `pigrocrm/packages/core/tests/fakes/azienda_fixtures.py` | `pigrocrm/packages/core/tests/fakes/legal_entity_fixtures.py` |  |
| `pigrocrm/packages/core/tests/periodo_fiscale.py` | `pigrocrm/packages/core/tests/tax_period.py` |  |
| `pigrocrm/packages/core/tests/test_attivita_service.py` | `pigrocrm/packages/core/tests/test_task_service.py` |  |
| `pigrocrm/packages/core/tests/test_azienda_assets.py` | `pigrocrm/packages/core/tests/test_legal_entity_assets.py` |  |
| `pigrocrm/packages/core/tests/test_azienda_chain.py` | `pigrocrm/packages/core/tests/test_legal_entity_chain.py` |  |
| `pigrocrm/packages/core/tests/test_azienda_scope.py` | `pigrocrm/packages/core/tests/test_legal_entity_scope.py` |  |
| `pigrocrm/packages/core/tests/test_calendario_service.py` | `pigrocrm/packages/core/tests/test_calendar_service.py` |  |
| `pigrocrm/packages/core/tests/test_dashboard_per_azienda.py` | `pigrocrm/packages/core/tests/test_dashboard_by_legal_entity.py` |  |
| `pigrocrm/packages/core/tests/test_emitter.py` | `pigrocrm/packages/core/tests/test_legal_entity.py` |  |
| `pigrocrm/packages/core/tests/test_invoice_ordine.py` | `pigrocrm/packages/core/tests/test_invoice_purchase_order.py` |  |
| `pigrocrm/packages/core/tests/test_invoice_register_per_azienda.py` | `pigrocrm/packages/core/tests/test_invoice_register_by_legal_entity.py` |  |
| `pigrocrm/packages/core/tests/test_invoice_rivalsa_line.py` | `pigrocrm/packages/core/tests/test_invoice_social_security_surcharge_line.py` |  |
| `pigrocrm/packages/core/tests/test_invoice_scadenza.py` | `pigrocrm/packages/core/tests/test_invoice_due_date.py` |  |
| `pigrocrm/packages/core/tests/test_render_per_azienda.py` | `pigrocrm/packages/core/tests/test_render_by_legal_entity.py` |  |
| `pigrocrm/packages/core/tests/test_search_attivita.py` | `pigrocrm/packages/core/tests/test_search_task.py` |  |
| `pigrocrm/packages/core/tests/test_solleciti_candidates.py` | `pigrocrm/packages/core/tests/test_payment_reminders_candidates.py` |  |
| `pigrocrm/packages/core/tests/test_solleciti_create.py` | `pigrocrm/packages/core/tests/test_payment_reminders_create.py` |  |
| `pigrocrm/packages/core/tests/test_sollecito_template.py` | `pigrocrm/packages/core/tests/test_payment_reminder_template.py` |  |

**hub** (6)

| Italian | English | Note |
|---|---|---|
| `hub/apps/mcp/src/rebase_mcp/talenti_view.py` | `hub/apps/mcp/src/rebase_mcp/talents_view.py` |  |
| `hub/apps/mcp/tests/test_talenti_tools.py` | `hub/apps/mcp/tests/test_talents_tools.py` |  |
| `hub/packages/core/src/rebase_core/talenti.py` | `hub/packages/core/src/rebase_core/talents.py` |  |
| `hub/packages/core/tests/test_card_persona.py` | `hub/packages/core/tests/test_card_person.py` |  |
| `hub/packages/core/tests/test_migration_0043_card_persona.py` | `hub/packages/core/tests/test_migration_0043_card_person.py` |  |
| `hub/packages/core/tests/test_talenti.py` | `hub/packages/core/tests/test_talents.py` |  |

### Routes (REST and browser)

**pigrocrm** (34)

| Italian | English | Note |
|---|---|---|
| `/api/activities/oggi` | `/api/activities/today` | GET; REST path as served by FastAPI. |
| `/api/activities/{attivita_id}` | `/api/activities/{task_id}` | GET; REST path as served by FastAPI. |
| `/api/activities/{attivita_id}/cancel` | `/api/activities/{task_id}/cancel` | POST; REST path as served by FastAPI. |
| `/api/activities/{attivita_id}/complete` | `/api/activities/{task_id}/complete` | POST; REST path as served by FastAPI. |
| `/api/activities/{attivita_id}/reopen` | `/api/activities/{task_id}/reopen` | POST; REST path as served by FastAPI. |
| `/api/activities/{attivita_id}/restore` | `/api/activities/{task_id}/restore` | POST; REST path as served by FastAPI. |
| `/api/aziende` | `/api/legal-entities` | GET; REST path as served by FastAPI. |
| `/api/aziende/proposta` | `/api/legal-entities/suggestion` | GET; REST path as served by FastAPI. |
| `/api/aziende/{azienda_id}` | `/api/legal-entities/{legal_entity_id}` | GET; REST path as served by FastAPI. |
| `/api/aziende/{azienda_id}/firma` | `/api/legal-entities/{legal_entity_id}/signature` | GET; REST path as served by FastAPI. |
| `/api/aziende/{azienda_id}/fiscal-profile` | `/api/legal-entities/{legal_entity_id}/fiscal-profile` | GET; REST path as served by FastAPI. |
| `/api/aziende/{azienda_id}/logo` | `/api/legal-entities/{legal_entity_id}/logo` | GET; REST path as served by FastAPI. |
| `/api/aziende/{azienda_id}/predefinita` | `/api/legal-entities/{legal_entity_id}/default` | POST; REST path as served by FastAPI. |
| `/api/documents/{document_id}/versions/{numero}/regenerate` | `/api/documents/{document_id}/versions/{number}/regenerate` | POST; REST path as served by FastAPI. |
| `/api/invoices/register/{anno}/gaps` | `/api/invoices/register/{year}/gaps` | GET; REST path as served by FastAPI. |
| `/api/period-locks/{anno}/{mese}` | `/api/period-locks/{year}/{month}` | DELETE; REST path as served by FastAPI. |
| `/api/tenants/{slug}/disponibile` | `/api/tenants/{slug}/available` | GET; REST path as served by FastAPI. |
| `/app/avvisi` | `/app/reminders` | Browser path. Real page (not a redirect). Linked from nav, digests and bookmarks. |
| `/app/calendario` | `/app/calendar` | Browser path. Redirect stub. |
| `/app/clienti` | `/app/customers` | Browser path. Redirect stub; «Clienti» is the real page at `/app/customers` since REB-1053 (the registry's lists are `/app/customers` and `/app/suppliers`, an organization's own page is `/app/organizations/$organizationId`, and `/app/organizations` redirects). |
| `/app/clienti/$customerId` | `/app/organizations/$organizationId` | Browser path. Redirect stub. |
| `/app/deal/lista` | `/app/deal/list` | Browser path. Redirect stub. |
| `/app/documenti/$documentId` | `/app/documents/$documentId` | Browser path. Redirect stub. |
| `/app/entra` | `/app/verify` | Browser path. Redirect stub; magic-link emails sent earlier and nginx 301 at the edge (deploy/nginx/*pigro*.conf, spa.conf). |
| `/app/fatture` | `/app/invoices` | Browser path. Redirect stub. |
| `/app/fatture/$invoiceId` | `/app/invoices/$invoiceId` | Browser path. Redirect stub. |
| `/app/impostazioni` | `/app/settings` | Browser path. Redirect layout + children: automazioni->automations, campi->fields, categorie-costo->cost-categories, drive, emittente->legal-entities, fiscale->legal-enti... |
| `/app/oggi` | `/app/tasks?view=today` | Browser path. Redirect stub today; Monday digest mails link here. |
| `/app/ore` | `/app/hours` | Browser path. Redirect stub of 2026-09-21. |
| `/app/persone` | `/app/people` | Browser path. Redirect stub. |
| `/app/persone/$personId` | `/app/people/$personId` | Browser path. Redirect stub. |
| `/app/registrati` | `/app/register` | Browser path. Redirect stub; nginx 301 and the hub Area.tsx still link here. |
| `/app/settings/aziende` | `/app/settings/legal-entities` | Browser path. Real page today (LegalEntitiesPanel); `/app/settings/issuer` and `/app/settings/fiscal` redirect to it. |
| `/app/settings/issuer` | `/app/settings/legal-entities` | Browser path. Redirect stub of 2026-10-03; the word `issuer` goes away with `emitter`. |

**hub** (24)

| Italian | English | Note |
|---|---|---|
| `/api/hub/admin/ai/tasks/{task}/prova` | `/api/hub/admin/ai/tasks/{task}/test` | POST; REST path as served by FastAPI. |
| `/api/hub/campagne/disiscrizione` | `/api/hub/campaigns/unsubscribe` | GET; REST path as served by FastAPI. |
| `/api/hub/freelancers/{freelancer_id}/cv-anonimo` | `/api/hub/freelancers/{freelancer_id}/anonymous-cv` | POST; REST path as served by FastAPI. |
| `/api/hub/talent/duplicati` | `/api/hub/talent/duplicates` | GET; REST path as served by FastAPI. |
| `/hub/accedi` | `/hub/login` | Browser path (basepath /hub). Redirect; outreach mails link `/accedi?utm_...`. |
| `/hub/admin/accessi` | `/hub/admin/access` | Browser path (basepath /hub). Redirect (note: stays `access`, the table is `logins`). |
| `/hub/admin/agenti` | `/hub/admin/agents` | Browser path (basepath /hub). Redirect. |
| `/hub/admin/amministratori` | `/hub/admin/admins` | Browser path (basepath /hub). Redirect. |
| `/hub/admin/aziende` | `/hub/admin/companies` | Browser path (basepath /hub). Redirect. |
| `/hub/admin/aziende/$id` | `/hub/admin/companies/$id` | Browser path (basepath /hub). Redirect. |
| `/hub/admin/companies/nuova` | `/hub/admin/companies/new` | Browser path (basepath /hub). REAL page. |
| `/hub/admin/guida` | `/hub/admin/guide` | Browser path (basepath /hub). Redirect. |
| `/hub/admin/modelli-ai` | `/hub/admin/ai-models` | Browser path (basepath /hub). REAL page. |
| `/hub/admin/talenti` | `/hub/admin/talent` | Browser path (basepath /hub). Redirect (keeps the list filters). |
| `/hub/admin/talenti/$id` | `/hub/admin/talent/$id` | Browser path (basepath /hub). Redirect. |
| `/hub/admin/team/proposte` | `/hub/admin/team/proposals` | Browser path (basepath /hub). REAL page. |
| `/hub/aziende` | `/hub/companies` | Browser path (basepath /hub). Public wizard; redirect keeps the query string (campaign UTM links already sent). |
| `/hub/disiscrizione` | `/hub/unsubscribe` | Browser path (basepath /hub). REAL page, linked from every campaign email (List-Unsubscribe too); API twin is /api/hub/campagne/disiscrizione. Needs a permanent alias. |
| `/hub/entra` | `/hub/verify` | Browser path (basepath /hub). Redirect; every magic-link mail already sent points here (`?t=`). |
| `/hub/grazie` | `/hub/thanks` | Browser path (basepath /hub). Redirect. |
| `/hub/io` | `/hub/me` | Browser path (basepath /hub). Redirect. |
| `/hub/io/modifica` | `/hub/me/edit` | Browser path (basepath /hub). Redirect. |
| `/hub/io/modifica-azienda` | `/hub/me/edit-company` | Browser path (basepath /hub). Redirect. |
| `/hub/team/risposta` | `/hub/team/answer` | Browser path (basepath /hub). REAL page, linked from availability mails to talents (`?t=&r=`); in-flight emails. |

**website** (3)

| Italian | English | Note |
|---|---|---|
| `/api/community/signups` | `/api/community/signups` | English already; website sends it. Its JSON body (nome, cognome, rif, utm_*) is covered under hub json_field. |
| `/termini` | `/terms` | Already renamed: `/terms` is the page, nginx 301s `/termini`. Keep the 301 (indexed page, old links). |
| `GET /api/hub/referral-rates` | `GET /api/hub/referral-rates` | English already. |

**shared** (4)

| Italian | English | Note |
|---|---|---|
| `GET /api/rebase/engagements/{match_id}/report?da=&a=` | `query from/to; response keys deal{nome,tariffa_oraria,ore_preventivate,stato}, giorni, totale_ore, ore_fatturate, ore_non_fatturate, fatture[{tipo,anno,numero,stato,stato_pagamento,data,ore}], ReportEntry{data,ore,descrizione,fatturabile,fattura}` | HUB->CRM; the hub also re-emits grouped keys (per_giorno, per_settimana, per_mese, fatture) to its own web UI, so two layers rename. |
| `GET /api/tenants/ (registry), POST /api/tenants/member, POST /api/hub/members/lookup` | `TenantSignup{slug,nome,email,membro}, MemberQuestion{email}, MemberAnswer{membro,nome,cognome,spazi}, TenantAvailability{slug,disponibile,motivo}; GET /api/tenants/{slug}/disponibile -> /available` | CRM<->hub both directions (registry token). Hub reads RegistryRow{slug,owner_email,created_at} (English already). `membro`->`member`, `spazi`->`spaces`. |
| `PUT /api/rebase/engagements/{match_id}` | `unchanged path; body keys renamed: freelancer{email,nome,cognome}->{email,first_name,last_name}, lettera{numero,ruolo,azienda,data_inizio,data_fine,compenso,giorni_previsti}->letter{number,role,company_name,start_date,end_date,fee,expected_days}, rebase{ragione_sociale,partita_iva,codice_fiscale,indirizzo,pec,codice_sdi}->{legal_name,vat_number,tax_code,address,certified_email,sdi_code}; response {slug,url,customer_id,deal_id,deal_url,spazio_creato,creato}->{...,space_created,created}` | HUB->CRM contract, server to server. Both projects must switch in one deploy, or the CRM accepts both key sets during a window. |
| `PUT /api/rebase/leads/{request_id}` | `body {azienda,referente{nome,cognome,email,telefono,linkedin_url},deal{nome,valore_previsto,data_chiusura_prevista,note},deal_id}->{company,contact{first_name,last_name,email,phone,linkedin_url,contact_points[{kind,value,label,note,is_primary}]},deal{name,expected_value,expected_close_date,notes},deal_id}; response {customer_id,person_id,deal_id,deal_url,creato->created}` | HUB->CRM. `contact.contact_points` (REB-945) is new: it has no Italian predecessor, is optional, and the door refuses unknown fields, so the CRM ships before the hub. Here `azienda` is the hub company (becomes a CRM customer): `company`, NOT legal_entity. |

### JSON fields

**pigrocrm** (458)

`a1_offerta_accettata_vince_deal` -> `a1_wins_accepted_offer_deal`, `a2_offerta_inviata_avanza_deal` -> `a2_advances_sent_offer_deal`, `a3_offerta_inviata_richiama` -> `a3_sent_offer_follow_up`, `a4_deal_fermo` -> `a4_stalled_deal`, `a4_deal_fermo_giorni` -> `a4_stalled_deal_days`, `a5_fattura_scaduta` -> `a5_overdue_invoice`, `aggiunta_sintetica` -> `brief_addition`, `aliquota_cassa` -> `pension_fund_rate`, `aliquota_imposta_sostitutiva` -> `substitute_tax_rate`, `aliquota_inps` -> `social_security_rate`, `aliquota_iva` -> `vat_rate`, `aliquota_iva_default` -> `default_vat_rate`, `allegati` -> `attachments`, `ambito_limitato` -> `limited_scope`, `anno` -> `year`, `annullata_il` -> `cancelled_at`, `annullate` -> `cancelled`, `applica_bollo` -> `apply_stamp_duty`, `approvazione` -> `approval`, `archiviata` -> `archived`, `arretrato` -> `backlog`, `assegnata_a` -> `assigned_to`, `attiva` -> `active`, `attivata_da` -> `triggered_by`, `attivata_via` -> `triggered_via`, `attivita` -> `tasks`, `attivita_recenti` -> `recent_task`, `attivita_senza_scadenza` -> `task_without_due_date`, `attivo` -> `active`, `attore` -> `actor`, `autore` -> `author`, `autore_id` -> `author_id`, `avanzamento_ore` -> `hours_progress`, `avvertenza` -> `warning`, `azienda` -> `legal_entity`, `azienda_id` -> `legal_entity_id`, `azienda_nome` -> `legal_entity_name`, `aziende` -> `legal_entities`, `azioni` -> `actions`, `body_html_scartato` -> `body_html_discarded`, `bollo` -> `stamp_duty`, `bozze` -> `drafts`, `buchi` -> `gaps`, `buchi_non_dichiarati` -> `undeclared_gaps`, `budget_pro_rata` -> `budget_pro_rata`, `cadenza_fatturazione` -> `billing_cadence`, `calcolato_alle` -> `calculated_at`, `campi` -> `fields`, `campi_accettati` -> `accepted_fields`, `campi_proposti` -> `proposed_fields`, `campo` -> `field`, `canale` -> `channel`, `cassa` -> `pension_fund`, `cassa_previdenziale` -> `social_security_pension_fund`, `causale` -> `memo`, `chiavi` -> `keys`, `chiusi` -> `closed`, `chiuso_da` -> `closed_by`, `chiuso_il` -> `closed_at`, `chiusure` -> `closings`, `chiusure_non_attribuibili` -> `closings_not_attributable`, `chiusure_previste_30_giorni` -> `expected_closings_30_days`, `cliente` -> `customer`, `clienti` -> `customers`, `clienti_collegati` -> `linked_customers`, `codice` -> `code`, `codice_fiscale` -> `tax_code`, `codice_regime` -> `regime_code`, `codice_sdi` -> `sdi_code`, `coefficiente_redditivita` -> `profitability_coefficient`, `cognome` -> `last_name`, `collegamento` -> `link`, `compenso` -> `fee`, `competenza_a` -> `accrual_to`, `competenza_da` -> `accrual_from`, `completata_il` -> `completed_at`, `completo` -> `complete`, `comune` -> `city`, `concentrazione_clienti` -> `customer_concentration`, `concentrazione_soglia_preferita` -> `preferred_concentration_threshold`, `condizioni_pagamento` -> `payment_terms`, `confidenza` -> `confidence`, `configurazione` -> `configuration`, `conseguenza` -> `consequence`, `conteggio` -> `count`, `contract_titolo` -> `contract_title`, `contratto_fine` -> `contract_end`, `contratto_inizio` -> `start_contract`, `contratto_precedente_id` -> `previous_contract_id`, `contratto_successivo_id` -> `next_contract_id`, `conversazioni` -> `conversations`, `corpo` -> `body`, `corpo_markdown` -> `body_markdown`, `costi` -> `costs`, `costi_diretti` -> `direct_costs`, `costo` -> `cost`, `costo_applicato` -> `applied_cost`, `costo_dopo` -> `cost_after`, `costo_lavoro` -> `work_cost`, `costo_orario_default` -> `default_hourly_cost`, `costo_origine` -> `cost_source`, `costo_prima` -> `cost_before`, `costo_riga` -> `line_cost`, `creato` -> `created`, `creato_da` -> `created_by`, `customer_nome` -> `customer_name`, `customer_ragione_sociale` -> `customer_legal_name`, `da_incassare` -> `unpaid`, `da_rinnovo` -> `from_renewal`, `data` -> `date`, `data_chiusura_prevista` -> `expected_close_date`, `data_emissione` -> `issue_date`, `data_fattura` -> `invoice_date`, `data_incasso` -> `payment_date`, `data_inizio` -> `start_date`, `data_scadenza` -> `due_date`, `deal_nome` -> `deal_name`, `deal_non_preventivati` -> `unbudgeted_deals`, `deal_preventivati` -> `budgeted_deals`, `deciso_da` -> `decided_by`, `deciso_il` -> `decided_at`, `descrizione` -> `description`, `destinatari` -> `recipients`, `dichiarato_da` -> `declared_by`, `dichiarato_il` -> `declared_at`, `dichiarazione_bollo` -> `stamp_duty_declaration`, `digest_settimanale` -> `weekly_digest`, `dimensione` -> `size`, `disponibile` -> `available`, `divisa` -> `currency`, `documento_id` -> `document_id`, `documento_titolo` -> `title_document`, `dominio` -> `domain`, `dopo` -> `after`, `durata` -> `duration`, `esecuzioni` -> `runs`, `estratto` -> `excerpt`, `etichetta` -> `label`, `fasce` -> `bands`, `fattura` -> `invoice`, `fatturabile` -> `billable`, `fatture` -> `invoices`, `fatture_emesse` -> `issued_invoices`, `fine` -> `end`, `finestra_irrevocabilita_fino_al` -> `irrevocability_window_until_to`, `firma_email` -> `email_signature`, `firma_key` -> `signature_key`, `fiscale` -> `tax`, `fiscale_proiettato` -> `projected_tax`, `fonte` -> `source`, `fornitore` -> `supplier`, `frazioni_ammesse` -> `allowed_fractions`, `full_access_prima` -> `full_access_before`, `fuori_termine` -> `out_of_term`, `gia_in_anagrafica` -> `already_in_directory`, `giornata` -> `work_unit`, `giornate` -> `work_units`, `giorni` -> `days`, `giorni_di_ritardo` -> `days_of_delay`, `giorni_fatturati` -> `invoiced_days`, `giorni_nel_periodo` -> `days_in_period`, `giorni_pagamento` -> `payment_days`, `giorni_previsti` -> `expected_days`, `giorni_scadenza` -> `due_days`, `giorni_senza_listino` -> `days_without_list_price`, `giorni_senza_ore` -> `days_without_hours`, `giorno` -> `day`, `gmail_configurato` -> `gmail_configured`, `gmail_message_id` -> `gmail_message_id`, `gmail_store_bodies` -> `gmail_store_bodies`, `gmail_thread_id` -> `gmail_thread_id`, `google_client_condiviso` -> `google_client_shared`, `google_client_secret_impostato` -> `google_client_secret_set`, `google_token_key_impostata` -> `google_token_key_set`, `id_risultato` -> `result_id`, `id_trasmittente` -> `id_transmitter`, `imponibile` -> `taxable_amount`, `imponibile_cassa` -> `pension_fund_taxable_amount`, `importata_da` -> `imported_from`, `importi_coincidono` -> `match_amounts`, `importo` -> `amount`, `importo_bollo` -> `stamp_duty_amount`, `importo_contributo_cassa` -> `pension_fund_contribution_amount`, `importo_fatturato` -> `invoiced_amount`, `importo_proposto` -> `proposed_amount`, `importo_riga` -> `line_amount`, `imposta` -> `vat_amount`, `imposta_sostitutiva` -> `substitute_tax`, `in_corso` -> `in_progress`, `in_ritardo` -> `overdue`, `incassato` -> `paid`, `indirizzo` -> `address`, `inizio` -> `start`, `interpretazione_adottata` -> `adopted_interpretation`, `inviato` -> `sent`, `invitato_da` -> `inviter_name`, `invito` -> `invitation`, `lettera` -> `letter`, `livello` -> `level`, `livello_allerta` -> `alert_level`, `livello_allerta_simulato` -> `alert_level_simulated`, `lordo_effettivo` -> `actual_gross`, `lordo_proiettato` -> `projected_gross`, `mappature_giorni` -> `days_mappings`, `margine_lordo` -> `gross_margin`, `margine_percentuale` -> `percentage_margin`, `media_precedente` -> `media_previous`, `membro` -> `member`, `mese` -> `month`, `mesi` -> `months`, `messaggi` -> `messages`, `messaggi_cancellati` -> `deleted_messages`, `messaggi_cancellati_conteggio` -> `count_deleted_messages`, `mittente` -> `sender`, `modalita_pagamento` -> `payment_method`, `modificabile` -> `editable`, `modificata_at` -> `edited_at`, `modificata_da` -> `edited_by`, `motivo` -> `reason`, `motivo_annullamento` -> `cancellation_reason`, `motivo_blocco` -> `block_reason`, `motivo_confidenza` -> `confidence_reason`, `natura` -> `vat_nature`, `natura_default` -> `default_vat_nature`, `nazione` -> `country`, `netto_effettivo` -> `actual_net`, `netto_proiettato` -> `projected_net`, `nome` -> `first_name`, `non_preventivato` -> `unbudgeted`, `nota` -> `note`, `nota_id` -> `note_id`, `note_clausole` -> `note_clauses`, `note_clausole_totale` -> `note_total_clauses`, `numero` -> `number`, `numero_fattura` -> `invoice_number`, `numero_giorni` -> `days_number`, `numero_linea` -> `line_number`, `obbligatoria` -> `required`, `offerte_accettate_deal_non_vinto` -> `accepted_offers_deal_not_won`, `offerte_in_attesa` -> `offers_pending`, `offerte_in_attesa_totale` -> `offers_pending_total`, `oggetto` -> `subject`, `oggi` -> `today`, `ordine_cig` -> `purchase_order_tender_code`, `ordine_cup` -> `purchase_order_project_code`, `ordine_data` -> `purchase_order_date`, `ordine_numero` -> `purchase_order_number`, `ore` -> `hours`, `ore_consuntivate` -> `actuals_hours`, `ore_fatturabili_non_fatturate` -> `billable_uninvoiced_hours`, `ore_fatturate` -> `invoiced_hours`, `ore_minime` -> `minimum_hours`, `ore_non_fatturate` -> `hours_uninvoiced`, `ore_preventivate` -> `budgeted_hours`, `ore_senza_tariffa` -> `hours_without_rate`, `ore_totali` -> `total_hours`, `origine` -> `origin`, `origine_proforma_id` -> `origin_proforma_id`, `origine_scadenza` -> `due_date_origin`, `orizzonte_al` -> `horizon_until`, `owner_nome` -> `owner_name`, `pagamento_fine_mese` -> `payment_end_of_month`, `partita_iva` -> `vat_number`, `pdf_sorgente` -> `pdf_source`, `pec` -> `certified_email`, `per_cliente` -> `by_customer`, `per_contratto` -> `by_contract`, `per_mese` -> `by_month`, `per_stato` -> `by_status`, `percorsi_usati` -> `used_journeys`, `periodo` -> `period`, `periodo_a` -> `period_to`, `periodo_chiuso` -> `closed_period`, `periodo_da` -> `period_from`, `periodo_erogazione` -> `delivery_period`, `persi` -> `lost`, `persone` -> `people`, `pila_andamento` -> `trend_stack`, `pila_proiezione` -> `projection_stack`, `politica_spese` -> `expense_policy`, `posizione` -> `position`, `pre_autorizzata` -> `pre_authorized`, `preavviso_disdetta_giorni` -> `termination_notice_days`, `preavviso_rinnovo_giorni` -> `renewal_notice_days`, `precedente_valido_a` -> `previous_valid_to`, `precedente_valido_da` -> `previous_valid_from`, `predefinita` -> `default`, `prezzo_totale` -> `total_price`, `prezzo_unitario` -> `unit_price`, `prima` -> `before`, `pro_rata_non_calcolabile` -> `pro_rata_not_calculable`, `probabilita` -> `probability`, `probabilita_default` -> `default_probability`, `programmato` -> `scheduled`, `progressivo_invio` -> `send_sequential`, `proiettato` -> `projected`, `prossimi_sette_giorni` -> `next_seven_days`, `prossimo_livello` -> `next_level`, `provenienza` -> `provenance`, `provincia` -> `province`, `punteggio` -> `score`, `quantita` -> `quantity`, `quota` -> `share`, `quota_attuale` -> `current_share`, `quota_registrati` -> `register_share`, `quota_senza_approvazione` -> `share_without_approval`, `quota_simulata` -> `simulated_share`, `quota_totale` -> `total_share`, `quote_andamento` -> `trend_shares`, `quote_proiezione` -> `projection_shares`, `raggruppa_per_mese` -> `group_by_by_month`, `ragione_sociale` -> `legal_name`, `reddito_netto_stimato` -> `estimated_net_income`, `referente` -> `contact_person`, `regime_fiscale` -> `tax_regime`, `regimi` -> `regimes`, `registrati` -> `register`, `registro` -> `register`, `regola` -> `rule`, `regole` -> `rules`, `residuo` -> `remaining`, `residuo_attuale` -> `current_remaining`, `residuo_simulato` -> `simulated_remaining`, `ricavi` -> `revenue`, `ricavi_attuali` -> `current_revenue`, `ricavi_cliente` -> `customer_revenue`, `ricavi_cliente_attuali` -> `current_customer_revenue`, `ricavi_cliente_simulati` -> `simulated_customer_revenue`, `ricavi_simulati` -> `simulated_revenue`, `ricavi_totali` -> `total_revenue`, `ricavi_totali_attuali` -> `current_total_revenue`, `ricavi_totali_simulati` -> `simulated_total_revenue`, `ricevuto_il` -> `received_at`, `richiesta` -> `request`, `riepiloghi` -> `recaps`, `riferimento` -> `reference`, `riferimento_autorizzazione` -> `authorization_reference`, `riferimento_clausola` -> `clause_reference`, `riferimento_da_rimuovere` -> `reference_to_remove`, `riferimento_normativo` -> `legal_reference`, `righe` -> `lines`, `rimborsabile` -> `reimbursable`, `rinnova` -> `renew`, `rivalsa` -> `social_security_surcharge`, `ruolo` -> `role`, `scadenza` -> `due_date`, `scadenza_da_rimuovere` -> `due_date_to_remove`, `scadenza_prevista` -> `expected_due_date`, `scadenze_contrattuali` -> `contractual_due_dates`, `scadono_oggi` -> `today_due`, `scadute` -> `overdue`, `scadute_totale` -> `total_overdue`, `scaduto` -> `expired`, `sconto_importo` -> `discount_amount`, `sconto_percentuale` -> `discount_percentage`, `scostamento_ore` -> `hours_variance`, `scostamento_valore` -> `value_variance`, `segnali` -> `signals`, `senza_approvazione` -> `without_approval`, `senza_approvazione_nel_periodo` -> `without_approval_in_period`, `senza_approvazione_sempre` -> `without_approval_always`, `senza_scadenza` -> `without_due_date`, `senza_tariffa` -> `without_rate`, `senza_valore` -> `without_value`, `settimana` -> `week`, `sezioni` -> `sections`, `sito_web` -> `website`, `soglia` -> `threshold`, `soglia_bollo` -> `stamp_duty_threshold`, `soglia_da_contratto` -> `threshold_from_contract`, `soglie` -> `thresholds`, `solleciti_grace_days` -> `payment_reminders_grace_days`, `solleciti_inviati` -> `sent_payment_reminders`, `solleciti_max_reminders` -> `payment_reminders_max_reminders`, `solleciti_min_interval_days` -> `payment_reminders_min_interval_days`, `sovrascritte` -> `overwritten`, `spazi` -> `spaces`, `spazio` -> `space`, `spazio_creato` -> `space_created`, `spese_generali` -> `general_expenses`, `stage_nome` -> `stage_name`, `stage_tipo` -> `stage_type`, `stato` -> `status`, `stato_dal` -> `status_since`, `stato_nuovo` -> `new_status`, `stato_pagamento` -> `payment_status`, `stato_precedente` -> `previous_status`, `stima` -> `estimate`, `successore` -> `successor`, `tariffa` -> `rate`, `tariffa_applicata` -> `applied_rate`, `tariffa_dopo` -> `rate_after`, `tariffa_media_consuntivata` -> `average_actual_rate`, `tariffa_media_preventivata` -> `average_budgeted_rate`, `tariffa_nominale` -> `nominal_rate`, `tariffa_oraria` -> `hourly_rate`, `tariffa_oraria_default` -> `default_hourly_rate`, `tariffa_origine` -> `rate_source`, `tariffa_prima` -> `rate_before`, `tariffa_reale` -> `actual_rate`, `tasso_conversione` -> `conversion_rate`, `telefono` -> `phone`, `termine` -> `deadline`, `termini_pagamento` -> `termini_payment`, `testo` -> `text`, `testo_letterale` -> `literal_text`, `tetto` -> `cap`, `tetto_contratto` -> `contract_cap`, `tetto_quota_cliente` -> `customer_share_cap`, `tipo` -> `type`, `tipo_cassa` -> `pension_fund_type`, `tipo_documento` -> `sdi_document_type`, `tipo_estratto` -> `excerpt_type`, `tipo_rinnovo` -> `renewal_type`, `titolo` -> `title`, `to_suggeriti` -> `to_suggested`, `totale` -> `total`, `totale_dovuto` -> `due_total`, `totale_e_un_minimo` -> `total_and_a_minimum`, `totale_ore` -> `hours_total`, `totale_preventivato` -> `budgeted_total`, `totale_quantita` -> `quantity_total`, `totale_ricavi` -> `revenue_total`, `trasmessa_esternamente_il` -> `transmitted_externally_at`, `trasmissione` -> `transmission`, `trattamenti` -> `treatments`, `troncato` -> `truncated`, `ultima_risposta_il` -> `last_answer_at`, `ultimo_mese_completo` -> `last_complete_month`, `ultimo_messaggio` -> `last_message`, `ultimo_sollecito_il` -> `last_payment_reminder_at`, `unita` -> `unit`, `unita_misura` -> `unit_of_measure`, `utilizzo` -> `usage`, `utilizzo_attuale` -> `current_usage`, `utilizzo_simulato` -> `simulated_usage`, `valido_a` -> `valid_to`, `valido_da` -> `valid_from`, `valore_maturato` -> `accrued_value`, `valore_ore_non_fatturate` -> `unbilled_hours_value`, `valore_ponderato` -> `weighted_value`, `valore_preventivato` -> `budgeted_value`, `valore_previsto` -> `expected_value`, `valore_riga` -> `line_value`, `valore_totale` -> `total_value`, `valore_vinto` -> `won_value`, `valute` -> `currencies`, `variabili` -> `variables`, `variabili_dichiarate` -> `declared_variables`, `variabili_non_usate` -> `variables_not_used`, `version_numero` -> `version_number`, `versione_corrente` -> `current_version`, `vinti` -> `won`, `voci` -> `entries`, `voci_aggiornate` -> `updated_entries`, `voci_scritte_in_ritardo` -> `written_entries_overdue`, `voci_senza_tariffa` -> `entries_without_rate`, `volume_atteso` -> `expected_volume`

**hub** (240)

`accessi` -> `logins`, `accettazione` -> `acceptance`, `admin_nome` -> `admin_name`, `altre_azioni` -> `other_actions`, `altre_condizioni` -> `other_terms`, `altri_link` -> `other_links`, `anni` -> `experience_years`, `anni_admin` -> `experience_years_admin`, `anni_hub` -> `experience_years_hub`, `anni_scheda` -> `experience_years_card`, `assicurazione` -> `insurance`, `assorbito` -> `absorbed`, `attivita` -> `activities`, `attivo` -> `active`, `autore` -> `author`, `autore_id` -> `author_id`, `avanzamento` -> `progress`, `azienda` -> `company`, `aziende` -> `companies`, `azione` -> `action`, `azione_at` -> `action_at`, `azione_dalla_mail` -> `action_from_mail`, `azioni` -> `actions`, `bottone_meta` -> `button_meta`, `bottone_testo` -> `button_text`, `bottone_url` -> `button_url`, `budget_giornaliero` -> `daily_budget`, `campagna` -> `campaign`, `campo` -> `field`, `citta` -> `city`, `cliccate` -> `clicked`, `cliente` -> `customer`, `cliente_piva` -> `customer_vat_number`, `cliente_ragione_sociale` -> `customer_legal_name`, `cliente_sede` -> `customer_registered_office`, `codice_fiscale` -> `tax_code`, `cognome` -> `last_name`, `compenso` -> `fee`, `competenze` -> `skills`, `completa` -> `complete`, `con_accessi` -> `with_logins`, `consegnata_at` -> `delivered_at`, `consegnate` -> `delivered`, `contattabile` -> `contactable`, `conteggi` -> `counts`, `contenuto_at` -> `content_at`, `coordinamento` -> `coordination`, `cosa_succede` -> `what_happens`, `created_by_nome` -> `created_by_name`, `creato_a` -> `created_to`, `creato_da` -> `created_from`, `crm_errore` -> `crm_error`, `crm_situazione` -> `crm_situation`, `crm_stato` -> `crm_status`, `data_inizio` -> `start_date`, `dati_autorizzazione` -> `authorization_data`, `dati_categorie` -> `category_data`, `dati_finalita` -> `purpose_data`, `dati_fiscali_mancanti` -> `missing_tax_data`, `dati_interessati` -> `data_subjects`, `dati_personali` -> `personal_data`, `descrizione` -> `description`, `descrizioni` -> `descriptions`, `destinatari` -> `recipients`, `differenze` -> `differences`, `documenti` -> `documents`, `documento` -> `document`, `domicilio` -> `domicile`, `durata` -> `duration`, `economia` -> `economics`, `entrate` -> `income`, `entrato_at` -> `signed_in_at`, `entrato_dalla_mail` -> `signed_in_from_mail`, `errore` -> `error`, `esclusiva` -> `exclusivity`, `esito` -> `outcome`, `etichetta` -> `label`, `fallite` -> `failed`, `fascia` -> `band`, `fatture` -> `invoices`, `fermo_at` -> `stalled_at`, `fermo_motivo` -> `stalled_reason`, `figura_richiesta` -> `requested_role`, `filtri` -> `filters`, `fine_mese` -> `month_end`, `fiscale` -> `tax`, `fonte` -> `source`, `fonti` -> `sources`, `freelancer_cognome` -> `freelancer_last_name`, `freelancer_nome` -> `freelancer_name`, `giorni_equivalenti` -> `equivalent_days`, `giorni_pagamento` -> `payment_days`, `giorni_preavviso` -> `notice_days`, `giorni_presenza` -> `onsite_days`, `giorni_previsti` -> `expected_days`, `giorni_settimana` -> `week_days`, `giorno` -> `day`, `granted_by_nome` -> `granted_by_name`, `ha_azienda` -> `has_company`, `ha_pdf_firmato` -> `has_pdf_signed`, `ha_scheda` -> `has_card`, `ha_scheda_anonima` -> `has_anonymous_card`, `impegno` -> `commitment`, `inizio` -> `start`, `inviata_at` -> `sent_at`, `inviate` -> `sent`, `inviato` -> `sent`, `iscrizione_utm` -> `signup_utm`, `iscrizioni` -> `signups`, `lavoro_extra` -> `extra_work`, `lettera` -> `letter`, `lettera_compenso` -> `letter_fee`, `lettera_data_fine` -> `letter_end_date`, `lettera_data_inizio` -> `letter_start_date`, `lettera_in_attesa` -> `letter_pending`, `lettera_numero` -> `number_letter`, `lettera_stato` -> `status_letter`, `lettera_unita` -> `unit_letter`, `lettere` -> `letters`, `lingue` -> `languages`, `lista` -> `list`, `luogo` -> `location`, `mail_inviata` -> `mail_sent`, `match_figura_richiesta` -> `match_requested_role`, `match_freelancer_nome` -> `match_freelancer_name`, `match_nome_azienda` -> `match_company_name`, `membri` -> `members`, `membri_totali` -> `total_members`, `membro` -> `member`, `mese` -> `month`, `modalita` -> `mode`, `modificabile` -> `editable`, `modificata_at` -> `edited_at`, `modificata_da` -> `edited_by`, `motivazione` -> `reasoning`, `motivo` -> `reason`, `nome` -> `first_name`, `nome_azienda` -> `company_name`, `nota_id` -> `note_id`, `numero` -> `number`, `numero_risorse` -> `headcount`, `nuova_versione` -> `new_version`, `oggetto` -> `subject`, `ore` -> `hours`, `ore_fatturate` -> `invoiced_hours`, `ore_non_fatturate` -> `hours_uninvoiced`, `ore_per_fattura` -> `hours_by_invoice`, `ore_previste` -> `expected_hours`, `origine` -> `origin`, `partita_iva` -> `vat_number`, `pec` -> `certified_email`, `per_giorno` -> `by_day`, `per_mese` -> `by_month`, `per_settimana` -> `by_week`, `per_stato` -> `by_status`, `periodo_da` -> `period_from`, `periodo_verifica` -> `review_period`, `persona` -> `person`, `persone` -> `people`, `pigro_errore` -> `pigro_error`, `pigro_stato` -> `pigro_status`, `posizione` -> `job_title`, `possibile_duplicato_di` -> `duplicate_possible_of`, `primo_clic_at` -> `first_click_at`, `profilo` -> `profile`, `progetto` -> `project`, `programmata_per` -> `scheduled_for`, `pronta` -> `ready`, `prossima_azione` -> `next_action`, `prova_inviata_at` -> `test_sent_at`, `provenienza` -> `provenance`, `quadri` -> `frameworks`, `quadri_annullati` -> `cancelled_frameworks`, `quadri_precedenti` -> `previous_frameworks`, `quadro` -> `framework`, `quadro_attivo` -> `active_framework`, `quadro_necessario` -> `required_framework`, `quadro_occasionale` -> `occasional_framework`, `rapporti_precedenti` -> `previous_reports`, `recenti` -> `recent`, `reclamo_at` -> `complaint_at`, `referente` -> `contact_person`, `referente_cliente` -> `customer_contact`, `referente_cognome` -> `contact_last_name`, `referente_nome` -> `contact_first_name`, `referente_rebase` -> `rebase_contact`, `referral_stato` -> `referral_status`, `referred_nome` -> `referred_name`, `referrer_nome` -> `referrer_name`, `remoto` -> `remote`, `revoked_by_nome` -> `revoked_by_name`, `riassunto` -> `summary`, `richieste` -> `requests`, `riepilogo` -> `recap`, `righe` -> `lines`, `rimbalzata_at` -> `bounced_at`, `rimbalzate` -> `bounced`, `rinnovo` -> `renewal`, `risposta` -> `answer`, `risposta_at` -> `answered_at`, `risultati` -> `results`, `riusato` -> `reused`, `ruoli` -> `roles`, `ruolo` -> `role`, `saltate` -> `skipped`, `scadenze_fatturazione` -> `billing_deadlines`, `scheda` -> `card`, `scheda_da_rigenerare` -> `card_from_regenerate`, `segnali` -> `signals`, `seniority_incerta` -> `seniority_uncertain`, `settimana` -> `week`, `settori` -> `industries`, `sintesi` -> `profile_summary`, `situazione` -> `situation`, `spese` -> `expenses`, `stato` -> `status`, `stato_pagamento` -> `payment_status`, `stato_percorso` -> `journey_status`, `talenti` -> `talents`, `talenti_si` -> `self_talents`, `talenti_totale` -> `total_talents`, `tariffa_giornaliera` -> `daily_rate`, `tariffa_max` -> `max_rate`, `tariffa_min` -> `min_rate`, `telefono` -> `phone`, `testo` -> `text`, `testo_bozza` -> `draft_text`, `tipo` -> `kind`, `totale` -> `total`, `totale_lead` -> `total_lead`, `totale_ore` -> `hours_total`, `ultimi_7_giorni` -> `last_7_days`, `ultimi_accessi` -> `last_logins`, `ultimi_download_guida` -> `last_download_guide`, `ultimo_accesso` -> `last_access`, `ultimo_giorno_disdetta` -> `last_notice_day`, `unibile` -> `mergeable`, `unibili` -> `mergeable`, `unita` -> `unit`, `updated_by_nome` -> `updated_by_name`

**website** (2)

`descrizione` -> `description`, `persone` -> `people`

### Query and search parameters

**pigrocrm** (39)

| Italian | English | Note |
|---|---|---|
| `a` | `to` | Date-range end on GET /api/rebase/engagements/{match_id}/report (with `da`); called by the hub. |
| `anche_non_assegnate` | `include_unassigned` |  |
| `anno` | `year` |  |
| `apri` | `open` | Web search param: open this task. |
| `archiviate` | `archived` |  |
| `assegnata` | `assigned` | Web search param: filter by assignee. |
| `assegnata_a` | `assigned_to` |  |
| `azienda_id` | `legal_entity_id` |  |
| `chiudi_precedente` | `close_previous` |  |
| `da` | `from` | `?da=digest` on the Monday digest link (PostHog recognises the return from the mail). Emailed URL: old mails keep the old name. |
| `da_fatturare` | `to_invoice` |  |
| `data_prevista` | `expected_date` |  |
| `elimina_messaggi` | `delete_messages` |  |
| `escludi_consumate` | `exclude_consumed` |  |
| `esito` | `outcome` | OAuth callbacks redirect to the SPA with `?esito=collegato\|negato\|errore\|sessione`. An in-flight consent flow crossing a deploy would lose its message. |
| `fatturabile` | `billable` |  |
| `fatturato` | `invoiced` |  |
| `fatturato_non_vinto` | `invoiced_unwon` |  |
| `formato` | `format` |  |
| `mese` | `month` | Web search param /app/calendar?mese=YYYY-MM and API ?mese. |
| `mesi` | `months` |  |
| `nazione` | `country` |  |
| `numero` | `number` |  |
| `ordine` | `order` |  |
| `ore_preventivate` | `budgeted_hours` |  |
| `origine_proforma_id` | `origin_proforma_id` |  |
| `scade_entro` | `due_within` |  |
| `scadute` | `overdue` |  |
| `senza_scadenza` | `without_due_date` |  |
| `soglia` | `threshold` |  |
| `solo_deal_non_vinto` | `only_unwon_deals` |  |
| `solo_generali` | `only_general` |  |
| `stato` | `status` |  |
| `stato_pagamento` | `payment_status` |  |
| `tariffa_oraria` | `hourly_rate` |  |
| `tipo` | `type` |  |
| `tutti` | `all` |  |
| `valore_preventivato` | `budgeted_value` |  |
| `vista` | `view` | Web search param of /app/activities; value `oggi` means today. |

**hub** (28)

| Italian | English | Note |
|---|---|---|
| `chi` | `who` | `/hub/thanks?chi=freelance\|azienda` |
| `competenza` | `skill` |  |
| `con_accessi` | `with_logins` |  |
| `creato_a` | `created_to` |  |
| `creato_da` | `created_from` |  |
| `da` | `from` | `?da=<page>` attribution (where a visitor came from), written by the website into every link to the hub, and by the CRM digest (`?da=digest`). Also `da`/`a` (date range) ... |
| `descrizione` | `description` | `/hub/team?descrizione=` and the website form field name="descrizione"; also JSON body of POST /api/hub/team/proposals. |
| `documento` | `document` |  |
| `esito` | `outcome` |  |
| `fascia_max` | `max_band` |  |
| `fascia_min` | `min_band` |  |
| `firmato` | `signed` |  |
| `mese` | `month` | `/admin/matches/{id}/report?mese=YYYY-MM\|tutto`. |
| `modalita` | `mode` |  |
| `origine` | `origin` |  |
| `pagina_origine` | `origin_page` |  |
| `periodo_da` | `period_from` |  |
| `persone` | `people` | `/hub/team?persone=` (headcount 1..10) and website select name="persone"; also JSON body key. |
| `posizione` | `job_title` |  |
| `proposta` | `proposal` | `/hub/team?proposta=<id>`: link to a team proposal; emitted by the website landing (team.js) and by emails. |
| `r` | `r` | Answer code (`/hub/team/risposta?t=&r=`), unchanged. |
| `remoto` | `remote` |  |
| `rif` | `ref` | PUBLIC: members share `https://letsrebase.com/hub/?rif=CODE`. Also form field `rif` on POST /api/community/signups and the freelancer/company wizards, column signups.rif ... |
| `ruolo` | `role` |  |
| `stato` | `status` |  |
| `t` | `t` | Token param (English-neutral), unchanged: `/hub/verify?t=`, `/hub/disiscrizione?t=`. |
| `tariffa_max` | `max_rate` |  |
| `tariffa_min` | `min_rate` |  |

### MCP tools and prompts

**pigrocrm** (27)

| Italian | English | Note |
|---|---|---|
| `acknowledge_promemoria` | `acknowledge_reminder` | MCP tool name. |
| `archive_attivita` | `archive_task` | MCP tool name. |
| `cancel_attivita` | `cancel_task` | MCP tool name. |
| `chiusura-mese` | `month-close` | MCP prompt name (not a tool). Its arguments da/a/anno/mese/settimana become from/to/year/month/week. |
| `complete_attivita` | `complete_task` | MCP tool name. |
| `create_attivita` | `create_task` | MCP tool name. |
| `create_azienda` | `create_legal_entity` | MCP tool name. |
| `describe_azienda` | `describe_legal_entity` | MCP tool name. |
| `get_attivita` | `get_task` | MCP tool name. |
| `get_attivita_oggi` | `get_tasks_today` | MCP tool name. |
| `get_promemoria_preferences` | `get_reminder_preferences` | MCP tool name. |
| `list_attivita` | `list_tasks` | MCP tool name. |
| `list_aziende` | `list_legal_entities` | MCP tool name. |
| `list_my_promemoria_state` | `list_my_reminder_state` | MCP tool name. |
| `list_promemoria` | `list_reminders` | MCP tool name. |
| `list_promemoria_types` | `list_reminder_types` | MCP tool name. |
| `ore-da-registrare` | `hours-to-log` | MCP prompt name (not a tool). Its arguments da/a/anno/mese/settimana become from/to/year/month/week. |
| `propose_azienda` | `suggest_legal_entity` | MCP tool name. |
| `reopen_attivita` | `reopen_task` | MCP tool name. |
| `reset_promemoria_preference` | `reset_reminder_preference` | MCP tool name. |
| `restore_attivita` | `restore_task` | MCP tool name. |
| `revisione-pipeline` | `pipeline-review` | MCP prompt name (not a tool). Its arguments da/a/anno/mese/settimana become from/to/year/month/week. |
| `set_promemoria_preference` | `set_reminder_preference` | MCP tool name. |
| `snooze_promemoria` | `snooze_reminder` | MCP tool name. |
| `stato-cliente` | `organization-status` | MCP prompt name (not a tool); `customer-status` until REB-969. Its arguments da/a/anno/mese/settimana become from/to/year/month/week. |
| `update_attivita` | `update_task` | MCP tool name. |
| `update_azienda` | `update_legal_entity` | MCP tool name. |

**hub** (6)

| Italian | English | Note |
|---|---|---|
| `get_campagna` | `get_campaign` | MCP tool name. |
| `get_talento` | `get_talent` | MCP tool name. |
| `list_aziende` | `list_companies` | MCP tool name. |
| `list_campagne` | `list_campaigns` | MCP tool name. |
| `list_duplicati` | `list_duplicates` | MCP tool name. |
| `list_talenti` | `list_talents` | MCP tool name. |

### MCP parameters

**pigrocrm** (106)

`anno` -> `year`, `assegnata_a` -> `assigned_to`, `attivita_id` -> `task_id`, `azienda_id` -> `legal_entity_id`, `cadenza_fatturazione` -> `billing_cadence`, `canale` -> `channel`, `causale` -> `memo`, `chiudi_precedente` -> `close_previous`, `codice_fiscale` -> `tax_code`, `codice_sdi` -> `sdi_code`, `cognome` -> `last_name`, `competenza_a` -> `accrual_to`, `competenza_da` -> `accrual_from`, `comune` -> `city`, `confidenza` -> `confidence`, `costo_applicato` -> `applied_cost`, `data` -> `date`, `data_chiusura_prevista` -> `expected_close_date`, `data_emissione` -> `issue_date`, `data_incasso` -> `payment_date`, `data_prevista` -> `expected_date`, `data_scadenza` -> `due_date`, `dati` -> `data`, `descrizione` -> `description`, `divisa` -> `currency`, `durata_conteggio` -> `duration_count`, `durata_unita` -> `duration_unit`, `escludi_consumate` -> `exclude_consumed`, `estratto` -> `excerpt`, `fatturabile` -> `billable`, `fatturato` -> `invoiced`, `fine` -> `end`, `fornitore` -> `supplier`, `frazioni_ammesse` -> `allowed_fractions`, `giorni_pagamento` -> `payment_days`, `importo` -> `amount`, `indirizzo` -> `address`, `inizio` -> `start`, `interpretazione_adottata` -> `adopted_interpretation`, `limite` -> `limit`, `mese` -> `month`, `mittente` -> `sender`, `motivo_confidenza` -> `confidence_reason`, `nazione` -> `country`, `nome` -> `name`, `numero` -> `number`, `ordine_cig` -> `purchase_order_tender_code`, `ordine_cup` -> `purchase_order_project_code`, `ordine_data` -> `purchase_order_date`, `ordine_numero` -> `purchase_order_number`, `ore` -> `hours`, `ore_minime` -> `minimum_hours`, `ore_preventivate` -> `budgeted_hours`, `orizzonte_al` -> `horizon_until`, `pagamento_fine_mese` -> `payment_end_of_month`, `partita_iva` -> `vat_number`, `pec` -> `certified_email`, `periodo_erogazione` -> `delivery_period`, `politica_spese` -> `expense_policy`, `pre_autorizzata` -> `pre_authorized`, `preavviso_disdetta_giorni` -> `termination_notice_days`, `preavviso_rinnovo_giorni` -> `renewal_notice_days`, `probabilita` -> `probability`, `profilo_fiscale` -> `tax_profile`, `provincia` -> `province`, `quantita` -> `quantity`, `ragione_sociale` -> `legal_name`, `rate_card_frazioni_ammesse` -> `rate_card_allowed_fractions`, `rate_card_importo` -> `rate_card_amount`, `rate_card_ore_minime` -> `rate_card_minimum_hours`, `rate_card_periodo_erogazione` -> `rate_card_delivery_period`, `rate_card_tipo` -> `rate_card_type`, `rate_card_unita` -> `rate_card_unit`, `rate_card_valido_a` -> `rate_card_valid_to`, `rate_card_valido_da` -> `rate_card_valid_from`, `ricevuto_il` -> `received_at`, `riferimento_autorizzazione` -> `authorization_reference`, `riferimento_clausola` -> `clause_reference`, `righe` -> `lines`, `ruolo` -> `role`, `scade_entro` -> `due_within`, `scadenza` -> `due_date`, `senza_scadenza` -> `without_due_date`, `soglia` -> `threshold`, `solo_generali` -> `only_general`, `stato` -> `status`, `stato_pagamento` -> `payment_status`, `tariffa_applicata` -> `applied_rate`, `tariffa_oraria` -> `hourly_rate`, `telefono` -> `phone`, `termine` -> `deadline`, `testo` -> `text`, `testo_letterale` -> `literal_text`, `tetto_quota_cliente` -> `customer_share_cap`, `tipo` -> `type`, `tipo_estratto` -> `excerpt_type`, `tipo_rinnovo` -> `renewal_type`, `titolo` -> `title`, `tutti` -> `all`, `unita` -> `unit`, `valido_a` -> `valid_to`, `valido_da` -> `valid_from`, `valore_preventivato` -> `budgeted_value`, `valore_previsto` -> `expected_value`, `variabili` -> `variables`, `volume_atteso` -> `expected_volume`

**hub** (47)

`anni` -> `experience_years`, `anni_max` -> `max_years`, `anni_min` -> `min_years`, `budget_giornaliero` -> `daily_budget`, `campagna_id` -> `campaign_id`, `campi` -> `fields`, `cliente` -> `customer`, `codice_fiscale` -> `tax_code`, `cognome` -> `last_name`, `competenze` -> `skills`, `con_accessi` -> `with_logins`, `condizioni` -> `terms`, `creato_a` -> `created_to`, `creato_da` -> `created_from`, `domicilio` -> `domicile`, `durata` -> `duration`, `esito` -> `outcome`, `figura_richiesta` -> `requested_role`, `fonti` -> `sources`, `giorni_presenza` -> `onsite_days`, `giorni_previsti` -> `expected_days`, `lingue` -> `languages`, `luogo` -> `location`, `nome` -> `first_name`, `nome_azienda` -> `company_name`, `numero_risorse` -> `headcount`, `ordina` -> `sort`, `origine` -> `origin`, `pagina_origine` -> `origin_page`, `partita_iva` -> `vat_number`, `pec` -> `certified_email`, `periodo_da` -> `period_from`, `posizione` -> `job_title`, `progetto` -> `project`, `referente_cognome` -> `contact_last_name`, `referente_nome` -> `contact_first_name`, `remoto` -> `remote`, `riassunto` -> `summary`, `scheda` -> `card`, `settori` -> `industries`, `stato` -> `status`, `talento_id` -> `talent_id`, `tariffa_giornaliera` -> `daily_rate`, `tariffa_max` -> `max_rate`, `tariffa_min` -> `min_rate`, `telefono` -> `phone`, `testo` -> `text`

### CLI

**pigrocrm** (3)

| Italian | English | Note |
|---|---|---|
| `--data` | `--date` | `pigrocrm digest\|reminders\|automations --data YYYY-MM-DD`; cron lines and runbooks pass it. |
| `--forza` | `--force` | `pigrocrm digest --forza`. Help strings (Italian) stay. |
| `--nome` | `--name` | `pigrocrm createadmin`; the dispatch passes args.nome. |

**hub** (2)

| Italian | English | Note |
|---|---|---|
| `--cognome` | `--last-name` | `rebase setrole`; with it `--nome` means `--first-name`. |
| `--nome` | `--name` | `rebase createtoken --nome` (token name) and `rebase setrole --nome` (first name, paired with --cognome). |

### Cookies

**hub** (1)

| Italian | English | Note |
|---|---|---|
| `__obref` | `__obref` | Set by the website pixel; "obref" is the old Orbiters referral cookie, not an Italian word: unchanged. |

### Browser storage keys

**pigrocrm** (1)

| Italian | English | Note |
|---|---|---|
| `pigrocrm:register-handoff-email` | `pigrocrm:register-handoff-email` | English already. |

**hub** (4)

| Italian | English | Note |
|---|---|---|
| `rebase.rif` | `rebase.ref` | Remembers the referral code (`?rif=`). The 2026-10-19 precedent renamed rebase.* keys with a read-only fallback to the old name; do the same. |
| `rebase.team-risposta` | `rebase.team-answer` | Browser key holding the team-answer link token; the emailed link itself carries ?t= and ?r=. |
| `rebase.wizard.azienda` | `rebase.wizard.company` | localStorage draft of the company wizard; renaming forgets drafts in progress (low impact, no fallback needed or read the old key once). |
| `rebase.wizard.freelance` | `rebase.wizard.freelance` | English already (freelance is an accepted English loanword); unchanged. |

### Analytics events, properties and groups

**pigrocrm** (18)

| Italian | English | Note |
|---|---|---|
| `assistente_collegato` | `assistant_connected` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `cliente_creato` | `customer_created` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `clienti_importati` | `customers_imported` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `deal_creato` | `deal_created` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `digest_inviato` | `digest_sent` | PostHog server event (constant DIGEST_SENT). Properties settimana->week, sezioni->sections. |
| `documento_creato` | `document_created` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `entrato_con_invito` | `signed_in_with_invitation` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `entrato_con_link` | `signed_in_with_link` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `fattura_emessa` | `invoice_issued` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `invito_inviato` | `invitation_sent` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `invito_revocato` | `invitation_revoked` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `ore_registrate` | `hours_logged` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `profilo_emittente_salvato` | `legal_entity_profile_saved` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `ruolo` | `role` | Person property on identify, also event property from the MCP middleware. PostHog property/group key; renaming splits history for queries that filter on it. |
| `settimana` | `week` | Event property of digest_sent. PostHog property/group key; renaming splits history for queries that filter on it. |
| `sezioni` | `sections` | Event property. PostHog property/group key; renaming splits history for queries that filter on it. |
| `spazio` | `space` | PostHog GROUP TYPE for a CRM space (identifyGroup). Renaming the group type creates a new group set. PostHog property/group key; renaming splits history for queries that ... |
| `spazio_creato` | `space_created` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |

**hub** (14)

| Italian | English | Note |
|---|---|---|
| `guida_scaricata` | `guide_downloaded` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `iscrizione_completata` | `signup_completed` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `passi` | `steps` | Event property. PostHog property/group key; renaming splits history for queries that filter on it. |
| `passo` | `step` | Event property of wizard_step. PostHog property/group key; renaming splits history for queries that filter on it. |
| `persone_richieste` | `people_requested` | Event property of team_proposal_generated (the head count the ask carried). PostHog property names are not migrated: breakdowns on the old name split history. |
| `ruolo` | `role` | Person property on identify (admin\|member). PostHog property/group key; renaming splits history for queries that filter on it. |
| `schermata` | `screen` | Event property. PostHog property/group key; renaming splits history for queries that filter on it. |
| `team_proposta_generata` | `team_proposal_generated` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `team_richiesta_inviata` | `team_request_sent` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `team_talento_risposta` | `team_talent_answer` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `tipo` | `kind` | Event property (freelance\|azienda) on wizard_* and signup_completed; azienda value -> company. PostHog property/group key; renaming splits history for queries that filter... |
| `wizard_completato` | `wizard_completed` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `wizard_iniziato` | `wizard_started` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |
| `wizard_passo` | `wizard_step` | PostHog event name. Funnels, cohorts and dashboards in the PostHog project are keyed on it; a rename splits history unless PostHog gets an alias/merge or both names are s... |

### Other identifiers

**pigrocrm** (744)

Class, function and constant names follow the term table mechanically (for example `AttivitaService` is `TaskService`, `fattura_scaduta` is `overdue_invoice`). SQL constraint, index, trigger and function names are rederived from the renamed tables and columns. The full list is in `glossary.json`; first entries:

- `AGENTI` -> `Agents`
- `ANNO_MAX` -> `YEAR_MAX`
- `ANNO_MIN` -> `YEAR_MIN`
- `ATTIVITA_SORTS` -> `TASK_SORTS`
- `AUTORE_MAX_LENGTH` -> `AUTHOR_MAX_LENGTH`
- `AVVERTENZA` -> `WARNING`
- `AVVISI_PATH` -> `REMINDERS_PATH`
- `AppAvvisiRoute` -> `AppRemindersRoute`
- `AppAvvisiRouteImport` -> `AppRemindersRouteImport`
- `AppCalendarioRoute` -> `AppCalendarRoute`
- `AppCalendarioRouteImport` -> `AppCalendarRouteImport`
- `AppClientiCustomerIdRoute` -> `AppCustomersCustomerIdRoute`
- `AppClientiCustomerIdRouteImport` -> `AppCustomersCustomerIdRouteImport`
- `AppClientiIndexRoute` -> `AppCustomersIndexRoute`
- `AppClientiIndexRouteImport` -> `AppCustomersIndexRouteImport`
- `AppDealListaRoute` -> `AppDealListRoute`
- `AppDealListaRouteImport` -> `AppDealListRouteImport`
- `AppDocumentiDocumentIdRoute` -> `AppDocumentsDocumentIdRoute`
- `AppDocumentiDocumentIdRouteImport` -> `AppDocumentsDocumentIdRouteImport`
- `AppEntraRoute` -> `AppVerifyRoute`
- `AppEntraRouteImport` -> `AppVerifyRouteImport`
- `AppFattureIndexRoute` -> `AppInvoicesIndexRoute`
- `AppFattureIndexRouteImport` -> `AppInvoicesIndexRouteImport`
- `AppFattureInvoiceIdRoute` -> `AppInvoicesInvoiceIdRoute`
- `AppFattureInvoiceIdRouteImport` -> `AppInvoicesInvoiceIdRouteImport`
- `AppImpostazioniAutomazioniRoute` -> `AppSettingsAutomationsRoute`
- `AppImpostazioniAutomazioniRouteImport` -> `AppSettingsAutomationsRouteImport`
- `AppImpostazioniCampiRoute` -> `AppSettingsFieldsRoute`
- `AppImpostazioniCampiRouteImport` -> `AppSettingsFieldsRouteImport`
- `AppImpostazioniCategorieCostoRoute` -> `AppSettingsCategoriesCostRoute`
- `AppImpostazioniCategorieCostoRouteImport` -> `AppSettingsCategoriesCostRouteImport`
- `AppImpostazioniDriveRoute` -> `AppSettingsDriveRoute`
- `AppImpostazioniDriveRouteImport` -> `AppSettingsDriveRouteImport`
- `AppImpostazioniEmittenteRoute` -> `AppSettingsLegalEntityRoute`
- `AppImpostazioniEmittenteRouteImport` -> `AppSettingsLegalEntityRouteImport`
- `AppImpostazioniFiscaleRoute` -> `AppSettingsTaxRoute`
- `AppImpostazioniFiscaleRouteImport` -> `AppSettingsTaxRouteImport`
- `AppImpostazioniGmailRoute` -> `AppSettingsGmailRoute`
- `AppImpostazioniGmailRouteImport` -> `AppSettingsGmailRouteImport`
- `AppImpostazioniIndexRoute` -> `AppSettingsIndexRoute`

**hub** (343)

Class, function and constant names follow the term table mechanically (for example `AttivitaService` is `TaskService`, `fattura_scaduta` is `overdue_invoice`). SQL constraint, index, trigger and function names are rederived from the renamed tables and columns. The full list is in `glossary.json`; first entries:

- `ALTRE_CONDIZIONI_FIELDS` -> `OtherTermsFields`
- `ALTRE_CONDIZIONI_GROUPS` -> `OtherTermsGroups`
- `ALTRE_SECTIONS` -> `OtherSections`
- `ANNI_TOLERANCE` -> `EXPERIENCE_YEARS_TOLERANCE`
- `AUTORE_MAX_LENGTH` -> `AUTHOR_MAX_LENGTH`
- `AZIENDA_MAX_LENGTH` -> `COMPANY_MAX_LENGTH`
- `AZIENDE_FILTRI_EMPTY` -> `CompaniesFiltersEmpty`
- `AZIENDE_MAIN` -> `CompaniesMain`
- `AZIONE_BREVE` -> `ActionShort`
- `AZIONE_FATTA_LABELS` -> `ActionDoneLabels`
- `AZIONE_LABELS` -> `ActionLabels`
- `A_CORPO_FIELDS` -> `ToBodyFields`
- `Accedi` -> `Login`
- `AdminAccessi` -> `AdminLogins`
- `AdminAgenti` -> `AdminAgents`
- `AdminCampagna` -> `AdminCampaign`
- `AdminCampagne` -> `AdminCampaigns`
- `AdminConsuntivo` -> `AdminActuals`
- `AdminContratti` -> `AdminContracts`
- `AdminCreaAzienda` -> `AdminCreateCompany`
- `AdminCreaCampagna` -> `AdminCreateCampaign`
- `AdminCreaMatch` -> `AdminCreateMatch`
- `AdminDuplicati` -> `AdminDuplicates`
- `AdminGuida` -> `AdminGuide`
- `AdminModelliAi` -> `AdminModelsAi`
- `AdminProposteTeam` -> `AdminProposalsTeam`
- `AdminRichiestaTeam` -> `AdminRequestTeam`
- `AdminRichiesteTeam` -> `AdminRequestsTeam`
- `AdminTalenti` -> `AdminTalents`
- `AdminTalentoLead` -> `AdminTalentLead`
- `AiProva` -> `AiTest`
- `Anteprima` -> `Preview`
- `AziendeFiltri` -> `CompaniesFilters`
- `AziendeFiltriForm` -> `CompaniesFiltersForm`
- `BarraInvio` -> `BarSend`
- `CAMPAIGN_FONTI` -> `CAMPAIGN_SOURCES`
- `CLIENTE_EMPTY` -> `CustomerEmpty`
- `CLIENTE_FIELDS` -> `CustomerFields`
- `CLIENTE_PIVA_MAX_LENGTH` -> `CUSTOMER_VAT_NUMBER_MAX_LENGTH`
- `CODICE_FISCALE_MAX_LENGTH` -> `TAX_CODE_MAX_LENGTH`

**website** (2)

Class, function and constant names follow the term table mechanically (for example `AttivitaService` is `TaskService`, `fattura_scaduta` is `overdue_invoice`). SQL constraint, index, trigger and function names are rederived from the renamed tables and columns. The full list is in `glossary.json`; first entries:

- `#aziende #guida #come-funziona #domande #selezione #voci #orbita #dentro #eventi #worked-with (HTML ids / anchors)` -> `#companies #guide #how-it-works #faq #selection #entries #orbit #inside #events`
- `data-persone` -> `data-people`

### Files and directories

**pigrocrm** (52)

| Italian | English | Note |
|---|---|---|
| `pigrocrm/apps/web/e2e/aziende.spec.ts` | `pigrocrm/apps/web/e2e/legal_entities.spec.ts` |  |
| `pigrocrm/apps/web/e2e/calendario.spec.ts` | `pigrocrm/apps/web/e2e/calendar.spec.ts` |  |
| `pigrocrm/apps/web/e2e/oggi.spec.ts` | `pigrocrm/apps/web/e2e/today.spec.ts` |  |
| `pigrocrm/apps/web/src/features/attivita/` | `pigrocrm/apps/web/src/features/tasks/` | directory |
| `pigrocrm/apps/web/src/features/attivita/AttivitaDialog.test.tsx` | `pigrocrm/apps/web/src/features/tasks/TaskDialog.test.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/AttivitaDialog.tsx` | `pigrocrm/apps/web/src/features/tasks/TaskDialog.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/AttivitaItem.tsx` | `pigrocrm/apps/web/src/features/tasks/TaskItem.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/AttivitaMeta.tsx` | `pigrocrm/apps/web/src/features/tasks/TaskMeta.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/AttivitaPage.test.tsx` | `pigrocrm/apps/web/src/features/tasks/TaskPage.test.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/AttivitaPage.tsx` | `pigrocrm/apps/web/src/features/tasks/TaskPage.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/EntityAttivita.test.tsx` | `pigrocrm/apps/web/src/features/tasks/EntityTask.test.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/EntityAttivita.tsx` | `pigrocrm/apps/web/src/features/tasks/EntityTask.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/OggiView.test.tsx` | `pigrocrm/apps/web/src/features/tasks/TodayView.test.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/OggiView.tsx` | `pigrocrm/apps/web/src/features/tasks/TodayView.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/TutteView.test.tsx` | `pigrocrm/apps/web/src/features/tasks/AllView.test.tsx` |  |
| `pigrocrm/apps/web/src/features/attivita/TutteView.tsx` | `pigrocrm/apps/web/src/features/tasks/AllView.tsx` |  |
| `pigrocrm/apps/web/src/features/dashboard/periodo.test.ts` | `pigrocrm/apps/web/src/features/dashboard/period.test.ts` |  |
| `pigrocrm/apps/web/src/features/dashboard/periodo.ts` | `pigrocrm/apps/web/src/features/dashboard/period.ts` |  |
| `pigrocrm/apps/web/src/features/drive/esito.ts` | `pigrocrm/apps/web/src/features/drive/outcome.ts` |  |
| `pigrocrm/apps/web/src/features/gmail/esito.ts` | `pigrocrm/apps/web/src/features/gmail/outcome.ts` |  |
| `pigrocrm/apps/web/src/features/reminders/AvvisiPage.test.tsx` | `pigrocrm/apps/web/src/features/reminders/RemindersPage.test.tsx` |  |
| `pigrocrm/apps/web/src/features/reminders/AvvisiPage.tsx` | `pigrocrm/apps/web/src/features/reminders/RemindersPage.tsx` |  |
| `pigrocrm/apps/web/src/features/slack/esito.ts` | `pigrocrm/apps/web/src/features/slack/outcome.ts` |  |
| `pigrocrm/apps/web/src/lib/entra-token.test.ts` | `pigrocrm/apps/web/src/lib/verify-token.test.ts` |  |
| `pigrocrm/apps/web/src/lib/entra-token.ts` | `pigrocrm/apps/web/src/lib/verify-token.ts` |  |
| `pigrocrm/apps/web/src/routes/app/avvisi.tsx` | `pigrocrm/apps/web/src/routes/app/reminders.tsx` | Italian route files already exist only as redirect stubs for the English paths retired on 2026-09-21; the new decision either deletes them or keeps them as URL redirects ... |
| `pigrocrm/apps/web/src/routes/app/calendario.tsx` | `pigrocrm/apps/web/src/routes/app/calendar.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/calendar.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files alread... |
| `pigrocrm/apps/web/src/routes/app/clienti/` | `pigrocrm/apps/web/src/routes/app/customers/` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/customers/` already exists (the Italian path is a redirect stub or a twin). directory Italian route file... |
| `pigrocrm/apps/web/src/routes/app/deal/lista.tsx` | `pigrocrm/apps/web/src/routes/app/deal/list.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/deal/list.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files alrea... |
| `pigrocrm/apps/web/src/routes/app/documenti/` | `pigrocrm/apps/web/src/routes/app/documents/` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/documents/` already exists (the Italian path is a redirect stub or a twin). directory Italian route file... |
| `pigrocrm/apps/web/src/routes/app/entra.tsx` | `pigrocrm/apps/web/src/routes/app/verify.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/verify.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files already ... |
| `pigrocrm/apps/web/src/routes/app/fatture/` | `pigrocrm/apps/web/src/routes/app/invoices/` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/invoices/` already exists (the Italian path is a redirect stub or a twin). directory Italian route files... |
| `pigrocrm/apps/web/src/routes/app/impostazioni.tsx` | `pigrocrm/apps/web/src/routes/app/settings.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files alread... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/` | `pigrocrm/apps/web/src/routes/app/settings/` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings/` already exists (the Italian path is a redirect stub or a twin). directory Italian route files... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/automazioni.tsx` | `pigrocrm/apps/web/src/routes/app/settings/automations.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings/automations.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route ... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/campi.tsx` | `pigrocrm/apps/web/src/routes/app/settings/fields.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings/fields.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/categorie-costo.tsx` | `pigrocrm/apps/web/src/routes/app/settings/cost-categories.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings/cost-categories.tsx` already exists (the Italian path is a redirect stub or a twin). Italian ro... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/emittente.tsx` | `pigrocrm/apps/web/src/routes/app/settings/legal_entity.tsx` | Italian route files already exist only as redirect stubs for the English paths retired on 2026-09-21; the new decision either deletes them or keeps them as URL redirects ... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/fiscale.tsx` | `pigrocrm/apps/web/src/routes/app/settings/tax.tsx` | Italian route files already exist only as redirect stubs for the English paths retired on 2026-09-21; the new decision either deletes them or keeps them as URL redirects ... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/periodi.tsx` | `pigrocrm/apps/web/src/routes/app/settings/periods.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings/periods.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route file... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/profilo.tsx` | `pigrocrm/apps/web/src/routes/app/settings/profile.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings/profile.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route file... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/spazio.tsx` | `pigrocrm/apps/web/src/routes/app/settings/space.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings/space.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files ... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/tariffe.tsx` | `pigrocrm/apps/web/src/routes/app/settings/rates.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings/rates.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files ... |
| `pigrocrm/apps/web/src/routes/app/impostazioni/utenti.tsx` | `pigrocrm/apps/web/src/routes/app/settings/users.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/settings/users.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files ... |
| `pigrocrm/apps/web/src/routes/app/oggi.tsx` | `pigrocrm/apps/web/src/routes/app/today.tsx` | Italian route files already exist only as redirect stubs for the English paths retired on 2026-09-21; the new decision either deletes them or keeps them as URL redirects ... |
| `pigrocrm/apps/web/src/routes/app/ore.tsx` | `pigrocrm/apps/web/src/routes/app/hours.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/hours.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files already e... |
| `pigrocrm/apps/web/src/routes/app/persone/` | `pigrocrm/apps/web/src/routes/app/people/` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/people/` already exists (the Italian path is a redirect stub or a twin). directory Italian route files a... |
| `pigrocrm/apps/web/src/routes/app/registrati.tsx` | `pigrocrm/apps/web/src/routes/app/register.tsx` | COLLISION: target path `projects/pigrocrm/apps/web/src/routes/app/register.tsx` already exists (the Italian path is a redirect stub or a twin). Italian route files alread... |
| `pigrocrm/apps/web/src/routes/app/settings/aziende.tsx` | `pigrocrm/apps/web/src/routes/app/settings/legal-entities.tsx` | Italian route files already exist only as redirect stubs for the English paths retired on 2026-09-21; the new decision either deletes them or keeps them as URL redirects ... |
| `pigrocrm/packages/core/src/pigrocrm/core/render/assets/template-offerta-default.md` | `pigrocrm/packages/core/src/pigrocrm/core/render/assets/template-offer-default.md` |  |
| `pigrocrm/packages/core/tests/fixtures/fatturapa/fpr12-consulenza-marzo.xml` | `pigrocrm/packages/core/tests/fixtures/fatturapa/fpr-12-march-consulting.xml` |  |
| `pigrocrm/packages/core/tests/fixtures/fatturapa/fpr12-lotto-due-fatture.xml` | `pigrocrm/packages/core/tests/fixtures/fatturapa/fpr-12-batch-due-invoices.xml` |  |

**hub** (69)

| Italian | English | Note |
|---|---|---|
| `hub/apps/web/src/lib/entra-token.test.ts` | `hub/apps/web/src/lib/verify-token.test.ts` |  |
| `hub/apps/web/src/lib/entra-token.ts` | `hub/apps/web/src/lib/verify-token.ts` |  |
| `hub/apps/web/src/pages/Disiscrizione.test.tsx` | `hub/apps/web/src/pages/Unsubscribe.test.tsx` |  |
| `hub/apps/web/src/pages/Disiscrizione.tsx` | `hub/apps/web/src/pages/Unsubscribe.tsx` |  |
| `hub/apps/web/src/pages/TeamRisposta.test.tsx` | `hub/apps/web/src/pages/TeamAnswer.test.tsx` |  |
| `hub/apps/web/src/pages/TeamRisposta.tsx` | `hub/apps/web/src/pages/TeamAnswer.tsx` |  |
| `hub/apps/web/src/pages/admin/Accessi.test.tsx` | `hub/apps/web/src/pages/admin/Logins.test.tsx` |  |
| `hub/apps/web/src/pages/admin/Accessi.tsx` | `hub/apps/web/src/pages/admin/Logins.tsx` |  |
| `hub/apps/web/src/pages/admin/Agenti.test.tsx` | `hub/apps/web/src/pages/admin/Agents.test.tsx` |  |
| `hub/apps/web/src/pages/admin/Agenti.tsx` | `hub/apps/web/src/pages/admin/Agents.tsx` |  |
| `hub/apps/web/src/pages/admin/Campagna.test.tsx` | `hub/apps/web/src/pages/admin/Campaign.test.tsx` |  |
| `hub/apps/web/src/pages/admin/Campagna.tsx` | `hub/apps/web/src/pages/admin/Campaign.tsx` |  |
| `hub/apps/web/src/pages/admin/Campagne.test.tsx` | `hub/apps/web/src/pages/admin/Campaigns.test.tsx` |  |
| `hub/apps/web/src/pages/admin/Campagne.tsx` | `hub/apps/web/src/pages/admin/Campaigns.tsx` |  |
| `hub/apps/web/src/pages/admin/Consuntivo.test.tsx` | `hub/apps/web/src/pages/admin/MatchReport.test.tsx` |  |
| `hub/apps/web/src/pages/admin/Consuntivo.tsx` | `hub/apps/web/src/pages/admin/MatchReport.tsx` |  |
| `hub/apps/web/src/pages/admin/Contratti.test.tsx` | `hub/apps/web/src/pages/admin/Contracts.test.tsx` |  |
| `hub/apps/web/src/pages/admin/Contratti.tsx` | `hub/apps/web/src/pages/admin/Contracts.tsx` |  |
| `hub/apps/web/src/pages/admin/CreaAzienda.test.tsx` | `hub/apps/web/src/pages/admin/CreateCompany.test.tsx` |  |
| `hub/apps/web/src/pages/admin/CreaAzienda.tsx` | `hub/apps/web/src/pages/admin/CreateCompany.tsx` |  |
| `hub/apps/web/src/pages/admin/CreaCampagna.test.tsx` | `hub/apps/web/src/pages/admin/CreateCampaign.test.tsx` |  |
| `hub/apps/web/src/pages/admin/CreaCampagna.tsx` | `hub/apps/web/src/pages/admin/CreateCampaign.tsx` |  |
| `hub/apps/web/src/pages/admin/CreaCampagnaLink.test.tsx` | `hub/apps/web/src/pages/admin/CreateCampaignLink.test.tsx` |  |
| `hub/apps/web/src/pages/admin/CreaMatch.test.tsx` | `hub/apps/web/src/pages/admin/CreateMatch.test.tsx` |  |
| `hub/apps/web/src/pages/admin/CreaMatch.tsx` | `hub/apps/web/src/pages/admin/CreateMatch.tsx` |  |
| `hub/apps/web/src/pages/admin/Duplicati.test.tsx` | `hub/apps/web/src/pages/admin/Duplicates.test.tsx` |  |
| `hub/apps/web/src/pages/admin/Duplicati.tsx` | `hub/apps/web/src/pages/admin/Duplicates.tsx` |  |
| `hub/apps/web/src/pages/admin/Guida.test.tsx` | `hub/apps/web/src/pages/admin/Guide.test.tsx` |  |
| `hub/apps/web/src/pages/admin/Guida.tsx` | `hub/apps/web/src/pages/admin/Guide.tsx` |  |
| `hub/apps/web/src/pages/admin/ModelliAi.test.tsx` | `hub/apps/web/src/pages/admin/AiModels.test.tsx` |  |
| `hub/apps/web/src/pages/admin/ModelliAi.tsx` | `hub/apps/web/src/pages/admin/AiModels.tsx` |  |
| `hub/apps/web/src/pages/admin/ProposteTeam.test.tsx` | `hub/apps/web/src/pages/admin/TeamProposals.test.tsx` |  |
| `hub/apps/web/src/pages/admin/ProposteTeam.tsx` | `hub/apps/web/src/pages/admin/TeamProposals.tsx` |  |
| `hub/apps/web/src/pages/admin/RichiestaTeam.test.tsx` | `hub/apps/web/src/pages/admin/TeamRequest.test.tsx` |  |
| `hub/apps/web/src/pages/admin/RichiestaTeam.tsx` | `hub/apps/web/src/pages/admin/TeamRequest.tsx` |  |
| `hub/apps/web/src/pages/admin/RichiesteTeam.test.tsx` | `hub/apps/web/src/pages/admin/TeamRequests.test.tsx` |  |
| `hub/apps/web/src/pages/admin/RichiesteTeam.tsx` | `hub/apps/web/src/pages/admin/TeamRequests.tsx` |  |
| `hub/apps/web/src/pages/admin/SchedaAnonima.test.tsx` | `hub/apps/web/src/pages/admin/AnonymousCard.test.tsx` |  |
| `hub/apps/web/src/pages/admin/SchedaAnonima.tsx` | `hub/apps/web/src/pages/admin/AnonymousCard.tsx` |  |
| `hub/apps/web/src/pages/admin/contratti/` | `hub/apps/web/src/pages/admin/contracts/` | directory |
| `hub/apps/web/src/pages/admin/crea-campagna/` | `hub/apps/web/src/pages/admin/create-campaign/` | directory |
| `hub/apps/web/src/pages/admin/crea-campagna/Anteprima.tsx` | `hub/apps/web/src/pages/admin/create-campaign/Preview.tsx` |  |
| `hub/apps/web/src/pages/admin/crea-campagna/BarraInvio.tsx` | `hub/apps/web/src/pages/admin/create-campaign/SendBar.tsx` |  |
| `hub/apps/web/src/pages/admin/crea-campagna/Destinatari.tsx` | `hub/apps/web/src/pages/admin/create-campaign/Recipients.tsx` |  |
| `hub/apps/web/src/pages/admin/crea-campagna/Messaggio.tsx` | `hub/apps/web/src/pages/admin/create-campaign/Message.tsx` |  |
| `hub/apps/web/src/pages/admin/crea-match/` | `hub/apps/web/src/pages/admin/create-match/` | directory |
| `hub/apps/web/src/pages/admin/crea-match/CondizioniStep.tsx` | `hub/apps/web/src/pages/admin/create-match/TermsStep.tsx` |  |
| `hub/apps/web/src/pages/admin/crea-match/ControllaStep.tsx` | `hub/apps/web/src/pages/admin/create-match/ReviewStep.tsx` |  |
| `hub/apps/web/src/pages/member/Accedi.test.tsx` | `hub/apps/web/src/pages/member/Login.test.tsx` |  |
| `hub/apps/web/src/pages/member/Accedi.tsx` | `hub/apps/web/src/pages/member/Login.tsx` |  |
| `hub/apps/web/src/pages/member/Contratti.test.tsx` | `hub/apps/web/src/pages/member/Contracts.test.tsx` |  |
| `hub/apps/web/src/pages/member/Contratti.tsx` | `hub/apps/web/src/pages/member/Contracts.tsx` |  |
| `hub/apps/web/src/pages/member/Entra.test.tsx` | `hub/apps/web/src/pages/member/Verify.test.tsx` |  |
| `hub/apps/web/src/pages/member/Entra.tsx` | `hub/apps/web/src/pages/member/Verify.tsx` |  |
| `hub/apps/web/src/pages/member/Modifica.test.tsx` | `hub/apps/web/src/pages/member/Edit.test.tsx` |  |
| `hub/apps/web/src/pages/member/Modifica.tsx` | `hub/apps/web/src/pages/member/Edit.tsx` |  |
| `hub/apps/web/src/pages/member/ModificaAzienda.test.tsx` | `hub/apps/web/src/pages/member/EditCompany.test.tsx` |  |
| `hub/apps/web/src/pages/member/ModificaAzienda.tsx` | `hub/apps/web/src/pages/member/EditCompany.tsx` |  |
| `hub/apps/web/src/pages/member/NuovaRichiestaAzienda.test.tsx` | `hub/apps/web/src/pages/member/NewCompanyRequest.test.tsx` |  |
| `hub/apps/web/src/pages/member/NuovaRichiestaAzienda.tsx` | `hub/apps/web/src/pages/member/NewCompanyRequest.tsx` |  |
| `hub/content/contratti/` | `hub/content/contracts/` | directory |
| `hub/content/contratti/incarico.esempio.json` | `hub/content/contracts/engagement.example.json` |  |
| `hub/content/guida-primi-passi-freelance.md` | `hub/content/guide-first-steps-freelance.md` |  |
| `hub/deploy/firma.letsrebase.conf` | unchanged | named after the public host `firma.letsrebase.com`, a DNS name and not an identifier |
| `hub/packages/core/src/rebase_core/contracts/texts/contratto-quadro-occasionale.md` | `hub/packages/core/src/rebase_core/contracts/texts/framework-agreement-occasional.md` | Document template content; the file name is an identifier, the text inside stays Italian. |
| `hub/packages/core/src/rebase_core/contracts/texts/contratto-quadro.md` | `hub/packages/core/src/rebase_core/contracts/texts/framework-agreement.md` | Document template content; the file name is an identifier, the text inside stays Italian. |
| `hub/packages/core/src/rebase_core/contracts/texts/lettera-di-incarico-occasionale.md` | `hub/packages/core/src/rebase_core/contracts/texts/engagement-letter-occasional.md` | Document template content; the file name is an identifier, the text inside stays Italian. |
| `hub/packages/core/src/rebase_core/contracts/texts/lettera-di-incarico.md` | `hub/packages/core/src/rebase_core/contracts/texts/engagement-letter.md` | Document template content; the file name is an identifier, the text inside stays Italian. |
| `hub/packages/core/src/rebase_core/perks/rebase-guida-primi-passi-freelance.pdf` | `hub/packages/core/src/rebase_core/perks/rebase-guide-first-steps-freelance.pdf` |  |

**website** (3)

| Italian | English | Note |
|---|---|---|
| `/pitch/guida-1.webp` | `/pitch/guide-1.webp` | Static assets guida-1.webp and guida-2.webp under src/pitch; renaming changes asset URLs (hashed by vite, but referenced by pitch.html and og images). |
| `website/src/pitch/guida-1.webp` | `website/src/pitch/guide-1.webp` |  |
| `website/src/pitch/guida-2.webp` | `website/src/pitch/guide-2.webp` |  |

## Names that are read from outside

Some identifiers are part of a contract with something that does not deploy with this repository. Renaming them needs an alias, a data migration or a coordinated deploy (the 2026-10-19 cookie and storage-key rename, with its read-only fallback to the old name, is the precedent).

- Stored JSON and strings: timeline `activities.kind` and `payload` keys, `admin_actions.kind`/`payload`, proposal `campi_proposti`/`campi_accettati` keys, invoice `snapshot` keys, `space_settings.key` values, reminder type codes, hub `card`, `filtri`, `testo_bozza` and team JSON.
- Immutable rows: `approvals`, `work_unit_transitions` and issued invoices are protected by triggers; a rename migration must recreate the triggers.
- Public URLs: `/hub/?rif=CODE` (members share it), `/hub/disiscrizione` and `/hub/team/risposta` (in sent emails), `/hub/entra`, `/hub/accedi`, `/hub/aziende`, `/app/oggi` (Monday digest), `/app/entra`, `/app/registrati`, website `/termini` and in-page anchors such as `#aziende`.
- Server-to-server: the hub calls `PUT /api/rebase/engagements/{id}`, `GET .../report?da=&a=`, `PUT /api/rebase/leads/{id}` and `GET /api/tenants/`; the CRM calls `POST /api/hub/members/lookup`, `POST /api/hub/engagement-notices` and `POST /api/hub/referrer-name` (REB-1106); the website posts `descrizione` and `persone` to `/api/hub/team/proposals`.
- Analytics: PostHog event names, event and person properties (`tipo`, `ruolo`, `passo`, `settimana`) and the group type `spazio` (the CRM) feed funnels and dashboards.
- Cookies and browser keys: `rebase_user`, `pigrocrm_*`, `rebase.wizard.azienda`, `rebase.rif`, `rebase.team-risposta`.
- External systems: Documenso payload keys are English already; Gmail labels and Slack payloads carry no Italian identifiers of ours; the signing host `firma.letsrebase.com` is a hostname, not an identifier, and is outside this rename.


## Corrections made while building the rename codemod

The first version of the tables had the defects below; the codemod applies the corrected form and these notes are the reason.

- **Leading underscore.** 146 private identifiers lost their `_` in the English column (`_PARTITA_IVA` was `VAT_NUMBER`). A private name stays private: `_` is kept (`_VAT_NUMBER`).
- **`partita_iva` in the hub.** The regime constant `PARTITA_IVA` is `VAT_REGISTERED` (the value of the regime is `vat_registered`); the field of the same name is `vat_number`. A string `"partita_iva"` is renamed by hand because the same text is a field name in one place and a regime value in another; only `field_validator("...")`, `getattr(..., "...")` and the like follow the field.
- **`posizione` in the hub** is `job_title` on a freelancer and `position` (the slot, 1, 2, 3) on a member of a proposed team; it is not renamed automatically.
- **`data-stato`** (an HTML data attribute) is `data-status`, not `dateStatus`.
- **TypeScript file names are kebab-case** (`legal-entities.tsx`, `legal-entities.spec.ts`), also for route files, because the file name is the route path.
- **`/app/oggi` and the segment `oggi`.** The browser route `/app/oggi` is a redirect stub to the task list; the segment `oggi` in an API path is `today`.
- **The website reads the hub's API**, so its field names are the hub's (`anni` is `experience_years`, not the generic `years`).
- **`shared/analytics/entra-token.ts`** (moved there from the two web apps) is `verify-token.ts`, with `takeEntraToken` and `stripEntraToken` renamed `takeVerifyToken` and `stripVerifyToken`.
- **Legacy redirect stubs stay.** The 31 route files that only redirect from a retired Italian path stay as they are, with the nginx `return 301` blocks, because bookmarks and mailed links still use them. The pages renamed by this glossary get the same stub: CRM `/app/settings/aziende`, hub `/disiscrizione`, `/team/risposta`, `/admin/team/proposte`, `/admin/companies/nuova`, `/admin/modelli-ai`. Existing legacy redirect routes of the hub router keep their old path.
- **Words that are not renamed automatically** because their English form depends on the record: `nome` (`first_name` or `name`), `data` (`date` or plain `data`), `da` and `a` (`from` and `to`, `from` being a Python keyword), `attivita` (`task` or `tasks`), `predefinita`, `posizione` (hub), `partita_iva` as a string (hub). Everything else with one English target in a project is renamed everywhere in that project.
- **Template variables stay Italian**, so the scope builders that fill them (`_template_scope`, `build_scope`, `as_template_values`, `report_variables`, `variables_for`, the contract email and the hub contract field keys) keep their Italian string keys while the schemas they read are English.
