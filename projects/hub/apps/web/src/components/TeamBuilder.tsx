import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import {
  ApiError,
  team,
  type TeamEconomia,
  type TeamMember,
  type TeamProposal,
  type TeamProposalCreate,
} from '@/lib/api'
import { bandLabel } from '@/lib/bands'
import { REMOTO_LABELS, SENIORITY_LABELS, formatExperience } from '@/lib/format'

/** The lengths core's `TeamProposalCreate` takes (`team_schemas.py`), measured as it
 *  measures them, stripped: the page says so before the API has to. */
const DESCRIZIONE_MIN = 40
const DESCRIZIONE_MAX = 4000
const NOTA_MAX = 500

/** Three projects a visitor can start from, each a description the API takes as it
 *  stands: remote, on site with a city, and a team that is not only developers. */
const EXAMPLES = [
  {
    label: 'Web app per una fintech',
    text: 'Siamo una fintech e ci serve una web app per i nostri clienti: dashboard dei conti, pagamenti e collegamento alle API di open banking. React e TypeScript davanti, Python dietro. Sei mesi, da remoto.',
  },
  {
    label: 'Pipeline dati in sede a Milano',
    text: 'Dobbiamo portare gli ordini di tre gestionali in un data warehouse su Google Cloud, con una pipeline affidabile e i report per la direzione. Quattro mesi, in sede a Milano tre giorni a settimana.',
  },
  {
    label: 'App mobile con un designer',
    text: 'Vogliamo lanciare un’app per prenotare le lezioni in palestra, su iOS e Android: serve chi la sviluppa, in React Native o Flutter, e un designer che ne curi l’esperienza e l’interfaccia. Tre mesi, da remoto.',
  },
]

/** The wizards' own test of an address (`CompanyWizard.tsx`, `FreelancerWizard.tsx`). */
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const PENDING = 'Sto leggendo i profili…'
const THANKS = 'Grazie: ti scriviamo entro due giorni lavorativi.'

/** `public`: «Assumi team» opens the three contacts, for a visitor with no account.
 *  `cloud` (D3): the signed-in company proposes through its own route and «Assumi
 *  team» files the request at once, with no form. */
export type TeamBuilderProps =
  | { mode: 'public' }
  | {
      mode: 'cloud'
      propose: (body: TeamProposalCreate) => Promise<TeamProposal>
      hire: (proposalId: string) => Promise<unknown>
    }

type Run = 'proponi' | 'rigenera'

/** The API's own sentence whenever it gave one (the builder off, too many requests,
 *  Claude not answering, a field, a proposal already requested); ours only when nothing
 *  answered at all. */
