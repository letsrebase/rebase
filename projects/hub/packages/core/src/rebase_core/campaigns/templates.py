"""The mail each journey state starts from: the second outreach wave's (REB-425, 25/09),
which the admin edits per campaign. Only the starting point lives here; a campaign keeps
its own copy."""

from dataclasses import dataclass

from rebase_core.campaigns.states import JOURNEY_STATES


@dataclass(frozen=True)
class Template:
    stato_percorso: str
    etichetta: str
    oggetto: str
    testo: str
    bottone_testo: str
    bottone_meta: str
    azione: str


def _t(key: str, oggetto: str, testo: str, bottone: str, meta: str, azione: str) -> Template:
    return Template(key, JOURNEY_STATES[key], oggetto, testo, bottone, meta, azione)


STATE_TEMPLATES: dict[str, Template] = {
    t.stato_percorso: t
    for t in (
        _t(
            "lead",
            "Chiudo la tua iscrizione a rebase?",
            "Ciao {nome},\n\nhai lasciato la tua email su letsrebase.com per entrare in rebase, "
            "e il profilo non c'è ancora. Te lo chiedo direttamente: ti interessa ancora?\n\n"
            "Se sì, bastano cinque minuti, dal bottone qui sotto.\n\n"
            "Se no, rispondi «no» e non ti scrivo più.",
            "Compila il profilo",
            "wizard",
            "profilo_creato",
        ),
        _t(
            "scheda_vuota_nuovi",
            "La tua scheda su rebase, la compilo io?",
            "Ciao {nome},\n\nla scheda che ti abbiamo aperto su rebase è ancora senza tariffa, "
            "modalità di lavoro e CV, e così non posso proporti a nessuna azienda.\n\n"
            "Facciamo prima così: rispondi a questa mail con il CV in allegato e quanto chiedi "
            "a giornata. Al resto penso io. Se preferisci farlo da te, entri con la sola email "
            "dal bottone qui sotto.\n\n"
            "Se invece non vuoi comparire, rispondi «no» e cancello la scheda.",
            "Completa la scheda",
            "area",
            "scheda_completa",
        ),
        _t(
            "scheda_vuota_entrati",
            "La tua scheda su rebase è rimasta vuota",
            "Ciao {nome},\n\nho visto che hai aperto la tua area su rebase, ma la scheda è "
            "rimasta com'era: senza tariffa, modalità di lavoro e CV. Qualcosa non ha "
            "funzionato, o non era chiaro? Dimmelo in una riga rispondendo a questa mail.\n\n"
            "Se vuoi riprovare, entri con la sola email dal bottone qui sotto. Oppure rispondi "
            "con il CV e quanto chiedi a giornata, e la compilo io.",
            "Completa la scheda",
            "area",
            "scheda_completa",
        ),
        _t(
            "manca_cv",
            "Manca solo il CV",
            "Ciao {nome},\n\nil tuo profilo su rebase è quasi pronto: manca solo il CV. È la "
            "prima cosa che un'azienda ci chiede quando le proponiamo una persona, e senza non "
            "posso proporti.\n\nLo carichi dalla tua area, entri con la sola email dal bottone "
            "qui sotto. Va bene anche il PDF esportato da LinkedIn, oppure rispondi a questa "
            "mail con il CV in allegato e lo carico io.",
            "Carica il CV",
            "area",
            "cv",
        ),
        _t(
            "completo",
            "Il tuo profilo su rebase è completo",
            "Ciao {nome},\n\nil tuo profilo su rebase è completo, grazie. Quando arriva un "
            "progetto adatto a te, ti scrivo io.\n\nIntanto una domanda, rispondi anche in una "
            "riga: da quando hai spazio per un nuovo progetto, e per quanti giorni a settimana?",
            "Entra nella tua area",
            "area",
            "entrato",
        ),
        _t(
            "azienda_aperta",
            "La tua richiesta su rebase è ancora aperta?",
            "Ciao {nome},\n\nti riscrivo per la tua richiesta su rebase. È ancora aperta?\n\n"
            "Se è cambiato qualcosa, quante persone, da quando, con che budget, la aggiorni "
            "dalla tua area con il bottone qui sotto. Se è ancora così, mi bastano dieci minuti "
            "al telefono: rispondi con un numero e un momento comodo e ti chiamo io.",
            "Aggiorna la richiesta",
            "richiesta",
            "richiesta_aggiornata",
        ),
    )
}
