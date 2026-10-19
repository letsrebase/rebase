# Le password di Rebase: guida per i membri

Rebase tiene le credenziali condivise in un gestore di password che gira su un server
nostro: il **Rebase Password Manager**, che parla con le app di Bitwarden. Tu usi le app di
Bitwarden; l'indirizzo del server è quello di Rebase. Ognuno vede solo le raccolte che gli
sono state assegnate, e solo con il permesso che gli è stato dato.

**Indirizzo del server: `https://vault.letsrebase.com`**

## 1. Installa l'app

Estensione per il browser (Chrome, Firefox, Edge, Safari), app per Android e iOS, app per
computer: tutte su [bitwarden.com/download](https://bitwarden.com/download). Sono le app
ufficiali di Bitwarden; il server a cui si collegano lo scegli tu al passo 2.

**Attenzione alla password di Chrome.** Nelle impostazioni dell'estensione (Impostazioni,
Compilazione automatica) c'è «Usa Bitwarden come gestore di password predefinito». Se la
attivi, l'estensione spegne il salvataggio e la compilazione di password, indirizzi e carte
di Chrome (Gestore password di Google, o il portachiavi del Mac): sembra che il gestore
«abbia bloccato il portachiavi». Non è un guasto, è quell'interruttore. Se vuoi continuare a
usare le password di Chrome, lascialo spento; se l'hai già attivato, spegnilo da lì e Chrome
torna a salvare e compilare come prima.

## 2. Accetta l'invito e punta l'app al nostro server

1. Ti arriva una mail da **vault@letsrebase.com**: «Your invitation to the Rebase password manager», da **Rebase Password Manager**. Come le app, le mail sono in inglese.
   Apri il link: si apre `vault.letsrebase.com`. Se non la trovi, guarda nello spam: a volte i
   filtri la mettono lì, soprattutto la prima. Aprila e usa «Non è spam», così le prossime
   arrivano in posta in arrivo.
2. Scegli la **password principale** (master password). Usane una lunga, solo tua, che
   non usi altrove: una frase di quattro o cinque parole va bene. Non è la password della
   tua mail e il server non la conosce.
3. Nell'app, prima di accedere, tocca la rotellina o «Accedi su» e scegli **self-hosted**
   (server in hosting autonomo). Come «URL del server» scrivi
   `https://vault.letsrebase.com` e salva. Poi accedi con la tua mail e la password
   principale. Le app non permettono di registrarsi su un server che non ti ha invitato:
   è voluto.

## 3. Entra nell'organizzazione Rebase

Dopo l'invito, un proprietario dell'organizzazione ti **conferma** come membro: finché non
lo fa vedi solo il tuo spazio personale. Quando succede, nell'app compare l'organizzazione
**Rebase** e, dentro, le raccolte assegnate a te. Ogni raccolta ha un permesso:

- **solo visualizzazione**: vedi le voci e puoi copiare le password;
- **visualizzazione, password nascoste**: vedi la voce ma puoi solo usare la password
  (compilazione automatica), non leggerla né copiarla dall'app. Non la tiene segreta a
  te: il sito in cui la compili la riceve, e la pagina del gestore la contiene comunque in
  chiaro. Non è una protezione, e per questo non la usiamo;
- **modifica** (con password visibili o nascoste): puoi anche cambiare le voci;
- **gestione**: permette anche di decidere chi altro entra nella raccolta. Non la
  assegniamo: ce l'hanno solo i proprietari.

Se una voce che ti aspetti non c'è, non è un errore: non è stata assegnata a te. Chiedila a
un proprietario dell'organizzazione: solo i proprietari decidono chi entra in una raccolta.

### Come sono organizzate le raccolte

Una **voce** è un singolo accesso (un sito, un servizio, un account) con il suo utente e la
sua password. Una **raccolta** è una cartella condivisa che raggruppa voci, e ha i suoi
membri, ciascuno con il suo permesso. Quindi i permessi si decidono per raccolta e per
persona, non per singola voce:

- **Una password specifica per un account**: una voce per account («Hetzner, console»,
  «GitHub, organizzazione letsrebase»), dentro la raccolta dell'area a cui appartiene. Ogni
  servizio ha la sua voce, con la sua password: non si riusa mai la stessa.
