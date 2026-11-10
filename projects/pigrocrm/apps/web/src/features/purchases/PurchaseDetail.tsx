import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { Pencil, ShoppingCart } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { Timeline } from '@/components/Timeline'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useDeal } from '@/features/deals/queries'
import { useInboxItem } from '@/features/inbox/queries'
import { useOrganization } from '@/features/organizations/queries'
import { formatMoneyValue } from '@/features/time/columns'
import { useCan } from '@/lib/auth'
import { formatIsoDateItalian, toIsoDate } from '@/lib/dates'
import { formatItalianQuantity } from '@/lib/decimal'
import { useLegalEntity } from '@/lib/legalEntity'
import { KIND_LABELS } from './formValues'
import {
  PURCHASE_STATUS_LABELS,
  PURCHASE_STATUS_TONE,
  documentLabel,
  foreignAmount,
} from './labels'
import {
  useCancelPurchase,
  useCostCategories,
  useDeletePurchase,
  usePurchase,
  useRestorePurchase,
  type Purchase,
} from './queries'
import { PurchaseFile } from './PurchaseFile'

const EMPTY = '—'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words tabular-nums">{children}</dd>
    </>
  )
}

const yesNo = (value: boolean) => (value ? 'Sì' : 'No')

/** A percentage as the API stored it («22.00»), only written the Italian way. */
const percent = (value: string) => `${formatItalianQuantity(value)} %`

/** The invoice a credit note corrects, named by its number; the link opens it. */
function CorrectedInvoice({ id }: { id: string }) {
  const corrected = usePurchase(id)
  return (
    <Link to="/app/purchases/$purchaseId" params={{ purchaseId: id }} className="underline">
      {corrected.data ? documentLabel(corrected.data) : 'Apri la fattura'}
    </Link>
  )
}

function DealLink({ id }: { id: string }) {
  const deal = useDeal(id)
  return (
    <Link to="/app/deal/$dealId" params={{ dealId: id }} className="underline">
      {deal.data?.name ?? 'Apri il deal'}
    </Link>
  )
}

