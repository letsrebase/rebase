import { useInfiniteQuery, useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { cn } from '@rebase/ui/cn'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import { admin, ApiError, type Company, type FiscalData, type Match, type MatchCreate, type MatchPrefill } from '@/lib/api'
import {
  CLIENTE_EMPTY,
  FISCAL_EMPTY,
  LETTERA_EMPTY,
  LETTERA_GROUPS,
  LETTERA_LABELS,
  LETTERA_MULTILINE,
  LETTERA_REQUIRED,
  clienteForm,
  draftFromFiscal,
  letteraForm,
  sendReportMessage,
  toCliente,
  toFiscalData,
  toLettera,
  type ClienteForm,
  type FiscalDraft,
  type LetteraFieldKey,
  type LetteraForm,
} from '@/lib/contracts'
import { formatDate } from '@/lib/format'
import { FiscalFields } from './Contratti'
import { Header } from './lists'

const STEPS = ['Azienda', 'Freelance', 'Cliente', 'Lettera di incarico', 'Anteprima'] as const
const SEARCH_DEBOUNCE_MS = 300
const COMPANIES_PAGE = 50

interface Previews {
  lettera: string
  quadro: string | null
}

interface Failure {
  message: string
  fields: string[]
  // A 409: the server refused the write for what the row already holds, not for what
  // the admin typed, so the failure points at the row instead of a field (REB-406).
  conflict: boolean
}

function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

function revoke(previews: Previews) {
  URL.revokeObjectURL(previews.lettera)
  if (previews.quadro) URL.revokeObjectURL(previews.quadro)
}

function failureOf(error: unknown, fallback: string): Failure | null {
  if (!error) return null
  if (error instanceof ApiError) return { message: error.message, fields: error.fields, conflict: error.status === 409 }
  return { message: fallback, fields: [], conflict: false }
}

function StepFooter({
  onBack,
  next,
  pending,
  ready = true,
  failure,
}: {
  onBack?: () => void
  next: string
  pending: boolean
  ready?: boolean
  failure: Failure | null
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {onBack && (
          <Button type="button" variant="outline" onClick={onBack}>
            Indietro
          </Button>
        )}
        <Button type="submit" disabled={pending || !ready}>
          {pending ? 'Un momento…' : next}
        </Button>
      </div>
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure.message}
        </p>
      )}
    </div>
  )
}

function CompanyStep({
  selected,
  onSelect,
  onNext,
  pending,
  failure,
}: {
  selected: Company | null
  onSelect: (company: Company) => void
  onNext: () => void
  pending: boolean
  failure: Failure | null
}) {
  const [q, setQ] = useState('')
  const term = useDebounce(q.trim(), SEARCH_DEBOUNCE_MS)
  // A page at a time, through the same cursor «Aziende» walks: an older request past the
  // first page stays one «Mostra altre» away (Greptile 4092036042).
  const companies = useInfiniteQuery({
    queryKey: ['companies', 'match', term],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      admin.companies({ q: term || undefined, limit: COMPANIES_PAGE, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  })
  const items = companies.data?.pages.flatMap((page) => page.items) ?? []
  let list: ReactNode
  if (companies.isError) list = <p className="text-sm text-destructive">Non riesco a leggere le richieste.</p>
  else if (companies.isPending) list = <p className="text-sm text-muted-foreground">Caricamento…</p>
  else if (items.length === 0) list = <p className="text-sm text-muted-foreground">Nessuna richiesta trovata.</p>
  else
    list = (
      <ul className="space-y-2">
        {items.map((item) => {
          const closed = item.stato === 'chiuso'
          const chosen = selected?.id === item.id
          return (
            <li key={item.id}>
              <Button
                type="button"
                variant={chosen ? 'default' : 'outline'}
                aria-pressed={chosen}
                disabled={closed}
                onClick={() => onSelect(item)}
                className={cn('h-auto w-full justify-start whitespace-normal py-2 text-left', closed && 'opacity-50')}
              >
                <span className="flex flex-col items-start gap-0.5">
                  <span className="font-medium">
                    {item.nome_azienda}
                    {closed && ' · chiusa'}
                  </span>
                  <span className="text-xs">
                    {item.referente} · {item.figura_richiesta} · dal {formatDate(item.periodo_da)}
                  </span>
                </span>
              </Button>
            </li>
          )
        })}
      </ul>
    )
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onNext()
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="match-azienda-q">Cerca una richiesta</Label>
        <Input
          id="match-azienda-q"
          type="search"
          maxLength={200}
          placeholder="Azienda, referente, email…"
          value={q}
          onChange={(event) => setQ(event.target.value)}
        />
      </div>
      {list}
      {companies.hasNextPage && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void companies.fetchNextPage()}
          disabled={companies.isFetchingNextPage}
        >
          {companies.isFetchingNextPage ? 'Caricamento…' : 'Mostra altre'}
        </Button>
      )}
      <StepFooter next="Avanti" pending={pending} ready={selected !== null} failure={failure} />
    </form>
  )
}

