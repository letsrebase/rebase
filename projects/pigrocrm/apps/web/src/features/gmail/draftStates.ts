import { toProblem } from '@/lib/api'
import type { SendState } from './draftQueries'
import type { GmailHealth } from './queries'

/** The scope `EmailSendService.send` asks the connected mailbox for. */
export const GMAIL_SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'

/**
 * What the person is told, per state. A plain module rather than constants inside the
 * component: `react-refresh/only-export-components` refuses a component module that also
 * exports values, and `esito.ts` next door already established the split.
 *
 * The line that matters is `incerto`. It means **nobody knows** whether the message left,
 * and the word it must never use is «inviata»: writing "sent" for a state that means "we
 * do not know" is the exact lie this whole design exists to prevent (spec 6.3(b)). It is
 * not an error state either: nothing went wrong that anybody can point at, which is
 * precisely why it needs a name of its own.
 */
export const STATE_HEADING: Record<SendState, string> = {
  bozza: 'Bozza',
  in_invio: 'Invio in corso',
  inviato: 'Inviata',
  incerto: 'Esito da verificare',
  fallito: 'Invio non riuscito',
}

/**
 * The sentence under each draft. Each one says what the person can *do*, because a
 * state label alone leaves «esito da verificare» looking like a warning to be dismissed.
 *
 * `incerto` names «Verifica» and says, in as many words, not to send it again: the send
 * has no idempotency key, so a second press is a second email in a client's inbox.
 *
 * `fallito` does not say «correggila»: the Email tab reads and sends, it does not edit
 * (REB-415), so the way to a different text is a new draft from the assistant.
 */
export const STATE_HELP: Record<SendState, string> = {
  bozza: 'Non è ancora partita. Parte esattamente quello che leggi qui.',
  in_invio:
    'L’invio è in corso: questa pagina si aggiorna da sola. Se resta così per più di ' +
    'qualche minuto, usa «Verifica»: non rinviarla.',
  inviato: 'È partita. Trovi il messaggio nella corrispondenza qui sotto.',
  incerto:
    'Gmail non ha risposto, quindi non sappiamo se il messaggio sia partito. ' +
    'Usa «Verifica» per scoprirlo: non rinviarlo, perché potrebbe essere già arrivato.',
  fallito:
    'Non è partita e il testo è intatto. Puoi riprovare, oppure eliminarla e chiedere ' +
    'all’assistente una nuova bozza.',
}

/**
 * Whether the text promises an attachment that is not there.
 *
 * It matters more now than when the composer had it: `draft_email` never attaches
 * anything (which document to attach is a person's decision, `tools/gmail.py`), so an
 * assistant that writes «in allegato l'offerta» produces exactly this draft. A warning
 * and not a refusal: «in allegato alla mia precedente email» is a perfectly good
 * sentence, and blocking the send would be the interface overruling a person about
 * their own words.
 */
const PROMISES_AN_ATTACHMENT = /in allegat[oi]\b|allegat[oi] a questa/i

export function promisesAnAttachmentItDoesNotHave(args: {
  body: string
  attachmentCount: number
}): boolean {
  return args.attachmentCount === 0 && PROMISES_AN_ATTACHMENT.test(args.body)
}

/**
 * Why this draft cannot be sent as it stands, or `null`. An attachment with no name is a
 * version the send would refuse (`EmailDraftAttachment`), so pressing «Invia» could only
 * end in a refusal the tab gives no way to fix.
 */
export function draftCannotLeave(
  attachments: readonly { filename: string | null }[],
): string | null {
  return attachments.some((attachment) => attachment.filename === null)
    ? 'Un allegato non si può più inviare, quindi l’invio verrebbe rifiutato. ' +
        'Elimina la bozza e preparane una nuova.'
    : null
}

/**
 * Why this person's own mailbox cannot send right now, or `null` when it can -- or when
 * the answer is not known yet.
 *
 * The send leaves from the mailbox of whoever presses «Invia» (`GoogleAccountService.
 * usable`, which reads the actor's own account), so the question is about this person,
 * never about the space. The server remains the one that decides: this only saves the
 * person a confirmation that could only end in «nessuna casella Google collegata». An
 * unknown answer (the health read pending or failed) blocks nothing, for the same reason
 * `useGmailConfigured` hides nothing it does not know about.
 */
export function mailboxCannotSend(health: GmailHealth | undefined): string | null {
  if (health === undefined) return null
  const account = health.account
  if (account === null) {
    return 'Per inviare serve una casella Gmail collegata al tuo utente.'
  }
  if (account.status !== 'active') {
    return 'La tua casella Gmail non è attiva: ricollegala per inviare.'
  }
  if (health.missing_scopes.includes(GMAIL_SEND_SCOPE)) {
    return (
      'La tua casella Gmail è collegata senza il permesso di invio: ' +
      'ri-autorizzala per inviare.'
    )
  }
  return null
}

/**
 * The sentence an action's failure is shown with.
 *
 * A `Conflict`'s `detail` is `"<entity>: <reason>"`, written for a log
 * (`email_draft: questa email è già stata inviata`); its `reason` is the Italian half on
 * its own, which is what a person should read. Every other problem keeps what
 * `toProblem` made of it, the translated role refusal included.
 */
export function outcomeSentence(error: unknown): string {
  const problem = toProblem(error)
  if (problem.code === 'conflict' && typeof problem.reason === 'string' && problem.reason) {
    return problem.reason.charAt(0).toUpperCase() + problem.reason.slice(1)
  }
  return problem.detail
}

/**
 * Whether a failed send is one whose outcome nobody knows: no answer from the server at
 * all (a dropped connection, `code: 'unknown'`), or an answer from something in front of
 * it (a gateway's 502/504, `code: 'http_error'`, any 5xx). The request may have reached
 * the API and the API may have sent, so «Riprova» -- `toProblem`'s generic sentence --
 * is the one thing this banner must not say.
 */
export function sendOutcomeUnknown(error: unknown): boolean {
  const problem = toProblem(error)
  return problem.code === 'unknown' || problem.code === 'http_error' || problem.status >= 500
}

/** What the card says instead. True as written: the claim is committed before Gmail is
 *  called (`gmail/send.py`), so a draft that still reads «Bozza» once the list is read
 *  again never reached Gmail. */
export const SEND_OUTCOME_UNKNOWN =
  'Nessuna risposta dal server: non sappiamo se sia partita. Se la bozza qui sopra risulta ' +
  'ancora «Bozza», non è partita; altrimenti usa «Verifica».'

/**
 * Whether a failed send already changed the draft on the server. A refused or unanswered
 * send answers a `Conflict` carrying the draft's new `send_state` (`fallito`, `incerto`),
 * and so does a press on a draft that already left: in all of those the row's own state
 * and `last_error`, read again after the press, are the outcome, and a second banner
 * would say the same thing twice.
 */
export function failureMovedTheDraft(error: unknown): boolean {
  return typeof toProblem(error).send_state === 'string'
}
