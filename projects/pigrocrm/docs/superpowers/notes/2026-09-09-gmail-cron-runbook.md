# Runbook: i lavori da cron di PigroCRM, e il banner che non prevede più una scadenza

Questo prodotto non ha un demone e non ha una coda (vedi la docstring di
`packages/core/src/pigrocrm/core/gmail/sync.py`): tutto ciò che deve succedere da solo
succede perché c'è una riga in `crontab`. Le righe sono due, e questo è il runbook di
tutte e due.

I §§1–3 sono il sync Gmail, deciso insieme al banner del consenso perché insieme
rispondono alla stessa domanda dell'operatore («la casella si sincronizza da sola, e se
smette me ne accorgo?»):

1. `pigrocrm gmail-sync`, un comando che esegue **un ciclo** di sincronizzazione e
   termina. Non è un demone, quindi i quindici minuti li tiene cron.
2. Il banner di scadenza del consenso, che ora esiste **solo** finché il progetto OAuth
   è in Testing.

Il §4 è il secondo lavoro: `pigrocrm digest`, il resoconto settimanale che ogni lunedì
mattina scrive a chi l'ha chiesto, uno spazio alla volta, a cominciare dall'installazione
radice.

## 1. Installare il cron (operatore, sul server)

Una riga in `crontab -e` dell'utente che possiede il deploy (quello che può parlare con
il socket di Docker). `$DEPLOY_PATH` è la directory di deploy configurata
sull'ambiente (la radice del checkout del repository su quel server):

```
*/15 * * * * cd $DEPLOY_PATH/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm gmail-sync >> /var/log/pigrocrm-gmail-sync.log 2>&1
```

Quattro dettagli della riga non sono decorativi:

- **`cd $DEPLOY_PATH/projects/pigrocrm`**: `docker compose` legge `docker-compose.yml`
  dalla directory corrente, e cron parte dalla home dell'utente, non da lì.
- **`--env-file ../../.env`**: il `.env` del server sta nella radice del repository, non
  accanto al compose file, e compose cerca `.env` nella *propria* directory. Senza
  questo, ogni interpolazione risulta vuota (è lo stesso motivo del commit `84a4c2d`,
  ed è la stessa opzione che serve a ogni altro comando compose su questo server).
- **`exec -T api`**: `exec` e non `run`, perché il container `api` è già in piedi e un
  `run` ne avvierebbe un secondo con la stessa configurazione ogni quarto d'ora. `-T`
  disattiva la pseudo-TTY, che cron non ha.
- **`uv run --no-sync`**: senza `--no-sync`, `uv` risincronizza l'ambiente del container
  di produzione contro l'intero `pyproject.toml`, gruppo `dev` incluso, e si scarica
  mypy e ruff dentro un container in esecuzione. È già successo una volta, dal vero
  (vedi `Dockerfile.api` e il §5 del README).

Verifica subito, senza aspettare il quarto d'ora, eseguendo la stessa riga a mano: deve
stampare una riga sola e uscire con stato 0.

```
cd $DEPLOY_PATH/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm gmail-sync; echo "uscita: $?"
```

Se l'installazione ha più di una casella Google collegata, il comando **si rifiuta di
indovinare** e le elenca: in quel caso serve una riga di cron per casella, ciascuna con
`--email casella@dominio.it`. Scegliere per conto dell'operatore vorrebbe dire lasciare
l'altra casella non sincronizzata, con un log identico nei due casi.

## 2. Leggere il log

Ogni esecuzione scrive **una riga sola**, che comincia con l'ora UTC in ISO 8601 (cron
non ne aggiunge nessuna, e un log di frasi senza orario non risponde alla prima domanda
che gli si fa: da quando non funziona più).

Ciclo eseguito, su `stdout`, uscita `0`:

```
2026-09-09T03:15:02+00:00 gmail-sync io@example.it: 4 messaggi nuovi, 11 già presenti, 3 conversazioni lette, 6 collegamenti, 0 invii riconciliati (2 query)
```

- **messaggi nuovi / già presenti**: la finestra si sovrappone di proposito a quella del
  ciclo precedente (`PIGROCRM_GMAIL_WATERMARK_OVERLAP_HOURS`), quindi «già presenti» è
  alto per costruzione e non è uno spreco: è il vincolo di unicità che assorbe un
  messaggio arrivato a cavallo di due esecuzioni.
- **query**: `0` significa che nessun indirizzo del CRM era da cercare, e quindi che a
  Google non è stata fatta **nessuna** richiesta, nemmeno il refresh del token. Su
  un'installazione senza clienti né persone con un indirizzo è la riga giusta, non un
  guasto.
