import { useNavigate } from '@tanstack/react-router'
import { useState, type FormEvent, type ReactNode } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
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
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import { useDeal, useDeals } from '@/features/deals/queries'
import { useOrganizationContracts } from '@/features/contracts/queries'
import { OrganizationPicker } from '@/features/organizations/OrganizationPicker'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useModules } from '@/lib/modules'
import { FreelancerAvailability } from './AvailabilityStrip'
import type { Engagement, EngagementWarning } from './labels'
import { useCreateEngagement, useUpdateEngagement } from './queries'

type Create = components['schemas']['EngagementCreate']
type Update = components['schemas']['EngagementUpdate']

/** «Giorni a settimana»: from half a day to five, in steps of half a day, as the API's decimal strings. */
const DAYS_PER_WEEK = ['0.5', '1', '1.5', '2', '2.5', '3', '3.5', '4', '4.5', '5'] as const

const FEE_UNITS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'day', label: 'Al giorno' },
  { value: 'hour', label: "All'ora" },
  { value: 'flat', label: 'A corpo' },
]

const LETTER_TYPES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'ordinary', label: 'Ordinaria' },
  { value: 'occasional', label: 'Occasionale' },
]

const PAYMENT_RULES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'fixed_term', label: 'A termine fisso, dalla fattura' },
  { value: 'on_collection', label: 'A incasso, dopo che il cliente ha pagato rebase' },
]

/** `Select` needs a non-empty string for «none»: a UI-only value, never sent. */
const NONE = 'none'

const DECIMAL = /^\d+(\.\d+)?$/
const INTEGER = /^\d+$/
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/

/** `450,50` as `450.50`: a decimal stays a string from the input to the body, never a float. */
function decimalText(text: string): string {
  return text.trim().replace(',', '.')
}

/** `4.00` and `4` are one number: the trailing zeros of a fraction are dropped, as strings. */
function trimDecimal(text: string): string {
  return text.includes('.') ? text.replace(/0+$/, '').replace(/\.$/, '') : text
}

function sameDecimal(typed: string, stored: string | null): boolean {
  return trimDecimal(decimalText(typed)) === trimDecimal(stored ?? '')
}

interface Values {
  freelancerId: string | null
  clientId: string | null
  dealId: string
  contractId: string
  role: string
  startsOn: string
  endsOn: string
  daysPerWeek: string
  fee: string
  feeUnit: string
  expectedDays: string
  letterType: string
  paymentRule: string
  paymentDays: string
  endOfMonth: boolean
  collectionDays: string
}

/**
 * What the hub's «Crea match» fills in (REB-1105): the client and the deal of the request, and the
 * engagement id the hub minted, which the draft is created under (`hub_match_id`) so the CRM's first
 * notice finds the hub's row.
 */
export interface EngagementPrefill {
  clientId?: string
  dealId?: string
  hubMatchId?: string
}

function initialValues(
  engagement: Engagement | undefined,
  freelancerId: string | undefined,
  prefill: EngagementPrefill | undefined,
): Values {
  if (engagement === undefined) {
    return {
      freelancerId: freelancerId ?? null,
      clientId: prefill?.clientId ?? null,
      dealId: prefill?.dealId ?? '',
      contractId: '',
      role: '',
      startsOn: '',
      endsOn: '',
      daysPerWeek: '',
      fee: '',
      feeUnit: 'day',
      expectedDays: '',
      letterType: '',
      paymentRule: 'fixed_term',
      paymentDays: '',
      endOfMonth: false,
      collectionDays: '',
    }
  }
  return {
    freelancerId: engagement.freelancer_id,
    clientId: engagement.client_organization_id,
    dealId: engagement.deal_id ?? '',
    contractId: engagement.contract_id ?? '',
    role: engagement.role ?? '',
    startsOn: engagement.starts_on,
    endsOn: engagement.ends_on ?? '',
    daysPerWeek: engagement.days_per_week === null ? '' : trimDecimal(engagement.days_per_week),
    fee: engagement.fee ?? '',
    feeUnit: engagement.fee_unit,
    expectedDays: engagement.expected_days === null ? '' : trimDecimal(engagement.expected_days),
    letterType: engagement.letter_type,
    paymentRule: engagement.payment_rule,
    paymentDays: engagement.payment_days === null ? '' : String(engagement.payment_days),
    endOfMonth: engagement.payment_end_of_month,
    collectionDays: engagement.collection_days === null ? '' : String(engagement.collection_days),
  }
}

