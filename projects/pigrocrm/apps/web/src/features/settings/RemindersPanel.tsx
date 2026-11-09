import { ReminderPreferences } from '@/features/reminders/ReminderPreferences'
import { useReminderPreferences } from '@/features/reminders/preferencesQueries'
import { SettingsHistory } from './SettingsHistory'

/**
 * Promemoria (REB-735), the space's own: the defaults every person starts from, which each
 * of them may override in Notifiche. Admin only, like every section but the two personal
 * ones (`SETTINGS_TAB_MIN_ROLE`); the writes are `require_unscoped_admin` in the service.
 * A type switched off here is raised for nobody, Reminders included.
 */
export function RemindersPanel() {
  const types = useReminderPreferences().data?.types
  // A type is named in its history by the word the section above uses for it.
  const typeLabel = (key: string) =>
    key === 'general' ? 'Ore di silenzio e riepilogo' : types?.find((kind) => kind.key === key)?.label
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
      <SettingsHistory entityType="reminder_defaults" typeLabel={typeLabel} />
    </div>
  )
}
