import { ReminderPreferences } from '@/features/reminders/ReminderPreferences'

/**
 * Promemoria (REB-735), the space's own: the defaults every person starts from, which each
 * of them may override in Notifiche. Admin only, like every section but the two personal
 * ones (`SETTINGS_TAB_MIN_ROLE`); the writes are `require_unscoped_admin` in the service.
 * A type switched off here is raised for nobody, Avvisi included.
 */
export function RemindersPanel() {
  return (
    <div className="max-w-5xl space-y-6">
      <div>
        <h2 className="font-semibold">Promemoria</h2>
        <p className="text-sm text-muted-foreground">
          I valori di partenza dello spazio: ognuno può cambiarli per sé in Notifiche. Spegnere
          un tipo qui lo spegne per tutti.
        </p>
      </div>
      <ReminderPreferences scope="space" />
    </div>
  )
}
