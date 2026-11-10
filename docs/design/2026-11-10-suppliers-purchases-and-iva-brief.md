# Brief: «Anagrafiche», «Acquisti» and «IVA» (PigroCRM)

Status: awaiting Lorenzo's answers on the artifact `pigrocrm-suppliers-purchases-iva`; draft
2026-11-10. Card: REB-996. Design:
`projects/pigrocrm/docs/superpowers/specs/2026-11-09-suppliers-purchases-and-iva-design.md`
§§ 1, 2, 5 to 9 (REB-961); frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` § A2 (modules), § A5 (the
fiscal register), § A7 (every new surface passes this gate), § B4 and § B6 (the sidebar and the
phone shell). The rules this brief creates will be rows of `docs/design/DECISIONS.md` once
Lorenzo has answered; until then everything marked «proposed» below is a proposal, and the
build cards read this file and those rows, never the artifact.

Everything here is drawn on `shared/ui/tokens.css` and the primitives of `@rebase/ui`:
squared, 1px ink lines, Outfit, Prussian sidebar, Watermelon Deep only on the primary action
and on errors (the application variant, DECISIONS 2026-09-18). It uses four of the five
`StatusPill` tones, `ink`, `muted`, `gold` and `danger`, and never `accent`, as the inbox brief
did. One light scheme. Strings are Italian, amounts `1.464,00 €`, dates `02/11/2026`, tabular
numerals in every column of figures, no emoji, no illustration. A wait is `LoadingState`
(`Loader` plus a `role="status"` sentence), never a skeleton (DECISIONS 2026-11-09 and
2026-11-10). Every figure in the artifact, and every example below, is invented. No screen
computes a fiscal rule: a rate, a share, a threshold or a date comes from the server, which
takes it from the fiscal pack (frame § A5, design § 6); the rules marked «to verify» there are
never stated as fact here.

## Settled before asking

A question already answered is a defect to ask again. Each row names its source.

| Question | Settled answer | Source |
|---|---|---|
| Look: shape, line, type, palette | Squared, 1px ink, Outfit, Prussian sidebar, Watermelon Deep for the primary action and errors only, one light scheme | DECISIONS 2026-09-18 |
| Density | Airy in reading, dense in entry: lists on the `Table` primitive's rows (48 px, 14 px cells, a two-line cell grows the row), forms two-column with the labels beside the fields and the action bar bottom right | Lorenzo's taste profile axes 1 and 6 (2026-08-20, kept outside the repo); application variant § 3 |
| Tables | Gridded and ruled, a 2px rule under the header, figures right-aligned in tabular numerals, 50 rows at a time with «Carica altri» or «Mostra altri» | taste axis 5; application variant § 3; `OrganizationsPage.tsx` |
| Empty and error states | One sentence on what lives here plus the single action; `QueryErrorBanner` with «Riprova» and no list under it | taste axis 7; inbox brief States |
| Loading | `LoadingState`, a sentence that names what is read | DECISIONS 2026-11-09 (REB-973) and 2026-11-10 (REB-981) |
| Status | A `StatusPill`: a dot, a colour and text, never colour alone; the tone map is a `Record` next to the labels | `StatusPill.tsx`; taste 2026-10-21 |
| Sidebar | Captioned sections by purpose, always open: «Da fare», «Vendite», «Amministrazione» | DECISIONS 2026-11-02 (REB-872); `nav.ts:44-70` |
| Learned navigation | A tab or entry the user has learned is a decision made; do not collapse it to redesign a screen | taste 2026-09-08 |
| The same element looks the same on every page | Page header `PageHeader` (icon, title, one sentence, the one strong button top right), filters in `FilterRow` with `FilterChips` and selects, tabs as «Proposte» and «In arrivo» draw them | taste 2026-10-21; `PageHeader.tsx`; `InvoicesList.tsx` |
| Phone | Below the desktop breakpoint one column of cards, figures before lists, the five-place bar with the centre Watermelon «+», everything else under «Altro» | DECISIONS 2026-11-09 (REB-956); frame § B6 |
| The phone «+» | Gains an expense once `purchases` exists | frame § B6 |
| Legal entities | The sidebar's «Azienda» select appears from the second entity; a list follows it; an «Azienda» column only under «Tutte le aziende»; a shared record (no entity) reads «Tutte» | `legalEntity.tsx`; `columns.tsx` of organizations (REB-625, REB-970) |
| A module that is off | Gone from the sidebar, the bar, the search and the Home; its route answers with the module's name; its data stays | frame § A2; `ModuleGate` |
| The registry | One record per organisation with the roles `customer`, `supplier`, `freelancer`; «Anagrafiche» filtered by role, «Clienti» and «Fornitori» its two default views; «niente doppioni» | design § 1; glossary «anagrafica» |
| Supplier views with `purchases` off | The `supplier` role's views and fields are hidden; the role stays on the row | design § 8 |
| What a purchase is | A document the space pays for: kind `invoice`, `credit_note`, `receipt` or `expense`, with the supplier optional on a receipt | design § 2 |
| Payment state | A purchase stores none: it is read from its due items (direction out), written through the cash design's service, so the column and filter exist only once the due-items milestone has shipped | design § 5; cash design § 1 |
| What the list answers | «Acquisti» lists documents by their date; «when does money move» is the cash schedule («Cassa», its own brief); the two are cross-linked, never summed | cash design § 4 |
| Filing from «In arrivo» | A certain FatturaPA files itself with its trace and an «Annulla»; a PDF or a photo is a proposal a person accepts; a foreign supplier asks a person to confirm reverse charge; accepting is never an agent's act | design §§ 2 and 7; frame § A4; DECISIONS 2026-10-27 (REB-806) |
| Provenance | Every read value says where it came from: «Dal file: dato certo», «Letto dal modello», «Letto dal tuo agente» | inbox brief «Signature» |
| Who may do what | A collaborator registers, updates, cancels a purchase and edits an organisation; marking a purchase paid is an administrator's act; the registers and settlements are read by administrators (the class of `get_fiscal_estimate`); closing or reopening an IVA period is an unscoped administrator's | design § 8; DECISIONS 2026-11-09 (REB-967); `permissions.ts:49,106,140,185` |
| The IVA cycle's reach | Ordinary-pack legal entities only; a flat-rate entity has no periodic settlement and a `non-it` entity no IVA register | design §§ 6 and 8 |
| Registers | Views over the documents, never a second copy, per period and legal entity; both include the integration documents TD16 to TD19 | design §§ 2 and 6 |
| Which period a document is in | The stored `iva_period`, not the document date; a closed IVA period blocks any write that changes a register | design § 6 |
| Cost | Costs are read by the purchase's date, not the payment date | design § 2 |
| Italian copy and formats | Strings 20% longer than English; `1.464,00 €`; `02/11/2026` | taste standing rules |

## Questions for Lorenzo

Six questions, each with exactly two defensible options. The artifact draws both at the same
scale, desktop and phone where they differ.

| # | Question | A | B | Recommendation |
|---|---|---|---|---|
| 1 | Where do «Clienti» and «Fornitori» sit in the sidebar? | One entry «Anagrafiche» in «Vendite», at «Clienti»'s place; Clienti, Fornitori and Tutte are tabs. Optimises the design's wording, one registry behind one door. Costs a supplier under a section about who buys from us. | «Clienti» stays in «Vendite»; «Fornitori» sits in «Amministrazione» beside «Acquisti»; both open the same page, pre-set to a role. Optimises sections by purpose and the learned place of «Clienti». Costs two doors to one page. | B: sections by purpose are decided (2026-11-02) and a learned entry is a decision made. |
| 2 | How is a purchase read from «Acquisti»? | A table with the open purchase in a 330 px panel (the inbox's shape): «Segna come pagata…» and «Apri la scheda» in the panel. Optimises staying on the list. Costs two readings of one record. | The row opens the purchase's own page, as «Fatture» does. Optimises the mirror of the neighbour entry and a readable record. Costs leaving the list. | B: «Acquisti» mirrors «Fatture», and a purchase's VAT lines, withholding and instalments do not fit 330 px. |
| 3 | How is a PDF or photo from «In arrivo» registered as a purchase? | A page «Registra come acquisto» with the file beside the form. Optimises checking against the original and one form for filing, editing and «Nuovo acquisto». Costs leaving the queue. | The «In arrivo» panel widens to about 540 px and becomes the form; the file is behind «Vedi il file». Optimises queue throughput. Costs the file is not beside the figures. | A: the reason to capture by hand is to check against the original. |
| 4 | What does «Spesa» open from the phone «+»? | A sheet with amount, merchant, category, date, «Già pagata», and an optional photo. Optimises speed and needs no camera. Costs the justifying document is often not attached. | The camera first, then three fields to confirm. Optimises keeping the document. Costs a second photo door beside «Carica un documento» (this one registers at once, that one waits to be read). | A: one photo door already exists; a second that behaves differently is the confusion. |
| 5 | What does «IVA» open on? | The entity's periods, one per row (period, open or closed, sales VAT, purchase VAT, credit, result, payment); a period opens its page. Optimises the year at a glance. Costs a step to the period used most. | The current period's page, with a period selector that names each period with its result. Optimises the common question with no click and the phone's figure-first rule. Costs no list to compare periods. | B: it answers the page's one question without a step and keeps figures before lists. |
| 6 | What is one row of an IVA register? | One row per document; a document with several rates says «2 aliquote»; a «Riepilogo per aliquota» under the table. Optimises scanning. Costs the groups of a mixed document, with their separate shares, are one step away. | One row per VAT group (rate, nature, deductible share) of a document, the document named on its first row. Optimises reconciling the settlement line by line. Costs a document repeats over rows. | A: the document is what a person recognises; the total of «Detraibile» is the settlement's own line. |

Not asked, because a source answers it or there is nothing to draw: the registry's columns, the
filters of «Acquisti», the figures strip, who sees what, the shape of the settlement (all under
«Inferred, not asked»); and nothing about fiscal law, which the design owns and the surface
only displays.

## «Anagrafiche»

```
Subject      The registry of every organisation the space deals with: a customer, a supplier,
             a freelancer, or several at once. One record per company, with its roles.
