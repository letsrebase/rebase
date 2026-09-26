import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import {
  A_CORPO_FIELDS,
  ALTRE_CONDIZIONI_GROUPS,
  LETTERA_LABELS,
  LETTERA_MULTILINE,
  LETTERA_REQUIRED,
  payModeOf,
  switchPayMode,
  type Failure,
  type LetteraForm,
  type LetteraTextFieldKey,
  type PayMode,
} from '@/lib/contracts'
import { StepFooter } from './StepFooter'

const PAY_MODES: readonly { mode: PayMode; label: string; per: string }[] = [
  { mode: 'a giornata', label: 'A giornata', per: 'al giorno' },
  { mode: 'a corpo', label: 'A corpo', per: 'in tutto' },
]

type Invalid = (field: string) => true | undefined

/** One text, date or number field of the letter, labelled as `LETTERA_LABELS` names it.
 *  No `min` or other constraint the browser checks: inside a closed «Altre condizioni»
 *  it would block «Avanti» with no bubble to see, and the server names a wrong value. */
function LetteraInput({
  field,
  form,
  onChange,
  invalid,
}: {
  field: LetteraTextFieldKey
  form: LetteraForm
  onChange: (form: LetteraForm) => void
  invalid: Invalid
}) {
  const inputId = `lettera-${field}`
  const set = (next: string) => onChange({ ...form, [field]: next })
  const type = field === 'data_inizio' || field === 'data_fine' ? 'date' : field === 'giorni_preavviso' ? 'number' : 'text'
  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{LETTERA_LABELS[field]}</Label>
      {LETTERA_MULTILINE.has(field) ? (
        <Textarea
          id={inputId}
          rows={3}
          required={LETTERA_REQUIRED.has(field)}
          value={form[field]}
          onChange={(event) => set(event.target.value)}
          aria-invalid={invalid(field)}
        />
      ) : (
        <Input
          id={inputId}
          type={type}
          required={LETTERA_REQUIRED.has(field)}
          value={form[field]}
          onChange={(event) => set(event.target.value)}
          aria-invalid={invalid(field)}
        />
      )}
    </div>
  )
}

/** «Come si paga» sets `modalita` and `unita` together; «A corpo» asks what it adds,
 *  and the total rather than the day rate (`switchPayMode`). */
function Pagamento({
  form,
  onChange,
  dayRate,
  invalid,
}: {
  form: LetteraForm
  onChange: (form: LetteraForm) => void
  dayRate: string
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
                name="lettera-pagamento"
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
        <Label htmlFor="lettera-compenso">{LETTERA_LABELS.compenso}</Label>
        <div className="flex items-center gap-2">
          <Input
            id="lettera-compenso"
            inputMode="decimal"
            required
            className="max-w-40"
            value={form.compenso}
            onChange={(event) => onChange({ ...form, compenso: event.target.value })}
            aria-invalid={invalid('compenso')}
            aria-describedby="lettera-compenso-per"
          />
          <span id="lettera-compenso-per" className="text-sm text-muted-foreground">
            {per}
          </span>
        </div>
      </div>
      {mode === 'a corpo' &&
        A_CORPO_FIELDS.map((field) => (
          <LetteraInput key={field} field={field} form={form} onChange={onChange} invalid={invalid} />
        ))}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {/* The number sits inside its sentence, «Pagamento a 30 giorni», which is also
         *  its accessible name. */}
        <Label className="font-normal">
          <span>Pagamento a</span>
          <Input
            id="lettera-giorni_pagamento"
            type="number"
            min={0}
            required
            className="w-20"
            value={form.giorni_pagamento}
            onChange={(event) => onChange({ ...form, giorni_pagamento: event.target.value })}
            aria-invalid={invalid('giorni_pagamento')}
          />
          <span>giorni</span>
        </Label>
        <div className="flex items-center gap-2">
          <Checkbox
            id="lettera-fine_mese"
            checked={form.fine_mese}
            onCheckedChange={(checked) => onChange({ ...form, fine_mese: checked === true })}
          />
          <Label htmlFor="lettera-fine_mese" className="font-normal">
            {LETTERA_LABELS.fine_mese}
          </Label>
        </div>
      </div>
    </>
  )
}

/** «Giorni previsti» (REB-497): the match's, not the letter's, so it sits after the fee
 *  but outside `LetteraForm`. Optional, and no `min` or `max` the browser checks: the
 *  server names a wrong value, as it does for the letter's own numbers. */
function GiorniPrevisti({
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
      <Label htmlFor="match-giorni_previsti">Giorni previsti</Label>
      <Input
        id="match-giorni_previsti"
        type="number"
        inputMode="numeric"
        className="w-24"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={invalid('giorni_previsti')}
        aria-describedby="match-giorni_previsti-aiuto"
      />
      <p id="match-giorni_previsti-aiuto" className="text-sm text-muted-foreground">
        Per il consuntivo: 8 ore al giorno. Il testo della lettera resta quello di «Impegno».
      </p>
    </div>
  )
}

/** Step 2: what changes from one engagement to the next, prefilled, in the open, with the
 *  expected days after the fee; every other field of the letter, none of them required,
 *  in a closed «Altre condizioni». */
export function CondizioniStep({
  form,
  onChange,
  giorniPrevisti,
  onGiorniPrevisti,
  dayRate,
  altreOpen,
  onAltreOpen,
  onBack,
  onNext,
  pending,
  failure,
}: {
  form: LetteraForm
  onChange: (form: LetteraForm) => void
  /** «Giorni previsti» as typed: `MatchCreate`'s, beside the letter's form. */
  giorniPrevisti: string
  onGiorniPrevisti: (value: string) => void
  /** The freelancer's day rate as the prefill wrote it into the fee. */
  dayRate: string
  altreOpen: boolean
  onAltreOpen: (open: boolean) => void
  onBack: () => void
  onNext: () => void
  pending: boolean
  failure: Failure | null
}) {
  const invalid: Invalid = (field) => failure?.fields.includes(field) || undefined
  const input = (field: LetteraTextFieldKey) => (
    <LetteraInput key={field} field={field} form={form} onChange={onChange} invalid={invalid} />
  )
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onNext()
      }}
    >
      {input('ruolo')}
      {input('attivita')}
      <div className="grid gap-3 sm:grid-cols-2">
        {input('data_inizio')}
        {input('data_fine')}
      </div>
      {input('impegno')}
      {input('luogo')}
      <Pagamento form={form} onChange={onChange} dayRate={dayRate} invalid={invalid} />
      <GiorniPrevisti value={giorniPrevisti} onChange={onGiorniPrevisti} invalid={invalid} />
      <details
        open={altreOpen}
        onToggle={(event) => onAltreOpen(event.currentTarget.open)}
        className="border"
      >
        <summary className="cursor-pointer px-4 py-3 text-sm font-medium">Altre condizioni (facoltative)</summary>
        <div className="space-y-6 border-t px-4 py-4">
          {ALTRE_CONDIZIONI_GROUPS.map((group) => (
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
