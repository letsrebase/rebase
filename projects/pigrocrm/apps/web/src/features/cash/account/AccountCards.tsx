import type { ReactNode } from 'react'
import { cn } from '@rebase/ui/cn'
import { formatIsoMonthYear } from '@/lib/dates'
import {
  ARRIVAL_LABELS,
  CADENCE_LABELS,
  accountMoney,
  balanceDateLine,
  groupIban,
  monthTitle,
} from './labels'
import type { AccountCoverage, BankAccount } from './queries'

/** The ruled white card every figure of the page sits in: squared, one ink line, no shadow. */
function Panel({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <section aria-label={label} className={cn('min-w-0 border bg-card p-5', className)}>
      <h2 className="text-[15px] leading-snug font-normal text-muted-foreground">{label}</h2>
      {children}
    </section>
  )
}

/**
 * The balance with the date it is true as of. A stale one (the server's flag) says so in words
 * («da 41 giorni») and with the gold dot. With no anchor at all there is no figure, and the card
 * says what gives it one, never «0,00 €».
 */
export function BalanceCard({ account }: { account: BankAccount }) {
  const date = balanceDateLine(account)
  return (
    <Panel label="Saldo">
      {account.balance === null ? (
        <p className="mt-2 text-sm">Nessun saldo: indica il saldo iniziale o carica un estratto conto.</p>
      ) : (
        <p className="mt-1 text-3xl font-semibold tracking-tight tabular-nums">
          {accountMoney(account.balance, account.currency)}
        </p>
      )}
      {date !== null && (
        <p className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
          {account.is_stale && <span aria-hidden="true" className="size-1.5 shrink-0 rounded-full bg-royal-gold" />}
          {date}
        </p>
      )}
    </Panel>
  )
}

/** Who holds the account: bank, entity and the IBAN as it is printed, in groups of four. */
export function AccountCard({ account }: { account: BankAccount }) {
  return (
    <Panel label="Conto">
      <dl className="mt-2 space-y-1 text-sm">
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Banca</dt>
          <dd>{account.bank}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Azienda</dt>
          <dd className="min-w-0 break-words">{account.legal_entity_name}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted-foreground">IBAN</dt>
          <dd className="min-w-0 break-all tabular-nums">{groupIban(account.iban)}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Valuta</dt>
          <dd>{account.currency}</dd>
        </div>
      </dl>
    </Panel>
  )
}

/** How its statements arrive: the way, the cadence and the addresses they come from. */
export function ArrivalCard({ account }: { account: BankAccount }) {
  return (
    <Panel label="Estratti conto">
      <dl className="mt-2 space-y-1 text-sm">
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Arrivo</dt>
          <dd>{ARRIVAL_LABELS[account.arrival]}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Cadenza</dt>
          <dd>{CADENCE_LABELS[account.cadence]}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted-foreground">Mittenti</dt>
          <dd className="min-w-0 break-all">
            {account.senders.length === 0 ? 'Nessun indirizzo indicato' : account.senders.join(', ')}
          </dd>
        </div>
      </dl>
    </Panel>
  )
}

/** The three letters a month cell carries («ott»): the month's own name cut short. */
function monthAbbreviation(periodFrom: string): string {
  return formatIsoMonthYear(periodFrom).slice(0, 3)
}

const CELL_STATE_WORDS: Record<AccountCoverage['months'][number]['state'], string> = {
  covered: 'in',
  missing: 'manca',
  pending: 'non ancora dovuto',
}

const CELL_STATE_STYLES: Record<AccountCoverage['months'][number]['state'], string> = {
  covered: 'border-foreground bg-foreground text-background',
  missing: 'border-dashed border-destructive text-destructive',
  pending: 'border-border text-muted-foreground',
}

/**
 * The last twelve months as twelve cells: one in ink where a statement covers the month, dashed
 * `danger` with the word «manca» where one should have arrived, plain where it is not due yet.
 * On a phone the cells are thin and the missing months are named under them.
 */
export function CoverageStrip({ coverage, wide }: { coverage: AccountCoverage; wide: boolean }) {
  const missing = coverage.months.filter((month) => month.state === 'missing')
  return (
    <section aria-label="Estratti conto degli ultimi 12 mesi" className="space-y-2">
      <ol className={cn('grid grid-cols-12', wide ? 'gap-2' : 'gap-1')}>
        {coverage.months.map((month) => (
          <li
            key={month.month}
            data-state={month.state}
            title={`${monthTitle(month.period_from)}: ${CELL_STATE_WORDS[month.state]}`}
            className={cn(
              'flex min-w-0 flex-col items-center justify-center border text-xs',
              wide ? 'h-14 gap-0.5' : 'h-6',
              CELL_STATE_STYLES[month.state],
            )}
          >
            <span className={wide ? undefined : 'sr-only'}>{monthAbbreviation(month.period_from)}</span>
            {wide && month.state === 'missing' && <span>manca</span>}
            {!wide && (
              <span className="sr-only">
                {monthTitle(month.period_from)}: {CELL_STATE_WORDS[month.state]}
              </span>
            )}
          </li>
        ))}
      </ol>
      {!wide && missing.length > 0 && (
        <p className="text-sm text-destructive">
          Manca: {missing.map((month) => monthTitle(month.period_from)).join(', ')}
        </p>
      )}
    </section>
  )
}