Audience     Whoever keeps the space's relationships: a founder, a collaborator. They know
             a customer from a supplier; they do not know that one table holds both.
The job      Find an organisation, see what it is to us (roles, terms, what we owe it or it
             owes us), add one without making a second record of a company already there.
Palette      As «In arrivo»: paper ground, white cards, Prussian ink and lines, Charcoal Blue
             secondary text, Watermelon Deep only on the one primary button and on errors.
             The roles are outlined chips, not states: no tone.
Type         Outfit only. 24 px title, 14 px row text, 12.5 px meta, tabular numerals, amounts
             right-aligned.
Density      Middle list (48 px rows); the new-organisation form dense two-column.
Signature    The role chips on a row and the one fact that matters per role: «Da pagare» on a
             supplier, the contact on a customer.
States       See the table below.
Constraints  Italian runs 20% longer: draw the 60-character company name and «Estero · extra
             UE · servizi». A foreign VAT number fits (`vat_number` is widened, design § 1).
             `shared/ui` primitives and tokens only. Phone first.
Non-goals    The merge of two organisations (its own card; the route exists, `POST
             /api/organizations/{id}/merge`); the freelancer view (C5); supplier IBANs (C4);
             removing a role.
```

**Desktop.** `PageHeader` with the icon, the title «Anagrafiche», one sentence («Le aziende con
cui lavori: clienti, fornitori o entrambi.») and the one strong button, which follows the view:
«Nuovo cliente» on «Clienti», «Nuovo fornitore» on «Fornitori», «Nuova anagrafica» on «Tutte».
Tabs, as «Proposte» does, with counts: «Tutte», «Clienti», «Fornitori» (the `role` parameter of
`organizations/queries.ts:29`: `any`, `customer`, `supplier`). Under Question 1 B the sidebar
entries «Clienti» and «Fornitori» open the same page on those tabs. The filter row keeps the
search box (ragione sociale, P.IVA, codice fiscale, email) and adds «Tipo fiscale» on
«Fornitori». Columns: «Clienti» keeps today's (ragione sociale, P.IVA, comune, email,
telefono, custom fields); «Fornitori»: ragione sociale, P.IVA, tipo fiscale (a foreign supplier
reads «Estero · extra UE · servizi»), pagamento («30 gg», «30 gg f.m.»), «Da pagare» (the open
due items, right-aligned; absent until due items exist); «Tutte»: ragione sociale with role
chips, P.IVA, comune, email. «Azienda» only under «Tutte le aziende» from the second entity.
A row opens the organisation's page, whose title carries the roles as chips; its tabs are
today's plus «Acquisti» when it is a supplier (the purchases list, filtered), and the
«Panoramica» holds one fact group per role it has: «Come cliente» (tipo di cliente, sostituto
d'imposta, codice destinatario or IPA code, our terms to it) and «Come fornitore» (tipo
fiscale, area and goods or services for a foreign one, the terms it grants us). «Aggiungi il
ruolo Fornitore» is in the header menu of a customer, and the reverse.

**Phone.** One column of cards under a segmented «Tutte / Clienti / Fornitori»; the supplier
view leads with one figure («Da pagare ai fornitori») and then the cards (name, chips, tipo
fiscale and terms, «Da pagare» on the right). A card opens the organisation full screen. The
centre «+» is unchanged. «Altro» lists «Anagrafiche» (Question 1 A) or «Clienti» and
«Fornitori» (B).

| State | What is drawn |
|---|---|
| Empty | «Clienti»: today's «Nessun cliente. Creane uno per iniziare.» and «Nuovo cliente». «Fornitori»: «Nessun fornitore. Compaiono quando registri una fattura passiva o ne crei uno.» and «Nuovo fornitore». |
| Loading | `LoadingState`, «Leggo le anagrafiche…», in the place of the rows. |
| Error | `QueryErrorBanner` and «Riprova»; no list under it. |
| Many / long | «Carica altri» as today; a 60-character name truncates in the cell with the full text as its title and wraps on the phone. |
| Duplicate | Typing a VAT number that a live organisation has shows, under the field, «Esiste già: Zanetti Impianti S.r.l. (cliente)» and the action «Aggiungi il ruolo Fornitore»; saving a second record for it is not offered. Two matches are refused as `match_legal_entity` refuses them. |
| Module off (`purchases`) | No «Fornitori» tab or entry, no «Come fornitore» group, no «Acquisti» tab; a stale link to the view says «Il modulo Acquisti è spento: un amministratore può accenderlo in Impostazioni → Moduli.» |
| Profile | `solo` and `company` see the same entries: both default `purchases` on (frame § A2); only the module decides. |
| Nothing to show | A customer-only space with `purchases` on shows an empty «Fornitori» with its sentence; nothing is hidden by regime. |
| Permissions | A read-only member lists and reads, with no create button; a collaborator creates and edits (`create_organization`, `update_organization`); the owner field stays an administrator's (`change_customer_owner`). A member scoped to some legal entities sees only theirs. |

## «Acquisti»

```
Subject      The purchases of the space: a supplier's invoice or credit note, a till
             receipt, an expense. Each with its supplier, its dates, its amounts, whether and
             when it is paid.
