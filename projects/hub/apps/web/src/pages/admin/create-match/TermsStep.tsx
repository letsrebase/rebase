import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import type { Regime } from '@/lib/api'
import {
  TO_BODY_FIELDS,
  OTHER_TERMS_GROUPS,
  LETTER_LABELS,
  LETTER_MULTILINE,
  LETTER_REQUIRED,
  feeHint,
  payModeOf,
  switchPayMode,
  type Failure,
  type LetterForm,
  type LetterTextFieldKey,
  type PayMode,
} from '@/lib/contracts'
import { StepFooter } from './StepFooter'

const PAY_MODES: readonly { mode: PayMode; label: string; per: string }[] = [
  { mode: 'to_work_unit', label: 'A giornata', per: 'al giorno' },
  { mode: 'to_body', label: 'A corpo', per: 'in tutto' },
]

type Invalid = (field: string) => true | undefined

/** One text, date or number field of the letter, labelled as `LETTER_LABELS` names it.
 *  No `min` or other constraint the browser checks: inside a closed «Altre condizioni»
 *  it would block «Avanti» with no bubble to see, and the server names a wrong value. */
function LetterInput({
  field,
  form,
  onChange,
  invalid,
}: {
  field: LetterTextFieldKey
  form: LetterForm
  onChange: (form: LetterForm) => void
  invalid: Invalid
}) {
  const inputId = `lettera-${field}`
  const set = (next: string) => onChange({ ...form, [field]: next })
  const type = field === 'start_date' || field === 'end_date' ? 'date' : field === 'notice_days' ? 'number' : 'text'
  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{LETTER_LABELS[field]}</Label>
      {LETTER_MULTILINE.has(field) ? (
        <Textarea
          id={inputId}
          rows={3}
          required={LETTER_REQUIRED.has(field)}
          value={form[field]}
          onChange={(event) => set(event.target.value)}
          aria-invalid={invalid(field)}
        />
      ) : (
        <Input
          id={inputId}
          type={type}
          required={LETTER_REQUIRED.has(field)}
          value={form[field]}
          onChange={(event) => set(event.target.value)}
          aria-invalid={invalid(field)}
        />
      )}
    </div>
  )
}

/** «Come si paga» sets `mode` and `unit` together; «A corpo» asks what it adds,
 *  and the total rather than the day rate (`switchPayMode`). The fee's hint follows the
 *  letter type (REB-687). */
function Payment({
  form,
  onChange,
  dayRate,
  regime,
  invalid,
}: {
  form: LetterForm
  onChange: (form: LetterForm) => void
  dayRate: string
  regime: Regime
  invalid: Invalid
}) {
  const mode = payModeOf(form)
  const per = PAY_MODES.find((option) => option.mode === mode)!.per
  return (
    <>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Come si paga</legend>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {PAY_MODES.map((option) => (
            <Label key={option.mode} className="font-normal">
              <input
                type="radio"
                name="letter-payment"
                value={option.mode}
                checked={mode === option.mode}
                onChange={() => onChange(switchPayMode(form, option.mode, dayRate))}
                className="size-4 shrink-0 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              />
              {option.label}
            </Label>
          ))}
        </div>
      </fieldset>
      <div className="space-y-1.5">
        <Label htmlFor="letter-fee">{LETTER_LABELS.fee}</Label>
        <div className="flex items-center gap-2">
          <Input
            id="letter-fee"
            inputMode="decimal"
            required
            className="max-w-40"
            value={form.fee}
            onChange={(event) => onChange({ ...form, fee: event.target.value })}
            aria-invalid={invalid('fee')}
            aria-describedby="letter-fee-per letter-fee-vat"
          />
          <span id="letter-fee-per" className="text-sm text-muted-foreground">
            {per}
          </span>
        </div>
        <p id="letter-fee-vat" className="text-sm text-muted-foreground">
          {feeHint(regime)}
        </p>
      </div>
      {mode === 'to_body' &&
        TO_BODY_FIELDS.map((field) => (
          <LetterInput key={field} field={field} form={form} onChange={onChange} invalid={invalid} />
        ))}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {/* The number sits inside its sentence, «Pagamento a 30 giorni», which is also
         *  its accessible name. */}
        <Label className="font-normal">
          <span>Pagamento a</span>
          <Input
            id="letter-payment-days"
            type="number"
            min={0}
            required
            className="w-20"
            value={form.payment_days}
            onChange={(event) => onChange({ ...form, payment_days: event.target.value })}
            aria-invalid={invalid('payment_days')}
          />
          <span>giorni</span>
        </Label>
        <div className="flex items-center gap-2">
          <Checkbox
            id="letter-month-end"
            checked={form.month_end}
            onCheckedChange={(checked) => onChange({ ...form, month_end: checked === true })}
          />
          <Label htmlFor="letter-month-end" className="font-normal">
            {LETTER_LABELS.month_end}
          </Label>
        </div>
      </div>
    </>
  )
}

