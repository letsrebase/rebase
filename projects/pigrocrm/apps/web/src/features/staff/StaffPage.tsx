import { useNavigate, useSearch } from '@tanstack/react-router'
import { CalendarClock, ChevronLeft, ChevronRight, IdCard, Plus } from 'lucide-react'
import { useState } from 'react'
import { DataTable, type DataTableFeatures } from '@/components/DataTable'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { cn } from '@rebase/ui/cn'
import { Label } from '@rebase/ui/label'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { useFiscalProfile } from '@/features/invoices/queries'
import { formatMoneyValue } from '@/features/time/columns'
import { useIsAdmin } from '@/lib/auth'
import { sumDecimalStrings, MONEY_SCALE } from '@/lib/decimal'
import { useLegalEntity } from '@/lib/legalEntity'
import { Absent, Figure, NetCell } from './Amounts'
import { PayDayDialog } from './PayDayDialog'
import { PersonDialog } from './PersonDialog'
import { StaffPanel } from './StaffPanel'
import {
  F24_KINDS,
  STATE_LABELS,
  STATE_LINES,
  STATE_TONES,
  formatDay,
  missingReason,
  monthName,
  monthOnly,
  namesList,
  netItems,
  shiftMonth,
  stateOf,
  termsLine,
  thisMonth,
  type MonthState,
} from './labels'
import { usePayDay, usePayrollMonth, useStaffList, type MemberMonth, type PayrollMonth, type StaffMember } from './queries'
import type { ColumnDef } from '@tanstack/react-table'

/** The table from `md` up, the inline panel from `lg`: below them rows are cards and the panel is
 *  a full-screen sheet (the brief: phone first). */
const TABLE = '(min-width: 768px)'
const INLINE_PANEL = '(min-width: 1024px)'

const EMPTY_TEXT = 'Qui vedi quanto costano dipendenti e amministratori, mese per mese.'
const ADMIN_ONLY = 'I compensi li vedono solo gli amministratori.'

interface Row {
  member: StaffMember
  month: MemberMonth | undefined
}

/** The pill of a month: the least advanced person's state, with the line naming who set it. */
function monthState(rows: Row[]): { state: MonthState; line: string } | null {
  const served = rows.filter((row) => row.month !== undefined)
  if (served.length === 0) return null
  const waiting = served.filter((row) => stateOf(row.month) === 'estimated')
  if (waiting.length === 0) return { state: 'accepted', line: 'Ogni cedolino è accettato' }
  return { state: 'estimated', line: `Lo tiene a «Stimato»: ${namesList(waiting.map((row) => row.member.name))}` }
}

function Figures({ rows, payroll }: { rows: Row[]; payroll: PayrollMonth; month: string }) {
  const served = rows.filter((row) => row.month?.cost != null)
  const fromPayslip = served.filter((row) => row.month?.source === 'payslip').length
  const net = rows.flatMap((row) => netItems(row.month))
  const dates = [...new Set(net.map((item) => item.due_date))].sort()
  const withoutNet = served.filter((row) => netItems(row.month).length === 0).length
  const state = monthState(rows)
  const leaving = dates.length === 0 ? null : monthOnly(dates[0]!.slice(0, 7))
  return (
    <dl className="grid gap-x-8 gap-y-4 px-8 pb-4 sm:grid-cols-3">
      <div>
        <dt className="text-sm text-muted-foreground">Costo di {monthOnly(payroll.month.slice(0, 7))}</dt>
        <dd className="text-2xl font-semibold tabular-nums">{formatMoneyValue(payroll.cost_total)}</dd>
        <dd className="text-sm text-muted-foreground">
          {served.length === 0 ? 'Nessuno in servizio' : `${fromPayslip} su ${served.length} da cedolino`}
        </dd>
      </div>
      <div>
        <dt className="text-sm text-muted-foreground">Esce{leaving === null ? '' : ` a ${leaving}`}, alle persone</dt>
        <dd className="text-2xl font-semibold tabular-nums">
          {net.length === 0 ? '—' : formatMoneyValue(sumDecimalStrings(net.map((item) => item.amount), MONEY_SCALE))}
        </dd>
        <dd className="text-sm text-muted-foreground">
          {withoutNet === 0
            ? dates.length === 0
              ? 'Nessuno stipendio in questo mese'
              : `il ${dates.map(formatDay).join(', ')}`
            : `Manca per ${withoutNet} ${withoutNet === 1 ? 'persona' : 'persone'}: ${rows.map((row) => missingReason(row.month)).find((reason) => reason !== null) ?? 'senza date'}`}
        </dd>
      </div>
      <div>
        <dt className="text-sm text-muted-foreground">Il mese</dt>
        <dd className="pt-1">
          {state === null ? (
            <span className="text-muted-foreground">—</span>
          ) : (
            <StatusPill tone={STATE_TONES[state.state]}>{STATE_LABELS[state.state]}</StatusPill>
          )}
        </dd>
        <dd className="text-sm text-muted-foreground">{state === null ? 'Nessuno in servizio' : state.line}</dd>
      </div>
    </dl>
  )
}

