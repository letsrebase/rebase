"""L'unico posto dove nasce un URL di Google Drive.

Spec 9C nomina il fallimento da evitare: leggere il Drive di una persona. Un Drive
personale contiene le foto dei figli, il contratto d'affitto, la cartella di un altro
lavoro. L'import parte invece sempre da una cartella che il titolare ha indicato
(`google_drive_accounts.root_folder_ids`), e "elenca i figli di questa cartella" e'
l'unica interrogazione di cui il sistema ha bisogno.

Quindi il vincolo e' applicato *server-side*, dentro il `q`, e questo modulo e' il solo
posto in cui un URL di Drive puo' essere costruito. Due guardie, perche' ognuna da sola
e' a una modifica di distanza dall'essere sconfitta:

  * `files_list_url` rifiuta un `q` privo di una clausola `'<id>' in parents` ben
    formata, rifiuta l'operatore di ricerca libera `contains`, e rifiuta `or`, `not` e
    le parentesi -- perche' la clausola `in parents` limita l'elenco solo se e' l'unica
    cosa che decide dove si guarda: `'<id>' in parents or mimeType='application/pdf'`
    la contiene e ciononostante elenca ogni PDF del Drive. Insieme rendono il bug non
    costruibile invece che soltanto vietato. I due controlli sugli operatori girano su
    uno scheletro con i literal fra apici svuotati, perche' `contains` dentro il nome
    di un cliente non e' un operatore;
  * `tests/test_drive_query.py` cammina l'AST di ogni file sorgente e fallisce se un
    literal contiene l'host di Drive, quello di upload o un path `/files`, cosi' che
    chi volesse bypassare questo modulo non abbia piu' un posto dove scrivere l'URL.
    La sua lista di deroghe e' vuota: `storage/gdrive.py`, che gli URL li scriveva a
    mano, e' stato migrato qui.

Tutto qui dentro e' una funzione pura su input validato. Nessuna funzione di questo
modulo legge il database, nessuna accetta testo libero, e in particolare non esiste --
deliberatamente -- un costruttore che prenda una stringa di ricerca di Drive: non c'e'
nessuna superficie di questa slice che ne offra una, e il modo piu' economico di
tenerlo vero e' non avere niente da chiamare.

**Due severita' per gli id, non una distrazione.** Un id che arriva da fuori -- la
cartella radice che il titolare ha incollato in `google_drive_accounts.root_folder_ids`,
o il file che qualcuno ha chiesto di leggere -- passa da `checked_outside_id`, che
pretende la forma di un id di Drive vero (10-128 caratteri dell'alfabeto opaco di
Google) e che riporta il *nome del parametro* che lo portava (`folder_id`, `file_id`),
perche' quel nome e' quasi tutto il contenuto del messaggio. Un id che Drive stesso ci
ha appena restituito -- il figlio di un elenco, la
cartella che `files.create` ha creato -- passa da `_checked_id`, che ha lo stesso
alfabeto senza minimo di lunghezza: qui la minaccia non e' un'espressione di ricerca
scelta da qualcuno, e' il path traversal verso un altro endpoint, e a quello basta
l'alfabeto. Pretendere dieci caratteri anche da questi significherebbe pretendere che
gli id di Drive abbiano una lunghezza minima *documentata*, che non hanno.

C'e' una terza sorgente di id, e passa anche lei dal controllo lasco: la cartella radice
dello storage (`Settings.gdrive_root_folder_id`, cioe'
`PIGROCRM_GDRIVE_ROOT_FOLDER_ID`), che `GDriveStorage` interpola come parent di ogni
cartella che crea e passa a `file_meta_url` in `verify_root_accessible`. Arriva da un
operatore, non da un elenco di Drive, quindi la severita' maggiore le si addirebbe --
ma e' un valore di configurazione letto una volta all'avvio, non un parametro di
richiesta, ed e' proprio `verify_root_accessible` a dire all'avvio, con un messaggio
comprensibile, che non e' raggiungibile. Alzarla adesso romperebbe le installazioni la
cui radice e' scritta in una forma che Drive accetta e questo regex no.
"""

import re
from urllib.parse import urlencode

from pigrocrm.core.errors import ValidationFailed

