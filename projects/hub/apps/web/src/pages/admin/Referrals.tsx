import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type ReferralLedgerItem, type ReferralSettings, type RewardStato } from '@/lib/api'
import { formatDate, formatEuro } from '@/lib/format'
import { Empty, Header, StateFilter } from './lists'

const LEDGER_KEY = ['referrals'] as const
const SETTINGS_KEY = ['referral-settings'] as const
const STATES: readonly RewardStato[] = ['da_confermare', 'confermato', 'pagato']
const STATE_LABELS: Record<string, string> = {
  da_confermare: 'Da confermare',
  confermato: 'Confermato',
  pagato: 'Pagato',
}
const KIND_LABELS: Record<string, string> = { freelancer: 'Freelance', company: 'Azienda' }
const NEXT_STATE: Record<RewardStato, RewardStato | null> = {
  da_confermare: 'confermato',
  confermato: 'pagato',
  pagato: null,
}
const NEXT_LABEL: Record<RewardStato, string> = { da_confermare: 'Conferma', confermato: 'Segna pagato', pagato: '' }

function RateForm() {
  const settings = useQuery({ queryKey: SETTINGS_KEY, queryFn: () => admin.referralSettings() })
  if (!settings.data) return null
  return <RateFields data={settings.data} />
}

/** Mounted only once the settings row has loaded, so each field's own state can be
 *  lazily initialised from it at mount -- no effect syncing state to a later-arriving
 *  prop, and no shared "has either field been touched" flag: that flag once sent the
 *  untouched field's still-blank state to the server as `0.00` the moment its sibling
 *  was edited, which recording the flow for this PR caught. */
function RateFields({ data }: { data: ReferralSettings }) {
  const client = useQueryClient()
  const [rateFreelancer, setRateFreelancer] = useState(() => (Number(data.rate_freelancer) * 100).toFixed(2))
  const [rateCompany, setRateCompany] = useState(() => (Number(data.rate_company) * 100).toFixed(2))

  const save = useMutation({
    mutationFn: () =>
      admin.saveReferralSettings(
        (Number(rateFreelancer) / 100).toFixed(4),
        (Number(rateCompany) / 100).toFixed(4),
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: SETTINGS_KEY })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    save.mutate()
  }

  return (
    <form
      onSubmit={submit}
      className="grid gap-4 border-b px-6 py-6 sm:grid-cols-[1fr_1fr_auto_auto] sm:items-end"
    >
      <div className="space-y-2">
        <Label htmlFor="rate-freelancer">Percentuale, segnalazione di un freelance</Label>
        <Input
          id="rate-freelancer"
          type="number"
          min={0}
          max={100}
          step="0.01"
          value={rateFreelancer}
          onChange={(event) => setRateFreelancer(event.target.value)}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="rate-company">Percentuale, segnalazione di un&apos;azienda</Label>
        <Input
          id="rate-company"
          type="number"
          min={0}
          max={100}
          step="0.01"
          value={rateCompany}
          onChange={(event) => setRateCompany(event.target.value)}
        />
      </div>
      <Button type="submit" disabled={save.isPending}>
        {save.isPending ? 'Salvo…' : 'Salva percentuali'}
      </Button>
      {data.updated_by_nome && (
        <p className="text-xs text-muted-foreground sm:col-span-4">
          Ultimo aggiornamento di {data.updated_by_nome}, {formatDate(data.updated_at)}.
        </p>
      )}
      {save.isError && (
        <p role="alert" className="text-sm text-destructive sm:col-span-4">
          Non riesco a salvare le percentuali.
        </p>
      )}
    </form>
  )
}

function PriceForm({ reward, onPriced }: { reward: ReferralLedgerItem; onPriced: () => void }) {
  const [base, setBase] = useState('')
  const [euroReward, setEuroReward] = useState('')
  const price = useMutation({
    mutationFn: () => admin.setReferralPrice(reward.id, base, euroReward),
    onSuccess: onPriced,
  })
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="space-y-1">
        <Label htmlFor={`base-${reward.id}`} className="text-xs">
          Base, €
        </Label>
        <Input
          id={`base-${reward.id}`}
          type="number"
          min={0}
          step="0.01"
          value={base}
          onChange={(event) => setBase(event.target.value)}
          className="w-28"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`reward-${reward.id}`} className="text-xs">
          Reward, €
        </Label>
        <Input
          id={`reward-${reward.id}`}
          type="number"
          min={0}
          step="0.01"
          value={euroReward}
          onChange={(event) => setEuroReward(event.target.value)}
          className="w-28"
        />
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={price.isPending || !base || !euroReward}
        onClick={() => price.mutate()}
      >
        {price.isPending ? 'Prezzo…' : 'Prezza'}
      </Button>
    </div>
  )
}