Audience     An administrator or a collaborator who pays suppliers and feeds the accountant:
             they know an invoice, a due date, a withholding; not SdI formats.
The job      See what was bought and what is still to pay; register a purchase that arrived
             as a PDF or a photo; correct one. On the phone: add an expense in two taps.
Palette      As «In arrivo». Payment pills: gold «Da pagare», danger «Scaduta», ink «Pagata»,
             gold «Pagata in parte» (with «1 di 2 rate»), muted for a cancelled or credit
             note. Watermelon Deep only on «Nuovo acquisto» and errors.
Type         Outfit only; 24 px title, 14 px rows, a 24 px figure in the strip.
Density      Middle (48 px rows, two-line first cell); the form dense two-column; phone cards
             airy.
Signature    The «Fornitore e documento» cell: the supplier over «Fattura 2026/0418», with a
             chip for what is special («Inversione contabile», «Nota di credito»).
States       See the table below.
Constraints  Italian copy 20% longer; a foreign amount shows its currency under the euro one.
             The payment column and filter wait for the due-items milestone. Phone first.
Non-goals    Paying or matching a payment (C4, «Cassa»); the collaborators' payment rules
             (C5); staff expense notes (C7); the accountant export (C8); bulk actions.
```

**Desktop.** `PageHeader` with the icon (not the `Receipt` of «Fatture»), the title
«Acquisti», one sentence, and the one strong button «Nuovo acquisto» (the same form as the
capture, blank, with the file optional). Under it a strip of three figures that follow the
filters: «Registrati nel periodo» (with the count), «Da pagare» and «Scaduti». The filter row
follows «Fatture»: `FilterChips` for the payment state («Tutti», «Da pagare», «Scaduti»,
«Pagati»), selects for «Fornitore» (a search-as-you-type picker, as `OrganizationPicker`),
«Categoria» and «Tipo» (fattura, nota di credito, scontrino, spesa), and the `PeriodPicker`
(«Dal / al» with «Mese», «Trimestre», «Anno»), carried in the URL. The period filters the
document date. Columns: fornitore e documento, categoria, data, scadenza (a purchase in
instalments reads «30/11/2026 · rata 2 di 2»), importo (the document total; a foreign one has
«USD 540,00» in its first cell), pagamento (the pill; a purchase with withholding adds «netto
1.530,00 €»). «Azienda» only under «Tutte le aziende» from the second entity. The row opens
the purchase (Question 2). The page of a purchase has `Panoramica` (documento, righe IVA with
the deductible share, ritenuta and the net to pay, the costo imputato the server froze), the
right column with «Scadenze» (each due item and its state) and «Origine» (from «In arrivo»,
its date, its provenance tag), `Documenti` and `Timeline`. A reverse-charge purchase has an
«Integrazione» block: its type (TD16 to TD19, chosen by the server), its state («Da produrre»,
«Prodotta», «Trasmessa») and, for an administrator with full access, «Produci l'XML» and «Segna
come trasmessa»; the deadline reminder's date is the pack's.
**Capture** (Question 3): from «In arrivo» the primary «Registra come…» on a supplier invoice or
receipt opens the form on the item. The form, dense two-column: fornitore (matched by VAT
number or tax code, or «Nuovo fornitore: verrà creato»), azienda (a select only when the
buyer matches no single entity), tipo, numero, data documento, ricevuta il, periodo IVA (the
options the pack allows, only for an entity with registers), importo and currency, with the
euro amount and the rate shown as read-only («Cambio BCE 0,9206 del 29/10/2026»), the VAT
lines (aliquota or natura, imponibile, imposta, quota detraibile defaulting from the category),
ritenuta (shown only when the buyer is a withholding agent and the supplier's kind is
professional or occasional), cassa e bollo, the flags «Scissione dei pagamenti» and
«Inversione contabile» (for a foreign supplier the person decides: «Lo decidi tu: il fornitore
è estero»), categoria, deal (hidden with `deals` off), scadenze (from the file, else the
supplier's terms, with the tag «Dalle condizioni del fornitore») and pagamento («Già pagata» or
«Da pagare»). Each value read carries its provenance tag. The action bar is bottom right,
«Annulla» and «Registra». A certain XML needs none of this: it is registered with «Registrata
da In arrivo il 03/11/2026. Annulla» and the item turns «Registrata».

**Phone.** One column of cards under a segmented «Tutti / Da pagare / Scaduti / Pagati» and a
«Filtri» button that opens a sheet (fornitore, categoria, tipo, periodo). The figure «Da pagare»,
with «di cui scaduti», leads; a card has supplier, amount, «Fattura 14/E · 28/10/2026» and the
pill. A card opens the purchase full screen with a sticky «Segna come pagata…» (administrators)
and «Apri il file». The capture is the same form in one column with «Vedi il file» at the top
(the file opens over it), «Scarta…» and «Registra» sticky. The centre «+» gains «Spesa»
(Question 4): a receipt, total only, paid, with amount, esercente, categoria and data, and an
optional photo; «Carica un documento» stays for what must be read.

| State | What is drawn |
|---|---|
| Empty | «Nessun acquisto. Qui compaiono le fatture dei fornitori, gli scontrini e le spese: carica un documento o registra un acquisto.» and «Nuovo acquisto». |
| Loading | `LoadingState`, «Leggo gli acquisti…». |
| Error | `QueryErrorBanner` and «Riprova»; no list and no strip under it, so no figure is offered that never arrived. |
| Many / long | 50 at a time with «Mostra altri (212 in tutto)»; a 60-character supplier truncates with a title; the strip and the count say how many match the filters. |
| Foreign currency | The euro amount in the column, the original under the document line; the record shows the rate and its date. |
| Credit note | Amount negative, chip «Nota di credito», the payment cell «riduce la fattura 22» (or «rimborso» when nothing is left to reduce); it never has a pill of its own. |
| Cancelled | Hidden by default; the chip «Annullati» shows them, muted, with their reason. |
| In a closed IVA period | A purchase whose `iva_period` is closed shows «Periodo IVA chiuso: non si può modificare.» in place of «Modifica» and «Annulla acquisto» (design § 6); marking it paid is not blocked. |
| Module off (`purchases`) | The entry is gone; a stale link says which module to switch on; an «In arrivo» item that needs it waits and names it (inbox brief). |
| Module not built / due items not built | Until the due-items milestone ships there is no payment column, no payment chips, no «Da pagare» or «Scaduti» figure and no sentence about a module to switch on. |
| Pack | An entity on `non-it` has no IVA fields (no periodo IVA, no righe IVA); one on the flat-rate pack shows the total as the cost and, for a reverse-charge purchase, the VAT it owes as a due item, with no register. The form shows what the pack gives it, never what a regime name suggests. |
| Partial / failed | A purchase whose due items could not be written is registered and shows «Scadenze da riprovare» with «Riprova»; a failed save keeps the form and says what to fix. |
| Permissions | A collaborator registers, updates and cancels; a read-only member lists and reads; «Segna come pagata…» and «Produci l'XML» are shown to administrators only (`settle_outgoing_due_item` is `admin`, `permissions.ts:106`); a shared purchase (no entity) is visible only under «Tutte le aziende». |

## «IVA»

```
Subject      The IVA cycle of one legal entity: the sales register, the purchase register
             and the settlement of each period, with the period's lock.
