# PigroCRM — Slice 3: Fatturazione

**Data:** 2026-08-20
**Stato:** da approvare
**Prerequisiti:** slice 1 (Core CRM + MCP) in `main`; slice 2 (Documenti e template) con motore
`{{}}`, `escape_for`, `DocumentStorage`, `documents`/`document_versions` e `emitter_profile`.

**Ambito:** fatture multi-riga, numerazione progressiva per anno, profilo fiscale, export XML
FatturaPA FPR12, fatture provvisorie.

---

## 1. Perché questo slice esiste

Lo slice 2 ha reso PigroCRM capace di mandare un'offerta e ritrovarla. Ma il ciclo dichiarato nella
spec dello slice 1 — *Contatto → Cliente → Deal → Offerta → Lavoro → Fattura* — si interrompe
sull'ultimo passo, che è l'unico obbligatorio per legge. Finché la fattura si fa altrove, il CRM
resta un posto dove si racconta il lavoro, non dove lo si chiude.

Questo slice è anche il punto in cui si riscuote un investimento fatto due volte in anticipo. Le
colonne fiscali di `customers` sono di prima classe da subito (`partita_iva`, `codice_fiscale`,
`codice_sdi`, `pec`, `indirizzo`, `cap`, `comune`, `provincia`, `nazione`) proprio perché la
FatturaPA si costruisce su quelle: il gestionale precedente le indovinava a runtime provando tre slug diversi per campo
su Attio, e ogni fattura era un tiro di dado sull'anagrafica. E lo slice 2 ha già costruito
`emitter_profile`, che è la controparte emittente degli stessi dati.

---

## 2. Cosa si porta dal gestionale precedente, e cosa si riscrive

Il generatore `buildInvoiceXml` (`.reference-*/website/vite.config.js:1720-1889`) ha prodotto
fatture accettate dal Sistema di Interscambio. Quel fatto vale più della documentazione dello schema,
perché lo SdI applica controlli che lo schema non descrive e rifiuta combinazioni sintatticamente
valide. **La conoscenza di dominio si porta; la macchina che la eseguiva no.** È lo stesso giudizio
che lo slice 2 ha applicato al template dell'offerta: il contenuto legale era materiale duramente
guadagnato, il meccanismo dei placeholder era un difetto di progetto.

### 2.1 Portato

| Elemento | Perché è conoscenza, non codice |
|---|---|
| Scheletro e **ordine** degli elementi: `DatiTrasmissione` → `CedentePrestatore` → `CessionarioCommittente` → `DatiGeneraliDocumento` → `DettaglioLinee` → `DatiRiepilogo` → `DatiPagamento` | FPR12 è una `xs:sequence`: l'ordine è vincolante e ricostruirlo dallo schema è il grosso del lavoro |
| Prologo: `versione="FPR12"`, prefisso `p:` sul root, dichiarazioni `ds:` e `xsi:` | Riprodotte identiche, comprese le due dichiarazioni che il file non usa. `ds:` diventerà portante solo con la firma (fuori ambito), ma rimuovere una dichiarazione da un prologo già accettato dallo SdI e dai validatori degli intermediari non ha nessun vantaggio. **È la ragione per cui il generatore usa `lxml` e non `xml.etree.ElementTree`**: `lxml` accetta un `nsmap` esplicito sul root, mentre ElementTree pota i prefissi non referenziati |
| `FormatoTrasmissione` = `FPR12` **anche** dentro `DatiTrasmissione` | Non è ridondante rispetto all'attributo del root: lo SdI lo legge da lì |
| Il quartetto `AliquotaIVA 0.00` + `Natura N2.2` + `Imposta 0.00` + `RiferimentoNormativo`, e il fatto che i quattro devono concordare | Sono due controlli SdI in coppia: aliquota zero senza `Natura` viene rifiutata, `Natura` con aliquota diversa da zero viene rifiutata. Chi non lo sa scopre il secondo dopo aver corretto il primo |
| `EsigibilitaIVA` = `I` | |
| `CodiceDestinatario` = `0000000` quando il cliente ha PEC ma non codice SDI, con `PECDestinatario` accanto | Il fallback corretto non è deducibile dallo schema, dove il campo è semplicemente obbligatorio |
| `IdTrasmittente/IdCodice` può essere il **codice fiscale** dell'emittente, non necessariamente la P.IVA | |
| Normalizzazione di P.IVA e CF del cliente: si toglie il prefisso `IT` e la punteggiatura, poi si accetta solo `^\d{11}$` oppure `^[A-Z0-9]{16}$`, e **se non combacia si omette l'elemento invece di emetterlo malformato** | La riga di maggior valore dell'intero file: un `IdCodice` malformato è uno scarto secco, un elemento assente spesso passa |
| `DatiBollo/BolloVirtuale` | |
| `Divisa` = `EUR`, `TipoDocumento` = `TD01` | |
| `CondizioniPagamento TP02` e `ModalitaPagamento MP05` come default | Sono i valori giusti per questo emittente: pagamento in un'unica soluzione, bonifico |
| `Anagrafica/Denominazione` anche per un cliente persona fisica con solo CF | FPR12 ammette `Nome`/`Cognome` in alternativa, ma `customers` ha una sola `ragione_sociale`: non c'è una scelta da fare |
| Layout Typst della fattura (`.reference-*/offer/template-invoice.md`): griglia di testata, blocco Committente, tabella Dettaglio, tabella Modalità pagamento, divisore e piede | Portato nella struttura. L'identità Studio Rossi hardcodata diventa `emitter_profile`, la sintassi `[PLACEHOLDER]` diventa `{{}}`, la tabella Dettaglio a riga singola diventa un `{{#each righe}}` |

### 2.2 Riscritto, e perché

Ogni voce è un difetto letto nel sorgente di riferimento, non un miglioramento ipotetico.

