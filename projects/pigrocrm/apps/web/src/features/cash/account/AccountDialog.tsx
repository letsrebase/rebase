import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
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
import { Textarea } from '@rebase/ui/textarea'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useLegalEntity } from '@/lib/legalEntity'
import { ARRIVAL_LABELS, CADENCE_LABELS, groupIban } from './labels'
import {
  useArchiveBankAccount,
  useCreateBankAccount,
  useUpdateBankAccount,
  type BankAccount,
  type BankAccountCreate,
  type BankAccountUpdate,
} from './queries'

type Arrival = BankAccount['arrival']
type Cadence = BankAccount['cadence']

const ARRIVALS = Object.keys(ARRIVAL_LABELS) as Arrival[]
const CADENCES = Object.keys(CADENCE_LABELS) as Cadence[]

interface FormValues {
  name: string
  bank: string
  iban: string
  currency: string
  opening_balance: string
  opening_balance_date: string
  arrival: Arrival
  cadence: Cadence
  senders: string
  legal_entity_id: string
}

const EMPTY: FormValues = {
  name: '',
  bank: '',
  iban: '',
  currency: 'EUR',
  opening_balance: '',
  opening_balance_date: '',
  arrival: 'upload',
  cadence: 'monthly',
  senders: '',
  legal_entity_id: '',
}

function fromAccount(account: BankAccount): FormValues {
  return {
    name: account.name,
    bank: account.bank,
    iban: groupIban(account.iban),
    currency: account.currency,
    opening_balance: account.opening_balance ?? '',
    opening_balance_date: account.opening_balance_date ?? '',
    arrival: account.arrival,
    cadence: account.cadence,
    senders: account.senders.join('\n'),
    legal_entity_id: account.legal_entity_id,
  }
}

/** The addresses written one per line, or separated by commas or spaces. */
function parseSenders(text: string): string[] {
  return text.split(/[\s,;]+/).filter((part) => part !== '')
}

/** «1.234,50» or «1234.50» as the decimal string the API stores; the digits are the person's. */
function decimalString(text: string): string {
  const trimmed = text.trim().replace(/\s/g, '')
  return trimmed.includes(',') ? trimmed.replace(/\./g, '').replace(',', '.') : trimmed
}

const FIELDS = [
  'name',
  'bank',
  'iban',
  'currency',
  'opening_balance',
  'opening_balance_date',
  'arrival',
  'cadence',
  'senders',
  'legal_entity_id',
]

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The account being edited; absent, a new one is made. */
  account?: BankAccount
  /** Called with the account a create made, so a page can open it. */
  onCreated?: (account: BankAccount) => void
}

/**
 * A bank account, to make or to change: a dense two-column form with the action bar at the
 * bottom. On an existing account it also holds «Archivia il conto», the one way an account
 * leaves the lists; its movements stay.
 */
