import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { Plus, ShoppingCart, SlidersHorizontal } from 'lucide-react'
import { useState } from 'react'
import { DataTable } from '@/components/DataTable'
import { FilterChip, FilterRow } from '@/components/FilterRow'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Label } from '@rebase/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@rebase/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { PeriodPicker } from '@/features/dashboard/PeriodPicker'
import { OrganizationPicker } from '@/features/organizations/OrganizationPicker'
import { formatMoneyValue } from '@/features/time/columns'
import { useCanWrite } from '@/lib/auth'
import { useLegalEntitiesToName } from '@/lib/legalEntity'
import { useEntitySchema } from '@/lib/schema'
import { buildPurchaseColumns } from './columns'
import { FILTER_KINDS, KIND_LABELS } from './formValues'
import { documentLine, foreignAmount, specialChips } from './labels'
import {
  useCostCategories,
  usePurchaseList,
  type CostCategory,
  type Purchase,
  type PurchaseKind,
} from './queries'
import type { PurchasesSearch } from './search'
import { SupplierLabel } from './SupplierLabel'

/** `Select` needs a non-empty string for «no filter»; the API parameter is simply left out. */
const ANY = 'all'

/** The table from `md` up, cards below it (the inbox's own breakpoint). */
const TABLE = '(min-width: 768px)'

const EMPTY_SENTENCE =
  'Nessun acquisto. Qui compaiono le fatture dei fornitori, gli scontrini e le spese: carica un documento o registra un acquisto.'

/** The search without the keys that say nothing, so the URL carries only filters that apply. */
function compact(search: PurchasesSearch): PurchasesSearch {
  return {
    kind: search.kind,
    supplier: search.supplier || undefined,
    category: search.category || undefined,
    from: search.from || undefined,
    to: search.to || undefined,
    cancelled: search.cancelled === true ? true : undefined,
  }
}

function isFiltered(search: PurchasesSearch): boolean {
  return Object.values(compact(search)).some((value) => value !== undefined)
}

function NewPurchaseButton() {
  return (
    <Button asChild>
      <Link to="/app/purchases/new">
        <Plus className="mr-2 size-4" />
        Nuovo acquisto
      </Link>
    </Button>
  )
}

/** «Fornitore» as a search-as-you-type picker: a popover on a wide screen, where a list of
 *  matches under the filter row would push the table down, and in place in the phone sheet. */
function SupplierFilter({
  value,
  onChange,
  inline,
}: {
  value: string | undefined
  onChange: (id: string | undefined) => void
  inline: boolean
}) {
  const [open, setOpen] = useState(false)
  const picker = (
    <OrganizationPicker
      id="purchases-supplier"
      role="supplier"
      value={value ?? null}
      onChange={(supplier) => {
        onChange(supplier?.id)
        setOpen(false)
      }}
      placeholder="Cerca un fornitore per ragione sociale, P.IVA o email…"
      ariaLabel="Cerca un fornitore"
    />
  )
  if (inline || value !== undefined) return <div className="w-full md:w-64">{picker}</div>
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          Fornitore
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80">
        {picker}
      </PopoverContent>
    </Popover>
  )
}

