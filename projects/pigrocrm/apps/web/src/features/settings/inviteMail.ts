/** What the Team panel says about the mail that carries an invitation (REB-678), kept out
 *  of `UsersPanel.tsx` so the component file exports components alone, and keyed on the
 *  API's own enum so a state the server learns to say makes these maps fail to compile
 *  instead of rendering as nothing. */
import type { StatusTone } from '@/components/StatusPill'
import type { components } from '@/lib/api-types'

export type InviteMailState = NonNullable<components['schemas']['InvitationRead']['mail_status']>

export const MAIL_LABEL: Record<InviteMailState, string> = {
  sent: 'Inviata',
  delayed: 'In ritardo',
  delivered: 'Consegnata',
  bounced: 'Non consegnata',
  suppressed: 'Non consegnata',
  complained: 'Segnalata come spam',
  failed: 'Non partita',
}

export const MAIL_TONE: Record<InviteMailState, StatusTone> = {
  sent: 'muted',
  delayed: 'gold',
  delivered: 'ink',
  bounced: 'danger',
  suppressed: 'danger',
  complained: 'danger',
  failed: 'danger',
}

/** What the admin does about it, for the states that ask for something. A resend to an
 *  address that bounced is refused by the mail service (it stops writing to an address
 *  that already bounced), so for those the way out is the address, not the button. */
export const MAIL_HINT: Partial<Record<InviteMailState, string>> = {
  delayed: 'Il servizio di posta sta ancora provando a consegnarla.',
  bounced:
    "L'indirizzo ha rifiutato la mail. Se è scritto male, revoca l'invito e invita di nuovo con quello giusto.",
  suppressed:
    "Una mail precedente a questo indirizzo è rimbalzata e il servizio di posta non ci scrive più. Se è scritto male, revoca l'invito e invita di nuovo con quello giusto.",
  complained:
    'Chi l’ha ricevuta l’ha segnalata come spam. Controlla con la persona che sia l’indirizzo giusto.',
  failed: 'La mail non è partita. Usa «Reinvia il link» per riprovare.',
}

/** A mail still on its way, and recent enough that the answer is worth waiting for: the
 *  list asks again while any row is, and stops once every one has ended or gone stale. */
const OPEN: readonly InviteMailState[] = ['sent', 'delayed']
const WATCH_MS = 2 * 60 * 1000

export function isWatched(
  invite: { mail_status?: InviteMailState | null; mail_sent_at?: string | null },
  now: number,
): boolean {
  return (
    invite.mail_status != null &&
    OPEN.includes(invite.mail_status) &&
    invite.mail_sent_at != null &&
    now - new Date(invite.mail_sent_at).getTime() < WATCH_MS
  )
}