function ClienteStep({
  form,
  onChange,
  onBack,
  onNext,
}: {
  form: ClienteForm
  onChange: (form: ClienteForm) => void
  onBack: () => void
  onNext: () => void
}) {
  const field = (name: keyof ClienteForm, label: string, maxLength: number) => (
    <div className="space-y-1.5">
      <Label htmlFor={`match-${name}`}>{label}</Label>
      <Input
        id={`match-${name}`}
        required
        maxLength={maxLength}
        value={form[name]}
        onChange={(event) => onChange({ ...form, [name]: event.target.value })}
      />
    </div>
  )
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onNext()
      }}
    >
      <p className="text-sm text-muted-foreground">Il cliente come lo stampa la lettera di incarico.</p>
      {field('cliente_ragione_sociale', 'Ragione sociale del cliente', 200)}
      {field('cliente_piva', 'Partita IVA del cliente', 32)}
      {field('cliente_sede', 'Sede del cliente', 300)}
      <StepFooter onBack={onBack} next="Avanti" pending={false} failure={null} />
    </form>
  )
}

function LetteraInput({
  field,
  form,
  onChange,
  invalid,
}: {
  field: LetteraFieldKey
  form: LetteraForm
  onChange: (form: LetteraForm) => void
  invalid: (field: string) => true | undefined
}) {
  const inputId = `lettera-${field}`
  if (field === 'fine_mese') {
    return (
      <div className="flex items-center gap-2">
        <Checkbox
          id={inputId}
          checked={form.fine_mese}
          onCheckedChange={(checked) => onChange({ ...form, fine_mese: checked === true })}
        />
        <Label htmlFor={inputId} className="font-normal">
          {LETTERA_LABELS.fine_mese}
        </Label>
      </div>
    )
  }
  const value = form[field]
  const set = (next: string) => onChange({ ...form, [field]: next })
  const type = field === 'data_inizio' || field === 'data_fine' ? 'date' : field === 'giorni_pagamento' || field === 'giorni_preavviso' ? 'number' : 'text'
  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{LETTERA_LABELS[field]}</Label>
      {LETTERA_MULTILINE.has(field) ? (
        <Textarea
          id={inputId}
          rows={3}
          required={LETTERA_REQUIRED.has(field)}
          value={value}
          onChange={(event) => set(event.target.value)}
          aria-invalid={invalid(field)}
        />
      ) : (
        <Input
          id={inputId}
          type={type}
          inputMode={field === 'compenso' ? 'decimal' : undefined}
          required={LETTERA_REQUIRED.has(field)}
          value={value}
          onChange={(event) => set(event.target.value)}
          aria-invalid={invalid(field)}
        />
      )}
    </div>
  )
}