function CategorySelect({
  categories,
  value,
  onChange,
}: {
  categories: CostCategory[]
  value: string | undefined
  onChange: (id: string | undefined) => void
}) {
  return (
    <Select value={value ?? ANY} onValueChange={(next) => onChange(next === ANY ? undefined : next)}>
      <SelectTrigger className="w-full md:w-48" aria-label="Categoria">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>Tutte le categorie</SelectItem>
        {categories.map((category) => (
          <SelectItem key={category.id} value={category.id}>
            {category.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function KindSelect({
  value,
  onChange,
}: {
  value: PurchaseKind | undefined
  onChange: (kind: PurchaseKind | undefined) => void
}) {
  return (
    <Select
      value={value ?? ANY}
      onValueChange={(next) => onChange(next === ANY ? undefined : (next as PurchaseKind))}
    >
      <SelectTrigger className="w-full md:w-48" aria-label="Tipo">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>Tutti i tipi</SelectItem>
        {FILTER_KINDS.map((kind) => (
          <SelectItem key={kind} value={kind}>
            {KIND_LABELS[kind]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** One card of the phone list: supplier and amount on a line, the document under it, then the chips. */
function PurchaseCard({ purchase }: { purchase: Purchase }) {
  const cancelled = purchase.status === 'cancelled'
  const chips = specialChips(purchase)
  const foreign = foreignAmount(purchase)
  return (
    <li>
      <Link
        to="/app/purchases/$purchaseId"
        params={{ purchaseId: purchase.id }}
        className={`block space-y-1 border bg-card p-4 ${cancelled ? 'text-muted-foreground' : ''}`}
      >
        <div className="flex items-start justify-between gap-3">
          <SupplierLabel purchase={purchase} className="min-w-0 break-words font-medium" />
          <span className="shrink-0 tabular-nums">{formatMoneyValue(purchase.amount)}</span>
        </div>
        <div className="flex items-start justify-between gap-3 text-sm text-muted-foreground">
          <span className="min-w-0 break-words">{documentLine(purchase)}</span>
          {foreign !== null && <span className="shrink-0 tabular-nums">{foreign}</span>}
        </div>
        {(cancelled || chips.length > 0) && (
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            {cancelled && <StatusPill tone="muted">Annullato</StatusPill>}
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

/** «Acquisti»: the purchases of the space, the figure of what the filters select, and the filters. */
export function PurchasesPage() {
  const search = useSearch({ from: '/app/purchases/' })
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const wide = useMediaQuery(TABLE)
  const [filtersOpen, setFiltersOpen] = useState(false)

  const categories = useCostCategories(true)
  const schema = useEntitySchema('purchase')
  const list = usePurchaseList({
    kind: search.kind,
    // Hidden by default: a cancelled purchase is a document that existed, shown on request.
    status: search.cancelled === true ? 'cancelled' : 'registered',
    supplier_id: search.supplier,
    category_id: search.category,
    from: search.from,
    to: search.to,
  })
  const columns = buildPurchaseColumns(categories.data ?? [], schema.data?.custom_fields ?? [], {
    withSupplierDocument: true,
    legal_entities: useLegalEntitiesToName(),
  })

  function change(patch: Partial<PurchasesSearch>) {
    void navigate({ to: '/app/purchases', search: compact({ ...search, ...patch }), replace: true })
  }

  const filtered = isFiltered(search)
  const cancelledView = search.cancelled === true
  const period = { from: search.from ?? '', to: search.to ?? '' }
  const controls = (inline: boolean) => (
    <>
      <SupplierFilter value={search.supplier} onChange={(supplier) => change({ supplier })} inline={inline} />
      <CategorySelect
        categories={categories.data ?? []}
        value={search.category}
        onChange={(category) => change({ category })}
      />
      <KindSelect value={search.kind} onChange={(kind) => change({ kind })} />
      <PeriodPicker
        period={period}
        onChange={(next) => change({ from: next.from || undefined, to: next.to || undefined })}
      />
    </>
  )

  const showFigure = !list.isError && list.totalCount !== undefined && list.totalAmount !== undefined

  let body
  if (list.isError && list.items.length === 0) {
    // No list and no strip: no figure is offered that never arrived.
    body = <QueryErrorBanner error={list.error} onRetry={list.retry} />
  } else if (list.isLoading) {
    body = <LoadingState label="Leggo gli acquisti…" className="py-8" />
  } else if (list.items.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-4 border bg-card p-6">
        <p className="text-sm text-muted-foreground">
          {filtered ? 'Nessun acquisto con questi filtri.' : EMPTY_SENTENCE}
        </p>
        {canWrite && <NewPurchaseButton />}
      </div>
    )
  } else if (wide) {
    body = (
      <DataTable
        label="Acquisti"
        columns={columns}
        data={list.items}
        isError={list.isError}
        error={list.error}
        onRetry={list.retry}
        onRowClick={(row) =>
          void navigate({ to: '/app/purchases/$purchaseId', params: { purchaseId: row.id } })
        }
      />
    )
  } else {
    body = (
      <ul aria-label="Acquisti" className="space-y-3">
        {list.items.map((purchase) => (
          <PurchaseCard key={purchase.id} purchase={purchase} />
        ))}
      </ul>
    )
  }

  const figure = showFigure && (
    <dl className="flex flex-wrap items-baseline gap-x-8 gap-y-2 px-8 pb-4">
      <div>
        <dt className="text-sm text-muted-foreground">
          {cancelledView ? 'Annullati nel periodo' : 'Registrati nel periodo'}
        </dt>
        <dd className="text-2xl font-semibold tabular-nums">
          {formatMoneyValue(list.totalAmount ?? null)}
        </dd>
        <dd className="text-sm text-muted-foreground">
          {list.totalCount === 1 ? '1 acquisto' : `${list.totalCount} acquisti`}
        </dd>
      </div>
    </dl>
  )

  return (
    <>
      <PageHeader
        icon={ShoppingCart}
        title="Acquisti"
        description="Le fatture dei fornitori, gli scontrini e le spese, ognuno col suo fornitore, le sue date e i suoi importi."
        actions={canWrite && <NewPurchaseButton />}
      >
        {figure}
        {wide ? (
          <FilterRow>
            <FilterChip pressed={cancelledView} onPress={() => change({ cancelled: !cancelledView })}>
              Annullati
            </FilterChip>
            {controls(false)}
          </FilterRow>
        ) : (
          <div role="search" aria-label="Filtra l’elenco" className="flex items-center gap-2 px-8 pb-4">
            <Button variant="outline" size="sm" onClick={() => setFiltersOpen(true)}>
              <SlidersHorizontal className="mr-2 size-4" />
              Filtri
            </Button>
            <FilterChip pressed={cancelledView} onPress={() => change({ cancelled: !cancelledView })}>
              Annullati
            </FilterChip>
          </div>
        )}
      </PageHeader>

      <div className="px-8 pb-8">
        {body}
        {list.hasMore && list.totalCount !== undefined && (
          <LoadMore
            label={`Mostra altri (${list.totalCount} in tutto)`}
            message={`Mostrati ${list.items.length} acquisti su ${list.totalCount}.`}
            isFetchingMore={list.isFetchingMore}
            hasError={list.isError}
            error={list.error}
            onLoadMore={list.loadMore}
          />
        )}
      </div>

      {!wide && (
        <Sheet open={filtersOpen} onOpenChange={setFiltersOpen}>
          <SheetContent side="bottom" closeLabel="Chiudi" className="max-h-[85vh] gap-4 overflow-y-auto p-4">
            <SheetTitle>Filtri</SheetTitle>
            <SheetDescription className="sr-only">
              Fornitore, categoria, tipo e periodo degli acquisti.
            </SheetDescription>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="purchases-supplier">Fornitore</Label>
                <SupplierFilter value={search.supplier} onChange={(supplier) => change({ supplier })} inline />
              </div>
              <div className="space-y-2">
                <Label>Categoria</Label>
                <CategorySelect
                  categories={categories.data ?? []}
                  value={search.category}
                  onChange={(category) => change({ category })}
                />
              </div>
              <div className="space-y-2">
                <Label>Tipo</Label>
                <KindSelect value={search.kind} onChange={(kind) => change({ kind })} />
              </div>
              <div className="space-y-2">
                <Label>Periodo</Label>
                <PeriodPicker
                  period={period}
                  onChange={(next) => change({ from: next.from || undefined, to: next.to || undefined })}
                />
              </div>
            </div>
            <Button onClick={() => setFiltersOpen(false)}>{list.totalCount === undefined ? 'Mostra gli acquisti' : `Mostra ${list.totalCount} acquisti`}</Button>
          </SheetContent>
        </Sheet>
      )}
    </>
  )
}
