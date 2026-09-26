import { useLocation, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { distinctId } from '@rebase/analytics/browser'
import { AMOUNT_PROBLEM, euroAmount, sentAmount } from '@/lib/amount'
import { useWizardAnalytics } from '@/lib/analytics'
import { ApiError, requestPeople, type CompanyRequest } from '@/lib/api'
import { TEAM_BUILDER_ORIGIN } from '@/lib/format'
import { resolveAttribution } from '@/lib/utm'
import { clearDraft, loadDraft, saveDraft } from '@/wizard/draft'
import { ChoiceField, LongTextField, TextField } from '@/wizard/fields'
import { screensFromFields, Wizard, type Field } from '@/wizard/Wizard'

/** The blank starting shape, exported so a page that never asks the identity fields
 *  (`member/NuovaRichiestaAzienda.tsx`, REB-381) can still satisfy `CompanyRequest`'s
 *  full shape without retyping every key. */
export const EMPTY: CompanyRequest = {
  nome_azienda: '',
  figura_richiesta: '',
  referente_nome: '',
  referente_cognome: '',
  email: '',
  telefono: '',
  progetto: '',
  periodo_da: '',
  durata: '',
  budget_giornaliero: '',
  remoto: '',
  giorni_presenza: '',
  numero_risorse: '',
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export const COMPANY_FIELDS: Field<CompanyRequest>[] = [
  {
    id: 'nome_azienda',
    label: 'Come si chiama la tua azienda?',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <TextField
        aria-label="Azienda"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        placeholder="ACME Srl"
        value={value.nome_azienda}
        onChange={(nome_azienda) => set({ nome_azienda })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) => (value.nome_azienda.trim() ? null : 'Serve il nome dell’azienda.'),
    summary: (value) => value.nome_azienda.trim(),
  },
  {
    id: 'figura_richiesta',
    label: 'Che figura state cercando?',
    hint: 'Il ruolo in poche parole: «Backend developer», «Fractional CTO», «Data engineer».',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <TextField
        aria-label="Figura richiesta"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        placeholder="Backend developer"
        value={value.figura_richiesta}
        onChange={(figura_richiesta) => set({ figura_richiesta })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) => (value.figura_richiesta.trim() ? null : 'Serve una figura.'),
    summary: (value) => value.figura_richiesta.trim(),
  },
  {
    id: 'referente',
    label: 'Chi sei, e dove ti scriviamo?',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <div className="grid gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField
            aria-label="Nome"
            aria-invalid={!!error && !value.referente_nome.trim()}
            aria-describedby={error ? errorId : undefined}
            placeholder="Nome"
            value={value.referente_nome}
            onChange={(referente_nome) => set({ referente_nome })}
            autoFocus={autoFocus}
          />
          <TextField
            aria-label="Cognome"
            aria-invalid={!!error && !value.referente_cognome.trim()}
            aria-describedby={error ? errorId : undefined}
            placeholder="Cognome"
            value={value.referente_cognome}
            onChange={(referente_cognome) => set({ referente_cognome })}
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
          aria-invalid={!!error && value.telefono.trim().length < 6}
          aria-describedby={error ? errorId : undefined}
          type="tel"
          placeholder="+39 345 1234567"
          value={value.telefono}
          onChange={(telefono) => set({ telefono })}
        />
      </div>
    ),
    validate: (value) =>
      value.referente_nome.trim() &&
      value.referente_cognome.trim() &&
      EMAIL.test(value.email.trim()) &&
      value.telefono.trim().length >= 6
        ? null
        : 'Servono nome, cognome, un indirizzo email valido e un numero di telefono.',
    summary: (value) =>
      `${value.referente_nome.trim()} ${value.referente_cognome.trim()} · ${value.email.trim()} · ${value.telefono.trim()}`,
  },
  {
    id: 'progetto',
    label: 'Raccontaci il progetto in due righe',
    hint: 'Cosa serve fare, con che stack o competenze, e cosa deve uscirne. Shift+Invio per andare a capo.',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <LongTextField
        aria-label="Progetto"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        placeholder="Dobbiamo rifare il backend del portale clienti…"
        value={value.progetto}
        onChange={(progetto) => set({ progetto })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) =>
      value.progetto.trim().length >= 20 ? null : 'Due righe bastano, ma servono: almeno venti caratteri.',
    summary: (value) => value.progetto.trim(),
  },
  {
    id: 'periodo_da',
    label: 'Da quando, e per quanto?',
    hint: 'Anche approssimativo: «da ottobre, per tre mesi».',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          aria-label="Da quando"
          aria-invalid={!!error && !/^\d{4}-\d{2}-\d{2}$/.test(value.periodo_da)}
          aria-describedby={error ? errorId : undefined}
          type="date"
          value={value.periodo_da}
          onChange={(periodo_da) => set({ periodo_da })}
          autoFocus={autoFocus}
        />
        <TextField
          aria-label="Per quanto"
          aria-invalid={!!error && !value.durata.trim()}
          aria-describedby={error ? errorId : undefined}
          placeholder="3 mesi"
          value={value.durata}
          onChange={(durata) => set({ durata })}
        />
      </div>
    ),
    validate: (value) =>
      /^\d{4}-\d{2}-\d{2}$/.test(value.periodo_da) && value.durata.trim()
        ? null
        : 'Servono una data di inizio e una durata.',
    summary: (value) => (value.periodo_da ? `dal ${value.periodo_da}, ${value.durata.trim()}` : ''),
  },
  {
    id: 'budget_giornaliero',
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
          value={value.budget_giornaliero}
          onChange={(budget_giornaliero) => set({ budget_giornaliero })}
          autoFocus={autoFocus}
        />
        <span className="text-lg text-muted-foreground">€ / giorno</span>
      </div>
    ),
    validate: (value) => {
      const number = euroAmount(value.budget_giornaliero)
      return Number.isFinite(number) && number >= 1 && number <= 99999 ? null : AMOUNT_PROBLEM
    },
    summary: (value) => (value.budget_giornaliero ? `${value.budget_giornaliero} € / giorno` : ''),
  },
  {
    id: 'remoto',
    label: 'Come si lavorerà?',
    render: ({ value, set, error, errorId }) => (
      <div className="grid gap-4">
        <ChoiceField
          value={value.remoto}
          onChange={(remoto) =>
            set({ remoto, giorni_presenza: remoto === 'ibrido' ? value.giorni_presenza : '' })
          }
          invalid={!!error}
          describedBy={error ? errorId : undefined}
          options={[
            { value: 'remoto', label: 'Da remoto', hint: 'Nessun giorno fisso in sede.' },
            { value: 'ibrido', label: 'Ibrido', hint: 'Qualche giorno in sede, gli altri no.' },
            { value: 'in_sede', label: 'In sede', hint: 'Serve una presenza fissa.' },
          ]}
        />
        {value.remoto === 'ibrido' && (
          <TextField
            aria-label="Giorni in sede a settimana"
            aria-invalid={!!error}
            aria-describedby={error ? errorId : undefined}
            placeholder="1-4"
            value={value.giorni_presenza}
            onChange={(giorni_presenza) => set({ giorni_presenza })}
          />
        )}
      </div>
    ),
    validate: (value) => {
      if (!value.remoto) return 'Scegli una delle tre.'
      if (value.remoto === 'ibrido') {
        const days = Number(value.giorni_presenza)
        return Number.isInteger(days) && days >= 1 && days <= 4
          ? null
          : 'Servono i giorni in sede a settimana, da 1 a 4.'
      }
      return null
    },
    summary: (value) => {
      if (value.remoto === 'ibrido') return `Ibrido · ${value.giorni_presenza} giorni in sede`
      return { remoto: 'Da remoto', in_sede: 'In sede', '': '' }[value.remoto]
    },
  },
  {
    id: 'numero_risorse',
    label: 'Quante persone servono?',
    render: ({ value, set, autoFocus, error, errorId }) => (
      <TextField
        aria-label="Numero di persone"
        aria-invalid={!!error}
        aria-describedby={error ? errorId : undefined}
        inputMode="decimal"
        placeholder="1"
        value={value.numero_risorse}
        onChange={(numero_risorse) => set({ numero_risorse })}
        autoFocus={autoFocus}
      />
    ),
    validate: (value) => {
      const number = Number(value.numero_risorse)
      return Number.isInteger(number) && number >= 1 ? null : 'Serve almeno una persona.'
    },
    summary: (value) =>
      value.numero_risorse
        ? `${value.numero_risorse} ${value.numero_risorse === '1' ? 'persona' : 'persone'}`
        : '',
  },
]