| Difetto | Cosa cambia |
|---|---|
| **Doppio escaping.** `normalizeSingleLine` applica `escapeTypstText` (che protegge `\ @ [ ] #`) e il risultato passa **poi** per `escapeXml`. Un cliente «Rossi & C. #1» finisce nell'XML come `Rossi &amp; C. \#1`: un backslash letterale dentro un documento fiscale, in un campo che l'Agenzia delle Entrate conserva | L'XML ha un contesto di escaping proprio, e l'escaping si applica **sempre al valore di dominio**, mai a un valore già preparato per un altro bersaglio. Nessun valore attraversa due escaper. È la regola che lo slice 2 ha già scritto per Markdown e Typst, estesa al terzo bersaglio |
| **XML costruito per concatenazione di stringhe.** La correttezza dipende dal fatto che ogni interpolazione ricordi `escapeXml` | Costruito come albero di elementi e serializzato una volta sola. L'iniezione di markup diventa impossibile **per struttura**, non per diligenza |
| `escapeXml` copre le cinque entità ma non filtra i caratteri di controllo | Il serializzatore rifiuta ogni code point non valido in XML 1.0, invece di produrre un file che nessun parser apre |
| **`NumeroLinea` fisso a 1**, `Quantita` 1, `PrezzoUnitario` = totale intero. Una fattura è una riga sola, e il dettaglio del lavoro non arriva al cliente | `DettaglioLinee` per riga reale, `DatiRiepilogo` per gruppo di aliquota |
| **Il progressivo è dedotto con una regex dall'etichetta di visualizzazione**: `parseInvoiceProgressive` fa `/Fattura\s+(\d+)/i` sul titolo. Il numero fiscale è un derivato di una stringa che l'utente vede | Il numero è un dato, in una colonna, con un vincolo di unicità |
| **`getNextInvoiceProgressive` legge tutti i JSON della cartella e fa max+1.** Read-then-write senza alcun lock, su filesystem | §3 |
| Il progressivo è **globale e non si azzera mai**, e `Numero` è `"{n}/00"` senza anno | Contatore per anno, `Numero` = `{anno}/{numero}` |
| **`formatIsoDate` usa `toISOString()`, cioè UTC.** Una fattura creata il 31 dicembre alle 23:30 CET porta la data del 1° gennaio: anno fiscale sbagliato, su un documento immutabile | La data di emissione è una `date` nel fuso dell'emittente, non la proiezione UTC di un istante |
| **`BolloVirtuale = SI` incondizionato**, anche su una fattura da 50 € | Condizionato alla soglia (§7) |
| **Importi in float**, e la percentuale si applica a un totale **riletto da una stringa formattata** (`parseAmount(values.TOTALE)`) | `Decimal`, e gli importi sono dati: non si rileggono mai da testo |
| `ProgressivoInvio` derivato dal progressivo della fattura | `ProgressivoInvio` identifica il **file**, non la fattura: si genera all'esportazione |
| **`RegimeFiscale`, `Denominazione`, P.IVA, CF, indirizzo, email, IBAN e codice SDI hardcodati nel sorgente** di un repo pubblico | `emitter_profile` (slice 2) + `fiscal_profile` (§7) |
| **`CodiceDestinatario` può risultare stringa vuota** se il cliente non ha né SDI né PEC: file invalido, generato senza errore | L'emissione si rifiuta, con un errore che nomina il campo mancante sul cliente |
| `RiferimentoNormativo` = `"N2.2 (non soggette - altri casi)"`, cioè la **descrizione del codice**, non un riferimento normativo | Testo sul profilo fiscale, con default `Operazione non soggetta a IVA ai sensi dell'art. 1, commi 54-89, L. 190/2014 — regime forfettario` |
| **`parseAddressParts` indovina** via, CAP, comune e provincia da un indirizzo in testo libero, con una regex sui prefissi stradali italiani | Sono già quattro colonne su `customers`. Non si indovina niente: se mancano, l'emissione si rifiuta |
| `temporary-invoices.json` come file separato, con `consumeTemporaryInvoice` che riconcilia due archivi | §5 |

---

## 3. Numerazione

Il vincolo è: **numerazione progressiva per anno, senza salti**. Due emissioni contemporanee non
devono prendere lo stesso numero, e un'emissione fallita non deve bruciarne uno. È un problema di
concorrenza, e si risolve dicendo cosa garantisce il database.

**Non una `SEQUENCE`.** `nextval()` in Postgres è deliberatamente non transazionale: non rientra nel
rollback. Una sequenza garantisce l'unicità e proibisce esattamente la proprietà che serve — l'assenza
di salti. Ogni transazione abortita lascerebbe un buco.

**Un contatore in tabella, con lock di riga.**

```
invoice_counters(anno INTEGER PRIMARY KEY, ultimo_numero INTEGER NOT NULL DEFAULT 0)
```

`InvoiceService.issue()`, in **una sola** transazione e in questo ordine:

1. `INSERT INTO invoice_counters (anno) VALUES (:anno) ON CONFLICT (anno) DO NOTHING`
   — due prime-fatture-dell'anno concorrenti: una inserisce, l'altra non fa niente, entrambe
   proseguono.
2. `SELECT ultimo_numero FROM invoice_counters WHERE anno = :anno FOR UPDATE`
   — da qui in poi ogni altra emissione dello stesso anno attende. È **la prima riga bloccata** della
   transazione, e nessun'altra istruzione della transazione prende un lock che un'emissione concorrente
   possa già tenere: due emissioni non possono quindi andare in deadlock fra loro.
3. Validazioni fiscali (§9), calcolo dei totali (§6.1), controllo di monotonia della data (§6.2).
4. `UPDATE invoice_counters SET ultimo_numero = ultimo_numero + 1`, scrittura della riga `invoices`
   con `(anno, numero)`, scrittura del congelamento (`snapshot`), scrittura dell'activity.
5. `COMMIT`.

**Il numero non esiste prima del commit.** Se un qualsiasi passo fallisce, il rollback riporta
`ultimo_numero` al valore precedente e nessun numero è stato consumato: è la proprietà che la sequenza
non dava. E una bozza non ha numero, quindi «una creazione fallita» non può bruciarne uno per
costruzione — il numero si assegna solo all'emissione.

**Indice unico parziale come rete, non come meccanismo:**

```sql
CREATE UNIQUE INDEX uq_invoices_anno_numero ON invoices (anno, numero) WHERE numero IS NOT NULL;
```

Serve a trasformare qualunque futura via che scavalchi il lock — una `INSERT` diretta, un importer, un
secondo servizio — in un errore anziché in un duplicato. Non è la difesa primaria: è ciò che rende
osservabile il fallimento della difesa primaria.

