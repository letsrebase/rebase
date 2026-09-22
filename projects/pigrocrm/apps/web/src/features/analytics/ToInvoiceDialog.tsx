import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
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
import { Label } from '@rebase/ui/label'
import { Skeleton } from '@rebase/ui/skeleton'
import { formatHoursValue, formatMoneyValue } from '@/features/time/columns'
import { useTimeEntries } from '@/features/time/queries'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { formatIsoDateItalian } from '@/lib/dates'
import { useToInvoiceDraft } from './queries'

export interface ToInvoiceDialogProps {
  dealId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** The refusal as the server wrote it. `reason` is the sentence, `expected` is the list
 *  of entries it is about -- two separate fields of the problem document, and dropping
 *  the second would leave the user a count with nothing to act on. */
function refusalText(problem: ProblemDetail): string {
  return problem.expected ? `${problem.detail} — ${problem.expected}` : problem.detail
}

/**
 * Picks which billable, unbilled hours become the lines of a **draft** invoice.
 *
 * Every entry starts ticked, because that is the common case, but the list is explicit
 * and so is the request: `BindTimeRequest.entry_ids` takes ids rather than "everything
 * billable", since choosing which hours to invoice is a commercial decision and a
 * default of "all of them" is that decision made silently. Unticking one is how it gets
 * made out loud.
 *
 * Nothing is issued here. The draft still has to be checked and issued from the invoice
 * itself, where numbering, fiscal validation and freezing live.
 */
export function ToInvoiceDialog({ dealId, open, onOpenChange }: ToInvoiceDialogProps) {
  const entries = useTimeEntries({ deal_id: dealId, fatturabile: true, fatturato: false })
  const draft = useToInvoiceDraft(dealId)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [grouped, setGrouped] = useState(true)
  // `null` means "nobody has touched the selection yet", which is not the same as an
  // empty one: the default is every listed entry, and it cannot be computed until the
  // list has arrived. Storing the excluded ids instead would give the same answer today
  // and quietly re-tick a row that appeared in a later refetch.
  const [selected, setSelected] = useState<Set<string> | null>(null)

  const items = entries.data?.items ?? []
  const chosen = selected ?? new Set(items.map((item) => item.id))

  /** Every change to what is being asked for drops the previous refusal. The server's
   *  message names *which* entries it objected to, so it stops being true the moment
   *  the selection changes -- leaving it on screen would be a message about something
   *  the user is no longer doing, the stale-banner defect this codebase has already
   *  fixed once elsewhere. */
  function replaceSelection(next: Set<string>) {
    setProblem(null)
    setSelected(next)
  }

  function toggle(entryId: string) {
    const next = new Set(chosen)
    if (next.has(entryId)) next.delete(entryId)
    else next.add(entryId)
    replaceSelection(next)
  }

  // Order taken from the list, not from the Set: `entry_ids` reaches the server in the
  // order shown on screen, so a draft's lines follow the order the user was reading.
  const entryIds = items.filter((item) => chosen.has(item.id)).map((item) => item.id)

  function generate() {
    setProblem(null)
    draft.mutate(
      { entry_ids: entryIds, raggruppa_per_mese: grouped },
      {
        onSuccess: () => {
          onOpenChange(false)
          toast.success('Bozza di fattura creata')
        },
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Genera bozza di fattura</DialogTitle>
          <DialogDescription>
            Solo le ore fatturabili non ancora fatturate. La bozza non emette niente:
            l&apos;emissione resta un passaggio a parte, sulla fattura.
          </DialogDescription>
        </DialogHeader>

        {entries.isLoading && <Skeleton className="h-40 w-full" />}

        {/* A failed read gets the banner and nothing else. A Genera button over a list
            nobody could see would post a selection the user never had the chance to
            make. */}
        {entries.isError && <QueryErrorBanner error={entries.error} />}

        {!entries.isLoading && !entries.isError && items.length === 0 && (
          <p className="text-muted-foreground">
            Nessuna voce da fatturare: le ore fatturabili di questo deal sono già tutte su
            una fattura.
          </p>
        )}

        {!entries.isError && items.length > 0 && (
          <>
            <ul className="divide-y border">
              {items.map((item) => (
                <li key={item.id} className="flex items-center gap-3 px-3 py-2">
                  <Checkbox
                    id={`voce-${item.id}`}
                    checked={chosen.has(item.id)}
                    onCheckedChange={() => toggle(item.id)}
                    // The date and the description, because that is what tells two
                    // entries of the same deal apart when they are read out one by one.
                    aria-label={`${formatIsoDateItalian(item.data)} — ${item.descrizione}`}
                  />
                  <Label htmlFor={`voce-${item.id}`} className="flex-1 font-normal">
                    <span className="text-muted-foreground">
                      {formatIsoDateItalian(item.data)}
                    </span>{' '}
                    {item.descrizione}
                  </Label>
                  <span className="tabular-nums">{formatHoursValue(item.ore)}</span>
                  {/* Straight from the API, never hours × rate computed here. An entry
                      the backend could not price shows a dash, which is also the reason
                      the draft would be refused. */}
                  <span className="w-28 text-right tabular-nums">
                    {formatMoneyValue(item.valore_riga ?? null)}
                  </span>
                </li>
              ))}
            </ul>

            <div className="flex items-center gap-2">
              <Checkbox
                id="raggruppa-per-mese"
                checked={grouped}
                onCheckedChange={(checked) => {
                  setProblem(null)
                  setGrouped(checked === true)
                }}
              />
              <Label htmlFor="raggruppa-per-mese" className="font-normal">
                Raggruppa per mese
              </Label>
            </div>

            {problem && (
              <p
                role="alert"
                className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {refusalText(problem)}
              </p>
            )}

            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Annulla
              </Button>
              {/* `entry_ids` has `min_length=1`: an empty list comes back as a 422 the
                  user can do nothing with, so the button says so before the round trip. */}
              <Button onClick={generate} disabled={entryIds.length === 0 || draft.isPending}>
                Genera
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