export const COMPANY_SCREENS = screensFromFields(COMPANY_FIELDS)
export const COMPANY_DRAFT_KEY = 'rebase.wizard.azienda'

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
 *  carries tells the admin so. */
function TalentCloudNote() {
  return (
    <aside
      role="note"
      aria-label="Talent cloud"
      className="mx-auto mb-8 w-full max-w-2xl border-l-4 border-(--landing-ink) bg-card px-4 py-3 text-sm"
    >
      Stai chiedendo l’accesso al talent cloud: compila la richiesta e ti ricontattiamo noi.
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
  const analytics = useWizardAnalytics('azienda', searchStr)
  const [draft] = useState(() => loadDraft<CompanyRequest>(COMPANY_DRAFT_KEY))
  const [value, setValue] = useState<CompanyRequest>(() => ({ ...EMPTY, ...draft?.value }))
  const [index, setIndex] = useState(draft?.index ?? 0)
  const [resumed, setResumed] = useState(draft !== null)
  const [attempt, setAttempt] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<{ message: string; field?: string } | null>(null)
  // The origin as the request will carry it (`resolveAttribution`), read once.
  const [forTalentCloud] = useState(() => resolveAttribution(searchStr).origine === TEAM_BUILDER_ORIGIN)

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
          budget_giornaliero: sentAmount(value.budget_giornaliero),
        },
        resolveAttribution(searchStr),
        distinctId(),
      )
      sent.current = true
      clearDraft(COMPANY_DRAFT_KEY)
      analytics.completed()
      void navigate({ to: '/thanks', search: { chi: 'azienda' } })
    } catch (error) {
      const failure = error instanceof ApiError ? error : null
      // `durata` shares a field with `periodo_da`; `email`, `referente_nome`,
      // `referente_cognome` and `telefono` all share the one step that collects them
      // together; `giorni_presenza` shares `remoto`'s own step; anything else names
      // its own field.
      const field = failure?.fields[0]
      const knownField =
        field === 'durata'
          ? 'periodo_da'
          : field === 'email' ||
              field === 'referente_nome' ||
              field === 'referente_cognome' ||
              field === 'telefono'
            ? 'referente'
            : field === 'giorni_presenza'
              ? 'remoto'
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