Audience     An administrator who runs the IVA with an accountant: they know a register and a
             settlement; they do not want the app to teach them the law.
The job      Know how much IVA is due or in credit for a period, see the documents that make
             it, close the period when the accountant agrees. On the phone: the figure.
Palette      As «In arrivo». State pills: gold «Aperto», ink «Chiuso»; payment pills: gold
             «Da versare», ink «Versato», muted «A credito». Watermelon Deep only on «Chiudi
             il periodo» and errors.
Type         Outfit only; the settlement figure at 24 px, the ladder in 14 px tabular
             numerals.
Density      Middle in the registers (dense rows allowed: they are read as ledgers), a ruled
             ladder for the settlement.
Signature    The settlement ladder: IVA sulle vendite, minus IVA detraibile sugli acquisti,
             minus the credit carried, then «Da versare» in bold. Each line only if the pack
             gives it.
States       See the table below.
Constraints  Never a computed rule on screen: rate, share, threshold, deadline and minimum are
             the server's, from the pack; a deadline is shown with «dal pacchetto fiscale».
             Taxes are never summed across entities. Phone first.
Non-goals    Transmitting the «LIPE» or the annual return (design § 6); pro-rata; Intrastat;
             the accountant's export (C8); a separate list of integration documents; paying
             the settlement (C4, «Cassa»: its due item is already in the schedule).
