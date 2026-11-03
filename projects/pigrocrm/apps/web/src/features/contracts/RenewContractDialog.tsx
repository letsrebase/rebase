import { useNavigate } from '@tanstack/react-router'
import { RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { formatDate } from '@/features/invoices/format'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useCan } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { useRenewContract, type RenewContractBody, type RenewalDuration } from './queries'

type RenewableContract = Pick<
  components['schemas']['ContractDetail'],
  'id' | 'title' | 'end' | 'next_contract_id'
>

type EndMode = 'stessa_duration' | 'duration' | 'data'

const END_MODES: Record<EndMode, string> = {
  stessa_duration: 'Stessa durata',
  duration: 'Durata',
  data: 'Data di fine',
}

const END_MODE_ORDER: EndMode[] = ['stessa_duration', 'duration', 'data']

const UNIT_LABELS: Record<RenewalDuration['unit'], string> = {
  days: 'Giorni',
  weeks: 'Settimane',
  months: 'Mesi',
  years: 'Anni',
}

const UNIT_ORDER: RenewalDuration['unit'][] = ['days', 'weeks', 'months', 'years']

/** The calendar day after `iso`, as `YYYY-MM-DD`. Built from the parts so a daylight
 *  saving change cannot move the day. */
function dayAfter(iso: string): string {
  const [year, month, day] = iso.split('-').map(Number)
  return toIsoDate(new Date(year ?? 0, (month ?? 1) - 1, (day ?? 1) + 1))
}

interface FormValues {
  title: string
  start: string
  mode: EndMode
  count: string
  unit: RenewalDuration['unit']
  end: string
}

const EMPTY_FORM: FormValues = {
  title: '',
  start: '',
  mode: 'stessa_duration',
  count: '1',
  unit: 'months',
  end: '',
}

/** Only what the person filled in: with nothing the server renews on the same terms for
 *  as long as the contract lasted, so the default submit is `{}`. A count that is not a
 *  number goes as typed and the server's own 422 names it. */
function toBody(values: FormValues): RenewContractBody {
  const body: RenewContractBody = {}
  const title = values.title.trim()
  if (title !== '') body.title = title
  if (values.start !== '') body.start = values.start
  if (values.mode === 'duration') {
    body.duration = { count: Number(values.count.trim()), unit: values.unit }
  } else if (values.mode === 'data' && values.end !== '') {
    body.end = values.end
  }
  return body
}

const FIELDS = ['title', 'start', 'end', 'duration', 'count', 'unit']

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contract: RenewableContract
}

/**
 * «Rinnova il contratto» (REB-763): the successor keeps every term of this contract and
 * moves its dates, and the three fields here are the only ways to say otherwise. The
 * server owns the dates; the sentence at the top only names the day it will start on when
 * the start is left empty, which is the day after `end`.
 */
