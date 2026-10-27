import { FileUp } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { useCustomers } from '@/features/customers/queries'
import { UploadDropzone } from '@/features/documents/UploadDropzone'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { EMPTY, formatDate, formatMoney } from './format'
import {
  batchFiles,
  CONFIRM_OUTCOME_LABELS,
  countByOutcome,
  countConfirmed,
  countWrites,
  customersOf,
  decisionsFor,
  newParties,
  REVIEW_OUTCOME_LABELS,
  REVIEW_OUTCOME_NOTES,
  REVIEW_OUTCOME_TONES,
  registerYears,
  reviewedAsConfirmed,
  sortReviewRows,
  willBeWritten,
  type ChosenCustomer,
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

const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  fattura: 'Fattura',
  acconto_fattura: 'Acconto su fattura',
  acconto_parcella: 'Acconto su parcella',
  nota_credito: 'Nota di credito',
  nota_debito: 'Nota di debito',
  parcella: 'Parcella',
}

function plural(count: number, one: string, many: string): string {
  return count === 1 ? `1 ${one}` : `${count} ${many}`
}

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
  const azienda = useLegalEntity()
  const [stage, setStage] = useState<Stage>({ kind: 'choose' })
  const [batches, setBatches] = useState<ReviewedBatch[]>([])
  // The new customers the person unticked, by `partyKey`: unticked is the exception.
  const [declined, setDeclined] = useState<ReadonlySet<string>>(new Set())
  // The existing customer picked by hand for a new party, by `partyKey`: absent is «create it».
  const [chosen, setChosen] = useState<ReadonlyMap<string, ChosenCustomer>>(new Map())
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
        for (const [party, customerId] of customersOf(result)) created.set(party, customerId)
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
            aziendaName={(id) => (azienda.several ? azienda.byId(id)?.ragione_sociale : undefined)}
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

