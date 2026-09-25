# Simpler matches, and the same actions over MCP

Linear: milestone «Simpler matches, and the same actions over MCP» in P-REB-21
(REB-476, REB-477, REB-478, REB-479). Builds on
`2026-09-23-matches-and-contract-signing-design.md`, which stays the authority on what a
match, a framework agreement and a letter are; this document changes how an admin
reaches them, not what they are.

## Why

Ivan ran the whole flow on the preview on 2026-09-25, from «Crea match» to a signed
letter, and found it too long and too hard to read (his words: «troppo verboso», «tante
info e non si capisce cosa è cosa»). Five steps ask for every field of the letter,
thirty of them, most of which the hub already knows or nobody needs. «Match e contratti»
is a table of states («Generato», «In firma», «In attesa del contratto quadro») that the
admin has to translate into what happened and what to do next. And none of it can be
done by an agent: the MCP server reads matches, but creating and sending one needs the
browser.

He asked for: a wizard that asks only what it must and prefills the rest; information
shown directly, with names that say what they mean; every action reachable over MCP;
the echo logo (not the lockup) on the contract PDFs, as it already is on Documenso.

## What changes

### 1. «Crea match»: three steps instead of five (REB-476)

Same route (`/admin/freelance/$id/match/new`), same API writes, same idempotent match
id, same guards against a late prefill or preview. The company's `budget_giornaliero`
still never shows.

**Step 1, «Chi e per chi».** The freelancer is the page's own, named in the title. The
admin picks the company request from the searchable list that exists today. Once
picked, the step shows, below the list:

- *The client on the letter*: ragione sociale, P.IVA, sede, prefilled as today (the
  same company user's last match, else the request's company name). Shown as one line
  with «Modifica» when all three are filled; as three fields when any is empty.
- *The freelancer's tax data*: when saved, one line («Dati fiscali salvati: CF …,
  P.IVA …») with «Modifica»; when missing, the four fields, required. They are saved
  on «Avanti» only when they were missing or edited.

**Step 2, «Condizioni».** Only what changes from one engagement to the next, prefilled:

| Field | Prefilled from |
| --- | --- |
| Ruolo | the request's `figura_richiesta` |
| Cosa farà | the request's `progetto` |
| Inizio, Fine prevista (facoltativa) | the request's `periodo_da`; none |
| Impegno | the request's `durata` |
| Dove | `luogo_suggestion` |
| Come si paga: «A giornata» / «A corpo» | «A giornata» |
| Compenso, IVA esclusa (€) | the freelancer's day rate |
| Pagamento a N giorni, fine mese sì/no | `rebase.json` |

«A corpo» adds its three fields (risultati, accettazione, scadenze di fatturazione) to
this step. Every other field of the letter (referenti, coordinamento, periodo di
verifica, lavoro extra, spese, preavviso, dati personali, esclusiva, portfolio,
assicurazione, altre condizioni, rapporti precedenti) sits in one closed disclosure,
«Altre condizioni (facoltative)», in the letter's own groups, with the prefill the hub
already has. Nothing in it is required.

**Step 3, «Controlla e invia».** No field. The hub writes what is about to happen in
sentences, from a new check endpoint (section 3), for example:

> Ada Lovelace lavorerà per Azienda Prova S.r.l. come Backend developer, da remoto,
> dal 1 ottobre 2026, per 3 mesi.
> Compenso: 450 € a giornata, IVA esclusa, pagato a 30 giorni fine mese.
> Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua firma.

Then two links, «Apri la lettera (PDF)» and, when one is written, «Apri il contratto
quadro (PDF)», the same unsaved previews as today. The primary button is «Invia ad Ada
per la firma», the secondary «Salva senza inviare». After a send, the admin lands on
«Match e contratti» with the send report's sentence shown there; a refused mail stays
on step 3 as today.

### 2. «Match e contratti» as cards with one next step each (REB-477)

The page reads top to bottom as the freelancer's situation:

- **Contratto quadro**: one card. A sentence says where it stands («Firmato il 25
  settembre 2026. Si rinnova da solo il 25 settembre 2027; disdetta entro il 27 giugno
  2027.», «Inviato il 25 settembre 2026: aspetta la firma del freelance.», «Nessun contratto
  quadro: parte con il primo match inviato.»). One primary button when there is a next
  step (for example «Reinvia email»), the PDFs as links, and «Altre azioni» holding the
  rest (Aggiorna stato, Annulla, Registra disdetta). The text version, and whether a
  newer one exists, move into a closed «Dettagli».
- **Match**: one card per match, newest first. Title: company and role. A sentence
  («Da inviare: la lettera n. 2026-003 è pronta, il freelance non ha ancora ricevuto nulla.»,
  «La lettera n. 2026-003 aspetta la firma del contratto quadro e parte da sola dopo.»,
  «Attivo: lettera n. 2026-003 firmata il 25 settembre 2026, dal 1 ottobre 2026.»). One
  primary button for the next step («Invia per la firma», «Reinvia email»), the letter's
  PDFs as links, «Altre azioni» for the rest (Aggiorna stato, Annulla, Chiudi match).
- **Dati fiscali**: a closed section with the one-line summary and «Modifica», since
  they are set once and read rarely.

The sentences and the actions come from the core (section 3), so the page, the «Match»
list and the MCP tools say the same thing. The «Match» list's «Stato» column shows the
match's short state label and its sentence under it. The member area's «Contratti»
already speaks to the freelancer in sentences and keeps its own words.