**Il render sta fuori dalla transazione.** Il PDF e l'XML si producono **dopo** il commit, in una
seconda transazione, leggendo lo `snapshot`. Tenere un lock di riga per la durata di un sottoprocesso
Typst serializzerebbe l'emissione sul tempo di compilazione di un PDF; e soprattutto la fattura è un
fatto giuridico indipendente dalla sua stampa. Se il render fallisce, la fattura esiste con il suo
numero e i suoi artefatti si rigenerano, deterministicamente, dallo `snapshot` (§4).

**L'assenza di salti resta vera nel tempo** solo perché una fattura numerata non si può eliminare, e
un errore si corregge con un annullamento che *conserva* il numero (§4). Senza quella regola, questa
non varrebbe nulla.

---

## 4. Immutabilità e correzione

Una fattura emessa è un documento fiscale. Non è una riga di CRM con uno stato in più.

| Campo | Dopo l'emissione |
|---|---|
| `anno`, `numero`, `data_emissione`, `tipo_documento`, `divisa` | **congelati** |
| righe (descrizione, quantità, prezzo, aliquota, natura) | **congelate**, e non cancellabili |
| `imponibile`, `imposta`, `bollo`, `totale`, `causale` | **congelati** |
| `snapshot` (anagrafica emittente e cliente, parametri fiscali) | **congelato** |
| `stato_pagamento`, `data_incasso` | modificabili — l'incasso è un fatto successivo, non parte del documento |
| `note_interne`, `custom_fields` | modificabili — non compaiono su nessun artefatto |
| `trasmessa_esternamente_il` | valorizzabile **una volta sola**, poi congelato |
| `pdf_document_id`, `xml_document_id` | possono acquisire nuove versioni, il cui contenuto deve risultare **identico byte per byte** al precedente: l'XML contro `invoices.xml_hash_sha256`, il PDF contro l'`hash_sha256` della versione precedente in `document_versions`. È una riparazione, non una modifica, e una divergenza è un errore da segnalare, non una versione da salvare |
| `deleted_at` | **impossibile**, e non per convenzione: `CHECK (deleted_at IS NULL OR (numero IS NULL AND stato <> 'consumata'))`. Ciò che non ha mai consumato un numero si cancella; ciò che l'ha consumato no, nemmeno via SQL diretto — e nemmeno una proforma `consumata`, che è l'antecedente di un documento immutabile |

Ogni tentativo di scrittura su un campo congelato solleva `ImmutableField` — l'eccezione esiste già in
`core/errors.py` dallo slice 1 e non è mai stata usata seriamente; questo è il dominio per cui era
stata scritta.

`xml_hash_sha256` viene scritto dalla **prima** esportazione riuscita, che è l'unico momento in cui non
esiste un valore precedente da confrontare; da lì in poi ogni esportazione confronta e non riscrive.
Fino a quel momento la colonna è `NULL` e l'esportazione è ripetibile senza vincoli — che è ciò che
rende innocuo il render fuori transazione del §3.

### 4.1 Quando si scopre un errore

**Una correzione è un documento nuovo, non una modifica.** Le vie disponibili sono due, e la scelta
non è dell'utente: dipende da un fatto verificabile.

**Fattura non ancora trasmessa** (`trasmessa_esternamente_il IS NULL`). Si annulla: `stato` passa a
`annullata`, con `annullata_il` e `motivo_annullamento` obbligatorio. Il numero **resta consumato** e
la riga resta leggibile: è l'equivalente della pagina barrata su un registro cartaceo, e conserva
l'assenza di salti del §3. Una nuova fattura corretta prende il numero successivo.

**Fattura già trasmessa.** L'annullamento si rifiuta con `Conflict`. Da quel momento la correzione
richiede una nota di credito, che questo slice non produce (§13), quindi avviene fuori da PigroCRM e
l'applicazione lo **dice**, invece di offrire un pulsante che finge di risolvere.

Questo è il motivo per cui `trasmessa_esternamente_il` esiste pur non essendoci trasmissione nello
slice. Il file XML è un deliverable: l'utente lo consegna al proprio intermediario. Senza quel dato,
il sistema non saprebbe distinguere una fattura mai uscita — annullabile — da una già depositata presso
l'Agenzia delle Entrate, e tratterebbe le due allo stesso modo. È il campo che rende l'annullamento
sicuro anziché ottimista.

---

## 5. Fatture provvisorie

Servono a concordare l'importo prima di consumare un numero. Un cliente che chiede «mandami il
prospetto e poi fattura» oggi costringe a emettere e sperare; una provvisoria è una **proforma**: ha
la forma di una fattura, si manda, si corregge quante volte serve, e non tocca il registro.

| | Provvisoria | Emessa |
|---|---|---|
| Numero | nessuno. Solo un `riferimento` tipo `PROV-2026-0007`, da una `SEQUENCE` Postgres per anno | fiscale, dal contatore in tabella dell'anno (§3) |
| Salti | ammessi: non è un registro | vietati |
| Modificabile | interamente, e cancellabile | §4 |
| PDF | template proprio, con `FATTURA PROFORMA — NON COSTITUISCE FATTURA` nel corpo, nessun `TD01` | layout fiscale |
| XML | `export_xml` solleva `Conflict` | prodotto |
| Prefisso di storage | `proforma/{id}/` | `fatture/{anno}/{numero}/` |

**Quattro meccanismi indipendenti impediscono la confusione**, e sono quattro perché un'etichetta sola
è un punto singolo di guasto su un documento che qualcuno potrebbe pagare:

1. Lo spazio dei numeri è diverso e il `riferimento` ha un prefisso non numerico, che nessuna regex di
   numero fiscale può accettare.
2. L'esportatore XML rifiuta in base allo **stato**, non a un flag passato dal chiamante.
3. Il PDF è un template diverso, con la dichiarazione nel corpo del documento — non un watermark che
   una stampa può perdere.
4. I byte vivono sotto un prefisso di storage diverso, così un file estratto dal contesto resta
   identificabile.

Una `SEQUENCE` è lo strumento giusto **qui** ed è quello sbagliato al §3, per la stessa proprietà:
`nextval()` non rientra nel rollback. Su una proforma un buco non significa nulla, e in cambio si
ottiene un contatore che non serializza nessuno.