export function AccountDialog({ open, onOpenChange, account, onCreated }: Props) {
  const isCreate = account === undefined
  const { legal_entities, several, selected } = useLegalEntity()
  const [values, setValues] = useState<FormValues>(EMPTY)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [confirmArchive, setConfirmArchive] = useState(false)
  const [local, setLocal] = useState<string | null>(null)
  const create = useCreateBankAccount()
  const update = useUpdateBankAccount(account?.id ?? '')
  const archive = useArchiveBankAccount(account?.id ?? '')

  const [wasOpen, setWasOpen] = useState(false)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setValues(account === undefined ? { ...EMPTY, legal_entity_id: selected ?? '' } : fromAccount(account))
      setProblem(null)
      setConfirmArchive(false)
      setLocal(null)
    }
  }

  function set<K extends keyof FormValues>(key: K, value: FormValues[K]) {
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  function submit() {
    setProblem(null)
    setLocal(null)
    const balance = decimalString(values.opening_balance)
    if ((balance === '') !== (values.opening_balance_date === '')) {
      setLocal('Il saldo iniziale e la sua data vanno indicati insieme.')
      return
    }
    if (several && isCreate && values.legal_entity_id === '') {
      setLocal('Scegli l’azienda a cui appartiene il conto.')
      return
    }
    const common = {
      name: values.name.trim(),
      bank: values.bank.trim(),
      arrival: values.arrival,
      cadence: values.cadence,
      senders: parseSenders(values.senders),
      opening_balance: balance === '' ? null : balance,
      opening_balance_date: values.opening_balance_date === '' ? null : values.opening_balance_date,
    }
    const done = (message: string) => (saved: BankAccount) => {
      onOpenChange(false)
      toast.success(message)
      if (isCreate) onCreated?.(saved)
    }
    const failed = (error: unknown) => setProblem(toProblem(error))
    if (isCreate) {
      const body: BankAccountCreate = {
        ...common,
        iban: values.iban.replace(/\s+/g, ''),
        currency: values.currency.trim().toUpperCase(),
        ...(several ? { legal_entity_id: values.legal_entity_id } : {}),
      }
      create.mutate(body, { onSuccess: done('Conto aggiunto'), onError: failed })
      return
    }
    // The IBAN and the currency are sent only when changed: once a statement exists the server
    // refuses a change, and an unchanged value must not be taken for one.
    const iban = values.iban.replace(/\s+/g, '')
    const currency = values.currency.trim().toUpperCase()
    const body: BankAccountUpdate = {
      ...common,
      ...(iban === account.iban ? {} : { iban }),
      ...(currency === account.currency ? {} : { currency }),
    }
    update.mutate(body, { onSuccess: done('Conto aggiornato'), onError: failed })
  }

  function archiveNow() {
    setProblem(null)
    archive.mutate(undefined, {
      onSuccess: () => {
        onOpenChange(false)
        toast.success('Conto archiviato')
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const busy = create.isPending || update.isPending || archive.isPending
  const fieldError = problem === null ? null : fieldErrorFrom(problem)
  const errorOn = (key: string) =>
    fieldError?.field === key ? <p className="text-sm text-destructive">{fieldError.message}</p> : null
  const invalid = (key: string) => fieldError?.field === key
  const elsewhere =
    local ??
    (problem === null ? null : fieldError !== null && FIELDS.includes(fieldError.field) ? null : problem.detail)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isCreate ? 'Aggiungi un conto' : 'Modifica il conto'}</DialogTitle>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="account-name">Nome del conto</Label>
            <Input
              id="account-name"
              value={values.name}
              onChange={(event) => set('name', event.target.value)}
              placeholder="Conto operativo"
              aria-invalid={invalid('name')}
            />
            {errorOn('name')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="account-bank">Banca</Label>
            <Input
              id="account-bank"
              value={values.bank}
              onChange={(event) => set('bank', event.target.value)}
              placeholder="Banco BPM"
              aria-invalid={invalid('bank')}
            />
            {errorOn('bank')}
          </div>

          <div className="space-y-2">
            <Label htmlFor="account-iban">IBAN</Label>
            <Input
              id="account-iban"
              value={values.iban}
              onChange={(event) => set('iban', event.target.value)}
              className="tabular-nums"
              autoComplete="off"
              aria-invalid={invalid('iban')}
            />
            {errorOn('iban')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="account-currency">Valuta</Label>
            <Input
              id="account-currency"
              value={values.currency}
              onChange={(event) => set('currency', event.target.value)}
              maxLength={3}
              className="uppercase"
              aria-invalid={invalid('currency')}
            />
            {errorOn('currency')}
          </div>

          <div className="space-y-2">
            <Label htmlFor="account-opening">Saldo iniziale</Label>
            <Input
              id="account-opening"
              inputMode="decimal"
              value={values.opening_balance}
              onChange={(event) => set('opening_balance', event.target.value)}
              className="tabular-nums"
              placeholder="12.400,00"
              aria-invalid={invalid('opening_balance')}
            />
            {errorOn('opening_balance')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="account-opening-date">Alla data del</Label>
            <Input
              id="account-opening-date"
              type="date"
              value={values.opening_balance_date}
              onChange={(event) => set('opening_balance_date', event.target.value)}
              aria-invalid={invalid('opening_balance_date')}
            />
            {errorOn('opening_balance_date')}
          </div>

          <div className="space-y-2">
            <Label htmlFor="account-arrival">Come arrivano gli estratti conto</Label>
            <Select value={values.arrival} onValueChange={(next) => set('arrival', next as Arrival)}>
              <SelectTrigger id="account-arrival" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ARRIVALS.map((arrival) => (
                  <SelectItem key={arrival} value={arrival}>
                    {ARRIVAL_LABELS[arrival]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errorOn('arrival')}
          </div>
          <div className="space-y-2">
            <Label htmlFor="account-cadence">Ogni quanto</Label>
            <Select value={values.cadence} onValueChange={(next) => set('cadence', next as Cadence)}>
              <SelectTrigger id="account-cadence" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CADENCES.map((cadence) => (
                  <SelectItem key={cadence} value={cadence}>
                    {CADENCE_LABELS[cadence]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errorOn('cadence')}
          </div>

          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="account-senders">Indirizzi da cui arrivano</Label>
            <Textarea
              id="account-senders"
              value={values.senders}
              onChange={(event) => set('senders', event.target.value)}
              placeholder="estratti@banca.it"
              aria-invalid={invalid('senders')}
            />
            <p className="text-xs text-muted-foreground">Uno per riga. Vuoto: nessun mittente indicato.</p>
            {errorOn('senders')}
          </div>

          {several && isCreate && (
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="account-entity">Azienda</Label>
              <Select value={values.legal_entity_id} onValueChange={(next) => set('legal_entity_id', next)}>
                <SelectTrigger id="account-entity" className="w-full">
                  <SelectValue placeholder="Scegli l’azienda" />
                </SelectTrigger>
                <SelectContent>
                  {legal_entities.map((entity) => (
                    <SelectItem key={entity.id} value={entity.id}>
                      {entity.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {errorOn('legal_entity_id')}
            </div>
          )}
        </div>

        {elsewhere !== null && (
          <p role="alert" className="text-sm text-destructive">
            {elsewhere}
          </p>
        )}

        {!isCreate && account.archived_at === null && (
          <div className="border-t pt-4">
            {confirmArchive ? (
              <div className="flex flex-wrap items-center gap-3">
                <p className="text-sm">Archiviare questo conto? I movimenti restano.</p>
                <Button type="button" variant="outline" size="sm" onClick={archiveNow} disabled={busy}>
                  Archivia il conto
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => setConfirmArchive(false)}>
                  Non archiviare
                </Button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-3">
                <Button type="button" variant="outline" size="sm" onClick={() => setConfirmArchive(true)}>
                  Archivia il conto
                </Button>
                <span className="text-sm text-muted-foreground">I movimenti restano.</span>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? 'Salvataggio…' : isCreate ? 'Aggiungi il conto' : 'Salva'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