- **invii riconciliati**: quante mail partite con esito ignoto sono state risolte in
  questo ciclo, in un verso o nell'altro.

Ciclo già in corso (un altro cron, o qualcuno che ha premuto Sincronizza), sempre su
`stdout`, uscita **`0`**:

```
2026-09-09T03:15:02+00:00 gmail-sync io@example.it: già in corso da 2026-09-09T03:14:58+00:00
```

Non è un errore: è il lucchetto che fa il suo mestiere, il secondo chiamante non ha
speso niente e mettere un errore nel log per il sistema che funziona come progettato è
il modo migliore per insegnare all'operatore a ignorare il log.

Guasto, su `stderr`, uscita **`1`**:

```
2026-09-09T03:15:02+00:00 gmail-sync: il consenso Google per io@example.it è stato revocato: ricollega la casella da Impostazioni → Gmail
```

Le frasi che si possono leggere qui, e cosa fare:

| Frase | Cosa è successo | Cosa fare |
| --- | --- | --- |
| `nessuna casella Google collegata` | Il cron è installato su un'installazione dove nessuno ha mai collegato Gmail (o l'unica casella è stata scollegata: una casella scollegata non viene né scelta né elencata) | Collegarla da Impostazioni → Gmail, o togliere la riga di cron |
| `la casella … appartiene a un utente disattivato` | Il titolare ha disattivato l'utente proprietario della casella | Riattivare l'utente, oppure scollegare la casella e togliere il cron. Il consenso di chi è stato disattivato non si spende |
| `… ha il ruolo readonly e non può sincronizzare …` | Il proprietario della casella non ha un ruolo che può scrivere | Cambiare il ruolo dell'utente: il cron non agisce con più diritti del titolare della casella |
| `più di una casella collegata, indica --email: …` | Più caselle, nessuna indicata | Una riga di cron per casella, con `--email` |
| `… non è una casella collegata: …` | `--email` non corrisponde a nessuna riga | Correggere l'indirizzo (il messaggio elenca quelli collegati) |
| `il consenso Google … è stato revocato` | Google ha risposto `invalid_grant`: terminale, non si risolve riprovando | Il titolare rifà il collegamento da Impostazioni → Gmail |
| `il consenso Google … è scaduto` | I sette giorni della modalità Testing sono finiti | Come sopra, e vedi il §3 qui sotto |
| `manca l'autorizzazione https://www.googleapis.com/auth/gmail.readonly` | Il consenso c'è ma è parziale | Ri-autorizzare dalla pagina Impostazioni |
| `Gmail non è configurato su questa installazione` | Mancano le variabili `PIGROCRM_GOOGLE_*` | `.env` + riavvio dell'API, oppure togliere il cron |
| `elenco dei messaggi fallita (429/RESOURCE_EXHAUSTED)` | Quota Gmail esaurita per ora | Niente: il ciclo dopo riprende da dove era arrivato |

Un errore *non previsto* stampa invece il suo traceback ed esce comunque con stato
diverso da 0: è voluto, perché un guasto che nessuno ha previsto merita il suo stack.

Il log non contiene mai un oggetto, un indirizzo di un corrispondente o un corpo di
messaggio: quello che il comando stampa sono i contatori di `SyncReport`, dove non c'è
spazio per nient'altro. È questo che rende sicuro appenderlo a un file sull'host.

**Rotazione**: `/var/log/pigrocrm-gmail-sync.log` cresce di circa 100 caratteri ogni
quarto d'ora (~3,5 MB l'anno). Se sul server c'è `logrotate`, una regola settimanale con
`rotate 8` basta e avanza; senza, va bene anche non farne nulla per un anno.

## 3. Il banner smette di prevedere

`GoogleAccountService.health` legge `consent_expires_at` **solo** quando
`PIGROCRM_GOOGLE_APP_UNVERIFIED=true`, cioè solo quando il progetto OAuth è ancora in
Testing — l'unica modalità in cui Google fa scadere davvero il refresh token, dopo sette
giorni. Pubblicato il progetto (vedi
[`2026-09-04-google-oauth-publish-runbook.md`](2026-09-04-google-oauth-publish-runbook.md)),
le righe scritte prima portano ancora la loro data, ma quella data non descrive più
niente: lasciata a prevedere, chiederebbe di rinnovare un consenso che non sta per
scadere e poi lo dichiarerebbe morto in un giorno in cui non è successo nulla.

Quindi, con `PIGROCRM_GOOGLE_APP_UNVERIFIED=false`:

- niente più banner «va rinnovato entro il …» né «è scaduto» dedotti dalla data;
- e niente più data nemmeno nella pagina Impostazioni → Gmail: il `consent_expires_at`
  che la pagina rende accanto all'indirizzo («Consenso da rinnovare entro il …») esce
  dallo stesso `health`, quindi passa per lo stesso filtro. Toglierla dal solo banner
  avrebbe spostato la frase, non rimossa;
- resta invece tutto ciò che è un **fatto**: `status = revoked` (che il CRM impara solo
  da un `invalid_grant` di Google), `status = expired`, un consenso parziale, una casella
  scollegata dal titolare. Il banner riporta quelli esattamente come prima.

La colonna `consent_expires_at` non viene ripulita: è il registro di ciò che era vero
sotto il consenso che l'ha scritta, e rimettere l'app in Testing la rende di nuovo
significativa senza dover ricostruire niente.

**Attenzione, e non è una contraddizione**: un consenso *dato* mentre il progetto era in
Testing scade davvero, anche dopo la pubblicazione — Google non guarisce un token già
emesso. Il passo 6 del runbook di pubblicazione (ricollegare la casella una volta) serve
ancora. La differenza è come lo si scopre: non più da una previsione del CRM, ma da
Google che risponde `invalid_grant`, che è un fatto, e che questo cron scrive nel log
la prima volta che capita.

## 4. Il resoconto settimanale (`pigrocrm digest`)

Una riga sola, **il lunedì mattina**, nello stesso `crontab -e` dell'utente che possiede
il deploy. Il percorso è scritto per esteso come lo si incolla; se la directory di deploy
di questo server non è `/opt/pigrocrm`, è il `$DEPLOY_PATH` del §1 e va sostituito:

```
0 8 * * 1 cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm digest >> /var/log/pigrocrm-digest.log 2>&1
```

I quattro dettagli del §1 valgono identici qui (`cd`, `--env-file ../../.env`,
`exec -T api`, `uv run --no-sync`) e per gli stessi motivi. Quello che cambia è
**l'orario**, ed è l'unica parte della riga che non si può copiare senza guardare il
server.

### L'orario dipende dal fuso dell'host

`0 8` significa «le otto secondo l'orologio del sistema», e cron non conosce
`PIGROCRM_TIMEZONE`: quello decide *quale settimana* viene raccontata (il lunedì-domenica
appena chiuso nel fuso dell'emittente), non a che ora parte il comando. Quindi, prima di
incollare la riga:

```
timedatectl
```

- `Time zone: Europe/Rome` → `0 8` è giusto: le otto italiane.
- `Time zone: Etc/UTC` (il default di quasi ogni VPS, ed è il caso di questo server) →
  `0 8` vorrebbe dire le dieci italiane d'estate. In UTC le otto italiane sono `0 6`
  durante l'ora legale (CEST, marzo–ottobre) e `0 7` durante l'ora solare (CET,
  ottobre–marzo). Una riga sola non può essere giusta tutto l'anno: o si sceglie `0 6` e
  d'inverno il resoconto arriva alle sette, o si cambia la riga due volte l'anno, o si
  mette l'host su `Europe/Rome` con `timedatectl set-timezone Europe/Rome` e si torna a
  `0 8`. L'ultima è la scelta fatta qui quando il server è solo di questo prodotto.

Un'ora di scarto non sposta niente di ciò che il resoconto racconta: la settimana è già
chiusa da ore, e il contenuto sarebbe identico anche eseguendolo il lunedì sera.

### Provare la riga a mano, prima di aspettare lunedì

`--dry-run` prepara davvero il resoconto di ogni spazio — legge il database, costruisce
le sezioni, conta i destinatari — e **non manda niente e non scrive niente**:

```
cd /opt/pigrocrm/projects/pigrocrm && docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm digest --dry-run; echo "uscita: $?"
```

Le righe della prova portano `(prova)` in fondo proprio perché nel log non si confondano
con una settimana partita davvero. Con `--slug studio-rossi` la prova (come il comando
vero) tocca un solo spazio.

L'installazione radice (il database di `PIGROCRM_DATABASE_URL`, quella che non sta nel
registro) è la prima riga di ogni giro, e nel log si chiama sempre `root`. Per toccare
solo lei: `--slug root`, un nome riservato che nessuno spazio può avere; un giro della
sola radice non apre nemmeno il registro. Non il valore di `PIGROCRM_ROOT_SLUG`: con
`--slug` quello resta il nome di uno spazio del registro, se ce n'è uno nato prima che la
radice lo prendesse. Per rimandare una settimana alla radice si usa quindi
`--slug root --forza --data …`, mai `--forza` sul giro completo, che la rimanderebbe a
ogni spazio.

### Rimandare una settimana