function sentence(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/**
 * The team builder (P-REB-43, spec § 3.1): a description, from scratch or from an
 * example, becomes an anonymous team with its price bands; a note and «Rigenera» ask
 * again with the proposal it replaces; «Assumi team» turns it into a request. A person
 * is shown by what the card says of them and nothing else: no name, no id, no place,
 * whatever the read carries.
 */
export function TeamBuilder(props: TeamBuilderProps) {
  const ids = useId()
  const [descrizione, setDescrizione] = useState('')
  const [descrizioneError, setDescrizioneError] = useState<string | null>(null)
  // The description the proposal on screen came from: «Rigenera» asks again about that
  // one, with the note, whatever the box says by then.
  const [result, setResult] = useState<{ proposal: TeamProposal; descrizione: string } | null>(null)
  const [nota, setNota] = useState('')
  const [running, setRunning] = useState<Run | null>(null)
  const [runError, setRunError] = useState<{ from: Run; message: string } | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const proposalId = result?.proposal.id
  const busy = running !== null

  // A proposal lands below the box, often below the fold: the focus follows it, so a
  // keyboard or a screen reader is not left on a button that has finished.
  useEffect(() => {
    if (proposalId) heading.current?.focus()
  }, [proposalId])

  async function run(from: Run) {
    let body: TeamProposalCreate
    if (from === 'proponi') {
      const text = descrizione.trim()
      if (text.length < DESCRIZIONE_MIN) {
        setDescrizioneError(`Raccontaci qualcosa in più: servono almeno ${DESCRIZIONE_MIN} caratteri.`)
        return
      }
      body = { descrizione: text }
    } else {
      if (!result) return
      const note = nota.trim()
      body = {
        descrizione: result.descrizione,
        ...(note ? { nota: note } : {}),
        previous_id: result.proposal.id,
      }
    }
    setDescrizioneError(null)
    setRunError(null)
    setRunning(from)
    try {
      const proposal = await (props.mode === 'cloud' ? props.propose(body) : team.propose(body))
      setResult({ proposal, descrizione: body.descrizione })
      setNota('')
    } catch (error) {
      setRunError({ from, message: sentence(error, 'Non siamo riusciti a proporre un team. Riprova.') })
    } finally {
      setRunning(null)
    }
  }

  const proposal = result?.proposal
  const descrizioneErrorId = `${ids}-descrizione-error`

  return (
    <div className="space-y-10">
      <form
        noValidate
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          void run('proponi')
        }}
      >
        <div className="space-y-2">
          <p id={`${ids}-esempi`} className="text-sm text-muted-foreground">
            Qualche esempio, per cominciare:
          </p>
          <div role="group" aria-labelledby={`${ids}-esempi`} className="flex flex-wrap gap-2">
            {EXAMPLES.map((example) => (
              <Button
                key={example.label}
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  setDescrizione(example.text)
                  setDescrizioneError(null)
                }}
              >
                {example.label}
              </Button>
            ))}
          </div>
        </div>
        <div className="space-y-2">
          <Textarea
            aria-label="Descrizione del progetto"
            aria-invalid={descrizioneError !== null}
            aria-describedby={descrizioneError ? descrizioneErrorId : undefined}
            placeholder="Descrivi il progetto: cosa va fatto, per quanto tempo, dove, con che tecnologie"
            value={descrizione}
            onChange={(event) => setDescrizione(event.target.value)}
            maxLength={DESCRIZIONE_MAX}
            rows={6}
            className="text-base md:text-base"
          />
          {descrizioneError && (
            <p role="alert" id={descrizioneErrorId} className="text-sm text-destructive">
              {descrizioneError}
            </p>
          )}
        </div>
        {runError?.from === 'proponi' && (
          <p role="alert" className="text-sm text-destructive">
            {runError.message}
          </p>
        )}
        <Button type="submit" disabled={busy}>
          {running === 'proponi' ? PENDING : 'Proponi il team'}
        </Button>
      </form>

      {proposal && (
        <section aria-labelledby={`${ids}-proposta`} className="space-y-6">
          <div className="space-y-2">
            <h2
              id={`${ids}-proposta`}
              ref={heading}
              tabIndex={-1}
              className="text-2xl font-semibold tracking-tight outline-none"
            >
              La nostra proposta
            </h2>
            <p>{proposal.riassunto}</p>
          </div>

          {proposal.team.length > 0 && (
            <>
              <ul aria-label="Il team" className="grid gap-4 sm:grid-cols-2">
                {proposal.team.map((member) => (
                  <li key={member.posizione}>
                    <MemberCard member={member} />
                  </li>
                ))}
              </ul>
              <TeamCost economia={proposal.economia} />
            </>
          )}

          <form
            noValidate
            className="space-y-2"
            onSubmit={(event) => {
              event.preventDefault()
              void run('rigenera')
            }}
          >
            <Label htmlFor={`${ids}-nota`}>Cosa cambieresti?</Label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                id={`${ids}-nota`}
                placeholder="Per esempio: togli il designer, serve più esperienza sul backend"
                value={nota}
                onChange={(event) => setNota(event.target.value)}
                maxLength={NOTA_MAX}
              />
              <Button type="submit" variant="outline" disabled={busy}>
                {running === 'rigenera' ? PENDING : 'Rigenera'}
              </Button>
            </div>
            {runError?.from === 'rigenera' && (
              <p role="alert" className="text-sm text-destructive">
                {runError.message}
              </p>
            )}
          </form>

          {proposal.team.length > 0 &&
            (props.mode === 'cloud' ? (
              <CloudHire key={proposal.id} proposalId={proposal.id} hire={props.hire} />
            ) : (
              <PublicHire key={proposal.id} proposalId={proposal.id} />
            ))}
        </section>
      )}
    </div>
  )
}

