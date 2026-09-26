import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams, useSearch } from '@tanstack/react-router'
import { ArrowLeft, Download, MoreHorizontal } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@rebase/ui/dropdown-menu'
import {
  admin,
  ApiError,
  type Comment,
  type CompaniesFilters,
  type Company,
  type CompanyOverride,
  type Freelancer,
  type FreelancerDraft,
  type FreelancerOverride,
  type Remoto,
  type Talento,
  type TalentiFilters,
} from '@/lib/api'
import { SEARCH_DEBOUNCE_MS, isFilterActive, useDebounce } from '@/lib/adminList'
import { AMOUNT_PROBLEM, amountFilter, machineAmount } from '@/lib/amount'
import {
  COMPANY_STATES,
  FREELANCER_LIST_STATES,
  FREELANCER_STATES,
  REMOTO_LABELS,
  STATE_LABELS,
  formatBytes,
  formatDate,
  formatDateTime,
  formatEuro,
} from '@/lib/format'
import { cn } from '@rebase/ui/cn'
import { AuditTrail } from './AuditTrail'
import { Comments } from './Comments'
import { CompanyOverrideDialog, FreelancerOverrideDialog, RecordLifecycle } from './Override'

const TONE: Record<string, string> = {
  nuovo: 'bg-[var(--color-royal-gold)]',
  lead: 'bg-muted',
  contattato: 'bg-muted-foreground',
  attivo: 'bg-foreground',
  in_corso: 'bg-foreground',
  scartato: 'bg-[var(--color-watermelon)]',
  chiuso: 'bg-muted-foreground',
}

function StatePill({ stato }: { stato: string }) {
  return (
    <Badge variant="pill" className="gap-1.5">
      <span aria-hidden="true" className={cn('size-1.5 rounded-full', TONE[stato] ?? 'bg-muted')} />
      {STATE_LABELS[stato] ?? stato}
    </Badge>
  )
}

/** Beside the state pill on a card that is missing the CV, the rate, the position or
 *  the remote preference. Either an admin wrote it from a signup and the person has not
 *  finished yet (ORB-155), or they filled the wizard and skipped the CV, which the
 *  wizard lets them do. */
function IncompletePill() {
  return <Badge variant="pill">Da completare</Badge>
}

/** Who the answers on a card come from, as the detail's «Scheda» row says it. */
function ownership(f: Freelancer): string {
  if (f.compilata_da === 'persona') return 'compilata dalla persona'
  return f.completa ? 'scritta dall’admin' : 'scritta dall’admin, da completare'
}

export function Header({ title, count, children }: { title: string; count?: number; children?: React.ReactNode }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-5">
      <h1 className="text-2xl font-semibold tracking-tight">
        {title}
        {count !== undefined && (
          <span className="ml-2 text-base font-normal text-muted-foreground">{count}</span>
        )}
      </h1>
      {children}
    </header>
  )
}

export function StateFilter({
  states,
  value,
  onChange,
  labels = STATE_LABELS,
}: {
  states: readonly string[]
  value: string | undefined
  onChange: (value: string | undefined) => void
  /** Talenti and Aziende share `STATE_LABELS`; a list over a different vocabulary
   *  (REB-413's own Match states) passes its own. */
  labels?: Record<string, string>
}) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filtra per stato">
      {[undefined, ...states].map((state) => (
        <Button
          key={state ?? 'tutti'}
          type="button"
          size="sm"
          variant={value === state ? 'default' : 'outline'}
          onClick={() => onChange(state)}
        >
          {state ? (labels[state] ?? state) : 'Tutti'}
        </Button>
      ))}
    </div>
  )
}

// A `Select` needs a non-empty string for "no filter"; the query string simply omits
// the key instead, same reasoning as `ANY` in the CRM's own fatture list.
const ANY = 'tutti'

function boolToSelect(value: boolean | undefined): string {
  return value === undefined ? ANY : value ? 'si' : 'no'
}

function selectToBool(value: string): boolean | undefined {
  return value === 'si' ? true : value === 'no' ? false : undefined
}

export function FilterField({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  )
}

/** A euro filter (REB-485): typed the Italian way, so «1.500» asks for 1500 whatever the
 *  browser's locale, which a number input did not (it read 1.5). The URL keeps what was
 *  typed; the query reads it through `amountFilter`, and a value that is not an amount
 *  narrows nothing and says so under the field. */
function AmountFilter({
  id,
  label,
  value,
  onChange,
}: {
  id: string
  label: string
  value: string | undefined
  onChange: (value: string | undefined) => void
}) {
  const problem = value !== undefined && amountFilter(value) === undefined
  const errorId = `${id}-error`
  return (
    <FilterField label={label} htmlFor={id}>
      <Input
        id={id}
        inputMode="decimal"
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value || undefined)}
        aria-invalid={problem || undefined}
        aria-describedby={problem ? errorId : undefined}
      />
      {problem && (
        <p role="alert" id={errorId} className="text-sm text-destructive">
          {AMOUNT_PROBLEM}
        </p>
      )}
    </FilterField>
  )
}

/** The affordance under a truncated page (REB-286), reimplemented here from the CRM's
 *  `LoadMoreInvoices` (PR #179) since this app may not import PigroCRM: a button that
 *  walks `next_cursor` one page further, a count of what is already on screen, and an
 *  `IntersectionObserver` sentinel so scrolling to the end does what the button does.
 *  Guarded against a missing `IntersectionObserver` (jsdom in tests) rather than
 *  shipping a polyfill for an admin-only page. */
