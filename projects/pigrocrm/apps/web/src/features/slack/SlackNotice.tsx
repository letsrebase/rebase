import { useSlackMine } from './queries'

/**
 * What Slack means for the person reading Notifiche (REB-739): whether the space has a
 * workspace and whether they are in it. It exists because the switch for Slack on a
 * reminder type changes nothing for somebody who cannot be reached there, and the page would
 * otherwise say nothing (spec 2026-10-26 §6.4: «a user with no match receives nothing on
 * Slack, and the settings say so»).
 *
 * Says nothing while loading, on a failure, and on an installation with no Slack app: a
 * sentence about a feature that is not there is noise, and one about a read that failed
 * would be a guess.
 */
export function SlackNotice() {
  const mine = useSlackMine()
  const data = mine.data
  if (!data || !data.configured) return null

  let message: string
  if (!data.connected) {
    message =
      'Slack non è collegato a questo spazio: l’interruttore Slack non ha effetto finché un ' +
      'amministratore non lo collega da Impostazioni → Slack.'
  } else if (data.matched) {
    message = `Slack: collegato a ${data.team_name}. I promemoria con Slack acceso ti arrivano in un messaggio diretto.`
  } else {
    message =
      `Slack: ${data.team_name} è collegato, ma nessuno ha il tuo indirizzo email, quindi su ` +
      'Slack non ricevi nulla. Chiedi a un amministratore di correggere l’abbinamento da ' +
      'Impostazioni → Slack.'
  }
  return (
    <p
      role="status"
      data-slack-state={!data.connected ? 'disconnected' : data.matched ? 'matched' : 'unmatched'}
      className="max-w-3xl text-sm text-muted-foreground"
    >
      {message}
    </p>
  )
}
