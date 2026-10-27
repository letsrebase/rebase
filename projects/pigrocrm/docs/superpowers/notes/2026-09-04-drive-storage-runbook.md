# Runbook: attivare Google Drive come storage documenti e importare le 14 fatture del gestionale precedente

Slice 9D, task 4. Prerequisiti già chiusi su questa installazione: il progetto OAuth è
pubblicato (`docs/superpowers/notes/2026-09-04-google-oauth-publish-runbook.md`) e
l'account `mario@example.com` ha già collegato Drive — la riga
`google_drive_accounts` è `active`, con `root_folder_ids` già valorizzato:

| Cartella | ID |
|---|---|
| Fatture | `1a-MUDzjInwR9TVQ5MWwwbAguynV3Rakg` |
| Offerte | `1VbusbbuC5Yf6eMKw0qqiFYwr9BJeixfZ` |
| Progetti | `1XVtObWQ0ZCzHp1LvVM5OMBbjysTc8Jdt` |

`storage_folder_id` è invece `NULL`: nessuna cartella di scrittura è stata scelta.
Questo runbook copre solo l'attivazione dello storage e l'import da Drive delle 14
fatture del gestionale precedente (spec `docs/superpowers/specs/2026-09-04-slice-9-import-storico-e-google-drive-design.md`
§4.3, §5.4, §6); il collegamento dell'account Drive e le sue radici di lettura sono già
fatti e non sono ripetuti qui. Per il dataset e le avvertenze sui dati delle 14 fatture
(bollo, date di incasso, scadenze) resta valido
`docs/superpowers/notes/2026-09-04-import-storico-runbook.md` — questo runbook lo sostituisce
solo nel passo 3 («caricare i PDF»), che con Drive configurato diventa parte dell'import
stesso invece di un passo separato.

Sull'installazione, oggi, la tabella `documents` ha **zero righe**: non c'è nessun
documento su storage locale da perdere. La cartella `var/documents/` contiene comunque
delle sottocartelle (residuo di prove/e2e locali), ma nessuna riga di `documents` le
referenzia: passare a `gdrive` non le rende irraggiungibili perché non erano raggiungibili
dall'app in primo luogo.

## Passi

### 1. Creare la cartella di scrittura su Drive

A differenza delle tre radici di lettura (già scelte), la cartella `PigroCRM` in cui il
CRM scriverà i documenti che genera **non esiste ancora** e va creata a mano, come
titolare, sul Drive collegato:

1. Su Google Drive, creare una cartella `PigroCRM` (dove preferito nel proprio Drive).
2. Aprirla e copiare l'id dall'URL (`https://drive.google.com/drive/folders/<ID>`).

### 2. `.env`: passare allo storage Drive

```
PIGROCRM_STORAGE_BACKEND=gdrive
```

**Senza** `PIGROCRM_GDRIVE_SERVICE_ACCOUNT_JSON` né `PIGROCRM_GDRIVE_ROOT_FOLDER_ID`
(`packages/core/src/pigrocrm/core/config.py`, campi `storage_backend`,
`gdrive_service_account_json`, `gdrive_root_folder_id`; il primo di default è `local`, gli
altri due sono vuoti). Valorizzare uno solo dei due obbligherebbe l'altro e farebbe
fallire la costruzione del backend (`storage_from_settings`,
`packages/core/src/pigrocrm/core/storage/factory.py`) — sull'API alla prima operazione
su un documento, perché è lì che la dipendenza `get_storage` viene risolta, e all'avvio
sull'adapter MCP, che costruisce il backend dentro `build_server`. Qui l'obiettivo è
l'altra strada, quella che usa la credenziale già collegata dal titolare invece di un
service account. Con `storage_backend=gdrive` e nessuna delle due variabili,
`storage_from_settings` costruisce un `LazyUserDriveStorage`
(`packages/core/src/pigrocrm/core/storage/lazy_drive.py`): a differenza del service
account non c'è niente da verificare quando il backend viene costruito (la riga da cui
dipende — l'account Drive e la sua cartella di scrittura — può ancora non esistere),
quindi l'API parte comunque e la prima operazione di storage (`put`/`get`/`delete`) è il
punto in cui la configurazione viene letta e, se manca, rifiutata.

