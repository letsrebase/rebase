import { useInfiniteQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Input } from '@rebase/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type MatchesFilters, type MatchListItem, type MatchReferral } from '@/lib/api'
import { SEARCH_DEBOUNCE_MS, isFilterActive, useDebounce } from '@/lib/adminList'
import { matchHeadingId } from '@/lib/contracts'
import {
  MATCH_STATES,
  MATCH_STATE_LABELS,
  PIGRO_STATE_LABELS,
  REFERRAL_KIND_LABELS,
  REFERRAL_STATE_LABELS,
  formatDate,
  formatEuro,
  formatRate,
} from '@/lib/format'
import { Empty, FilterField, Header, LoadMore, StateFilter } from './lists'

// The server's own default page size (`LIST_LIMIT_DEFAULT`, `rebase_core.matches`):
// the same mechanism «Talenti» and «Aziende» paginate with, a page at a time through
// «Mostra altri», just offset rather than cursor (REB-413's own brief).
const PAGE_SIZE = 100

/** Where the match's link to its deal on Pigro stands (REB-497): a linked one opens its
 *  «Consuntivo» (spec § 3.5, REB-503), which links the deal itself; a match not active
 *  yet has no link to speak of. */
function PigroState({ item }: { item: MatchListItem }) {
  if (!item.pigro_stato) return <span className="text-muted-foreground">—</span>
  const label = PIGRO_STATE_LABELS[item.pigro_stato] ?? item.pigro_stato
  if (item.pigro_stato !== 'collegato') return <span>{label}</span>
  return (
    <Link
      to="/admin/matches/$id/report"
      params={{ id: item.id }}
      aria-label={`${label}: consuntivo del match con ${item.nome_azienda} come ${item.figura_richiesta}`}
      className="hover:underline"
    >
      {label}
    </Link>
  )
}

/** A referral's amount, projected or real, in euros; `-` where there is nothing to say. */
function ReferralAmount({ amount }: { amount: string | null }) {
  return <span className="tabular-nums">{amount === null ? '-' : formatEuro(amount)}</span>
}

/** Who referred which side of a match, at which rate, and what it earns (REB-609):
 *  a hairline `Previsto` while the first letter is not signed (an estimate), the reward's
 *  own filled state after it, and `Già maturato` when the referral already paid on another
 *  match -- a referral pays once, so there is no figure to show. */
function MatchReferrals({ referrals }: { referrals: MatchReferral[] }) {
  if (referrals.length === 0) return <span className="text-muted-foreground">Nessun referral</span>
  return (
    <ul className="space-y-2">
      {referrals.map((referral) => (
        <li key={referral.kind} className="space-y-0.5">
          <p>
            <span className="text-muted-foreground">{`Referral ${REFERRAL_KIND_LABELS[referral.kind]}: `}</span>
            <ReferrerName referral={referral} />
            {referral.stato !== 'gia_maturato' && (
              // One unit, so a narrow cell breaks before the rate, never inside a figure.
              <span className="whitespace-nowrap">
                {' · '}
                {referral.rate !== null ? formatRate(referral.rate) : '-'}
                {' · '}
                <ReferralAmount amount={referral.amount} />
              </span>
            )}
          </p>
          <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            <Badge variant={referral.stato === 'previsto' ? 'outline' : 'pill'}>
              {REFERRAL_STATE_LABELS[referral.stato] ?? referral.stato}
            </Badge>
            {referral.stato === 'gia_maturato' && 'Ha già maturato su un altro match.'}
          </p>
        </li>
      ))}
    </ul>
  )
}

/** The referrer, a link to his talent page when he has a card of his own. */
function ReferrerName({ referral }: { referral: Pick<MatchReferral, 'referrer_nome' | 'referrer_freelancer_id'> }) {
  if (referral.referrer_freelancer_id === null) return <span className="font-medium">{referral.referrer_nome}</span>
  return (
    <Link
      to="/admin/freelance/$id"
      params={{ id: referral.referrer_freelancer_id }}
      className="font-medium hover:underline"
    >
      {referral.referrer_nome}
    </Link>
  )
}

/** The letter's fee and what it is priced by, with the estimated days under it. */
function Compenso({ item }: { item: MatchListItem }) {
  if (item.lettera_compenso === null) return <span className="text-muted-foreground">—</span>
  const detail = [item.lettera_unita, item.giorni_previsti !== null ? `${item.giorni_previsti} gg` : null]
    .filter(Boolean)
    .join(' · ')
  return (
    <>
      <p className="font-medium tabular-nums">{formatEuro(item.lettera_compenso)}</p>
      {detail && <p className="text-xs text-muted-foreground">{detail}</p>}
    </>
  )
}

