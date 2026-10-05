import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type ReferralLedgerItem, type ReferralSettings, type RewardStato } from '@/lib/api'
import { matchHeadingId } from '@/lib/contracts'
import { REFERRAL_STATE_LABELS, formatDate, formatDateTime, formatEuro, formatRate } from '@/lib/format'
import { Empty, Header, StateFilter } from './lists'

const LEDGER_KEY = ['referrals'] as const
const SETTINGS_KEY = ['referral-settings'] as const
const STATES: readonly RewardStato[] = ['da_confermare', 'confermato', 'pagato']
const STATE_LABELS: Record<string, string> = { ...REFERRAL_STATE_LABELS, in_attesa: 'In attesa' }
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

function PriceForm({ rewardId, onPriced }: { rewardId: string; onPriced: () => void }) {
  const [base, setBase] = useState('')
  const [euroReward, setEuroReward] = useState('')
  const price = useMutation({
    mutationFn: () => admin.setReferralPrice(rewardId, base, euroReward),
    onSuccess: onPriced,
  })
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="space-y-1">
        <Label htmlFor={`base-${rewardId}`} className="text-xs">
          Base, €
        </Label>
        <Input
          id={`base-${rewardId}`}
          type="number"
          min={0}
          step="0.01"
          value={base}
          onChange={(event) => setBase(event.target.value)}
          className="w-28"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`reward-${rewardId}`} className="text-xs">
          Reward, €
        </Label>
        <Input
          id={`reward-${rewardId}`}
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

/** The referred person or company, a link to its own page by `kind`, unless an admin
 *  deleted it: that page answers not found, so the name stays, unlinked, and says so. */
function ReferredName({ item }: { item: ReferralLedgerItem }) {
  const className = 'font-medium hover:underline'
  if (item.referred_deleted) return <p className="font-medium">{item.referred_nome}</p>
  return item.kind === 'freelancer' ? (
    <Link to="/admin/freelance/$id" params={{ id: item.referred_id }} className={className}>
      {item.referred_nome}
    </Link>
  ) : (
    <Link to="/admin/companies/$id" params={{ id: item.referred_id }} className={className}>
      {item.referred_nome}
    </Link>
  )
}

/** The match the reward came from or, for a referral with none yet, the referred side's
 *  live one, on its freelancer's «Match e contratti» (REB-609). */
function MatchCell({ item }: { item: ReferralLedgerItem }) {
  if (item.match_id === null || item.match_freelancer_id === null) {
    return <span className="text-muted-foreground">—</span>
  }
  return (
    <>
      {item.match_freelancer_deleted ? (
        <p className="font-medium">{item.match_nome_azienda}</p>
      ) : (
        <Link
          to="/admin/freelance/$id/contracts"
          params={{ id: item.match_freelancer_id }}
          hash={matchHeadingId(item.match_id)}
          aria-label={`Dettaglio del match con ${item.match_nome_azienda} come ${item.match_figura_richiesta}`}
          className="font-medium hover:underline"
        >
          {item.match_nome_azienda}
        </Link>
      )}
      <p className="text-xs text-muted-foreground">
        {item.match_figura_richiesta} · {item.match_freelancer_nome}
      </p>
    </>
  )
}

/** What the referral is worth: the reward as computed or priced, else the estimate the
 *  referred side's live match gives (`projected_*`), marked as one. A real figure is
 *  never replaced by the estimate. */
function RewardCell({ item, onPriced }: { item: ReferralLedgerItem; onPriced: () => void }) {
  if (item.reward_id === null) {
    if (item.projected_amount !== null && item.projected_rate !== null) {
      return (
        <>
          <p className="font-medium text-muted-foreground tabular-nums">{formatEuro(item.projected_amount)}</p>
          <p className="text-xs text-muted-foreground">{`Stima al ${formatRate(item.projected_rate)}, se il match firma`}</p>
        </>
      )
    }
    return (
      <p className="text-sm text-muted-foreground">
        {item.match_id === null ? 'In attesa del primo contratto firmato' : 'Nessuna stima per il match'}
      </p>
    )
  }
  if (item.base_amount === null || item.reward_amount === null) {
    return (
      <div className="flex justify-end">
        <PriceForm rewardId={item.reward_id} onPriced={onPriced} />
      </div>
    )
  }
  return (
    <>
      <p className="font-medium tabular-nums">{formatEuro(item.reward_amount)}</p>
      <p className="text-xs text-muted-foreground tabular-nums">
        {formatEuro(item.base_amount)} · {item.rate !== null ? formatRate(item.rate) : '-'}
      </p>
    </>
  )
}

