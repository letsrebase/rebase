# Prestazione occasionale: contracts for members without a partita IVA

Linear: REB-687. Builds on `2026-09-23-matches-and-contract-signing-design.md` and
`2026-09-25-simpler-matches-and-mcp-design.md`, which stay the authority on what a
match, a framework agreement and a letter are. This document adds a second family of
the same two documents, for members who work without a partita IVA.

## Why

A member without a partita IVA has a job through rebase. Ivan wants it on the hub as a
match, like every other one, paid as a prestazione occasionale (gross, a lump sum or a
day rate), with the entity in `REBASE_SIGNER_JSON` as the committente. Whether the
committente withholds depends on its own tax position.

The hub cannot do it today. The tax data requires an 11-digit partita IVA in four
places (`FiscalData`, `freelancer_fiscal.partita_iva NOT NULL`, the admin form and
`set_freelancer_tax_data`), and both contract texts state that the Professionista
works with a partita IVA and invoices with VAT. A match made for a member without a
partita IVA would print false statements about them.

Ivan chose, on 2026-10-12, between three shapes (one standalone document, a letter
with another text, or a framework plus a letter) the third: **a framework agreement
for occasional work plus a letter of a dedicated type, chosen when the match is
made**. He asked me to write both texts.

## What changes

### 1. A regime on every contract document

`contract_documents` gets a `regime` column, `partita_iva` or `occasionale`, NOT NULL,
backfilled to `partita_iva` (migration 0033; renumber if another open PR takes 0033
first, see the hub migration rule). A CHECK pins the two values. The kind constraints
do not change: a quadro still has no match and no number, a letter still has both. The
downgrade refuses while any `occasionale` document exists or any tax data has no
partita IVA, since dropping the column would re-typeset occasional documents with the
ordinary text.

The pair (kind, regime) names the text:

| kind    | regime        | text                                    |
|---------|---------------|-----------------------------------------|
| quadro  | partita_iva   | `contratto-quadro.md`                   |
| quadro  | occasionale   | `contratto-quadro-occasionale.md`       |
| lettera | partita_iva   | `lettera-di-incarico.md`                |
| lettera | occasionale   | `lettera-di-incarico-occasionale.md`    |

`DOCUMENT_BY_KIND` becomes a function of both, and `render.DOCUMENTS` and
`contracts-check` cover four texts. The PDF file name carries the text's name
(`contratto-quadro-occasionale-v1.0.pdf`, not `contratto-quadro-v1.0.pdf` twice), and so
do the signing mail and the Documenso title (`mail.document_name`).

**A match's regime is its letter's regime.** Before the letter exists, the
`MatchCreate` payload carries it (section 3). A freelancer may hold quadri of both
regimes over time (a member opens a partita IVA later: their next match needs the
ordinary quadro, the occasional one stays until it is cancelled or lapses), so **every
lookup of a freelancer's framework agreement takes a regime**, active or pending alike:

- in `matches.py`: `active_framework`, `pending_framework`, the stale-`generato`
  cancellation in `create` (it cancels only quadri of the match's regime),
  `_framework_step` (behind `check` and `prefill`), `write_framework`, and the
  `nuova_versione` call sites (`matches.py` 392, 483, 960);
- in `framework.py`: `framework_state` and `framework_states`, keyed by
  (freelancer, regime), which feed `match_words` from `get`, `list_all` and
  `for_freelancer`;
- in `signing.py`: `send_match`, `_framework_to_send`, `_send_waiting` (the letter
  leaves only when the quadro **of its regime** is active), `_to_finish`,
  `_release_letters` (signing a quadro releases only the waiting letters of that
  quadro's regime), and the `nuova_versione` call site (1060).

A pending ordinary quadro (`inviato`) does not stop an occasional match from writing
its own occasional quadro, and the reverse.

**One quadro per regime in the payloads.** `FreelancerContracts.quadro` keeps meaning
the ordinary quadro (active, else the latest pending, as today); a new
`quadro_occasionale` holds the occasional one by the same rule. `quadri` keeps listing
all, each with its regime. `MemberContracts` follows the same split, so an active
occasional quadro is not filed under «precedenti» and a pending one shows its signing
link. The admin and member «Match e contratti» pages render one quadro card per regime
present, each with its own actions (Annulla, Reinvia, Registra disdetta). MCP
`get_match` attaches the quadro of the match's regime.

### 2. The partita IVA becomes optional

`freelancer_fiscal.partita_iva` becomes nullable (same migration). `FiscalData` accepts
it omitted, null or empty (stored as NULL); when given it is still 11 digits.
`FiscalRead.partita_iva` is `str | None`. The codice fiscale (16 characters, or 11
digits for a firm), the address and the optional PEC do not change. The audit trail
keeps naming fields, never values.

The admin form (`FiscalFields`) gets a checkbox «Senza partita IVA (prestazione
occasionale)». It is ticked exactly when tax data is saved with a NULL partita IVA; when
ticked it clears and hides the P.IVA field, when unticked the field stays required as
today. The address keeps one label, «Domicilio». `fiscalLine` prints «senza partita
IVA» instead of a P.IVA. On the web side `FiscalData.partita_iva` is `string | null`
(`lib/api.ts`); `draftFromFiscal` maps NULL to an empty field and the ticked box,
`toFiscalData` maps the ticked box to NULL, and `fiscalToSave` treats NULL and an empty
string as the same value, so «Avanti» does not save again when nothing changed.
`set_freelancer_tax_data` takes `partita_iva` as optional: omitted or empty means none.

### 3. The letter type is chosen when the match is made

`MatchCreate`, `MatchPrefill` and `MatchCheck` carry `regime`. The prefill proposes
`occasionale` when the freelancer's tax data is saved with no partita IVA, and
`partita_iva` otherwise, including when no tax data is saved yet. An ordinary letter
for a freelancer whose saved tax data has no partita IVA is refused, with the sentence
«Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale"
o salva la partita IVA.» An occasional letter for someone who has a partita IVA is
allowed, because occasional work outside their professional activity exists, and the
check adds the sentence «Ha la partita IVA: la prestazione occasionale vale solo per un
lavoro fuori dalla sua attività professionale.» The occasional texts never print a
partita IVA and never state that the collaborator has none.

In «Crea match», step 1 («Chi e per chi») shows, under the tax data, «Tipo di lettera»
with two options, «Incarico con partita IVA» and «Prestazione occasionale», preselected
from the prefill. The choice drives the rest: which quadro the check looks for, the
step 3 sentences and the PDF links.

`preview_match` and `create_match` take an optional `regime` (`partita_iva` or
`occasionale`, default the prefill's). `get_match`, `list_matches` and the
«Match e contratti» cards, admin and member side, name the type of each document:
«Contratto quadro», «Contratto quadro per prestazioni occasionali», «Lettera di
incarico», «Lettera di incarico occasionale».

### 4. Words without VAT

`match_words.check_sentences` and the cards say «IVA esclusa» today. For an occasional
letter they say «lordi, senza IVA»: «Compenso: 3.000,00 € a corpo, lordi, senza IVA,
pagato a 30 giorni fine mese.» `WHAT_LEAVES_FIRST` gets occasional versions of its
three sentences (quadro `da_inviare`, `in_firma`, `attivo`), naming «il contratto quadro
per prestazioni occasionali» and «la lettera di incarico occasionale». The step 2 label
«Compenso, IVA esclusa (€)» (`lib/contracts.ts`) becomes «Compenso (€)» with the hint
«IVA esclusa» or «lordo, senza IVA» by regime. `termine-pagamento`, which
`contracts/fields.py` writes as «N giorni data fattura [fine mese]», reads «N giorni
data ricevuta [fine mese]» on an occasional letter. The MCP docstrings that promise
«IVA esclusa» say it holds for the ordinary letter.

### 5. The two texts

Both start at `version: "1.0"`, `status: final`, dated 12 ottobre 2026. The occasional
quadro uses the ordinary quadro's placeholders minus `professionista-piva` and
`rebase-codice-destinatario`. The occasional letter uses the ordinary letter's
placeholders with `professionista-cf` in place of `professionista-piva`: the letter
form does not change, but the fields the hub fills itself become per regime
(`LETTER_AUTO_FIELDS` for the ordinary letter, an occasional set with
`professionista-cf`), `_letter_party_fields` fills `professionista-cf` from the tax
data, and the two tests that compare a text's fields with the form and the example
(`test_contract_schemas.py`, `test_contract_pdf.py`) run on every letter text. The
ordinary article numbers are kept so the letter can cite them; the one new clause is
5.7.

#### 5.1 `contratto-quadro-occasionale.md`

A copy of `contratto-quadro.md` 1.0 with these changes and no others.

- **Front matter.** Title «Contratto quadro per prestazioni occasionali», subtitle «Le
  regole di ogni prestazione occasionale che un membro della community svolge tramite
  rebase».
- **Throughout.** «Professionista» becomes «Collaboratore» (with its articles: «il
  Collaboratore», «al Collaboratore»); the collaborator's «fattura» becomes «ricevuta»,
  including the heading «6. Rendiconto e ricevuta» and 6.2, whose last words read «e la
  parte non contestata si paga comunque».
- **Parties.** rebase's paragraph drops the codice destinatario. The collaborator's
  reads: «e **{{professionista-nome}}**, codice fiscale {{professionista-cf}}, con
  domicilio in {{professionista-domicilio}}, email {{professionista-email}}, PEC
  {{professionista-pec}} (il «**Collaboratore**»),».
- **Premessa B.** «Il Collaboratore svolge le prestazioni regolate da questo Contratto
  come lavoro autonomo occasionale, senza abitualità né continuità e fuori da
  un'attività professionale esercitata con partita IVA. Ha l'esperienza per svolgere le
  attività che accetterà ed è membro della community.»
- **Premessa C**, last sentence. «Ogni lavoro sarà descritto in una lettera di incarico
  occasionale, con il cliente, le attività, i tempi e i numeri, e resta una prestazione
  a sé.»
- **1, Lettera di incarico.** «il documento che descrive un Incarico, la lettera di
  incarico occasionale, e ne fissa le condizioni particolari.»
- **4.1.** «Il Collaboratore svolge ogni Incarico come lavoratore autonomo occasionale,
  secondo gli articoli 2222 e seguenti del codice civile, senza vincolo di
  subordinazione e senza coordinamento continuativo con rebase o con il Cliente. Il
  Contratto e gli Incarichi non creano tra le Parti, né tra il Collaboratore e il
  Cliente, un rapporto di lavoro subordinato né una collaborazione coordinata e
  continuativa.»
- **5.5.** «Il Compenso è lordo. La prestazione occasionale è fuori dal campo di
  applicazione dell'IVA.»
- **5.6**, last words. «…e se sono documentate nella ricevuta.»
- **5.7, new.** «Il Collaboratore dichiara a rebase, prima di firmare ogni Lettera di
  incarico, i compensi da lavoro autonomo occasionale già percepiti nell'anno da
  chiunque, e la avvisa subito se, insieme ai Compensi di rebase, superano 5.000 euro.
  Oltre quella soglia sulla parte eccedente sono dovuti i contributi alla Gestione
  separata dell'INPS, ripartiti come prevede l'articolo 44, comma 2, del decreto-legge
  269/2003: rebase trattiene dal Compenso la quota a carico del Collaboratore e li
  versa.»
- **6.3.** «Approvato il Rendiconto, il Collaboratore emette a rebase una ricevuta per
  prestazione occasionale per il Compenso del mese, con la marca da bollo da 2 euro
  quando l'importo supera 77,47 euro. Per gli Incarichi a corpo emette la ricevuta alle
  scadenze indicate nella Lettera di incarico, dopo che il Cliente ha accettato i
  risultati o, se non arriva una contestazione motivata, dieci giorni lavorativi dopo la
  consegna.»
- **6.4.** «rebase opera la ritenuta d'acconto del 20% quando la legge la obbliga a
  operarla, e in quel caso il Collaboratore la indica nella ricevuta. Altrimenti rebase
  paga il Compenso lordo per intero e il Collaboratore lo dichiara nella propria
  dichiarazione dei redditi. Il Collaboratore avvisa subito rebase se apre una partita
  IVA.»
- **7.1.** «rebase paga ogni ricevuta con bonifico a 30 giorni data ricevuta fine mese,
  salvo che la Lettera di incarico indichi un altro termine, e in ogni caso entro 60
  giorni dal ricevimento della ricevuta, come prevede l'articolo 3 della legge
  81/2017.»
- **11.4.** «Se il Collaboratore apre una partita IVA per l'attività che svolge negli
  Incarichi, lo dice subito a rebase e le Parti firmano il contratto quadro per chi
  lavora con partita IVA. Il lavoro svolto da quel momento negli Incarichi in corso
  prosegue con nuove lettere di incarico secondo quel contratto; il lavoro svolto prima
  resta regolato da questo Contratto.» No unilateral immediate withdrawal, so nothing
  joins the specific approval list.
- **12.2.** «…il 15% degli importi che riceve da quell'azienda, al netto dell'IVA se
  dovuta.» **12.4.** «…su tutti gli importi ricevuti da quell'azienda…».
- **17.1.** «Il Contratto e le Lettere di incarico si firmano…» stays; «Professionista»
  becomes «Collaboratore» as everywhere.

Everything else, including 9.1 (twelve months, renewing), Allegato A and the specific
approval list, is the ordinary text with the word change. Keeping the renewal keeps the
code that computes «prossimo rinnovo» and «ultimo giorno per la disdetta» the same for
both regimes; the occasional nature rests on 4.1, Premessa B and each letter standing
alone. A renewing framework for occasional work is a legal trade-off, listed for the
accountant under «Outside the code».

#### 5.2 `lettera-di-incarico-occasionale.md`

```markdown
---
title: Lettera di incarico occasionale
subtitle: "n. {{numero}}, secondo il Contratto quadro per prestazioni occasionali firmato il {{data-contratto-quadro}}"
version: "1.0"
date: 12 ottobre 2026
status: final
---

Tra **{{rebase-ragione-sociale}}** («**rebase**») e **{{professionista-nome}}**, codice
fiscale {{professionista-cf}} (il «**Collaboratore**»), che hanno firmato il Contratto
quadro per prestazioni occasionali il {{data-contratto-quadro}}, si conviene la
prestazione occasionale descritta qui sotto (l'«**Incarico**»). Le parole con l'iniziale
maiuscola hanno il significato che dà loro il Contratto quadro, che vale per tutto ciò
che questa Lettera non dice.

## 1. Cliente

{{cliente-ragione-sociale}}, partita IVA {{cliente-piva}}, con sede in
{{cliente-sede}}.

## 2. Attività

**Ruolo:** {{ruolo}}.

**Cosa fa il Collaboratore:** {{attivita}}

**Risultati da consegnare** (solo per un Incarico a corpo): {{risultati}}

**Come il Cliente accetta i risultati** (solo per un Incarico a corpo): {{accettazione}}

## 3. Tempi e impegno

- **Inizio:** {{data-inizio}}.
- **Fine prevista:** {{data-fine}}.
- **Impegno:** {{impegno}}.
- **Periodo iniziale di verifica:** {{periodo-verifica}}.

Se l'Incarico è iniziato prima della firma, questa Lettera regola anche l'attività già
svolta dalla data di inizio.

## 4. Modalità di lavoro

- **Luogo:** {{luogo}}.
- **Incontri con il Cliente:** {{coordinamento}}.
- **Referente del Cliente:** {{referente-cliente}}.
- **Referente di rebase:** {{referente-rebase}}.

Il Collaboratore organizza autonomamente la propria attività, senza vincoli di orario e
con mezzi propri (articolo 4 del Contratto quadro). Gli incontri indicati qui sopra
servono solo a consegnare e a verificare il lavoro.

## 5. Condizioni economiche

| Voce                          | Condizione                                                       |
|-------------------------------|------------------------------------------------------------------|
| Modalità                      | {{modalita}}                                                     |
| Compenso lordo, senza IVA     | {{compenso}} {{unita}}                                           |
| Lavoro festivo o fuori fascia | {{lavoro-extra}}                                                 |
| Spese                         | {{spese}}                                                        |
| Pagamento                     | {{termine-pagamento}}                                            |
| Ricevute, solo a corpo        | {{scadenze-fatturazione}}                                        |

Il Compenso è lordo e la prestazione è fuori dal campo dell'IVA. È concordato a partire
dalla tariffa giornaliera che il Collaboratore ha chiesto (articolo 5 del Contratto
quadro). Il Collaboratore invia il Rendiconto ed emette la ricevuta come dice
l'articolo 6 del Contratto quadro; ritenuta d'acconto e contributi seguono gli articoli
6.4 e 5.7.

**Consuntivazione.** Il Collaboratore registra le ore lavorate per l'incarico in
PigroCRM, nel progetto che rebase predispone nel suo spazio all'avvio dell'incarico, con
cadenza almeno settimanale e comunque prima di ogni ricevuta. rebase legge le ore di
quel progetto per rendicontare l'incarico al Cliente e per riscontrare le ricevute del
Collaboratore, e non accede ad altro dello spazio.

## 6. Preavviso

Ciascuna Parte può recedere dall'Incarico con {{giorni-preavviso}} giorni di preavviso
(articolo 10 del Contratto quadro).

## 7. Dati personali

- **Il Collaboratore tratta dati personali di cui il Cliente è titolare:**
  {{dati-personali}}.
- **Natura e finalità del trattamento:** {{dati-finalita}}.
- **Categorie di dati:** {{dati-categorie}}.
- **Categorie di interessati:** {{dati-interessati}}.
- **Autorizzazione scritta del Cliente alla nomina:** {{dati-autorizzazione}}.

Se il Collaboratore tratta questi dati, con questa Lettera rebase lo nomina
sub-responsabile del trattamento per la durata dell'Incarico, alle condizioni
dell'Allegato A del Contratto quadro.

## 8. Condizioni particolari

- **Esclusiva verso il Cliente:** {{esclusiva}}.
- **Citazione nel portfolio:** {{portfolio}}.
- **Assicurazione di responsabilità civile professionale:** {{assicurazione}}.
- **Segnalato da:** {{azienda-segnalata-da}}.
- **Altro:** {{altre-condizioni}}.

## 9. Rapporti precedenti con il Cliente

Il Collaboratore dichiara, secondo l'articolo 3.3 del Contratto quadro:
{{rapporti-precedenti}}.

## Firme

Luogo e data: {{luogo-firma}}, {{data-firma}}

| Per rebase                           | Il Collaboratore                     |
|--------------------------------------|--------------------------------------|
| {{rebase-rappresentante}}            | {{professionista-nome}}              |
| {{firma-rebase}}                     | {{firma-professionista}}             |
```

## Not changing

- The match states, the letter counter (occasional letters take the next `YYYY-NNN`
  like any letter), Documenso, the webhook, `refresh` and `sweep`.
- Pay modes: an occasional letter is `a giornata` or `a corpo` like an ordinary one, so
  referral rewards (`reward_base` reads `modalita`) and the Pigro link (an active match
  is `da_collegare`) work unchanged.
- Who signs for rebase: `REBASE_SIGNER_JSON`.
- The PostHog warehouse: if `contract_documents` or `freelancer_fiscal` is synced, the
  warehouse contract test decides whether `regime` joins the allowlist; the fiscal
  values never do.

## Outside the code

- **First case.** After the release, the first occasional match is saved as a draft
  from the admin and leaves for signature only when Ivan says so.
- **For the accountant**, before the first occasional letter leaves:
  - the comunicazione preventiva to the Ispettorato del Lavoro, due from a committente
    that is an imprenditore before occasional autonomous work starts (art. 14 D.Lgs.
    81/2008 as amended in 2021);
  - a framework for occasional work that renews every 12 months (9.1) against the
    occasional nature the texts claim;
  - 11.4, work done after a partita IVA is opened;
  - whether the committente must withhold the 20% ritenuta d'acconto, which depends on
    its own tax regime (text 6.4 covers both cases).

## Testing

- **Migration 0033:** backfill to `partita_iva`, the CHECK on `regime`, a NULL
  partita IVA accepted; the downgrade drops `regime` and restores NOT NULL, and stops
  with a clear error while a row has no partita IVA or any `occasionale` document
  exists (dropping the column would re-typeset those with the ordinary text).
- **Tax data:** omitted, null and empty partita IVA saved as NULL; 11 digits still
  enforced when given; audit names the field.
- **Matches:** the prefill proposes the regime from the tax data (and `partita_iva`
  with none saved); an ordinary letter without a partita IVA is refused with the
  sentence; an occasional letter with a partita IVA carries the warning; an occasional
  match writes an occasional quadro when none is active, reuses an active one, and
  ignores both an active and a pending (`inviato`) ordinary quadro; the stale-`generato`
  cleanup leaves the other regime's quadri alone; `for_freelancer` returns `quadro` and
  `quadro_occasionale` side by side.
- **Signing**, with the fake Documenso: signing the occasional quadro releases the
  waiting occasional letter and not a waiting ordinary one, and the reverse; an
  occasional letter whose ordinary quadro is active still waits; the match turns
  `attivo` on the letter's signature; the PDF file names and mail titles differ by
  regime.
- **Texts:** both new texts render to PDF, `contracts-check` passes on four texts, the
  form and the example cover every field of both letters, and the two occasional
  Markdown texts contain no `professionista-piva`, no «IVA esclusa» and no «fattura».
- **Words:** «lordi, senza IVA», «data ricevuta», and the three occasional
  `WHAT_LEAVES_FIRST` sentences.
- **MCP:** `set_freelancer_tax_data` without a partita IVA; `preview_match` and
  `create_match` with and without `regime`; `get_match` attaches the quadro of the
  match's regime.
- **Web:** the checkbox in `Fiscal.test.tsx` (ticked from a NULL, no second save on
  «Avanti»), the letter type in `CreaMatch.test.tsx`, one quadro card per regime and
  the labels on both «Match e contratti» pages.
