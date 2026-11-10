import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { documentLabel, specialChips } from '@/features/purchases/labels'
import { formatMoneyValue } from '@/features/time/columns'
import { formatIsoDateItalian } from '@/lib/dates'
import { Badge } from '@rebase/ui/badge'
import { cn } from '@rebase/ui/cn'
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { formatPercent, groupsLabel, rateLabel } from './format'
import { LOADING_SENTENCE, NO_REGISTRATIONS, SALES_EXIGIBILITY_LABELS } from './labels'
import type {
  IvaPurchaseRegister,
  IvaPurchaseRow,
  IvaSalesRegister,
  IvaSalesRow,
} from './queries'

/** What `useSalesRegister` and `usePurchaseRegister` answer, whichever register it is. */
export interface RegisterView<Row, Totals, Summary> {
  rows: Row[]
  totals: Totals | undefined
  summary: Summary[] | undefined
  totalCount: number | undefined
  isLoading: boolean
  isError: boolean
  error: unknown
  hasMore: boolean
  isFetchingMore: boolean
  loadMore: () => void
  retry: () => void
}

const NUMERIC = 'text-right tabular-nums whitespace-nowrap'

/** The sentence a register says for a part of a mixed document; the totals row is the period's,
 *  the rows are only the pages read so far. */