/** One person, by what their card says: the role in this team, why them, the
 *  anonymous description, seniority and years, the skills, the work mode, the band. */
function MemberCard({ member }: { member: TeamMember }) {
  const { scheda } = member
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>
          <h3>{member.ruolo}</h3>
        </CardTitle>
        <CardDescription>{`${scheda.ruolo}, ${SENIORITY_LABELS[scheda.seniority] ?? scheda.seniority}, ${formatExperience(scheda.anni)}`}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <p>{member.motivazione}</p>
        <p className="text-muted-foreground">{scheda.sintesi}</p>
        {scheda.competenze.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {scheda.competenze.map((skill, index) => (
              <Badge key={`${index}-${skill}`} variant="outline">
                {skill}
              </Badge>
            ))}
          </div>
        )}
        <p className="mt-auto flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {member.modalita && (
            <span className="text-muted-foreground">{REMOTO_LABELS[member.modalita]}</span>
          )}
          <span className="font-medium">
            {member.fascia ? bandLabel(member.fascia) : 'Tariffa da definire'}
          </span>
        </p>
      </CardContent>
    </Card>
  )
}

/** The team's bands per day and per month (spec § 3.3): the hub's sums, never the
 *  model's; «Tariffa da definire» when somebody has no band, since a sum that leaves a
 *  person out is a price nobody quoted. */
function TeamCost({ economia }: { economia: TeamEconomia }) {
  const id = useId()
  return (
    <section
      aria-labelledby={id}
      className="space-y-1 border-l-4 border-(--landing-ink) bg-card py-2 pl-4 pr-2"
    >
      <h3 id={id} className="font-medium">
        Quanto costa il team
      </h3>
      {economia.giorno ? (
        <>
          <p>{bandLabel(economia.giorno)}</p>
          {economia.mese && (
            <p>
              {bandLabel(economia.mese, 'mese')}{' '}
              <span className="text-muted-foreground">({economia.giorni_mese} giorni al mese)</span>
            </p>
          )}
        </>
      ) : (
        <p>Tariffa da definire</p>
      )}
    </section>
  )
}

type Contact = 'azienda' | 'email' | 'telefono'

const CONTACTS: {
  key: Contact
  label: string
  type: 'text' | 'email' | 'tel'
  autoComplete: string
  placeholder: string
  maxLength: number
}[] = [
  { key: 'azienda', label: 'Azienda', type: 'text', autoComplete: 'organization', placeholder: 'ACME Srl', maxLength: 200 },
  { key: 'email', label: 'Email', type: 'email', autoComplete: 'email', placeholder: 'nome@azienda.it', maxLength: 254 },
  { key: 'telefono', label: 'Telefono', type: 'tel', autoComplete: 'tel', placeholder: '+39 345 1234567', maxLength: 40 },
]

/** The company wizard's rules and words for the same three answers: a name, an
 *  address, a number of six characters at least. */
function check(value: Record<Contact, string>): Partial<Record<Contact, string>> {
  const problems: Partial<Record<Contact, string>> = {}
  if (!value.azienda.trim()) problems.azienda = 'Serve il nome dell’azienda.'
  if (!EMAIL.test(value.email.trim())) problems.email = 'Serve un indirizzo email valido.'
  if (value.telefono.trim().length < 6) problems.telefono = 'Serve un numero di telefono.'
  return problems
}

/** «Assumi team» on the public page: the three contacts, then the thanks. No account
 *  and no mail to the visitor (spec § 1): the admin writes. */
