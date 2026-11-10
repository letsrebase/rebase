import { FileText } from 'lucide-react'
import type { ReactNode } from 'react'
import { DownloadButton } from '@/components/DownloadButton'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { formatMoneyValue } from '@/features/time/columns'
import { StateCell } from './NoteCells'
import {
  OWN_NOTE,
  SOLE_ADMINISTRATOR,
  actionsFor,
  refusedAsOwn,
  formatDay,
  formatInstantDay,
  mileageLine,
  routeLine,
  wayLine,
} from './labels'
import { useApproveNote, type ExpenseNote } from './queries'

/** The sentence an administrator reads on a note that is theirs: refused to them, or approved by
 *  them because they are the space's only administrator (design § 3). Shown instead of an act
 *  that is not theirs, never only a disabled button. */
function OwnNoteSentence({ note, admin, sole }: { note: ExpenseNote; admin: boolean; sole: boolean }) {
  const sentence =
    admin && note.own && note.status === 'submitted' && sole
      ? SOLE_ADMINISTRATOR
      : refusedAsOwn(note, admin, sole)
        ? OWN_NOTE
        : null
  if (sentence === null) return null
  return (
    <p role="note" className="border bg-paper px-3 py-2 text-sm">
      {sentence}
    </p>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-6 border-b py-2 text-sm last:border-b-0">
      <dt className="shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-right font-medium">{children}</dd>
    </div>
  )
}

/** The receipt, the first thing of the panel and of the phone's card: a plain bordered box with
 *  its file name (the brief), and the file itself one press away when there is one. A mileage note
 *  has no receipt, so its box says the route and the kilometres, as typed. */
function Receipt({ note }: { note: ExpenseNote }) {
  if (note.kind === 'mileage') {
    return (
      <div aria-label="Percorso" className="space-y-1 border bg-background p-4 text-sm">
        <p className="font-medium">{routeLine(note) ?? 'Chilometri'}</p>
        <p className="text-muted-foreground tabular-nums">{mileageLine(note)}</p>
      </div>
    )
  }
  return (
    <div aria-label="Ricevuta" className="flex items-center gap-3 border bg-background p-4">
      <FileText className="size-5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1 space-y-1">
        <p className="break-words text-sm font-medium">{note.receipt_name ?? 'Nessuna ricevuta allegata'}</p>
        {note.inbox_item_id === null ? null : (
          <DownloadButton variant="outline" size="sm" path={`/api/inbox/items/${note.inbox_item_id}/file`}>
            Apri il file
          </DownloadButton>
        )}
      </div>
    </div>
  )
}

/** Who decided and when, once somebody did. The reason of a rejection is read in full here. */
function Decision({ note }: { note: ExpenseNote }) {
  if (note.decided_at === null) return null
  const verb = note.status === 'rejected' ? 'Rifiutata' : 'Approvata'
  return (
    <>
      <Row label={verb}>
        {note.decided_by_name === null ? '' : `${note.decided_by_name}, `}
        {formatInstantDay(note.decided_at)}
      </Row>
      {note.rejection_reason === null ? null : <Row label="Motivo">{note.rejection_reason}</Row>}
      {note.self_approved ? <Row label="Approvazione">Autoapprovata</Row> : null}
    </>
  )
}

/**
 * The open note: the receipt, the details, what was decided and how it is paid back. The full
 * description is here, where the table ellipsised it. The same body is the 330 px panel of the
 * desktop and the phone's full-screen card, so the same note looks the same on both sides; the
 * acts are `NoteActions`, drawn at the foot of one and as the sticky bar of the other.
 */
export function NoteBody({
  note,
  admin,
  sole,
  forStaffMember,
}: {
  note: ExpenseNote
  admin: boolean
  /** The caller is the space's only administrator (the summary's `only_administrator`). */
  sole: boolean
  forStaffMember: boolean
}) {
  const way = wayLine(note)
  return (
    <div className="space-y-4 p-4">
      <Receipt note={note} />
      <div className="space-y-2">
        <p className="whitespace-pre-line break-words font-medium">{note.description}</p>
        <StateCell note={note} forStaffMember={forStaffMember} />
      </div>
      <OwnNoteSentence note={note} admin={admin} sole={sole} />
      <dl className="border-t">
        {admin ? <Row label="Persona">{note.staff_member_name}</Row> : null}
        <Row label="Data">{formatDay(note.date)}</Row>
        <Row label="Categoria">{note.category_name}</Row>
        <Row label="Importo">
          <span className="tabular-nums">{formatMoneyValue(note.amount)}</span>
        </Row>
        {note.kind === 'mileage' ? <Row label="Chilometri">{mileageLine(note)}</Row> : null}
        <Decision note={note} />
        {way === null ? null : <Row label="Rimborso">{way}</Row>}
      </dl>
    </div>
  )
}

/**
 * «Rifiuta…» and «Approva», or «Rimborsa…» on an approved note: the acts of the one reading the
 * panel, and only the ones the server would take. «Approva» is the strong one and approving does
 * not choose the way of paying (brief, question 5). A refusal, the own-note sentence included, is
 * read in the server's own words above the buttons.
 */
export function NoteActions({
  note,
  admin,
  sole,
  onReject,
  onReimburse,
  onDecided,
}: {
  note: ExpenseNote
  admin: boolean
  sole: boolean
  onReject: () => void
  onReimburse: () => void
  onDecided: () => void
}) {
  const approve = useApproveNote()
  const actions = actionsFor(note, admin, sole)
  if (actions.length === 0) return null
  return (
    <div className="space-y-2">
      {approve.error === null ? null : <QueryErrorBanner error={approve.error} />}
      <div className="flex flex-wrap justify-end gap-2">
        {actions.includes('reject') ? (
          <Button className="flex-1 sm:flex-none" variant="outline" disabled={approve.isPending} onClick={onReject}>
            Rifiuta…
          </Button>
        ) : null}
        {actions.includes('approve') ? (
          <Button
            className="flex-1 sm:flex-none"
            disabled={approve.isPending}
            onClick={() =>
              approve.mutate(note.id, {
                onSuccess: () => {
                  toast.success('Nota approvata')
                  onDecided()
                },
              })
            }
          >
            {approve.isPending ? <Loader className="mr-2 size-4" /> : null}
            Approva
          </Button>
        ) : null}
        {actions.includes('reimburse') ? (
          <Button className="flex-1 sm:flex-none" onClick={onReimburse}>
            Rimborsa…
          </Button>
        ) : null}
      </div>
    </div>
  )
}
