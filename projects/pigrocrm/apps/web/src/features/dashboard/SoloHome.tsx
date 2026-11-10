import { Link } from '@tanstack/react-router'
import { LayoutDashboard } from 'lucide-react'
import type { ReactNode } from 'react'
import { useQueries } from '@tanstack/react-query'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { billableDaysOptions } from '@/features/contracts/billingQueries'
import { formatAmount } from '@/features/contracts/labels'
import { useActiveContracts, type Contract } from '@/features/contracts/queries'
import { useReminders } from '@/features/reminders/queries'
import { recordLink } from '@/features/reminders/links'
import { subjectOf } from '@/features/reminders/fields'
import { daysBetweenIso, formatIsoDateItalian, formatIsoMonthYear, toIsoDate } from '@/lib/dates'
import { toProblem } from '@/lib/api'
import { useLegalEntity } from '@/lib/legalEntity'
import { useModules } from '@/lib/modules'
import { Loader } from '@rebase/ui/loader'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { BarRows, BigNumber, type BarRow } from './charts'
import { CeilingPanel } from './CeilingPanel'
import { FiscalPanel } from './FiscalPanel'
import { money } from './format'
import { HomeTaxDates } from '@/features/taxes/TaxDates'
import { Freshness } from './Freshness'
import { useCeilingHeadroom, useReceivablesDashboard } from './queries'

/** The reminder type of a fixed fee whose period closed with no invoice (spec 2026-10-26
 *  §3.3): the «canone ricorrente da fatturare» of the solo Home, read from the reminders
 *  the CRM already computes rather than recomputed here. */
const FIXED_FEE_REMINDER = 'fixed_fee_period_unbilled'

/** The app's own loading state, the four-tile loader, in the place the figures will fill. */
function Loading() {
  return (
    <div
      role="status"
      className="flex items-center gap-2 border bg-card p-6 text-sm text-muted-foreground"
    >
      <Loader className="size-6" />
      Caricamento…
    </div>
  )
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        {hint ? <p className="text-sm text-muted-foreground">{hint}</p> : null}
      </div>
      {children}
    </section>
  )
}

function ContractLink({ contract }: { contract: Pick<Contract, 'id' | 'title'> }) {
  return (
    <Link
      to="/app/contracts/$contractId"
      params={{ contractId: contract.id }}
      className="font-medium underline-offset-4 hover:underline"
    >
      {contract.title}
    </Link>
  )
}

/**
 * «Da fatturare ora» (spec 2026-11-09 B5): the contract days approved and not invoiced,
 * contract by contract and priced the way the invoice will price them, and the fixed fees
 * whose period closed without one. Each contract's ready days are the contract page's own
 * read (`billable-days`), so the line says what the draft will carry; nothing is added up
 * across contracts, because their currencies and rate cards are not the same sum.
 */
