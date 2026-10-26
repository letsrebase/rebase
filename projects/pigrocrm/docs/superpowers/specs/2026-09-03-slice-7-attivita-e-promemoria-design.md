# PigroCRM — Slice 7: Attività e promemoria

**Data:** 2026-09-03
**Stato:** da approvare
**Prerequisiti:** slice 1 (Core CRM + MCP), slice 2 (Documenti), slice 3 (Fatturazione), slice 4
(Time tracking e P&L), slice 5 (Gmail e invii), slice 6 (Ricerca, automazioni, dashboard) —
tutti in `main`.

**Ambito:** una cosa da fare, con una scadenza, attaccata a un cliente, a una persona o a un deal.
Il pannello «Oggi» che ne discende. Le tre automazioni che ne creano una senza che nessuno la digiti.

---

## 1. Perché questo slice esiste

Il brief originale elenca sette funzionalità con la stella. Sei sono state costruite: timeline
unificata, note per entità, pipeline Kanban, preventivo vs consuntivo, ricerca globale, automazioni
semplici. La settima — *«Todo e promemoria: ogni Cliente e Deal dovrebbe avere attività ("richiamare
il cliente", "inviare offerta", "sollecitare pagamento") con scadenze»* — è l'unica rimasta, ed è
quella che il brief stesso definiva **«una delle funzionalità più usate in un CRM»**.

La sua assenza si vede in una asimmetria precisa. `activities` esiste dallo slice 1 ed è ottima: una
tabella sola, `kind` aperto, ogni slice successivo vi ha appeso i propri eventi senza una migrazione.
Ma `activities` registra **il passato**. Non c'è nessuna tabella che descriva il futuro.

Questo rende PigroCRM, oggi, uno strumento che risponde benissimo a *«com'è andata?»* e non risponde
affatto a *«cosa devo fare adesso?»*. La dashboard dello slice 6 è il caso limite: sa dirti quanto
hai fatturato, quali offerte sono ferme da venti giorni e quali fatture sono scadute — e da nessuna
di quelle tre risposte discende un'azione che il sistema possa ricordarti. Un'offerta ferma da venti
giorni è un segnale; «richiamare Rossi giovedì» è un impegno. Il segnale lo produce già; l'impegno no.

C'è anche un debito concreto da riscuotere, come in ogni slice di questo progetto. `payment_reminders`
(slice 5B, task B2-8) è una tabella di impegni con una scadenza, un livello e un tetto — ma sa
occuparsi di **una sola cosa**, il sollecito di pagamento. Il meccanismo generale che avrebbe dovuto
ospitarla non esisteva. Questo slice lo costruisce, e §9 dice esplicitamente cosa si fa e cosa **non**
si fa di quella tabella.

---

## 2. Il confine con ciò che esiste già

Tre entità vicine, e la ragione per cui nessuna delle tre basta.

| Esiste | Cos'è | Perché non basta |
|---|---|---|
| `activities` | La timeline. Append-only, `kind` aperto, un evento accaduto | Nessuna scadenza, nessuno stato aperto/chiuso, nessun assegnatario. Aggiungere tre colonne a una tabella append-only per farne una lista di cose da fare significa che ogni riscrittura di un impegno diventa un secondo evento nella timeline — o che la timeline smette di essere append-only. Entrambe peggiori di una tabella nuova |
| `payment_reminders` | Il sollecito, con scadenza, livello e tetto per fattura | Sa solo di fatture. Il suo `livello` e il suo tetto sono regole del sollecito, non di un impegno generico |
| `automation_config` | Le regole che reagiscono a un cambio di stato | Reagiscono, non ricordano. Un'automazione agisce nell'istante del trigger; un promemoria esiste *fra* un istante e un altro |

**Decisione:** una tabella nuova, `attivita`. Non un `kind` di `activities`, non una generalizzazione
di `payment_reminders`.

---

## 3. Modello dati

### 3.1 `attivita`

| Colonna | Tipo | Note |
|---|---|---|
| `id` | UUID v7 | |
| `titolo` | `String(200)` | Obbligatorio. È ciò che si legge nella lista |
| `note` | `Text` null | Il dettaglio, se serve |
| `scadenza` | `Date` null | **Nullable di proposito.** Vedi §3.2 |
| `stato` | `String(20)` | `aperta` \| `completata` \| `annullata` |
| `completata_il` | `Date` null | Valorizzata quando e solo quando `stato = 'completata'`, vincolo di check |
| `assegnata_a` | UUID null → `users.id` | Null significa «di chi apre la lista», non «di nessuno» |
| `customer_id` | UUID null | |
| `person_id` | UUID null | |
| `deal_id` | UUID null | |
| `invoice_id` | UUID null | |
| `origine` | `String(20)` | `manuale` \| `automazione` \| `sollecito` |
| `regola` | `String(50)` null | Quale automazione l'ha creata, quando `origine = 'automazione'` |
| `custom_fields` | JSONB | `entity_type = 'attivita'`, come tutto il resto |
| `deleted_at` | timestamptz null | Soft delete |

**Al massimo un riferimento, e almeno zero.** Un vincolo di check impone che al più uno fra
`customer_id`, `person_id`, `deal_id` e `invoice_id` sia valorizzato. Zero è legittimo: «fare la
fattura elettronica di marzo» non appartiene a nessun cliente. Due no: un'attività che comparisse su
due schede è un'attività che verrà chiusa una volta e lasciata aperta un'altra.

Questo è **diverso** da `documents`, dove lo slice 2 impone *esattamente uno* fra cliente e deal. La
differenza è deliberata e ha una ragione: un documento è un fatto che riguarda qualcuno, un impegno
può riguardare solo te.

### 3.2 Perché `scadenza` è nullable

Perché il caso più comune di lista di cose da fare è quello senza data. «Chiedere a Rossi il codice
SDI» è un impegno vero che non scade giovedì.

Una `scadenza` obbligatoria produce uno di due comportamenti, entrambi cattivi: l'utente inventa una
data che non significa nulla — e da quel momento la lista di «cose in scadenza» contiene rumore — o
non registra l'impegno. La prima è peggiore, perché avvelena la funzione che dà valore alla tabella.

**Conseguenza da rispettare ovunque:** un'attività senza scadenza non è «in ritardo», non è «per
oggi», e non compare in nessun conteggio ordinato per data. Compare nella lista dell'entità e in una
sezione «senza scadenza» del pannello Oggi. Lo stesso errore che lo slice 6C ha già dovuto correggere
su `documents.stato_dal` — *un `NULL` non è zero giorni* — vale qui parola per parola.

### 3.3 Gli indici

Tre, e ognuno risponde a una domanda che l'interfaccia pone davvero:

- `ix_attivita_scadenza` su `(stato, scadenza)` parziale su `deleted_at IS NULL` — il pannello Oggi.
- `ix_attivita_entity` su `(customer_id, deal_id, person_id, invoice_id, scadenza)` parziale sullo
  stesso predicato — la tab «Attività» di una scheda.
- `ix_attivita_assegnata` su `(assegnata_a, stato, scadenza)` — «le mie».

Ognuno va misurato come lo slice 6A ha misurato i propri, contro un corpus gonfiato e col planner
libero. E vale la lezione del task A9: **le statistiche di un indice si scrivono con `VACUUM
(ANALYZE)`, non con `ANALYZE`** — un indice caricato in massa e solo analizzato riporta «nessuna
statistica» e il planner risponde di un ordine di grandezza in eccesso.

---

## 4. Gli stati, e perché sono tre e non due

`aperta` → `completata` è ovvio. Il terzo, `annullata`, esiste perché la cosa che si vuole sapere sei
mesi dopo non è «questa attività è sparita», ma **perché**.

Un impegno che non serve più non va cancellato: va chiuso dichiarando che non serviva. «Sollecitare
Rossi» annullata perché Rossi ha pagato è un'informazione; la stessa riga eliminata è un buco.
Il soft delete resta per l'errore di battitura — ho creato l'attività sbagliata — e non per il
cambio di programma.

`completata_il` è una `Date` e non un timestamp perché la domanda che si pone è «l'ho fatto» e non «a
che ora». Ed è scritta con `today_local()`, mai con `date.today()`: lo slice 6B ha ridotto il progetto
a **un solo orologio** e una scansione AST fallisce la build se qualcuno ne introduce un secondo.

---

## 5. Il pannello «Oggi»

Una pagina, `/app/oggi`, e quattro sezioni in quest'ordine:

1. **In ritardo** — `scadenza < oggi`, stato `aperta`. In cima perché è ciò che fa male.
2. **Oggi** — `scadenza = oggi`.
3. **Prossimi sette giorni** — `oggi < scadenza <= oggi + 7`.
4. **Senza scadenza** — `scadenza IS NULL`, in fondo e ripiegabile.

«Oggi» è `today_local()`, cioè il giorno che sta vivendo la persona, non quello del fuso in cui gira
il processo. Non è pedanteria: l'immagine image dell'API gira in UTC, e fra mezzanotte e l'01:00 CET un
`date.today()` risponde *ieri*. Tre siti di produzione sono già stati corretti per questo errore
esatto, uno dei quali rifiutava una registrazione di ore perfettamente legale.

**Il conteggio in ritardo va anche nell'header**, accanto alla ricerca globale che lo slice 6A ha
messo lì. È l'unico numero del prodotto che chiede un'azione invece di descrivere uno stato, e va
visto senza aprire una pagina.

---

## 6. Le automazioni che creano un'attività

Lo slice 6B ha costruito `AutomationRunner` con quattro ragioni dichiarate per non agire, un
`AutomationOutcome` in cui un rifiuto senza motivo è **inesprimibile**, e la garanzia che il runner
giri dentro la transazione del trigger — o l'attività e il cambio di stato accadono insieme, o nessuno
dei due. Questo slice ne usa il meccanismo per tre regole nuove, non ne costruisce un secondo.

| Regola | Trigger | Attività creata | Scadenza |
|---|---|---|---|
| `offerta_inviata_richiama` | Un documento di tipo `offerta` passa a `inviata` | «Richiamare {cliente} sull'offerta {titolo}» | +7 giorni |
| `deal_fermo` | Un deal resta nello stesso stato oltre la soglia configurata | «Il deal {nome} è fermo da {n} giorni» | oggi |
| `fattura_scaduta` | Una fattura emessa supera `data_scadenza` non incassata | «Sollecitare {cliente} per {numero}» | oggi |

Tutte e tre **disattivabili** da `automation_config`, come le esistenti, e tutte e tre soggette alla
stessa regola di R5 che lo slice 6B ha applicato a se stesso: la configurazione è audited.

> **Note of 2026-10-26 (REB-727, REB-728).** Built as a daily pass, `pigrocrm automations`
> (`docs/superpowers/notes/2026-10-26-automations-cron-runbook.md`), and the table above is
> read as follows. `offerta_inviata_richiama` matches an offer in `inviata` for 7 to 37 days
> (older ones are no longer the follow-up of that sending), due 7 days after it; `deal_fermo`
> counts from the last stage change on the deal's timeline (the deal's creation when it never
> moved) against `a4_deal_fermo_giorni`, 30 by default; `fattura_scaduta` is the receivables
> page's «scaduto». Each is assigned to the deal's owner, else the customer's. The index of
> §6.1 is over `(regola, coalesce(customer_id, person_id, deal_id, invoice_id))` and not over
> the four columns: a NULL is distinct from a NULL, so four nullable columns in one key would
> admit every duplicate. The attività reminders skip what `fattura_scaduta` opened, because
> `invoice_overdue` already raises that cause.

