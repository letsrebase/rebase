import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import {
  ApiError,
  team,
  type CloudTeamMember,
  type CloudTeamProposal,
  type TeamEconomia,
  type TeamMember,
  type TeamProposal,
  type TeamProposalCreate,
} from '@/lib/api'
import { bandLabel } from '@/lib/bands'
import { resolveReferral } from '@/lib/utm'
import { REMOTO_LABELS, SENIORITY_LABELS, formatDaysPerWeek, formatExperience } from '@/lib/format'

/** The lengths core's `TeamProposalCreate` takes (`team_schemas.py`), measured as it
 *  measures them, stripped: the page says so before the API has to. */
const DESCRIZIONE_MIN = 40
const DESCRIZIONE_MAX = 4000
const NOTA_MAX = 500
/** The number of people a company can ask for (`PERSONE_MAX` in `team_schemas.py`):
 *  one by default, since most requests are for one person (REB-591). */
const PERSONE = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] as const
const PERSONE_DEFAULT = 1

function personeLabel(persone: number): string {
  return persone === 1 ? '1 persona' : `${persone} persone`
}

/** Four projects a visitor can start from, each a description the API takes as it
 *  stands: remote, on site with a city, a team that is not only developers, and a
 *  software house that needs one forward-deployed engineer to place at its own client.
 *  Each carries the number of people its own text implies, so the selector agrees with
 *  the box. */
const EXAMPLES = [
  {
    label: 'Web app per una fintech',
    text: 'Siamo una fintech e ci serve una web app per i nostri clienti: dashboard dei conti, pagamenti e collegamento alle API di open banking. React e TypeScript davanti, Python dietro. Sei mesi, da remoto.',
    persone: 3,
  },
  {
    label: 'Pipeline dati in sede a Milano',
    text: 'Dobbiamo portare gli ordini di tre gestionali in un data warehouse su Google Cloud, con una pipeline affidabile e i report per la direzione. Quattro mesi, in sede a Milano tre giorni a settimana.',
    persone: 2,
  },
  {
    label: 'App mobile con un designer',
    text: 'Vogliamo lanciare un’app per prenotare le lezioni in palestra, su iOS e Android: serve chi la sviluppa, in React Native o Flutter, e un designer che ne curi l’esperienza e l’interfaccia. Tre mesi, da remoto.',
    persone: 2,
  },
  {
    label: 'Un FDE nel team di un cliente',
    text: 'Siamo una software house e cerchiamo un forward deployed engineer da inserire da un nostro cliente: lavora nel loro team, capisce i processi e porta in produzione le integrazioni con i loro sistemi. Python, Postgres e API dei gestionali. Sei mesi, due giorni a settimana in sede dal cliente.',
    persone: 1,
  },
]

/** The wizards' own test of an address (`CompanyWizard.tsx`, `FreelancerWizard.tsx`). */
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const PENDING = 'Sto leggendo i profili…'
const THANKS = 'Grazie: ti scriviamo entro due giorni lavorativi.'

/** `public`: «Assumi team» opens the three contacts, for a visitor with no account.
 *  `cloud` (D3): the signed-in company proposes through its own route, sees each
 *  person by name, and «Assumi team» files the request at once, with no form. */
export type TeamBuilderProps =
  | { mode: 'public' }
  | {
      mode: 'cloud'
      propose: (body: TeamProposalCreate) => Promise<CloudTeamProposal>
      hire: (proposalId: string) => Promise<unknown>
    }

type Run = 'proponi' | 'rigenera'

/** The API's own sentence whenever it gave one (the builder off, too many requests,
 *  Claude not answering, a field, a proposal already requested); ours only when nothing
 *  answered at all. */