**La conversione non è un cambio di stato in loco.** `issue_from_proforma` crea una **nuova riga** in
stato `emessa`, con `origine_proforma_id` che punta alla proforma, e marca quest'ultima `consumata`
lasciandola leggibile. Se fosse una transizione sulla stessa riga, quella riga avrebbe una storia con
un periodo mutabile prima di uno congelato, e «immutabile dopo l'emissione» sarebbe una proprietà di
qualcosa che *era* mutabile: verificabile solo ricostruendo la cronologia. Due righe: una sempre
mutabile, una sempre congelata.

---

## 6. Denaro e arrotondamenti

Nessun `float`, in nessun punto: `Decimal` nel servizio, `Numeric` in Postgres, e i totali **calcolati
dal servizio e memorizzati**, mai ricalcolati dal frontend. Il totale calcolato nel browser è il
difetto strutturale del gestionale precedente citato nella spec dello slice 1 §2.2, e su una fattura costa di più.

| Grandezza | Tipo | Perché |
|---|---|---|
| `imponibile`, `imposta`, `bollo`, `totale`, `prezzo_totale` di riga | `Numeric(12,2)` | Sono gli importi che compaiono sul documento: la convenzione dello slice 1 |
| `quantita`, `prezzo_unitario` | `Numeric(12,6)` | **Estensione deliberata** della convenzione. Non sono importi ma fattori: a due decimali, 3 ore a 33,3333 €/h non è esprimibile, e l'utente sarebbe costretto a scrivere un totale che non è il prodotto di ciò che ha dichiarato. FPR12 ammette fino a 8 decimali su entrambi |
| `aliquota_iva` | `Numeric(5,2)` | |

Gli schemi Pydantic riportano `max_digits`/`decimal_places` di ogni colonna, come già fanno
`deals/schemas.py`: senza, un valore fuori capacità arriva a `flush()` e torna come
`NumericValueOutOfRange` grezzo, che non è un `IntegrityError` e non ha handler.

### 6.1 Le regole, e perché le due non concordano

L'IVA per riga e l'IVA per fattura danno risultati diversi, e l'XML è validato contro i totali. Le
regole vanno fissate qui, non scoperte al primo scarto.

1. **Riga:** `prezzo_totale = ROUND(quantita × prezzo_unitario − sconto, 2)`, `ROUND_HALF_UP`.
   Non half-even: la prassi fiscale italiana e l'aritmetica dei controlli SdI arrotondano per eccesso
   sul mezzo.
2. **Imponibile di gruppo = somma dei `prezzo_totale` già arrotondati**, non arrotondamento della
   somma esatta. Lo SdI verifica che l'`ImponibileImporto` di un `DatiRiepilogo` corrisponda alla somma
   dei `PrezzoTotale` delle righe di quel gruppo: arrotondare la somma esatta può differire di un
   centesimo da ciò che è stampato riga per riga, e il file viene scartato.
3. **Imposta calcolata per gruppo, non per riga:**
   `imposta_gruppo = ROUND(imponibile_gruppo × aliquota / 100, 2)`. Lo SdI verifica l'`Imposta` contro
   il prodotto `ImponibileImporto × AliquotaIVA`: la somma delle imposte di riga può differire di
   qualche centesimo da quel prodotto e viene scartata. **È qui che le due regole divergono**, ed è la
   ragione per cui il gruppo — non la riga — è l'unità di calcolo dell'imposta.
4. `totale = Σ imponibili_gruppo + Σ imposte_gruppo`. Il bollo **non** entra nel totale (§7).
5. Un gruppo di riepilogo è la coppia `(aliquota_iva, natura)`. Righe con natura diversa e aliquota
   uguale sono gruppi distinti, perché il `RiferimentoNormativo` differisce.
6. Righe negative ammesse (uno sconto è una riga). Il **totale** all'emissione deve essere `> 0`:
   una TD01 a zero o negativa non è una fattura, e si rifiuta con `ValidationFailed`.

Sotto forfettario tutte le imposte sono zero e le regole 2 e 3 non sono distinguibili dai dati reali.
È esattamente perché non sono osservabili oggi che devono essere scritte oggi, e verificate con un
profilo sintetico (§14.8).

### 6.2 Data di emissione

`data_emissione` è una `date`, non un timestamp: non è un istante, è la data che compare sul documento
e determina l'anno fiscale. La conversione da `timestamptz` con `toISOString()` sposta di un giorno
tutto ciò che accade dopo le 23:00 CET — e il 31 dicembre sposta di un anno.

Ammessa la retrodatazione, con due limiti verificati dentro la transazione bloccata del §3, dove
leggere «la data del numero precedente» è sicuro:

- **non anteriore al 1° gennaio dell'anno in corso.** Un anno chiuso è chiuso: inserire nel registro di
  un anno passato dopo che è iniziato quello nuovo è sbagliato indipendentemente da cosa fa il
  contatore. È anche la ragione per cui il profilo fiscale non è storicizzato (§7.1);
- **non anteriore alla `data_emissione` dell'ultima fattura emessa dello stesso anno**, perché il
  registro deve essere cronologicamente monotono rispetto al numero.

Una data futura si rifiuta. `anno` è quindi sempre l'anno di `data_emissione`, che coincide con l'anno
in corso: le due letture del §3 e del §8.1 non possono divergere.

### 6.3 Data di scadenza (aggiunta 2026-09-22, REB-326)

`data_scadenza` si calcola all'emissione dai **termini di pagamento del cliente**: `giorni_pagamento`
(null: i `giorni_scadenza` del profilo fiscale) e `pagamento_fine_mese`. L'aritmetica è una sola,
`invoices/scadenza.py`: prima i giorni, poi l'ultimo giorno del mese in cui si atterra, che è ciò che
«30 giorni data fattura fine mese» significa. Una data scritta a mano sulla bozza o sulla proforma
vince sul calcolo. Un documento ancora modificabile espone `scadenza_prevista`, la data che «Emetti»
stamperebbe oggi. Una fattura emessa tiene la data con cui è nata: un termine cambiato dopo non la
sposta. Regola in `docs/design/DECISIONS.md`, riga del 2026-09-22.

---

## 7. Il regime fiscale

Il regime forfettario non applica IVA e porta una dichiarazione normativa specifica — già visibile nel
template del gestionale precedente come stringa fissa. Il regime **guida** i totali e l'XML, e lo fa attraverso un dato,
non attraverso un `if` sparso nel generatore.

