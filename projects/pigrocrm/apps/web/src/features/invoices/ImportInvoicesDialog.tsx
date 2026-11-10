import { FileUp } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Loader } from '@rebase/ui/loader'
import { toast } from '@rebase/ui/sonner'
import { UploadDropzone } from '@/features/documents/UploadDropzone'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { ResultPanel, ReviewPanel } from './ImportReview'
import {
  batchFiles,
  countConfirmed,
  countWrites,
  organizationsOf,
  decisionsFor,
  plural,
  reviewedAsConfirmed,
  willBeWritten,
  type ChosenOrganization,
  type ConfirmRow,
  type ReviewRow,
} from './importPlan'
import { confirmImportFiles, reviewImportFiles, useInvalidateAfterImport } from './importQueries'

/** What the file picker offers: FatturaPA XML and ZIPs of it. Only a hint to the picker,
 *  like every `accept` here: the server reads the bytes and decides. */
const ACCEPT = '.xml,.zip,application/xml,text/xml,application/zip'

/** One request's files and what the review said about them. The confirmation sends the
 *  same files again, batch by batch, and only the batches that hold something to write. */
interface ReviewedBatch {
  files: File[]
  rows: ReviewRow[]
}

type Stage =
  | { kind: 'choose' }
  | { kind: 'reviewing'; done: number; total: number }
  | { kind: 'review' }
  | { kind: 'confirming'; done: number; total: number }
  | { kind: 'done' }

/**
 * The invoices page's import (REB-758): choose files, read what they would create, confirm.
 *
 * Three decisions shape it. The review writes nothing, and says so before the first
 * file is chosen, because a person importing a year of invoices is afraid of exactly one
 * thing. A counterparty the register has never seen is a checkbox per *customer*, ticked,
 * not one per file: a year to one new customer is one choice. And the files go to the
 * server in batches, one after the other, review and then write, so a year of XML shows
 * how far along it is instead of one spinner; the server stores nothing between the two
 * passes and re-reads every file on the second one (`importQueries.ts`).
 */
