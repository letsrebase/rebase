# Runbook: pubblicare il progetto OAuth Google (Internal), per non rifare più il login

Slice 9B, prerequisito del collegamento Drive (§5.6 della spec dello slice 9). Oggi il
progetto OAuth su Google Cloud è in modalità *Testing* (`PIGROCRM_GOOGLE_APP_UNVERIFIED=true`
in questo `.env`): Google fa scadere il refresh token Gmail sette giorni dopo il consenso,
e il titolare rifà il login in continuazione. L'account del titolare
(`mario@example.com`) è un account Google Workspace, quindi il progetto può passare
al tipo utente **Internal** ed essere pubblicato **senza la revisione di verifica di
Google**, anche con gli scope sensibili di Gmail e Drive.

**Da fare prima di collegare Drive**: collegarlo con l'app ancora in Testing produrrebbe un
secondo consenso da rifare ogni settimana, oltre a quello Gmail.

## Passi nella Google Cloud Console (titolare)

Questi passi richiedono l'accesso alla Google Cloud Console sul progetto OAuth esistente e
vanno fatti dal titolare (o da chi ha i permessi di proprietario/editor su quel progetto).

### 1. OAuth consent screen

`APIs & Services` → `OAuth consent screen`:

1. **User Type**: **Internal** (solo utenti del dominio `example.com`). È questa la
   scelta che evita la revisione di Google: uno user type Internal non passa mai per la
   verifica, indipendentemente dagli scope dichiarati.
2. **Publishing status**: **In produzione** (*In production*, non più *Testing*).
3. **Scopes**: dichiarare gli stessi sei scope che il codice richiede già, copiati verbatim
   da dove sono definiti:

   Da `packages/core/src/pigrocrm/core/gmail/schemas.py`, `REQUESTED_SCOPES` (i quattro
   scope Gmail):
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email` (the code sends the full URI since
     REB-606, the one form the Console's picker offers, so the review's string match holds)
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`

   Da `packages/core/src/pigrocrm/core/drive/schemas.py`, `DRIVE_REQUESTED_SCOPES` (i due
   scope Drive aggiuntivi — `openid` e `https://www.googleapis.com/auth/userinfo.email` sono già in elenco sopra):
   - `https://www.googleapis.com/auth/drive.readonly`
   - `https://www.googleapis.com/auth/drive.file`

   Non aggiungere `drive` (pieno), `drive.metadata` o `drive.appdata`: non sono richiesti da
   nessun tool di questo slice (§5.1 della spec).

### 2. Credenziali OAuth

`APIs & Services` → `Credentials` → il client OAuth 2.0 usato da PigroCRM
(`PIGROCRM_GOOGLE_CLIENT_ID`). **Authorized redirect URIs** deve contenere entrambi:

- `{PIGROCRM_PUBLIC_URL}/api/gmail/oauth/callback`
- `{PIGROCRM_PUBLIC_URL}/api/drive/oauth/callback`

dove `{PIGROCRM_PUBLIC_URL}` è il valore già in uso in produzione (non `localhost`: quello
è solo per lo sviluppo locale).

### 3. Abilitare la Drive API

`APIs & Services` → `Library` → cercare **Google Drive API** → **Enable**, sullo stesso
progetto Google Cloud del client OAuth Gmail. Senza questo passo il grant Drive del §5.1
si ottiene ma le chiamate API rispondono con un errore di API non abilitata, indipendente
dagli scope.

## Modifiche a `.env` e riavvio (operatore)

Questi passi sono lato installazione, non Google Cloud, e li fa l'operatore dopo che il
titolare ha completato i tre passi sopra.

### 4. `.env`

```
PIGROCRM_GOOGLE_APP_UNVERIFIED=false
PIGROCRM_REFRESH_TOKEN_DAYS=180
```

- `PIGROCRM_GOOGLE_APP_UNVERIFIED=false` dice al CRM che il progetto non è più in Testing:
  da questo momento non valorizza più `consent_expires_at` sui nuovi consensi, e il banner
  di scadenza a 48 ore smette di comparire.
- `PIGROCRM_REFRESH_TOKEN_DAYS=180` **è già il default** (`config.py`, task T1 di questa
  fetta): scriverla qui non cambia il comportamento di un'installazione che non la
  sovrascrive, e la si tiene nel blocco solo per rendere esplicito nel `.env` quanto dura
  la sessione. Con la rotazione a scorrimento del refresh token, chi usa il CRM non rivede
  più il login, chi lo lascia fermo sei mesi sì. Non tocca il token di accesso, che resta
  a 15 minuti.

### 5. Riavviare l'API

Il processo uvicorn in produzione non gira con `--reload`: le due variabili sopra non hanno
effetto finché l'API non viene riavviata.

### 6. Ricollegare la casella Gmail una volta

Dopo il riavvio, il titolare rifà il login Gmail da Impostazioni una sola volta: il consenso
già presente è stato dato mentre il progetto era ancora in Testing e scade comunque secondo
la vecchia regola dei sette giorni. Il nuovo consenso, dato con il progetto pubblicato, non
porta più una scadenza fissa. (Il collegamento Drive, quando arriva, parte già sotto le
nuove regole: non serve alcun passo di migrazione per quello.)

## Verifica (operatore)

- `describe_gmail_account` (tool MCP, o l'endpoint equivalente) deve mostrare
  `consent_expires_at: null` per l'account appena ricollegato.
- Nessun banner di scadenza (`expiring` / `expired`) nella UI o nella risposta del tool.

## Perché

Un refresh token Google non ha una scadenza fissa imposta dal progetto pubblicato: Google
lo revoca solo dopo **sei mesi di inutilizzo**. Il sync Gmail lo usa ogni giorno, quindi
quella soglia non scatta mai finché il sync gira. Il CRM, dal canto suo, **non aggiunge mai
una scadenza propria** a una credenziale Google: `consent_expires_at` esiste solo per
riflettere il limite reale che Google impone (i sette giorni della modalità Testing), non
per inventarne uno quando quel limite non c'è più. Con il progetto pubblicato e Internal,
quel campo resta `NULL` e lo resta stabilmente, non solo al primo controllo dopo il
ricollegamento.
