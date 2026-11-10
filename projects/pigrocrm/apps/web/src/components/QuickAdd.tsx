import { useNavigate, useRouterState } from '@tanstack/react-router'
import {
  CalendarPlus,
  Clock,
  FileInput,
  HandCoins,
  ListChecks,
  Receipt,
  StickyNote,
  type LucideIcon,
} from 'lucide-react'
import { useState } from 'react'
import { DayForm } from '@/features/days/DayForm'
import { NewNote } from '@/features/expense-notes/NewNote'
import { useExpenseSummary } from '@/features/expense-notes/queries'
import { QuickUpload } from '@/features/inbox/QuickUpload'
import { QuickExpense } from '@/features/purchases/QuickExpense'
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
import { daysInBackground, useModules } from '@/lib/modules'
import { QuickHours } from './QuickHours'
import { QuickNote } from './QuickNote'

type Action = 'task' | 'note' | 'day' | 'hours' | 'expense' | 'expense-note' | 'document'

/**
 * «+» of the phone's bottom bar (spec 2026-11-09 B6, REB-959): what can be added from
 * anywhere, without navigating. An activity and a note always; a worked day with `contracts`
 * on (last in a `company` space, which records days rarely, REB-1054); hours with `time` on; an
 * expense with `purchases` on; an expense note (REB-1046) with `staff` and `purchases` on, for a
 * user who may submit and is linked to a staff member. An entry of a module the space has off is
 * not drawn, the same question (`useModules().on`) the sidebar and the route guard ask.
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
  const navigate = useNavigate()
  const notesOn = modules.on('staff', 'purchases')
  const summary = useExpenseSummary(notesOn)
  const canSendNote = notesOn && summary.data?.can_submit === true && (summary.data.staff_member_id ?? null) !== null
  const { location } = useRouterState()
  const [adding, setAdding] = useState<{ action: Action; at: string } | null>(null)
  // A dialog does not outlive the page it was opened on: coming back to that path later
  // (back, then forward) must not find an empty form waiting.
  if (adding !== null && adding.at !== location.pathname) setAdding(null)
  const action = adding?.action ?? null
  const close = () => setAdding(null)

  const dayEntry = {
    action: 'day' as const,
    label: 'Giornata',
    hint: 'Un giorno lavorato su un contratto',
    icon: CalendarPlus,
  }
  const entries: { action: Action; label: string; hint: string; icon: LucideIcon }[] = [
    { action: 'task', label: 'Attività', hint: 'Una cosa da fare, con una scadenza', icon: ListChecks },
    {
      action: 'note',
      label: 'Nota',
      hint: modules.on('deals')
        ? 'Su un cliente, una persona o un deal'
        : 'Su un cliente o una persona',
      icon: StickyNote,
    },
    ...(modules.on('contracts') && !daysInBackground(modules) ? [dayEntry] : []),
    ...(modules.on('time')
      ? [{ action: 'hours' as const, label: 'Ore', hint: 'Ore lavorate su un deal', icon: Clock }]
      : []),
    // A receipt or an expense, registered at once from a sheet (the brief of 2026-11-10): the
    // purchases are a module, so the entry goes with it.
    ...(modules.on('purchases')
      ? [
          {
            action: 'expense' as const,
            label: 'Spesa',
            hint: 'Uno scontrino o una spesa, in due tocchi',
            icon: Receipt,
          },
        ]
      : []),
    // A note for the person's own expenses (the brief, «Note spese»): shown to a user linked to a
    // staff member who may write, with both modules on.
    ...(canSendNote
      ? [
          {
            action: 'expense-note' as const,
            label: 'Nota spese',
            hint: 'Una spesa o dei chilometri da farti rimborsare',
            icon: HandCoins,
          },
        ]
      : []),
    // The inbox is core, never behind a module: a photo or a PDF of a document goes in at once
    // and is read afterwards (REB-984).
    {
      action: 'document',
      label: 'Carica un documento',
      hint: 'Una foto o un file, in In arrivo',
      icon: FileInput,
    },
    // A company space records days rarely (REB-1054): the entry stays, after everything it does
    // more often, instead of being the third thing the sheet offers.
    ...(modules.on('contracts') && daysInBackground(modules) ? [dayEntry] : []),
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
      <QuickExpense open={action === 'expense'} onOpenChange={(next) => !next && close()} />
      <NewNote
        open={action === 'expense-note'}
        onOpenChange={(next) => !next && close()}
        onSent={() => void navigate({ to: '/app/expense-notes' })}
      />
      <QuickUpload open={action === 'document'} onOpenChange={(next) => !next && close()} />
    </>
  )
}
