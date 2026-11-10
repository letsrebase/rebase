import { Plus, X } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { emptyVatLine, isZeroRate, type VatLineDraft } from './formValues'

/**
 * The VAT recap of an invoice or a credit note: one row per rate, as the document prints it
 * («riepilogo IVA»). Every figure is the text typed and stays text -- the server checks that
 * the lines add up to the total and that a zero-rate line names its nature; nothing here
 * computes a figure.
 *
 * The nature is asked only when the rate is zero, which is the only case the server accepts
 * it for.
 */
export function VatLinesEditor({
  lines,
  onChange,
  error,
}: {
  lines: VatLineDraft[]
  onChange: (lines: VatLineDraft[]) => void
  error?: string
}) {
  function patch(index: number, change: Partial<VatLineDraft>) {
    onChange(lines.map((line, i) => (i === index ? { ...line, ...change } : line)))
  }

  return (
    <fieldset className="@container min-w-0 space-y-3">
      <legend className="text-sm font-medium">Riepilogo IVA</legend>
      {lines.map((line, index) => {
        const n = index + 1
        return (
          // Rows have no identity of their own and are only ever added or removed at the
          // end of an edit, so the position is the key.
          <div
            key={index}
            className="grid grid-cols-2 items-end gap-3 @xl:grid-cols-[6rem_minmax(0,1fr)_minmax(0,1fr)_6rem_auto]"
          >
            <div className="space-y-2">
              <Label htmlFor={`vat-rate-${n}`}>Aliquota (%)</Label>
              <Input
                id={`vat-rate-${n}`}
                inputMode="decimal"
                aria-label={`Aliquota IVA, riga ${n}`}
                value={line.rate}
                onChange={(event) => patch(index, { rate: event.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`vat-taxable-${n}`}>Imponibile (€)</Label>
              <Input
                id={`vat-taxable-${n}`}
                inputMode="decimal"
                aria-label={`Imponibile, riga ${n}`}
                value={line.taxable_amount}
                onChange={(event) => patch(index, { taxable_amount: event.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`vat-amount-${n}`}>IVA (€)</Label>
              <Input
                id={`vat-amount-${n}`}
                inputMode="decimal"
                aria-label={`IVA, riga ${n}`}
                value={line.vat_amount}
                onChange={(event) => patch(index, { vat_amount: event.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`vat-share-${n}`}>Detraibile (%)</Label>
              <Input
                id={`vat-share-${n}`}
                inputMode="decimal"
                aria-label={`Quota detraibile, riga ${n}`}
                value={line.deductible_share}
                onChange={(event) => patch(index, { deductible_share: event.target.value })}
              />
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              className="col-span-2 justify-self-end @xl:col-span-1"
              aria-label={`Rimuovi la riga IVA ${n}`}
              onClick={() => onChange(lines.filter((_, i) => i !== index))}
            >
              <X className="size-4" />
            </Button>
            {isZeroRate(line.rate) && (
              <div className="col-span-2 space-y-2 @xl:col-span-5">
                <Label htmlFor={`vat-nature-${n}`}>Natura (es. N2.2)</Label>
                <Input
                  id={`vat-nature-${n}`}
                  aria-label={`Natura, riga ${n}`}
                  value={line.nature}
                  onChange={(event) => patch(index, { nature: event.target.value })}
                />
              </div>
            )}
          </div>
        )
      })}
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button type="button" variant="outline" size="sm" onClick={() => onChange([...lines, emptyVatLine()])}>
        <Plus className="mr-2 size-4" />
        Aggiungi riga IVA
      </Button>
    </fieldset>
  )
}
