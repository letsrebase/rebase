import { Timeline } from '@/components/Timeline'
import { SETTINGS_TIMELINE_ID } from '@/lib/query'
import type { TimelineEntityType } from '@/lib/schema'

/**
 * Who changed a space-wide setting and when (REB-828), under the section that holds it. Since
 * REB-781 the Slack connection and the reminder defaults record every change with the
 * member's name; this is where an administrator reads it. The same timeline component every
 * record page uses, so an entry reads the same here.
 */
export function SettingsHistory({
  entityType,
  typeLabel,
}: {
  entityType: Extract<TimelineEntityType, 'slack' | 'reminder_defaults'>
  typeLabel?: (key: string) => string | undefined
}) {
  const id = `storico-${entityType}`
  return (
    <section aria-labelledby={id} className="space-y-3 border-t pt-6">
      <div>
        <h3 id={id} className="font-semibold">
          Storico delle modifiche
        </h3>
        <p className="text-sm text-muted-foreground">Chi ha cambiato queste impostazioni e quando.</p>
      </div>
      <Timeline entityType={entityType} entityId={SETTINGS_TIMELINE_ID} typeLabel={typeLabel} />
    </section>
  )
}
