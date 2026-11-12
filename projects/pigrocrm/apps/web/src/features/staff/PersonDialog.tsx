import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import { useMemberDirectory } from '@/features/users/directory'
import { useLegalEntitySites } from '@/features/settings/queries'
import { siteLabel } from '@/features/settings/sites'
import { fieldErrorFrom, toProblem } from '@/lib/api'
import { CONTRACT_TYPE_LABELS, KIND_LABELS } from './labels'
import {
  useCreateStaff,
  useUpdateStaff,
  type DirectorBody,
  type EmployeeBody,
  type StaffMember,
  type StaffUpdateBody,
} from './queries'
import { rowsFrom, wireContracts, type ContractWire } from './seasonalContracts'
import { SeasonalContractsField } from './SeasonalContractsField'

type Kind = StaffMember['kind']

/** Every field as the text in its input; what is empty is left out or cleared on the way out. */
interface Values {
  name: string
  start_date: string
  end_date: string
  hours_per_month: string
  user_id: string
  collective_agreement: string
  contract_level: string
  contract_type: string
  disabled_worker: string
  gross_annual_pay: string
  monthly_payments: string
  part_time_percent: string
  employer_inps_rate: string
  employer_inail_rate: string
  employee_inps_rate: string
  thirteenth_month: string
  fourteenth_month: string
  annual_fee: string
  fee_payment: string
  accrues_tfm: boolean
  tfm_annual_amount: string
  inps_company_share: string
}

const NONE = 'none'
/** The choice «Sede legale» in the workplace select; any other value is a site's id. */
const REGISTERED_OFFICE = 'registered-office'

/** Where the person works as the select holds it: a site's id, the registered office, or nothing. */
function workplaceOf(member: StaffMember | undefined): string {
  if (member?.at_registered_office === true) return REGISTERED_OFFICE
  return member?.site_id ?? NONE
}

/** The place of work in the body's two fields; `before` is what the person had, so a change
 *  names only what the server needs: a site takes them off the registered office, the registered
 *  office takes them off any site, and «Non indicata» empties whichever was set. */
function workplaceBody(before: string, next: string): Record<string, unknown> {
  if (next === REGISTERED_OFFICE) return { at_registered_office: true }
  if (next !== NONE) return { site_id: next }
  return before === REGISTERED_OFFICE ? { at_registered_office: false } : { site_id: null }
}

function from(member: StaffMember | undefined): Values {
  const text = (value: string | number | null | undefined) => (value === null || value === undefined ? '' : String(value))
  return {
    name: text(member?.name),
    start_date: text(member?.start_date),
    end_date: text(member?.end_date),
    hours_per_month: text(member?.hours_per_month),
    user_id: text(member?.user_id),
    collective_agreement: text(member?.collective_agreement),
    contract_level: text(member?.contract_level),
    contract_type: text(member?.contract_type),
    disabled_worker: member?.disabled_worker == null ? '' : String(member.disabled_worker),
    gross_annual_pay: text(member?.gross_annual_pay),
    monthly_payments: text(member?.monthly_payments ?? 13),
    part_time_percent: text(member?.part_time_percent ?? 100),
    employer_inps_rate: text(member?.employer_inps_rate),
    employer_inail_rate: text(member?.employer_inail_rate),
    employee_inps_rate: text(member?.employee_inps_rate),
    thirteenth_month: text(member?.thirteenth_month),
    fourteenth_month: text(member?.fourteenth_month),
    annual_fee: text(member?.annual_fee),
    fee_payment: text(member?.fee_payment ?? 'monthly'),
    accrues_tfm: member?.accrues_tfm ?? false,
    tfm_annual_amount: text(member?.tfm_annual_amount),
    inps_company_share: text(member?.inps_company_share),
  }
}

const DECIMALS = new Set<keyof Values>([
  'hours_per_month',
  'gross_annual_pay',
  'part_time_percent',
  'employer_inps_rate',
  'employer_inail_rate',
  'employee_inps_rate',
  'annual_fee',
  'tfm_annual_amount',
  'inps_company_share',
])
const INTEGERS = new Set<keyof Values>(['monthly_payments', 'thirteenth_month', 'fourteenth_month'])

