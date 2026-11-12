import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { NOT_COMPUTABLE, formatPercent } from '@/features/analytics/format'
import { money } from '@/features/dashboard/format'
import type { FiscalEstimate } from './labels'

type IrapRegion = NonNullable<NonNullable<FiscalEstimate['company']>['irap']['regions']>[number]

/**
 * An srl with people in several regions pays its IRAP split between them (REB-1092, art. 4
 * c. 2 D.Lgs. 446/1997): the share of each region, the base it carries, its rate and its tax.
 * Every figure is the server's; a rate or tax it could not give reads «non calcolabile».
 */
export function IrapRegions({ regions }: { regions: readonly IrapRegion[] }) {
  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">
        Ripartita su {regions.length} regioni, in base alle retribuzioni delle persone di ciascuna sede.
      </p>
      <div className="overflow-x-auto overflow-y-hidden border bg-card">
        <Table scrollLabel="Ripartizione dell'IRAP tra le regioni">
          <TableHeader>
            <TableRow>
              <TableHead>Regione</TableHead>
              <TableHead className="text-right">Imposta</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {regions.map((part) => (
              <TableRow key={part.region}>
                <TableCell className="whitespace-normal">
                  <span className="block">{part.name}</span>
                  <span className="block text-xs text-muted-foreground tabular-nums">
                    Quota {formatPercent(part.share)} · base {money(part.base)} · aliquota {formatPercent(part.rate)}
                  </span>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {part.amount === null ? NOT_COMPUTABLE : money(part.amount)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
