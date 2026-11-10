import { Percent } from 'lucide-react'
import type { ReactNode } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { NOT_COMPUTABLE } from '@/features/analytics/format'
import { formatMoneyValue } from '@/features/time/columns'
import { useAuth, useCan } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { Badge } from '@rebase/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { TabsContent, TabsList, TabsTrigger, Tabs } from '@rebase/ui/tabs'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { Ladder } from './Ladder'
import {
  EVERY_ENTITY_SENTENCE,
  LOADING_SENTENCE,
  NOT_ALLOWED_SENTENCE,
  NOT_THIS_ENTITY_SENTENCE,
  NO_ENTITY_SENTENCE,
  NO_PERIODS_SENTENCE,
  periodPill,
} from './labels'
import {
  useIvaEntities,
  useIvaPeriods,
  usePurchaseRegister,
  useSalesRegister,
  type IvaPeriod,
} from './queries'
import { PurchaseRegisterView, SalesRegisterView } from './Registers'
import type { IvaRegisterTab, IvaSearch } from './search'

/** The table from `md` up, cards below it (the inbox's own breakpoint); the ladder beside the
 *  registers from `lg`. */
const TABLE = '(min-width: 768px)'
const BESIDE = '(min-width: 1024px)'

/** The years the selector offers: the current one and the four before it. */
const YEARS_SHOWN = 5

const OUTCOME_WORDS: Record<NonNullable<IvaPeriod['ladder']>['outcome'], string> = {
  due: 'da versare',
  carried: 'riportato',
  credit: 'a credito',
  nil: 'nulla da versare',
}

/** How the period select names a period: the period, its result as the server sent it and what it
 *  came to; a period the server cannot figure says so, and one still running says «in corso». */
function periodOption(period: IvaPeriod): string {
  if (period.ladder === null) {
    return `${period.label} · ${period.state === 'future' ? 'non ancora iniziato' : NOT_COMPUTABLE}`
  }
  const words = period.state === 'open' ? 'in corso' : OUTCOME_WORDS[period.ladder.outcome]
  return `${period.label} · ${formatMoneyValue(period.ladder.result)} · ${words}`
}

/** The period the page opens on: the one running now, else the last one that ended, else the first. */
function defaultPeriod(periods: readonly IvaPeriod[]): IvaPeriod | undefined {
  return (
    periods.find((period) => period.state === 'open') ??
    periods.filter((period) => period.state === 'ended').at(-1) ??
    periods[0]
  )
}

function Note({ children }: { children: string }) {
  return (
    <p role="note" className="border bg-card p-6 text-sm text-muted-foreground">
      {children}
    </p>
  )
}

