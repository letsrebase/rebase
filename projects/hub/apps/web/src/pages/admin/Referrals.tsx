import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type ReferralLedgerItem, type ReferralSettings, type RewardStatus } from '@/lib/api'
import { failureMessage, pageItems, useCursorList } from '@/lib/adminList'
import {
  REFERRAL_STATE_LABELS,
  REFERRAL_VERIFIED_VIA_LABELS,
  formatDate,
  formatDateTime,
  formatRate,
} from '@/lib/format'
import { formatMoney } from '@rebase/ui/format'
import { keys } from '@/lib/query-keys'
import { Empty, Header, LoadingState, LoadMore, StateFilter } from './list-kit'

const STATES: readonly RewardStatus[] = ['to_confirm', 'confirmed', 'paid']
const STATE_LABELS: Record<string, string> = { ...REFERRAL_STATE_LABELS, pending: 'In attesa' }
const KIND_LABELS: Record<string, string> = { freelancer: 'Freelance', company: 'Azienda' }
const NEXT_STATE: Record<RewardStatus, RewardStatus | null> = {
  to_confirm: 'confirmed',
  confirmed: 'paid',
  paid: null,
}
const NEXT_LABEL: Record<RewardStatus, string> = { to_confirm: 'Conferma', confirmed: 'Segna pagato', paid: '' }

function RateForm() {
  const settings = useQuery({ queryKey: keys.referralSettings, queryFn: () => admin.referralSettings() })
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
      void client.invalidateQueries({ queryKey: keys.referralSettings })
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
      {data.updated_by_name && (
        <p className="text-xs text-muted-foreground sm:col-span-4">
          Ultimo aggiornamento di {data.updated_by_name}, {formatDate(data.updated_at)}.
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
  const priceFailure = failureMessage(price.error, 'Non riesco a salvare il prezzo.')
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
      {priceFailure && (
        <p role="alert" className="w-full text-xs text-destructive">
          {priceFailure}
        </p>
      )}
    </div>
  )
}

/** The referred person or company, a link to its own page by `kind`, unless an admin
 *  deleted it: that page answers not found, so the name stays, unlinked, and says so. */
function ReferredName({ item }: { item: ReferralLedgerItem }) {
  const className = 'font-medium hover:underline'
  if (item.referred_deleted) return <p className="font-medium">{item.referred_name}</p>
  return item.kind === 'freelancer' ? (
    <Link to="/admin/freelance/$id" params={{ id: item.referred_id }} className={className}>
      {item.referred_name}
    </Link>
  ) : (
    <Link to="/admin/companies/$id" params={{ id: item.referred_id }} className={className}>
      {item.referred_name}
    </Link>
  )
}

/** The engagement the reward came from or, for a referral with none yet, the referred side's
 *  live one, linked to its freelancer's card (REB-609). It is the hub's read model of the CRM's
 *  engagement (REB-1106), so the link leads to the card, not to a match page of the hub's. */
function EngagementCell({ item }: { item: ReferralLedgerItem }) {
  if (item.engagement_id === null || item.engagement_freelancer_id === null) {
    return <span className="text-muted-foreground">—</span>
  }
  return (
    <>
      {item.engagement_freelancer_deleted ? (
        <p className="font-medium">{item.engagement_company_name}</p>
      ) : (
        <Link
          to="/admin/freelance/$id"
          params={{ id: item.engagement_freelancer_id }}
          aria-label={`Dettaglio dell'incarico con ${item.engagement_company_name} come ${item.engagement_role}`}
          className="font-medium hover:underline"
        >
          {item.engagement_company_name}
        </Link>
      )}
      <p className="text-xs text-muted-foreground">
        {item.engagement_role} · {item.engagement_freelancer_name}
      </p>
    </>
  )
}

/** What the referral is worth: the reward as computed or priced, else the estimate the
 *  referred side's live engagement gives (`projected_*`), marked as one. A real figure is
 *  never replaced by the estimate. A pending referral with no estimate says what it is
 *  waiting for instead (REB-658): its referred person's proof, never a letter. */
function RewardCell({ item, onPriced }: { item: ReferralLedgerItem; onPriced: () => void }) {
  if (item.reward_id === null) {
    if (item.projected_amount !== null && item.projected_rate !== null) {
      return (
        <>
          <p className="font-medium text-muted-foreground tabular-nums">{formatMoney(item.projected_amount)}</p>
          <p className="text-xs text-muted-foreground">{`Stima al ${formatRate(item.projected_rate)}, se la lettera firma`}</p>
        </>
      )
    }
    if (item.referral_status === 'to_verify') {
      return (
        <p className="ml-auto max-w-56 text-sm whitespace-normal text-muted-foreground">
          {item.kind === 'company'
            ? "Non conta finché l'azienda non fa il primo accesso"
            : 'Non conta finché non fa accesso o firma una lettera'}
        </p>
      )
    }
    return (
      <p className="text-sm text-muted-foreground">
        {item.engagement_id === null ? 'In attesa del primo contratto firmato' : "Nessuna stima per l'incarico"}
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
      <p className="font-medium tabular-nums">{formatMoney(item.reward_amount)}</p>
      <p className="text-xs text-muted-foreground tabular-nums">
        {formatMoney(item.base_amount)} · {item.rate !== null ? formatRate(item.rate) : '-'}
      </p>
    </>
  )
}

/** The evidence behind the attribution (REB-657), read-only: the code used, when the
 *  referred person signed up, where from, whether the two email domains match and whether
 *  they ever logged in. A matching domain and a person who never logged in are the two
 *  that call for a second look, so they are said in words rather than left as a blank.
 *  The last line is whether the referral counts yet (REB-658), and what made it. */
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
        {evidence.same_email_domain === null
          ? 'Dominio email non disponibile'
          : evidence.same_email_domain
            ? 'Stesso dominio email'
            : 'Dominio email diverso'}
      </p>
      <p className={evidence.ever_logged_in === false ? 'font-medium' : 'text-muted-foreground'}>
        {evidence.ever_logged_in === null
          ? 'Accessi non disponibili'
          : evidence.ever_logged_in
            ? 'Ha già fatto accesso'
            : 'Non ha mai fatto accesso'}
      </p>
      <p className={item.referral_status === 'to_verify' ? 'font-medium' : 'text-muted-foreground'}>
        {item.referral_status === 'to_verify' || item.verified_at === null || item.verified_via === null
          ? 'Non ancora verificato'
          : `${REFERRAL_VERIFIED_VIA_LABELS[item.verified_via] ?? item.verified_via} · ${formatDate(item.verified_at)}`}
      </p>
    </div>
  )
}