const EMPLOYEE_FIELDS: (keyof Values)[] = [
  'collective_agreement',
  'contract_level',
  'contract_type',
  'disabled_worker',
  'gross_annual_pay',
  'monthly_payments',
  'part_time_percent',
  'employer_inps_rate',
  'employer_inail_rate',
  'employee_inps_rate',
  'thirteenth_month',
  'fourteenth_month',
]
const DIRECTOR_FIELDS: (keyof Values)[] = [
  'annual_fee',
  'fee_payment',
  'accrues_tfm',
  'tfm_annual_amount',
  'inps_company_share',
]
const COMMON: (keyof Values)[] = ['name', 'start_date', 'end_date', 'hours_per_month', 'user_id']

/** One field's value as the body carries it: decimals as strings with a point, whole numbers
 *  as numbers, and an empty optional field as `null` (which clears it on an update). */
function wire(field: keyof Values, values: Values): unknown {
  const raw = values[field]
  if (typeof raw === 'boolean') return raw
  if (field === 'disabled_worker') return raw === 'true' ? true : raw === 'false' ? false : null
  const text = raw.trim()
  if (text === '') return null
  if (INTEGERS.has(field)) return Number(text)
  if (DECIMALS.has(field)) return text.replace(',', '.')
  return text
}

const REQUIRED: ReadonlySet<keyof Values> = new Set([
  'name',
  'start_date',
  'gross_annual_pay',
  'monthly_payments',
  'part_time_percent',
  'annual_fee',
])

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The legal entity a new person joins; a person already there keeps theirs. */
  legalEntityId: string
  /** The entity's name, said beside the form from the second legal entity on. */
  legalEntityName: string | null
  /** A company pack takes directors; the others take employees only. */
  directorsAllowed: boolean
  member?: StaffMember
}

/** «Aggiungi una persona» and «Modifica condizioni»: one dense two-column form, the kind first.
 *  The server's refusal (a pack that takes no staff, a missing field) is shown as it says it. */