```

**Desktop.** The entry «IVA» is in «Amministrazione» after «Acquisti» (the order the money
moves: «Fatture», «Acquisti», «IVA»). It is shown to administrators, and only when at least one
legal entity's pack has registers (the pack decides, not the regime's name). The page always
shows one entity: the sidebar's choice, or, under «Tutte le aziende» with several eligible
entities, the page's own «Azienda» select (the sentence «L'IVA non si somma tra aziende.»
sits under the title). Question 5 decides the landing; the period's page is the same either
way: `PageHeader` «IVA · 4° trimestre 2026» with the entity, the period select and the pill
«Aperto», and the one strong button «Chiudi il periodo» (an unscoped administrator's act). On
it: the settlement ladder in a card at the right, and two tabs of registers, «Registro vendite»
and «Registro acquisti», with their counts. The sales register lists the issued invoices and
credit notes with data, numero, cliente, imponibile, aliquota or natura, IVA, esigibilità
(from the document: immediata, differita, scissione) and totale; an annulled number is a muted
row «Annullata», and a number the space declared it will never carry is a row with its reason
(the register gaps that exist today). The purchase register lists data di registrazione,
fornitore and document, aliquota, imponibile, IVA, quota and IVA detraibile; a document's
integration (TD16 to TD19) appears in both registers, with the chip «Integrazione TD17», and a
chip filter «Integrazioni» narrows either. Question 6 decides the row. Both registers are
read-only: a row opens the document it comes from. «Chiudi il periodo» asks «Chiudere il 4°
trimestre 2026 per Lumen Studio S.r.l.? Dopo, nessuna registrazione di questo periodo si
modifica. Un amministratore può riaprirlo.»; a closed period shows a band «Periodo chiuso il
16/11/2026 da Giulia Ferri» and «Riapri». The deadline of the payment is a line under the ladder
with «dal pacchetto fiscale»; the due item is already in the schedule.

**Phone.** «IVA» is under «Altro» (a second-class destination by the frame's rule). It opens on
the figure (Question 5 B), «Da versare · 4° trimestre 2026», its deadline and the pill, then the
three ladder lines as one card, then a segmented «Acquisti / Vendite» of cards (supplier or
customer, document, date, the VAT figure; Question 6). The period select is a sheet. «Chiudi il
periodo» and «Riapri» are sticky and shown to an unscoped administrator only. With Question 5 A
the first screen is the periods as cards.

| State | What is drawn |
|---|---|
| Empty | A period with no document: the ladder with zeros and «Nessuna registrazione in questo periodo.» in each register. An entity with no sales yet: the same sentence. |
| Loading | `LoadingState`, «Leggo i registri…». |
| Error | `QueryErrorBanner` and «Riprova»; no ladder, so no figure is offered that never arrived. |
| Many / long | A register pages 50 at a time with «Mostra altri»; the totals row is the period's, not the page's. |
| Open | Pill «Aperto»; the result is provisional («in corso»), never styled as final. |
| Closed | Pill «Chiuso», the band with who and when, «Riapri» for an unscoped administrator; a purchase or an invoice dated in it shows the lock where it would be edited. |
| Credit | The result is negative: pill «A credito», the figure «-310,00 €», and the next period's ladder carries it as «Credito del periodo precedente». |
| Below a minimum | When the pack says a result is carried forward instead of paid, the line says «Riportato al periodo successivo» with the pack's reason; nothing on screen computes it. |
| Flat-rate or `non-it` entity | No «IVA» entry for it; a space with only such entities has no entry at all. A flat-rate buyer's reverse-charge VAT is a due item shown on the purchase, not here. |
| `purchases` off | The design says both that a space with it off still sees its sales register (§ 8) and that the IVA cycle is part of `purchases` (§ 8): proposed here, the page stays and shows the sales register alone, with the sentence «La liquidazione richiede il modulo Acquisti.» in place of the ladder and the purchase tab. |
| Scoped member | A read-only or scoped member does not see the entry; a scoped administrator reads the registers of their entities and is not offered «Chiudi il periodo» («Solo un amministratore senza limiti all'azienda può chiudere il periodo.»). |
| Permissions | Administrators read; a collaborator and a read-only member do not see the entry, and a link answers «L'IVA è visibile agli amministratori.» |
| Partial / failed | A register that could not be read says so and shows no total; the ladder is drawn only when both registers answered. |

## What a component can violate

Proposed; each is checkable. The rows that depend on a question say which, and are written for
the option marked as recommended.

- «Anagrafiche» is one page on the `organizations` list with a role view (`role` of
  `organizations/queries.ts:29`); a «Fornitori» view is never a second list component (Q1: both
  options).
- The sidebar has «Acquisti» after «Fatture» and «IVA» after «Acquisti» in «Amministrazione»;
  with Q1 B «Fornitori» sits before «Acquisti»; each entry is gone with its module and the
  entry «IVA» with the pack (Q1, Q5).
- The title of the registry page is «Anagrafiche» on every view, and the active view is a tab,
  never a second title.
- A role chip is an outlined chip, not a `StatusPill`; a payment state is a `StatusPill`
  whose tone comes from a `Record` next to its labels, and never uses `accent`.
- «Acquisti» is a gridded table whose row opens the purchase's own page; the filters are
  `FilterChips` and selects in `FilterRow`, and the period is `PeriodPicker` in the URL (Q2 B).
- The payment column, filter and the «Da pagare» and «Scaduti» figures read the due items and are
  absent, not empty, before the due-items milestone ships.
- Marking a purchase paid and producing or transmitting an integration XML are shown only to a
  role that may do them (`can(role, 'settle_outgoing_due_item')`), never shown and refused.
- The purchase form is one component for registering from «In arrivo», for «Nuovo acquisto» and
  for editing; the file opens beside it on desktop and over it on the phone (Q3 A).
- A value the form shows as read carries its provenance tag; the euro amount, the rate and its
  date are read-only, never typed (design § 4).
- The form shows the withholding block only when the server says the buyer is a withholding agent
  and the supplier's kind carries one; it shows the VAT fields only for an entity whose pack has
  them. No component compares a regime's name.
- A reverse-charge decision on a foreign supplier is made by a person, on the form, never
  preselected as final by a model or an agent (design § 7).
- «Spesa» on the phone writes a `receipt` or `expense` purchase paid at once and never leaves a
  due item open unless the person chose «Da pagare» (Q4 A).
- «IVA» draws one legal entity at a time and never a sum across entities; a register is a view
  over the documents with a link to each, never an editable table.
- A figure or a date on the settlement, a deadline, a minimum, a share, is rendered from a field
  the server sent; no component multiplies, adds a percentage or picks a date (the register `A5`
  stays «to verify» in code, not on screen).
- «Chiudi il periodo» and «Riapri» are shown only to an unscoped administrator, and a closed
  period's documents say the period is closed where they would be edited.
- Every list and page of the three surfaces has the empty, loading and error states of its table;
  loading is `LoadingState`, never `Skeleton`.

## Inferred, not asked

These follow from the design and the code and were not shown to Lorenzo; a reviewer should push
back on any.

- **Routes and modules.** `/app/purchases` and `/app/vat` (English routes, the 2026-11-02
  rule), «Acquisti» owned by `purchases` in `ROUTE_MODULES`; «IVA» needs a finer guard than the
  table, which maps a path to modules and cannot say «the sales register is core, the settlement
  needs `purchases`». The registry view is `?role=`, with `any` for «Tutte».
- **Icons.** «Acquisti» and «IVA» each need an icon that is not `Receipt` (the icon of «Fatture»)
  or `Building2`; «Fornitori» in Q1 B reuses `Building2`.
- **Registry columns** per view as above, the supplier column «Da pagare» from due items, the
  role chips on «Tutte», the title fixed at «Anagrafiche», and the primary button following the
  view.
- **Duplicate catch** under the VAT field as the person types, with «Aggiungi il ruolo»: the
  design's unique index and the two-match refusal say what is refused, not how the form says it.
- **Role removal** is not drawn: the design adds roles and says nothing on taking one away
  (reported below).
- **«Acquisti» filters**: payment state chips «Da pagare», «Scaduti», «Pagati» (an open
  purchase with a part paid is «Da pagare» with its «Pagata in parte» pill); the period filters
  the document date, not the due date or the IVA period; cancelled purchases hidden by default;
  the supplier select is a search picker.
- **The figures strip** («Registrati nel periodo», «Da pagare», «Scaduti») follows the filters
  and reads the open due items after withholding (the net to pay).
- **Payment pill labels and tones**: «Da pagare» gold, «Scaduta» danger, «Pagata» ink, «Pagata
  in parte» gold, «Previsto» muted for a forecast item; a credit note and a cancelled purchase
  show no payment pill.
- **A receipt or an expense** is registered «Già pagata» by default and an invoice «Da pagare»;
  the design does not say whether a receipt gets a due item (reported below).
- **Capture form** fields, the tag «Dalle condizioni del fornitore» for a computed due date, the
  tag «Calcolato» for the rate, the provenance tags reused from the inbox brief, and the lines
  «Registrata da In arrivo il … Annulla» for a certain XML.
- **«Integrazione» block** on the purchase page, with the three states and the two actions for
  an administrator with full access; no separate list of integration documents (a chip filter
  in the registers instead).
- **Visibility of «IVA»**: administrators only, the web mirror of the class of
  `get_fiscal_estimate` (`permissions.ts:185`), since the design classes reading the registers
  and settlements as full access (§ 8) and gives no web role; a scoped administrator reads and
  cannot close.
- **The settlement ladder** shows a line only when the pack gives one; the page reads the
  deadline and the minimum from the same data.
- **The IVA page with `purchases` off** shows the sales register alone (design § 8, first and
  last bullets disagree; reported below).
- **Phone placement**: «IVA» and «Fornitori» or «Anagrafiche» under «Altro», not in the bar;
  «Acquisti» likewise.
- **Deal page.** The «Costi» tab of a deal and of the hours page becomes the deal's purchases,
  with the columns of «Acquisti» that apply; not drawn here (the list is the same component).
- **Sample copy** that the design does not name: «Registra come acquisto», «Nuovo acquisto»,
  «Segna come pagata…», «Chiudi il periodo», «Riapri», «Spesa», the band and empty sentences.

## Not in this brief

- «Cassa» and the schedule, bank accounts, statements and matching (C4, its own brief): «Acquisti»
  and «IVA» link to it; the order of its entry in «Amministrazione» is that brief's.
- The merge of two organisations (its own card; `POST /api/organizations/{id}/merge` exists).
- The freelancer view of the registry and the engagements (C5); staff and expense notes (C7).
- The accountant's export (C8), the «LIPE», the annual return.
- The «Impostazioni» sections for a supplier's fiscal defaults and for the legal entity's
  settlement frequency (monthly or quarterly) and cash-basis option: they sit in existing
  sections and carry no new structure.

## Gaps and contradictions found

- Design § 8 says a space with `purchases` off still sees its sales register (the IVA cycle reads
  core invoices) and, a bullet later, that the IVA cycle is part of `purchases`. Proposed above:
  the page stays, sales register alone.
- Design § 5 stores no payment state on a purchase and § 9 asks for the payment-state filter «from
  C4»; the C3 cards that write due items are blocked by the C4 milestone, so the list ships first
  without it (States).
- The design does not say whether a till receipt or an expense gets a due item or is settled at
  registration; proposed: paid by default, the person may choose «Da pagare».
- The design adds roles and never removes one (§ 1); who may and what happens to purchases that
  use it is open.
- Costs have no sidebar page today: `CostsPanel` without a deal (`only_general`,
  `features/costs/CostsPanel.tsx:24`) has no caller, so a general expense is unreachable from the
  UI and «Acquisti» is the first list of general spending.
- `ROUTE_MODULES` (`lib/modules.ts:50`) maps one path to modules; it cannot express a page whose
  parts have different owners (IVA).
- The cash design also adds «Cassa» to «Amministrazione» (§ 6): the section will hold six
  entries; the order is left to that brief.
- Design § 8 lists reading purchases as «collaborator» in a table about agents; the web gates only
  writes, so a read-only member listing purchases is inferred, not stated.