DRIVE_API_ROOT = "https://www.googleapis.com/drive/v3"
# Un upload non passa dall'endpoint normale: e' un host path diverso, con un
# `uploadType` obbligatorio. Sta qui e non in `storage/gdrive.py` per la guardia due.
DRIVE_UPLOAD_ROOT = "https://www.googleapis.com/upload/drive/v3"
_FILES = f"{DRIVE_API_ROOT}/files"
_UPLOAD_FILES = f"{DRIVE_UPLOAD_ROOT}/files"

FOLDER_MIME = "application/vnd.google-apps.folder"

# La forma di un id di Drive come arriva da fuori: gli id veri stanno fra 28 e 44
# caratteri, quindi un minimo di dieci non taglia via niente di legittimo e taglia via
# ogni parola che qualcuno abbia scritto pensando a qualcos'altro. `re.fullmatch`, mai
# `re.match` con `$` -- `$` accetta anche un newline finale, che e' esattamente il
# carattere con cui si tenta un'injection.
_SAFE_FOLDER_ID = re.compile(r"[A-Za-z0-9_\-]{10,128}")
# La stessa forma per gli id che Drive ci restituisce, senza minimo: vedi il docstring
# del modulo.
_SAFE_ID = re.compile(r"[A-Za-z0-9_\-]{1,128}")
# La chiave di un `appProperties`: una costante del codice, verificata perche' verificare
# costa una riga e rende impossibile che diventi un input per distrazione.
_SAFE_PROPERTY_KEY = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
# Un media type, per `files.export`. Ristretto perche' finisce in un parametro di query
# e perche' l'export accetta una lista chiusa di conversioni: qualunque cosa non abbia
# questa forma e' un errore di chi chiama, non una conversione esotica.
_SAFE_MIME = re.compile(r"[a-z]+/[A-Za-z0-9.+\-]+")
# Cosa rende legittimo un elenco: una clausola che nomina una cartella precisa. La
# forma dell'id e' load-bearing, non decorazione -- `in parents` come sottostringa
# comparirebbe anche in un `q` che non limita niente, ed e' quella la ricerca ampia che
# questo modulo esiste per impedire.
_SCOPED_TO_A_FOLDER = re.compile(r"'[A-Za-z0-9_\-]{1,128}' in parents")
# L'operatore di ricerca libera di Drive, in ogni sua forma (`name contains`,
# `fullText contains`). Rifiutato anche dentro una cartella: l'import elenca e decide
# dopo, e ammettere `contains` qui vorrebbe dire ammettere che una stringa di ricerca
# arrivi da fuori. Cercato sullo *scheletro* (vedi `_skeleton`), mai sul `q` grezzo:
# la parola dentro un valore fra apici non e' un operatore, e confonderla con uno
# significa far fallire ogni upload di un cliente che si chiama "Contains S.r.l.".
_FULL_TEXT_SEARCH = re.compile(r"\bcontains\b", re.IGNORECASE)
# I tre modi di *allargare* un filtro invece di restringerlo. `or` e `not` sono ovvi;
# le parentesi lo sono meno, ma servono a raggruppare un `or` e nessuna query che questo
# modulo costruisce ne ha bisogno, quindi vietarle costa niente e chiude la scappatoia
# di annidare l'`or`. Anche questi sullo scheletro: un nome di cartella puo'
# legittimamente contenere una `(` o la parola "or".
_WIDENS_THE_FILTER = re.compile(r"\bor\b|\bnot\b|[()]", re.IGNORECASE)
# Un literal fra apici, con l'escaping di `escape_query_value` gia' applicato: `\\.`
# copre sia `\'` sia `\\`, quindi la scansione non perde il conto delle virgolette
# proprio sul valore piu' ostile.
_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'")