### 6.1 L'idempotenza, che è la parte difficile

Una regola che gira ogni giorno su una fattura scaduta crea un'attività al giorno. Dopo una settimana
la lista è illeggibile e la funzione è peggio che assente.

**Vincolo:** un indice unico parziale su `(regola, customer_id, person_id, deal_id, invoice_id)`
limitato a `stato = 'aperta' AND deleted_at IS NULL`. Finché l'attività precedente è aperta, la regola
non ne crea una seconda; quando l'utente la chiude, la regola può ricominciare — che è il
comportamento giusto, perché chiudere «sollecitare Rossi» senza che Rossi paghi significa «ci ho
parlato», e fra un mese il promemoria serve di nuovo.

Questo è un vincolo di database e **non** un controllo `SELECT`-poi-`INSERT`. Il vincolo globale del
progetto lo dice: un pre-controllo di unicità non sostituisce mai il vincolo, perché due richieste
concorrenti passano entrambe la `SELECT`. Serve il vincolo, più la cattura di `IntegrityError` con
rollback e la rialzata del `Conflict` di dominio.

E va dimostrato come questo progetto dimostra le affermazioni sulla concorrenza: **fixture committata,
due connessioni, una barriera** — non due chiamate in fila. Lo slice 5 ha stabilito lo standard e lo
slice 6 lo ha ripetuto quattro volte.

