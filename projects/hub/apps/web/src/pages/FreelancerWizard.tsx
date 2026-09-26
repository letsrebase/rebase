import { useLocation, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { distinctId } from '@rebase/analytics/browser'
import { AMOUNT_PROBLEM, euroAmount, machineAmount } from '@/lib/amount'
import { readPerkParam, useWizardAnalytics } from '@/lib/analytics'
import { ApiError, applyAsFreelancer, type FreelancerApplication } from '@/lib/api'
import { isLinkedinName, LINKEDIN_OWN_PROFILE, linkedinFieldValue, linkedinProfile } from '@/lib/linkedin'
import { resolveAttribution } from '@/lib/utm'
import { clearDraft, loadDraft, saveDraft } from '@/wizard/draft'
import { ChoiceField, FileField, LinksField, TextField } from '@/wizard/fields'
import { screensFromFields, Wizard, type Field } from '@/wizard/Wizard'

const EMPTY: FreelancerApplication = {
  nome: '',
  cognome: '',
  email: '',
  linkedin_url: '',
  tariffa_giornaliera: '',
  posizione: '',
  remoto: '',
  links: [],
  cv: null,
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const MAX_CV = 5 * 1024 * 1024

export const FREELANCER_FIELDS: Field<FreelancerApplication>[] = [
  {
    id: 'nome',
    label: 'Come ti chiami?',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          aria-label="Nome"
          aria-invalid={!!error && !value.nome.trim()}
          aria-describedby={error ? errorId : undefined}
          placeholder="Nome"
          value={value.nome}
          onChange={(nome) => set({ nome })}
          autoFocus={autoFocus}
        />
        <TextField
          aria-label="Cognome"
          aria-invalid={!!error && !value.cognome.trim()}
          aria-describedby={error ? errorId : undefined}
          placeholder="Cognome"
          value={value.cognome}
          onChange={(cognome) => set({ cognome })}
        />
      </div>
    ),
    validate: (value) =>
      value.nome.trim() && value.cognome.trim() ? null : 'Servono nome e cognome.',
    summary: (value) => `${value.nome.trim()} ${value.cognome.trim()}`.trim(),
  },
  {
    id: 'email',
    label: 'A che indirizzo ti scriviamo?',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <TextField
        aria-label="Email"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        type="email"
        inputMode="email"
        placeholder="nome@studio.it"
        value={value.email}
        onChange={(email) => set({ email })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) => (EMAIL.test(value.email.trim()) ? null : 'Serve un indirizzo email valido.'),
    summary: (value) => value.email.trim(),
  },
  {
    id: 'linkedin_url',
    label: 'Il tuo profilo LinkedIn',
    hint: 'Se ce l’hai. Basta il nome che segue linkedin.com/in/, oppure incolla l’indirizzo com’è.',
    optional: true,
    render: ({ value, set, autoFocus, error, errorId }) => {
      const prefixed = isLinkedinName(value.linkedin_url)
      const describedBy = [error ? errorId : null, prefixed ? 'linkedin-prefix' : null]
        .filter(Boolean)
        .join(' ')
      return (
        <div className="grid gap-2">
          <div className="flex items-center gap-2">
            {/* The fixed half of the address, shown while the field holds a name: an
                address pasted whole is reduced to its name as it lands (ORB-203). */}
            {prefixed && (
              <span id="linkedin-prefix" className="text-lg text-muted-foreground">
                linkedin.com/in/
              </span>
            )}
            <TextField
              aria-label="Profilo LinkedIn"
              aria-invalid={!!error}
              aria-describedby={describedBy || undefined}
              inputMode="url"
              placeholder="mario-rossi"
              value={value.linkedin_url}
              onChange={(linkedin_url) =>
                set({ linkedin_url: linkedinFieldValue(linkedin_url, value.linkedin_url) })
              }
              autoFocus={autoFocus}
            />
          </div>
          <a
            className="w-fit text-sm underline underline-offset-2"
            href={LINKEDIN_OWN_PROFILE}
            target="_blank"
            rel="noreferrer"
          >
            Apri il tuo profilo LinkedIn
          </a>
        </div>
      )
    },
    validate: (value) =>
      linkedinProfile(value.linkedin_url) === null
        ? 'Serve il tuo profilo su linkedin.com, oppure niente.'
        : null,
    summary: (value) => linkedinProfile(value.linkedin_url) ?? value.linkedin_url.trim(),
  },
  {
    id: 'cv',
    label: 'Il tuo CV',
    // Optional since the wizard started turning away people who did not have a PDF to
    // hand: the card is stored without one, the area they land in asks for it again,
    // and `completa` on the admin side already says which cards are missing it.
    optional: true,
    hint: 'Un PDF, al massimo 5 MB. Se non ce l’hai qui, salta: puoi caricarlo quando vuoi dalla tua area. Lo leggiamo noi e chi ti proporrà un progetto; puoi chiederci di cancellarlo quando vuoi.',
    render: ({ value, set, error, errorId }) => (
      <FileField
        value={value.cv}
        onChange={(cv) => set({ cv })}
        accept="application/pdf,.pdf"
        hint="PDF fino a 5 MB"
        invalid={!!error}
        describedBy={error ? errorId : undefined}
      />
    ),
    // What is attached is still checked here, with the same two rules the server
    // applies to the bytes; what is not attached is simply not a refusal.
    validate: (value) => {
      if (!value.cv) return null
      // An attached file with no bytes is a broken pick, not a choice to skip: the
      // server refuses it too, and being told here saves the round trip.
      if (value.cv.size === 0) return 'Questo file è vuoto: riprova con il PDF.'
      if (value.cv.size > MAX_CV) return 'Il CV può pesare al massimo 5 MB.'
      if (value.cv.type && value.cv.type !== 'application/pdf') return 'Il CV deve essere un PDF.'
      return null
    },
    summary: (value) => value.cv?.name ?? '',
  },
  {
    id: 'tariffa_giornaliera',
    label: 'Quanto costa una tua giornata?',
    hint: 'In euro, IVA esclusa. Una cifra indicativa: serve a proporti i progetti giusti.',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <div className="flex items-center gap-3">
        <TextField
          aria-label="Tariffa a giornata"
          aria-invalid={!!error}
          aria-describedby={error ? errorId : undefined}
          inputMode="decimal"
          placeholder="450"
          value={value.tariffa_giornaliera}
          onChange={(tariffa_giornaliera) => set({ tariffa_giornaliera })}
          autoFocus={autoFocus}
        />
        <span className="text-lg text-muted-foreground">€ / giorno</span>
      </div>
    ),
    validate: (value) => {
      const number = euroAmount(value.tariffa_giornaliera)
      return Number.isFinite(number) && number >= 1 && number <= 99999
        ? null
        : AMOUNT_PROBLEM
    },
    summary: (value) => (value.tariffa_giornaliera ? `${value.tariffa_giornaliera} € / giorno` : ''),
  },
  {
    id: 'posizione',
    label: 'Cosa fai?',
    hint: 'Il ruolo con cui ti presenti: «Backend developer», «AI engineer», «Fractional CTO».',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <TextField
        aria-label="Posizione"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        placeholder="Backend developer"
        value={value.posizione}
        onChange={(posizione) => set({ posizione })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) => (value.posizione.trim() ? null : 'Serve una posizione.'),
    summary: (value) => value.posizione.trim(),
  },
  {
    id: 'remoto',
    label: 'Come preferisci lavorare?',
    render: ({ value, set, error, errorId }) => (
      <ChoiceField
        value={value.remoto}
        onChange={(remoto) => set({ remoto })}
        invalid={!!error}
        describedBy={error ? errorId : undefined}
        options={[
          { value: 'remoto', label: 'Da remoto', hint: 'Ovunque, con le call che servono.' },
          { value: 'ibrido', label: 'Ibrido', hint: 'Qualche giorno in sede va bene.' },
          { value: 'in_sede', label: 'In sede', hint: 'Preferisco essere dal cliente.' },
        ]}
      />
    ),
    validate: (value) => (value.remoto ? null : 'Scegli una delle tre.'),
    summary: (value) =>
      ({ remoto: 'Da remoto', ibrido: 'Ibrido', in_sede: 'In sede', '': '' })[value.remoto],
  },
  {
    id: 'links',
    label: 'Altri link che vuoi farci vedere',
    hint: 'GitHub, portfolio, sito: uno per riga, con https.',
    optional: true,
    render: ({ value, set, autoFocus, error, errorId }) => (
      <LinksField
        value={value.links}
        onChange={(links) => set({ links })}
        autoFocus={autoFocus}
        invalid={!!error}
        describedBy={error ? errorId : undefined}
      />
    ),
    validate: (value) => {
      const links = value.links.map((link) => link.trim()).filter(Boolean)
      if (links.length > 10) return 'Al massimo dieci link.'
      return links.every((link) => /^https:\/\/[^\s]+\.[^\s]+/.test(link))
        ? null
        : 'Ogni link deve essere un indirizzo https completo.'
    },
    summary: (value) => value.links.map((link) => link.trim()).filter(Boolean).join(', '),
  },
]