function PublicHire({ proposalId }: { proposalId: string }) {
  const ids = useId()
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState<Record<Contact, string>>({ azienda: '', email: '', telefono: '' })
  const [errors, setErrors] = useState<Partial<Record<Contact, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [done, setDone] = useState(false)
  const inputs = useRef<Partial<Record<Contact, HTMLInputElement | null>>>({})

  async function submit(event: FormEvent) {
    event.preventDefault()
    const problems = check(value)
    setErrors(problems)
    setFormError(null)
    const first = CONTACTS.find((contact) => problems[contact.key])
    if (first) {
      inputs.current[first.key]?.focus()
      return
    }
    setSending(true)
    try {
      await team.request({
        proposal_id: proposalId,
        azienda: value.azienda.trim(),
        email: value.email.trim(),
        telefono: value.telefono.trim(),
      })
      setDone(true)
    } catch (error) {
      const message = sentence(error, 'Non siamo riusciti a inviare la richiesta. Riprova.')
      // A 422 that names a contact goes under it, as the wizard does; one about the
      // proposal (gone, or already requested: a 409) is the form's.
      const field = error instanceof ApiError ? error.fields[0] : undefined
      const contact = CONTACTS.find((candidate) => candidate.key === field)
      if (contact) {
        setErrors({ [contact.key]: message })
        inputs.current[contact.key]?.focus()
      } else {
        setFormError(message)
      }
    } finally {
      setSending(false)
    }
  }

  if (done) {
    return (
      <p role="status" className="border-l-4 border-(--landing-ink) bg-card py-2 pl-4 pr-2 font-medium">
        {THANKS}
      </p>
    )
  }

  if (!open) {
    return (
      <Button type="button" size="lg" onClick={() => setOpen(true)}>
        Assumi team
      </Button>
    )
  }

  return (
    <form
      noValidate
      aria-labelledby={`${ids}-assumi`}
      className="space-y-4 border-(length:--landing-border-width) bg-card p-4 sm:p-6"
      onSubmit={(event) => void submit(event)}
    >
      <div>
        <h3 id={`${ids}-assumi`} className="text-lg font-semibold">
          Assumi team
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Tre contatti e ti scriviamo noi. Nessun account da creare.
        </p>
      </div>
      {CONTACTS.map((contact) => {
        const id = `${ids}-${contact.key}`
        const error = errors[contact.key]
        return (
          <div key={contact.key} className="space-y-2">
            <Label htmlFor={id}>{contact.label}</Label>
            <Input
              id={id}
              ref={(element) => {
                inputs.current[contact.key] = element
              }}
              type={contact.type}
              autoComplete={contact.autoComplete}
              placeholder={contact.placeholder}
              maxLength={contact.maxLength}
              aria-invalid={error !== undefined}
              aria-describedby={error ? `${id}-error` : undefined}
              value={value[contact.key]}
              onChange={(event) => {
                const next = event.target.value
                setValue((current) => ({ ...current, [contact.key]: next }))
              }}
            />
            {error && (
              <p role="alert" id={`${id}-error`} className="text-sm text-destructive">
                {error}
              </p>
            )}
          </div>
        )
      })}
      {formError && (
        <p role="alert" className="text-sm text-destructive">
          {formError}
        </p>
      )}
      <Button type="submit" disabled={sending}>
        {sending ? 'Invio…' : 'Invia la richiesta'}
      </Button>
    </form>
  )
}

/** «Assumi team» in the talent cloud (D3): the company is known, so the request is
 *  filed at once. */
function CloudHire({ proposalId, hire }: { proposalId: string; hire: (proposalId: string) => Promise<unknown> }) {
  const [sending, setSending] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function file() {
    setSending(true)
    setError(null)
    try {
      await hire(proposalId)
      setDone(true)
    } catch (failure) {
      setError(sentence(failure, 'Non siamo riusciti a inviare la richiesta. Riprova.'))
    } finally {
      setSending(false)
    }
  }

  if (done) {
    return (
      <p role="status" className="border-l-4 border-(--landing-ink) bg-card py-2 pl-4 pr-2 font-medium">
        {THANKS}
      </p>
    )
  }

  return (
    <div className="space-y-2">
      <Button type="button" size="lg" disabled={sending} onClick={() => void file()}>
        {sending ? 'Invio…' : 'Assumi team'}
      </Button>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
