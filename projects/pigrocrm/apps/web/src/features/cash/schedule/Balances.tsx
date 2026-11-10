import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { money } from '@/features/dashboard/format'
import { AccountDialog } from '@/features/cash/account/AccountDialog'
import { formatIsoDateItalian } from '@/lib/dates'
import { useLegalEntity } from '@/lib/legalEntity'
import { NO_ACCOUNT_SENTENCE, moneyIn } from './labels'
import type { CashAccountCard, CashSchedule } from './queries'

/** Four account cards, then «Tutti i conti (N)» (the brief's «Many» row). */
const VISIBLE_ACCOUNTS = 4

const CARD = 'min-w-0 border border-border bg-card p-4'

function AsOf({ account }: { account: CashAccountCard }) {
  if (account.balance_as_of === null) return null
  return (
    <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
      <span>al {formatIsoDateItalian(account.balance_as_of)}</span>
      {account.is_stale && (
        <>
          <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-royal-gold" />
          <span>{account.stale_days === null ? 'non aggiornato' : `da ${account.stale_days} giorni`}</span>
        </>
      )}
    </p>
  )
}

function AccountCard({ account, entity }: { account: CashAccountCard; entity: boolean }) {
  return (
    <Link
      to="/app/cash/accounts/$accountId"
      params={{ accountId: account.id }}
      className={cn(CARD, 'block hover:bg-muted')}
    >
      <p className="truncate text-sm font-medium" title={account.name}>
        {account.name}
      </p>
      <p className="truncate text-xs text-muted-foreground">{account.bank}</p>
      {entity && <p className="truncate text-xs text-muted-foreground">{account.legal_entity_name}</p>}
      <p className="mt-2 break-words text-xl font-semibold tabular-nums">
        {account.balance === null ? (
          <span className="text-sm font-normal text-muted-foreground">Saldo non ancora noto</span>
        ) : (
          moneyIn(account.balance, account.currency)
        )}
      </p>
      <AsOf account={account} />
    </Link>
  )
}

/**
 * Today's balance and the accounts behind it. Absent altogether when the schedule says the
 * viewer may not read balances (`accounts` null: a role without them, or the module off): never
 * a «0,00 €», never a dash. An allowed viewer with no account reads the empty state of the
 * brief, with the administrator's one button. The total sums the euro accounts only (the server
 * does not convert), and says which currencies it leaves out.
 */
export function Balances({ schedule }: { schedule: CashSchedule }) {
  const [all, setAll] = useState(false)
  const [adding, setAdding] = useState(false)
  const { several, selected } = useLegalEntity()
  const accounts = schedule.accounts
  if (accounts === null) return null

  if (accounts.length === 0) {
    return (
      <section aria-label="Saldo oggi" className={cn(CARD, 'space-y-3')}>
        <p className="text-sm">{NO_ACCOUNT_SENTENCE}</p>
        <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
          Aggiungi un conto
        </Button>
        {adding && <AccountDialog open onOpenChange={setAdding} />}
      </section>
    )
  }

  const entity = several && selected === null
  const total = schedule.total_balance
  const shown = all ? accounts : accounts.slice(0, VISIBLE_ACCOUNTS)
  return (
    <section aria-label="Saldo oggi" className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {total !== null && (
          <div role="group" aria-label={entity ? 'Saldo oggi, tutte le aziende' : 'Saldo oggi'} className={cn(CARD, 'sm:col-span-2 lg:col-span-1')}>
            <p className="text-sm text-muted-foreground">{entity ? 'Saldo oggi, tutte le aziende' : 'Saldo oggi'}</p>
            <p className="mt-1.5 break-words text-[26px] font-semibold leading-tight tabular-nums">{money(total.amount)}</p>
            {total.as_of !== null && (
              <p className="mt-1 text-xs text-muted-foreground">al {formatIsoDateItalian(total.as_of)}</p>
            )}
            {total.excluded_currencies.length > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                Non include i conti in {total.excluded_currencies.join(', ')}: non sono convertiti.
              </p>
            )}
            {total.accounts_without_balance > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                {total.accounts_without_balance === 1
                  ? '1 conto senza saldo iniziale non è nel totale.'
                  : `${total.accounts_without_balance} conti senza saldo iniziale non sono nel totale.`}
              </p>
            )}
          </div>
        )}
        {shown.map((account) => (
          <AccountCard key={account.id} account={account} entity={entity} />
        ))}
      </div>
      {accounts.length > VISIBLE_ACCOUNTS && (
        <Button variant="ghost" size="sm" onClick={() => setAll((previous) => !previous)}>
          {all ? 'Mostra meno conti' : `Tutti i conti (${accounts.length})`}
        </Button>
      )}
    </section>
  )
}