export const FREELANCER_SCREENS = screensFromFields(FREELANCER_FIELDS)

/** The perk the URL says the person came for, if any: `?perk=guida` is what the
 *  landing's «Entra e scaricala» button carries (ORB-154), so the wizard can say why it
 *  is worth finishing. Anything else is nobody's business and reads as no perk. */
export function readPerk(search: string): 'guida' | null {
  return readPerkParam(search) === 'guida' ? 'guida' : null
}

/** One line above the wizard for whoever came for the guide: what finishing buys them,
 *  and where the file will be. The site's own shapes -- an ink line, a stepped shadow,
 *  the gold the deck uses for its second accent -- inside the panel, over the progress
 *  bar, so it reads as part of this page and not as a notice dropped on it. */
function GuideBanner() {
  return (
    <aside
      role="note"
      aria-label="Perché completare l’iscrizione"
      className="mx-auto mb-8 w-full max-w-2xl border-(length:--landing-border-width) bg-(--color-royal-gold) px-4 py-3 text-(--landing-ink) shadow-sm"
    >
      <p className="font-medium">
        Completa l’iscrizione per scaricare la guida per diventare un freelance tech.
      </p>
      <p className="mt-1 text-sm">
        È un PDF: lo trovi nella tua area appena sei dentro. Gratis, come PigroCRM.
      </p>
    </aside>
  )
}

