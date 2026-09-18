import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { emptyLine, type DraftLine } from './lineDraft'
import { formatMoney, sumLineTotals } from './format'
import { useReplaceLines, type Invoice, type InvoiceLine, type InvoiceLineInput } from './queries'

function draftFrom(line: InvoiceLine): DraftLine {
  return {
    descrizione: line.descrizione,
    quantita: line.quantita,
    unita_misura: line.unita_misura ?? '',
    prezzo_unitario: line.prezzo_unitario,
    sconto_percentuale: line.sconto_percentuale ?? '',
    sconto_importo: line.sconto_importo ?? '',
  }
}

/**
 * An emptied optional field becomes `null`, never an omitted key.
 *
 * `replace_lines` swaps the whole set, so a key left out is a value the caller never
 * mentioned rather than one they cleared — and the row would keep its old unit or its
 * old discount while the screen showed the field empty. That is the same defect the
 * entity forms had to fix, and it is why `''` maps to `null` here explicitly.
 *
 * `'0'` is *not* empty. A zero discount is a value, exactly as `is_blank` treats `0`
 * and `false` as values on the backend, and it must survive the round trip as `0`.
 */
function optional(value: string): string | null {
  return value.trim() === '' ? null : value.trim()
}

function toInput(draft: DraftLine): InvoiceLineInput {
  return {
    descrizione: draft.descrizione,
    quantita: draft.quantita,
    unita_misura: optional(draft.unita_misura),
    prezzo_unitario: draft.prezzo_unitario,
    sconto_percentuale: optional(draft.sconto_percentuale),
    sconto_importo: optional(draft.sconto_importo),
  } as InvoiceLineInput
}

export function InvoiceLinesEditor({
  invoice,
  lines,
  readOnly,
}: {
  invoice: Invoice
  lines: InvoiceLine[]
  readOnly: boolean
}) {
  const [draft, setDraft] = useState<DraftLine[]>(() => lines.map(draftFrom))
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const replace = useReplaceLines(invoice.id)

  function update(index: number, field: keyof DraftLine, value: string) {
    setDraft((previous) =>
      previous.map((row, position) => (position === index ? { ...row, [field]: value } : row)),
    )
  }

  function submit() {
    setProblem(null)
    replace.mutate(draft.map(toInput), {
      onSuccess: () => toast.success('Righe salvate'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  // An issued invoice's lines are immutable, and the guard is the row's own state, not
  // a prop a caller chose: the backend refuses the write either way, so rendering
  // inputs would only invite an edit that cannot be saved.
  if (readOnly) {
    return (
      <div className="space-y-3">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-muted-foreground text-left">
              <th className="pb-2 font-medium">Descrizione</th>
              <th className="pb-2 font-medium">Quantità</th>
              <th className="pb-2 font-medium">Unità</th>
              <th className="pb-2 font-medium">Prezzo</th>
              <th className="pb-2 text-right font-medium">Totale</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.id} className="border-t">
                <td className="py-2">{line.descrizione}</td>
                <td className="py-2">{line.quantita}</td>
                <td className="py-2">{line.unita_misura ?? '—'}</td>
                <td className="py-2">{formatMoney(line.prezzo_unitario)}</td>
                <td className="py-2 text-right">{formatMoney(line.prezzo_totale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {problem ? <QueryErrorBanner error={problem} /> : null}

      {draft.map((row, index) => (
        <div key={index} className="grid items-end gap-2 sm:grid-cols-12">
          <div className="space-y-1 sm:col-span-4">
            <Label htmlFor={`riga-descrizione-${index}`} className="text-xs">
              Descrizione
            </Label>
            <Input
              id={`riga-descrizione-${index}`}
              value={row.descrizione}
              onChange={(event) => update(index, 'descrizione', event.target.value)}
            />
          </div>
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor={`riga-quantita-${index}`} className="text-xs">
              Quantità
            </Label>
            <Input
              id={`riga-quantita-${index}`}
              value={row.quantita}
              onChange={(event) => update(index, 'quantita', event.target.value)}
            />
          </div>
          <div className="space-y-1 sm:col-span-1">
            <Label htmlFor={`riga-unita-${index}`} className="text-xs">
              Unità
            </Label>
            <Input
              id={`riga-unita-${index}`}
              value={row.unita_misura}
              onChange={(event) => update(index, 'unita_misura', event.target.value)}
            />
          </div>
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor={`riga-prezzo-${index}`} className="text-xs">
              Prezzo
            </Label>
            <Input
              id={`riga-prezzo-${index}`}
              value={row.prezzo_unitario}
              onChange={(event) => update(index, 'prezzo_unitario', event.target.value)}
            />
          </div>
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor={`riga-sconto-${index}`} className="text-xs">
              Sconto %
            </Label>
            <Input
              id={`riga-sconto-${index}`}
              value={row.sconto_percentuale}
              onChange={(event) => update(index, 'sconto_percentuale', event.target.value)}
            />
          </div>
          <div className="sm:col-span-1">
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Rimuovi riga ${index + 1}`}
              onClick={() =>
                setDraft((previous) => previous.filter((_, position) => position !== index))
              }
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        </div>
      ))}

      <div className="flex items-center justify-between">
        <Button
          variant="outline"
          size="sm"
          onClick={() => setDraft((previous) => [...previous, emptyLine()])}
        >
          <Plus className="mr-2 size-4" />
          Aggiungi riga
        </Button>

        <div className="flex items-center gap-4">
          {/* Explicitly a preview. The authoritative totals are the ones the API
              stored -- `sumLineTotals` adds integer cents purely so the editor can
              show a running figure before saving, and the label is what stops anyone
              reading it as the document's total. */}
          <span className="text-muted-foreground text-sm">
            Anteprima imponibile: {formatMoney(sumLineTotals(lines))}
          </span>
          <Button onClick={submit} disabled={replace.isPending}>
            Salva righe
          </Button>
        </div>
      </div>
    </div>
  )
}
