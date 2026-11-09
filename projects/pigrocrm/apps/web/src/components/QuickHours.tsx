import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Label } from '@rebase/ui/label'
import { RecordPicker } from '@/features/tasks/RecordPicker'
import type { LinkValue } from '@/features/tasks/links'
import { TimeEntryForm } from '@/features/time/TimeEntryForm'
import { useEntitySchema } from '@/lib/schema'

/**
 * «Ore» of the phone's quick add (REB-959): hours on a deal, in two steps. The first asks
 * which deal; the second is the same form the deal's Ore tab opens, so what is asked, what
 * is priced and what is refused cannot differ between the two ways in.
 */
export function QuickHours({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [link, setLink] = useState<LinkValue | null>(null)
  const schema = useEntitySchema('time_entry')

  if (!open) return null
  if (link !== null) {
    return (
      <TimeEntryForm
        open
        onOpenChange={(next) => {
          if (!next) {
            setLink(null)
            onOpenChange(false)
          }
        }}
        dealId={link.id}
        customFields={schema.data?.custom_fields ?? []}
        title="Registra ore"
        onSaved={() => {
          setLink(null)
          onOpenChange(false)
        }}
      />
    )
  }
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Registra ore</DialogTitle>
          <DialogDescription>Su quale deal hai lavorato?</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="quick-hours-deal">Deal</Label>
          <RecordPicker
            id="quick-hours-deal"
            value={link}
            onChange={setLink}
            kinds={['deal']}
            placeholder="Cerca un deal…"
          />
        </div>
        <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
          Annulla
        </Button>
      </DialogContent>
    </Dialog>
  )
}