---

## 7. La superficie MCP

È la decisione più interessante dello slice, e va nella direzione **opposta** a quella dei solleciti.

**Esposto:** `create_attivita`, `list_attivita`, `complete_attivita`, `update_attivita`.

Il confronto con `create_reminder` — deliberatamente *non* esposto nello slice 5B — è il punto. Un
sollecito è una richiesta di denaro a nome del proprietario, col suo IBAN, e prepararlo **consuma** una
delle tre posizioni per fattura: un agente spenderebbe il tetto che impedisce a una fattura contestata
di diventare una persecuzione automatica. Un'attività non esce di casa. Nessuno la riceve, non ha un
destinatario, e l'unico effetto di una sbagliata è una riga da chiudere.

È anche il posto in cui un agente vale di più: *«ricordami di richiamare Rossi giovedì»* è
esattamente la frase che una persona dice a voce e non digita mai in un form.

`delete_attivita` resta **fuori**: il soft delete è per l'errore di battitura, e chiudere con
`annullata` è ciò che un agente deve fare. Come sempre l'esclusione è **strutturale** — il tool non è
registrato — e non un controllo di permessi, perché un PAT eredita il ruolo pieno del proprietario e
non scade.

Ogni metodo nuovo dev'essere un tool o un'esclusione **nominata e motivata**:
`test_mcp_surface_coverage.py` lo impone meccanicamente, e nessuna voce può rimandare a un task futuro.

