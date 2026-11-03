"""The Italian the lint looks for. Data only; the rules are in `english_lint.py`.

Three sets, all lowercase:

* `WORDS`: whole words. An identifier is split into words (`snake_case`, `camelCase`,
  `kebab-case`, `SCREAMING_CASE`) and fails when any word is here. Every word is checked
  against an English dictionary when it is added (nothing here is also an English word),
  which is why `data`, `note`, `a`, `da` and `nome`-like homographs the codemod left
  alone are not: the lint cannot tell `data` the date from `data` the payload, and a
  reviewer can.
* `STEMS`: prefixes for the inflected forms (`fattur` covers fattura, fatture, fatturato,
  fatturazione). Each is at least five letters and starts no English word.
* `STOPWORDS`: Italian function words, used only to recognise a comment or a docstring
  written in Italian. Several of them are English words in another sense (`per`, `non`,
  `con`), so one alone never flags a comment; the rules need two, or one plus an
  Italian word from the sets above, or an accented letter.
"""

WORDS: frozenset[str] = frozenset(
    """
accaduti accedi accessi accesso accettata accettate accettati accettazione acquisto
adesso adottata agenti aggiorna aggiornate aggiungi aggiunta agosto aliquota allegati
allegato allerta altre altri altrimenti altro ambiguo ambito ammesse ammesso anagrafica
anagrafici anche ancora andamento anni anno annuale annulla annullamento annullata
annullate annullati anonima anteprima aperta aperte aperti aperto applica applicata
applicato approvato approvazione apri archiviata archiviate archiviato arretrate
arretrato articolo assegnata assegnataria assegnate assente assicurazione assistente
assorbito attesa attese atteso attiva attivata attive attivi attivita attivo attore
attraverso attribuibili attuale attuali automazioni autore autorizzata autorizzazione
avanti avanza avanzamento avvertenza avvisi azienda aziende azione azioni barra bassa
beni blocco bollo bottone bozza bozze breve brevi buchi buco calcolabile calcolato
calendario cambio campagna campagne campi campo canale canali cancellati carica cartella
cassa categoria categorie causale cedente cella cerca cessionario chiavi chiudi chiusa
chiuse chiusi chiuso chiusura chiusure ciascuno cig citato citta civico clausola
clausole clic cliccate cliente clienti codice coefficiente cognome coincidono
collaboratore collegamento collegare collegati collegato colonna colonne commerciale
compenso competenza competenze completa completata completato completo comune comunque
concentrazione condiviso condizioni conferma confermata conferme confidenza configurato
configurazione consegnata consegnate conseguenza consulenza consumata consumate
consuntivata consuntivate consuntivo conta contabile contatta contattabile contatti
conteggi conteggio contenuto contratti contratto contrattuali contributi contributo
contro controlla conversazioni conversione coordinamento coperta corpo corrente
corrispondenti corso cosa costi costo crea creato creazione credito dalla dati davanti
debito decisioni deciso denominazione dentro descrizione descrizioni destinatari
destinatario destinazione dettagli dettaglio dichiarate dichiarati dichiarato
dichiarazione dietro differenze dimensione diretti disattivata disattivato disdetta
disiscrizione disponibile divisa documenti documento domani domicilio dominio dopo
dovuto dunque duplicati duplicato durata economia economica effettivo elimina eliminata
emessa emesse emesso emettere emissione emittente entita entra entrambi entrate
entrato entro equivalenti erogazione errore escludi escluse esclusi esclusiva escluso
esecuzioni esegui esigibilita esistente esito esperienza esplicita esplicito esplode
esporta esposizione esterna esternamente esterno estero esteso estratto etichetta
etichette fallita fallite fasce fascia fatta fattura fatturabile fatturabili fatturapa
fatturare fatturata fatturate fatturati fatturato fatturazione fatture febbraio ferma
fermo figura filtra filtri filtro finalita finestra fino firma firmato fiscale fiscali
fissa fisso fonte fonti forfettario formato fornitore fra frazione frazioni fuori futura
generali generata gennaio gia giornaliera giornaliero giornata giornate giorni giorno
giugno grafico grande gruppi gruppo guida identificativi identificativo identita ieri
impegno imponibile importa importata importati importi importo imposta impostata
impostato impostazioni incassare incassata incassate incassati incassato incassi incasso
incerta incluse indietro indirizzo infatti iniziato inizio inoltre interessati interno
intero interpretazione inversione invia inviata inviate inviati inviato invio invitato
invito irrevocabilita iscrizione iscrizioni  italiana italiani lasciati lavorato
lavoro legali leggibile lettera letterale lettere letti letto lettura libero limitato
limite linea linee lingua lingue lista listino livelli livello lontano lordo lungo luogo
mancante mancanti manuale mappature margine materiali maturato membri membro mensile
mentre mese mesi messaggi messaggio mezza mie minime minimo misura mittente modalita
modelli modifica modificabile modificata molti molto mostrate motivazione motivo
movimenti nativa natura nazione necessario negato nella nello nessuna nessuno netto
niente nomi nominale normativo nota  nulla numero nuova nuove nuovi nuovo
obbligatoria occasionale offerta offerte oggetto oggi ognuno operativa operazione oppure
oraria orario ordina ordinaria ordinario ordine ordini origine orizzonte paese paesi
pagamento pagata pagato pagina pagine partita passi passo percentuale perche perciò
percorsi percorso perde periodi periodo persi perso personale personali persone pila piu
piva poco politica ponderato posizione possibile preautorizzazione preavviso precedente
precedenti predefinita preferita presenti presenza presso preventivata preventivate
preventivati preventivato previdenziale prevista previste previsti previsto prezzo prima
primi primo probabilita processo profilo progetto programmata programmato progressivo
proiettato proiezione promemoria pronta proposta proposte proposti proposto proprio
prossima prossimi prossimo prova provenienza provincia pulito pulsante punteggio punto
quadri quadro qualcosa qualcuno quando quante quanti quantita quindi raccolta radice
raggiungibile raggruppa ragione rapporti rapporto  reale recenti reclamo redditivita
reddito referente regimi registra registrare registrata registrate registrati registro
regola regole reinvia remoto residente residuo revisione revocato riapri riassunto
ricavi ricerca riceventi riceveranno ricevuta ricevuto richiama richiedente richiesta
richieste richiesto ricorrente rientra riepiloghi riepilogo rif riferimenti riferimento
riferire rifiutata rifiutato rigenerare righe rimanda rimbalzata rimbalzate rimborsabile
rimosso rimuovere rimuovi rinnova rinnovo riprova riscrivi risorse risposta risultati
risultato ritardo riusato rivalsa rivedere  ruoli ruolo saltata saltate salva
salvato scade scadenza scadenze scadenziario scadono scaduta scadute scaduto scarica
scaricata scartato scegli scheda schermata sconto scorsa scostamento scritte scrivere
secondo sede segnalato segnali seguisse selettivo seleziona sempre senza servizi
servizio sessione setaccio sette settimana settimanale settimane settore settori sezione
sezioni sigla simulata simulati simulato sintesi sintetica sito situazione sociale
soggetta soglia soglie solleciti sollecito somma sopra sorgente sostitutiva sotto
sottotitolo sovrascritte spazi spazio spese spesso stampa stati stato stessa stima
stimato storico succede successivo successore suggeriti suo superata tabella talenti
talento tanto tantum tariffa tariffe tasso telefono tentativi termine terzo testo tetto
tipo titolare titoli titolo tolte torna totale totali tra tramite transizioni trasferte
trasmessa trasmissione trasmittente trattamenti trimestrale troncato troppo trovato
tuttavia tutte tutti tutto ultima ultimi ultimo unibile unibili unita unitario urgente
usate usati utente utenti utilizzo vai valide valido valore valute variabile variabili
vecchia vecchio verbale verifica verificare verificata verificato versione verso viaggi
vicino vinti vinto virtuale visibile viste voce voci vuota vuoto
""".split()
)