### 7.1 `fiscal_profile`

`emitter_profile` (slice 2) conserva l'**identità** dell'emittente: denominazione, indirizzo,
contatti, logo, firma. `fiscal_profile` aggiunge i **parametri fiscali**, ed è una riga singola come
`emitter_profile`:

| Colonna | Note |
|---|---|
| `codice_regime` | `RF01`…`RF19`, come richiesto da `RegimeFiscale` |
| `aliquota_iva_default` `Numeric(5,2)` | `0.00` per il forfettario |
| `natura_default` `String(4)` null | `N2.2` per il forfettario |
| `riferimento_normativo` `Text` null | il testo che va in `RiferimentoNormativo` |
| `applica_bollo` `bool`, `soglia_bollo` `Numeric(12,2)`, `importo_bollo` `Numeric(12,2)` | default `true`, `77.47`, `2.00`. Sono valori di legge, non preferenze: configurabili perché la legge li ha già cambiati |
| `condizioni_pagamento`, `modalita_pagamento`, `giorni_scadenza`, `iban` | `TP02`, `MP05`, e la scadenza da cui si deriva `DataScadenzaPagamento` |

**Una riga sola, e non una tabella storicizzata con `valido_da`/`valido_a`.** La storicizzazione è
stata considerata e scartata perché in questo slice non ha nessun esercitatore: un regime cambia il 1°
gennaio, ma il §6.2 vieta la retrodatazione oltre l'anno corrente — un anno chiuso è chiuso — quindi
non esiste un'emissione che debba leggere i parametri di un periodo precedente. Un secondo insieme di
righe servirebbe solo a rispondere a una domanda a cui già risponde qualcos'altro, meglio.

**Chi vince.** Per una fattura **già emessa** l'autorità è lo `snapshot` congelato sulla riga, sempre e
solo: è la risposta per documento alla domanda «in che regime era questa fattura», e non può divergere
dal profilo perché non lo consulta. `fiscal_profile` vale per l'emissione presente e per nient'altro.

Una modifica a `fiscal_profile` scrive un'activity. Il residuo R5 dello slice 1A («nessun audit trail
per la configurazione») resta aperto in generale; qui si chiude per questa tabella, perché cambiare
regime senza traccia è di un ordine di gravità diverso dal rinominare uno stage di pipeline — e la
timeline è anche ciò che ricostruisce la cronologia dei regimi senza una colonna di validità.

### 7.2 Come il regime guida totali e XML

Una `RegimeStrategy` risolta dal `codice_regime` decide tre cose e nient'altro:

| | Forfettario `RF19` |
|---|---|
| Aliquota di default sulle righe | `0.00`, e un'aliquota diversa si rifiuta |
| `Natura` e `RiferimentoNormativo` | `N2.2` + il testo del profilo, su ogni riga e ogni riepilogo |
| Bollo | `DatiBollo/BolloVirtuale = SI` **solo se** l'imponibile non soggetto supera `soglia_bollo`; altrimenti l'elemento `DatiBollo` è assente |

Il bollo **non entra nel totale** e non si riaddebita al cliente: `DatiBollo` dichiara che l'imposta di
bollo è assolta in modo virtuale dall'emittente. È ciò che il gestionale precedente fa e ciò che l'emittente fa davvero.
Il riaddebito richiederebbe una riga con `Natura N1` (escluse ex art. 15 DPR 633/72), cioè una
funzionalità con una sua semantica, e non si specifica a metà (§13).

**Cosa servirebbe a un regime diverso, senza inventarlo.** Un `RF01` ordinario è una seconda strategy
con `aliquota_default = 22.00`, `natura_default = NULL`, nessun riferimento normativo: **nessuna nuova
colonna, nessuna migrazione**, e le regole di arrotondamento del §6.1 — già scritte — cominciano a
produrre valori diversi da zero. Restano fuori, e sono blocchi XML distinti che il forfettario non
esercita in nessuna forma: ritenuta d'acconto, cassa previdenziale, split payment, reverse charge,
esigibilità differita o per cassa. Nominarli è il confine; progettarli oggi sarebbe indovinare.

---

## 8. Modello dati

### 8.1 `invoices`

| Colonna | Tipo | Note |
|---|---|---|
| `id` | UUID v7 | |
| `customer_id` | UUID FK | obbligatorio: una fattura senza cessionario non esiste |
| `deal_id` | UUID FK null | |
| `tipo` | `fattura` \| `proforma` | |
| `stato` | vedi sotto | `CHECK` sulle coppie `(tipo, stato)` ammesse |
| `anno`, `numero` | Integer null | solo su `fattura` emessa; indice unico parziale (§3) |
| `riferimento` | String(30) null | l'etichetta della proforma |
| `data_emissione`, `data_scadenza` | Date null | §6.2 |
| `tipo_documento`, `divisa` | String(4), String(3) | `TD01`, `EUR` |
| `imponibile`, `imposta`, `bollo`, `totale` | Numeric(12,2) | derivati e memorizzati |
| `causale` | Text null | |
| `snapshot` | JSONB null | scritto all'emissione, `NULL` su una bozza e su una proforma. §8.3 |
| `snapshot_versione` | Integer null | idem. §8.3 |
| `stato_pagamento` | `da_incassare` \| `incassato` | mutabile |
| `data_incasso` | Date null | mutabile |
| `trasmessa_esternamente_il` | Date null | §4.1 |
| `xml_hash_sha256` | String(64) null | |
| `pdf_document_id`, `xml_document_id` | UUID FK null | §8.4 |
| `origine_proforma_id` | UUID FK null | §5 |
| `annullata_il`, `motivo_annullamento` | | |
| `note_interne` | Text null | mutabile |
| `custom_fields` | JSONB | `entity_type = 'invoice'` |
| `deleted_at` | timestamptz null | con il `CHECK` del §4 |

Stati: `bozza` → `emessa` → `annullata` per `fattura`; `bozza` → `confermata` → `consumata` per
`proforma`. Due macchine a stati in una colonna sarebbero ambigue, quindi le coppie ammesse sono un
vincolo di tabella e non una convenzione del servizio. Una tabella sola perché elenchi, timeline e
ricerca restano una query sola, e la differenza fra i due tipi è già portata da `tipo`.