function ReferralRow({ item }: { item: ReferralLedgerItem }) {
  const client = useQueryClient()
  const move = useMutation({
    mutationFn: ({ id, status }: { id: string; status: RewardStatus }) => admin.setReferralState(id, status),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.referrals() }),
  })
  const moveFailure = failureMessage(move.error, 'Non riesco a cambiare lo stato.')
  const rewardId = item.reward_id
  const next = item.status ? NEXT_STATE[item.status] : null
  const estimated = rewardId === null && item.projected_amount !== null
  const pending = rewardId === null && item.referral_status === 'to_verify'

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
            {item.referrer_name}
          </Link>
        ) : (
          <p className="font-medium">{item.referrer_name}</p>
        )}
        <p className="text-xs text-muted-foreground">{item.referrer_email}</p>
      </TableCell>
      <TableCell className="align-top">
        <EngagementCell item={item} />
      </TableCell>
      <TableCell className="text-right align-top">
        <RewardCell item={item} onPriced={() => void client.invalidateQueries({ queryKey: keys.referrals() })} />
      </TableCell>
      <TableCell className="align-top">
        {/* A hairline pill for an estimate, a filled one for a state a reward is really in. */}
        <Badge variant={estimated || pending ? 'outline' : 'pill'}>
          {STATE_LABELS[item.status ?? (pending ? 'to_verify' : estimated ? 'expected' : 'pending')] ?? item.status}
        </Badge>
      </TableCell>
      <TableCell className="text-right align-top text-muted-foreground">{formatDate(item.created_at)}</TableCell>
      <TableCell className="text-right align-top">
        {rewardId !== null && next && (item.status === 'to_confirm' ? item.reward_amount !== null : true) && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={move.isPending}
            onClick={() => move.mutate({ id: rewardId, status: next })}
          >
            {item.status ? NEXT_LABEL[item.status] : ''}
          </Button>
        )}
        {moveFailure && (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {moveFailure}
          </p>
        )}
      </TableCell>
    </TableRow>
  )
}

/**
 * The referral ledger (P-REB-44): every reward a signed first letter earned, newest
 * first, with the two rates that decide how much right above it -- a database row an
 * admin edits here, never an environment variable (design record 2026-09-26). A
 * reward with no computed figure (a `lump_sum` letter with no estimated days) gets
 * one typed in by hand instead of the usual line, and moves only once it has one. An
 * admin confirms a reward, then marks it paid once the transfer happened outside the
 * hub -- tracking only, nothing here pays anyone.
 */
export function AdminReferrals() {
  const [status, setStatus] = useState<RewardStatus | undefined>(undefined)
  const { list, items } = useCursorList({
    queryKey: keys.referrals(status),
    fetchPage: (cursor) => admin.referrals({ status, cursor }),
    itemsOf: pageItems,
  })

  return (
    <>
      <Header title="Referral" count={items.length}>
        <StateFilter
          states={STATES}
          value={status}
          onChange={(value) => setStatus(value as RewardStatus | undefined)}
          labels={STATE_LABELS}
        />
      </Header>

      <RateForm />

      {list.isError ? (
        <Empty>Non riesco a leggere il registro.</Empty>
      ) : list.isPending ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <Empty>{status ? 'Nessun referral in questo stato.' : 'Nessun referral ancora.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table scrollLabel="Referral">
                <TableHeader>
                  <TableRow>
                    <TableHead>Segnalato</TableHead>
                    <TableHead>Segnalato da</TableHead>
                    <TableHead>Incarico</TableHead>
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
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrati ${items.length} referral, ce ne sono altri.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}
