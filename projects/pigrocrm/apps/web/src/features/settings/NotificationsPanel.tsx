import { ReminderPreferences } from '@/features/reminders/ReminderPreferences'
import { SlackNotice } from '@/features/slack/SlackNotice'
import { DigestPreference } from './DigestPreference'
import { PushNotifications } from './PushNotifications'

/**
 * Notifiche (REB-735), a person's own: the weekly digest, push on this browser (REB-738),
 * and the reminder settings that override the space's defaults for them alone, each with
 * its way back to the space's value. Every role has it, like Profilo: the digest mail's
 * opt-out link lands here, and the writes touch the caller's own rows and nobody else's.
 */
export function NotificationsPanel() {
  return (
    <div className="max-w-5xl space-y-8">
      <div>
        <h2 className="font-semibold">Notifiche</h2>
        <p className="text-sm text-muted-foreground">
          Cosa ricevi e come, per te soltanto: dove non scegli tu valgono i valori dello spazio.
        </p>
      </div>

      <DigestPreference />

      <PushNotifications />

      <div className="space-y-6">
        <div>
          <h3 className="font-semibold">Promemoria</h3>
          <p className="text-sm text-muted-foreground">
            Gli avvisi dell’app restano sempre attivi. Qui scegli per ogni tipo se riceverlo,
            su quali altri canali e da quando è urgente.
          </p>
        </div>
        <SlackNotice />
        <ReminderPreferences scope="me" />
      </div>
    </div>
  )
}