> **Note of 2026-10-26 (REB-724).** The paragraph on `delete_attivita` stands, but the sentence
> «chiudere con `annullata` è ciò che un agente deve fare» no longer rules out archiving:
> `archive_attivita` and `restore_attivita` are on the default MCP surface, because archiving
> is a reversible soft delete and that is what the reasoning above protects. `delete_attivita`
> stays unregistered. Row in `docs/design/DECISIONS.md`, 2026-10-26.

---

## 8. Cosa questo slice deliberatamente non fa

- **Nessuna ricorrenza.** «Ogni primo del mese» è una funzione a sé, con la sua ora di generazione e i
  suoi casi limite (il 31 di febbraio). Se serve, è uno slice suo.
- **Nessuna notifica push, nessuna email di promemoria.** Il canale email esiste dallo slice 5, ma
  mandare posta per conto di qualcuno è la cosa che quello slice ha scelto di non automatizzare mai.
- **Nessun assegnatario multiplo.** Il prodotto è per un freelance e una micro software house;
  `assegnata_a` è una FK, non una tabella di join.
- **Nessuna priorità.** La scadenza è la priorità. Un campo priorità accanto a una scadenza è un
  secondo ordinamento che contraddice il primo.

> **Note of 2026-10-26 (REB-729).** The line «Nessuna notifica push, nessuna email di promemoria»
> is superseded: Lorenzo reversed it on 2026-10-26. Reminders, with push, Slack and email, are
> in `2026-10-26-promemoria-design.md`; the row of `docs/design/DECISIONS.md` dated 2026-10-26
> says why. The other three entries of this list (no recurrence, no multiple assignees, no
> priority) stand as they were.

---

## 9. `payment_reminders`, e perché resta dov'è

La tentazione ovvia è farne una vista di `attivita`. **No**, e la ragione va scritta perché non si
ripresenti.

`payment_reminders` porta tre cose che un'attività non ha: un `sequence` (la posizione nel registro
dei solleciti, che decide quale testo si usa), un `livello` (la durezza della formulazione) e un tetto
di tre per fattura che esiste perché un sollecito è una richiesta di denaro. Nessuna delle tre ha
senso su «richiamare Rossi».

Quello che **si fa** è il contrario: la regola `fattura_scaduta` di §6 crea un'*attività* che dice
«sollecitare», e da lì si arriva con un click alla pagina solleciti che già esiste. L'attività è il
promemoria; il sollecito resta l'atto. Il primo può essere creato da un agente, il secondo no — ed è
esattamente §7.

---

## 10. Criteri di accettazione

1. Un'attività senza scadenza si salva, compare nella scheda della sua entità e nella sezione «senza
   scadenza» del pannello Oggi, e **non** compare in nessun conteggio di ritardo.
2. Un'attività con `scadenza = ieri` e stato `aperta` compare in «In ritardo» e nel contatore
   dell'header.
3. Il confine di «oggi» è `today_local()`: con l'orologio congelato a 00:30 dell'1 gennaio a Roma —
   le 23:30 del 31 dicembre in UTC — la sezione «Oggi» contiene le attività dell'1 gennaio.
4. La regola `fattura_scaduta` che gira due volte sulla stessa fattura crea **una** attività, e la
   seconda esecuzione riporta uno dei motivi dichiarati. Dimostrato con due connessioni e una
   barriera, non con due chiamate in fila.
5. Chiusa l'attività, la stessa regola ne crea una nuova alla passata successiva.
6. Un'attività creata da un'automazione e il cambio di stato che l'ha scatenata o esistono entrambi o
   non esiste nessuno dei due — provato facendo fallire la seconda metà e verificando che la prima
   sia sparita.
7. `create_attivita` esiste come tool MCP; `delete_attivita` non è registrato, e la scansione della
   sorgente non trova nessuna chiamata al soft delete sotto `tools/`.

   > **Note of 2026-10-26 (REB-724).** Criterion 7 is amended: `archive_attivita` and
   > `restore_attivita` are registered (in `tools/__init__.py`, implemented in
   > `tools/calendario.py`), so the source scan does find calls to the soft delete under
   > `tools/`. It stays true that `delete_attivita` is not registered and that no tool deletes
   > an attività for good. Row in `docs/design/DECISIONS.md`, 2026-10-26.
8. Nessuna cifra e nessuna data nasce nel pacchetto della pagina: `oggi` arriva da un servizio.
9. Un'attività compare nella ricerca globale, come sesto ramo — con il suo indice trigram, misurato.
10. Le tre regole nuove sono disattivabili e la loro configurazione lascia traccia in timeline.