/** The API fields this form has a control for: an error on any other is shown above the fields. */
const DRAWN_FIELDS = [
  'freelancer_id',
  'client_organization_id',
  'deal_id',
  'contract_id',
  'role',
  'starts_on',
  'ends_on',
  'days_per_week',
  'fee',
  'fee_unit',
  'expected_days',
  'letter_type',
  'payment_rule',
  'payment_days',
  'collection_days',
]

type Errors = Partial<Record<string, string>>

/** What the form can say before asking: the fields the letter cannot be written without. The rest is the server's. */
function validate(values: Values, locked: boolean): Errors {
  const found: Errors = {}
  if (values.daysPerWeek === '') found.days_per_week = 'Scegli i giorni a settimana.'
  if (locked) return found
  if (values.freelancerId === null) found.freelancer_id = 'Scegli il freelance.'
  if (values.role.trim() === '') found.role = 'Scrivi il ruolo.'
  if (!ISO_DAY.test(values.startsOn)) found.starts_on = 'Scegli la data di inizio.'
  if (values.endsOn !== '' && values.startsOn !== '' && values.endsOn < values.startsOn) {
    found.ends_on = 'La fine non può essere prima dell’inizio.'
  }
  if (values.fee.trim() === '') found.fee = 'Scrivi il compenso.'
  else if (!DECIMAL.test(decimalText(values.fee))) found.fee = 'Il compenso è un numero, ad esempio 450,00.'
  if (values.expectedDays.trim() !== '' && !DECIMAL.test(decimalText(values.expectedDays))) {
    found.expected_days = 'I giorni previsti sono un numero, ad esempio 20 o 12,5.'
  }
  if (values.paymentRule === 'fixed_term') {
    if (!INTEGER.test(values.paymentDays.trim())) found.payment_days = 'Scrivi i giorni dalla fattura.'
  } else if (values.collectionDays.trim() !== '' && !INTEGER.test(values.collectionDays.trim())) {
    found.collection_days = 'Scrivi un numero intero di giorni.'
  }
  return found
}

/** The body of a new draft: what was filled in, and nothing the person left empty. */
function createBody(values: Values, hubMatchId: string | undefined): Create {
  const body: Create = {
    freelancer_id: values.freelancerId ?? '',
    role: values.role.trim(),
    starts_on: values.startsOn,
    days_per_week: values.daysPerWeek,
    fee: decimalText(values.fee),
    fee_unit: values.feeUnit,
    payment_rule: values.paymentRule,
    payment_end_of_month: values.paymentRule === 'fixed_term' && values.endOfMonth,
  }
  if (hubMatchId !== undefined) body.hub_match_id = hubMatchId
  if (values.clientId !== null) body.client_organization_id = values.clientId
  if (values.dealId !== '') body.deal_id = values.dealId
  if (values.contractId !== '') body.contract_id = values.contractId
  if (values.endsOn !== '') body.ends_on = values.endsOn
  if (values.expectedDays.trim() !== '') body.expected_days = decimalText(values.expectedDays)
  if (values.letterType !== '') body.letter_type = values.letterType
  if (values.paymentRule === 'fixed_term') body.payment_days = Number(values.paymentDays.trim())
  else if (values.collectionDays.trim() !== '') body.collection_days = Number(values.collectionDays.trim())
  return body
}

/**
 * What the edit sends: only what changed. A signed or sent letter changes only the days a week and
 * the three links; a draft changes freely, and a rule that changes is restated whole (the server
 * clears what the new rule does not use).
 */