export const FREELANCER_DRAFT_KEY = 'rebase.wizard.freelance'

/** What this is and how long it takes, above the first question (REB-215). The person
 *  who arrived from an ad was asked their name before being told anything, and three
 *  of nine left on that screen without typing a letter. */
function Intro() {
  return (
    <aside
      aria-label="Cos’è rebase"
      className="border-l-4 border-(--landing-ink) bg-card py-2 pl-4 pr-2"
    >
      <p className="font-medium">rebase è la community di chi fa software in proprio in Italia.</p>
      <p className="mt-1 text-sm text-muted-foreground">
        {FREELANCER_FIELDS.length} domande, circa tre minuti: chi sei, cosa fai e quanto costi,
        così le aziende che cercano persone ti trovano. Dentro c’è anche PigroCRM, gratis.
      </p>
    </aside>
  )
}

/** The draft was picked up where it was left: say so, and keep the blank form one
 *  click away for whoever is not the same person, or wants to start over. */
function ResumedNote({ onRestart }: { onRestart: () => void }) {
  return (
    <aside
      role="note"
      aria-label="Risposte ritrovate"
      className="mx-auto mb-8 flex w-full max-w-2xl flex-wrap items-center justify-between gap-2 border-(length:--landing-border-width) bg-card px-4 py-3 text-sm"
    >
      <span>Abbiamo ritrovato le risposte di prima: riprendi da dove eri.</span>
      <button type="button" className="font-medium underline underline-offset-2" onClick={onRestart}>
        Ricomincia
      </button>
    </aside>
  )
}

export function FreelancerWizard() {
  const navigate = useNavigate()
  // The URL's own query string, from the router rather than `window`: the attribution
  // is whatever this page was opened with.
  const searchStr = useLocation({ select: (location) => location.searchStr })
  const perk = readPerk(searchStr)
  const analytics = useWizardAnalytics('freelance', searchStr)
  // Read once, when the page opens: what the person left last time, if anything, minus
  // the CV (`wizard/draft.ts` says why it cannot follow).
  const [draft] = useState(() => loadDraft<FreelancerApplication>(FREELANCER_DRAFT_KEY))
  const [value, setValue] = useState<FreelancerApplication>(() => ({ ...EMPTY, ...draft?.value, cv: null }))
  const [index, setIndex] = useState(draft?.index ?? 0)
  const [resumed, setResumed] = useState(draft !== null)
  // Bumped by «Ricomincia»: a new key remounts the engine on the first question.
  const [attempt, setAttempt] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<{ message: string; field?: string } | null>(null)

  // Once the application is in, nothing is saved again, whatever React still has to
  // flush: the next visit must open a blank form, not the one just sent.
  const sent = useRef(false)
  useEffect(() => {
    if (!sent.current) saveDraft(FREELANCER_DRAFT_KEY, value, index)
  }, [value, index])

  function restart() {
    clearDraft(FREELANCER_DRAFT_KEY)
    setValue(EMPTY)
    setIndex(0)
    setResumed(false)
    setAttempt((current) => current + 1)
  }

  async function submit() {
    setSubmitting(true)
    setSubmitError(null)
    try {
      await applyAsFreelancer(
        { ...value, tariffa_giornaliera: machineAmount(value.tariffa_giornaliera) },
        resolveAttribution(searchStr),
        distinctId(),
      )
      sent.current = true
      clearDraft(FREELANCER_DRAFT_KEY)
      analytics.completed()
      void navigate({ to: '/thanks', search: { chi: 'freelance' } })
    } catch (error) {
      const failure = error instanceof ApiError ? error : null
      setSubmitError({
        message: failure?.message ?? 'Non siamo riusciti a inviare la candidatura. Riprova.',
        field: failure?.fields[0],
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      {perk === 'guida' && <GuideBanner />}
      {resumed && <ResumedNote onRestart={restart} />}
      <Wizard
        key={attempt}
        title="Entra in rebase"
        screens={FREELANCER_SCREENS}
        value={value}
        set={(patch) => setValue((current) => ({ ...current, ...patch }))}
        onSubmit={() => void submit()}
        submitting={submitting}
        submitError={submitError}
        submitLabel="Invia la candidatura"
        onStep={analytics.onStep}
        initialIndex={resumed ? (draft?.index ?? 0) : 0}
        onIndexChange={setIndex}
        intro={<Intro />}
      />
    </>
  )
}
