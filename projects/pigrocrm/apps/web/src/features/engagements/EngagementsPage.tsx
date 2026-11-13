import { useNavigate, useSearch } from '@tanstack/react-router'
import { BriefcaseBusiness, Plus, SlidersHorizontal } from 'lucide-react'
import { useEffect, useState } from 'react'
import { FilterRow } from '@/components/FilterRow'
import { LoadMore } from '@/components/LoadMore'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Label } from '@rebase/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@rebase/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { OrganizationPicker } from '@/features/organizations/OrganizationPicker'
import { useIsAdmin } from '@/lib/auth'
import { EngagementFormDialog } from './EngagementFormDialog'
import { EngagementsTable } from './EngagementsTable'
import { STATE_LABELS, TABS, type EngagementBucket, type EngagementState } from './labels'
import { useEngagementCounts, useEngagementList } from './queries'
import { isFiltered, type EngagementsSearch } from './search'

/** The table from `md` up, cards below it (the inbox's own breakpoint). */
const TABLE = '(min-width: 768px)'

/** `Select` needs a non-empty string for «no filter»; the API parameter is simply left out. */
const ANY = 'all'

const STATES = Object.keys(STATE_LABELS) as EngagementState[]

const EMPTY_SENTENCE = 'Qui vedi le lettere di incarico dei freelance, dalla bozza alla firma.'

/**
 * The form the hub's «Crea match» opens (REB-1105): the link carries the engagement id the hub minted
 * (`hub_match_id`) and what to fill in. Opening it again after the draft was saved must not make a second
 * engagement, since the id is unique: the existing one is shown instead. The form opens only once the
 * list says no engagement has that id.
 */
function HubHandoff({ search }: { search: EngagementsSearch }) {
  const navigate = useNavigate()
  const hubMatchId = search.hub_match_id
  const existing = useEngagementList(null, { hubMatchId })
  const found = existing.items[0]
  useEffect(() => {
    if (found !== undefined) {
      void navigate({ to: '/app/engagements/$engagementId', params: { engagementId: found.id }, replace: true })
    }
  }, [found, navigate])
  if (existing.isLoading || found !== undefined) return null
  if (existing.isError) return <QueryErrorBanner error={existing.error} onRetry={existing.refetch} />
  return (
    <EngagementFormDialog
      open
      onOpenChange={(open) => {
        // Closing leaves the link behind: the page is the plain list again.
        if (!open) void navigate({ to: '/app/engagements', search: { tab: search.tab }, replace: true })
      }}
      freelancerId={search.freelancer_id}
      prefill={{ clientId: search.client_id, dealId: search.deal_id, hubMatchId }}
    />
  )
}

/** The search as the URL should carry it: only the filters that apply. */
function compact(search: EngagementsSearch): EngagementsSearch {
  return {
    tab: search.tab,
    freelancer: search.freelancer || undefined,
    client: search.client || undefined,
    state: search.state,
  }
}

/**
 * «Freelance» and «Cliente» as search-as-you-type pickers: a popover on a wide screen, where a list
 * of matches under the filter row would push the table down, and in place in the phone's sheet.
 */
function OrganizationFilter({
  id,
  role,
  label,
  value,
  onChange,
  inline,
}: {
  id: string
  role: 'freelancer' | 'customer'
  label: string
  value: string | undefined
  onChange: (id: string | undefined) => void
  inline: boolean
}) {
  const [open, setOpen] = useState(false)
  const picker = (
    <OrganizationPicker
      id={id}
      role={role}
      value={value ?? null}
      onChange={(organization) => {
        onChange(organization?.id)
        setOpen(false)
      }}
      placeholder={`Cerca ${role === 'freelancer' ? 'un freelance' : 'un cliente'} per nome, P.IVA o email…`}
      ariaLabel={`Cerca ${role === 'freelancer' ? 'un freelance' : 'un cliente'}`}
    />
  )
  if (inline || value !== undefined) return <div className="w-full md:w-64">{picker}</div>
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          {label}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80">
        {picker}
      </PopoverContent>
    </Popover>
  )
}