STEMS: tuple[str, ...] = (
    "fattur",
    "aziend",
    "attivit",
    "scadenz",
    "emittent",
    "consuntiv",
    "contratt",
    "disiscri",
    "giornat",
    "incass",
    "rinnov",
    "scadut",
    "approvazion",
    "collegament",
    "configurazion",
    "descrizion",
    "richiest",
    "pagament",
    "dichiaraz",
    "autorizzazion",
    "registrazion",
)

STOPWORDS: frozenset[str] = frozenset(
    """
il lo gli della delle dello dei degli nella nelle nello nei negli alla alle allo agli
dalla dalle dallo dai dagli sul sulla sulle sui che cui chi non sono siamo siete era
erano viene vengono deve devono puo possono perche poiche quindi anche ancora sempre mai
senza gia piu molto tutto tutti tutte ogni questo questa questi queste quello quella
quelli loro nostro nostra vostro suo sua suoi sue come quando dove mentre dopo prima
oppure invece cioe ovvero tuttavia comunque pero ma se si ci vi lui lei noi voi
un uno una per con tra fra del dal nel sul
""".split()
)

# Words of the stop list that are ordinary English or code words: they count for half.
AMBIGUOUS_STOPWORDS: frozenset[str] = frozenset(
    "non per con tra fra del dal nel sul se si ci vi lo un uno una chi era come ma lui lei noi voi dove mentre".split()
)