### 3. Riavviare l'API

Il processo in questo worktree gira senza `--reload`
(`uv run uvicorn pigrocrm_api.main:app --host 127.0.0.1 --port 8000`): la nuova
variabile non ha effetto finché non lo si riavvia da questo worktree.

Finché il passo 4 non è fatto, ogni tentativo di scrittura di un documento (upload,
generazione da template, import di fattura con PDF) risponde `409 Conflict` con il
messaggio esatto che `StorageNotConfigured` produce
(`packages/core/src/pigrocrm/core/storage/errors.py`):

> «Google Drive non è pronto a ricevere i documenti: collega Drive e scegli la cartella
> di scrittura in Impostazioni → Drive»

`DriveRepository.storage_account()` è la query dietro questo controllo: cerca un account
`status = "active"` con `storage_folder_id` non nullo, e su questa installazione oggi non
esiste ancora nessuna riga che lo soddisfi — è `active`, ma `storage_folder_id` è `NULL`.

### 4. Impostazioni → Drive: scegliere la cartella di scrittura

Nella web app, `/app/impostazioni/drive` (route `apps/web/src/routes/app/impostazioni/drive.tsx`,
componente `DrivePanel`), campo **«Cartella di scrittura»**: incollare l'id copiato al
passo 1 e salvare. In REST è lo stesso `PATCH /api/drive/account/roots`
(`apps/api/src/pigrocrm_api/routers/drive.py`) che gestisce anche le radici di lettura:

```
PATCH /api/drive/account/roots
{
  "root_folder_ids": [
    "1a-MUDzjInwR9TVQ5MWwwbAguynV3Rakg",
    "1VbusbbuC5Yf6eMKw0qqiFYwr9BJeixfZ",
    "1XVtObWQ0ZCzHp1LvVM5OMBbjysTc8Jdt"
  ],
  "storage_folder_id": "<id della cartella PigroCRM creata al passo 1>"
}
```