The state labels an admin sees become, for a match: «Da inviare» (bozza), «In attesa di
firma» (in_firma), «Attivo», «Concluso», «Annullato»; for a document: «Pronto, non
inviato» (generato), «Parte dopo il contratto quadro» (in_attesa), «Da firmare»
(inviato), «Firmato», «Annullato», «Disdetto». The stored states do not change.

### 3. The core says what happened and what comes next

A new module `rebase_core/match_words.py`, pure functions of the read models and
today's date:

- `situazione` on `ContractDocumentRead`, `MatchRead` and `MatchListItem`: the sentence
  of section 2. It says «il freelance», not a name: the page's title already names the
  person, and a document read does not carry the name.
- `prossima_azione` and `altre_azioni` on `ContractDocumentRead` and `MatchRead`: the
  next step, if there is one, and the other actions the item has in its state, each one
  of `invia`, `reinvia_email`, `aggiorna_stato`, `annulla`, `chiudi`,
  `registra_disdetta`. The rules are today's (`canSend`, `SigningActions`, the cancel
  and close conditions), moved from the page into the core and tested there.
- `MatchService.check(freelancer_id, payload) -> MatchCheck`: validates a `MatchCreate`
  as `create` would (the fields, a closed request), writes nothing, numbers nothing, and
  answers `riepilogo: list[str]` (the step 3 sentences), `cosa_succede: str` (which
  document leaves first), `quadro_necessario: bool` and `dati_fiscali_mancanti: bool`.
  Missing tax data are reported, not refused: `create` still refuses them. Route:
  `POST /api/hub/freelancers/{id}/matches/check`, admin only, 422 naming the field as
  `create` does.
- `MatchService.proposal(freelancer_id, company_id, cliente=None, lettera=None) ->
  MatchCreate`: the prefill with the given fields laid over it, for the MCP tools.

Dates in the sentences use `italian_date`; amounts use the Italian decimal comma.

### 4. The same actions over MCP (REB-478)

New tools on `apps/mcp`, each one calling the core service the admin API calls, with
the calling admin as the actor, so the audit trail reads the same whichever door was
used. Documents and matches come back with `situazione`, `prossima_azione`, `altre_azioni` and `pdf_url`, never
the PDF bytes, never the tax identifiers.

| Tool | Does | Core |
| --- | --- | --- |
| `preview_match(freelancer_id, company_id, cliente?, condizioni?)` | the step 3 sentences for the proposal with the given fields laid over it; writes nothing | `proposal` + `check` |
| `create_match(freelancer_id, company_id, cliente?, condizioni?, match_id?)` | writes the draft match (the «Salva senza inviare» of step 3); `match_id` makes a retry idempotent | `proposal` + `create` |
| `send_match_for_signature(match_id)` | «Invia per la firma»; answers the send report's sentence | `SigningService.send_match` |
| `resend_signing_mail(document_id)` | «Reinvia email» | `resend_mail` |
| `refresh_contract(document_id)` | «Aggiorna stato» | `refresh` |
| `cancel_contract(document_id)` | «Annulla» on a framework agreement not signed yet | `cancel_document` |
| `cancel_match(match_id)` | «Annulla» on a match | `cancel_match` |
| `close_match(match_id)` | «Chiudi match» | `MatchService.close` |
| `record_notice(document_id)` | «Registra disdetta» | `record_notice` |
| `set_freelancer_tax_data(freelancer_id, codice_fiscale, partita_iva, domicilio, pec?)` | saves the tax data; answers that they are saved, not the values | `FiscalService.save` |

`cliente` and `condizioni` are objects with the API's own field names; any field left
out keeps the prefill. The server's `INSTRUCTIONS` and `list_matches`' description stop
saying that matches are created only in the admin area. The tools that cannot be taken
back (send, cancel, notice, close) say so in their description. A soft-deleted
freelancer's match or document reads as not found, as over the API.

The MCP container runs the API image with the API's environment, so it already has
pandoc, Typst, the Documenso settings and the mail sender.

### 5. The echo on the contract PDFs (REB-479)

Ivan, 2026-09-25: the signing surfaces and the documents carry the echo; the site's and
the hub's headers and the business cards keep the lockup. The contract template's title
block replaces the four tiles and the word with
`shared/brand/echo/echo-ink-watermelon-outlines.png`; the running header on the pages
after the first keeps the words «rebase · title» without the tiles, since the echo has
no compact variant at 8 pt. The API image, and the Nix `hub-api` package where it
carries the brand files, carry the PNG. The rule goes into `shared/brand/README.md` and
`docs/design/DECISIONS.md`. The guide PDF is a separate artefact with its own lock and
stays as it is.

## Not changing

The stored states, the documents' texts, the signing flow, the webhook, the sweep, the
member area's «Contratti», the numbering of letters.

## Testing

- Core: `match_words` for every state of a document and a match (sentence and actions),
  `check` refusing what `create` refuses and writing nothing, `proposal` laying fields
  over the prefill.
- API: the check route (200, 422, admin only).
- MCP: every new tool in `apps/mcp/tests`, and each listed in `tests/test_tools.py`; the
  audit row names the calling admin.
- Web: the wizard's three steps (prefill shown, the disclosure closed, «A corpo» adding
  its fields, the tax data line versus the fields, the send landing on the contracts
  page with the sentence), the cards (one per state, the primary button, «Altre
  azioni»), the «Match» list's sentence.
- PDF: the contract renders with the echo; `test_contract_pdf.py` still passes.
- The PR carries screenshots of the three steps and the cards, and a short video of a
  match from step 1 to «Match e contratti».