function PreviewStep({
  previews,
  prefill,
  onBack,
  onSave,
  onSend,
  saving,
  sending,
  locked,
  failure,
  sentMessage,
  freelancerId,
}: {
  previews: Previews
  prefill: MatchPrefill
  onBack: () => void
  onSave: () => void
  onSend: () => void
  saving: boolean
  sending: boolean
  locked: boolean
  failure: Failure | null
  sentMessage: string | null
  freelancerId: string
}) {
  const order = prefill.quadro_necessario
    ? 'Con la firma elettronica partirà per primo il contratto quadro; la lettera di incarico aspetterà la sua firma e partirà da sola subito dopo.'
    : prefill.lettera_in_attesa
      ? 'La lettera di incarico aspetterà la firma del contratto quadro già inviato, e partirà da sola subito dopo.'
      : 'Il contratto quadro è già attivo: con la firma elettronica partirà solo la lettera di incarico.'
  return (
    <div className="space-y-4">
      <ul className="space-y-2 text-sm">
        <li>
          <a className="underline underline-offset-2" href={previews.lettera} target="_blank" rel="noreferrer">
            Apri la lettera di incarico
          </a>
        </li>
        {previews.quadro && (
          <li>
            <a className="underline underline-offset-2" href={previews.quadro} target="_blank" rel="noreferrer">
              Apri il contratto quadro
            </a>
          </li>
        )}
      </ul>
      <p className="text-sm">{order}</p>
      <p className="text-sm text-muted-foreground">
        «Salva come bozza» non manda nulla a nessuno; «Invia per la firma» manda al freelance una mail per il documento
        che parte.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={onBack} disabled={saving || sending || locked}>
          Indietro
        </Button>
        <Button type="button" variant="outline" onClick={onSave} disabled={saving || sending}>
          {saving ? 'Salvo…' : 'Salva come bozza'}
        </Button>
        {/* A report already back (even a refusal's, `sentMessage`) means this send
         *  already happened once: a second click must not send it again (REB-406). */}
        <Button type="button" onClick={onSend} disabled={saving || sending || sentMessage !== null}>
          {sending ? 'Invio…' : 'Invia per la firma'}
        </Button>
      </div>
      {failure && (
        <div role="alert" className="space-y-1 text-sm text-destructive">
          <p>{failure.message}</p>
          {/* A 409 names a row already saved differently, never a field on this page
           *  (REB-406): the way onward is the row itself, in «Match e contratti». */}
          {failure.conflict && (
            <Link
              to="/admin/freelance/$id/contracts"
              params={{ id: freelancerId }}
              className="underline underline-offset-2"
            >
              Vai a Match e contratti
            </Link>
          )}
        </div>
      )}
      {sentMessage && (
        <div role="status" className="space-y-1 text-sm">
          <p>{sentMessage}</p>
          <Link
            to="/admin/freelance/$id/contracts"
            params={{ id: freelancerId }}
            className="underline underline-offset-2"
          >
            Vai a Match e contratti
          </Link>
        </div>
      )}
    </div>
  )
}

/** «Crea match» (REB-387): five steps from a card to a draft match with its documents.
 *  The tax data are saved when the admin leaves step 2; step 5 shows previews that
 *  nothing stores; «Salva come bozza» writes the match and takes the letter's number.
 *  «Invia per la firma» writes the match once and sends it (REB-390). The company's
 *  `budget_giornaliero` is never shown or sent from this page (spec § 1h). */