function Under({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>
}

/** One line of a summary: the rate and what sets it apart (the exigibility of a sale, the
 *  deductible share of a purchase), then the amounts the server added up for it. */
interface SummaryRow {
  key: string
  rate: string
  extra: string
  amounts: ReadonlyArray<readonly [label: string, value: string]>
}

/** The summary under a table: one line per rate (and nature, share or exigibility), from the
 *  server, which adds the whole period and not the pages on screen. A table from `md` up; on a
 *  phone one card per line, so no column is cut. */
function Summary({
  extraHead,
  rows,
  wide,
}: {
  extraHead: string
  rows: readonly SummaryRow[]
  wide: boolean
}) {
  const label = 'Riepilogo per aliquota'
  const labels = rows[0]?.amounts.map(([name]) => name) ?? []
  return (
    <section aria-label={label} className="mt-6 space-y-2">
      <h2 className="text-sm font-semibold">{label}</h2>
      {wide ? (
        <div className="border bg-card">
          <Table aria-label={label} scrollLabel={label}>
            <TableHeader>
              <TableRow>
                <TableHead>Aliquota</TableHead>
                <TableHead>{extraHead}</TableHead>
                {labels.map((name) => (
                  <TableHead key={name} className="text-right">
                    {name}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.key}>
                  <TableCell>{row.rate}</TableCell>
                  <TableCell>{row.extra}</TableCell>
                  {row.amounts.map(([name, value]) => (
                    <TableCell key={name} className={NUMERIC}>
                      {value}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <ul className="space-y-3">
          {rows.map((row) => (
            <li key={row.key} className="space-y-1 border bg-card p-4">
              <p className="font-medium">
                {row.rate} <span className="text-sm font-normal text-muted-foreground">· {row.extra}</span>
              </p>
              <dl className="space-y-0.5 text-sm">
                {row.amounts.map(([name, value]) => (
                  <div key={name} className="flex items-baseline justify-between gap-3">
                    <dt className="text-muted-foreground">{name}</dt>
                    <dd className="tabular-nums">{value}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** The shared frame of a register: its wait, its failure with no total under it, and its rows. */
function Frame<Row, Totals, Summary>({
  view,
  noun,
  children,
}: {
  view: RegisterView<Row, Totals, Summary>
  noun: string
  children: ReactNode
}) {
  if (view.isError && view.rows.length === 0) {
    return <QueryErrorBanner error={view.error} onRetry={view.retry} />
  }
  if (view.isLoading || view.totalCount === undefined) {
    return <LoadingState label={LOADING_SENTENCE} className="py-8" />
  }
  if (view.totalCount === 0) {
    return (
      <p className="border bg-card p-6 text-sm text-muted-foreground">{NO_REGISTRATIONS}</p>
    )
  }
  return (
    <>
      {children}
      {view.hasMore && (
        <LoadMore
          label={`Mostra altri (${view.totalCount} in tutto)`}
          message={`Mostrati ${view.rows.length} ${noun} su ${view.totalCount}.`}
          isFetchingMore={view.isFetchingMore}
          hasError={view.isError}
          error={view.error}
          onLoadMore={view.loadMore}
        />
      )}
    </>
  )
}

// ---- sales ----------------------------------------------------------------------------------

function salesNumber(row: IvaSalesRow): string {
  return `${row.year}/${row.number}`
}

function SalesDocument({ row }: { row: IvaSalesRow }) {
  if (row.invoice_id === null) return <span>{salesNumber(row)}</span>
  return (
    <Link
      to="/app/invoices/$invoiceId"
      params={{ invoiceId: row.invoice_id }}
      className="font-medium underline-offset-4 hover:underline"
    >
      {salesNumber(row)}
    </Link>
  )
}

function SalesTable({ rows, totals }: { rows: IvaSalesRow[]; totals: IvaSalesRegister['totals'] }) {
  return (
    <div className="border bg-card">
      <Table aria-label="Registro vendite" scrollLabel="Registro vendite">
        <TableHeader>
          <TableRow>
            <TableHead>Data</TableHead>
            <TableHead>Numero</TableHead>
            <TableHead>Cliente</TableHead>
            <TableHead className="text-right">Imponibile</TableHead>
            <TableHead>Aliquota</TableHead>
            <TableHead className="text-right">IVA</TableHead>
            <TableHead>Esigibilità</TableHead>
            <TableHead className="text-right">Totale</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) =>
            row.kind === 'gap' ? (
              <TableRow key={`gap-${row.number}`} className="text-muted-foreground">
                <TableCell>—</TableCell>
                <TableCell>{salesNumber(row)}</TableCell>
                <TableCell colSpan={6}>Numero non utilizzato: {row.gap_reason}</TableCell>
              </TableRow>
            ) : (
              <TableRow
                key={row.invoice_id}
                className={cn(row.status === 'cancelled' && 'text-muted-foreground')}
              >
                <TableCell className="whitespace-nowrap">
                  {row.issue_date === null ? '—' : formatIsoDateItalian(row.issue_date)}
                </TableCell>
                <TableCell className="whitespace-nowrap">
                  <SalesDocument row={row} />
                </TableCell>
                <TableCell className="max-w-40">
                  <p className="truncate" title={row.customer_name ?? undefined}>
                    {row.customer_name}
                  </p>
                  {(row.status === 'cancelled' || row.document_type === 'TD04') && (
                    <div className="flex flex-wrap items-center gap-1.5 pt-1">
                      {row.status === 'cancelled' && <StatusPill tone="muted">Annullata</StatusPill>}
                      {row.document_type === 'TD04' && <Badge variant="outline">Nota di credito</Badge>}
                    </div>
                  )}
                </TableCell>
                <TableCell className={NUMERIC}>{formatMoneyValue(row.taxable_amount)}</TableCell>
                <TableCell className="whitespace-nowrap">{groupsLabel(row.groups)}</TableCell>
                <TableCell className={NUMERIC}>{formatMoneyValue(row.vat_amount)}</TableCell>
                <TableCell className="whitespace-nowrap">
                  {row.exigibility === null ? '—' : SALES_EXIGIBILITY_LABELS[row.exigibility]}
                </TableCell>
                <TableCell className={NUMERIC}>{formatMoneyValue(row.total)}</TableCell>
              </TableRow>
            ),
          )}
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell colSpan={3} className="font-semibold">
              Totale del periodo
            </TableCell>
            <TableCell className={cn(NUMERIC, 'font-semibold')}>
              {formatMoneyValue(totals.taxable_amount)}
            </TableCell>
            <TableCell />
            <TableCell className={cn(NUMERIC, 'font-semibold')}>
              {formatMoneyValue(totals.vat_amount)}
            </TableCell>
            <TableCell />
            <TableCell className={cn(NUMERIC, 'font-semibold')}>
              {formatMoneyValue(totals.total)}
            </TableCell>
          </TableRow>
        </TableFooter>
      </Table>
    </div>
  )
}

function SalesCard({ row }: { row: IvaSalesRow }) {
  if (row.kind === 'gap') {
    return (
      <li className="space-y-1 border bg-card p-4 text-muted-foreground">
        <p className="font-medium">{salesNumber(row)}</p>
        <Under>Numero non utilizzato: {row.gap_reason}</Under>
      </li>
    )
  }
  return (
    <li
      className={cn('space-y-1 border bg-card p-4', row.status === 'cancelled' && 'text-muted-foreground')}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 break-words font-medium">{row.customer_name}</p>
        <span className="shrink-0 tabular-nums">{formatMoneyValue(row.vat_amount)}</span>
      </div>
      <div className="flex items-start justify-between gap-3 text-sm text-muted-foreground">
        <span className="min-w-0 break-words">
          <SalesDocument row={row} />
          {row.issue_date !== null && ` · ${formatIsoDateItalian(row.issue_date)}`}
        </span>
        <span className="shrink-0">{groupsLabel(row.groups)}</span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        {row.status === 'cancelled' && <StatusPill tone="muted">Annullata</StatusPill>}
        {row.document_type === 'TD04' && <Badge variant="outline">Nota di credito</Badge>}
        {row.exigibility === 'split_payment' && (
          <Badge variant="outline">{SALES_EXIGIBILITY_LABELS.split_payment}</Badge>
        )}
      </div>
    </li>
  )
}

/** «Registro vendite»: the issued and annulled invoices of the period, one row per document, read
 *  only; a row opens the invoice. */
export function SalesRegisterView({
  view,
  wide,
}: {
  view: RegisterView<IvaSalesRow, IvaSalesRegister['totals'], IvaSalesRegister['summary'][number]>
  wide: boolean
}) {
  return (
    <Frame view={view} noun="documenti">
      {view.totals !== undefined &&
        (wide ? (
          <SalesTable rows={view.rows} totals={view.totals} />
        ) : (
          <ul aria-label="Registro vendite" className="space-y-3">
            {view.rows.map((row) => (
              <SalesCard key={row.invoice_id ?? `gap-${row.number}`} row={row} />
            ))}
          </ul>
        ))}
      {view.totals !== undefined && (
        <dl className="mt-3 flex items-baseline justify-between gap-3 border bg-card p-4 text-sm">
          <dt className="font-semibold">IVA esigibile del periodo</dt>
          <dd className="font-semibold tabular-nums">{formatMoneyValue(view.totals.vat_due)}</dd>
        </dl>
      )}
      {view.summary !== undefined && view.summary.length > 0 && (
        <Summary
          extraHead="Esigibilità"
          wide={wide}
          rows={view.summary.map((line) => ({
            key: `${line.rate}-${line.nature}-${line.exigibility}`,
            rate: rateLabel(line.rate, line.nature),
            extra: SALES_EXIGIBILITY_LABELS[line.exigibility],
            amounts: [
              ['Imponibile', formatMoneyValue(line.taxable_amount)],
              ['IVA', formatMoneyValue(line.vat_amount)],
            ],
          }))}
        />
      )}
    </Frame>
  )
}

// ---- purchases ------------------------------------------------------------------------------

/** The share column of a document: the one group's, or the one every group shares, else «varie». */
function shareLabel(groups: IvaPurchaseRow['groups']): string {
  const [first] = groups
  if (first === undefined) return '—'
  return groups.every((group) => group.deductible_share === first.deductible_share)
    ? formatPercent(first.deductible_share)
    : 'varie'
}

function PurchaseDocument({ row }: { row: IvaPurchaseRow }) {
  const chips = specialChips(row)
  return (
    <div className="min-w-0 space-y-1">
      <Link
        to="/app/purchases/$purchaseId"
        params={{ purchaseId: row.purchase_id }}
        className="block max-w-56 truncate font-medium underline-offset-4 hover:underline"
        title={row.supplier_name ?? undefined}
      >
        {row.supplier_name}
      </Link>
      <div className="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
        <span>
          {documentLabel(row)} · {formatIsoDateItalian(row.document_date)}
        </span>
        {chips.map((chip) => (
          <Badge key={chip} variant="outline">
            {chip}
          </Badge>
        ))}
      </div>
    </div>
  )
}

type PurchaseTotals = IvaPurchaseRegister['totals']

function PurchaseTable({ rows, totals }: { rows: IvaPurchaseRow[]; totals: PurchaseTotals }) {
  return (
    <div className="border bg-card">
      <Table aria-label="Registro acquisti" scrollLabel="Registro acquisti">
        <TableHeader>
          <TableRow>
            <TableHead>Registrazione</TableHead>
            <TableHead>Fornitore e documento</TableHead>
            <TableHead>Aliquota</TableHead>
            <TableHead className="text-right">Imponibile</TableHead>
            <TableHead className="text-right">IVA</TableHead>
            <TableHead className="text-right">Quota</TableHead>
            <TableHead className="text-right">IVA detraibile</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.purchase_id} className="align-top">
              <TableCell className="whitespace-nowrap">
                {formatIsoDateItalian(row.registered_on)}
              </TableCell>
              <TableCell>
                <PurchaseDocument row={row} />
              </TableCell>
              <TableCell className="whitespace-nowrap">{groupsLabel(row.groups)}</TableCell>
              <TableCell className={NUMERIC}>{formatMoneyValue(row.taxable_amount)}</TableCell>
              <TableCell className={NUMERIC}>{formatMoneyValue(row.vat_amount)}</TableCell>
              <TableCell className={NUMERIC}>{shareLabel(row.groups)}</TableCell>
              <TableCell className={NUMERIC}>{formatMoneyValue(row.deductible_vat)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
        <TableFooter>
          <TableRow>
            <TableCell colSpan={3} className="font-semibold">
              Totale del periodo
            </TableCell>
            <TableCell className={cn(NUMERIC, 'font-semibold')}>
              {formatMoneyValue(totals.taxable_amount)}
            </TableCell>
            <TableCell className={cn(NUMERIC, 'font-semibold')}>
              {formatMoneyValue(totals.vat_amount)}
            </TableCell>
            <TableCell />
            <TableCell className={cn(NUMERIC, 'font-semibold')}>
              {formatMoneyValue(totals.deductible_vat)}
            </TableCell>
          </TableRow>
        </TableFooter>
      </Table>
    </div>
  )
}

function PurchaseCard({ row }: { row: IvaPurchaseRow }) {
  const chips = specialChips(row)
  return (
    <li>
      <Link
        to="/app/purchases/$purchaseId"
        params={{ purchaseId: row.purchase_id }}
        className="block space-y-1 border bg-card p-4"
      >
        <div className="flex items-start justify-between gap-3">
          <span className="min-w-0 break-words font-medium">{row.supplier_name}</span>
          <span className="shrink-0 tabular-nums">{formatMoneyValue(row.deductible_vat)}</span>
        </div>
        <div className="flex items-start justify-between gap-3 text-sm text-muted-foreground">
          <span className="min-w-0 break-words">
            {documentLabel(row)} · {formatIsoDateItalian(row.document_date)}
          </span>
          <span className="shrink-0">{groupsLabel(row.groups)}</span>
        </div>
        {chips.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            {chips.map((chip) => (
              <Badge key={chip} variant="outline">
                {chip}
              </Badge>
            ))}
          </div>
        )}
      </Link>
    </li>
  )
}

/** «Registro acquisti»: the registered invoices and credit notes placed in the period, one row per
 *  document at its deductible share, read only; a row opens the purchase. */
export function PurchaseRegisterView({
  view,
  wide,
}: {
  view: RegisterView<
    IvaPurchaseRow,
    PurchaseTotals,
    IvaPurchaseRegister['summary'][number]
  >
  wide: boolean
}) {
  return (
    <Frame view={view} noun="documenti">
      {view.totals !== undefined &&
        (wide ? (
          <PurchaseTable rows={view.rows} totals={view.totals} />
        ) : (
          <ul aria-label="Registro acquisti" className="space-y-3">
            {view.rows.map((row) => (
              <PurchaseCard key={row.purchase_id} row={row} />
            ))}
          </ul>
        ))}
      {view.totals !== undefined && !wide && (
        <dl className="mt-3 flex items-baseline justify-between gap-3 border bg-card p-4 text-sm">
          <dt className="font-semibold">IVA detraibile del periodo</dt>
          <dd className="font-semibold tabular-nums">
            {formatMoneyValue(view.totals.deductible_vat)}
          </dd>
        </dl>
      )}
      {view.totals !== undefined && view.totals.reverse_charge_vat !== '0.00' && (
        <p className="mt-3 text-sm text-muted-foreground">
          IVA dovuta per inversione contabile nel periodo:{' '}
          <span className="tabular-nums">{formatMoneyValue(view.totals.reverse_charge_vat)}</span>
        </p>
      )}
      {view.summary !== undefined && view.summary.length > 0 && (
        <Summary
          extraHead="Quota"
          wide={wide}
          rows={view.summary.map((line) => ({
            key: `${line.rate}-${line.nature}-${line.deductible_share}`,
            rate: rateLabel(line.rate, line.nature),
            extra: `quota ${formatPercent(line.deductible_share)}`,
            amounts: [
              ['Imponibile', formatMoneyValue(line.taxable_amount)],
              ['IVA', formatMoneyValue(line.vat_amount)],
              ['IVA detraibile', formatMoneyValue(line.deductible_vat)],
            ],
          }))}
        />
      )}
    </Frame>
  )
}
