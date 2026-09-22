# PigroCRM — Slice 8: Scadenziario incassi e fatturazione pianificata

**Data:** 2026-09-03
**Stato:** parte A approvata da Ivan e realizzata il 2026-09-22 (REB-329, vedi §2.4); parte B da approvare
**Prerequisiti:** slice 3 (Fatturazione), slice 4 (Time tracking e P&L), slice 6 (Dashboard) in
`main`. Lo slice 7 (Attività e promemoria) **non** è un prerequisito, ma §6 lo usa se c'è.

**Ambito:** due domande sul futuro del denaro. *Quando arriva ciò che ho già fatturato* e *cosa dovrò
fatturare*. La prima si risponde con i dati che esistono; la seconda richiede una tabella nuova.

---

## 1. Perché questo slice esiste

Il ciclo dichiarato nello slice 1 finisce con «Analisi economica», e lo slice 4B l'ha costruita bene:
margine per deal, conto economico di periodo, preventivo contro consuntivo, stima fiscale. Tutte
domande sul **passato**.

Lo slice 6 ha aggiunto la dashboard, e ha portato il presente: quanto c'è da incassare adesso, quanto
è scaduto adesso, quante ore sono lavorate e non fatturate adesso.

Nessuno dei due risponde alla domanda che un freelance si fa il primo del mese, che è una domanda sul
futuro e si divide in due:

- **«Quando arrivano i soldi che ho già fatturato?»** — i dati ci sono tutti (`data_scadenza`,
  `stato_pagamento`, `totale`), ma il prodotto ne mostra due totali aggregati, `da_incassare` e
  `scaduto`. Due numeri non sono uno scadenziario: non dicono se i 40.000 € da incassare arrivano
  fra dieci giorni o fra cinque mesi, e la differenza fra le due risposte è se puoi assumere.
- **«Cosa dovrò fatturare?»** — e qui l'informazione **non esiste da nessuna parte**. Un retainer da
  3.000 € al mese per dodici mesi, o tre milestone contrattuali su un deal, sono impegni presi che
  nessuna riga del database rappresenta. Vivono in un contratto PDF e nella testa di chi l'ha firmato.

La seconda è la ragione principale di questo slice. È anche il complemento esatto di una funzione
appena costruita: `unbilled_backlog` (slice 6C) dice **cosa puoi fatturare** — ore lavorate e non
ancora legate a una fattura emessa. Un piano dice **cosa devi fatturare**. Sono due insiemi diversi, e
la parte interessante è dove non coincidono: lavoro fatto che nessuno ha pianificato di fatturare, e
scadenze contrattuali che arrivano senza che il lavoro sia stato fatto.

---

## 2. Parte A — lo scadenziario incassi

### 2.1 Cosa è, esattamente

Le fatture **emesse e non incassate**, ordinate per `data_scadenza`, raggruppate in cinque fasce:

| Fascia | Predicato |
|---|---|
| Scaduto | `data_scadenza < oggi` |
| Entro 30 giorni | `oggi <= data_scadenza <= oggi + 30` |
| 31–60 | |
| 61–90 | |
| Oltre 90 | |
| Senza scadenza | `data_scadenza IS NULL` |

Sei righe, non cinque: `data_scadenza` è nullable e un `NULL` **non è oggi**. È lo stesso errore che lo
slice 6C ha già dovuto correggere due volte — su `documents.stato_dal` e sul giorno senza ore — e la
regola è la stessa: una fascia temporale non inghiotte l'assenza di data.

### 2.2 Il vincolo che rende questa parte poco costosa, e verificabile

**Nessun dato nuovo.** Ogni figura viene da colonne che esistono dallo slice 3, e il totale delle sei
fasce deve fare esattamente `da_incassare`, che la dashboard economica già calcola.

Questo è il criterio 1 dello slice 6 applicato a se stesso, ed è il test che vale: non «lo scadenziario
restituisce sei numeri», ma **la loro somma è identica, al centesimo, alla cifra che un'altra parte del
sistema calcola per un'altra strada**. Se le due divergono, una delle due mente — ed è esattamente il
tipo di disaccordo che il criterio 2 dello slice 6 esiste per impedire fra una card e la sua
drill-through.

Il predicato «emessa e non incassata» esiste già come `_receivable_filter` in `InvoiceRepository`
(slice 6C, task C3): **va importato, non riscritto**. Una seconda copia è come una card e la sua lista
finiscono per disaccordarsi.

### 2.3 Dove vive

Una tab «Scadenziario» accanto a «Economica» in `/app/analisi`, e la cifra della fascia scaduta è
cliccabile verso la lista fatture già filtrata — la pagina solleciti dello slice 5B è a un click da lì,
perché è ciò che si fa con una fattura scaduta.

### 2.4 Com'è stata realizzata (2026-09-22, REB-329)