`root_folder_ids` va sempre inviato per intero (è un PATCH, ma questo campo non ha
semantica di "lascialo com'è": il pannello web risottomette sempre l'elenco corrente) —
qui sono gli stessi tre id già in uso, invariati.

**Solo `storage_folder_id`, fra i due, viene verificato al salvataggio**: `set_roots`
(`packages/core/src/pigrocrm/core/drive/account.py`) fa un `files.get` sulla cartella
appena indicata con la credenziale del titolare, prima di scrivere qualunque riga, e
rifiuta con `ValidationFailed` se l'id non esiste, non è visibile a quella credenziale o
non è una cartella. `root_folder_ids` non viene mai interrogato qui: le radici di lettura
si verificano solo quando vengono effettivamente usate da chi legge (`list_drive_files`,
`read_drive_file`). Se l'id incollato al passo 1 è sbagliato, l'errore arriva qui, non al
primo upload.

### 5. Verifica: generare un documento e vederlo su Drive

Verificare la scrittura prima di importare le 14 fatture. Lo strumento MCP
`create_document_from_template` (`apps/mcp/src/pigrocrm_mcp/tools/__init__.py`) crea un
documento da un template generico dandogli `variabili` a mano — è la via più diretta per
verificare *lo storage*, ma per «Rapporto ore» specificamente non è il modo in cui il
CRM lo genera davvero: il template dichiara solo tre variabili per il form di
compilazione (`periodo`, `totale_ore`, `numero_voci` — `packages/core/src/pigrocrm/core/timetracking/report.py`,
`TIME_REPORT_TEMPLATE_VARIABLES`), mentre la tabella delle voci (`voci`) è calcolata dal
servizio e non è mai battuta a mano; passandola vuota (o omettendola) tramite
`create_document_from_template` produce comunque un PDF, solo con la tabella vuota. Per
verificare con il rapporto ore *reale* di un deal, generato con le sue ore effettive, la
via corretta è l'endpoint dedicato:

```
GET /api/deals/{deal_id}/time-report?mese=AAAA-MM&formato=pdf
```

(`apps/api/src/pigrocrm_api/routers/deals.py`, `time_report` → `TimeReportService.render_pdf`),
scegliendo un deal e un mese con almeno una voce di ore registrata. Entrambe le vie
passano dallo stesso `DocumentService.create_from_template`, quindi entrambe esercitano
lo stesso storage; la prima è più rapida da chiamare a mano, la seconda produce il
documento che il prodotto genera davvero.

Verifiche dopo la generazione:

1. Su Google Drive, sotto `PigroCRM/<slug-ragione-sociale>-<8 cifre dell'id cliente>/<id
   documento>/`, deve comparire il nuovo PDF (`storage_key_for`,
   `packages/core/src/pigrocrm/core/documents/service.py`: la cartella è quella del
   *cliente proprietario del deal*, non del deal stesso — per un documento su deal,
   `_customer_of` risale al cliente del deal prima di calcolare la chiave).
2. `GET /api/documents/{id}/download` (`apps/api/src/pigrocrm_api/routers/documents.py`)
   deve restituire lo stesso PDF.

### 6. Importare le 14 fatture del gestionale precedente, dalla cartella Drive `Fatture`

Con il backend attivo, il passo 3 del vecchio runbook (caricare il PDF come documento
prima dell'import) diventa **facoltativo**: `import_issued_invoice` accetta
`pdf_sorgente: {"drive_file_id": "<id file Drive>"}`
(`packages/core/src/pigrocrm/core/invoices/schemas.py`, classe `PdfSorgente` — esattamente
uno fra `document_id` e `drive_file_id`) e recupera i byte da Drive **dentro la stessa
transazione** dell'import (`InvoiceService.import_issued`, commento «One transaction,
Drive included» in `packages/core/src/pigrocrm/core/invoices/service.py`): non serve più
un `POST /api/documents` + upload separati prima di ogni riga.

Per ciascuna delle 14 fatture:

1. **Trovare il file su Drive**: `list_drive_files` (senza argomenti) elenca le radici
   configurate, fra cui `Fatture`; richiamato con l'`id` di `Fatture` come `cartella_id`
   elenca le sottocartelle per cliente, e sceso in una di quelle il PDF della fattura
   (`apps/mcp/src/pigrocrm_mcp/tools/drive_privileged.py`; registrato solo con
   `PIGROCRM_MCP_FULL_ACCESS=true` **e** Google configurato — `gmail_configured`,
   verificato da `server.py`; dal REB-810 li vede solo un token con l'«Accesso completo»
   scelto dal suo titolare, creato con quella spunta o aperto dalla pagina Token). Non
   esiste ricerca per nome: bisogna scendere cartella per cartella.
2. **Controllo incrociato numero↔file — passo dell'operatore, nessun tool lo fa da
   solo**: prima di usare un file come `drive_file_id` di una riga, aprirlo (o leggerlo
   con `read_drive_file`) e confermare che il numero di fattura sul PDF corrisponda alla
   riga del dataset che si sta per importare. A differenza del caricamento manuale
   (dove l'operatore sceglieva il file da caricare), qui l'unico controllo che il numero
   giusto sta finendo sulla riga giusta è questo: **non esiste un controllo «già
   importato»** sul lato Drive — `import_drive_file` (lo strumento di import generico)
   crea sempre un nuovo documento, e riprovare con lo stesso file ne crea un secondo
   invece di segnalare un duplicato; per una fattura, comunque, non si passa da
   `import_drive_file` (vedi punto 3) ma il principio — nessuna difesa automatica contro
   il file sbagliato — vale identico.
3. Costruire il payload `InvoiceImport` come nel runbook del gestionale precedente (righe del dataset,
   private delle chiavi `_...`, `customer_id` risolto), con
   `"pdf_sorgente": {"drive_file_id": "<id file>"}` al posto di `{"document_id": ...}`.
   **Non usare `import_drive_file` per queste righe**: il suo stesso docstring lo dice
   esplicitamente — per una fattura già emessa dal gestionale precedente il PDF va
   indicato a `import_issued_invoice` in `pdf_sorgente.drive_file_id`, non copiato prima
   con `import_drive_file`.
4. Chiamare `import_issued_invoice` (MCP, server `pigrocrm`) o `POST /api/invoices/import`
   (REST), in ordine di `numero`, verificando ogni risposta prima di passare alla riga
   successiva — stessa disciplina del runbook del gestionale precedente passo 4.

Al termine, dichiarare i buchi 1, 4, 6 con `declare_invoice_register_gaps` e verificare
come nel runbook del gestionale precedente (§5): `list_invoices`, `invoice_counters.ultimo_numero = 17`,
`export_invoice_xml` → `409`, `GET /api/invoices/{id}/pdf` byte per byte, `undeclared_gaps`
vuoto.

## Documenti già su storage locale: nessuna migrazione

Su questa installazione, oggi, non ce n'è nessuno (`documents`: 0 righe) — è il caso che
la spec assume (§6: «nessuna migrazione dei byte da storage locale a Drive (oggi non ce
ne sono)»). Ma la proprietà vale in generale, non solo oggi, e va tenuta a mente per
qualunque documento generato *dopo* questo runbook mentre il backend torna `local` per
qualunque motivo, o per un'installazione che arriva a questo punto con documenti già
presenti: **il codice non offre un passo di migrazione** (nessun tool, nessuna route);
`GDriveStorage.get` cerca il file per `storage key` nelle `appProperties` dei file *su
Drive* (`packages/core/src/pigrocrm/core/storage/gdrive.py`) e un documento scritto solo
su disco locale non ha mai avuto quella proprietà da nessuna parte — quindi
`GET /api/documents/{id}/download` di un documento del genere, con il backend su
`gdrive`, risponde `NotFound` invece del PDF.

Le due opzioni oneste, se in futuro esistono documenti locali da preservare:

1. **Restare su `PIGROCRM_STORAGE_BACKEND=local`** finché non esiste una fetta dedicata
   alla migrazione dei byte — rimandando l'attivazione di Drive.
2. **Accettare che le versioni vecchie non sono più raggiungibili dall'app**: i file
   restano fisicamente sotto `var/documents/` (non vengono cancellati dal cambio di
   backend), consultabili a mano dal filesystem, ma non più tramite
   `GET /api/documents/{id}/download` né alcun'altra funzione del CRM, finché
   quell'installazione non torna a `local` o finché non viene scritta una migrazione.

Nessuna delle due è implementata da questo task: sono le uniche due strade compatibili
con il codice così com'è oggi.

## Perché

`PIGROCRM_STORAGE_BACKEND=gdrive` senza service account sceglie deliberatamente la
credenziale del titolare — quella già collegata per il §5 dello slice 9 — invece di
richiedere una seconda configurazione Google Cloud (un service account, una Shared
Drive) che questa installazione non ha e non le serve: un solo utente, un solo Drive,
la stessa credenziale che già legge le tre radici del §4.2. La cartella di scrittura è
verificata al salvataggio (a differenza della radice del service account, verificata da
`storage_from_settings` nel momento in cui costruisce il backend: all'avvio sull'adapter
MCP, alla prima operazione su un documento sull'API) perché la riga da cui dipende —
quale account, quale cartella — non è una variabile d'ambiente ma un dato che il
titolare sceglie da un'interfaccia dopo che l'API è già in esecuzione: non c'è nessun
momento anteriore in cui quella scelta esista già da verificare.