function ToInvoice({ contracts }: { contracts: Contract[] }) {
  const days = useQueries({ queries: contracts.map((contract) => billableDaysOptions(contract.id)) })
  const reminders = useReminders()
  // The reminders are the caller's across every legal entity and every contract status; the
  // list above is the sidebar's legal entity's active contracts, so a fee counts only for one.
  const activeIds = new Set(contracts.map((contract) => contract.id))
  const fees = (reminders.data ?? []).filter(
    (reminder) =>
      reminder.type === FIXED_FEE_REMINDER &&
      reminder.status !== 'snoozed' &&
      activeIds.has(reminder.entity_id),
  )

  if (days.some((query) => query.isPending) || reminders.isPending) {
    return <Loading />
  }

  // A read that failed is said, and «niente da fatturare» is never claimed over it: the
  // contracts that did answer are still listed under the banner.
  const errors = [...days.map((query) => query.error), reminders.error].filter(
    (error): error is Error => error !== null && error !== undefined,
  )
  const rows = contracts.flatMap((contract, index) => {
    const ready = days[index]?.data
    if (!ready || ready.work_units === 0) return []
    return [{ contract, ready }]
  })

  if (rows.length === 0 && fees.length === 0) {
    return errors[0] ? (
      <QueryErrorBanner error={errors[0]} />
    ) : (
      <p className="text-sm text-muted-foreground">Niente da fatturare adesso.</p>
    )
  }

  return (
    <div className="space-y-3">
      {errors[0] ? <QueryErrorBanner error={errors[0]} /> : null}
    <ul className="divide-y border bg-card">
      {rows.map(({ contract, ready }) => (
        <li key={contract.id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 p-4">
          <div className="min-w-0">
            <ContractLink contract={contract} />
            <p className="text-sm text-muted-foreground">
              {plural(ready.work_units, 'giornata pronta', 'giornate pronte')} da fatturare
              {ready.without_approval > 0
                ? ` · ${plural(ready.without_approval, 'giornata aspetta', 'giornate aspettano')} l’approvazione`
                : ''}
            </p>
            {ready.billable ? null : (
              <p className="text-sm text-destructive">
                {ready.without_rate.length > 0
                  ? `${plural(ready.without_rate.length, 'giornata non ha', 'giornate non hanno')} una tariffa in vigore: la bozza non si può creare.`
                  : `${(ready.block_reason ?? 'la bozza non si può creare').replace(/^./, (c) => c.toUpperCase())}.`}
              </p>
            )}
          </div>
          {ready.billable ? (
            <span className="text-lg font-semibold tabular-nums">
              {formatAmount(ready.taxable_amount, ready.currency)}
            </span>
          ) : null}
        </li>
      ))}
      {fees.map((fee) => {
        const link = recordLink(fee)
        return (
          <li key={fee.key} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 p-4">
            <div className="min-w-0">
              <p className="text-sm text-muted-foreground">{fee.label}</p>
              {link ? (
                <Link {...link} className="font-medium underline-offset-4 hover:underline">
                  {subjectOf(fee)}
                </Link>
              ) : (
                <p className="font-medium">{subjectOf(fee)}</p>
              )}
            </div>
          </li>
        )
      })}
    </ul>
    </div>
  )
}

/**
 * «Quando arrivano i soldi»: the receivables schedule, the same read the company's
 * Scadenziario tab draws (one endpoint, one cache entry), cut to what a freelancer checks
 * first: what is owed, what is late, and the month each due date falls in.
 */
function MoneyIn({ afterFirstFigure }: { afterFirstFigure?: ReactNode }) {
  const query = useReceivablesDashboard()
  // The tax dates do not wait on the receivables: with the read still on its way or failed they
  // follow its state instead of going missing.
  if (query.isError) {
    return (
      <div className="space-y-4">
        <QueryErrorBanner error={query.error} />
        {afterFirstFigure}
      </div>
    )
  }
  if (query.isPending || !query.data) {
    return (
      <div className="space-y-4">
        <Loading />
        {afterFirstFigure}
      </div>
    )
  }

  const data = query.data
  const expired = data.bands.find((row) => row.code === 'expired')
  const months: BarRow[] = data.by_month.map((row, index) => ({
    label: `${formatIsoMonthYear(row.month)} (${row.number})`,
    value: money(row.amount),
    ratio: row.share,
    tone: (index % 5) + 1,
  }))

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <BigNumber label="Da incassare" value={money(data.total)} tone="accent" />
        {afterFirstFigure}
        <BigNumber
          label="Di cui scaduto"
          value={money(expired?.amount ?? '0.00')}
          hint={plural(data.total_overdue, 'fattura scaduta', 'fatture scadute')}
        />
      </div>
      <BarRows caption="Incassi attesi per mese di scadenza" rows={months} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        {data.total_overdue > 0 ? (
          <Link
            to="/app/invoices"
            search={{ overdue: true }}
            className="text-sm underline underline-offset-4"
          >
            Vedi le fatture scadute
          </Link>
        ) : (
          <span />
        )}
        <Freshness calculatedAt={data.calculated_at} onRefresh={() => void query.refetch()} />
      </div>
    </div>
  )
}

/** «fino al 30/06/2026 · 52 giorni», «senza scadenza» or «scaduto da 3 giorni». The days
 *  are calendar days from today to the contract's `end`: the contract carries no budget of
 *  days, so its end is the only «left» it has. */
function leftOf(contract: Contract, today: string): string {
  if (contract.end === null) return 'senza scadenza'
  const left = daysBetweenIso(today, contract.end)
  const until = `fino al ${formatIsoDateItalian(contract.end)}`
  if (left === null) return until
  if (left < 0) return `${until} · scaduto da ${plural(-left, 'giorno', 'giorni')}`
  return `${until} · ${plural(left, 'giorno', 'giorni')}`
}