function ReferralRow({ item }: { item: ReferralLedgerItem }) {
  const client = useQueryClient()
  const move = useMutation({
    mutationFn: (stato: RewardStato) => admin.setReferralState(item.id, stato),
    onSuccess: () => void client.invalidateQueries({ queryKey: LEDGER_KEY }),
  })
  const next = NEXT_STATE[item.stato]

  return (
    <TableRow>
      <TableCell>
        <p className="font-medium">{item.referred_nome}</p>
        <p className="text-xs text-muted-foreground">{KIND_LABELS[item.kind] ?? item.kind}</p>
      </TableCell>
      <TableCell>
        <p className="font-medium">{item.referrer_nome}</p>
        <p className="text-xs text-muted-foreground">{item.referrer_email}</p>
      </TableCell>
      <TableCell>
        {item.base_amount === null || item.reward_amount === null ? (
          <PriceForm reward={item} onPriced={() => void client.invalidateQueries({ queryKey: LEDGER_KEY })} />
        ) : (
          <>
            <p className="font-medium">{formatEuro(item.reward_amount)}</p>
            <p className="text-xs text-muted-foreground">
              {formatEuro(item.base_amount)} · {(Number(item.rate) * 100).toFixed(0)}%
            </p>
          </>
        )}
      </TableCell>
      <TableCell>
        <Badge variant="pill">{STATE_LABELS[item.stato] ?? item.stato}</Badge>
      </TableCell>
      <TableCell className="text-right text-muted-foreground">{formatDate(item.created_at)}</TableCell>
      <TableCell className="text-right">
        {next && (item.stato === 'da_confermare' ? item.reward_amount !== null : true) && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={move.isPending}
            onClick={() => move.mutate(next)}
          >
            {NEXT_LABEL[item.stato]}
          </Button>
        )}
      </TableCell>
    </TableRow>
  )
}

/**
 * The referral ledger (P-REB-44): every reward a signed first letter earned, newest
 * first, with the two rates that decide how much right above it -- a database row an
 * admin edits here, never an environment variable (design record 2026-09-26). A
 * reward with no computed figure (an `a corpo` letter with no estimated days) gets
 * one typed in by hand instead of the usual line, and moves only once it has one. An
 * admin confirms a reward, then marks it paid once the transfer happened outside the
 * hub -- tracking only, nothing here pays anyone.
 */
export function AdminReferrals() {
  const [stato, setStato] = useState<RewardStato | undefined>(undefined)
  const list = useInfiniteQuery({
    queryKey: [...LEDGER_KEY, stato] as const,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.referrals({ stato, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  })
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data])
  const { hasNextPage, fetchNextPage } = list

  const sentinelRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!hasNextPage || typeof IntersectionObserver === 'undefined') return
    const node = sentinelRef.current
    if (!node) return
    const observer = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting) void fetchNextPage()
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [hasNextPage, fetchNextPage])

  return (
    <>
      <Header title="Referral" count={items.length}>
        <StateFilter
          states={STATES}
          value={stato}
          onChange={(value) => setStato(value as RewardStato | undefined)}
          labels={STATE_LABELS}
        />
      </Header>

      <RateForm />

      {list.isError ? (
        <Empty>Non riesco a leggere il registro.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{stato ? 'Nessun referral in questo stato.' : 'Nessun referral ancora.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Segnalato</TableHead>
                    <TableHead>Segnalato da</TableHead>
                    <TableHead>Reward</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead className="text-right">Da quando</TableHead>
                    <TableHead>
                      <span className="sr-only">Azioni</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <ReferralRow key={item.id} item={item} />
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          <div ref={sentinelRef} />
          {list.hasNextPage && (
            <div className="flex flex-col items-center gap-2 px-6 py-5">
              <p className="text-sm text-muted-foreground">Mostrati {items.length} referral, ce ne sono altri.</p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={list.isFetchingNextPage}
                onClick={() => void list.fetchNextPage()}
              >
                {list.isFetchingNextPage ? 'Carico…' : 'Mostra altri'}
              </Button>
            </div>
          )}
        </>
      )}
    </>
  )
}