function updateBody(values: Values, engagement: Engagement, locked: boolean): Update {
  const body: Update = {}
  if (!sameDecimal(values.daysPerWeek, engagement.days_per_week)) body.days_per_week = values.daysPerWeek
  const linksChanged =
    values.clientId !== engagement.client_organization_id ||
    (values.dealId || null) !== engagement.deal_id ||
    (values.contractId || null) !== engagement.contract_id
  if (linksChanged) {
    body.client_organization_id = values.clientId
    body.deal_id = values.dealId || null
    body.contract_id = values.contractId || null
  }
  if (locked) return body
  if (values.role.trim() !== engagement.role) body.role = values.role.trim()
  if (values.startsOn !== engagement.starts_on) body.starts_on = values.startsOn
  if ((values.endsOn || null) !== engagement.ends_on) body.ends_on = values.endsOn || null
  if (!sameDecimal(values.fee, engagement.fee)) body.fee = decimalText(values.fee)
  if (values.feeUnit !== engagement.fee_unit) body.fee_unit = values.feeUnit
  if (!sameDecimal(values.expectedDays, engagement.expected_days)) {
    body.expected_days = values.expectedDays.trim() === '' ? null : decimalText(values.expectedDays)
  }
  if (values.letterType !== '' && values.letterType !== engagement.letter_type) {
    body.letter_type = values.letterType
  }
  const ruleChanged =
    values.paymentRule !== engagement.payment_rule ||
    (values.paymentRule === 'fixed_term' &&
      (values.paymentDays.trim() !== String(engagement.payment_days ?? '') ||
        values.endOfMonth !== engagement.payment_end_of_month)) ||
    (values.paymentRule === 'on_collection' &&
      values.collectionDays.trim() !== String(engagement.collection_days ?? ''))
  if (ruleChanged) {
    body.payment_rule = values.paymentRule
    if (values.paymentRule === 'fixed_term') {
      body.payment_days = Number(values.paymentDays.trim())
      body.payment_end_of_month = values.endOfMonth
    } else if (values.collectionDays.trim() !== '') {
      body.collection_days = Number(values.collectionDays.trim())
    }
  }
  return body
}

/** One field: the label beside the control from `sm` up (the dense form of the brief), above it on the phone. */
function Field({
  id,
  label,
  error,
  hint,
  children,
}: {
  id: string
  label: string
  error?: string | null
  hint?: string
  children: ReactNode
}) {
  return (
    <div className="grid gap-1.5 sm:grid-cols-[9.5rem_minmax(0,1fr)] sm:gap-x-3">
      <Label htmlFor={id} className="sm:pt-2">
        {label}
      </Label>
      <div className="min-w-0 space-y-1">
        {children}
        {hint === undefined ? null : <p className="text-xs text-muted-foreground">{hint}</p>}
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  )
}

