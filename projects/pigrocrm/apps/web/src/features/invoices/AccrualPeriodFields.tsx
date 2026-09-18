import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import type { AccrualPeriodDraft } from './accrualPeriod'

/**
 * «Competenza dal … al …»: the two date inputs of an invoice's accrual period (ORB-61),
 * shared by the create dialog and the detail page's header editor so the pair is asked
 * the same way in both places.
 *
 * Two field blocks and no grid of their own: the caller lays them out beside whatever
 * else its row holds (the proforma's date, in both callers today) and renders the
 * error, because where a message belongs differs between a dialog and a section. Both
 * inputs are optional and are validated together by `validateAccrualPeriod`.
 *
 * The second input is labelled «al» on screen, which is how the phrase reads, and
 * «Competenza al» to a screen reader, since an `aria-label` wins over the `<label>`:
 * «al» alone, read out of context, names nothing.
 */
export function AccrualPeriodFields({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string
  value: AccrualPeriodDraft
  onChange: (next: AccrualPeriodDraft) => void
}) {
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor={`${idPrefix}-competenza-da`}>Competenza dal</Label>
        <Input
          id={`${idPrefix}-competenza-da`}
          type="date"
          value={value.competenza_da}
          onChange={(event) => onChange({ ...value, competenza_da: event.target.value })}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${idPrefix}-competenza-a`}>al</Label>
        <Input
          id={`${idPrefix}-competenza-a`}
          type="date"
          aria-label="Competenza al"
          value={value.competenza_a}
          onChange={(event) => onChange({ ...value, competenza_a: event.target.value })}
        />
      </div>
    </>
  )
}