Una tab «Scadenziario» nella Home accanto a Economica e Commerciale, senza periodo. Oltre alle sei
fasce del §2.1, con la stessa lettura e lo stesso predicato importato, la tab mostra tre cose che i
dati permettevano già e nessuna schermata diceva: l'atteso per mese di `data_scadenza`,
l'esposizione per cliente con la quota scaduta, e le fatture scadute dalla più vecchia con quanti
solleciti sono partiti (`payment_reminders.sent_at`, non le bozze) e quando l'ultimo. Le quattro
letture stanno in `InvoiceRepository` (`ageing_receivables`, `receivables_by_due_month`,
`receivables_by_customer`, `overdue_with_reminders`), la composizione in
`DashboardService.get_receivables_dashboard`, la REST è `GET /api/dashboard/receivables` e il tool
MCP `get_receivables_dashboard`. Solo la fascia «scaduto» ha una drill-through (`?scadute=true`
sulla lista fatture, che è lo stesso `_overdue_predicate`); le altre l'avranno quando la lista
prenderà un intervallo di scadenza. Il criterio 10 (orologio a mezzanotte e mezza) non ha un test
suo: `oggi` è `today_local()`, letto una volta e passato a tutte le letture.

---

## 3. Parte B — la fatturazione pianificata

### 3.1 `fatture_pianificate`

| Colonna | Tipo | Note |
|---|---|---|
| `id` | UUID v7 | |
| `customer_id` | UUID | Obbligatorio: si fattura sempre a qualcuno |
| `deal_id` | UUID null | Opzionale — un retainer può non avere un deal |
| `descrizione` | `String(200)` | Diventa la causale della fattura |
| `imponibile_previsto` | `Numeric(12, 2)` | |
| `data_prevista` | `Date` | Quando va emessa |
| `stato` | `String(20)` | `pianificata` \| `emessa` \| `annullata` |
| `invoice_id` | UUID null | Valorizzata quando `stato = 'emessa'`, vincolo di check |
| `piano_id` | UUID null | Le righe generate da una ricorrenza condividono questo |
| `note` | `Text` null | |
| `deleted_at` | timestamptz null | |

**`imponibile_previsto`, non `totale`.** L'IVA la decide il profilo fiscale al momento
dell'emissione, e una previsione che la include si disallinea il giorno in cui il regime cambia. È la
stessa ragione per cui una fattura emessa conserva una copia dei propri parametri fiscali invece di
rileggerli.

### 3.2 La ricorrenza, e come si evita di costruire un motore di cron

Un retainer mensile è la richiesta ovvia, ed è anche il punto in cui una funzione semplice diventa un
sistema di scheduling con i suoi casi limite — il 31 di febbraio, l'ora legale, il fuso.

**Decisione: le righe si generano tutte al momento della creazione, e sono righe normali.** L'utente
dice «3.000 € il 1° di ogni mese, da gennaio a dicembre», il sistema scrive **dodici righe** con lo
stesso `piano_id`, e da quel momento non esiste nessun processo che gira di notte. Ogni riga è
modificabile e annullabile per conto suo, perché nella vita reale il retainer di agosto salta.

Il costo è che estendere un piano oltre l'orizzonte richiede un'azione esplicita. È il costo giusto: un
generatore che estende da solo è un processo in più da sorvegliare, e questo prodotto non ne ha
nessuno oggi. Un tetto di 36 righe per piano rende la cosa impossibile da sbagliare in modo costoso.

Per il giorno del mese: `data_prevista` cade sul giorno richiesto, o **sull'ultimo giorno del mese** se
quel giorno non esiste. Il 31 gennaio genera il 28 (o 29) febbraio, non il 3 marzo. Va scritto in un
test, perché è la riga che qualcuno «semplificherà».

### 3.3 Dalla riga pianificata alla fattura

Un pulsante «Emetti», e ciò che produce è **una bozza**, mai una fattura emessa.

Questo non è prudenza generica: emettere consuma un numero del registro, e lo slice 3 ha costruito la
numerazione senza buchi con un contatore per anno sotto `SELECT … FOR UPDATE` — deliberatamente *non*
una `SEQUENCE`, perché `nextval()` non è transazionale e brucia numeri sul rollback. Un piano che
emettesse da solo trasformerebbe un errore di pianificazione in un buco nel registro fiscale.

La bozza nasce con cliente, causale e imponibile della riga; il resto è il flusso dello slice 3, che
già distingue «un agente prepara» da «una persona emette».

Quando la fattura viene emessa, la riga passa a `emessa` e punta alla fattura. Da quel momento la
previsione non è più una previsione, e §4 la esclude dai totali futuri.

### 3.4 La riconciliazione, che è il punto

Tre cifre per periodo, e la loro relazione è ciò che il prodotto guadagna:

| Cifra | Da dove |
|---|---|
| **Pianificato** | somma degli `imponibile_previsto` con `stato = 'pianificata'` nel periodo |
| **Fatturato** | `revenue_in_range`, che lo slice 4B già calcola |
| **Fatturabile non pianificato** | `unbilled_backlog` (slice 6C) meno ciò che un piano copre |

La terza è quella che nessuno vede oggi: lavoro fatto, fatturabile, che nessun piano prevedeva. Per un
consulente è la voce che si dimentica di fatturare.

