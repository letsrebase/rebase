import { Download, FileSpreadsheet, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Skeleton } from '@rebase/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { formatDate } from '@/features/invoices/format'
import { useCan } from '@/lib/auth'
import { toIsoMonth } from '@/lib/dates'
import {
  monthBounds,
  registerCsvUrl,
  useContractRegister,
  useRenderRegisterPdf,
  type RegisterEntry,
} from './billingQueries'

/** The states a day on an invoice can be in now: billed, worked again after a dispute, disputed, paid. */
const STATE_LABEL: Record<string, string> = {
  lavorato: 'Lavorato',
  fatturato: 'Fatturato',
  contestato: 'Contestato',
  pagato: 'Pagato',
}

/** What the proof cell says on screen: the channel and who sent it. The files carry the
 *  whole reference (day and message id); a table cell has no room for it, and the page is
 *  not the document. */
function proof(entry: RegisterEntry): string {
  return entry.approvazione === null || entry.approvazione === undefined
    ? '—'
    : `${entry.approvazione.canale} · ${entry.approvazione.mittente}`
}

/**
 * The «Registro giornate» section of a contract's page (REB-752): the days billed in a
 * month, each with its invoice and the proof of approval behind it, and the two files a
 * client asks for next to an invoice. The PDF is archived as the customer's document (so a
 * regenerated one is a new version) and the CSV is a working copy. Both are the server's
 * own rendering of the rows shown here, so the total on screen, in the PDF and in the CSV
 * is one number, and it is the invoice's.
 */
export function RegisterSection({ contractId }: { contractId: string }) {
  const canRender = useCan('render_contract_register')
  // `toIsoMonth`, never `toISOString().slice(0, 7)` -- see @/lib/dates for why.
  const [month, setMonth] = useState(() => toIsoMonth(new Date()))
  // Cleared with the keyboard, the field is empty: nothing to ask for until it is a month.
  const validMonth = /^\d{4}-\d{2}$/.test(month)
  const period = monthBounds(validMonth ? month : toIsoMonth(new Date()))
  const register = useContractRegister(contractId, period, validMonth)
  const render = useRenderRegisterPdf(contractId)

  return (
    <Card>
      <CardContent className="pt-6">
        <h2 className="mb-3 font-semibold">Registro giornate</h2>

        <div className="mb-4 flex flex-wrap items-end gap-3">
          <div className="space-y-2">
            <Label htmlFor="register-month">Mese</Label>
            <Input
              id="register-month"
              type="month"
              value={month}
              onChange={(event) => setMonth(event.target.value)}
              className="w-40"
            />
          </div>
          {canRender && (
            <Button
              variant="outline"
              disabled={render.isPending || !validMonth}
              onClick={() =>
                render.mutate(period, {
                  onSuccess: () => toast.success('Registro archiviato e scaricato'),
                })
              }
            >
              {render.isPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Download className="mr-2 size-4" aria-hidden="true" />
              )}
              PDF
            </Button>
          )}
          <Button asChild variant="outline">
            <a
              // Snyk Code javascript/DOMXSS here is a false positive: always a same-origin
              // path under /api/contracts/, with the period passed through URLSearchParams.
              href={registerCsvUrl(contractId, period)}
            >
              <FileSpreadsheet className="mr-2 size-4" aria-hidden="true" />
              CSV
            </a>
          </Button>
        </div>
        {render.isError && <QueryErrorBanner error={render.error} />}

        {register.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : register.isError ? (
          <QueryErrorBanner error={register.error} />
        ) : register.data === undefined ? null : register.data.voci.length === 0 ? (
          <p className="text-muted-foreground">Nessuna giornata fatturata in questo mese.</p>
        ) : (
          <div className="space-y-3">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Data</TableHead>
                  <TableHead className="text-right">Quantità</TableHead>
                  <TableHead>Descrizione</TableHead>
                  <TableHead>Stato</TableHead>
                  <TableHead>Fattura</TableHead>
                  <TableHead>Approvazione</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {register.data.voci.map((entry) => (
                  <TableRow key={entry.work_unit_id}>
                    <TableCell>{formatDate(entry.data)}</TableCell>
                    <TableCell className="text-right tabular-nums">{entry.quantita}</TableCell>
                    <TableCell>{entry.descrizione}</TableCell>
                    <TableCell>{STATE_LABEL[entry.stato] ?? entry.stato}</TableCell>
                    <TableCell>{entry.numero_fattura ?? 'Bozza'}</TableCell>
                    <TableCell>{proof(entry)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="text-right font-semibold">Totale: {register.data.totale_quantita}</p>
            {register.data.senza_approvazione > 0 && (
              <p className="text-sm text-muted-foreground">
                {register.data.senza_approvazione === 1
                  ? '1 giornata è nel registro senza approvazione'
                  : `${register.data.senza_approvazione} giornate sono nel registro senza approvazione`}
                : il contratto non la richiede, oppure non è stata ancora registrata.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
