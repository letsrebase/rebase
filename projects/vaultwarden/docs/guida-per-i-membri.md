# Le password di Rebase: guida per i membri

Rebase tiene le credenziali condivise in un gestore di password che gira su un server
nostro: **Vaultwarden**, che parla con le app di Bitwarden. Tu usi le app di Bitwarden;
l'indirizzo del server è quello di Rebase. Ognuno vede solo le raccolte che gli sono state
assegnate, e solo con il permesso che gli è stato dato.

**Indirizzo del server: `https://vault.letsrebase.com`**

## 1. Installa l'app

Estensione per il browser (Chrome, Firefox, Edge, Safari), app per Android e iOS, app per
computer: tutte su [bitwarden.com/download](https://bitwarden.com/download). Sono le app
ufficiali di Bitwarden; il server a cui si collegano lo scegli tu al passo 2.

## 2. Accetta l'invito e punta l'app al nostro server

1. Ti arriva una mail da **vault@letsrebase.com**: «Unisciti all'organizzazione Rebase».
   Apri il link: si apre `vault.letsrebase.com`. Se non la trovi, guarda nello spam.
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
  te: il sito in cui la compili la riceve, e lì potresti vederla;
- **modifica** (con password visibili o nascoste): puoi anche cambiare le voci;
- **gestione**: puoi anche decidere chi altro entra nella raccolta.

Se una voce che ti aspetti non c'è, non è un errore: non è stata assegnata a te. Chiedi a
chi gestisce la raccolta.

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
