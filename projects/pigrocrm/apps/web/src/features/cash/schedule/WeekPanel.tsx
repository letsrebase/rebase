import { Link } from '@tanstack/react-router'
import { useState, type ReactNode } from 'react'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { useCan } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { useLegalEntity } from '@/lib/legalEntity'
import { useModules } from '@/lib/modules'
import {
  ITEM_STATE_TONE,
  SOURCES,
  counterpartyName,
  estimatedSentence,
  itemPillLabel,
  itemWhat,
  itemsCount,
  moneyIn,
  settlesAsAdmin,
  sourceLabel,
  sourcePage,
  weekLabel,
} from './labels'
import { useSettleDueItem, useWeekItems, type CashSchedule, type CashWeek, type DueItem } from './queries'

const NO_ITEMS = 'Niente in questa settimana.'

/** The panel beside the weeks from `lg`, a full-screen sheet below it (the inbox's pattern). */
export function Detail({
  inline,
  label,
  onClose,
  children,
}: {
  inline: boolean
  label: string
  onClose: () => void
  children: ReactNode
}) {
  if (inline) {
    return (
      <aside aria-label={label} className="flex w-82.5 shrink-0 flex-col self-start border bg-card">
        <div className="flex justify-end border-b px-2 py-1">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Chiudi
          </Button>
        </div>
        {children}
      </aside>
    )
  }
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        side="right"
        closeLabel="Chiudi"
        className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-none"
      >
        <SheetTitle className="sr-only">{label}</SheetTitle>
        <SheetDescription className="sr-only">Le voci della settimana e come segnarle.</SheetDescription>
        <div className="flex min-h-0 flex-1 flex-col pt-12">{children}</div>
      </SheetContent>
    </Sheet>
  )
}

/** The day an item falls due: «circa» and the sentence of the rule when it follows another
 *  item (design § 1.3), the date alone otherwise. */
function ItemDate({ item }: { item: DueItem }) {
  if (item.due_date === null) return <span>senza scadenza</span>
  const date = formatIsoDateItalian(item.due_date)
  if (!item.due_date_estimated) return <span>{date}</span>
  return (
    <span className="italic">
      circa {date}, {estimatedSentence(item.direction)}
    </span>
  )
}

function ItemRow({
  item,
  open,
  entity,
  onOpen,
}: {
  item: DueItem
  open: boolean
  /** The legal entity's name, muted under the row, only under «Tutte le aziende». */
  entity: string | null
  onOpen: () => void
}) {
  const forecast = item.state === 'forecast'
  return (
    <li className="border-b last:border-b-0">
      <button
        type="button"
        aria-pressed={open}
        onClick={onOpen}
        className={cn('flex w-full items-start justify-between gap-3 px-4 py-3 text-left', open && 'bg-muted')}
      >
        <span className="min-w-0 flex-1">
          <span className="block break-words text-sm">{counterpartyName(item)}</span>
          <span className="block break-words text-xs text-muted-foreground">{itemWhat(item)}</span>
          {entity !== null && <span className="block text-xs text-muted-foreground">{entity}</span>}
          <span
            className={cn(
              'mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground',
              forecast && 'italic',
            )}
          >
            <ItemDate item={item} />
            <StatusPill tone={ITEM_STATE_TONE[item.state]}>{itemPillLabel(item)}</StatusPill>
          </span>
        </span>
        <span className={cn('shrink-0 text-sm tabular-nums', forecast && 'italic text-muted-foreground')}>
          {moneyIn(item.remaining, item.currency)}
        </span>
      </button>
    </li>
  )
}

/** The sticky foot of the panel: what the open item is, and the one act on it. A forecast has
 *  no document and so no act; a sales item's date is never offered for change (design § 1.1). */