export function LoadMore({
  label,
  isFetchingMore,
  onLoadMore,
}: {
  label: string
  isFetchingMore: boolean
  onLoadMore: () => void
}) {
  const sentinel = useRef<HTMLDivElement>(null)
  const onLoadMoreRef = useRef(onLoadMore)
  useEffect(() => {
    onLoadMoreRef.current = onLoadMore
  })
  useEffect(() => {
    const node = sentinel.current
    if (!node || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting) onLoadMoreRef.current()
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  return (
    <div ref={sentinel} className="flex flex-wrap items-center gap-3 px-6 py-4">
      <Button type="button" variant="outline" size="sm" onClick={onLoadMore} disabled={isFetchingMore}>
        {isFetchingMore ? 'Caricamento…' : 'Mostra altri'}
      </Button>
      <span role="status" className="text-sm text-muted-foreground">
        {label}
      </span>
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-6 py-10 text-center text-sm text-muted-foreground">{children}</p>
}

/** One number on a stats page («La guida», «Accessi»), inside a `<dl>`. */
export function Figure({ label, value, note }: { label: string; value: number; note?: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-3xl font-semibold tracking-tight">
        {value}
        {note && <span className="ml-2 text-base font-normal text-muted-foreground">{note}</span>}
      </dd>
    </div>
  )
}

// ---- talenti -----------------------------------------------------------------------------

/** «Talenti»: every freelancer card and every bare sign-up as one list (REB-282/283),
 *  `stato` `lead` for the bare ones and the freelancer's own state otherwise -- the
 *  single list that replaced «Developer e CTO» and «Iscrizioni». A card row opens the
 *  existing freelancer detail; a lead row opens the page that offers to draft one.
 *  REB-286 adds the search box, the filter row and infinite scroll, all three carried
 *  in the URL through `validateSearch` on `/admin/talent` (`router.tsx`) so a reload
 *  or a shared link reproduces the exact view; only `q` is debounced client-side
 *  before it reaches the URL and the query, everything else applies immediately like
 *  the state pills always have. */
export function AdminTalenti() {
  const search = useSearch({ from: '/signedIn/admin/talent' })
  const navigate = useNavigate()
  const [qInput, setQInput] = useState(search.q ?? '')
  const debouncedQ = useDebounce(qInput, SEARCH_DEBOUNCE_MS)

  useEffect(() => {
    if (debouncedQ === (search.q ?? '')) return
    void navigate({
      to: '/admin/talent',
      search: (prev: TalentiFilters) => ({ ...prev, q: debouncedQ || undefined }),
      replace: true,
    })
  }, [debouncedQ, navigate, search.q])

  function setFilter<K extends keyof TalentiFilters>(key: K, value: TalentiFilters[K]) {
    void navigate({
      to: '/admin/talent',
      search: (prev: TalentiFilters) => ({ ...prev, [key]: value }),
      replace: true,
    })
  }

  const filters: TalentiFilters = {
    ...search,
    q: debouncedQ || undefined,
    tariffa_min: amountFilter(search.tariffa_min),
    tariffa_max: amountFilter(search.tariffa_max),
  }
  const list = useInfiniteQuery({
    queryKey: ['talenti', filters],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.talent({ ...filters, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data?.pages])
  const activeFilters = isFilterActive(search)

  return (
    <>
      <Header title="Talenti" count={list.data?.pages[0]?.totale}>
        <StateFilter
          states={FREELANCER_LIST_STATES}
          value={search.stato}
          onChange={(value) => setFilter('stato', value)}
        />
      </Header>
      <div className="grid gap-4 border-b px-6 py-4 sm:grid-cols-2 lg:grid-cols-4">
        <FilterField label="Cerca" htmlFor="talenti-q">
          <Input
            id="talenti-q"
            type="search"
            maxLength={200}
            placeholder="Nome, cognome, email, posizione…"
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
          />
        </FilterField>
        <FilterField label="Posizione" htmlFor="talenti-posizione">
          <Input
            id="talenti-posizione"
            value={search.posizione ?? ''}
            onChange={(event) => setFilter('posizione', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Da remoto" htmlFor="talenti-remoto">
          <Select
            value={search.remoto ?? ANY}
            onValueChange={(value) => setFilter('remoto', value === ANY ? undefined : (value as Remoto))}
          >
            <SelectTrigger id="talenti-remoto">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutti</SelectItem>
              {Object.entries(REMOTO_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FilterField>
        <AmountFilter
          id="talenti-tariffa-min"
          label="Tariffa min (€/giorno)"
          value={search.tariffa_min}
          onChange={(value) => setFilter('tariffa_min', value)}
        />
        <AmountFilter
          id="talenti-tariffa-max"
          label="Tariffa max (€/giorno)"
          value={search.tariffa_max}
          onChange={(value) => setFilter('tariffa_max', value)}
        />
        <FilterField label="Pagina di provenienza" htmlFor="talenti-origine">
          <Input
            id="talenti-origine"
            placeholder="home, pigrocrm…"
            value={search.origine ?? ''}
            onChange={(event) => setFilter('origine', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="UTM source" htmlFor="talenti-utm-source">
          <Input
            id="talenti-utm-source"
            value={search.utm_source ?? ''}
            onChange={(event) => setFilter('utm_source', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Ha un CV" htmlFor="talenti-has-cv">
          <Select value={boolToSelect(search.has_cv)} onValueChange={(value) => setFilter('has_cv', selectToBool(value))}>
            <SelectTrigger id="talenti-has-cv">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutti</SelectItem>
              <SelectItem value="si">Sì</SelectItem>
              <SelectItem value="no">No</SelectItem>
            </SelectContent>
          </Select>
        </FilterField>
        <FilterField label="Ha fatto accesso" htmlFor="talenti-con-accessi">
          <Select
            value={boolToSelect(search.con_accessi)}
            onValueChange={(value) => setFilter('con_accessi', selectToBool(value))}
          >
            <SelectTrigger id="talenti-con-accessi">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY}>Tutti</SelectItem>
              <SelectItem value="si">Sì</SelectItem>
              <SelectItem value="no">No</SelectItem>
            </SelectContent>
          </Select>
        </FilterField>
        <FilterField label="Creato dal" htmlFor="talenti-creato-da">
          <Input
            id="talenti-creato-da"
            type="date"
            value={search.creato_da ?? ''}
            onChange={(event) => setFilter('creato_da', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Creato al" htmlFor="talenti-creato-a">
          <Input
            id="talenti-creato-a"
            type="date"
            value={search.creato_a ?? ''}
            onChange={(event) => setFilter('creato_a', event.target.value || undefined)}
          />
        </FilterField>
      </div>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{activeFilters ? 'Nessun risultato per questi filtri.' : 'Nessun profilo qui.'}</Empty>
      ) : (
        <>
          {/* The record's ruled table, the shape #228 gave the CRM's lists: a white
             container closed by a 1px ink line, 48px rows, a 2px rule under the header
             and a 1px separator between the cells, all from `@rebase/ui/table`. The
             padding the raw `th`/`td` used to type is the primitive's own. */}
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Chi</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead>Provenienza</TableHead>
                    <TableHead className="text-right">Quando</TableHead>
                    <TableHead className="w-12"><span className="sr-only">Azioni</span></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <TalentoRow key={item.id} item={item} />
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrati ${items.length} talenti, ce ne sono altri.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}
function TalentoRow({ item }: { item: Talento }) {
  const name = [item.nome, item.cognome].filter(Boolean).join(' ')
  const to = item.stato === 'lead' ? '/admin/talent/$id' : '/admin/freelance/$id'
  return (
    <TableRow>
      <TableCell>
        <Link to={to} params={{ id: item.id }} className="font-medium hover:underline">
          {name || '—'}
        </Link>
        <p className="text-xs text-muted-foreground">{item.email}</p>
      </TableCell>
      <TableCell>
        <StatePill stato={item.stato} />
      </TableCell>
      <TableCell className="text-muted-foreground">{item.origine}</TableCell>
      <TableCell className="text-right text-muted-foreground">{formatDate(item.created_at)}</TableCell>
      <TableCell className="w-12 text-right">
        {item.stato !== 'lead' && <TalentoMenu id={item.id} label={name || item.email} />}
      </TableCell>
    </TableRow>
  )
}

/** The row's own actions (REB-387), on a card only: a bare sign-up has no card to match
 *  and no contract to read. The name keeps linking to the card. */
function TalentoMenu({ id, label }: { id: string; label: string }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={`Azioni per ${label}`}>
          <MoreHorizontal aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem asChild>
          <Link to="/admin/freelance/$id/match/new" params={{ id }}>
            Crea match
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to="/admin/freelance/$id/contracts" params={{ id }}>
            Match e contratti
          </Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

interface LeadDraft {
  nome: string
  cognome: string
  linkedin_url: string
  posizione: string
  tariffa_giornaliera: string
  remoto: Remoto | ''
  links: string
  fonti: string
}

const LEAD_DRAFT_EMPTY: LeadDraft = {
  nome: '',
  cognome: '',
  linkedin_url: '',
  posizione: '',
  tariffa_giornaliera: '',
  remoto: '',
  links: '',
  fonti: '',
}

/** A bare sign-up (ORB-163, REB-283): what the landing knows, and the form that turns
 *  it into a card in place, through the same `draft_from_signup` the MCP tool
 *  `create_freelancer_from_signup` calls (ORB-155). One line per URL for «Link» and
 *  «Fonti»; at least one source is required, since a card written from research with
 *  no source is a card nobody can check. The row itself comes from the `talenti` list
 *  (`stato: 'lead'`): there is no single-sign-up fetch, so a direct visit refetches
 *  that page and reads its own row out of it. */
export function AdminTalentoLead() {
  const { id } = useParams({ from: '/signedIn/admin/talent/$id' })
  const navigate = useNavigate()
  const client = useQueryClient()
  const leads = useQuery({
    queryKey: ['talenti', 'lead'],
    queryFn: () => admin.talent({ stato: 'lead', limit: 500 }),
  })
  const lead = leads.data?.items.find((item) => item.id === id)
  const [draft, setDraft] = useState(LEAD_DRAFT_EMPTY)
  const seeded = useRef(false)
  useEffect(() => {
    if (lead && !seeded.current) {
      seeded.current = true
      setDraft((current) => ({
        ...current,
        nome: lead.nome ?? current.nome,
        cognome: lead.cognome ?? current.cognome,
        linkedin_url: lead.linkedin_url ?? current.linkedin_url,
      }))
    }
  }, [lead])

  const draftCard = useMutation({
    mutationFn: (data: FreelancerDraft) => admin.draftFromSignup(id, data),
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: ['talenti'] })
      void navigate({ to: '/admin/freelance/$id', params: { id: created.id } })
    },
  })
  const failure = draftCard.error instanceof ApiError ? draftCard.error : null
  const message = failure ? failure.message : draftCard.error ? 'Non riesco a creare la scheda.' : null
  const wrong = (field: string) => failure?.fields.includes(field) || undefined

  function field(name: keyof LeadDraft) {
    return (value: string) => setDraft((current) => ({ ...current, [name]: value }))
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    draftCard.mutate({
      nome: draft.nome.trim(),
      cognome: draft.cognome.trim(),
      linkedin_url: draft.linkedin_url.trim() || undefined,
      posizione: draft.posizione.trim() || undefined,
      tariffa_giornaliera: machineAmount(draft.tariffa_giornaliera) || undefined,
      remoto: draft.remoto || undefined,
      links: draft.links.split('\n').map((line) => line.trim()).filter(Boolean),
      fonti: draft.fonti.split('\n').map((line) => line.trim()).filter(Boolean),
    })
  }

  if (leads.isError) return <Empty>Non riesco a leggere questo lead.</Empty>
  if (leads.isPending) return <Empty>Caricamento…</Empty>
  if (!lead) return <Empty>Lead non trovato.</Empty>
  return (
    <>
      <Header title={[lead.nome, lead.cognome].filter(Boolean).join(' ') || lead.email}>
        <StatePill stato="lead" />
      </Header>
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <dl className="space-y-3 text-sm lg:col-span-2">
          <Row label="Email">
            <a className="underline underline-offset-2" href={`mailto:${lead.email}`}>
              {lead.email}
            </a>
          </Row>
          <Row label="LinkedIn">
            {lead.linkedin_url ? (
              <a className="underline underline-offset-2" href={lead.linkedin_url} target="_blank" rel="noreferrer">
                {lead.linkedin_url}
              </a>
            ) : (
              '—'
            )}
          </Row>
          <Row label="Arrivato">
            {formatDate(lead.created_at)}
            {lead.utm_source ? ` · da ${lead.utm_source}` : ''}
          </Row>
        </dl>
        <form onSubmit={submit} className="space-y-3 border p-4">
          <p className="text-sm font-medium">Scrivi la scheda</p>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="lead-nome">Nome</Label>
              <Input
                id="lead-nome"
                required
                maxLength={120}
                value={draft.nome}
                onChange={(event) => field('nome')(event.target.value)}
                aria-invalid={wrong('nome')}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="lead-cognome">Cognome</Label>
              <Input
                id="lead-cognome"
                required
                maxLength={120}
                value={draft.cognome}
                onChange={(event) => field('cognome')(event.target.value)}
                aria-invalid={wrong('cognome')}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lead-linkedin">LinkedIn</Label>
            <Input
              id="lead-linkedin"
              value={draft.linkedin_url}
              onChange={(event) => field('linkedin_url')(event.target.value)}
              aria-invalid={wrong('linkedin_url')}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lead-posizione">Posizione</Label>
            <Input
              id="lead-posizione"
              value={draft.posizione}
              onChange={(event) => field('posizione')(event.target.value)}
              aria-invalid={wrong('posizione')}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="lead-tariffa">Tariffa a giornata</Label>
              <Input
                id="lead-tariffa"
                inputMode="decimal"
                value={draft.tariffa_giornaliera}
                onChange={(event) => field('tariffa_giornaliera')(event.target.value)}
                aria-invalid={wrong('tariffa_giornaliera')}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="lead-remoto">Modalità</Label>
              <select
                id="lead-remoto"
                value={draft.remoto}
                onChange={(event) => field('remoto')(event.target.value)}
                className="h-9 w-full border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <option value="">—</option>
                {(Object.keys(REMOTO_LABELS) as Remoto[]).map((value) => (
                  <option key={value} value={value}>
                    {REMOTO_LABELS[value]}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lead-links">Link</Label>
            <Textarea
              id="lead-links"
              rows={2}
              placeholder="Un URL per riga"
              value={draft.links}
              onChange={(event) => field('links')(event.target.value)}
              aria-invalid={wrong('links')}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lead-fonti">Fonti</Label>
            <Textarea
              id="lead-fonti"
              required
              rows={2}
              placeholder="Da dove viene questa scheda: un URL per riga"
              value={draft.fonti}
              onChange={(event) => field('fonti')(event.target.value)}
              aria-invalid={wrong('fonti')}
            />
          </div>
          <Button type="submit" size="sm" disabled={draftCard.isPending}>
            {draftCard.isPending ? 'Creo…' : 'Crea scheda'}
          </Button>
          {message && (
            <p role="alert" className="text-sm text-destructive">
              {message}
            </p>
          )}
        </form>
      </div>
      <p className="px-6 pb-6">
        <Link to="/admin/talent" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutti i talenti
        </Link>
      </p>
    </>
  )
}

function StatusEditor({
  states,
  stato,
  note,
  onSave,
  saving,
}: {
  states: readonly string[]
  stato: string
  note: string | null
  onSave: (stato: string, note: string) => void
  saving: boolean
}) {
  const [draftState, setDraftState] = useState(stato)
  const [draftNote, setDraftNote] = useState(note ?? '')
  return (
    <div className="space-y-3 border p-4">
      <p className="text-sm font-medium">Stato e note</p>
      <StateFilter states={states} value={draftState} onChange={(value) => value && setDraftState(value)} />
      <Textarea
        aria-label="Note"
        value={draftNote}
        onChange={(event) => setDraftNote(event.target.value)}
        rows={3}
        placeholder="Una nota per chi rileggerà questa scheda"
      />
      <Button size="sm" onClick={() => onSave(draftState, draftNote)} disabled={saving}>
        {saving ? 'Salvo…' : 'Salva'}
      </Button>
    </div>
  )
}

/** What REB-284 adds to the freelancer detail beyond `Freelancer`: everywhere else in
 *  the hub that already knows this address, fetched server-side in the same call so
 *  the page does not fan out four requests of its own. */
interface FreelancerDetail extends Freelancer {
  /** The sign-up's own attribution, if this address left one on the landing --
   *  separate from `utm_source` above (the card's own), since the two can differ. */
  iscrizione_utm: {
    origine: string | null
    utm_source: string | null
    utm_medium: string | null
    utm_campaign: string | null
    utm_content: string | null
    utm_term: string | null
    utm_id: string | null
  } | null
  ultimi_accessi: { id: string; logged_at: string }[]
  ultimi_download_guida: { id: string; downloaded_at: string }[]
  pigro_slug: string | null
}

const ISCRIZIONE_UTM_LABELS: [key: keyof NonNullable<FreelancerDetail['iscrizione_utm']>, label: string][] = [
  ['origine', 'Origine'],
  ['utm_source', 'Sorgente'],
  ['utm_medium', 'Medium'],
  ['utm_campaign', 'Campagna'],
  ['utm_content', 'Contenuto'],
  ['utm_term', 'Termine'],
  ['utm_id', 'Id'],
]

/** The sign-up this address left on the landing, if it did, with its own UTM
 *  (REB-284): a section of its own since an admin-drafted card copies the signup's
 *  UTM onto the card at creation but a wizard card keeps its own, and the two can
 *  genuinely differ from what «Arrivato» shows above. */
function FreelancerIscrizione({ utm }: { utm: FreelancerDetail['iscrizione_utm'] }) {
  const known = utm ? ISCRIZIONE_UTM_LABELS.filter(([key]) => utm[key] !== null) : []
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">Iscrizione alla newsletter</h2>
      {known.length ? (
        <dl className="space-y-2 text-sm">
          {known.map(([key, label]) => (
            <Row key={key} label={label}>{utm?.[key]}</Row>
          ))}
        </dl>
      ) : (
        <p className="text-sm text-muted-foreground">
          {utm ? 'Iscritta, senza UTM registrati.' : 'Nessuna iscrizione con questo indirizzo.'}
        </p>
      )}
    </section>
  )
}

/** A short list of dated events («Ultimi accessi», «Download della guida»), newest
 *  first, with an empty state instead of nothing when there are none (REB-284). */
function RecentEvents({
  title,
  empty,
  items,
}: {
  title: string
  empty: string
  items: { id: string; when: string }[]
}) {
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">{title}</h2>
      {items.length ? (
        <ul className="space-y-1 text-sm">
          {items.map((item) => (
            <li key={item.id}>{formatDateTime(item.when)}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">{empty}</p>
      )}
    </section>
  )
}

export function AdminFreelancerDetail() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id' })
  const client = useQueryClient()
  const auditKey = ['audit', 'freelancers', id] as const
  const row = useQuery({
    queryKey: ['freelancer', id],
    queryFn: async () => (await admin.freelancer(id)) as FreelancerDetail,
  })
  const move = useMutation({
    mutationFn: ({ stato, note }: { stato: string; note: string }) =>
      admin.moveFreelancer(id, stato, note.trim() || null),
    // The PATCH answers the plain card, not the REB-284 sections, and its own
    // `commenti` is always `[]` (REB-356, same cause as REB-355's own mutations
    // below): merged onto the cached detail with `commenti` kept from the current
    // cache, or a save would drop both the sections and the thread from view.
    onSuccess: (updated: Freelancer) => {
      client.setQueryData<FreelancerDetail>(['freelancer', id], (current) =>
        current && { ...current, ...updated, commenti: current.commenti },
      )
      void client.invalidateQueries({ queryKey: ['freelancers'] })
    },
  })
  // Every mutation REB-355 adds shares `move`'s own merge-not-replace contract, plus a
  // fresh read of the trail: an override, a clear, a delete or a restore all write a
  // fresh `AdminAction` the admin expects to see without reloading the page. `commenti`
  // is kept from the current cache rather than `updated`'s own (always `[]`, since none
  // of these routes' response models fill it -- only `get` does): replacing it here
  // would wipe the thread from view until the next full fetch.
  function mergeAndRefresh(updated: Freelancer) {
    client.setQueryData<FreelancerDetail>(['freelancer', id], (current) =>
      current && { ...current, ...updated, commenti: current.commenti },
    )
    void client.invalidateQueries({ queryKey: ['freelancers'] })
    void client.invalidateQueries({ queryKey: auditKey })
  }
  const [overrideOpen, setOverrideOpen] = useState(false)
  const override = useMutation({
    mutationFn: (data: FreelancerOverride) => admin.overrideFreelancer(id, data),
    onSuccess: (updated) => {
      mergeAndRefresh(updated)
      setOverrideOpen(false)
    },
  })
  const clearCv = useMutation({ mutationFn: () => admin.clearFreelancerCv(id), onSuccess: mergeAndRefresh })
  const del = useMutation({ mutationFn: () => admin.deleteFreelancer(id), onSuccess: mergeAndRefresh })
  const restore = useMutation({ mutationFn: () => admin.restoreFreelancer(id), onSuccess: mergeAndRefresh })
  // The thread lives on the detail row, so a new comment goes into the same cache entry
  // and nothing is fetched twice.
  const onCommentAdded = (created: Comment) =>
    client.setQueryData<FreelancerDetail>(['freelancer', id], (current) =>
      current && { ...current, commenti: [created, ...current.commenti] },
    )
  if (row.isError) return <Empty>Scheda non trovata.</Empty>
  if (row.isPending) return <Empty>Caricamento…</Empty>
  const f = row.data
  const overrideFailure =
    override.error instanceof ApiError
      ? override.error.message
      : override.error
        ? 'Non riesco a salvare la scheda.'
        : null
  const clearCvFailure =
    clearCv.error instanceof ApiError
      ? clearCv.error.message
      : clearCv.error
        ? 'Non riesco a rimuovere il CV.'
        : null
  const lifecycleError = del.error ?? restore.error
  const lifecycleFailure =
    lifecycleError instanceof ApiError
      ? lifecycleError.message
      : lifecycleError
        ? 'Non riesco a completare l’operazione.'
        : null
  return (
    <>
      <Header title={`${f.nome} ${f.cognome}`}>
        <div className="flex flex-wrap items-center gap-2">
          <StatePill stato={f.stato} />
          {!f.completa && <IncompletePill />}
          {f.cv_filename !== null && f.cv_size !== null && f.deleted_at === null && (
            <>
              <Button asChild variant="outline" size="sm">
                <a href={admin.cvUrl(f.id)}>
                  <Download className="mr-2 size-4" />
                  CV · {formatBytes(f.cv_size)}
                </a>
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => clearCv.mutate()}
                disabled={clearCv.isPending}
              >
                {clearCv.isPending ? 'Rimuovo…' : 'Rimuovi CV'}
              </Button>
            </>
          )}
          {f.deleted_at === null && (
            <Button asChild variant="outline" size="sm">
              <Link to="/admin/freelance/$id/contracts" params={{ id: f.id }}>
                Match e contratti
              </Link>
            </Button>
          )}
          {f.deleted_at === null && (
            <Button type="button" variant="outline" size="sm" onClick={() => setOverrideOpen(true)}>
              Modifica scheda
            </Button>
          )}
          <RecordLifecycle
            deletedAt={f.deleted_at}
            deleting={del.isPending}
            restoring={restore.isPending}
            onDelete={() => del.mutate()}
            onRestore={() => restore.mutate()}
            error={lifecycleFailure}
          />
        </div>
      </Header>
      {clearCvFailure && (
        <p role="alert" className="px-6 text-sm text-destructive">
          {clearCvFailure}
        </p>
      )}
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <dl className="space-y-3 text-sm lg:col-span-2">
          <Row label="Email"><a className="underline underline-offset-2" href={`mailto:${f.email}`}>{f.email}</a></Row>
          <Row label="Posizione">{f.posizione ?? '—'}</Row>
          <Row label="Tariffa a giornata">
            {f.tariffa_giornaliera === null ? '—' : formatEuro(f.tariffa_giornaliera)}
          </Row>
          <Row label="Modalità">{f.remoto ? REMOTO_LABELS[f.remoto] : '—'}</Row>
          <Row label="LinkedIn">
            {f.linkedin_url ? <a className="underline underline-offset-2" href={f.linkedin_url} target="_blank" rel="noreferrer">{f.linkedin_url}</a> : '—'}
          </Row>
          <Row label="Link">
            {f.links.length ? (
              <ul className="space-y-1">
                {f.links.map((link) => (
                  <li key={link}><a className="underline underline-offset-2" href={link} target="_blank" rel="noreferrer">{link}</a></li>
                ))}
              </ul>
            ) : '—'}
          </Row>
          <Row label="Arrivato">{formatDate(f.created_at)}{f.utm_source ? ` · da ${f.utm_source}` : ''}{f.origine ? ` · pagina ${f.origine}` : ''}</Row>
          <Row label="Provenienza">{f.provenienza}</Row>
          <Row label="Accessi">
            {f.accessi === 0 || f.ultimo_accesso === null
              ? 'Mai entrato'
              : `${f.accessi} · ultimo ${formatDateTime(f.ultimo_accesso)}`}
          </Row>
          <Row label="Scheda">{ownership(f)}</Row>
        </dl>
        {f.deleted_at === null && (
          <StatusEditor
            states={FREELANCER_STATES}
            stato={f.stato}
            note={f.note}
            saving={move.isPending}
            onSave={(stato, note) => move.mutate({ stato, note })}
          />
        )}
      </div>
      <FreelancerIscrizione utm={f.iscrizione_utm} />
      <RecentEvents
        title="Ultimi accessi"
        empty="Non è mai entrata."
        items={f.ultimi_accessi.map((login) => ({ id: login.id, when: login.logged_at }))}
      />
      <RecentEvents
        title="Download della guida"
        empty="Non ha scaricato la guida."
        items={f.ultimi_download_guida.map((download) => ({
          id: download.id,
          when: download.downloaded_at,
        }))}
      />
      {f.pigro_slug && (
        <section className="space-y-2 px-6 pb-6">
          <h2 className="text-sm font-medium">Spazio PigroCRM</h2>
          <p className="text-sm"><code className="border bg-muted px-1.5 py-0.5">{f.pigro_slug}</code></p>
        </section>
      )}
      <AuditTrail
        kind="freelancers"
        id={f.id}
        canRevert={f.deleted_at === null}
        onReverted={() => void client.invalidateQueries({ queryKey: ['freelancer', id] })}
      />
      <Comments kind="freelancers" id={f.id} comments={f.commenti} onAdded={onCommentAdded} />
      <p className="px-6 pb-6">
        <Link to="/admin/talent" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutti i talenti
        </Link>
      </p>
      <FreelancerOverrideDialog
        freelancer={f}
        open={overrideOpen}
        onOpenChange={setOverrideOpen}
        onSave={(data) => override.mutate(data)}
        saving={override.isPending}
        error={overrideFailure}
      />
    </>
  )
}

export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[10rem_1fr] gap-3 border-b pb-3 last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

// ---- companies -------------------------------------------------------------------------

/** «Aziende»: every company request as a list. REB-286 adds the search box, the
 *  filter row and infinite scroll, all three carried in the URL through
 *  `validateSearch` on `/admin/companies` (`router.tsx`) the same way `AdminTalenti`
 *  carries its own -- see that component's own note for why only `q` is debounced. */
export function AdminCompanies() {
  const search = useSearch({ from: '/signedIn/admin/companies' })
  const navigate = useNavigate()
  const [qInput, setQInput] = useState(search.q ?? '')
  const debouncedQ = useDebounce(qInput, SEARCH_DEBOUNCE_MS)

  useEffect(() => {
    if (debouncedQ === (search.q ?? '')) return
    void navigate({
      to: '/admin/companies',
      search: (prev: CompaniesFilters) => ({ ...prev, q: debouncedQ || undefined }),
      replace: true,
    })
  }, [debouncedQ, navigate, search.q])

  function setFilter<K extends keyof CompaniesFilters>(key: K, value: CompaniesFilters[K]) {
    void navigate({
      to: '/admin/companies',
      search: (prev: CompaniesFilters) => ({ ...prev, [key]: value }),
      replace: true,
    })
  }

  const filters: CompaniesFilters = {
    ...search,
    q: debouncedQ || undefined,
    budget_min: amountFilter(search.budget_min),
    budget_max: amountFilter(search.budget_max),
  }
  const list = useInfiniteQuery({
    queryKey: ['companies', filters],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.companies({ ...filters, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const items = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data?.pages])
  const activeFilters = isFilterActive(search)

  return (
    <>
      <Header title="Aziende" count={list.data?.pages[0]?.totale}>
        <StateFilter states={COMPANY_STATES} value={search.stato} onChange={(value) => setFilter('stato', value)} />
      </Header>
      <div className="grid gap-4 border-b px-6 py-4 sm:grid-cols-2 lg:grid-cols-4">
        <FilterField label="Cerca" htmlFor="aziende-q">
          <Input
            id="aziende-q"
            type="search"
            maxLength={200}
            placeholder="Azienda, referente, email, progetto…"
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
          />
        </FilterField>
        <AmountFilter
          id="aziende-budget-min"
          label="Budget min (€/giorno)"
          value={search.budget_min}
          onChange={(value) => setFilter('budget_min', value)}
        />
        <AmountFilter
          id="aziende-budget-max"
          label="Budget max (€/giorno)"
          value={search.budget_max}
          onChange={(value) => setFilter('budget_max', value)}
        />
        <FilterField label="Periodo dal" htmlFor="aziende-periodo-da">
          <Input
            id="aziende-periodo-da"
            type="date"
            value={search.periodo_da ?? ''}
            onChange={(event) => setFilter('periodo_da', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Pagina di provenienza" htmlFor="aziende-origine">
          <Input
            id="aziende-origine"
            placeholder="home, pigrocrm…"
            value={search.origine ?? ''}
            onChange={(event) => setFilter('origine', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Creata dal" htmlFor="aziende-creato-da">
          <Input
            id="aziende-creato-da"
            type="date"
            value={search.creato_da ?? ''}
            onChange={(event) => setFilter('creato_da', event.target.value || undefined)}
          />
        </FilterField>
        <FilterField label="Creata al" htmlFor="aziende-creato-a">
          <Input
            id="aziende-creato-a"
            type="date"
            value={search.creato_a ?? ''}
            onChange={(event) => setFilter('creato_a', event.target.value || undefined)}
          />
        </FilterField>
      </div>
      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : items.length === 0 ? (
        <Empty>{activeFilters ? 'Nessun risultato per questi filtri.' : 'Nessuna richiesta qui.'}</Empty>
      ) : (
        <>
          <div className="px-6 pt-6 pb-6">
            <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Azienda</TableHead>
                    <TableHead>Progetto</TableHead>
                    <TableHead>Periodo</TableHead>
                    <TableHead className="text-right">Budget</TableHead>
                    <TableHead>Stato</TableHead>
                    <TableHead className="text-right">Quando</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <TableRow key={item.id}>
                      <TableCell>
                        <Link to="/admin/companies/$id" params={{ id: item.id }} className="font-medium hover:underline">
                          {item.nome_azienda}
                        </Link>
                        <p className="text-xs text-muted-foreground">{item.referente} · {item.email}</p>
                      </TableCell>
                      <TableCell className="max-w-xs truncate">{item.progetto}</TableCell>
                      <TableCell>dal {formatDate(item.periodo_da)}, {item.durata}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatEuro(item.budget_giornaliero)}</TableCell>
                      <TableCell><StatePill stato={item.stato} /></TableCell>
                      <TableCell className="text-right text-muted-foreground">{formatDate(item.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
          {list.hasNextPage && (
            <LoadMore
              label={`Mostrate ${items.length} aziende, ce ne sono altre.`}
              isFetchingMore={list.isFetchingNextPage}
              onLoadMore={() => void list.fetchNextPage()}
            />
          )}
        </>
      )}
    </>
  )
}

export function AdminCompanyDetail() {
  const { id } = useParams({ from: '/signedIn/admin/companies/$id' })
  const client = useQueryClient()
  const auditKey = ['audit', 'companies', id] as const
  const row = useQuery({ queryKey: ['company', id], queryFn: () => admin.company(id) })
  const move = useMutation({
    mutationFn: ({ stato, note }: { stato: string; note: string }) =>
      admin.moveCompany(id, stato, note.trim() || null),
    // The PATCH's own `commenti` is always `[]` (REB-356, same cause as REB-355's own
    // mutations below): merged onto the cached detail with `commenti` kept from the
    // current cache, or a save would wipe the thread from view.
    onSuccess: (updated: Company) => {
      client.setQueryData<Company>(['company', id], (current) =>
        current && { ...current, ...updated, commenti: current.commenti },
      )
      void client.invalidateQueries({ queryKey: ['companies'] })
    },
  })
  // Every mutation REB-355 adds shares `move`'s own merge-not-replace contract, plus a
  // fresh read of the trail: an override, a delete or a restore all write a fresh
  // `AdminAction` the admin expects to see without reloading the page. `commenti` is
  // kept from the current cache rather than `updated`'s own (always `[]`, since none of
  // these routes' response models fill it -- only `get` does): replacing it here would
  // wipe the thread from view until the next full fetch.
  function mergeAndRefresh(updated: Company) {
    client.setQueryData<Company>(['company', id], (current) =>
      current && { ...current, ...updated, commenti: current.commenti },
    )
    void client.invalidateQueries({ queryKey: ['companies'] })
    void client.invalidateQueries({ queryKey: auditKey })
  }
  const [overrideOpen, setOverrideOpen] = useState(false)
  const override = useMutation({
    mutationFn: (data: CompanyOverride) => admin.overrideCompany(id, data),
    onSuccess: (updated) => {
      mergeAndRefresh(updated)
      setOverrideOpen(false)
    },
  })
  const del = useMutation({ mutationFn: () => admin.deleteCompany(id), onSuccess: mergeAndRefresh })
  const restore = useMutation({ mutationFn: () => admin.restoreCompany(id), onSuccess: mergeAndRefresh })
  const onCommentAdded = (created: Comment) =>
    client.setQueryData<Company>(['company', id], (current) =>
      current && { ...current, commenti: [created, ...current.commenti] },
    )
  if (row.isError) return <Empty>Richiesta non trovata.</Empty>
  if (row.isPending) return <Empty>Caricamento…</Empty>
  const c = row.data
  const overrideFailure =
    override.error instanceof ApiError
      ? override.error.message
      : override.error
        ? 'Non riesco a salvare la richiesta.'
        : null
  const lifecycleError = del.error ?? restore.error
  const lifecycleFailure =
    lifecycleError instanceof ApiError
      ? lifecycleError.message
      : lifecycleError
        ? 'Non riesco a completare l’operazione.'
        : null
  return (
    <>
      <Header title={c.nome_azienda}>
        <div className="flex flex-wrap items-center gap-2">
          <StatePill stato={c.stato} />
          {c.deleted_at === null && (
            <Button type="button" variant="outline" size="sm" onClick={() => setOverrideOpen(true)}>
              Modifica richiesta
            </Button>
          )}
          <RecordLifecycle
            deletedAt={c.deleted_at}
            deleting={del.isPending}
            restoring={restore.isPending}
            onDelete={() => del.mutate()}
            onRestore={() => restore.mutate()}
            error={lifecycleFailure}
          />
        </div>
      </Header>
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <dl className="space-y-3 text-sm lg:col-span-2">
          <Row label="Referente">
            {c.referente} · <a className="underline underline-offset-2" href={`mailto:${c.email}`}>{c.email}</a>
            {c.telefono ? ` · ${c.telefono}` : ''}
          </Row>
          <Row label="Figura richiesta">{c.figura_richiesta}</Row>
          <Row label="Progetto"><p className="whitespace-pre-wrap">{c.progetto}</p></Row>
          <Row label="Periodo">dal {formatDate(c.periodo_da)}, {c.durata}</Row>
          <Row label="Budget a giornata">{formatEuro(c.budget_giornaliero)}</Row>
          <Row label="Modalità">
            {REMOTO_LABELS[c.remoto]}
            {c.giorni_presenza ? ` · ${c.giorni_presenza} giorni a settimana in sede` : ''}
          </Row>
          <Row label="Persone richieste">{c.numero_risorse}</Row>
          <Row label="Arrivata">{formatDate(c.created_at)}{c.utm_source ? ` · da ${c.utm_source}` : ''}{c.origine ? ` · pagina ${c.origine}` : ''}</Row>
        </dl>
        {c.deleted_at === null && (
          <StatusEditor
            states={COMPANY_STATES}
            stato={c.stato}
            note={c.note}
            saving={move.isPending}
            onSave={(stato, note) => move.mutate({ stato, note })}
          />
        )}
      </div>
      <AuditTrail
        kind="companies"
        id={c.id}
        canRevert={c.deleted_at === null}
        onReverted={() => void client.invalidateQueries({ queryKey: ['company', id] })}
      />
      <Comments kind="companies" id={c.id} comments={c.commenti} onAdded={onCommentAdded} />
      <p className="px-6 pb-6">
        <Link to="/admin/companies" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutte le aziende
        </Link>
      </p>
      <CompanyOverrideDialog
        company={c}
        open={overrideOpen}
        onOpenChange={setOverrideOpen}
        onSave={(data) => override.mutate(data)}
        saving={override.isPending}
        error={overrideFailure}
      />
    </>
  )
}