function MatchRow({ item }: { item: MatchListItem }) {
  const name = `${item.freelancer_nome} ${item.freelancer_cognome}`.trim()
  return (
    <TableRow>
      <TableCell className="align-top">
        <Link
          to="/admin/freelance/$id/contracts"
          params={{ id: item.freelancer_id }}
          className="font-medium hover:underline"
        >
          {name || item.freelancer_email}
        </Link>
        <p className="text-xs text-muted-foreground">{item.freelancer_email}</p>
      </TableCell>
      <TableCell className="align-top">
        {/* A deleted request's page answers not found: its name stays, unlinked, and says so. */}
        {item.company_deleted ? (
          <p className="font-medium">{item.nome_azienda}</p>
        ) : (
          <Link to="/admin/companies/$id" params={{ id: item.company_id }} className="font-medium hover:underline">
            {item.nome_azienda}
          </Link>
        )}
        <p className="text-xs text-muted-foreground">
          {item.figura_richiesta}
          {item.company_deleted && ' · richiesta eliminata'}
        </p>
      </TableCell>
      <TableCell className="min-w-48 space-y-1 align-top whitespace-normal">
        <Badge variant="pill">{MATCH_STATE_LABELS[item.stato] ?? item.stato}</Badge>
        {/* Where the match stands, in the words «Match e contratti» uses (REB-477): it
            names the letter's state too, so the «Lettera» column keeps just the number. */}
        <p className="text-xs text-muted-foreground">{item.situazione}</p>
      </TableCell>
      <TableCell className="align-top">
        {item.lettera_numero && <p className="text-sm">{`n. ${item.lettera_numero}`}</p>}
        <p className="text-xs text-muted-foreground">
          {item.lettera_data_inizio}
          {item.lettera_data_fine && ` · ${item.lettera_data_fine}`}
        </p>
      </TableCell>
      <TableCell className="text-right align-top">
        <Compenso item={item} />
      </TableCell>
      <TableCell className="min-w-80 align-top whitespace-normal">
        <MatchReferrals referrals={item.referrals} />
      </TableCell>
      <TableCell className="align-top">
        <PigroState item={item} />
      </TableCell>
      <TableCell className="text-right align-top text-muted-foreground">
        <p>{formatDate(item.created_at)}</p>
        <p className="text-xs">{item.created_by_nome || item.created_by_email}</p>
      </TableCell>
      <TableCell className="text-right align-top">
        {/* No page of its own: a match lives on its freelancer's «Match e contratti», which
            opens on this match's card (`matchHeadingId`). */}
        <Link
          to="/admin/freelance/$id/contracts"
          params={{ id: item.freelancer_id }}
          hash={matchHeadingId(item.id)}
          aria-label={`Dettaglio del match con ${item.nome_azienda} come ${item.figura_richiesta}`}
          className="text-sm underline-offset-2 hover:underline"
        >
          Dettaglio
        </Link>
      </TableCell>
    </TableRow>
  )
}

/** «Match» (REB-413): every match the admin area has, newest first -- the last
 *  addition to the milestone, over `MatchService.list_all` and the same filter/search/
 *  pagination mechanism «Talenti» and «Aziende» already use (`lists.tsx`), offset in
 *  place of a cursor since the list needs no keyset merge of two sources. */
export function AdminMatches() {
  const search = useSearch({ from: '/signedIn/admin/matches' })
  const navigate = useNavigate()
  const urlQ = search.q ?? ''
  const [qInput, setQInput] = useState(urlQ)
  const [seenQ, setSeenQ] = useState(urlQ)
  const debouncedQ = useDebounce(qInput, SEARCH_DEBOUNCE_MS)

  // `?q=` moved while the page stayed open (back, forward, a link): unless this box
  // wrote it, the box follows the URL (Greptile 4092036048). Adjusted while rendering,
  // React's own pattern for state that tracks a changing input.
  if (urlQ !== seenQ) {
    setSeenQ(urlQ)
    if (urlQ !== debouncedQ) setQInput(urlQ)
  }

  // Only a settled box writes the URL: while the debounce still holds an older value,
  // writing it would put back the search the URL just moved away from.
  useEffect(() => {
    if (debouncedQ !== qInput || debouncedQ === urlQ) return
    void navigate({
      to: '/admin/matches',
      search: (prev: MatchesFilters) => ({ ...prev, q: debouncedQ || undefined }),
      replace: true,
    })
  }, [debouncedQ, qInput, navigate, urlQ])

  function setFilter<K extends keyof MatchesFilters>(key: K, value: MatchesFilters[K]) {
    void navigate({
      to: '/admin/matches',
      search: (prev: MatchesFilters) => ({ ...prev, [key]: value }),
      replace: true,
    })
  }

  // The URL drives the request: the box reaches it through the URL, debounced.
  const filters: MatchesFilters = search
  const list = useInfiniteQuery({
    queryKey: ['matches', filters],
    queryFn: ({ pageParam }: { pageParam: number }) =>
      admin.matches({ ...filters, limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => {
      const seen = pages.reduce((total, page) => total + page.items.length, 0)
      return seen < lastPage.totale ? seen : undefined
    },
  })
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data?.pages])
  const activeFilters = isFilterActive(search)

  return (
    <>
      <Header title="Match" count={list.data?.pages[0]?.totale}>
        <StateFilter
          states={MATCH_STATES}
          value={search.stato}
          onChange={(value) => setFilter('stato', value)}
          labels={MATCH_STATE_LABELS}
        />
      </Header>
      <div className="grid gap-4 border-b px-6 py-4 sm:grid-cols-2 lg:grid-cols-4">
        <FilterField label="Cerca" htmlFor="matches-q">
          <Input
            id="matches-q"
            type="search"
            maxLength={200}
            placeholder="Nome, cognome, email, azienda…"
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
          />
        </FilterField>
      </div>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{activeFilters ? 'Nessun risultato per questi filtri.' : 'Nessun match qui.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Freelance</TableHead>
                    <TableHead>Azienda</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead>Lettera</TableHead>
                    <TableHead className="text-right">Compenso</TableHead>
                    <TableHead>Referral</TableHead>
                    <TableHead>Pigro</TableHead>
                    <TableHead className="text-right">Creato</TableHead>
                    <TableHead>
                      <span className="sr-only">Azioni</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <MatchRow key={item.id} item={item} />
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrati ${items.length} match, ce ne sono altri.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}
