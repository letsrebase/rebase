import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { BadgeCheck, FileText } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Toaster, toast } from '@rebase/ui/sonner'
import { TeamBuilder } from '@/components/TeamBuilder'
import { SEARCH_DEBOUNCE_MS, useDebounce } from '@/lib/adminList'
import { ApiError, cloud, type Band, type CloudFilters, type CloudTalent } from '@/lib/api'
import { DAY_BANDS, bandLabel } from '@/lib/bands'
import { CLOUD_VETTED_LABEL, REMOTO_LABELS, SENIORITY_LABELS, formatExperience } from '@/lib/format'

/** The builder's own thanks (`TeamBuilder.tsx`): a request from a card is the same
 *  request, and the same people write back. */
const THANKS = 'Grazie: ti scriviamo entro due giorni lavorativi.'
/** Core's `cloud.CLOUD_LIST_CAP`: the one page the cloud has, until a later card
 *  paginates it (spec § 4.2). */
const CAP = 200
/** A `Select` needs a non-empty value for «no filter»; the query leaves the key out. */
const ANY = 'tutti'

type SelectFilter = 'ruolo' | 'seniority' | 'modalita' | 'fascia'
type Choices = Record<SelectFilter, string>

const NO_CHOICE: Choices = { ruolo: ANY, seniority: ANY, modalita: ANY, fascia: ANY }

/** A band as a `Select` value: `500-650`, `800-` for the one with no top. */
function bandValue(band: Band): string {
  return `${band.min}-${band.max ?? ''}`
}

/** The filters as the API takes them: a band becomes its bottom and its top, a blank
 *  skill or «Tutti» nothing at all. */
function toFilters(choices: Choices, competenza: string): CloudFilters {
  const filters: CloudFilters = {}
  if (choices.ruolo !== ANY) filters.ruolo = choices.ruolo
  if (choices.seniority !== ANY) filters.seniority = choices.seniority
  if (choices.modalita !== ANY) filters.modalita = choices.modalita
  const band = DAY_BANDS.find((candidate) => bandValue(candidate) === choices.fascia)
  if (band) {
    filters.fascia_min = band.min
    if (band.max !== null) filters.fascia_max = band.max
  }
  if (competenza) filters.competenza = competenza
  return filters
}

/** The API's own sentence whenever it gave one; ours only when nothing answered. */
function sentence(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/** A link the freelancer gave, as its host («github.com/ada» reads as «github.com»);
 *  anything that is not http(s) is not a link this page opens. */
function linkLabel(url: string): string | null {
  try {
    const parsed = new URL(url)
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null
    return parsed.hostname.replace(/^www\./, '')
  } catch {
    return null
  }
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
  const competenza = useDebounce(skill.trim(), SEARCH_DEBOUNCE_MS)
  const filters = toFilters(choices, competenza)
  const talents = useQuery({
    queryKey: ['cloud', 'talents', filters],
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

  const { items, ruoli, capped } = talents.data
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
            <Select value={choices.ruolo} onValueChange={set('ruolo')}>
              <SelectTrigger id={`${ids}-ruolo`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Tutti</SelectItem>
                {ruoli.map((ruolo) => (
                  <SelectItem key={ruolo} value={ruolo}>
                    {ruolo}
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
          <Filter label="Competenza" id={`${ids}-competenza`}>
            <Input
              id={`${ids}-competenza`}
              type="search"
              maxLength={100}
              placeholder="Python, Figma, SAP…"
              value={skill}
              onChange={(event) => setSkill(event.target.value)}
            />
          </Filter>
          <Filter label="Modalità" id={`${ids}-modalita`}>
            <Select value={choices.modalita} onValueChange={set('modalita')}>
              <SelectTrigger id={`${ids}-modalita`} className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ANY}>Tutte</SelectItem>
                {Object.entries(REMOTO_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Filter>
          <Filter label="Fascia" id={`${ids}-fascia`}>
            <Select value={choices.fascia} onValueChange={set('fascia')}>
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
              <li key={talent.freelancer_id}>
                <TalentCard talent={talent} />
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

/** One talent: who, the role and the experience the card gives, «Verificato da rebase»
 *  when rebase vetted them, the anonymous description, the skills, sectors and
 *  languages, the work mode and the band, the links and the CV, and «Richiedi». */
function TalentCard({ talent }: { talent: CloudTalent }) {
  const [state, setState] = useState<'idle' | 'sending' | 'sent'>('idle')
  const { card } = talent
  const name = `${talent.nome} ${talent.cognome}`
  const links = talent.links
    .map((url) => ({ url, label: linkLabel(url) }))
    .filter((link): link is { url: string; label: string } => link.label !== null)
  const linkedin = talent.linkedin_url && linkLabel(talent.linkedin_url) ? talent.linkedin_url : null

  async function ask() {
    setState('sending')
    try {
      await cloud.ask(talent.freelancer_id)
      setState('sent')
      toast.success(THANKS)
    } catch (error) {
      setState('idle')
      toast.error(sentence(error, 'Non siamo riusciti a inviare la richiesta. Riprova.'))
    }
  }

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>
          <h3>{name}</h3>
        </CardTitle>
        <CardDescription>{`${card.ruolo}, ${SENIORITY_LABELS[card.seniority] ?? card.seniority}, ${formatExperience(card.anni)}`}</CardDescription>
        {talent.vetted && (
          <Badge variant="pill" className="mt-1">
            <BadgeCheck aria-hidden="true" />
            {CLOUD_VETTED_LABEL}
          </Badge>
        )}
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <p className="text-muted-foreground">{card.sintesi}</p>
        <Badges label="Competenze" items={card.competenze} variant="outline" />
        <Badges label="Settori" items={card.settori} variant="secondary" />
        <Badges label="Lingue" items={card.lingue} variant="secondary" />
        <p className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {talent.modalita && <span className="text-muted-foreground">{REMOTO_LABELS[talent.modalita]}</span>}
          <span className="font-medium">{talent.fascia ? bandLabel(talent.fascia) : 'Tariffa da definire'}</span>
        </p>
        {(linkedin || links.length > 0 || talent.ha_cv) && (
          <ul aria-label={`Link di ${name}`} className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {talent.ha_cv && (
              <li>
                <a
                  href={cloud.cvUrl(talent.freelancer_id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`Apri il CV di ${name}`}
                  className="inline-flex items-center gap-1 font-medium underline underline-offset-4"
                >
                  <FileText className="size-4" aria-hidden="true" />
                  Apri il CV
                </a>
              </li>
            )}
            {linkedin && (
              <li>
                <a href={linkedin} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">
                  LinkedIn
                </a>
              </li>
            )}
            {links.map((link) => (
              <li key={link.url}>
                <a href={link.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-auto pt-2">
          <Button
            type="button"
            aria-label={state === 'sent' ? `Richiesta inviata per ${name}` : `Richiedi ${name}`}
            disabled={state !== 'idle'}
            onClick={() => void ask()}
          >
            {state === 'sent' ? 'Richiesta inviata' : state === 'sending' ? 'Invio…' : 'Richiedi'}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function Badges({
  label,
  items,
  variant,
}: {
  label: string
  items: string[]
  variant: 'outline' | 'secondary'
}) {
  if (items.length === 0) return null
  return (
    <ul aria-label={label} className="flex flex-wrap gap-1.5">
      {items.map((item, index) => (
        <li key={`${index}-${item}`}>
          <Badge variant={variant}>{item}</Badge>
        </li>
      ))}
    </ul>
  )
}