- **Le raccolte che ci sono**, con i nomi in inglese come le app: `Infrastructure` (server,
  DNS, servizi tecnici) e `Administration` (account dell'azienda, fornitori) per le
  credenziali di Rebase, e per ogni cliente `Clients/<cliente>`, con dentro una raccolta per
  progetto, `Clients/<cliente>/<progetto>`. Chi lavora a un progetto riceve la raccolta di
  quel progetto, di solito in **solo visualizzazione**; chi non ci lavora non la vede.
- **Annidare è solo un nome**: `Clients/<cliente>/<progetto>` compare sotto il cliente, ma
  non eredita niente da `Clients/<cliente>`. Avere il progetto non ti fa vedere le voci del
  cliente, e il contrario: ogni raccolta si assegna da sola.
- **Niente gruppi**: non usiamo i «gruppi» del server (sono una funzione ancora in prova):
  si assegna la raccolta ai nomi, uno per uno.
- **Una voce in più raccolte**: si può, ed è la voce stessa a comparire in entrambe, non una
  copia. Se cambi la password in un posto, è cambiata dappertutto.

Per aggiungere una voce che serve agli altri, nell'app scegli «Nuovo elemento», come
proprietario **Rebase** e almeno una raccolta. Puoi farlo solo nelle raccolte in cui hai il
permesso di modifica: con «solo visualizzazione» la raccolta non compare nemmeno fra quelle
che puoi scegliere, e se ti serve aggiungere una voce lo chiedi a un proprietario. Una voce creata nel tuo spazio personale («La mia
cassaforte») resta tua e di nessun altro. Per condividerla, aprila dal menu e scegli
«Assegna alle raccolte», poi l'organizzazione Rebase e le raccolte giuste: **la voce passa
all'organizzazione**, non ne resta una copia personale, e da quel momento la gestisce chi ha
il permesso sulla raccolta.

I ruoli principali di chi fa parte dell'organizzazione sono tre e si distinguono per quanto vedono: un
**proprietario** e un **amministratore** aprono tutte le raccolte, anche quelle a cui non
sono assegnati; un **utente** vede solo le raccolte che gli sono state date. Per questo i
primi due ruoli restano a poche persone.

## 4. Iscriviti al recupero dell'account (non saltarlo)

Questo è il passo che decide cosa succede se **dimentichi la password principale**.

1. Nell'app web, **Impostazioni, Gestione dell'account** (o la pagina dell'organizzazione
   Rebase): trova «Recupero dell'account» / «Reimposta la password principale» e scegli
   **Iscriviti**. Se l'organizzazione ha l'iscrizione automatica, risulti già iscritto:
   controlla che sia scritto.
2. Se un giorno dimentichi la password, scrivi a un amministratore di Rebase. Può
   reimpostarla da «Membri» e tu scegli una nuova password principale. Le tue voci
   restano.

**Cosa si perde se non sei iscritto.** La password principale non è conservata da nessuna
parte: tutto ciò che è cifrato con lei si apre solo con lei. Se la dimentichi **e non sei
iscritto al recupero**, nessuno la può reimpostare e perdi per sempre le voci del tuo
spazio personale. Le voci delle raccolte di Rebase non sono tue: restano all'organizzazione
e un amministratore può darti di nuovo accesso con un account nuovo. Per questo: iscriviti,
e tieni la password principale in un posto sicuro che non sia questo gestore.

## Qualche regola

- **Cosa metterci**: le credenziali di Rebase stanno nelle raccolte. Nel tuo spazio
  personale tieni solo ciò che accetti possa essere recuperato da un amministratore, se
  sei iscritto al recupero, o perso, se non lo sei.
- **Attiva la verifica in due passaggi** sul tuo account (Impostazioni, Sicurezza,
  Verifica in due passaggi): un'app autenticatore va benissimo.
- **Non condividere la password principale** e non mandarla per mail o chat: nessuno di
  Rebase te la chiederà mai.
- **Per qualsiasi cosa**: scrivi a ciao@letsrebase.com.