### 8.2 `invoice_lines`

`invoice_id` · `numero_linea` (unique con `invoice_id`, da 1, contiguo) · `descrizione` (Text) ·
`quantita` · `unita_misura` (String(10) null) · `prezzo_unitario` · `sconto_percentuale` /
`sconto_importo` · `prezzo_totale` · `aliquota_iva` · `natura` (String(4) null) ·
`riferimento_normativo` (Text null)

```sql
CHECK ((aliquota_iva = 0) = (natura IS NOT NULL))
```

I due controlli che lo SdI applica in coppia diventano un vincolo di tabella: la combinazione invalida
non è memorizzabile, quindi non è esportabile. Difendere l'invariante nel generatore la lascerebbe
aggirabile da qualunque altra via di scrittura.

Ogni campo testuale è `SafeStr`, e `descrizione` ha un `max_length` che rispecchia la colonna: è la
settima occorrenza della famiglia di difetti che `core/validation.py` documenta, e non c'è ragione di
riaprirla su una tabella nuova.

### 8.3 Lo `snapshot`

JSONB con la denominazione, i dati fiscali e l'indirizzo di **entrambe** le parti come erano
all'emissione, più i parametri del profilo fiscale applicato. È ciò che rende la rigenerazione fedele:
un cliente che cambia sede non riscrive la fattura di due anni fa.

`snapshot_versione` è un intero perché uno `snapshot` scritto oggi verrà letto da codice di fra tre
anni, e un payload JSON senza versione si interpreta indovinando. Costa una colonna.

### 8.4 Dove stanno i byte

**Non si reinventa il documentale.** Gli artefatti sono `documents` + `document_versions` dello slice 2,
con lo `DocumentStorage` e i suoi due backend, il versioning, l'hash e la timeline già funzionanti.

`documents.tipo` acquisisce tre valori: `fattura` (il PDF), `fattura_xml` (l'XML), `proforma` (il PDF
della provvisoria, che non ha XML). Quindi **due righe `documents` per fattura emessa, non una**. Un
`document_versions` è un flusso lineare di versioni di **un** file logico, con un `hash_sha256` usato
per deduplica e integrità; infilarci due formati diversi renderebbe «versione 3» ambigua e i due hash
non confrontabili. Due flussi, due hash, due verifiche di integrità, e un ri-render del PDF non tocca
l'XML.

`documents.stato` resta `null` per tutti e tre i tipi: lo stato autorevole è quello di `invoices`.
**Duplicare una macchina a stati in due tabelle produce due verità**, ed è la ragione per cui non si fa.

Nome del file XML secondo la convenzione dello SdI, `IT{cf_o_piva}_{progressivo}.xml`, con
`progressivo` = `anno × 10000 + numero` in base 36 su 5 caratteri. Deterministico — riesportare produce
lo stesso nome — e senza collisioni finché il numero resta sotto 10.000 all'anno, oltre il quale
l'esportazione si rifiuta anziché produrre un nome ambiguo. Il nome conta perché il file va a un
intermediario, che spesso lo valida prima del contenuto.

### 8.5 `entity_type = 'invoice'`

Le spec dello slice 1 (§5.6) e dello slice 2 (§4.1) affermano che aggiungere un `entity_type` non
richiede modifiche. **Nel codice consegnato non è vero:**
`fields/schemas.py:12` è `EntityType = Literal["customer", "person", "deal"]`. La colonna in database è
`String(30)` senza vincolo, quindi il database è davvero aperto; il `Literal` no. Il costo reale è una
riga nel `Literal` più una voce in `native_fields("invoice")` — quest'ultima obbligatoria, perché il
residuo A13 dello slice 1B è ancora aperto e una definizione di campo etichettata «Totale» collide
altrimenti con la colonna nativa `totale`, senza che nessuno protesti. Va scritto così com'è: ripetere
la promessa la renderebbe falsa una terza volta.

---

## 9. Generazione XML

Un `FatturaPAExporter` in `packages/core`, che riceve la riga `invoices` con le sue righe e il suo
`snapshot` e restituisce `bytes`. Non legge `emitter_profile` né `customers`: legge lo `snapshot`. È ciò
che rende l'export riproducibile e, insieme, testabile senza database.

Tre proprietà, che sono le tre riscritture del §2.2 rese architettura:

1. **Costruisce un albero `lxml`, non una stringa** (§2.1 per la ragione della libreria). Ogni valore
   entra come testo di un nodo e il serializzatore escapa per costruzione: non esiste un punto in cui
   si possa dimenticare `escapeXml`.
2. **Un solo escaper per valore.** I valori arrivano dallo `snapshot` nella loro forma di dominio, non
   preparati per Typst. Il contesto `xml` si aggiunge a `RenderContext` in
   `templates/escaping.py`, accanto a `markdown`, `typst`, `typst_string`, `url`, `verbatim`, in modo
   che il terzo bersaglio abbia la sua regola esplicita nello stesso posto degli altri due — e non una
   regola implicita in un modulo diverso, che è come il gestionale precedente è arrivata al doppio escaping.
3. **Rifiuta i code point invalidi in XML 1.0** invece di produrre un file non parsabile. `SafeStr`
   ferma già il NUL a monte; qui si chiude la classe, non il caso singolo.

L'emissione si rifiuta, con `ValidationFailed` che **nomina il campo mancante sul cliente**, quando:
il cliente non ha né `codice_sdi` né `pec`; manca uno fra `indirizzo`, `cap`, `comune`, `provincia`;
`nazione != 'IT'` (§13); non c'è nessuna riga; il totale non è positivo.

---

## 10. PDF e template

Il PDF passa dalla pipeline dello slice 2 senza modifiche: template `{{}}`, Pandoc, Typst, versioni
pinnate nell'immagine, render sincrono, nessun input utente su una riga di comando.

Due template nuovi, `fattura` e `proforma`, derivati dal layout di `template-invoice.md` con la tabella
Dettaglio trasformata in `{{#each righe}}`. Il template della proforma porta la dichiarazione nel corpo
(§5). Il piede riporta la dichiarazione del regime, che nel forfettario è dovuta e che oggi nel gestionale precedente è
una stringa fissa nel sorgente: viene da `fiscal_profile.riferimento_normativo`.

---

## 11. Superficie MCP e API