function VatRecap({ purchase }: { purchase: Purchase }) {
  if (purchase.vat_lines.length === 0) return null
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-medium">Righe IVA</h2>
      <div className="overflow-hidden border bg-card">
        <Table scrollLabel="Righe IVA">
          <TableHeader>
            <TableRow>
              <TableHead>Aliquota o natura</TableHead>
              <TableHead className="text-right">Imponibile</TableHead>
              <TableHead className="text-right">Imposta</TableHead>
              <TableHead className="text-right">Quota detraibile</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {purchase.vat_lines.map((line) => (
              <TableRow key={line.position}>
                <TableCell>
                  {percent(line.rate)}
                  {line.nature ? ` · ${line.nature}` : ''}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatMoneyValue(line.taxable_amount)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatMoneyValue(line.vat_amount)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {percent(line.deductible_share)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  )
}

/** The document as the API stored it: nothing here is computed in the browser. */
function Overview({ purchase, categoryName }: { purchase: Purchase; categoryName: string }) {
  const foreign = foreignAmount(purchase)
  const withholding = purchase.withholding_amount !== null || purchase.withholding_rate !== null
  const fund = purchase.fund_amount !== null || purchase.fund_rate !== null
  return (
    <div className="space-y-8">
      <dl className="grid max-w-xl grid-cols-[minmax(0,10rem)_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <Row label="Tipo">{KIND_LABELS[purchase.kind]}</Row>
        <Row label="Numero">{purchase.number ?? EMPTY}</Row>
        <Row label="Data">{formatIsoDateItalian(purchase.date)}</Row>
        <Row label="Ricevuta il">
          {purchase.received_on === null ? EMPTY : formatIsoDateItalian(purchase.received_on)}
        </Row>
        <Row label="Periodo IVA">
          {purchase.iva_period === null ? EMPTY : formatIsoDateItalian(purchase.iva_period)}
        </Row>
        <Row label="Importo">{formatMoneyValue(purchase.amount)}</Row>
        {foreign !== null && (
          <>
            <Row label="Valuta">{foreign}</Row>
            {purchase.exchange_rate !== null && (
              <Row label="Cambio">
                {formatItalianQuantity(purchase.exchange_rate)}
                {purchase.exchange_rate_date !== null
                  ? ` del ${formatIsoDateItalian(purchase.exchange_rate_date)}`
                  : ''}
              </Row>
            )}
          </>
        )}
        <Row label="Categoria">{categoryName}</Row>
        {purchase.deal_id !== null && (
          <Row label="Deal">
            <DealLink id={purchase.deal_id} />
          </Row>
        )}
        {purchase.corrects_purchase_id !== null && (
          <Row label="Corregge">
            <CorrectedInvoice id={purchase.corrects_purchase_id} />
          </Row>
        )}
        {purchase.stamp_duty !== null && <Row label="Bollo">{formatMoneyValue(purchase.stamp_duty)}</Row>}
        {withholding && (
          <>
            <Row label="Ritenuta">
              {purchase.withholding_rate === null ? EMPTY : percent(purchase.withholding_rate)}
              {purchase.withholding_amount === null
                ? ''
                : ` · ${formatMoneyValue(purchase.withholding_amount)}`}
            </Row>
            {purchase.withholding_base !== null && (
              <Row label="Base della ritenuta">{formatMoneyValue(purchase.withholding_base)}</Row>
            )}
          </>
        )}
        {fund && (
          <Row label="Cassa">
            {purchase.fund_rate === null ? EMPTY : percent(purchase.fund_rate)}
            {purchase.fund_amount === null ? '' : ` · ${formatMoneyValue(purchase.fund_amount)}`}
          </Row>
        )}
        <Row label="Scissione dei pagamenti">{yesNo(purchase.split_payment)}</Row>
        <Row label="Inversione contabile">{yesNo(purchase.reverse_charge)}</Row>
        <Row label="Costo imputato">
          <strong>{formatMoneyValue(purchase.cost_amount)}</strong>
        </Row>
        {purchase.description !== '' && <Row label="Descrizione">{purchase.description}</Row>}
      </dl>
      <VatRecap purchase={purchase} />
    </div>
  )
}

/** Where the purchase came from, when it was registered from «In arrivo»: the day the purchase
 *  was registered, which is the day the item was filed. */
function Origin({ registeredAt, inboxItemId }: { registeredAt: string; inboxItemId: string }) {
  return (
    <aside aria-label="Origine" className="space-y-2 border bg-card p-4 text-sm">
      <h2 className="font-medium">Origine</h2>
      <p>Registrata da In arrivo il {formatIsoDateItalian(toIsoDate(new Date(registeredAt)))}</p>
      <Link to="/app/inbox" search={{ tab: 'filed', item: inboxItemId }} className="underline">
        Apri In arrivo
      </Link>
    </aside>
  )
}

/** The justification: the file of the «In arrivo» item, when the purchase has one. */
function Documents({ inboxItemId }: { inboxItemId: string | null }) {
  const item = useInboxItem(inboxItemId)
  const noFile = <p className="text-muted-foreground">Nessun file allegato a questo acquisto.</p>
  if (inboxItemId === null) return noFile
  if (item.isError) return <QueryErrorBanner error={item.error} onRetry={() => void item.refetch()} />
  if (item.data === undefined) return <LoadingState label="Leggo il file…" />
  return item.data.has_file ? <PurchaseFile inboxItemId={inboxItemId} /> : noFile
}

/** «Acquisti» → one purchase: the document, its VAT lines and where it came from. */
export function PurchaseDetail() {
  const { purchaseId } = useParams({ from: '/app/purchases/$purchaseId' })
  const navigate = useNavigate()
  const canUpdate = useCan('update_purchase')
  const canCancel = useCan('cancel_purchase')
  const canDelete = useCan('delete_purchase')
  const purchase = usePurchase(purchaseId)
  const categories = useCostCategories(true)
  const supplier = useOrganization(purchase.data?.supplier_id ?? '')
  const legalEntity = useLegalEntity()
  const cancel = useCancelPurchase()
  const remove = useDeletePurchase()
  const restore = useRestorePurchase()
  const [refusal, setRefusal] = useState<unknown>(null)

  if (purchase.isLoading) return <LoadingState label="Leggo l’acquisto…" className="p-8" />
  if (purchase.isError) {
    return (
      <div className="p-8">
        <QueryErrorBanner error={purchase.error} onRetry={() => void purchase.refetch()} />
      </div>
    )
  }
  if (!purchase.data) return <p className="p-8">Acquisto non trovato.</p>

  const row = purchase.data
  const cancelled = row.status === 'cancelled'
  const busy = cancel.isPending || remove.isPending || restore.isPending
  const categoryName = categories.data?.find((category) => category.id === row.category_id)?.name ?? EMPTY
  const title = supplier.data?.legal_name ?? row.merchant_name ?? 'Acquisto'
  const issuedTo = legalEntity.several ? legalEntity.byId(row.legal_entity_id)?.name : undefined

  function annul() {
    const confirmed = window.confirm(
      'Annullare questo acquisto? Resta nel registro come annullato e non conta più nei costi.',
    )
    if (!confirmed) return
    setRefusal(null)
    cancel.mutate(row.id, {
      onSuccess: () => toast.success('Acquisto annullato'),
      // A closed period or any other refusal is shown in the server's own words.
      onError: setRefusal,
    })
  }

  function archive() {
    const confirmed = window.confirm(
      'Eliminare questo acquisto? Sparisce dagli elenchi e dai conti del deal, ma il dato resta e l’operazione è reversibile.',
    )
    if (!confirmed) return
    const id = row.id
    setRefusal(null)
    remove.mutate(id, {
      onSuccess: () => {
        toast.success('Acquisto eliminato', {
          action: { label: 'Ripristina', onClick: () => restore.mutate(id) },
        })
        void navigate({ to: '/app/purchases' })
      },
      onError: setRefusal,
    })
  }

  return (
    <Tabs defaultValue="panoramica">
      <PageHeader
        icon={ShoppingCart}
        title={title}
        description={
          [documentLabel(row), formatIsoDateItalian(row.date), issuedTo ? `Azienda ${issuedTo}` : undefined]
            .filter((part): part is string => part !== undefined)
            .join(' · ')
        }
        actions={
          <>
            <Badge variant="outline">{KIND_LABELS[row.kind]}</Badge>
            <StatusPill tone={PURCHASE_STATUS_TONE[row.status]}>
              {PURCHASE_STATUS_LABELS[row.status]}
            </StatusPill>
            {canUpdate && !cancelled && (
              <Button variant="outline" asChild>
                <Link to="/app/purchases/edit/$purchaseId" params={{ purchaseId: row.id }}>
                  <Pencil className="mr-2 size-4" />
                  Modifica
                </Link>
              </Button>
            )}
            {canCancel && !cancelled && (
              <Button variant="outline" onClick={annul} disabled={busy}>
                Annulla acquisto
              </Button>
            )}
            {canDelete && (
              <Button variant="destructive" onClick={archive} disabled={busy}>
                Elimina
              </Button>
            )}
          </>
        }
        tabs={
          <TabsList variant="line">
            <TabsTrigger value="panoramica">Panoramica</TabsTrigger>
            <TabsTrigger value="documents">Documenti</TabsTrigger>
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
          </TabsList>
        }
      />
      <div className="px-8 pb-8">
        {refusal !== null && (
          <div className="mt-6">
            <QueryErrorBanner error={refusal} />
          </div>
        )}
        <TabsContent value="panoramica" className="mt-6">
          <div className="grid grid-cols-1 gap-8 xl:grid-cols-[minmax(0,1fr)_20rem]">
            <Overview purchase={row} categoryName={categoryName} />
            {row.inbox_item_id !== null && <Origin registeredAt={row.created_at} inboxItemId={row.inbox_item_id} />}
          </div>
        </TabsContent>
        <TabsContent value="documents" className="mt-6">
          <Documents inboxItemId={row.inbox_item_id} />
        </TabsContent>
        <TabsContent value="timeline" className="mt-6">
          <Timeline entityType="purchase" entityId={row.id} />
        </TabsContent>
      </div>
    </Tabs>
  )
}
