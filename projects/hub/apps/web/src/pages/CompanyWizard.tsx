import { useLocation, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { distinctId } from '@rebase/analytics/browser'
import { AMOUNT_PROBLEM, euroAmount, sentAmount } from '@/lib/amount'
import { useWizardAnalytics } from '@/lib/analytics'
import { ApiError, requestPeople, type CompanyRequest } from '@/lib/api'
import { TEAM_BUILDER_ORIGIN } from '@/lib/format'
import { resolveAttribution, resolveReferral } from '@/lib/utm'
import { clearDraft, loadDraft, saveDraft } from '@/wizard/draft'
import { ChoiceField, LongTextField, TextField } from '@/wizard/fields'
import { screensFromFields, Wizard, type Field } from '@/wizard/Wizard'

/** The blank starting shape, exported so a page that never asks the identity fields
 *  (`member/NewRequestCompany.tsx`, REB-381) can still satisfy `CompanyRequest`'s
 *  full shape without retyping every key. */
export const EMPTY: CompanyRequest = {
  company_name: '',
  requested_role: '',
  contact_first_name: '',
  contact_last_name: '',
  email: '',
  phone: '',
  project: '',
  period_from: '',
  duration: '',
  daily_budget: '',
  remote: '',
  onsite_days: '',
  headcount: '',
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export const COMPANY_FIELDS: Field<CompanyRequest>[] = [
  {
    id: 'company_name',
    label: 'Come si chiama la tua azienda?',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <TextField
        aria-label="Azienda"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        placeholder="ACME Srl"
        value={value.company_name}
        onChange={(company_name) => set({ company_name })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) => (value.company_name.trim() ? null : 'Serve il nome dell’azienda.'),
    summary: (value) => value.company_name.trim(),
  },
  {
    id: 'requested_role',
    label: 'Che figura state cercando?',
    hint: 'Il ruolo in poche parole: «Backend developer», «Fractional CTO», «Data engineer».',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <TextField
        aria-label="Figura richiesta"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        placeholder="Backend developer"
        value={value.requested_role}
        onChange={(requested_role) => set({ requested_role })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) => (value.requested_role.trim() ? null : 'Serve una figura.'),
    summary: (value) => value.requested_role.trim(),
  },
  {
    id: 'contact_person',
    label: 'Chi sei, e dove ti scriviamo?',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <div className="grid gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            aria-label="Nome"
            aria-invalid={!!error && !value.contact_first_name.trim()}
            aria-describedby={error ? errorId : undefined}
            placeholder="Nome"
            value={value.contact_first_name}
            onChange={(contact_first_name) => set({ contact_first_name })}
            autoFocus={autoFocus}
          />
          <TextField
            aria-label="Cognome"
            aria-invalid={!!error && !value.contact_last_name.trim()}
            aria-describedby={error ? errorId : undefined}
            placeholder="Cognome"
            value={value.contact_last_name}
            onChange={(contact_last_name) => set({ contact_last_name })}
          />
        </div>
        <TextField
          aria-label="Email"
          aria-invalid={!!error && !EMAIL.test(value.email.trim())}
          aria-describedby={error ? errorId : undefined}
          type="email"
          inputMode="email"
          placeholder="nome@azienda.it"
          value={value.email}
          onChange={(email) => set({ email })}
        />
        <TextField
          aria-label="Telefono"
          aria-invalid={!!error && value.phone.trim().length < 6}
          aria-describedby={error ? errorId : undefined}
          type="tel"
          placeholder="+39 345 1234567"
          value={value.phone}
          onChange={(phone) => set({ phone })}
        />
      </div>
    ),
    validate: (value) =>
      value.contact_first_name.trim() &&
      value.contact_last_name.trim() &&
      EMAIL.test(value.email.trim()) &&
      value.phone.trim().length >= 6
        ? null
        : 'Servono nome, cognome, un indirizzo email valido e un numero di telefono.',
    summary: (value) =>
      `${value.contact_first_name.trim()} ${value.contact_last_name.trim()} · ${value.email.trim()} · ${value.phone.trim()}`,
  },
  {
    id: 'project',
    label: 'Raccontaci il progetto in due righe',
    hint: 'Cosa serve fare, con che stack o competenze, e cosa deve uscirne. Shift+Invio per andare a capo.',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <LongTextField
        aria-label="Progetto"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        placeholder="Dobbiamo rifare il backend del portale clienti…"
        value={value.project}
        onChange={(project) => set({ project })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) =>
      value.project.trim().length >= 20 ? null : 'Due righe bastano, ma servono: almeno venti caratteri.',
    summary: (value) => value.project.trim(),
  },
  {
    id: 'period_from',
    label: 'Da quando, e per quanto?',
    hint: 'Anche approssimativo: «da ottobre, per tre mesi».',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          aria-label="Da quando"
          aria-invalid={!!error && !/^\d{4}-\d{2}-\d{2}$/.test(value.period_from)}
          aria-describedby={error ? errorId : undefined}
          type="date"
          value={value.period_from}
          onChange={(period_from) => set({ period_from })}
          autoFocus={autoFocus}
        />
        <TextField
          aria-label="Per quanto"
          aria-invalid={!!error && !value.duration.trim()}
          aria-describedby={error ? errorId : undefined}
          placeholder="3 mesi"
          value={value.duration}
          onChange={(duration) => set({ duration })}
        />
      </div>
    ),
    validate: (value) =>
      /^\d{4}-\d{2}-\d{2}$/.test(value.period_from) && value.duration.trim()
        ? null
        : 'Servono una data di inizio e una durata.',
    summary: (value) => (value.period_from ? `dal ${value.period_from}, ${value.duration.trim()}` : ''),
  },
  {
    id: 'daily_budget',
    label: 'Che budget hai per una giornata?',
    hint: 'In euro, IVA esclusa. Serve a proporti le persone giuste, non a trattare.',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <div className="flex items-center gap-3">
        <TextField
          aria-label="Budget a giornata"
          aria-invalid={!!error}
          aria-describedby={error ? errorId : undefined}
          inputMode="decimal"
          placeholder="500"
          value={value.daily_budget}
          onChange={(daily_budget) => set({ daily_budget })}
          autoFocus={autoFocus}
        />
        <span className="text-lg text-muted-foreground">€ / giorno</span>
      </div>
    ),
    validate: (value) => {
      const number = euroAmount(value.daily_budget)
      return Number.isFinite(number) && number >= 1 && number <= 99999 ? null : AMOUNT_PROBLEM
    },
    summary: (value) => (value.daily_budget ? `${value.daily_budget} € / giorno` : ''),
  },
  {
    id: 'remote',
    label: 'Come si lavorerà?',
    render: ({ value, set, error, errorId }) => (
      <div className="grid gap-4">
        <ChoiceField
          value={value.remote}
          onChange={(remote) =>
            set({ remote, onsite_days: remote === 'hybrid' ? value.onsite_days : '' })
          }
          invalid={!!error}
          describedBy={error ? errorId : undefined}
          options={[
            { value: 'remote', label: 'Da remoto', hint: 'Nessun giorno fisso in sede.' },
            { value: 'hybrid', label: 'Ibrido', hint: 'Qualche giorno in sede, gli altri no.' },
            { value: 'on_site', label: 'In sede', hint: 'Serve una presenza fissa.' },
          ]}
        />
        {value.remote === 'hybrid' && (
          <TextField
            aria-label="Giorni in sede a settimana"
            aria-invalid={!!error}
            aria-describedby={error ? errorId : undefined}
            placeholder="1-4"
            value={value.onsite_days}
            onChange={(onsite_days) => set({ onsite_days })}
          />
        )}
      </div>
    ),
    validate: (value) => {
      if (!value.remote) return 'Scegli una delle tre.'
      if (value.remote === 'hybrid') {
        const days = Number(value.onsite_days)
        return Number.isInteger(days) && days >= 1 && days <= 4
          ? null
          : 'Servono i giorni in sede a settimana, da 1 a 4.'
      }
      return null
    },
    summary: (value) => {
      if (value.remote === 'hybrid') return `Ibrido · ${value.onsite_days} giorni in sede`
      return { remote: 'Da remoto', on_site: 'In sede', '': '' }[value.remote]
    },
  },
  {
    id: 'headcount',
    label: 'Quante persone servono?',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <TextField
        aria-label="Numero di persone"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        inputMode="decimal"
        placeholder="1"
        value={value.headcount}
        onChange={(headcount) => set({ headcount })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) => {
      const number = Number(value.headcount)
      return Number.isInteger(number) && number >= 1 ? null : 'Serve almeno una persona.'
    },
    summary: (value) =>
      value.headcount
        ? `${value.headcount} ${value.headcount === '1' ? 'person' : 'people'}`
        : '',
  },
]