La regola non negoziabile resta: **i tool MCP e i router FastAPI chiamano gli stessi servizi
in-process**, e il test di architettura lo verifica. Ma emettere un documento fiscale da un agente
autonomo merita una decisione esplicita, non un'estensione per inerzia.

**Un agente può preparare. Non può emettere.**

| Operazione | API | MCP |
|---|---|---|
| `list_invoices`, `get_invoice` | sì | sì |
| `create_proforma`, `replace_proforma_lines`, `render_proforma_pdf` | sì | sì |
| `describe_fiscal_profile` | sì | sì |
| URL dell'XML di una fattura già emessa | sì | sì (un URL, mai i byte in base64 — regola dello slice 2 §7) |
| `issue_invoice` — un solo metodo, che accetta un `origine_proforma_id` facoltativo | sì, ruolo `admin` | **no** |
| `annul_invoice` | sì, ruolo `admin` | **no** |
| `mark_transmitted_externally` | sì, ruolo `admin` | **no** |
| `update_fiscal_profile` | sì, ruolo `admin` | **no** |
| `set_payment_state` | sì, ruolo `collaboratore` | sì |

Tre ragioni, nessuna delle quali è la diffidenza generica verso gli agenti.

1. **L'emissione è l'unica creazione irreversibile del prodotto.** Ogni altro errore raggiungibile via
   MCP si annulla: il soft delete si ripristina, uno stato si rimette, un campo si riscrive.
   Consumare un numero non si annulla — al massimo si documenta. La spec dello slice 1 §5.9 aveva già
   deciso che «l'MCP non espone delete distruttivi»; questa è la stessa regola applicata alla creazione
   che non si disfa.
2. **La garanzia è strutturale perché il permesso non basta.** Il residuo R10 dello slice 1A è aperto:
   un PAT non ha scope ed erede il ruolo pieno, quindi «l'MCP non emette» non è imponibile con un
   controllo di autorizzazione — un token amministrativo lo passerebbe. È imposto **non registrando il
   tool**, che è l'unico meccanismo che tiene finché R10 è aperto.
3. **Costa un clic.** L'agente fa tutto il lavoro: legge il deal, compone le righe, produce una
   proforma leggibile. L'umano preme un pulsante. L'autonomia che si perde è quella; quella che si
   guadagnerebbe cedendo l'emissione non vale un numero irrecuperabile.

**Il divieto va reso meccanico**, o degrada in una dimenticanza. Il test di architettura cresce di una
clausola: per ogni metodo pubblico di `InvoiceService`, o esiste un tool MCP che lo chiama, o il metodo
è in una lista di esclusione dichiarata — e la lista deve essere **esattamente** `issue_invoice`,
`annul_invoice`, `mark_transmitted_externally`, `update_fiscal_profile`. Aggiungere un tool per uno dei
quattro rompe la build; togliere un nome dalla lista senza aggiungere il tool la rompe pure. Chi legge
la lista capisce che l'assenza era una decisione e non una svista.

`origine_proforma_id` è un parametro di `issue_invoice`, non un quinto metodo: emettere da zero ed
emettere da una proforma condividono lock, validazioni, congelamento e numerazione, e separarli in due
metodi significherebbe due percorsi da tenere allineati sulla parte che non deve divergere.

Endpoint:

```
GET    /api/invoices                     lista, filtri per stato/anno/cliente/pagamento
POST   /api/invoices                     crea bozza o proforma
PUT    /api/invoices/{id}/lines          sostituisce l'intero elenco righe
POST   /api/invoices/{id}/issue          emette (admin)
POST   /api/invoices/{id}/annul          annulla (admin)
POST   /api/invoices/{id}/transmitted    marca consegnata all'intermediario (admin)
PATCH  /api/invoices/{id}/payment        stato incasso
GET    /api/invoices/{id}/pdf · /xml     download autorizzato
GET    /api/fiscal-profile · PUT (admin)
```

Le righe si **sostituiscono in blocco**, non si aggiornano parzialmente. Due ragioni: è la forma
naturale di un editor di righe, ed evita il residuo A14 dello slice 1B — con `exclude_none=True` non
esiste alcuna grafia per azzerare una colonna numerica o di data, quindi `sconto_importo` e
`unita_misura` sarebbero inazzerabili. Sostituire l'elenco aggira il difetto invece di fingere che sia
chiuso.

L'emissione richiede `admin`, non `collaboratore`: la spec dello slice 1 §6.3 definisce
`collaboratore` come «scrittura sulle entità, esclusa configurazione», e consumare un numero del
registro fiscale sta più vicino alla configurazione che alla scrittura di un'entità.

---

## 12. Residui degli slice precedenti che toccano questo

| Residuo | Interazione |
|---|---|
| **R1** — la sessione condivisa del server MCP non è sicura in concorrenza | Il lock del §3 è corretto solo se ogni chiamata ha la sua sessione e la sua transazione. Il processo MCP oggi ne condivide una. Questo slice **non ne dipende**, perché l'MCP non emette (§11) e quindi non prende mai quel lock. Va scritto qui, o qualcuno «completerà» la superficie MCP e reintrodurrà una race sulla numerazione senza sapere di averlo fatto |
| **R10** — i PAT sono senza scope | È la ragione 2 del §11: la difesa è strutturale perché quella per permessi non esiste |
| **R5** — nessun audit trail per la configurazione | Chiuso per `fiscal_profile` (§7.1), aperto per il resto |
| **A13** — una chiave custom può collidere con una colonna nativa | Da gestire aggiungendo `invoice` a `native_fields`, §8.5. Il difetto generale resta aperto |
| **A14** — `Update` non sa azzerare una colonna numerica o di data | Aggirato sostituendo le righe in blocco, §11 |
| `customers.partita_iva` validata `^\d{11}$` | Un cliente estero non è memorizzabile, quindi l'emissione lo rifiuta esplicitamente (§9, §13). Non è un residuo noto: emerge qui per la prima volta |

---

## 13. Ciò che questo slice **non** fa