def escape_query_value(value: str) -> str:
    """Fa l'escape di `\\` e `'` per un filtro `q` di Drive, il backslash per primo.

    L'ordine e' load-bearing per lo stesso motivo per cui lo e' negli escaper dei
    template: fare prima l'apice significherebbe poi vedere il proprio backslash
    raddoppiato dal secondo passaggio, lasciando un backslash finale in grado di
    mangiarsi l'apice di chiusura del filtro.

    Ogni valore che questo modulo mette dentro un filtro -- i nomi di cartella e il
    valore dell'`appProperties` -- e' oggi un segmento di una storage key validata da
    `validate_storage_key`, la cui classe di caratteri (`[a-z0-9._-]`) non contiene
    nessuno dei due, quindi questa funzione e' irraggiungibile attraverso qualunque
    chiamata il CRM faccia oggi. Resta comunque, per lo stesso motivo per cui `base.py`
    tiene i suoi controlli gia' ridondanti: un allargamento futuro di quella classe non
    deve riaprire in silenzio la query injection solo perche' oggi nessuno la esercita.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


# La regola di `checked_outside_id` scritta come `pattern` di JSON Schema, per l'unico
# tipo di chiamante che deve *pubblicarla* invece di limitarsi ad applicarla: i tool
# Drive di `apps/mcp` la dichiarano sui parametri `folder_id`/`file_id`, cosi' che
# un'espressione di ricerca passata da un agente venga rifiutata dallo schema dello
# strumento prima che un corpo giri, e il rifiuto nomini il parametro che ha scritto lui
# invece di risalire da tre livelli piu' sotto.
#
# Sta qui, accanto alla funzione, e non nel modulo che la pubblica: e' la stessa regola,
# e la ragione per cui `checked_outside_id` e' pubblica invece di essere riscritta da
# ogni chiamante vale identica per la sua grafia in JSON Schema. Derivata dal regex
# compilato, mai ribattuta a mano: due grafie di una regola sono una regola che
# divergera', e la divergenza e' silenziosa in entrambe le direzioni -- uno schema piu'
# lasco trasforma un rifiuto leggibile in un `ValidationFailed` dal profondo, uno piu'
# severo rifiuta id che il titolare ha configurato legittimamente.
#
# Gli anchor sono espliciti perche' `pattern` in JSON Schema e' una *ricerca*, non un
# full match: senza `^…$` accetterebbe `'x' in parents and 1RadiceClientiAAAA`, cioe'
# esattamente la stringa che esiste per rifiutare. `re.fullmatch` qui sotto e' cio' che
# rende equivalente il lato Python.
OUTSIDE_ID_PATTERN = f"^{_SAFE_FOLDER_ID.pattern}$"


def checked_outside_id(value: str, *, field: str) -> str:
    """La forma severa di un id che arriva **da fuori**, sotto il nome del parametro
    che lo portava.

    Un id esterno e' la cartella radice che il titolare ha incollato in
    `google_drive_accounts.root_folder_ids` *oppure* il file che qualcuno -- una rotta,
    un tool MCP -- ha chiesto di leggere: la severita' e' la stessa (vedi il docstring
    del modulo), il nome del parametro no. `field` esiste perche' quel nome e' quasi
    tutto il contenuto del messaggio: leggere «non e' un id di cartella Drive» dopo
    aver passato l'id di un *file* manda a controllare la configurazione delle radici,
    che e' giusta, invece del parametro che si e' scritto. Per lo stesso motivo la
    frase non nomina nessuno dei due tipi: e' la *forma* dell'id a essere sbagliata.

    Pubblica, e non un `_checked_folder_id` che ogni chiamante importa di straforo:
    `drive/reader.py` deve applicare esattamente questo controllo ai suoi `file_id`, e
    due regex per una sola regola sono una regola che prima o poi divergera'.
    """
    if not _SAFE_FOLDER_ID.fullmatch(value):
        raise ValidationFailed(
            "drive_query",
            field,
            "non è un id di Drive interpolabile in una query",
            expected="10-128 caratteri fra lettere, cifre, - e _",
        )
    return value


def _checked_id(file_id: str) -> str:
    if not _SAFE_ID.fullmatch(file_id):
        raise ValidationFailed(
            "drive_query", "file_id", "un id di Drive contiene solo lettere, cifre, - e _"
        )
    return file_id


def children_query(folder_id: str) -> str:
    """L'unica interrogazione che l'import ha bisogno di fare: i figli non cestinati di
    una cartella che il titolare ha indicato.

    Il cestino e' escluso qui e non a valle: un file cestinato e' un file che qualcuno
    ha deciso di buttare, e importarlo per poi filtrarlo dopo significa che e' passato
    comunque dal processo.
    """
    return f"'{checked_outside_id(folder_id, field='folder_id')}' in parents and trashed = false"


def folder_by_name_query(parent_id: str, name: str) -> str:
    """La cartella di nome `name` direttamente sotto `parent_id`.

    Serve al *placement* di `GDriveStorage` (una cartella per cliente, come il sistema
    che sostituisce), non all'identita': l'identita' vive in `appProperties`. Il nome e'
    un segmento di storage key generato dal CRM, non testo di un utente, ed e' escapato
    comunque -- vedi `escape_query_value`.
    """
    clauses = [
        "trashed=false",
        f"name='{escape_query_value(name)}'",
        f"'{_checked_id(parent_id)}' in parents",
        f"mimeType='{FOLDER_MIME}'",
    ]
    return " and ".join(clauses)


def _skeleton(q: str) -> str:
    """Il `q` con ogni literal fra apici svuotato, cioe' la sua sola struttura.

    E' su questo che si guarda per gli operatori, perche' un operatore dentro un valore
    non e' un operatore. La ragione sociale di un cliente diventa un segmento di storage
    key, quindi un nome di cartella, quindi il valore di un `name='...'`: cercare
    `contains` nel `q` grezzo vorrebbe dire far fallire ogni put e ogni get del cliente
    "Contains S.r.l." con un `ValidationFailed` su una query che il CRM aveva costruito
    lui. Svuotare invece di eliminare mantiene le virgolette al loro posto, cosi' che
    `name='' and '' in parents` resti leggibile come struttura.
    """
    return _QUOTED.sub("''", q)


def _with_params(base: str, **params: str) -> str:
    """Ogni chiamata dichiara di saper gestire uno Shared Drive.

    Non e' un dettaglio: la cartella radice di un service account *deve* stare su uno
    Shared Drive (un service account non ha quota propria), e la cartella indicata da un
    titolare puo' starci. Senza questo parametro Drive risponde 404 a entrambe.
    """
    return f"{base}?{urlencode({'supportsAllDrives': 'true', **params})}"


def files_list_url(q: str, *, page_token: str | None = None, fields: str) -> str:
    """Il solo modo di costruire un URL di `files.list` in questo codebase.

    Tre rifiuti, che insieme sono il meccanismo di spec 9C -- un elenco che esca dalla
    cartella indicata dal titolare, o una ricerca di testo nel suo Drive, non si possono
    costruire, quindi non si possono spedire per sbaglio:

    1. un `q` senza una clausola `'<id>' in parents` ben formata non e' limitato a
       nessuna cartella;
    2. un `q` che contenga `contains` cerca nel Drive di una persona;
    3. un `q` che contenga `or`, `not` o una parentesi *allarga* invece di restringere,
       e la sola presenza della clausola `in parents` non lo impedisce:
       `'<id>' in parents or mimeType='application/pdf'` la contiene e ciononostante
       elenca ogni PDF del Drive. Questo terzo controllo e' cio' che rende vera la
       frase "solo i figli di quella cartella"; senza di lui il docstring del modulo
       promette una cosa che il codice non prova.

    I punti 2 e 3 guardano lo *scheletro* (`_skeleton`), non il `q` grezzo: un valore
    fra apici non contiene operatori per definizione, e ispezionarlo significherebbe
    rifiutare la cartella di un cliente per come si chiama. Il punto 1 guarda il `q`
    vero, perche' e' proprio il literal `'<id>'` che deve esserci.

    L'unica deroga e' `files_by_app_property_url`, ed e' un'altra funzione con un altro
    nome invece di un parametro `strict=False`, cosi' che chi la legge veda nel nome
    cosa filtra.
    """
    structure = _skeleton(q)
    if _FULL_TEXT_SEARCH.search(structure):
        raise ValidationFailed(
            "drive_query",
            "q",
            "l'operatore contains cerca nel Drive di una persona: l'import elenca una "
            "cartella indicata e decide dopo",
            expected="un elenco dei figli di una cartella, senza contains",
        )
    if _WIDENS_THE_FILTER.search(structure):
        raise ValidationFailed(
            "drive_query",
            "q",
            "or, not e le parentesi allargano il filtro oltre la cartella indicata: un "
            "elenco può solo restringere",
            expected="clausole unite da and, senza or, not o parentesi",
        )
    if not _SCOPED_TO_A_FOLDER.search(q):
        raise ValidationFailed(
            "drive_query",
            "q",
            "un elenco non limitato a una cartella indicata dal titolare è un bug, "
            "non una ricerca ampia",
            expected="una clausola '<id cartella>' in parents",
        )
    params: dict[str, str] = {"q": q, "fields": fields, "includeItemsFromAllDrives": "true"}
    if page_token:
        params["pageToken"] = page_token
    return _with_params(_FILES, **params)


def files_by_app_property_url(key: str, value: str, *, fields: str) -> str:
    """L'unico elenco non limitato a una cartella, e lo e' perche' e' *piu'* specifico,
    non meno: nomina un valore che il CRM ha scritto lui stesso nell'`appProperties` di
    un file che ha caricato lui, non una ricerca del Drive di qualcuno. E' la stessa
    forma di deroga dell'`rfc822msgid` di Gmail.

    Non e' limitato a una cartella *per progetto*: e' cosi' che `GDriveStorage` trova un
    file senza camminare l'albero, che sarebbe ambiguo (vedi il docstring di
    `storage/gdrive.py`). Gira su ogni Shared Drive che la credenziale vede, il che e'
    sicuro perche' un'installazione PigroCRM ha una credenziale Drive sola e ogni file
    che scrive porta questa proprieta'.
    """
    if not _SAFE_PROPERTY_KEY.fullmatch(key):
        raise ValidationFailed(
            "drive_query",
            "key",
            "una chiave appProperties è una costante del codice, non un input",
            expected="1-64 caratteri fra lettere, cifre, ., - e _",
        )
    clauses = [
        "trashed=false",
        f"appProperties has {{ key='{key}' and value='{escape_query_value(value)}' }}",
    ]
    return _with_params(
        _FILES,
        q=" and ".join(clauses),
        fields=fields,
        includeItemsFromAllDrives="true",
        corpora="allDrives",
    )


def files_create_url(*, fields: str) -> str:
    """`files.create` senza corpo binario: e' cosi' che nasce una cartella."""
    return _with_params(_FILES, fields=fields)


def file_url(file_id: str) -> str:
    """L'URL di un singolo file, senza parametri oltre il supporto agli Shared Drive:
    quello che serve a un `DELETE`."""
    return _with_params(f"{_FILES}/{_checked_id(file_id)}")


def file_meta_url(file_id: str, *, fields: str) -> str:
    """I metadati di un file o di una cartella. `fields` e' obbligatorio perche' senza
    di esso Drive risponde con un sottoinsieme fisso che non contiene mai quello che chi
    chiama voleva -- `driveId`, per dirne uno, che e' l'intera domanda di
    `verify_root_accessible`."""
    return _with_params(
        f"{_FILES}/{_checked_id(file_id)}", fields=fields, includeItemsFromAllDrives="true"
    )


def file_media_url(file_id: str) -> str:
    """I byte di un file caricato, cosi' come sono."""
    return _with_params(f"{_FILES}/{_checked_id(file_id)}", alt="media")


