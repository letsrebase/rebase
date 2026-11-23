import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useId, useState, type ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Toaster } from '@rebase/ui/sonner'
import { TalentCloudCard } from '@/components/TalentCloudCard'
import { TeamBuilder } from '@/components/TeamBuilder'
import { ANY, SEARCH_DEBOUNCE_MS, useDebounce } from '@/lib/adminList'
import { ApiError, cloud, type Band, type CloudFilters } from '@/lib/api'
import { DAY_BANDS, bandLabel } from '@/lib/bands'
import { REMOTE_LABELS, SENIORITY_LABELS } from '@/lib/format'
import { keys } from '@/lib/query-keys'

/** Core's `cloud.CLOUD_LIST_CAP`: the one page the cloud has, until a later card
 *  paginates it (spec § 4.2). */
const CAP = 200

type SelectFilter = 'role' | 'seniority' | 'mode' | 'band'
type Choices = Record<SelectFilter, string>

const NO_CHOICE: Choices = { role: ANY, seniority: ANY, mode: ANY, band: ANY }

/** A band as a `Select` value: `500-650`, `800-` for the one with no top. */
function bandValue(band: Band): string {
  return `${band.min}-${band.max ?? ''}`
}

/** The filters as the API takes them: a band becomes its bottom and its top, a blank
 *  skill or «Tutti» nothing at all. */
function toFilters(choices: Choices, skill: string): CloudFilters {
  const filters: CloudFilters = {}
  if (choices.role !== ANY) filters.role = choices.role
  if (choices.seniority !== ANY) filters.seniority = choices.seniority
  if (choices.mode !== ANY) filters.mode = choices.mode
  const band = DAY_BANDS.find((candidate) => bandValue(candidate) === choices.band)
  if (band) {
    filters.min_band = band.min
    if (band.max !== null) filters.max_band = band.max
  }
  if (skill) filters.skill = skill
  return filters
}