---

## 4. Le regole che i totali devono rispettare

1. Una riga `emessa` **non** entra nel pianificato futuro. Contarla due volte — una come previsione e
   una come ricavo — è il difetto che rende inutile qualunque previsione.
2. Una riga `annullata` non entra in nessun totale, e resta visibile con il suo stato: sapere che un
   retainer è stato interrotto ad agosto è un'informazione.
3. Il pianificato è **al netto dell'IVA**, come il fatturato con cui viene confrontato. Confrontare un
   imponibile con un totale è un errore che si nota solo quando i numeri sono grandi.
4. Il denaro è `Decimal`, mai `float`, e `money.py` è l'unico posto dove si arrotonda. Nel browser ogni
   cifra arriva come **stringa** e una guardia AST fa fallire la build se un campo monetario finisce in
   un'espressione aritmetica.
5. «Oggi» è `today_local()`. C'è **un solo orologio** in questo progetto e una scansione AST fallisce
   la build se qualcuno ne introduce un secondo.

---

## 5. La superficie MCP

**Esposto:** `list_fatture_pianificate`, `create_fattura_pianificata`, `update_fattura_pianificata`,
`get_scadenziario`.

**Non esposto:** l'azione che crea la bozza da una riga pianificata.

La linea è la stessa dello slice 3 e la ragione è identica: preparare una previsione è dichiarare
un'intenzione, e sbagliarla costa una riga da correggere. Creare una bozza entra invece nel flusso che
porta a consumare un numero di registro, e quel flusso resta di una persona. `issue_invoice` non è
raggiungibile da un agente per costruzione, e questo slice non apre una seconda porta sulla stessa
stanza.

Come sempre l'esclusione è **strutturale** — il tool non è registrato — e non un controllo di permessi,
perché un PAT eredita il ruolo pieno del proprietario e non scade. Ogni metodo nuovo dev'essere un tool
o un'esclusione **nominata e motivata**: `test_mcp_surface_coverage.py` lo impone meccanicamente.

---

## 6. Il legame con lo slice 7, se c'è

Se le attività esistono, una riga pianificata la cui `data_prevista` è arrivata e che è ancora
`pianificata` genera un'attività «Emettere {descrizione} per {cliente}», con la stessa idempotenza di
§6.1 di quello slice: un indice unico parziale, non un `SELECT`-poi-`INSERT`.

Se non esistono, lo scadenziario le mostra in una sezione «da emettere» e questo slice funziona lo
stesso. **Nessuna dipendenza dura in nessuna delle due direzioni.**

---

## 7. Cosa questo slice deliberatamente non fa

- **Nessuna previsione automatica.** Il sistema non indovina che fatturerai come l'anno scorso. Un
  piano lo scrive una persona; una previsione statistica su un solo cliente è rumore con l'aria di un
  numero.
- **Nessun sollecito automatico dallo scadenziario.** Lo slice 5B ha deciso che l'invio è di una
  persona; questa vista porta al sollecito, non lo manda.
- **Nessuna gestione di acconti e note di credito.** Sono fatture con una loro semantica fiscale e
  appartengono a un'estensione dello slice 3, non a una vista di previsione.
- **Nessun cash-flow con le uscite.** I costi esistono (slice 4A) ma un cash-flow vero richiede anche
  le scadenze fiscali e i contributi, cioè un modello che questo prodotto non ha.

---

## 8. Criteri di accettazione

1. La somma delle sei fasce dello scadenziario è **identica al centesimo** a `da_incassare` della
   dashboard economica, calcolata per un'altra strada. Il test lo verifica su un corpus con almeno una
   fattura per fascia, inclusa una senza `data_scadenza`.
2. Una fattura senza `data_scadenza` compare in «senza scadenza» e in **nessuna** fascia temporale.
3. Il predicato «emessa e non incassata» è `_receivable_filter` importato, non riscritto — verificato
   da una scansione della sorgente che fallisce se il predicato compare due volte.
4. Un piano mensile creato il 31 gennaio per dodici mesi genera dodici righe, e quella di febbraio cade
   il 28 (o il 29 in anno bisestile), non il 3 marzo.
5. Un piano non può superare 36 righe.
6. Il pulsante «Emetti» produce una **bozza**: nessun numero di registro è consumato, provato leggendo
   il contatore dell'anno prima e dopo.
7. Una riga passata a `emessa` sparisce dal pianificato futuro e la sua fattura compare nel fatturato:
   la somma dei due non cambia attraversando quel confine.
8. Il pianificato è confrontato con `revenue_in_range` e sono **entrambi imponibili**, verificato da un
   test che fallisce se uno dei due usa `totale`.
9. Il tool che crea una bozza da una riga pianificata **non** è registrato, e la scansione della
   sorgente non trova nessuna chiamata a `issue` sotto `tools/`.
10. Con l'orologio congelato a 00:30 dell'1 gennaio a Roma, la fascia «scaduto» contiene ciò che
    scadeva il 31 dicembre e non ciò che scade l'1 gennaio.