function sentence(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/** «Nome Cognome» of a member the cloud's read names; `null` for anyone else. */
function nameOf(member: TeamMember | CloudTeamMember): string | null {
  if (!('nome' in member) || !member.nome) return null
  return `${member.nome} ${member.cognome}`.trim()
}

/**
 * The team builder (P-REB-43, spec § 3.1): a description, from scratch or from an
 * example, becomes an anonymous team with its price bands; a note and «Rigenera» ask
 * again with the proposal it replaces; «Assumi team» turns it into a request. On the
 * public page a person is shown by what the card says of them and nothing else: no
 * name, no id, no place, whatever the read carries. In the cloud (§ 4.2) the card is
 * headed by the person's name, as the profiles under it are.
 */
export function TeamBuilder(props: TeamBuilderProps) {
  const ids = useId()
  const [descrizione, setDescrizione] = useState('')
  const [descrizioneError, setDescrizioneError] = useState<string | null>(null)
  const [persone, setPersone] = useState<number>(PERSONE_DEFAULT)
  // The description and the number the proposal on screen came from: «Rigenera» asks
  // again about those, with the note, whatever the box and the selector say by then.
  const [result, setResult] = useState<{
    proposal: TeamProposal | CloudTeamProposal
    descrizione: string
    persone: number
  } | null>(null)
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
    // The page always says how many: the API sizes from the description only when
    // the field is absent, which is the MCP tool's case, not this one.
    let body: TeamProposalCreate & { persone: number }
    if (from === 'proponi') {
      const text = descrizione.trim()
      if (text.length < DESCRIZIONE_MIN) {
        setDescrizioneError(`Raccontaci qualcosa in più: servono almeno ${DESCRIZIONE_MIN} caratteri.`)
        return
      }
      body = { descrizione: text, persone }
    } else {
      if (!result) return
      const note = nota.trim()
      body = {
        descrizione: result.descrizione,
        persone: result.persone,
        ...(note ? { nota: note } : {}),
        previous_id: result.proposal.id,
      }
    }
    setDescrizioneError(null)
    setRunError(null)
    setRunning(from)
    try {
      const proposal = await (props.mode === 'cloud' ? props.propose(body) : team.propose(body))
      setResult({ proposal, descrizione: body.descrizione, persone: body.persone })
      setNota('')
    } catch (error) {
      setRunError({ from, message: sentence(error, 'Non siamo riusciti a proporre un team. Riprova.') })
    } finally {
      setRunning(null)
    }
  }

  const proposal = result?.proposal
  const members: readonly (TeamMember | CloudTeamMember)[] = proposal?.team ?? []
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
                  setPersone(example.persone)
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
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-2">
            <Label htmlFor={`${ids}-persone`}>Quante persone</Label>
            <Select value={String(persone)} onValueChange={(value) => setPersone(Number(value))}>
              <SelectTrigger id={`${ids}-persone`} className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PERSONE.map((count) => (
                  <SelectItem key={count} value={String(count)}>
                    {personeLabel(count)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button type="submit" disabled={busy}>
            {running === 'proponi' ? PENDING : 'Proponi il team'}
          </Button>
        </div>
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

          {members.length > 0 && (
            <>
              <ul aria-label="Il team" className="grid gap-4 sm:grid-cols-2">
                {members.map((member) => (
                  <li key={member.posizione}>
                    <MemberCard member={member} name={props.mode === 'cloud' ? nameOf(member) : null} />
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

          {members.length > 0 &&
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
 *  anonymous description, seniority and years, the skills, the days a week this team
 *  asks of them, the work mode, the band. With a `name` (the cloud) the name is the
 *  heading and the role comes under it; without one (the public page) the role is the
 *  heading and nobody is named. */
function MemberCard({ member, name }: { member: TeamMember; name: string | null }) {
  const { scheda } = member
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>
          <h3>{name ?? member.ruolo}</h3>
        </CardTitle>
        {name && <p className="font-medium">{member.ruolo}</p>}
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
          <span className="text-muted-foreground">
            {[formatDaysPerWeek(member.giorni_settimana), member.modalita && REMOTO_LABELS[member.modalita]]
              .filter(Boolean)
              .join(' · ')}
          </span>
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
 *  person out is a price nobody quoted. The month is 22 days for everyone, as the spec
 *  fixes it, so it says «a tempo pieno» beside each person's own days a week. */
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
              <span className="text-muted-foreground">(a tempo pieno, {economia.giorni_mese} giorni al mese)</span>
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

/** «Assumi team» on the public page: the three contacts in a dialog over the proposal
 *  (REB-592), then the thanks where the button was. No account and no mail to the
 *  visitor (spec § 1): the admin writes. Closing the dialog keeps what was typed, so a
 *  visitor who goes back to read the team once more does not start over; the `Dialog`
 *  stays mounted across opens for the same reason. */
function PublicHire({ proposalId }: { proposalId: string }) {
  const ids = useId()
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState<Record<Contact, string>>({ azienda: '', email: '', telefono: '' })
  const [errors, setErrors] = useState<Partial<Record<Contact, string>>>({})
  const [formError, setFormError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [done, setDone] = useState(false)
  const inputs = useRef<Partial<Record<Contact, HTMLInputElement | null>>>({})
  const thanks = useRef<HTMLParagraphElement>(null)

  // The dialog and the button it would hand focus back to both go with `done`: the
  // thanks line takes the focus, so a keyboard or a screen reader lands on the answer.
  useEffect(() => {
    if (done) thanks.current?.focus()
  }, [done])

  // The referral link is remembered the moment the page opens (REB-600), so a visitor who
  // navigates away and comes back without the query string still files it.
  useEffect(() => {
    try {
      resolveReferral(window.location.search)
    } catch {
      /* storage refused: the URL still has it at submission */
    }
  }, [])

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
      const rif = resolveReferral(window.location.search)
      await team.request({
        proposal_id: proposalId,
        azienda: value.azienda.trim(),
        email: value.email.trim(),
        telefono: value.telefono.trim(),
        // The referral link the visitor arrived with, remembered by the tab like the
        // wizards' (REB-600): kept on the request and credited only once a company
        // exists for this contact.
        ...(rif ? { rif } : {}),
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
      <p
        role="status"
        ref={thanks}
        tabIndex={-1}
        className="border-l-4 border-(--landing-ink) bg-card py-2 pl-4 pr-2 font-medium outline-none"
      >
        {THANKS}
      </p>
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // A request on its way is not abandoned by a click outside the dialog.
        if (next || !sending) setOpen(next)
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" size="lg">
          Assumi team
        </Button>
      </DialogTrigger>
      {/* No X in the corner: «Annulla» in the footer is the one way out besides Escape,
          next to «Invia la richiesta», where a form's two choices belong. */}
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>Assumi team</DialogTitle>
          <DialogDescription>Tre contatti e ti scriviamo noi. Nessun account da creare.</DialogDescription>
        </DialogHeader>
        <form noValidate className="space-y-4" onSubmit={(event) => void submit(event)}>
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
          <DialogFooter>
            <Button type="button" variant="outline" disabled={sending} onClick={() => setOpen(false)}>
              Annulla
            </Button>
            <Button type="submit" disabled={sending}>
              {sending ? 'Invio…' : 'Invia la richiesta'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
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