/** The API's own sentence whenever it gave one; ours only when nothing answered. */
function sentence(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/**
 * The talent cloud (REB-519, spec § 4.2): for a company rebase admitted, the builder on
 * top, where «Assumi team» files the request at once with the company's own data, then
 * every talent by name with the links, the CV, the anonymous description, the work
 * mode, «Verificato da rebase» and the client's band, filtered by role, seniority, one
 * skill, work mode and band; «Richiedi» on a card asks for that person alone, with no
 * form, and a toast says thanks. Never the rate, the state or the notes: the API does
 * not carry them. Vetted first, then by name, at most 200, and the page says when the
 * cloud has more.
 *
 * The `Toaster` lives here, the one page of the hub that toasts; the layout takes it
 * when a second page does.
 */
export function Cloud() {
  const ids = useId()
  const [choices, setChoices] = useState<Choices>(NO_CHOICE)
  const [skill, setSkill] = useState('')
  const skillTerm = useDebounce(skill.trim(), SEARCH_DEBOUNCE_MS)
  const filters = toFilters(choices, skillTerm)
  const talents = useQuery({
    queryKey: keys.cloudTalents(filters),
    queryFn: () => cloud.talents(filters),
    placeholderData: keepPreviousData,
    retry: false,
  })

  if (talents.error instanceof ApiError && talents.error.status === 403) {
    return <Closed message={talents.error.message} />
  }
  if (talents.isPending) {
    return (
      <div className="flex items-center justify-center gap-2 p-12 text-sm text-muted-foreground">
        <Loader className="size-8" />
        Carico i profili…
      </div>
    )
  }
  if (!talents.data) {
    return (
      <div className="mx-auto max-w-2xl space-y-4 p-6">
        <p role="alert">{sentence(talents.error, 'Non riusciamo a leggere i profili. Riprova tra poco.')}</p>
        <Button variant="outline" onClick={() => void talents.refetch()}>
          Riprova
        </Button>
      </div>
    )
  }

  const { items, roles, capped } = talents.data
  const filtered = Object.keys(filters).length > 0
  const set = (key: SelectFilter) => (value: string) => setChoices((current) => ({ ...current, [key]: value }))

  return (
    <div className="mx-auto max-w-5xl space-y-12 p-6">
      <Toaster position="bottom-right" />
      <header className="space-y-2 pt-2">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">rebase</p>
        <h1 className="text-3xl font-semibold tracking-tight">Talent cloud</h1>
        <p className="max-w-2xl text-muted-foreground">
          I profili della community di rebase, con il nome, i link e il CV. Descrivi il progetto e ti
          proponiamo il team, oppure scegli tu chi ti serve e premi «Richiedi»: ti scriviamo noi.
        </p>
      </header>

      <section aria-labelledby={`${ids}-builder`} className="space-y-4">
        <div className="space-y-1">
          <h2 id={`${ids}-builder`} className="text-xl font-semibold tracking-tight">
            Proponi un team
          </h2>
          <p className="text-sm text-muted-foreground">
            «Assumi team» ci manda la richiesta con i dati della tua azienda: nessun modulo da compilare.
          </p>
        </div>
        <TeamBuilder mode="cloud" propose={cloud.propose} hire={cloud.hire} />
      </section>

      <section aria-labelledby={`${ids}-profili`} className="space-y-6">
        <h2 id={`${ids}-profili`} className="text-xl font-semibold tracking-tight">
          I profili
        </h2>

        <div role="group" aria-label="Filtri" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <Filter label="Ruolo" id={`${ids}-ruolo`}>
            <Select value={choices.role} onValueChange={set('role')}>
              <SelectTrigger id={`${ids}-ruolo`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Tutti</SelectItem>
                {roles.map((role) => (
                  <SelectItem key={role} value={role}>
                    {role}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Filter>
          <Filter label="Seniority" id={`${ids}-seniority`}>
            <Select value={choices.seniority} onValueChange={set('seniority')}>
              <SelectTrigger id={`${ids}-seniority`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Tutte</SelectItem>
                {Object.entries(SENIORITY_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Filter>
          <Filter label="Competenza" id={`${ids}-skill`}>
            <Input
              id={`${ids}-skill`}
              type="search"
              maxLength={100}
              placeholder="Python, Figma, SAP…"
              value={skill}
              onChange={(event) => setSkill(event.target.value)}
            />
          </Filter>
          <Filter label="Modalità" id={`${ids}-modalita`}>
            <Select value={choices.mode} onValueChange={set('mode')}>
              <SelectTrigger id={`${ids}-modalita`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Tutte</SelectItem>
                {Object.entries(REMOTE_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Filter>
          <Filter label="Fascia" id={`${ids}-fascia`}>
            <Select value={choices.band} onValueChange={set('band')}>
              <SelectTrigger id={`${ids}-fascia`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Tutte</SelectItem>
                {DAY_BANDS.map((band) => (
                  <SelectItem key={bandValue(band)} value={bandValue(band)}>
                    {bandLabel(band)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Filter>
        </div>

        <div aria-live="polite" className="space-y-1 text-sm text-muted-foreground">
          {items.length > 0 && <p>{items.length === 1 ? '1 profilo' : `${items.length} profili`}</p>}
          {capped && <p>Qui trovi i primi {CAP} profili: usa i filtri per vedere gli altri.</p>}
          {talents.isError && (
            <p role="alert" className="text-destructive">
              {sentence(talents.error, 'Non riusciamo a leggere i profili. Riprova tra poco.')}
            </p>
          )}
        </div>

        {items.length === 0 ? (
          filtered ? (
            <div className="space-y-3">
              <p>Nessun profilo corrisponde ai filtri.</p>
              <Button
                variant="outline"
                onClick={() => {
                  setChoices(NO_CHOICE)
                  setSkill('')
                }}
              >
                Togli i filtri
              </Button>
            </div>
          ) : (
            <p>Nel talent cloud non c’è ancora nessun profilo.</p>
          )
        ) : (
          <ul aria-label="Profili" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {items.map((talent) => (
              <li key={talent.freelancer_id} className="min-w-0">
                <TalentCloudCard talent={talent} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function Filter({ label, id, children }: { label: string; id: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  )
}

/** Signed in, and the cloud is not open for this account: the API's sentence, and the
 *  way back to the member area. */
function Closed({ message }: { message: string }) {
  return (
    <div className="mx-auto max-w-2xl space-y-4 p-6">
      <h1 className="text-3xl font-semibold tracking-tight">Talent cloud</h1>
      <p role="alert">{message}</p>
      <Button asChild variant="outline">
        <Link to="/me">Torna alla tua area</Link>
      </Button>
    </div>
  )
}