/** The month's F24: one line for the whole month and not one per person, with no amount while
 *  anyone lacks an accepted payslip. */
function F24Row({ rows }: { rows: Row[] }) {
  const served = rows.filter((row) => row.month !== undefined)
  const items = served.flatMap((row) => row.month?.cash.filter((item) => F24_KINDS.has(item.kind)) ?? [])
  const waiting = served.filter((row) => stateOf(row.month) !== 'accepted')
  const dates = [...new Set(items.map((item) => item.due_date))].sort()
  if (served.length === 0) return null
  return (
    <div aria-label="F24 del mese" className="mt-4 flex flex-wrap items-start justify-between gap-x-6 gap-y-1 border bg-card p-4 text-sm">
      <div>
        <p className="font-medium">F24 del mese{dates.length === 0 ? '' : `, il ${dates.map(formatDay).join(', ')}`}</p>
        <p className="text-muted-foreground">Ritenute, INPS e INAIL di tutti, in un unico versamento.</p>
      </div>
      {waiting.length === 0 && items.length > 0 ? (
        <span className="tabular-nums font-semibold">
          {formatMoneyValue(sumDecimalStrings(items.map((item) => item.amount), MONEY_SCALE))}
        </span>
      ) : (
        <Absent
          reason={
            waiting.length > 0
              ? `Serve il cedolino accettato di ${namesList(waiting.map((row) => row.member.name))}`
              : null
          }
        />
      )}
    </div>
  )
}

function PersonCell({ row }: { row: Row }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <p className="max-w-64 truncate font-medium" title={row.member.name}>
        {row.member.name}
      </p>
      <p className="whitespace-normal text-xs text-muted-foreground">
        {termsLine(row.member)}, dal {formatDay(row.member.start_date)}
      </p>
    </div>
  )
}

function StateCell({ row }: { row: Row }) {
  const state = stateOf(row.month)
  if (state === null) return <span className="text-sm text-muted-foreground">Non in servizio</span>
  return (
    <div className="space-y-1">
      <StatusPill tone={STATE_TONES[state]}>{STATE_LABELS[state]}</StatusPill>
      <p className="whitespace-normal text-xs text-muted-foreground">{STATE_LINES[state]}</p>
    </div>
  )
}

function costCell(row: Row) {
  const month = row.month
  if (month === undefined) return <Absent reason={null} />
  return month.cost === null ? <Absent reason={missingReason(month)} /> : <Figure value={month.cost.total} from={month.source} />
}

function columnsFor(monthKey: string): ColumnDef<DataTableFeatures, Row>[] {
  return [
    { id: 'person', header: 'Persona', cell: ({ row }) => <PersonCell row={row.original} /> },
    {
      id: 'cost',
      header: `Costo di ${monthOnly(monthKey)}`,
      cell: ({ row }) => <div className="text-right">{costCell(row.original)}</div>,
      meta: { align: 'right' },
    },
    {
      id: 'cash',
      header: 'Esce alle persone',
      cell: ({ row }) => (
        <div className="text-right">
          <NetCell month={row.original.month} />
        </div>
      ),
      meta: { align: 'right' },
    },
    { id: 'state', header: 'Stato della paga', cell: ({ row }) => <StateCell row={row.original} /> },
  ]
}