function IvaBody({
  year,
  entityId,
  entityName,
  search,
  onSearch,
  lead,
  description,
}: {
  year: number
  entityId: string
  entityName: string
  search: IvaSearch
  onSearch: (patch: Partial<IvaSearch>) => void
  /** The selectors that choose what the page reads (the legal entity and the year). */
  lead: ReactNode
  description: string
}) {
  const wide = useMediaQuery(TABLE)
  const beside = useMediaQuery(BESIDE)
  const periods = useIvaPeriods(year, entityId)
  const list = periods.data ?? []
  const period = list.find((candidate) => candidate.number === search.period) ?? defaultPeriod(list)
  const number = period?.number ?? null
  const sales = useSalesRegister(year, number, entityId)
  const purchases = usePurchaseRegister(year, number, entityId)
  const tab: IvaRegisterTab = search.register ?? 'sales'

  const pill = period === undefined ? null : periodPill(period)
  const header = (
    <PageHeader
      icon={Percent}
      title={period === undefined ? 'IVA' : `IVA · ${period.label}`}
      titleAddon={
        pill === null ? undefined : (
          <>
            <Badge variant="outline">{entityName}</Badge>
            <StatusPill tone={pill.tone}>{pill.label}</StatusPill>
          </>
        )
      }
      description={description}
      actions={
        <>
          {lead}
          {period !== undefined && (
            <PeriodSelect
              periods={list}
              value={period.number}
              onChange={(next) => onSearch({ period: next })}
            />
          )}
        </>
      }
    />
  )

  if (periods.isError) {
    return (
      <>
        {header}
        <div className="px-8 pb-6 pt-1">
          <QueryErrorBanner error={periods.error} onRetry={() => void periods.refetch()} />
        </div>
      </>
    )
  }
  if (periods.isPending) {
    return (
      <>
        {header}
        <div className="px-8 pb-6 pt-1">
          <LoadingState label={LOADING_SENTENCE} className="py-8" />
        </div>
      </>
    )
  }
  if (period === undefined) {
    return (
      <>
        {header}
        <div className="px-8 pb-6 pt-1">
          <Note>{NO_PERIODS_SENTENCE}</Note>
        </div>
      </>
    )
  }

  const answered = sales.totalCount !== undefined && purchases.totalCount !== undefined
  const failed = sales.isError || purchases.isError
  const count = (total: number | undefined) => (total === undefined ? '' : ` (${total})`)
  const order: IvaRegisterTab[] = wide ? ['sales', 'purchases'] : ['purchases', 'sales']
  const tabLabels: Record<IvaRegisterTab, string> = {
    sales: `Registro vendite${count(sales.totalCount)}`,
    purchases: `Registro acquisti${count(purchases.totalCount)}`,
  }
  // The ladder is drawn only when both registers answered: no figure is offered that is not backed.
  let ladder: ReactNode = <LoadingState label={LOADING_SENTENCE} className="py-8" />
  if (answered) ladder = <Ladder period={period} />
  else if (failed) ladder = <Note>La liquidazione si mostra quando i due registri sono stati letti.</Note>

  return (
    <>
      {header}
      <div className={beside ? 'flex items-start gap-6 px-8 pb-8 pt-2' : 'space-y-6 px-8 pb-8 pt-2'}>
        {!beside && ladder}
        <Tabs
          value={tab}
          onValueChange={(next) => onSearch({ register: next as IvaRegisterTab })}
          className="min-w-0 flex-1"
        >
          <TabsList aria-label="Registri" className={wide ? undefined : 'w-full'}>
            {order.map((id) => (
              <TabsTrigger key={id} value={id} className={wide ? undefined : 'flex-1'}>
                {tabLabels[id]}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="sales" className="pt-4">
            <SalesRegisterView view={sales} wide={wide} />
          </TabsContent>
          <TabsContent value="purchases" className="pt-4">
            <PurchaseRegisterView view={purchases} wide={wide} />
          </TabsContent>
        </Tabs>
        {beside && <div className="w-88 shrink-0">{ladder}</div>}
      </div>
    </>
  )
}

function PeriodSelect({
  periods,
  value,
  onChange,
}: {
  periods: readonly IvaPeriod[]
  value: number
  onChange: (next: number) => void
}) {
  return (
    <Select
      value={String(value)}
      onValueChange={(next) => {
        const picked = periods.find((candidate) => String(candidate.number) === next)
        if (picked !== undefined) onChange(picked.number)
      }}
    >
      <SelectTrigger aria-label="Periodo" className="w-full md:w-auto md:min-w-96">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {periods.map((candidate) => (
          <SelectItem key={candidate.number} value={String(candidate.number)}>
            {periodOption(candidate)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/**
 * «IVA» (REB-1024, the approved brief `docs/design/2026-11-10-suppliers-purchases-and-iva-brief.md`):
 * the IVA cycle of one legal entity, opened on the period running now with a select that names each
 * period with its result, the settlement ladder beside the sales and the purchase registers. Every
 * figure, rate, share, limit and date is a field the server sent; a figure it did not send reads
 * «non calcolabile». One legal entity at a time, never a sum: under «Tutte le aziende» with several
 * that settle IVA the page's own «Azienda» select chooses. An administrator's page; any other role
 * gets one sentence and no request is made.
 */
export function IvaPage({
  search,
  onSearch,
}: {
  search: IvaSearch
  onSearch: (patch: Partial<IvaSearch>) => void
}) {
  const { user } = useAuth()
  const allowed = useCan('list_iva_periods')
  const { selected } = useLegalEntity()
  const entities = useIvaEntities(allowed)
  const current = new Date().getFullYear()
  const year = search.year ?? current
  const years = Array.from({ length: YEARS_SHOWN }, (_, index) => current - index)
  if (!years.includes(year)) years.push(year)

  const eligible = entities.data ?? []
  const everyEntity = selected === null && eligible.length > 1
  let entity = eligible.find((candidate) => candidate.legal_entity_id === selected)
  if (selected === null) {
    entity =
      eligible.find((candidate) => candidate.legal_entity_id === search.entity) ?? eligible[0]
  }

  const description = everyEntity
    ? EVERY_ENTITY_SENTENCE
    : 'Registri e liquidazione dei periodi IVA dell’azienda.'
  const bare = (children: ReactNode) => (
    <>
      <PageHeader icon={Percent} title="IVA" description={description} />
      <div className="px-8 pb-6 pt-1">{children}</div>
    </>
  )

  if (user === null) return null
  if (!allowed) return bare(<Note>{NOT_ALLOWED_SENTENCE}</Note>)
  if (entities.isError) {
    return bare(<QueryErrorBanner error={entities.error} onRetry={() => void entities.refetch()} />)
  }
  if (entities.isPending) return bare(<LoadingState label={LOADING_SENTENCE} className="py-8" />)
  if (eligible.length === 0 || entity === undefined) {
    return bare(<Note>{eligible.length === 0 ? NO_ENTITY_SENTENCE : NOT_THIS_ENTITY_SENTENCE}</Note>)
  }

  const lead = (
    <>
      {everyEntity && (
        <Select
          value={entity.legal_entity_id}
          onValueChange={(next) => onSearch({ entity: next, period: undefined })}
        >
          <SelectTrigger aria-label="Azienda" className="w-full sm:w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {eligible.map((candidate) => (
              <SelectItem key={candidate.legal_entity_id} value={candidate.legal_entity_id}>
                {candidate.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <Select
        value={String(year)}
        onValueChange={(next) => {
          const picked = years.find((candidate) => String(candidate) === next)
          if (picked !== undefined) onSearch({ year: picked, period: undefined })
        }}
      >
        <SelectTrigger aria-label="Anno" className="w-full sm:w-28">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {years.map((candidate) => (
            <SelectItem key={candidate} value={String(candidate)}>
              {candidate}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </>
  )

  return (
    <IvaBody
      key={`${entity.legal_entity_id}-${year}`}
      year={year}
      entityId={entity.legal_entity_id}
      entityName={entity.name}
      search={search}
      onSearch={onSearch}
      lead={lead}
      description={description}
    />
  )
}