function ReviewPanel({
  rows,
  declined,
  chosen,
  onChoose,
  onToggleParty,
}: {
  rows: ReviewRow[]
  declined: ReadonlySet<string>
  chosen: ReadonlyMap<string, ChosenCustomer>
  onChoose: (key: string, customer: ChosenCustomer | undefined) => void
  onToggleParty: (key: string) => void
}) {
  const counts = countByOutcome(rows)
  const parties = newParties(rows)
  const sorted = sortReviewRows(rows)
  return (
    <div className="space-y-4">
      <ul aria-label="Esito della lettura" className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        {(Object.keys(REVIEW_OUTCOME_LABELS) as (keyof typeof REVIEW_OUTCOME_LABELS)[])
          .filter((outcome) => counts[outcome])
          .map((outcome) => (
            <li key={outcome}>
              <span className="text-muted-foreground">{REVIEW_OUTCOME_LABELS[outcome]}</span>{' '}
              <span className="font-medium">{counts[outcome]}</span>
            </li>
          ))}
      </ul>

      {parties.length > 0 ? (
        <section aria-labelledby="import-new-customers" className="space-y-2 border p-3">
          <h3 id="import-new-customers" className="font-medium">
            Clienti da creare
          </h3>
          <p className="text-sm text-muted-foreground">
            Questi clienti non sono in anagrafica. Lasciali spuntati per crearli insieme alle
            loro fatture, oppure scegli un cliente che hai già: le fatture vanno a lui e non ne
            nasce un altro. Se ne togli uno, le sue fatture non vengono importate.
          </p>
          {parties.map((party) => (
            <div key={party.key} className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={!declined.has(party.key)}
                  onCheckedChange={() => onToggleParty(party.key)}
                />
                <span className="font-medium">{party.name}</span>
                <span className="text-muted-foreground">
                  {party.taxId !== '' ? `${party.taxId} · ` : ''}
                  {plural(party.invoices, 'fattura', 'fatture')}
                </span>
              </label>
              <ExistingCustomerPicker
                party={party.taxId !== '' ? `${party.name} (${party.taxId})` : party.name}
                aziendaId={party.aziendaId}
                mixedAziende={party.mixedAziende}
                value={chosen.get(party.key)}
                disabled={declined.has(party.key) || party.mixedAziende}
                onChange={(customer) => onChoose(party.key, customer)}
              />
            </div>
          ))}
        </section>
      ) : null}

      <div className="max-h-[40vh] overflow-y-auto border">
        <Table scrollLabel="Fatture del file">
          <TableHeader className="sticky top-0 bg-popover">
            <TableRow>
              <TableHead>Fattura</TableHead>
              <TableHead>Cliente</TableHead>
              <TableHead className="text-right">Totale</TableHead>
              <TableHead>File</TableHead>
              <TableHead>Esito</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((row, index) => (
              <ReviewTableRow
                key={`${row.sha256}-${index}`}
                row={row}
                declined={declined}
                chosen={chosen}
              />
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

/** The value of the picker's «create it» entry: a Radix item cannot carry an empty value. */
const CREATE_NEW = '__crea__'

/**
 * Attach a new party to a customer already on file (REB-777). `match_customer` finds one by
 * an Italian VAT number or tax code, so a customer without one (a foreign customer, a
 * private person with no tax code stated) is offered for creation on every import; this is
 * the way to say it is somebody already known. `useCustomers({ limit: 200 })` is the
 * picker every dialog here uses (`NewProformaDialog`): a one-shot cap for a dropdown, one
 * request shared by every party through the query cache. A list that cannot be read leaves
 * only «crea un nuovo cliente», which is the default and the old behaviour.
 */
function ExistingCustomerPicker({
  party,
  aziendaId,
  mixedAziende,
  value,
  disabled,
  onChange,
}: {
  party: string
  aziendaId: string | null
  mixedAziende: boolean
  value: ChosenCustomer | undefined
  disabled: boolean
  onChange: (customer: ChosenCustomer | undefined) => void
}) {
  const customers = useCustomers({ limit: 200 })
  // Only the customers of the azienda the invoice is written on: the server takes any
  // `customer_id` it is sent, and an invoice on one azienda's register for another's
  // customer is a state nothing else in the product can produce. With the sidebar on
  // another azienda the list is that one's, and a party of this one finds nobody here.
  const items = (customers.data?.items ?? []).filter(
    (customer) => aziendaId === null || customer.azienda_id === aziendaId,
  )
  // `limit` is a page, not the whole list, and the purpose of this picker is to not create
  // a second customer: when the page is not all of them, it says so.
  const partial = customers.data?.next_cursor != null
  const id = useId()
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor={id} className="text-sm text-muted-foreground">
        Cliente
      </Label>
      <Select
        value={value?.id ?? CREATE_NEW}
        disabled={disabled}
        onValueChange={(next) => {
          const picked = items.find((customer) => customer.id === next)
          onChange(picked === undefined ? undefined : { id: picked.id, name: picked.ragione_sociale })
        }}
      >
        <SelectTrigger id={id} aria-label={`Cliente di ${party}`} className="w-64">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={CREATE_NEW}>Crea un nuovo cliente</SelectItem>
          {/* A customer picked before the list reloaded stays selectable by its own entry. */}
          {value !== undefined && !items.some((customer) => customer.id === value.id) ? (
            <SelectItem value={value.id}>{value.name}</SelectItem>
          ) : null}
          {items.map((customer) => (
            <SelectItem key={customer.id} value={customer.id}>
              {customer.ragione_sociale}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {mixedAziende ? (
        <span className="text-xs text-muted-foreground">
          Le sue fatture sono di più aziende: non si può collegare a un cliente che hai già.
        </span>
      ) : null}
      {partial ? (
        <span className="text-xs text-muted-foreground">
          Elenco parziale: solo i primi 200 clienti.
        </span>
      ) : null}
    </div>
  )
}

function ReviewTableRow({
  row,
  declined,
  chosen,
}: {
  row: ReviewRow
  declined: ReadonlySet<string>
  chosen: ReadonlyMap<string, ChosenCustomer>
}) {
  const { invoice } = row
  const skipped =
    row.outcome === 'needs_customer_confirmation' &&
    row.party_key != null &&
    declined.has(row.party_key ?? '')
  const attached =
    !skipped && row.outcome === 'needs_customer_confirmation' && row.party_key != null
      ? chosen.get(row.party_key)
      : undefined
  const note = skipped
    ? 'Il cliente non viene creato: questa fattura non viene importata.'
    : attached !== undefined
      ? `Va al cliente «${attached.name}», già in anagrafica: non ne viene creato un altro.`
      : row.outcome === 'unclaimed' && row.motivo
      ? `Sembra una fattura FatturaPA ma non si legge: ${row.motivo}`
      : REVIEW_OUTCOME_NOTES[row.outcome]
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap">
        {invoice ? (
          <>
            <span className="font-medium">{invoice.numero}</span>
            <span className="block text-xs text-muted-foreground">
              {formatDate(invoice.data_emissione)}
              {invoice.tipo_documento !== 'fattura'
                ? ` · ${DOCUMENT_TYPE_LABELS[invoice.tipo_documento] ?? invoice.tipo_documento}`
                : ''}
            </span>
          </>
        ) : (
          EMPTY
        )}
      </TableCell>
      <TableCell>{invoice ? invoice.cliente.ragione_sociale : EMPTY}</TableCell>
      <TableCell className="text-right whitespace-nowrap">
        {invoice ? formatMoney(invoice.totale) : EMPTY}
      </TableCell>
      <TableCell className="max-w-48 truncate text-muted-foreground" title={row.file}>
        {row.file}
      </TableCell>
      <TableCell>
        <StatusPill
          tone={skipped ? 'muted' : attached !== undefined ? 'ink' : REVIEW_OUTCOME_TONES[row.outcome]}
        >
          {attached !== undefined ? 'Cliente scelto' : REVIEW_OUTCOME_LABELS[row.outcome]}
        </StatusPill>
        {note !== '' ? <p className="mt-1 text-xs text-muted-foreground">{note}</p> : null}
      </TableCell>
    </TableRow>
  )
}

/** The outcomes of a confirmation a person has to follow up, with the file each came from. */
const FOLLOW_UP: ConfirmRow['outcome'][] = [
  'conflict',
  'unclaimed',
  'needs_customer_confirmation',
]

function ResultPanel({
  rows,
  interrupted,
  aziendaName,
}: {
  rows: ConfirmRow[]
  interrupted: boolean
  aziendaName: (id: string | null) => string | undefined
}) {
  const counts = countConfirmed(rows)
  const years = registerYears(rows)
  const outcomes = Object.keys(CONFIRM_OUTCOME_LABELS) as (keyof typeof CONFIRM_OUTCOME_LABELS)[]
  const followUp = rows.filter((row) => FOLLOW_UP.includes(row.outcome))
  return (
    <div className="space-y-4" role="status">
      <ul className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        {outcomes
          .filter((outcome) => counts[outcome])
          .map((outcome) => (
            <li key={outcome}>
              <span className="text-muted-foreground">{CONFIRM_OUTCOME_LABELS[outcome]}</span>{' '}
              <span className="font-medium">{counts[outcome]}</span>
            </li>
          ))}
        {rows.length === 0 && !interrupted ? <li>Nessuna fattura è stata importata.</li> : null}
        {interrupted ? (
          <li>
            L’importazione si è interrotta: quello che è già stato scritto resta nel registro.
          </li>
        ) : null}
      </ul>
      {followUp.length > 0 ? (
        <section aria-labelledby="import-follow-up" className="space-y-1 border p-3 text-sm">
          <h3 id="import-follow-up" className="font-medium">
            Da guardare
          </h3>
          <ul className="space-y-1">
            {followUp.map((row, index) => (
              <li key={`${row.sha256}-${index}`}>
                <span className="font-medium">{row.file}</span>{' '}
                <span className="text-muted-foreground">
                  {CONFIRM_OUTCOME_LABELS[row.outcome]}
                  {row.motivo ? `: ${row.motivo}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {years.map((year) => {
        const name = aziendaName(year.aziendaId)
        const label = `Registro ${year.anno}${name ? ` di ${name}` : ''}`
        return (
          <p key={`${year.aziendaId}/${year.anno}`} className="border p-3 text-sm">
            <span className="font-medium">{label}: </span>
            {year.missing.length === 0
              ? 'non manca nessun numero.'
              : `mancano ancora i numeri ${year.missing.join(', ')}.`}
          </p>
        )
      })}
    </div>
  )
}

/**
 * The entry point on the Fatture page, next to «Nuova fattura». Mounted only while open,
 * like `NewProformaButton`'s dialog. Asks for the admin action the service asks for: the
 * review and the write are both admin-only, and a collaboratore who pressed it would get
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
