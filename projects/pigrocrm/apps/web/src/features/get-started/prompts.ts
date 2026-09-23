/**
 * The prompts a person copies into the assistant they just connected (ORB-182): one
 * for the first conversation, one per first step. Written the way a person talks to
 * their assistant, with every value to fill in marked as «…», and naming only what the
 * MCP can do: since ORB-188 that includes the fiscal profile and the emitter
 * (`update_fiscal_profile`, `update_emitter_profile`, admin-only), so the fiscal step
 * is one imperative voice, «imposta», like the others. Every prompt asks for a summary
 * and the person's ok before anything is written.
 */
import type { StepId } from './firstSteps'

export const INTRO_PROMPT =
  'Ciao! Sei collegato al mio spazio PigroCRM. Presentati in due righe, dimmi cosa vedi nello spazio (clienti, deal, ore, documenti, il profilo fiscale) e cosa puoi fare per me. Poi proponimi i primi tre passi per partire, nell’ordine giusto, e aspetta il mio ok prima di fare qualsiasi modifica.'

export const STEP_PROMPTS: Record<StepId, string> = {
  fiscali:
    'Imposta su PigroCRM i miei dati fiscali, sia l’emittente sia il profilo fiscale. Emittente: ragione sociale «…», partita IVA «…», codice fiscale «…», indirizzo «via …, CAP, città (provincia)», PEC «…», codice SDI «…», email «…», telefono «…». Profilo fiscale: regime «forfettario» (codice «RF19»), coefficiente di redditività «78» %, imposta sostitutiva «5» %, contributi INPS «26,07» %, pagamento con «bonifico» a «30» giorni, IBAN «IT…». Prima di salvare mostrami il riepilogo completo di entrambi e chiedimi conferma. Se un dato manca, lascialo vuoto e dimmelo: non inventare niente.',
  cliente:
    'Crea su PigroCRM un nuovo cliente: «Ragione sociale S.r.l.», partita IVA «…», indirizzo «via …, CAP, città (provincia)», email «…», PEC «…», codice SDI «…». Aggiungi come referente «Nome Cognome», ruolo «…», email «…», telefono «…». Se un campo non lo sai, lascialo vuoto e dimmelo: non inventare niente.',
  lavoro:
    'Sul cliente «…» crea un deal chiamato «…», valore previsto «… €», nello stato «Offerta», con chiusura prevista il «gg/mm/aaaa». Poi registra le ore che ho già lavorato su quel deal: «gg/mm/aaaa», «2» ore, «cosa ho fatto». Alla fine dimmi il totale delle ore e il valore della pipeline aperta.',
  documento:
    'Prepara un’offerta per il deal «…» del cliente «…» usando il template «Offerta». Oggetto: «…». Ambito e obiettivi: «…». Attività: «…». Condizioni economiche: «…». Fatturazione e pagamento: «…». Mostrami l’anteprima del testo e aspetta il mio ok prima di generare il PDF.',
}

/**
 * The invoice door's handoff (spec 2026-09-16 §6, REB-224): the PDF is on file as a
 * `fattura` document of the customer the person chose first, and the assistant reads it
 * (`read_document_text`) and registers it (`import_issued_invoice`). What the spec's
 * wording did not carry, each because the import needs it or would go wrong without it:
 *
 * - the customer's id, since the person already chose it and the import takes it;
 * - the document as `pdf_sorgente.document_id`, which is what makes the door say
 *   «Registrata»;
 * - the fiscal profile, because the import reads the regime and refuses without one, and
 *   a new space has none (`update_fiscal_profile`, admin, on the default surface);
 * - what the PDF does not say (whether it was paid, whether it went to the SdI), asked
 *   rather than left to the import's defaults;
 * - the earlier numbers of the year: importing the last invoice leaves 1 to n-1 as
 *   undeclared gaps, and PigroCRM refuses to issue that year until they are imported or
 *   declared. They are real invoices, so the assistant says so instead of declaring them;
 * - the summary before the write, the rule every prompt here keeps, which matters most
 *   for an import that cannot be undone.
 */
export function invoicePrompt({
  documentId,
  customerId,
  customerName,
}: {
  documentId: string
  customerId: string
  customerName: string
}): string {
  return (
    `Leggi su PigroCRM il documento ${documentId}: è una fattura che ho emesso al cliente «${customerName}» (customer_id ${customerId}). ` +
    'Se il mio profilo emittente è vuoto, compilalo con i miei dati che leggi lì; se manca il profilo fiscale, chiedimi regime e parametri e impostalo. ' +
    'Chiedimi anche se è già stata incassata (e quando) e se l’ho già trasmessa allo SdI: sul PDF non c’è scritto. ' +
    'Poi prepara l’import con import_issued_invoice: il suo numero e la sua data, le righe e i totali come sono stampati, ' +
    `quel cliente, e questo documento come PDF originale (pdf_sorgente.document_id ${documentId}). ` +
    'Prima di scrivere qualsiasi cosa mostrami il riepilogo e aspetta il mio ok, perché l’import non si disfa; se un dato non si legge, dimmelo e non inventare niente. ' +
    'Alla fine dimmi cosa hai registrato e quali numeri precedenti dello stesso anno mancano ancora nel registro: sono fatture vere da importare, non buchi da dichiarare.'
  )
}