export function PersonDialog({ open, onOpenChange, legalEntityId, legalEntityName, directorsAllowed, member }: Props) {
  const editing = member !== undefined
  const [kind, setKind] = useState<Kind>(member?.kind ?? 'employee')
  const [values, setValues] = useState(() => from(member))
  const [missing, setMissing] = useState<string | null>(null)
  const [contracts, setContracts] = useState(() => rowsFrom(member?.seasonal_contracts))
  const [contractsError, setContractsError] = useState<string | null>(null)
  const thisYear = new Date().getFullYear()
  const [workplace, setWorkplace] = useState(() => workplaceOf(member))
  const sites = useLegalEntitySites(legalEntityId).data ?? []
  const members = useMemberDirectory()
  const create = useCreateStaff()
  const update = useUpdateStaff(member?.id ?? '')
  const busy = create.isPending || update.isPending
  const error = create.error ?? update.error
  const problem = error === null ? null : toProblem(error)
  const fieldError = problem === null ? null : fieldErrorFrom(problem)

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setMissing(null)
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  // A fourteenth month belongs to fourteen payments: with thirteen it is not asked, and a stored
  // one is emptied on an edit (the server refuses it otherwise).
  const fourteenth = kind === 'employee' && values.monthly_payments === '14'
  const fields = [
    ...COMMON,
    ...(kind === 'employee' ? EMPLOYEE_FIELDS : DIRECTOR_FIELDS),
  ].filter((field) => field !== 'fourteenth_month' || fourteenth || editing)

  function submit() {
    const notWhole = fields.find((field) => INTEGERS.has(field) && !/^\d{0,2}$/.test((values[field] as string).trim()))
    if (notWhole !== undefined) {
      setMissing(notWhole)
      return
    }
    const empty = fields.find((field) => REQUIRED.has(field) && wire(field, values) === null)
    if (empty !== undefined) {
      setMissing(empty)
      return
    }
    const done = (message: string) => () => {
      toast.success(message)
      onOpenChange(false)
    }
    let history: ContractWire[] | undefined
    if (kind === 'employee' && values.contract_type === 'seasonal') {
      const wired = wireContracts(contracts, thisYear)
      if (!wired.ok) {
        setContractsError(wired.error)
        return
      }
      history = wired.value
    }
    const wasHistory = wireContracts(rowsFrom(member?.seasonal_contracts), thisYear)
    if (editing) {
      const before = from(member)
      const body: Record<string, unknown> = {}
      for (const field of fields) {
        if (field === 'fourteenth_month' && !fourteenth) {
          if (before[field] !== '') body[field] = null
        } else if (before[field] !== values[field]) {
          body[field] = wire(field, values)
        }
      }
      if (history !== undefined && JSON.stringify(history) !== JSON.stringify(wasHistory)) {
        body.seasonal_contracts = history.length === 0 ? null : history
      }
      const placeBefore = workplaceOf(member)
      if (workplace !== placeBefore) Object.assign(body, workplaceBody(placeBefore, workplace))
      if (Object.keys(body).length === 0) {
        onOpenChange(false)
        return
      }
      update.mutate(body as StaffUpdateBody, { onSuccess: done('Condizioni salvate') })
      return
    }
    const body: Record<string, unknown> = { kind, legal_entity_id: legalEntityId }
    for (const field of fields) {
      const value = wire(field, values)
      if (value !== null) body[field] = value
    }
    if (history !== undefined && history.length > 0) body.seasonal_contracts = history
    if (workplace !== NONE) Object.assign(body, workplaceBody(NONE, workplace))
    create.mutate(body as unknown as EmployeeBody | DirectorBody, { onSuccess: done('Persona aggiunta') })
  }

  const text = (field: keyof Values, label: string, props: { type?: string; inputMode?: 'decimal' | 'numeric'; hint?: string } = {}) => {
    const value = values[field] as string
    const invalid = missing === field || fieldError?.field === field
    return (
      <div className="space-y-1.5">
        <Label htmlFor={`person-${field}`}>
          {label}
          {REQUIRED.has(field) ? ' *' : ''}
        </Label>
        <Input
          id={`person-${field}`}
          type={props.type ?? 'text'}
          inputMode={props.inputMode}
          value={value}
          aria-invalid={invalid || undefined}
          onChange={(event) => set(field, event.target.value as never)}
        />
        {props.hint === undefined ? null : <p className="text-xs text-muted-foreground">{props.hint}</p>}
        {fieldError?.field === field ? <p className="text-xs text-destructive">{fieldError.message}</p> : null}
      </div>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] gap-4 overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{editing ? 'Modifica condizioni' : 'Aggiungi una persona'}</DialogTitle>
          <DialogDescription>
            {legalEntityName === null ? 'Le condizioni servono per stimare quanto costa ogni mese.' : `Per ${legalEntityName}. Le condizioni servono per stimare quanto costa ogni mese.`}
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        >
          {editing ? null : (
            <div className="space-y-1.5">
              <Label htmlFor="person-kind">Chi è</Label>
              <Select value={kind} onValueChange={(next) => setKind(next as Kind)}>
                <SelectTrigger id="person-kind" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="employee">{KIND_LABELS.employee}</SelectItem>
                  {directorsAllowed ? <SelectItem value="director">{KIND_LABELS.director}</SelectItem> : null}
                </SelectContent>
              </Select>
              {directorsAllowed ? null : (
                <p className="text-xs text-muted-foreground">Gli amministratori si registrano solo su una società.</p>
              )}
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            {text('name', 'Nome')}
            <div className="space-y-1.5">
              <Label htmlFor="person-user">Utente collegato</Label>
              <Select value={values.user_id === '' ? NONE : values.user_id} onValueChange={(next) => set('user_id', next === NONE ? '' : next)}>
                <SelectTrigger id="person-user" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>Nessuno</SelectItem>
                  {members.members.map((candidate) => (
                    <SelectItem key={candidate.id} value={candidate.id}>
                      {candidate.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">Serve per il costo orario nelle ore registrate.</p>
            </div>
            {text('start_date', 'In servizio dal', { type: 'date' })}
            {text('end_date', 'Fino al', { type: 'date' })}
            {text('hours_per_month', 'Ore di lavoro al mese', { inputMode: 'decimal' })}
            {sites.length === 0 ? null : (
              <div className="space-y-1.5">
                <Label htmlFor="person-workplace">Sede di lavoro</Label>
                <Select value={workplace} onValueChange={setWorkplace}>
                  <SelectTrigger id="person-workplace" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>Non indicata</SelectItem>
                    <SelectItem value={REGISTERED_OFFICE}>Sede legale</SelectItem>
                    {sites.map((site) => (
                      <SelectItem key={site.id} value={site.id}>
                        {siteLabel(site)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">Serve all’IRAP: si ripartisce tra le regioni in base alle retribuzioni.</p>
              </div>
            )}
            {kind === 'employee' ? (
              <>
                {text('collective_agreement', 'Contratto collettivo')}
                {text('contract_level', 'Livello')}
                <div className="space-y-1.5">
                  <Label htmlFor="person-contract_type">Tipo di contratto</Label>
                  <Select value={values.contract_type === '' ? NONE : values.contract_type} onValueChange={(next) => set('contract_type', next === NONE ? '' : next)}>
                    <SelectTrigger id="person-contract_type" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>Non indicato</SelectItem>
                      <SelectItem value="permanent">{CONTRACT_TYPE_LABELS.permanent}</SelectItem>
                      <SelectItem value="fixed_term">{CONTRACT_TYPE_LABELS.fixed_term}</SelectItem>
                      <SelectItem value="seasonal">{CONTRACT_TYPE_LABELS.seasonal}</SelectItem>
                      <SelectItem value="apprentice">{CONTRACT_TYPE_LABELS.apprentice}</SelectItem>
                      <SelectItem value="training">{CONTRACT_TYPE_LABELS.training}</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">Serve all’IRAP: apprendisti e formazione lavoro si deducono per intero.</p>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="person-disabled_worker">Lavoratore disabile</Label>
                  <Select value={values.disabled_worker === '' ? NONE : values.disabled_worker} onValueChange={(next) => set('disabled_worker', next === NONE ? '' : next)}>
                    <SelectTrigger id="person-disabled_worker" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE}>Non indicato</SelectItem>
                      <SelectItem value="true">Sì</SelectItem>
                      <SelectItem value="false">No</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">Legge 68/1999 o 482/1968: costo dedotto per intero.</p>
                </div>
                {values.contract_type === 'seasonal' ? (
                  <SeasonalContractsField
                    rows={contracts}
                    onChange={(rows) => {
                      setContractsError(null)
                      setContracts(rows)
                    }}
                    thisYear={thisYear}
                    error={contractsError ?? (fieldError?.field === 'seasonal_contracts' ? fieldError.message : null)}
                  />
                ) : null}
                {text('gross_annual_pay', 'Retribuzione lorda annua (€)', { inputMode: 'decimal' })}
                <div className="space-y-1.5">
                  <Label htmlFor="person-monthly_payments">Mensilità *</Label>
                  <Select value={values.monthly_payments} onValueChange={(next) => set('monthly_payments', next)}>
                    <SelectTrigger id="person-monthly_payments" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="13">13</SelectItem>
                      <SelectItem value="14">14</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {text('part_time_percent', 'Part-time (%)', { inputMode: 'decimal' })}
                {text('employer_inps_rate', 'INPS a carico azienda (%)', { inputMode: 'decimal', hint: 'La digiti tu: non esiste un’aliquota uguale per tutti.' })}
                {text('employer_inail_rate', 'INAIL, tasso applicato (%)', { inputMode: 'decimal', hint: 'INAIL lo scrive per mille nelle Basi di calcolo premi: 9,75‰ si scrive 0,975.' })}
                {text('employee_inps_rate', 'INPS a carico della persona (%)', { inputMode: 'decimal' })}
                {text('thirteenth_month', 'Mese della tredicesima', { inputMode: 'numeric', hint: 'Da 1 a 12.' })}
                {values.monthly_payments === '14'
                  ? text('fourteenth_month', 'Mese della quattordicesima', { inputMode: 'numeric', hint: 'Da 1 a 12.' })
                  : null}
              </>
            ) : (
              <>
                {text('annual_fee', 'Compenso annuo (€)', { inputMode: 'decimal' })}
                <div className="space-y-1.5">
                  <Label htmlFor="person-fee_payment">Come viene pagato</Label>
                  <Select value={values.fee_payment} onValueChange={(next) => set('fee_payment', next)}>
                    <SelectTrigger id="person-fee_payment" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="monthly">Ogni mese</SelectItem>
                      <SelectItem value="other">In altro modo</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center gap-2 sm:col-span-2">
                  <Checkbox
                    id="person-accrues_tfm"
                    checked={values.accrues_tfm}
                    onCheckedChange={(next) => set('accrues_tfm', next === true)}
                  />
                  <Label htmlFor="person-accrues_tfm">Matura il trattamento di fine mandato</Label>
                </div>
                {text('tfm_annual_amount', 'Importo annuo del TFM (€)', { inputMode: 'decimal' })}
                {text('inps_company_share', 'Quota INPS a carico società (%)', { inputMode: 'decimal' })}
              </>
            )}
          </div>

          {missing !== null ? (
            <p role="alert" className="text-sm text-destructive">
              {INTEGERS.has(missing as keyof Values) ? 'Scrivi un numero intero, da 1 a 14.' : 'Compila i campi con l’asterisco.'}
            </p>
          ) : null}
          {problem !== null && fieldError === null ? (
            <p role="alert" className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {problem.detail}
            </p>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Annulla
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? 'Salvo…' : editing ? 'Salva le condizioni' : 'Aggiungi'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