export const COMPANY_SCREENS = screensFromFields(COMPANY_FIELDS)
export const COMPANY_DRAFT_KEY = 'rebase.wizard.company'

/** Above the first question, as on the freelance side (REB-215): what this is and how
 *  long it takes, before a company is asked its name. */
function Intro() {
  return (
    <aside
      aria-label="Cos’è rebase"
      className="border-l-4 border-(--landing-ink) bg-card py-2 pl-4 pr-2"
    >
      <p className="font-medium">rebase è la community di chi fa software in proprio in Italia.</p>
      <p className="mt-1 text-sm text-muted-foreground">
        {COMPANY_FIELDS.length} domande, un paio di minuti: chi siete, cosa cercate e con che
        budget, così vi proponiamo le persone giuste.
      </p>
    </aside>
  )
}

/** Above the form when the team builder's beta box opened the wizard (REB-518, spec
 *  § 4.3): this request is how a company asks for the talent cloud, and the origin it
 *  carries tells the admin so. On royal gold like the freelancer wizard's guide banner
 *  (REB-570): a banner the company is meant to notice, not one more quiet note. */
function TalentCloudNote() {
  return (
    <aside
      role="note"
      aria-label="Talent cloud"
      className="mx-auto mb-8 w-full max-w-2xl border-(length:--landing-border-width) bg-(--color-royal-gold) px-4 py-3 text-(--landing-ink) shadow-sm"
    >
      <p className="font-medium">Stai chiedendo l’accesso al talent cloud.</p>
      <p className="mt-1 text-sm">Compila la richiesta e ti ricontattiamo noi per aprirtelo.</p>
    </aside>
  )
}

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