Una settimana già inviata non parte una seconda volta: la riga `digests.settimana` è
unica, ed è questo che rende innocuo un cron che scatta due volte. Per rimandarla davvero
— una mail persa, un errore di configurazione di Resend scoperto il martedì — servono
tutte e due le opzioni, la data di **un giorno qualsiasi** di quella settimana e il
permesso esplicito di riscriverla:

```
docker compose --env-file ../../.env exec -T api uv run --no-sync pigrocrm digest --slug studio-rossi --forza --data 2026-09-07
```

La riga della settimana non viene duplicata: viene aggiornata con i nuovi destinatari e
la nuova ora.

### Leggere il log

Una riga per spazio, contatori soltanto: mai un indirizzo, mai una cifra del resoconto.
È questo che rende sicuro appendere `/var/log/pigrocrm-digest.log` a un file sull'host.

| Riga | Cosa è successo | Cosa fare |
| --- | --- | --- |
| `root: inviato a 2` | L'installazione radice: stesso resoconto, stessi destinatari (gli utenti attivi con il resoconto acceso), letto come il primo admin attivo. Le altre righe di uno spazio valgono anche per lei, con `root:` davanti | Niente |
| `studio-rossi: inviato a 3` | Il resoconto è partito a tre persone, e la settimana è registrata | Niente |
| `studio-rossi: inviato a 3 (prova)` | `--dry-run`: sarebbe partito a tre persone | Niente: nessuna mail, nessuna riga scritta |
| `studio-rossi: vuoto` | Lo spazio non ha ancora né clienti, né deal, né fatture, né ore | Niente: chi non ha ancora cominciato non riceve una mail piena di zeri |
| `studio-rossi: già inviato per 2026-W37` | Quella settimana era già partita (un secondo cron, o una riga eseguita a mano) | Niente. Se va rimandata davvero, vedi «Rimandare una settimana» |
| `studio-rossi: nessun destinatario` | Nessun utente attivo di quello spazio ha il resoconto acceso | Niente: è una scelta loro (Impostazioni → Profilo) |
| `studio-rossi: saltato (…)` su `stderr` | Quello spazio non è stato mandato; fra parentesi c'è il **tipo** dell'errore, mai il testo (che può contenere l'URL del database, password compresa) | Vedi qui sotto |
| `registro degli spazi non raggiungibile (…)` su `stderr` | Il registro dei tenant non risponde: nessuno spazio del registro è stato visitato. Nel giro completo la radice sì, perché non ci sta: la sua riga `root:` c'è lo stesso | Controllare il database e i `PIGROCRM_*` del `.env`; poi rieseguire la riga a mano |

Gli `saltato` che si incontrano davvero:

- `titolare_mancante` / `titolare_disattivato`: la mail del titolare nel registro non
  corrisponde a nessun utente di quello spazio, o quell'utente è stato disattivato. La
  radice non ha una riga nel registro e il suo titolare è il primo admin ancora attivo:
  `root: saltato (titolare_mancante)` vuol dire che non ne resta nessuno.
- `invio_rifiutato`: Resend ha rifiutato **tutti** gli indirizzi. Non viene registrato
  niente, quindi la prossima esecuzione ci riprova: una settimana che non è arrivata a
  nessuno non è una settimana inviata.
- `invio_non_configurato`: manca `PIGROCRM_RESEND_API_KEY`, quindi non c'è nessun modo di
  mandare la mail e non è stato tentato niente. Come sopra, non viene registrato niente:
  la prima esecuzione dopo che la chiave è a posto manda la settimana invece di trovarla
  già segnata come inviata. Rimedio: mettere la chiave nel `.env`, riavviare l'API e
  rieseguire la riga a mano.
- `ProgrammingError`, `UndefinedTable`: lo schema di quello spazio è indietro rispetto
  all'immagine. **Questo comando non migra niente**, di proposito: l'unico che migra è
  `pigrocrm ensure-space-defaults`, che gira nel CMD dell'immagine API a ogni deploy
  (ORB-189). Un cron che alle otto di lunedì eseguisse Alembic su otto database senza
  nessuno che guarda sarebbe la cosa più rischiosa che fa questo prodotto. Rimedio:
  riavviare l'API (che lo migra) e rieseguire la riga a mano.
- `OperationalError`: il database di quello spazio non risponde. Gli altri spazi sono
  stati mandati lo stesso, ed è il motivo per cui il comando esce sempre con stato `0`:
  uno spazio rotto non deve nascondere i sette che hanno funzionato.

**Rotazione**: una riga per spazio a settimana; anche con cento spazi il file cresce di
pochi kilobyte l'anno e non ha bisogno di `logrotate`.