/** «Giorni previsti» (REB-497): the match's, not the letter's, so it sits after the fee
 *  but outside `LetterForm`. Optional. In the open, unlike «Altre condizioni», so the
 *  browser can check the column's bounds, and its default step of one refuses a fraction,
 *  before the request; the server's own sentence stays the backstop (REB-502). */
function ExpectedDays({
  value,
  onChange,
  invalid,
}: {
  value: string
  onChange: (value: string) => void
  invalid: Invalid
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor="match-expected-days">Giorni previsti</Label>
      <Input
        id="match-expected-days"
        type="number"
        inputMode="numeric"
        min={1}
        max={366}
        className="w-24"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={invalid('expected_days')}
        aria-describedby="match-expected-days-help"
      />
      <p id="match-expected-days-help" className="text-sm text-muted-foreground">
        Per il consuntivo: 8 ore al giorno. Il testo della lettera resta quello di «Impegno».
      </p>
    </div>
  )
}

/** Step 2: what changes from one engagement to the next, prefilled, in the open, with the
 *  expected days after the fee; every other field of the letter, none of them required,
 *  in a closed «Altre condizioni». */
export function TermsStep({
  form,
  onChange,
  expectedDays,
  onExpectedDays,
  dayRate,
  regime,
  otherOpen,
  onOtherOpen,
  onBack,
  onNext,
  pending,
  failure,
}: {
  form: LetterForm
  onChange: (form: LetterForm) => void
  /** «Giorni previsti» as typed: `MatchCreate`'s, beside the letter's form. */
  expectedDays: string
  onExpectedDays: (value: string) => void
  /** The freelancer's day rate as the prefill wrote it into the fee. */
  dayRate: string
  /** «Tipo di lettera» from step 1 (REB-687). */
  regime: Regime
  otherOpen: boolean
  onOtherOpen: (open: boolean) => void
  onBack: () => void
  onNext: () => void
  pending: boolean
  failure: Failure | null
}) {
  const invalid: Invalid = (field) => failure?.fields.includes(field) || undefined
  const input = (field: LetterTextFieldKey) => (
    <LetterInput key={field} field={field} form={form} onChange={onChange} invalid={invalid} />
  )
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onNext()
      }}
    >
      {input('role')}
      {input('activities')}
      <div className="grid gap-3 sm:grid-cols-2">
        {input('start_date')}
        {input('end_date')}
      </div>
      {input('commitment')}
      {input('location')}
      <Payment form={form} onChange={onChange} dayRate={dayRate} regime={regime} invalid={invalid} />
      <ExpectedDays value={expectedDays} onChange={onExpectedDays} invalid={invalid} />
      <details
        open={otherOpen}
        onToggle={(event) => onOtherOpen(event.currentTarget.open)}
        className="border"
      >
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium">Altre condizioni (facoltative)</summary>
        <div className="space-y-6 border-t px-4 py-4">
          {OTHER_TERMS_GROUPS.map((group) => (
            <fieldset key={group.title} className="space-y-3">
              <legend className="text-sm font-medium">{group.title}</legend>
              {group.fields.map(input)}
            </fieldset>
          ))}
        </div>
      </details>
      <StepFooter onBack={onBack} next="Avanti" pending={pending} failure={failure} />
    </form>
  )
}