function StateSelect({
  value,
  onChange,
}: {
  value: EngagementState | undefined
  onChange: (state: EngagementState | undefined) => void
}) {
  return (
    <Select
      value={value ?? ANY}
      onValueChange={(next) => onChange(next === ANY ? undefined : (next as EngagementState))}
    >
      <SelectTrigger className="w-full md:w-52" aria-label="Stato">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ANY}>Tutti gli stati</SelectItem>
        {STATES.map((state) => (
          <SelectItem key={state} value={state}>
            {STATE_LABELS[state]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/**
 * «Collaboratori» (REB-1035, the approved brief of 2026-11-10, named after the module in REB-1053): the
 * engagements of the space, which it still calls «incarichi», one row
 * each, in three disjoint tabs, «Da fare», «In corso» and «Chiusi». The tab and the filters are in
 * the URL. It is an administrator's page: anyone else who types the address reads one sentence and
 * no figure, and no request is made. A count is drawn only once it arrived, and never beside an
 * error.
 */
export function EngagementsPage() {
  const search = useSearch({ from: '/app/engagements/' })
  const navigate = useNavigate()
  const admin = useIsAdmin()
  const wide = useMediaQuery(TABLE)
  const [creating, setCreating] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)

  const list = useEngagementList(search.tab, {
    freelancerId: search.freelancer,
    clientId: search.client,
    state: search.state,
  })
  const counts = useEngagementCounts()

  if (!admin) {
    return (
      <>
        <PageHeader
          icon={BriefcaseBusiness}
          title="Collaboratori"
          description="Le lettere di incarico dei freelance: dalla bozza alla firma, i giorni e i pagamenti."
        />
        <p className="px-8 pb-8 text-sm text-muted-foreground">
          Gli incarichi dei freelance li vedono solo gli amministratori.
        </p>
      </>
    )
  }

  function change(patch: Partial<EngagementsSearch>) {
    void navigate({ to: '/app/engagements', search: compact({ ...search, ...patch }), replace: true })
  }

  const filtered = isFiltered(search)
  // A count that never arrived, sits beside an error or counts something else than the filtered
  // list is not drawn: no figure is shown that is not known.
  const known = list.isError || filtered ? undefined : counts.data
  const total = known?.[search.tab]

  const controls = (inline: boolean) => (
    <>
      <OrganizationFilter
        id="engagements-freelancer"
        role="freelancer"
        label="Freelance"
        value={search.freelancer}
        onChange={(freelancer) => change({ freelancer })}
        inline={inline}
      />
      <OrganizationFilter
        id="engagements-client"
        role="customer"
        label="Cliente"
        value={search.client}
        onChange={(client) => change({ client })}
        inline={inline}
      />
      <StateSelect value={search.state} onChange={(state) => change({ state })} />
    </>
  )

  const newButton = (
    <Button onClick={() => setCreating(true)}>
      <Plus className="mr-2 size-4" aria-hidden />
      Nuovo incarico
    </Button>
  )

  let body
  if (list.isError && list.items.length === 0) {
    body = <QueryErrorBanner error={list.error} onRetry={list.refetch} />
  } else if (list.isLoading) {
    body = <LoadingState label="Leggo gli incarichi…" className="py-8" />
  } else if (list.items.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-3 border bg-card p-6">
        {filtered ? (
          <p className="text-sm text-muted-foreground">Nessun incarico con questi filtri.</p>
        ) : search.tab === 'todo' ? (
          <p className="text-sm text-muted-foreground">
            Niente da fare: nessun incarico aspetta te.
          </p>
        ) : (
          <>
            <p className="font-medium">Nessun incarico.</p>
            <p className="text-sm text-muted-foreground">{EMPTY_SENTENCE}</p>
            {wide ? newButton : null}
          </>
        )}
      </div>
    )
  } else {
    body = <EngagementsTable engagements={list.items} />
  }

  return (
    <Tabs value={search.tab} onValueChange={(next) => change({ tab: next as EngagementBucket })}>
      <PageHeader
        icon={BriefcaseBusiness}
        title="Collaboratori"
        description="Le lettere di incarico dei freelance: dalla bozza alla firma, i giorni e i pagamenti."
        actions={wide ? newButton : undefined}
        tabs={
          <TabsList variant="line">
            {TABS.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id}>
                {tab.label}
                {known === undefined ? null : (
                  <span className="ml-2 tabular-nums text-muted-foreground">{known[tab.id]}</span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        }
      >
        {wide ? (
          <FilterRow>{controls(false)}</FilterRow>
        ) : (
          <div role="search" aria-label="Filtra l’elenco" className="flex items-center gap-2 px-8 pb-4">
            <Button variant="outline" size="sm" onClick={() => setFiltersOpen(true)}>
              <SlidersHorizontal className="mr-2 size-4" aria-hidden />
              Filtri
            </Button>
          </div>
        )}
      </PageHeader>

      <div className="px-8 pb-8">
        {body}
        {list.hasMore ? (
          <LoadMore
            label={total === undefined ? 'Mostra altri' : `Mostra altri (${total} in tutto)`}
            message={
              total === undefined
                ? `Mostrati ${list.items.length} incarichi, ce ne sono altri.`
                : `Mostrati ${list.items.length} incarichi su ${total}.`
            }
            isFetchingMore={list.isFetchingMore}
            hasError={list.isError}
            error={list.error}
            onLoadMore={list.fetchMore}
          />
        ) : null}
      </div>

      {wide ? null : (
        <div className="sticky bottom-0 border-t bg-background p-4">
          <Button className="w-full" onClick={() => setCreating(true)}>
            <Plus className="mr-2 size-4" aria-hidden />
            Nuovo incarico
          </Button>
        </div>
      )}

      {wide ? null : (
        <Sheet open={filtersOpen} onOpenChange={setFiltersOpen}>
          <SheetContent side="bottom" closeLabel="Chiudi" className="max-h-[85vh] gap-4 overflow-y-auto p-4">
            <SheetTitle>Filtri</SheetTitle>
            <SheetDescription className="sr-only">Freelance, cliente e stato degli incarichi.</SheetDescription>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="engagements-freelancer">Freelance</Label>
                <OrganizationFilter
                  id="engagements-freelancer"
                  role="freelancer"
                  label="Freelance"
                  value={search.freelancer}
                  onChange={(freelancer) => change({ freelancer })}
                  inline
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="engagements-client">Cliente</Label>
                <OrganizationFilter
                  id="engagements-client"
                  role="customer"
                  label="Cliente"
                  value={search.client}
                  onChange={(client) => change({ client })}
                  inline
                />
              </div>
              <div className="space-y-2">
                <Label>Stato</Label>
                <StateSelect value={search.state} onChange={(state) => change({ state })} />
              </div>
            </div>
            <Button onClick={() => setFiltersOpen(false)}>Mostra gli incarichi</Button>
          </SheetContent>
        </Sheet>
      )}

      <EngagementFormDialog open={creating} onOpenChange={setCreating} />
      {search.hub_match_id === undefined ? null : <HubHandoff search={search} />}
    </Tabs>
  )
}