function ActiveContracts({ contracts }: { contracts: Contract[] }) {
  const today = toIsoDate(new Date())
  if (contracts.length === 0) {
    return <p className="text-sm text-muted-foreground">Nessun contratto attivo.</p>
  }
  return (
    <ul className="divide-y border bg-card">
      {contracts.map((contract) => (
        <li key={contract.id} className="space-y-0.5 p-4">
          <ContractLink contract={contract} />
          <p className="text-sm text-muted-foreground">{leftOf(contract, today)}</p>
        </li>
      ))}
    </ul>
  )
}

/**
 * The distance to the ceilings and the tax estimate of one legal entity, only when its pack
 * has ceilings (the flat-rate regime): the server answers an empty list for any other, and
 * then neither the ceilings nor an estimate (which the other packs do not have) is drawn.
 */
function FlatRateFigures({
  year,
  legalEntityId,
  title,
}: {
  year: number
  legalEntityId?: string
  title?: string
}) {
  const headroom = useCeilingHeadroom(year, legalEntityId)
  if (headroom.isError) {
    // No fiscal profile (404): «Impostazioni» is where that is said, and a Home that
    // repeats it for every legal entity would be noise. Anything else is the server's own.
    return toProblem(headroom.error).status === 404 ? null : <QueryErrorBanner error={headroom.error} />
  }
  if (headroom.isPending) return <Loading />
  if (headroom.data.thresholds.length === 0) return null
  return (
    <>
      <CeilingPanel year={year} legalEntityId={legalEntityId} title={title} withSimulator={false} />
      <FiscalPanel year={year} legalEntityId={legalEntityId} title={title} quiet />
    </>
  )
}

/**
 * The Home of a `solo` space (spec 2026-11-09 B5): what to invoice now, when the money
 * comes in, the active contracts and how long each runs, and on the flat-rate pack the
 * distance to the ceilings and the tax estimate. Every figure is the API's, read through
 * the queries the company's tabs already use; a section whose module is off is not drawn
 * and asks for nothing (`contracts` for the first and third, `taxes` for the last).
 *
 * Between the receivables and the contracts sit the next two tax dates (REB-1026, an
 * administrator's, with `taxes` on and a pack that has dates). It draws nothing else: the design
 * lists four blocks, and the day's tasks have their own
 * place in the navigation («Attività»).
 */
export function SoloHome({ notice }: { notice?: ReactNode }) {
  const modules = useModules()
  const contractsOn = modules.on('contracts')
  const taxesOn = modules.on('taxes')
  const { selected, legal_entities, several } = useLegalEntity()
  const active = useActiveContracts(selected, contractsOn)
  const year = new Date().getFullYear()
  const perLegalEntity = several && selected === null
  // On a phone the first figure leads and the next tax dates come right under it, before the rest
  // (brief, «The Home's tax dates»); from `sm` they are a block of their own after the money.
  const phone = !useMediaQuery('(min-width: 640px)')

  return (
    <div>
      <PageHeader icon={LayoutDashboard} title="Home" />
      <div className="space-y-8 px-8 py-6">
        {notice}
        {contractsOn && (
          <Section title="Da fatturare ora" hint="Giornate pronte e canoni del periodo chiuso.">
            {active.isError ? (
              <QueryErrorBanner error={active.error} />
            ) : active.isPending || !active.data ? (
              <Loading />
            ) : (
              <ToInvoice contracts={active.data} />
            )}
          </Section>
        )}
        <Section title="Quando arrivano i soldi" hint="Fatture emesse e non incassate, per scadenza.">
          <MoneyIn afterFirstFigure={phone ? <HomeTaxDates variant="block" /> : undefined} />
        </Section>
        {/* Money in, then money out (REB-1026): the next two tax dates, a block of their own. */}
        {phone ? null : <HomeTaxDates variant="block" />}
        {contractsOn && (
          <Section title="Contratti attivi">
            {active.isError ? (
              <QueryErrorBanner error={active.error} />
            ) : active.isPending || !active.data ? (
              <Loading />
            ) : (
              <ActiveContracts contracts={active.data} />
            )}
          </Section>
        )}
        {taxesOn &&
          (perLegalEntity ? (
            legal_entities.map((legalEntity) => (
              <FlatRateFigures
                key={legalEntity.id}
                year={year}
                legalEntityId={legalEntity.id}
                title={legalEntity.name}
              />
            ))
          ) : (
            <FlatRateFigures year={year} />
          ))}
      </div>
    </div>
  )
}