export function RenewContractDialog({ open, onOpenChange, contract }: DialogProps) {
  const navigate = useNavigate()
  const [values, setValues] = useState<FormValues>(EMPTY_FORM)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const renew = useRenewContract(contract.id)

  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setValues(EMPTY_FORM)
      setProblem(null)
    }
  }

  function set<K extends keyof FormValues>(key: K, value: FormValues[K]) {
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  function submit() {
    setProblem(null)
    // An end date chosen and left blank is not «same duration»: the person did not pick
    // that, so the dialog asks instead of renewing for a term they never named.
    if (values.mode === 'data' && values.end === '') {
      setProblem({
        type: 'about:blank',
        title: 'Dati non validi',
        status: 422,
        detail: 'Scegli la data di fine, oppure «Stessa durata».',
        code: 'validation_failed',
        field: 'end',
      } as ProblemDetail)
      return
    }
    renew.mutate(toBody(values), {
      onSuccess: (successor) => {
        onOpenChange(false)
        toast.success('Contratto rinnovato')
        void navigate({ to: '/app/contracts/$contractId', params: { contractId: successor.id } })
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const errorOn = (...keys: string[]) =>
    fieldError !== null && keys.includes(fieldError.field) ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null
  // A 409 (already renewed) or a 422 about the contract (no end) names no field: it is
  // read here, inside the dialog, and not as a toast that closes before it is read.
  const elsewhere =
    problem !== null && (fieldError === null || !FIELDS.includes(fieldError.field))
      ? fieldError === null
        ? problem.detail
        : fieldError.message
      : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Rinnova il contratto</DialogTitle>
          <DialogDescription>
            {contract.end === null || values.start !== ''
              ? 'Il nuovo contratto ha gli stessi termini.'
              : `Il nuovo contratto ha gli stessi termini e comincia il ${formatDate(dayAfter(contract.end))}.`}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="renew-titolo">Titolo</Label>
          <Input
            id="renew-titolo"
            placeholder={contract.title}
            value={values.title}
            onChange={(event) => set('title', event.target.value)}
            aria-invalid={fieldError?.field === 'title'}
          />
          {errorOn('title')}
        </div>

        <div className="space-y-2">
          <Label htmlFor="renew-inizio">Inizio</Label>
          <Input
            id="renew-inizio"
            type="date"
            value={values.start}
            onChange={(event) => set('start', event.target.value)}
            aria-invalid={fieldError?.field === 'start'}
          />
          <p className="text-xs text-muted-foreground">Vuoto: il giorno dopo la fine.</p>
          {errorOn('start')}
        </div>

        <div className="space-y-2">
          <Label htmlFor="renew-fine-modo">Fine</Label>
          <Select value={values.mode} onValueChange={(value) => set('mode', value as EndMode)}>
            <SelectTrigger id="renew-fine-modo" className="w-full">
              <SelectValue placeholder="Seleziona…" />
            </SelectTrigger>
            <SelectContent>
              {END_MODE_ORDER.map((mode) => (
                <SelectItem key={mode} value={mode}>
                  {END_MODES[mode]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {values.mode === 'stessa_duration' && (
            <p className="text-xs text-muted-foreground">
              Il nuovo contratto dura quanto questo.
            </p>
          )}
        </div>

        {values.mode === 'duration' && (
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="renew-conteggio">Quanto dura</Label>
              <Input
                id="renew-conteggio"
                type="number"
                min={1}
                value={values.count}
                onChange={(event) => set('count', event.target.value)}
                aria-invalid={fieldError?.field === 'count' || fieldError?.field === 'duration'}
              />
              {errorOn('count', 'duration')}
            </div>
            <div className="space-y-2">
              <Label htmlFor="renew-unita">Unità</Label>
              <Select
                value={values.unit}
                onValueChange={(value) => set('unit', value as RenewalDuration['unit'])}
              >
                <SelectTrigger id="renew-unita" className="w-full">
                  <SelectValue placeholder="Seleziona…" />
                </SelectTrigger>
                <SelectContent>
                  {UNIT_ORDER.map((unit) => (
                    <SelectItem key={unit} value={unit}>
                      {UNIT_LABELS[unit]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {errorOn('unit')}
            </div>
          </div>
        )}

        {values.mode === 'data' && (
          <div className="space-y-2">
            <Label htmlFor="renew-fine">Data di fine</Label>
            <Input
              id="renew-fine"
              type="date"
              value={values.end}
              onChange={(event) => set('end', event.target.value)}
              aria-invalid={fieldError?.field === 'end'}
            />
            {errorOn('end')}
          </div>
        )}

        {elsewhere !== null && <p className="text-sm text-destructive">{elsewhere}</p>}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={renew.isPending}>
            {renew.isPending ? 'Rinnovo…' : 'Rinnova'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * The page's «Rinnova» action. Offered to a writer, on a contract that has an end to
 * renew from and has not been renewed yet: a contract without `end` has nothing to
 * continue (the API answers 422) and one with a successor answers 409.
 */
export function RenewContractButton({ contract }: { contract: RenewableContract }) {
  const canRenew = useCan('renew_contract')
  const [open, setOpen] = useState(false)

  if (!canRenew || contract.end === null || contract.next_contract_id !== null) return null

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <RefreshCw className="mr-2 size-4" />
        Rinnova
      </Button>
      <RenewContractDialog open={open} onOpenChange={setOpen} contract={contract} />
    </>
  )
}