/** A deal of the client, optional: the one the engagement serves. The list is the client's own. */
function DealSelect({
  customerId,
  value,
  onChange,
  disabled,
}: {
  customerId: string
  value: string
  onChange: (id: string) => void
  disabled: boolean
}) {
  const deals = useDeals({ customer_id: customerId })
  return (
    <Select
      value={value === '' ? NONE : value}
      onValueChange={(next) => onChange(next === NONE ? '' : next)}
      disabled={disabled}
    >
      <SelectTrigger id="engagement-deal" className="w-full">
        <SelectValue placeholder="Nessun deal" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>Nessun deal</SelectItem>
        {deals.data?.items.map((deal) => (
          <SelectItem key={deal.id} value={deal.id}>
            {deal.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** A contract of the client, optional: the one the engagement serves. */
function ContractSelect({
  customerId,
  value,
  onChange,
  disabled,
}: {
  customerId: string
  value: string
  onChange: (id: string) => void
  disabled: boolean
}) {
  const contracts = useOrganizationContracts(customerId)
  return (
    <Select
      value={value === '' ? NONE : value}
      onValueChange={(next) => onChange(next === NONE ? '' : next)}
      disabled={disabled}
    >
      <SelectTrigger id="engagement-contract" className="w-full">
        <SelectValue placeholder="Nessun contratto" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>Nessun contratto</SelectItem>
        {contracts.data?.items.map((contract) => (
          <SelectItem key={contract.id} value={contract.id}>
            {contract.title}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** The server's sentences about an overlap or a week over capacity: a warning, never a refusal. */
function WarningsBand({ warnings }: { warnings: readonly EngagementWarning[] }) {
  if (warnings.length === 0) return null
  return (
    <div role="status" aria-label="Da sapere" className="space-y-1 border bg-card px-4 py-3 text-sm">
      <p className="font-medium">Da sapere</p>
      <ul className="space-y-1">
        {warnings.map((warning) => (
          <li key={`${warning.code}-${warning.engagement_ids.join(',')}`} className="flex items-start gap-2">
            <span aria-hidden="true" className="mt-1.5 size-1.5 shrink-0 bg-royal-gold" />
            <span className="min-w-0 break-words">{warning.message}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function EngagementForm({
  engagement,
  freelancerId,
  prefill,
  onClose,
}: {
  engagement: Engagement | undefined
  freelancerId: string | undefined
  prefill: EngagementPrefill | undefined
  onClose: () => void
}) {
  const navigate = useNavigate()
  const modules = useModules()
  const create = useCreateEngagement()
  const update = useUpdateEngagement(engagement?.id ?? '')
  const [values, setValues] = useState(() => initialValues(engagement, freelancerId, prefill))
  // Handed over from the hub, the engagement serves the request's deal: client and deal are
  // read-only, and the client is the deal's own when the link did not carry one.
  const fixedDealId = engagement === undefined ? prefill?.dealId : undefined
  const fixedDeal = useDeal(fixedDealId ?? '', { enabled: fixedDealId !== undefined })
  const dealCustomerId = fixedDeal.data?.customer_id
  const clientId = fixedDealId !== undefined ? (dealCustomerId ?? values.clientId) : values.clientId
  const [errors, setErrors] = useState<Errors>({})
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  // A letter that has gone out, or a client the reader cannot see, changes only what the server
  // allows: the days a week and the client links.
  const locked = engagement !== undefined && (engagement.state !== 'draft' || engagement.redacted)
  const lockedFreelancer = engagement !== undefined || freelancerId !== undefined
  const pending = create.isPending || update.isPending

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  function done(saved: Engagement, message: string) {
    toast.success(message)
    // The overlap and over-capacity sentences of the answer are not lost on the way to the page.
    for (const warning of saved.warnings ?? []) toast.warning(warning.message)
    onClose()
    void navigate({ to: '/app/engagements/$engagementId', params: { engagementId: saved.id } })
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const found = validate(values, locked)
    setErrors(found)
    setProblem(null)
    if (Object.keys(found).length > 0) return
    if (engagement === undefined) {
      create.mutate(createBody({ ...values, clientId }, prefill?.hubMatchId), {
        onSuccess: (saved) => done(saved, 'Bozza creata'),
        onError: (error) => setProblem(toProblem(error)),
      })
      return
    }
    const body = updateBody(values, engagement, locked)
    if (Object.keys(body).length === 0) {
      onClose()
      return
    }
    update.mutate(body, {
      onSuccess: (saved) => done(saved, 'Incarico salvato'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const fieldError = problem === null ? null : fieldErrorFrom(problem)
  const errorOn = (field: string): string | null =>
    errors[field] ?? (fieldError?.field === field ? fieldError.message : null)
  // A refusal that names no field of this form (a 409, a closed engagement) is read inside the dialog.
  const elsewhere = problem !== null && (fieldError === null || !DRAWN_FIELDS.includes(fieldError.field))

  return (
    <form onSubmit={submit} noValidate className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-4">
        {engagement === undefined ? null : <WarningsBand warnings={engagement.warnings ?? []} />}
        {elsewhere ? <QueryErrorBanner error={problem} /> : null}

        <div className="grid gap-x-8 gap-y-4 lg:grid-cols-2">
          <div className="space-y-4">
            <Field id="engagement-freelancer" label="Freelance" error={errorOn('freelancer_id')}>
              <OrganizationPicker
                id="engagement-freelancer"
                role="freelancer"
                value={values.freelancerId}
                selectedName={engagement?.freelancer_name ?? undefined}
                disabled={lockedFreelancer}
                onChange={(organization) => set('freelancerId', organization?.id ?? null)}
                placeholder="Cerca un freelance per nome, P.IVA o email…"
                ariaLabel="Cerca un freelance"
              />
            </Field>
            <Field id="engagement-client" label="Cliente" error={errorOn('client_organization_id')}>
              <OrganizationPicker
                id="engagement-client"
                value={clientId}
                disabled={fixedDealId !== undefined}
                onChange={(client) =>
                  setValues((previous) => ({
                    ...previous,
                    clientId: client?.id ?? null,
                    // A deal or a contract of the previous client is not this client's.
                    dealId: '',
                    contractId: '',
                  }))
                }
              />
            </Field>
            {fixedDealId !== undefined ? (
              <Field id="engagement-deal" label="Deal" error={errorOn('deal_id')}>
                <Input
                  id="engagement-deal"
                  value={fixedDeal.data?.name ?? 'Caricamento…'}
                  readOnly
                  disabled
                  aria-label="Deal"
                />
              </Field>
            ) : values.clientId !== null && modules.on('deals') ? (
              <Field id="engagement-deal" label="Deal" error={errorOn('deal_id')}>
                <DealSelect
                  customerId={values.clientId}
                  value={values.dealId}
                  onChange={(id) => set('dealId', id)}
                  disabled={false}
                />
              </Field>
            ) : null}
            {clientId !== null && modules.on('contracts') ? (
              <Field id="engagement-contract" label="Contratto" error={errorOn('contract_id')}>
                <ContractSelect
                  customerId={clientId}
                  value={values.contractId}
                  onChange={(id) => set('contractId', id)}
                  disabled={false}
                />
              </Field>
            ) : null}
            <Field id="engagement-role" label="Ruolo" error={errorOn('role')}>
              <Input
                id="engagement-role"
                value={values.role}
                maxLength={200}
                disabled={locked}
                aria-invalid={errorOn('role') !== null}
                placeholder="Backend developer"
                onChange={(event) => set('role', event.target.value)}
              />
            </Field>
            <Field id="engagement-starts-on" label="Inizio" error={errorOn('starts_on')}>
              <Input
                id="engagement-starts-on"
                type="date"
                value={values.startsOn}
                disabled={locked}
                aria-invalid={errorOn('starts_on') !== null}
                onChange={(event) => set('startsOn', event.target.value)}
              />
            </Field>
            <Field
              id="engagement-ends-on"
              label="Fine"
              error={errorOn('ends_on')}
              hint="Vuota: senza data di fine."
            >
              <Input
                id="engagement-ends-on"
                type="date"
                value={values.endsOn}
                disabled={locked}
                aria-invalid={errorOn('ends_on') !== null}
                onChange={(event) => set('endsOn', event.target.value)}
              />
            </Field>
          </div>

          <div className="space-y-4">
            <Field id="engagement-days-per-week" label="Giorni a settimana" error={errorOn('days_per_week')}>
              <Select
                value={values.daysPerWeek}
                onValueChange={(next) => set('daysPerWeek', next)}
              >
                <SelectTrigger id="engagement-days-per-week" className="w-full">
                  <SelectValue placeholder="Scegli…" />
                </SelectTrigger>
                <SelectContent>
                  {DAYS_PER_WEEK.map((days) => (
                    <SelectItem key={days} value={days}>
                      {days.replace('.', ',')}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field id="engagement-fee" label="Compenso" error={errorOn('fee') ?? errorOn('fee_unit')}>
              <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2">
                <Input
                  id="engagement-fee"
                  inputMode="decimal"
                  value={values.fee}
                  disabled={locked}
                  aria-label="Compenso in euro"
                  aria-invalid={errorOn('fee') !== null}
                  placeholder="450,00"
                  className="tabular-nums"
                  onChange={(event) => set('fee', event.target.value)}
                />
                <Select value={values.feeUnit} onValueChange={(next) => set('feeUnit', next)} disabled={locked}>
                  <SelectTrigger className="w-full" aria-label="Unità del compenso">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FEE_UNITS.map((unit) => (
                      <SelectItem key={unit.value} value={unit.value}>
                        {unit.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </Field>
            <Field
              id="engagement-expected-days"
              label="Giorni previsti"
              error={errorOn('expected_days')}
              hint="Con un compenso all’ora, le ore divise per 8."
            >
              <Input
                id="engagement-expected-days"
                inputMode="decimal"
                value={values.expectedDays}
                disabled={locked}
                aria-invalid={errorOn('expected_days') !== null}
                className="tabular-nums"
                onChange={(event) => set('expectedDays', event.target.value)}
              />
            </Field>
            <Field
              id="engagement-letter-type"
              label="Tipo di lettera"
              error={errorOn('letter_type')}
              hint="Vuoto: segue il tipo fiscale del freelance."
            >
              <Select
                value={values.letterType === '' ? NONE : values.letterType}
                onValueChange={(next) => set('letterType', next === NONE ? '' : next)}
                disabled={locked}
              >
                <SelectTrigger id="engagement-letter-type" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>Come il tipo fiscale</SelectItem>
                  {LETTER_TYPES.map((type) => (
                    <SelectItem key={type.value} value={type.value}>
                      {type.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field id="engagement-payment-rule" label="Pagamento" error={errorOn('payment_rule')}>
              <Select
                value={values.paymentRule}
                onValueChange={(next) => set('paymentRule', next)}
                disabled={locked}
              >
                <SelectTrigger id="engagement-payment-rule" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAYMENT_RULES.map((rule) => (
                    <SelectItem key={rule.value} value={rule.value}>
                      {rule.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {values.paymentRule === 'fixed_term' ? (
              <Field id="engagement-payment-days" label="Giorni dalla fattura" error={errorOn('payment_days')}>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                  <Input
                    id="engagement-payment-days"
                    inputMode="numeric"
                    value={values.paymentDays}
                    disabled={locked}
                    aria-invalid={errorOn('payment_days') !== null}
                    placeholder="30"
                    className="w-24 tabular-nums"
                    onChange={(event) => set('paymentDays', event.target.value)}
                  />
                  <div className="flex items-center gap-2">
                    <Checkbox
                      id="engagement-end-of-month"
                      checked={values.endOfMonth}
                      disabled={locked}
                      onCheckedChange={(checked) => set('endOfMonth', checked === true)}
                    />
                    <Label htmlFor="engagement-end-of-month" className="font-normal">
                      A fine mese
                    </Label>
                  </div>
                </div>
              </Field>
            ) : (
              <Field
                id="engagement-collection-days"
                label="Giorni dall’incasso"
                error={errorOn('collection_days')}
                hint="Dopo che il cliente ha pagato rebase. Vuoto: appena rebase incassa."
              >
                <Input
                  id="engagement-collection-days"
                  inputMode="numeric"
                  value={values.collectionDays}
                  disabled={locked}
                  aria-invalid={errorOn('collection_days') !== null}
                  className="w-24 tabular-nums"
                  onChange={(event) => set('collectionDays', event.target.value)}
                />
              </Field>
            )}
          </div>
        </div>

        <section aria-label="Disponibilità del freelance" className="space-y-2">
          <h3 className="text-sm font-medium">Disponibilità del freelance</h3>
          <FreelancerAvailability
            freelancerId={values.freelancerId}
            dateFrom={ISO_DAY.test(values.startsOn) ? values.startsOn : undefined}
            excludingEngagementId={engagement?.id}
          />
        </section>
      </div>

      <DialogFooter className="mx-0 mb-0 shrink-0 px-6">
        <Button type="button" variant="ghost" onClick={onClose}>
          Annulla
        </Button>
        <Button type="submit" disabled={pending}>
          {engagement === undefined
            ? pending
              ? 'Creo…'
              : 'Crea la bozza'
            : pending
              ? 'Salvo…'
              : 'Salva'}
        </Button>
      </DialogFooter>
    </form>
  )
}

/**
 * «Nuovo incarico» and «Modifica» (REB-1035, the approved brief): one dense two-column dialog,
 * label beside the field, one column on the phone with the action bar always in view. A new
 * engagement is a draft; its letter is written and sent from its own page. With `engagement` the
 * dialog edits: a draft changes freely, a letter that has gone out only the days a week and the
 * client, deal and contract (the rest reads as it is). `freelancerId` fixes the freelancer, as the
 * registry's own page does, and `prefill` fills a new one in from the hub's «Crea match». The overlap and over-capacity sentences are the server's, and so is
 * every refusal: a field error sits under its field, any other in the dialog. Decimals travel as
 * the strings they were typed as; nothing is computed here.
 */
export function EngagementFormDialog({
  open,
  onOpenChange,
  engagement,
  freelancerId,
  prefill,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  engagement?: Engagement
  freelancerId?: string
  prefill?: EngagementPrefill
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[92dvh] w-full max-w-[calc(100%-1rem)] flex-col gap-0 p-0 sm:max-w-5xl">
        <DialogHeader className="shrink-0 px-6 pt-6">
          <DialogTitle>{engagement === undefined ? 'Nuovo incarico' : 'Modifica l’incarico'}</DialogTitle>
          <DialogDescription>
            {engagement === undefined
              ? 'Si crea come bozza: la lettera si scrive e si invia dalla pagina dell’incarico.'
              : engagement.state === 'draft' && !engagement.redacted
                ? 'Una bozza si cambia in ogni parte.'
                : 'La lettera è già partita: si cambiano solo i giorni a settimana e i collegamenti a cliente, deal e contratto.'}
          </DialogDescription>
        </DialogHeader>
        <EngagementForm
          engagement={engagement}
          freelancerId={freelancerId}
          prefill={prefill}
          onClose={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  )
}