function ImportInvoicesDialog({ onClose }: { onClose: () => void }) {
  const invalidate = useInvalidateAfterImport()
  const legal_entity = useLegalEntity()
  const [stage, setStage] = useState<Stage>({ kind: 'choose' })
  const [batches, setBatches] = useState<ReviewedBatch[]>([])
  // The new organizations the person unticked, by `partyKey`: unticked is the exception.
  const [declined, setDeclined] = useState<ReadonlySet<string>>(new Set())
  // The existing customer picked by hand for a new party, by `partyKey`: absent is «create it».
  const [chosen, setChosen] = useState<ReadonlyMap<string, ChosenOrganization>>(new Map())
  const [confirmed, setConfirmed] = useState<ConfirmRow[]>([])
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  // A request in flight when the dialog closes is not abandoned, but the loops below
  // stop starting new ones: closing mid-import must not keep writing invoices behind a
  // screen that is gone.
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const rows = batches.flatMap((batch) => batch.rows)

  async function review(files: File[]) {
    setProblem(null)
    const reviewed: ReviewedBatch[] = []
    let done = 0
    setStage({ kind: 'reviewing', done, total: files.length })
    try {
      for (const batch of batchFiles(files)) {
        if (!mounted.current) return
        reviewed.push({ files: batch, rows: await reviewImportFiles(batch) })
        done += batch.length
        setStage({ kind: 'reviewing', done, total: files.length })
      }
    } catch (error) {
      setProblem(toProblem(error))
      setStage({ kind: 'choose' })
      return
    }
    setBatches(reviewed)
    setDeclined(new Set())
    setChosen(new Map())
    setStage({ kind: 'review' })
  }

  async function confirm() {
    setProblem(null)
    const toWrite = batches.filter((batch) =>
      batch.rows.some((row) => willBeWritten(row, declined)),
    )
    // The batches not sent keep their review in the result: their conflicts and unreadable
    // files are still files the person has to look at.
    const collected: ConfirmRow[] = batches
      .filter((batch) => !toWrite.includes(batch))
      .flatMap((batch) => reviewedAsConfirmed(batch.rows))
    // The customer each party's invoices went to so far: a batch after the first attaches
    // a party an earlier one created by id, instead of asking for it to be created again.
    const created = new Map<string, string>()
    const total = toWrite.reduce((sum, batch) => sum + batch.files.length, 0)
    let done = 0
    setStage({ kind: 'confirming', done, total })
    try {
      for (const batch of toWrite) {
        if (!mounted.current) break
        const result = await confirmImportFiles(batch.files, decisionsFor(batch.rows, declined, created, chosen))
        collected.push(...result)
        for (const [party, customerId] of organizationsOf(result)) created.set(party, customerId)
        done += batch.files.length
        setStage({ kind: 'confirming', done, total })
      }
    } catch (error) {
      // What was written before the failure stays written, so the result is shown as
      // far as it got, with the refusal above it, instead of going back to a review
      // that is now out of date.
      setProblem(toProblem(error))
    }
    setConfirmed(collected)
    setStage({ kind: 'done' })
    // Whatever happened, the register may have changed: the lists are stale either way.
    invalidate()
    const imported = countConfirmed(collected).imported ?? 0
    if (imported > 0) toast.success(plural(imported, 'fattura importata', 'fatture importate'))
  }

  function chooseAgain() {
    setBatches([])
    setProblem(null)
    setStage({ kind: 'choose' })
  }

  const busy = stage.kind === 'reviewing' || stage.kind === 'confirming'

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        // Not while a request is out: the close would look like a cancel and is not one.
        if (!next && !busy) onClose()
      }}
    >
      {/* `grid-cols-[minmax(0,1fr)]`: the dialog is a one-column grid whose column would
          otherwise grow to the review table's own width and push the footer's buttons
          out of the box; the table scrolls inside its own container instead. */}
      <DialogContent className="max-h-[85vh] grid-cols-[minmax(0,1fr)] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>Importa fatture</DialogTitle>
          <DialogDescription>
            Carica i file XML FatturaPA emessi con il sistema precedente, o un ZIP che li contiene.
            Prima vedi cosa verrebbe creato: il registro cambia solo quando confermi.
          </DialogDescription>
        </DialogHeader>

        {problem ? <QueryErrorBanner error={problem} /> : null}

        {stage.kind === 'choose' ? (
          <UploadDropzone
            multiple
            accept={ACCEPT}
            inputLabel="Carica i file delle fatture"
            onFiles={(files) => void review(files)}
          />
        ) : null}

        {stage.kind === 'reviewing' ? (
          <Progress
            label={`Lettura dei file: ${stage.done} di ${stage.total}`}
            done={stage.done}
            total={stage.total}
          />
        ) : null}

        {stage.kind === 'review' ? (
          <ReviewPanel
            rows={rows}
            declined={declined}
            chosen={chosen}
            onChoose={(key, customer) =>
              setChosen((previous) => {
                const next = new Map(previous)
                if (customer === undefined) next.delete(key)
                else next.set(key, customer)
                return next
              })
            }
            onToggleParty={(key) =>
              setDeclined((previous) => {
                const next = new Set(previous)
                if (next.has(key)) next.delete(key)
                else next.add(key)
                return next
              })
            }
          />
        ) : null}

        {stage.kind === 'confirming' ? (
          <Progress
            label={`Importazione: ${stage.done} file di ${stage.total}`}
            done={stage.done}
            total={stage.total}
          />
        ) : null}

        {stage.kind === 'done' ? (
          <ResultPanel
            rows={confirmed}
            interrupted={problem !== null}
            legalEntityName={(id) => (legal_entity.several ? legal_entity.byId(id)?.legal_name : undefined)}
          />
        ) : null}

        <DialogFooter>
          {stage.kind === 'review' ? (
            <>
              <Button variant="ghost" onClick={chooseAgain}>
                Scegli altri file
              </Button>
              <Button onClick={() => void confirm()} disabled={countWrites(rows, declined) === 0}>
                {countWrites(rows, declined) === 0
                  ? 'Niente da importare'
                  : `Importa ${plural(countWrites(rows, declined), 'fattura', 'fatture')}`}
              </Button>
            </>
          ) : stage.kind === 'done' ? (
            <Button onClick={onClose}>Chiudi</Button>
          ) : (
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Annulla
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Progress({ label, done, total }: { label: string; done: number; total: number }) {
  return (
    <div role="status" className="flex flex-col gap-3 border p-6">
      <p className="flex items-center gap-2 text-sm">
        <Loader />
        {label}
      </p>
      <div
        aria-hidden
        className="h-1 w-full bg-muted"
      >
        <div
          className="h-full bg-foreground transition-[width]"
          style={{ width: `${total === 0 ? 0 : Math.round((done / total) * 100)}%` }}
        />
      </div>
    </div>
  )
}

/**
 * The entry point on the Invoices page, next to «Nuova fattura». Mounted only while open,
 * like `NewProformaButton`'s dialog. Asks for the admin action the service asks for: the
 * review and the write are both admin-only, and a collaborator who pressed it would get
 * a 403 for a screen they were offered.
 */
export function ImportInvoicesButton() {
  const [open, setOpen] = useState(false)
  const mayImport = useCan('confirm_invoice_import')
  if (!mayImport) return null
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <FileUp className="mr-2 size-4" />
        Importa
      </Button>
      {open ? <ImportInvoicesDialog onClose={() => setOpen(false)} /> : null}
    </>
  )
}