/** The evidence behind the attribution (REB-657), read-only: the code used, when the
 *  referred person signed up, where from, whether the two email domains match and whether
 *  they ever logged in. A matching domain and a person who never logged in are the two
 *  that call for a second look, so they are said in words rather than left as a blank. */
function EvidenceCell({ item }: { item: ReferralLedgerItem }) {
  const { evidence } = item
  return (
    <div className="mt-2 space-y-0.5 text-xs">
      <dl className="space-y-0.5">
        <div className="flex gap-1">
          <dt className="text-muted-foreground">Codice</dt>
          <dd className="font-mono">{evidence.code}</dd>
        </div>
        <div className="flex gap-1">
          <dt className="text-muted-foreground">Iscritto</dt>
          <dd>{formatDateTime(evidence.signed_up_at)}</dd>
        </div>
        <div className="flex gap-1">
          <dt className="text-muted-foreground">Fonte</dt>
          <dd>{evidence.utm_source ?? '-'}</dd>
        </div>
      </dl>
      <p className={evidence.same_email_domain ? 'font-medium' : 'text-muted-foreground'}>
        {evidence.same_email_domain ? 'Stesso dominio email' : 'Dominio email diverso'}
      </p>
      <p className={evidence.ever_logged_in ? 'text-muted-foreground' : 'font-medium'}>
        {evidence.ever_logged_in ? 'Ha già fatto accesso' : 'Non ha mai fatto accesso'}
      </p>
    </div>
  )
}

function ReferralRow({ item }: { item: ReferralLedgerItem }) {
  const client = useQueryClient()
  const move = useMutation({
    mutationFn: ({ id, stato }: { id: string; stato: RewardStato }) => admin.setReferralState(id, stato),
    onSuccess: () => void client.invalidateQueries({ queryKey: LEDGER_KEY }),
  })
  const rewardId = item.reward_id
  const next = item.stato ? NEXT_STATE[item.stato] : null
  const estimated = rewardId === null && item.projected_amount !== null

  return (
    <TableRow>
      <TableCell className="align-top">
        <ReferredName item={item} />
        <p className="text-xs text-muted-foreground">
          {KIND_LABELS[item.kind] ?? item.kind}
          {item.referred_deleted && (item.kind === 'freelancer' ? ' · eliminato' : ' · eliminata')}
        </p>
        <EvidenceCell item={item} />
      </TableCell>
      <TableCell className="align-top">
        {item.referrer_freelancer_id !== null ? (
          <Link
            to="/admin/freelance/$id"
            params={{ id: item.referrer_freelancer_id }}
            className="font-medium hover:underline"
          >
            {item.referrer_nome}
          </Link>
        ) : (
          <p className="font-medium">{item.referrer_nome}</p>
        )}
        <p className="text-xs text-muted-foreground">{item.referrer_email}</p>
      </TableCell>
      <TableCell className="align-top">
        <MatchCell item={item} />
      </TableCell>
      <TableCell className="text-right align-top">
        <RewardCell item={item} onPriced={() => void client.invalidateQueries({ queryKey: LEDGER_KEY })} />
      </TableCell>
      <TableCell className="align-top">
        {/* A hairline pill for an estimate, a filled one for a state a reward is really in. */}
        <Badge variant={estimated ? 'outline' : 'pill'}>
          {STATE_LABELS[item.stato ?? (estimated ? 'previsto' : 'in_attesa')] ?? item.stato}
        </Badge>
      </TableCell>
      <TableCell className="text-right align-top text-muted-foreground">{formatDate(item.created_at)}</TableCell>
      <TableCell className="text-right align-top">
        {rewardId !== null && next && (item.stato === 'da_confermare' ? item.reward_amount !== null : true) && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={move.isPending}
            onClick={() => move.mutate({ id: rewardId, stato: next })}
          >
            {item.stato ? NEXT_LABEL[item.stato] : ''}
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
                    <TableHead>Match</TableHead>
                    <TableHead className="text-right">Reward</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead className="text-right">Da quando</TableHead>
                    <TableHead>
                      <span className="sr-only">Azioni</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <ReferralRow key={item.referral_id} item={item} />
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