export function CompanyWizard() {
  const navigate = useNavigate()
  // The URL's own query string, from the router rather than `window`: the attribution
  // is whatever this page was opened with.
  const searchStr = useLocation({ select: (location) => location.searchStr })
  const analytics = useWizardAnalytics('company', searchStr)
  const [draft] = useState(() => loadDraft<CompanyRequest>(COMPANY_DRAFT_KEY))
  const [value, setValue] = useState<CompanyRequest>(() => ({ ...EMPTY, ...draft?.value }))
  const [index, setIndex] = useState(draft?.index ?? 0)
  const [resumed, setResumed] = useState(draft !== null)
  const [attempt, setAttempt] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<{ message: string; field?: string } | null>(null)
  // The origin as the request will carry it (`resolveAttribution`), read once.
  const [forTalentCloud] = useState(() => resolveAttribution(searchStr).origin === TEAM_BUILDER_ORIGIN)

  // Once the application is in, nothing is saved again, whatever React still has to
  // flush: the next visit must open a blank form, not the one just sent.
  const sent = useRef(false)
  useEffect(() => {
    if (!sent.current) saveDraft(COMPANY_DRAFT_KEY, value, index)
  }, [value, index])

  function restart() {
    clearDraft(COMPANY_DRAFT_KEY)
    setValue(EMPTY)
    setIndex(0)
    setResumed(false)
    setAttempt((current) => current + 1)
  }

  async function submit() {
    setSubmitting(true)
    setSubmitError(null)
    try {
      await requestPeople(
        {
          ...value,
          daily_budget: sentAmount(value.daily_budget),
        },
        resolveAttribution(searchStr),
        distinctId(),
        resolveReferral(searchStr),
      )
      sent.current = true
      clearDraft(COMPANY_DRAFT_KEY)
      analytics.completed()
      void navigate({ to: '/thanks', search: { who: 'company' } })
    } catch (error) {
      const failure = error instanceof ApiError ? error : null
      // `duration` shares a field with `period_from`; `email`, `contact_first_name`,
      // `contact_last_name` and `phone` all share the one step that collects them
      // together; `onsite_days` shares `remote`'s own step; anything else names
      // its own field.
      const field = failure?.fields[0]
      const knownField =
        field === 'duration'
          ? 'period_from'
          : field === 'email' ||
              field === 'contact_first_name' ||
              field === 'contact_last_name' ||
              field === 'phone'
            ? 'contact_person'
            : field === 'onsite_days'
              ? 'remote'
              : field
      setSubmitError({
        message: failure?.message ?? 'Non siamo riusciti a inviare la richiesta. Riprova.',
        field: knownField,
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      {forTalentCloud && <TalentCloudNote />}
      {resumed && <ResumedNote onRestart={restart} />}
      <Wizard
        key={attempt}
        title="Cerchi persone"
        screens={COMPANY_SCREENS}
        value={value}
        set={(patch) => setValue((current) => ({ ...current, ...patch }))}
        onSubmit={() => void submit()}
        submitting={submitting}
        submitError={submitError}
        submitLabel="Invia la richiesta"
        onStep={analytics.onStep}
        initialIndex={resumed ? (draft?.index ?? 0) : 0}
        onIndexChange={setIndex}
        intro={<Intro />}
      />
    </>
  )
}