| Fuori ambito | Perché |
|---|---|
| Trasmissione allo SdI | Richiede accreditamento o un intermediario e un certificato di firma. Il deliverable è il file; lo consegna l'utente. Restare fuori evita anche l'intero ciclo di vita delle ricevute — RC, NS, MC, DT, AT, NE — che è un progetto suo |
| Firma elettronica (`XAdES`, `CAdES`) | Non richiesta per la FPR12 verso privati, e senza trasmissione non ha destinatario |
| **Note di credito (`TD04`)** | Motivo strutturale, non un rinvio: una nota di credito corregge una fattura **già accettata dallo SdI**, e in questo slice nessuna fattura viene trasmessa. La correzione di una fattura mai uscita è l'annullamento (§4.1); quella di una fattura consegnata a un intermediario avviene fuori da PigroCRM, e l'app lo dichiara |
| Riaddebito del bollo al cliente | Richiede una riga con `Natura N1` ex art. 15, cioè una semantica propria. Il bollo resta a carico dell'emittente (§7.2) |
| Ritenuta d'acconto, cassa previdenziale, split payment, reverse charge, esigibilità differita | Blocchi XML che il forfettario non esercita mai. Il punto di estensione è la `RegimeStrategy` (§7.2) |
| `FPA12` verso la Pubblica Amministrazione | Vuole `CodiceUnivocoUfficio`, CIG/CUP e split payment: è un secondo formato, non un flag |
| `TipoDocumento` diversi da `TD01` — acconti, autofatture, `TD16`-`TD19` | |
| Clienti esteri e multivaluta | `Divisa` è fissa a `EUR`. Una valuta estera richiede un secondo insieme di importi e uno storico dei cambi; e `customers.partita_iva` accetta solo 11 cifre, quindi una P.IVA comunitaria non è nemmeno memorizzabile (§12) |
| Riconciliazione bancaria e incasso | `stato_pagamento` si mette a mano. Leggere un conto è un'integrazione, non una fattura |
| Fatture ricorrenti, solleciti | I solleciti sono slice 5, col Gmail |
| Import di XML FatturaPA (fatture passive) | Direzione opposta: un parser, non un generatore |

---

## 14. Criteri di successo

Eseguibili in CI, non da guardare.

1. **Lo schema ufficiale valida.** `xmllint --schema` contro lo XSD FPR12 v1.2.1 versionato nel repo,
   su cinque casi: fattura a riga singola, a cinque righe, con una riga di sconto negativa, e due
   sull'esatto confine del bollo — imponibile `77.47` (nessun `DatiBollo`) e `77.48`
   (`BolloVirtuale = SI`).
2. **Un nome ostile non corrompe l'XML.** Un cliente chiamato
   `Rossi & C. <IdCodice>999</IdCodice> "#$@\ ]]>` produce un file che (a) valida contro lo schema,
   (b) ri-parsato restituisce una `Denominazione` **identica byte per byte** alla stringa in ingresso, e
   (c) non contiene alcun elemento che l'esportatore non abbia creato — verificato navigando l'albero,
   non con un confronto di stringhe. Lo stesso cliente, sulla stessa fattura, compare nel PDF come
   testo. Lo slice 2 ha imparato questa lezione sul compilatore Typst; qui i compilatori sono due e
   l'asserzione si fa su entrambi.
3. **Numerazione sotto concorrenza.** Venti thread emettono contemporaneamente su Postgres reale:
   venti fatture, numeri da 1 a 20, nessun duplicato e nessun salto, verificato con una `SELECT`. Poi
   dieci emissioni con un errore iniettato **fra l'`UPDATE` del contatore e il `COMMIT`** — cioè dopo
   che il numero è stato assegnato in transazione: nessuno dei dieci numeri risulta consumato, e la
   successiva emissione riuscita prende il 21.
4. **Immutabilità imposta dal database, non dal servizio.** Su una fattura emessa: ogni scrittura su un
   campo congelato solleva `ImmutableField`; `DELETE` viene rifiutato dall'API; e il soft delete
   fallisce **anche eseguendo `UPDATE invoices SET deleted_at = now()` in SQL diretto**, per via del
   `CHECK`.
5. **Una proforma non produce XML.** `export_xml` solleva `Conflict`, il suo PDF contiene la stringa di
   dichiarazione, e il suo `riferimento` non combacia con la regex del numero fiscale.
6. **Rigenerazione fedele a un anno di distanza.** Con `emitter_profile` e `fiscal_profile` modificati
   nel frattempo, il ri-render produce un PDF e un XML **identici byte per byte** agli originali, con
   l'XML confrontato contro `xml_hash_sha256`. È il criterio 4 dello slice 2 applicato a un documento
   fiscale, dove non è una comodità ma un obbligo.
7. **Il divieto MCP è nel build.** Un test verifica che nessun tool MCP raggiunga `issue_invoice`,
   `annul_invoice`, `mark_transmitted_externally`, `update_fiscal_profile`, e che la lista di esclusione
   dichiarata sia **esattamente** quei quattro nomi.
8. **Gli arrotondamenti sono comportamento, non documentazione.** Un profilo `RF01` sintetico, presente
   solo nelle fixture di test, con aliquote `22.00` e `10.00` sulla stessa fattura: un `DatiRiepilogo`
   per gruppo, `Imposta` di gruppo pari a `ROUND(imponibile_gruppo × aliquota / 100, 2)`,
   `ImponibileImporto` pari alla somma dei `PrezzoTotale` **già arrotondati**, e
   `ImportoTotaleDocumento` pari alla somma dei due. Lo slice consegna il forfettario; questa strategy
   esiste per rendere verificabili le regole del §6.1, che il forfettario non esercita.
9. **I rifiuti all'emissione nominano il campo.** Cliente senza `codice_sdi` né `pec`; cliente con
   `nazione != 'IT'`; cliente senza `cap` o `comune`; fattura senza righe; totale non positivo; data di
   emissione futura; data anteriore a quella dell'ultima fattura dello stesso anno. Ognuno produce un
   `ValidationFailed` con `entity` e `field` popolati, quindi un problem detail RFC 9457 **e** un
   messaggio azionabile da un modello, come richiede lo slice 1 §6.2.
10. **Il ciclo completo, da entrambi gli adapter.** Claude, via MCP, legge un deal e prepara una
    proforma con tre righe; l'umano la apre nell'app, la converte in fattura, ottiene PDF e XML; il file
    passa il criterio 1; la timeline del cliente mostra le due sequenze distinguendo `actor_type`
    `mcp` da `user`; e il tentativo di Claude di emettere la fattura non trova alcun tool da chiamare.