function ItemFoot({ item, today }: { item: DueItem; today: string }) {
  const settle = useSettleDueItem()
  const canIn = useCan('settle_due_item')
  const canOut = useCan('settle_outgoing_due_item')
  const allowed = settlesAsAdmin(item) ? canOut : canIn
  const page = sourcePage(item)
  const incoming = item.direction === 'in'
  return (
    <div className="space-y-2 border-t bg-card px-4 py-3">
      <p className="text-xs text-muted-foreground">
        {sourceLabel(item.source_kind)}
        {item.source_rule ? ` · ${item.source_rule}` : ''}
      </p>
      {settle.isError && <QueryErrorBanner error={settle.error} />}
      <div className="flex flex-wrap items-center gap-2">
        {item.state === 'expected' && allowed && (
          <Button
            variant="outline"
            size="sm"
            disabled={settle.isPending}
            onClick={() => settle.mutate({ item, today })}
          >
            {settle.isPending ? 'Registro…' : incoming ? 'Segna come incassato' : 'Segna come pagato'}
          </Button>
        )}
        {page === 'invoice' && item.source_id && (
          <Button variant="ghost" size="sm" asChild>
            <Link to="/app/invoices/$invoiceId" params={{ invoiceId: item.source_id }}>
              Apri la fattura
            </Link>
          </Button>
        )}
        {page === 'purchase' && item.source_id && (
          <Button variant="ghost" size="sm" asChild>
            <Link to="/app/purchases/$purchaseId" params={{ purchaseId: item.source_id }}>
              Apri la fattura
            </Link>
          </Button>
        )}
      </div>
    </div>
  )
}

function Group({
  title,
  items,
  selected,
  entityOf,
  onOpen,
}: {
  title: string | null
  items: DueItem[]
  selected: string | null
  entityOf: (item: DueItem) => string | null
  onOpen: (id: string) => void
}) {
  if (items.length === 0) return null
  return (
    <section>
      {title !== null && <h3 className="border-b bg-muted px-4 py-2 text-xs font-medium text-muted-foreground">{title}</h3>}
      <ul>
        {items.map((item) => (
          <ItemRow key={item.id} item={item} open={selected === item.id} entity={entityOf(item)} onOpen={() => onOpen(item.id)} />
        ))}
      </ul>
    </section>
  )
}

/**
 * One week's items (up to 50 with «Mostra altri»), from `GET /api/due-items` over the week's own
 * dates. The current week starts today: the days of it before today are late and live in the
 * late figures. Only kinds whose module is on are drawn; outgoing items form their own group
 * only when the schedule has an outgoing side.
 */
export function WeekPanel({ schedule, week }: { schedule: CashSchedule; week: CashWeek }) {
  const [openId, setOpenId] = useState<string | null>(null)
  const modules = useModules()
  const { several, selected, byId } = useLegalEntity()
  const items = useWeekItems({
    from: week.is_current ? schedule.today : week.start,
    to: week.end,
    ...(schedule.show_outgoing ? {} : { direction: 'in' as const }),
  })

  const all = (items.data?.pages ?? []).flatMap((page) => page.items)
  const shown = all.filter((item) => {
    const source = SOURCES[item.source_kind]
    return source !== undefined && modules.on(...source.modules)
  })
  const open = shown.find((item) => item.id === openId) ?? null
  const showEntity = several && selected === null
  const entityOf = (item: DueItem) => (showEntity ? (byId(item.legal_entity_id)?.name ?? null) : null)
  const incoming = shown.filter((item) => item.direction === 'in')
  const outgoing = shown.filter((item) => item.direction === 'out')

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b px-4 py-3">
        <h2 className="text-sm font-semibold">Settimana dal {weekLabel(week)}</h2>
        <p className="text-xs text-muted-foreground">{itemsCount(week.item_count)}</p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {items.isError ? (
          <div className="p-4">
            <QueryErrorBanner error={items.error} onRetry={() => void items.refetch()} />
          </div>
        ) : items.isPending ? (
          <LoadingState label="Leggo le voci…" className="px-4" />
        ) : shown.length === 0 ? (
          <p className="px-4 py-4 text-sm text-muted-foreground">{NO_ITEMS}</p>
        ) : (
          <>
            <Group
              title={schedule.show_outgoing ? 'Entrate' : null}
              items={incoming}
              selected={openId}
              entityOf={entityOf}
              onOpen={setOpenId}
            />
            <Group title="Uscite" items={outgoing} selected={openId} entityOf={entityOf} onOpen={setOpenId} />
          </>
        )}
        {items.hasNextPage && (
          <div className="px-4 pb-4">
            <LoadMore
              label="Mostra altri"
              message={`Mostrate ${itemsCount(shown.length)}, ce ne sono altre.`}
              isFetchingMore={items.isFetchingNextPage}
              hasError={items.isFetchNextPageError}
              error={items.error}
              onLoadMore={() => void items.fetchNextPage()}
            />
          </div>
        )}
      </div>
      {open !== null && <ItemFoot item={open} today={schedule.today} />}
    </div>
  )
}
