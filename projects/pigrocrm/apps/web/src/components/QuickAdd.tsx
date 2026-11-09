import { useRouterState } from '@tanstack/react-router'
import { CalendarPlus, Clock, ListChecks, StickyNote, type LucideIcon } from 'lucide-react'
import { useState } from 'react'
import { DayForm } from '@/features/days/DayForm'
import { TaskDialog } from '@/features/tasks/TaskDialog'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@rebase/ui/sheet'
import { useModules } from '@/lib/modules'
import { QuickHours } from './QuickHours'
import { QuickNote } from './QuickNote'

type Action = 'task' | 'note' | 'day' | 'hours'

/**
 * «+» of the phone's bottom bar (spec 2026-11-09 B6, REB-959): what can be added from
 * anywhere, without navigating. An activity and a note always; a worked day with `contracts`
 * on; hours with `time` on. An entry of a module the space has off is not drawn, the same
 * question (`useModules().on`) the sidebar and the route guard ask.
 *
 * The sheet only chooses. Each choice opens the dialog of the thing itself, the one the rest
 * of the app already uses, and the sheet closes behind it. The choice is remembered with the
 * path it was made on, so a link inside a form that leaves the page takes its dialog away.
 */
export function QuickAdd({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const modules = useModules()
  const { location } = useRouterState()
  const [adding, setAdding] = useState<{ action: Action; at: string } | null>(null)
  const action = adding !== null && adding.at === location.pathname ? adding.action : null
  const close = () => setAdding(null)

  const entries: { action: Action; label: string; hint: string; icon: LucideIcon }[] = [
    { action: 'task', label: 'Attività', hint: 'Una cosa da fare, con una scadenza', icon: ListChecks },
    { action: 'note', label: 'Nota', hint: 'Su un cliente, una persona o un deal', icon: StickyNote },
    ...(modules.on('contracts')
      ? [{ action: 'day' as const, label: 'Giornata', hint: 'Un giorno lavorato su un contratto', icon: CalendarPlus }]
      : []),
    ...(modules.on('time')
      ? [{ action: 'hours' as const, label: 'Ore', hint: 'Ore lavorate su un deal', icon: Clock }]
      : []),
  ]

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="bottom" className="pb-[env(safe-area-inset-bottom)]" closeLabel="Chiudi">
          <SheetHeader>
            <SheetTitle>Aggiungi</SheetTitle>
            <SheetDescription>Senza lasciare la pagina.</SheetDescription>
          </SheetHeader>
          <ul className="divide-y border-y">
            {entries.map(({ action: key, label, hint, icon: Icon }) => (
              <li key={key}>
                <button
                  type="button"
                  className="flex min-h-14 w-full items-center gap-4 px-4 py-2 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                  onClick={() => {
                    setAdding({ action: key, at: location.pathname })
                    onOpenChange(false)
                  }}
                >
                  <Icon className="size-5 shrink-0" aria-hidden="true" />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{label}</span>
                    <span className="block text-sm text-muted-foreground">{hint}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </SheetContent>
      </Sheet>

      <TaskDialog open={action === 'task'} onOpenChange={(next) => !next && close()} />
      <QuickNote open={action === 'note'} onOpenChange={(next) => !next && close()} />
      <Dialog open={action === 'day'} onOpenChange={(next) => !next && close()}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Registra una giornata</DialogTitle>
            <DialogDescription>
              Una giornata lavorata su un contratto: intera o mezza, con quello che hai fatto.
            </DialogDescription>
          </DialogHeader>
          {action === 'day' ? <DayForm onRecorded={close} /> : null}
        </DialogContent>
      </Dialog>
      <QuickHours open={action === 'hours'} onOpenChange={(next) => !next && close()} />
    </>
  )
}