def file_export_url(file_id: str, mime: str = "text/plain") -> str:
    """La conversione di un documento nativo di Google (Doc, Sheet) in un formato
    leggibile: un Google Doc non ha byte propri da scaricare, `alt=media` su di esso
    risponde 403.

    Il solo endpoint qui senza `supportsAllDrives`: non e' un suo parametro -- il client
    generato di Google non lo espone per `files.export` -- e passare all'API un parametro
    che non documenta e' un 400 in attesa di succedere. Un documento su uno Shared Drive
    si esporta comunque, perche' l'accesso e' per `fileId`.
    """
    if not _SAFE_MIME.fullmatch(mime):
        raise ValidationFailed(
            "drive_query", "mime", "non è un media type", expected="tipo/sottotipo"
        )
    return f"{_FILES}/{_checked_id(file_id)}/export?{urlencode({'mimeType': mime})}"


def upload_create_url(*, fields: str) -> str:
    """La creazione di un file con i suoi byte: metadati e contenuto in un corpo
    `multipart/related` solo, cosi' che un file non possa esistere senza le proprieta'
    che lo rendono ritrovabile."""
    return _with_params(_UPLOAD_FILES, uploadType="multipart", fields=fields)


def upload_media_url(file_id: str) -> str:
    """La sostituzione dei soli byte di un file che esiste già: il file resta nella
    cartella giusta, con il nome giusto e le proprieta' giuste, e cambia solo il
    contenuto."""
    return _with_params(f"{_UPLOAD_FILES}/{_checked_id(file_id)}", uploadType="media")