/** One card of the phone list: name and cost on a line, kind under it, the pill and what leaves. */
function PersonCard({ row, onOpen }: { row: Row; onOpen: () => void }) {
  const net = netItems(row.month)[0]
  return (
    <li>
      <button type="button" onClick={onOpen} className="block w-full space-y-2 border bg-card p-4 text-left">
        <div className="flex items-start justify-between gap-3">
          <p className="min-w-0 break-words font-medium">{row.member.name}</p>
          <div className="shrink-0 text-right">{costCell(row)}</div>
        </div>
        <p className="text-sm text-muted-foreground">{termsLine(row.member)}</p>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <StateCell row={row} />
          <p className="text-sm tabular-nums">
            {net === undefined ? <span className="text-muted-foreground">—</span> : `esce ${formatMoneyValue(net.amount)} il ${formatDay(net.due_date)}`}
          </p>
        </div>
      </button>
    </li>
  )
}

/** The month of one legal entity: its figures, the people, the F24, the panel. */
function StaffMonth({ entity, entityName }: { entity: string; entityName: string | null }) {
  const search = useSearch({ from: '/app/staff' })
  const navigate = useNavigate()
  const wide = useMediaQuery(TABLE)
  const inline = useMediaQuery(INLINE_PANEL)
  const month = search.month ?? thisMonth()
  const ended = search.ended === true
  const staff = useStaffList(entity, ended)
  const payroll = usePayrollMonth(entity, month)
  const payDay = usePayDay(entity)
  // Directors exist only on a company pack (design § 1); the server says so too, in words.
  const directorsAllowed = useFiscalProfile(entity).data?.pack_id === 'it-company'
  const [openId, setOpenId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState(false)
  const [payDayOpen, setPayDayOpen] = useState(false)

  function go(patch: { month?: string; ended?: boolean }) {
    void navigate({
      to: '/app/staff',
      search: { month: patch.month ?? search.month, ended: patch.ended ?? search.ended },
      replace: true,
    })
  }

  const byId = new Map((payroll.data?.members ?? []).map((row) => [row.staff_member_id, row]))
  const rows: Row[] = staff.items.map((member) => ({ member, month: byId.get(member.id) }))
  const open = rows.find((row) => row.member.id === openId) ?? null
  const loading = staff.isLoading || payroll.isPending
  const failed = staff.isError || payroll.isError

  const addButton = (
    <Button onClick={() => setAdding(true)}>
      <Plus className="mr-2 size-4" />
      Aggiungi una persona
    </Button>
  )

  let body
  if (failed && rows.length === 0) {
    body = (
      <QueryErrorBanner
        error={staff.error ?? payroll.error}
        onRetry={() => {
          void payroll.refetch()
          window.location.reload()
        }}
      />
    )
  } else if (loading) {
    body = <LoadingState label="Leggo le persone…" className="py-8" />
  } else if (rows.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-4 border bg-card p-6">
        <p className="font-medium">Nessuna persona ancora.</p>
        <p className="text-sm text-muted-foreground">{EMPTY_TEXT}</p>
        {addButton}
      </div>
    )
  } else if (wide) {
    body = (
      <DataTable
        label="Persone dell'azienda"
        columns={columnsFor(month)}
        data={rows}
        onRowClick={(row) => setOpenId(row.member.id)}
      />
    )
  } else {
    body = (
      <ul aria-label="Persone dell'azienda" className="space-y-3">
        {rows.map((row) => (
          <PersonCard key={row.member.id} row={row} onOpen={() => setOpenId(row.member.id)} />
        ))}
      </ul>
    )
  }

  const panel =
    open === null ? null : (
      <StaffPanel member={open.member} month={open.month} monthKey={month} onEdit={() => setEditing(true)} />
    )

  return (
    <>
      <PageHeader
        icon={IdCard}
        title="Persone dell'azienda"
        description={`Quanto costano dipendenti e amministratori nel mese lavorato, e quando i soldi escono${entityName === null ? '' : ` (${entityName})`}.`}
        actions={addButton}
      >
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-8 pb-4">
          <div className="flex items-center gap-1">
            <Button variant="outline" size="icon" aria-label="Mese prima" onClick={() => go({ month: shiftMonth(month, -1) })}>
              <ChevronLeft className="size-4" />
            </Button>
            <span aria-live="polite" className="min-w-36 text-center font-medium capitalize">
              {monthName(month)}
            </span>
            <Button variant="outline" size="icon" aria-label="Mese dopo" onClick={() => go({ month: shiftMonth(month, 1) })}>
              <ChevronRight className="size-4" />
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox id="staff-ended" checked={ended} onCheckedChange={(next) => go({ ended: next === true })} />
            <Label htmlFor="staff-ended">Mostra anche chi non c’è più</Label>
          </div>
          <Button variant="outline" size="sm" onClick={() => setPayDayOpen(true)}>
            <CalendarClock className="mr-2 size-4" />
            Giorno di paga
          </Button>
        </div>
        {payroll.data === undefined || rows.length === 0 ? null : <Figures rows={rows} payroll={payroll.data} month={month} />}
      </PageHeader>

      <div className={cn('flex gap-6 px-8 pb-8 pt-2', inline ? 'items-start' : '')}>
        <div className="min-w-0 flex-1">
          {body}
          {rows.length > 0 && payroll.data !== undefined ? <F24Row rows={rows} /> : null}
          {staff.hasMore ? (
            <LoadMore
              label="Mostra altri"
              message={`Mostrate ${rows.length} persone, ce ne sono altre.`}
              isFetchingMore={staff.isFetchingMore}
              hasError={staff.isError}
              error={staff.error}
              onLoadMore={staff.loadMore}
            />
          ) : null}
        </div>
        {open !== null && inline ? (
          <aside aria-label="Persona aperta" className="w-82.5 shrink-0 border bg-card">
            <div className="flex justify-end border-b px-2 py-1">
              <Button variant="ghost" size="sm" onClick={() => setOpenId(null)}>
                Chiudi
              </Button>
            </div>
            {panel}
          </aside>
        ) : null}
      </div>

      {open !== null && !inline ? (
        <Sheet open onOpenChange={(next) => !next && setOpenId(null)}>
          <SheetContent side="right" closeLabel="Chiudi" className="gap-0 overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-none">
            <SheetTitle className="sr-only">Persona aperta</SheetTitle>
            <SheetDescription className="sr-only">Il mese, il cedolino, le condizioni e il costo orario.</SheetDescription>
            <div className="pt-12">{panel}</div>
          </SheetContent>
        </Sheet>
      ) : null}

      {adding ? (
        <PersonDialog open onOpenChange={setAdding} legalEntityId={entity} legalEntityName={entityName} directorsAllowed={directorsAllowed} />
      ) : null}
      {editing && open !== null ? (
        <PersonDialog
          open
          onOpenChange={setEditing}
          legalEntityId={entity}
          legalEntityName={entityName}
          directorsAllowed={directorsAllowed}
          member={open.member}
        />
      ) : null}
      {payDayOpen ? <PayDayDialog open onOpenChange={setPayDayOpen} legalEntityId={entity} current={payDay.data} /> : null}
    </>
  )
}

/** «Persone dell'azienda»: the people the company pays, what each costs for a month and when
 *  that money leaves. Administrators only: anyone else is told so and nothing is asked. */
export function StaffPage() {
  const admin = useIsAdmin()
  const legal = useLegalEntity()
  if (!admin) {
    return (
      <>
        <PageHeader icon={IdCard} title="Persone dell'azienda" />
        <p role="alert" className="mx-8 border bg-card p-4 text-sm text-muted-foreground">
          {ADMIN_ONLY}
        </p>
      </>
    )
  }
  const entity = legal.selected !== null ? legal.byId(legal.selected) : legal.legal_entities[0]
  if (!legal.loaded) return <LoadingState label="Leggo le persone…" className="p-8" />
  if (entity === undefined) {
    return (
      <>
        <PageHeader icon={IdCard} title="Persone dell'azienda" />
        <p className="mx-8 border bg-card p-4 text-sm text-muted-foreground">
          Serve almeno un’azienda con il profilo fiscale compilato: la trovi in Impostazioni.
        </p>
      </>
    )
  }
  return (
    <StaffMonth
      key={entity.id}
      entity={entity.id}
      entityName={legal.several ? entity.name : null}
    />
  )
}