export function AdminCreaMatch() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id/match/new' })
  const navigate = useNavigate()
  const person = useQuery({ queryKey: ['freelancer', id], queryFn: () => admin.freelancer(id) })
  // One id per wizard run (REB-406): mounting this page is starting one over, so a
  // fresh id here is all «reset when the wizard starts over» asks for -- this page
  // always navigates away once a match is written, never resets mid-mount. Sent with
  // both «Salva come bozza» and «Invia per la firma», so a retry after the response is
  // lost (a network drop, not the 503 `sendNow` already recovers from with `created`)
  // writes nothing new: the server returns the match already written under it.
  const [matchId] = useState(() => crypto.randomUUID())
  const [step, setStep] = useState(0)
  const [company, setCompany] = useState<Company | null>(null)
  const [prefillFor, setPrefillFor] = useState<string | null>(null)
  const [prefill, setPrefill] = useState<MatchPrefill | null>(null)
  const [fiscal, setFiscal] = useState<FiscalDraft>(FISCAL_EMPTY)
  const [cliente, setCliente] = useState<ClienteForm>(CLIENTE_EMPTY)
  const [lettera, setLettera] = useState<LetteraForm>(LETTERA_EMPTY)
  const [previews, setPreviews] = useState<Previews | null>(null)

  // A preview is a blob in this tab's memory: let it go once replaced, or with the page.
  useEffect(() => {
    if (!previews) return
    return () => revoke(previews)
  }, [previews])

  const payload = (): MatchCreate | null =>
    company
      ? { id: matchId, company_id: company.id, cliente: toCliente(cliente), lettera: toLettera(lettera) }
      : null
  // What the page shows now. A prefill or a preview can land after the admin has moved
  // on: picked another company, left the step, changed what the preview was made from.
  // Such a response is dropped rather than let it fill the forms with another company's
  // details or jump to an outdated preview (Greptile 4092036036).
  const shown = useRef({ company: null as string | null, step: 0, request: '' })
  const request = JSON.stringify(payload())
  useLayoutEffect(() => {
    shown.current = { company: company?.id ?? null, step, request }
  })

  const loadPrefill = useMutation({
    mutationFn: (companyId: string) => admin.matchPrefill(id, companyId),
    onSuccess: (data, companyId) => {
      if (shown.current.step !== 0 || shown.current.company !== companyId) return
      setPrefill(data)
      setPrefillFor(companyId)
      setFiscal(draftFromFiscal(data.fiscale))
      setCliente(clienteForm(data.cliente))
      setLettera(letteraForm(data.lettera))
      setStep(1)
    },
  })
  const saveFiscal = useMutation({
    mutationFn: (data: FiscalData) => admin.saveFiscal(id, data),
    onSuccess: () => setStep(2),
  })
  const generate = useMutation({
    mutationFn: async (payload: MatchCreate): Promise<Previews> => {
      const letter = await admin.matchPreview(id, payload, 'lettera')
      const quadro = prefill?.quadro_necessario ? await admin.matchPreview(id, payload, 'quadro') : null
      return { lettera: URL.createObjectURL(letter), quadro: quadro ? URL.createObjectURL(quadro) : null }
    },
    onSuccess: (made, sent) => {
      if (shown.current.step !== 3 || shown.current.request !== JSON.stringify(sent)) {
        revoke(made)
        return
      }
      setPreviews(made)
      setStep(4)
    },
  })
  const save = useMutation({
    mutationFn: (payload: MatchCreate) => admin.createMatch(id, payload),
    onSuccess: () => void navigate({ to: '/admin/freelance/$id/contracts', params: { id } }),
  })
  // «Invia per la firma» writes the match first, once: after a refusal the draft exists,
  // and the next click sends that one rather than writing another with a new number.
  const [created, setCreated] = useState<Match | null>(null)
  // REB-406: a refused mail (`mail_inviata: false`) stays on step 5 with the report's
  // own sentence and a link onward, so the admin sees it rather than land on «Match e
  // contratti» none the wiser that the freelancer never got the link.
  const [sentMessage, setSentMessage] = useState<string | null>(null)
  const sendNow = useMutation({
    mutationFn: async (payload: MatchCreate) => {
      const match = created ?? (await admin.createMatch(id, payload))
      setCreated(match)
      return admin.sendMatch(match.id)
    },
    onSuccess: (report) => {
      if (report.mail_inviata === false) {
        setSentMessage(sendReportMessage(report))
        return
      }
      void navigate({ to: '/admin/freelance/$id/contracts', params: { id } })
    },
  })
  const sendFailure = failureOf(sendNow.error, 'Non riesco a inviare per la firma.')
  const previewFailure =
    failureOf(save.error, 'Non riesco a salvare la bozza.') ??
    (sendFailure && created
      ? { ...sendFailure, message: `${sendFailure.message} La bozza è salvata: la trovi in «Match e contratti».` }
      : sendFailure)

  const fiscalFailure = failureOf(saveFiscal.error, 'Non riesco a salvare i dati fiscali.')
  const letterFailure = failureOf(generate.error, 'Non riesco a generare l’anteprima.')
  const name = person.data ? `${person.data.nome} ${person.data.cognome}` : ''

  function submitLettera(event: FormEvent) {
    event.preventDefault()
    const body = payload()
    if (body) generate.mutate(body)
  }

  return (
    <>
      <Header title={name ? `Crea match · ${name}` : 'Crea match'} />
      <ol aria-label="Passi" className="flex flex-wrap gap-x-4 gap-y-1 border-b px-6 py-3 text-sm">
        {STEPS.map((label, index) => (
          <li
            key={label}
            aria-current={index === step ? 'step' : undefined}
            className={cn(index === step ? 'font-medium' : 'text-muted-foreground')}
          >
            {index + 1}. {label}
          </li>
        ))}
      </ol>
      <div className="max-w-3xl space-y-4 p-6">
        {step === 0 && (
          <CompanyStep
            selected={company}
            onSelect={setCompany}
            onNext={() => {
              if (!company) return
              if (prefillFor === company.id) setStep(1)
              else loadPrefill.mutate(company.id)
            }}
            pending={loadPrefill.isPending}
            failure={failureOf(loadPrefill.error, 'Non riesco a leggere questa richiesta.')}
          />
        )}
        {step === 1 && (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              saveFiscal.mutate(toFiscalData(fiscal))
            }}
          >
            <p className="text-sm text-muted-foreground">
              I dati fiscali del freelance, come li stampano i contratti: salvati qui, restano per il prossimo match.
            </p>
            <FiscalFields
              idPrefix="match"
              draft={fiscal}
              onChange={setFiscal}
              wrong={(field) => fiscalFailure?.fields.includes(field) || undefined}
            />
            <StepFooter onBack={() => setStep(0)} next="Avanti" pending={saveFiscal.isPending} failure={fiscalFailure} />
          </form>
        )}
        {step === 2 && (
          <ClienteStep form={cliente} onChange={setCliente} onBack={() => setStep(1)} onNext={() => setStep(3)} />
        )}
        {step === 3 && (
          <form className="space-y-6" onSubmit={submitLettera}>
            {LETTERA_GROUPS.map((group) => (
              <fieldset key={group.title} className="space-y-3">
                <legend className="text-sm font-medium">{group.title}</legend>
                {group.fields.map((field) => (
                  <LetteraInput
                    key={field}
                    field={field}
                    form={lettera}
                    onChange={setLettera}
                    invalid={(key) => letterFailure?.fields.includes(key) || undefined}
                  />
                ))}
              </fieldset>
            ))}
            <StepFooter onBack={() => setStep(2)} next="Genera l’anteprima" pending={generate.isPending} failure={letterFailure} />
          </form>
        )}
        {step === 4 && previews && prefill && (
          <PreviewStep
            previews={previews}
            prefill={prefill}
            onBack={() => {
              setPreviews(null)
              setStep(3)
            }}
            onSave={() => {
              if (created) return void navigate({ to: '/admin/freelance/$id/contracts', params: { id } })
              const body = payload()
              if (body) save.mutate(body)
            }}
            onSend={() => {
              setSentMessage(null)
              const body = payload()
              if (body) sendNow.mutate(body)
            }}
            saving={save.isPending}
            sending={sendNow.isPending}
            locked={created !== null}
            failure={previewFailure}
            sentMessage={sentMessage}
            freelancerId={id}
          />
        )}
      </div>
      <p className="px-6 pb-6">
        <Link to="/admin/freelance/$id/contracts" params={{ id }} className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Match e contratti
        </Link>
      </p>
    </>
  )
}
