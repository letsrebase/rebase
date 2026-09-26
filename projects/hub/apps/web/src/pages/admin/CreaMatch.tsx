import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { cn } from '@rebase/ui/cn'
import { admin, ApiError, type Company, type Fiscal, type FiscalData, type Match, type MatchCreate } from '@/lib/api'
import {
  ALTRE_CONDIZIONI_FIELDS,
  CLIENTE_EMPTY,
  CLIENTE_FIELDS,
  DRAFT_SAVED,
  FISCAL_EMPTY,
  FISCAL_FIELDS,
  LETTERA_EMPTY,
  clienteComplete,
  clienteForm,
  draftFromFiscal,
  failureOf,
  fiscalToSave,
  giorniPrevistiToSend,
  olderFiscal,
  refillFiscal,
  typedAfterSave,
  typedFiscalFields,
  letteraForm,
  letteraToSend,
  payModeOf,
  sendReportMessage,
  toCliente,
  withPayMode,
  type ClienteForm,
  type FiscalDraft,
  type FiscalKey,
  type LetteraForm,
} from '@/lib/contracts'
import { ChiStep } from './crea-match/ChiStep'
import { CondizioniStep } from './crea-match/CondizioniStep'
import { ControllaStep, type Review } from './crea-match/ControllaStep'
import { Header } from './lists'

const STEPS = ['Chi e per chi', 'Condizioni', 'Controlla e invia'] as const

// A request closed after it was picked: `create` and the check refuse it by this name.
const REQUEST_FIELD = 'company_id'
const onStepOne = (field: string) => field === REQUEST_FIELD || CLIENTE_FIELDS.has(field) || FISCAL_FIELDS.has(field)

function revoke(review: Review) {
  URL.revokeObjectURL(review.lettera)
  if (review.quadro) URL.revokeObjectURL(review.quadro)
}

/** «Crea match» (REB-387, REB-476): three steps from a company request to a draft match
 *  with its documents. Step 1 shows what the prefill knows and asks only what is missing;
 *  step 2 asks the conditions of this engagement, the rest of the letter closed away;
 *  step 3 says in the hub's own sentences what saving would do, beside previews nothing
 *  stores. «Salva senza inviare» writes the match and takes the letter's number;
 *  «Invia per la firma» writes it once and sends it (REB-390). The company's
 *  `budget_giornaliero` is never shown or sent from this page (spec § 1h). */
export function AdminCreaMatch() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id/match/new' })
  const navigate = useNavigate()
  const person = useQuery({ queryKey: ['freelancer', id], queryFn: () => admin.freelancer(id) })
  // One id per wizard run (REB-406): mounting this page is starting one over, and the
  // page always navigates away once a match is written. Sent with both «Salva senza
  // inviare» and «Invia per la firma», so a retry after the response is lost (a network
  // drop, not the 503 `sendNow` already recovers from with `created`) writes nothing
  // new: the server returns the match already written under it.
  const [matchId] = useState(() => crypto.randomUUID())
  const [step, setStep] = useState(0)
  const [company, setCompany] = useState<Company | null>(null)
  const [prefillFor, setPrefillFor] = useState<string | null>(null)
  const [savedFiscal, setSavedFiscal] = useState<Fiscal | null>(null)
  // The same record, readable at once from a response's callback, which may run before
  // the page renders what the previous one set.
  const heldFiscal = useRef<Fiscal | null>(null)
  function holdFiscal(record: Fiscal | null) {
    heldFiscal.current = record
    setSavedFiscal(record)
  }
  const [fiscal, setFiscal] = useState<FiscalDraft>(FISCAL_EMPTY)
  // The tax fields the admin typed in since the draft was last filled and not saved yet:
  // a save landing refills every other one and leaves these as typed.
  const fiscalTyped = useRef(new Set<FiscalKey>())
  const [editFiscal, setEditFiscal] = useState(false)
  const [cliente, setCliente] = useState<ClienteForm>(CLIENTE_EMPTY)
  const [editCliente, setEditCliente] = useState(false)
  const [lettera, setLettera] = useState<LetteraForm>(LETTERA_EMPTY)
  // «Giorni previsti» (REB-497): the match's, so beside the letter's form, never in it.
  const [giorni, setGiorni] = useState('')
  const [dayRate, setDayRate] = useState('')
  const [altreOpen, setAltreOpen] = useState(false)
  const [review, setReview] = useState<Review | null>(null)
  // «Invia per la firma» writes the match first, once: after a refusal the draft exists,
  // and the next click sends that one rather than writing another with a new number.
  const [created, setCreated] = useState<Match | null>(null)
  // REB-406: a refused mail (`mail_inviata: false`) stays on step 3 with the report's
  // own sentence and a link onward, so the admin sees it rather than land on «Match e
  // contratti» none the wiser that the freelancer never got the link.
  const [sentMessage, setSentMessage] = useState<string | null>(null)

  // A preview is a blob in this tab's memory: let it go once replaced, or with the page.
  useEffect(() => {
    if (!review) return
    return () => revoke(review)
  }, [review])

  // A new step replaces the button that led to it: focus goes to the step's heading, so
  // a keyboard or a screen reader starts there rather than at the top of the page.
  const stepHeading = useRef<HTMLHeadingElement>(null)
  const lastStep = useRef(step)
  useEffect(() => {
    if (lastStep.current === step) return
    lastStep.current = step
    stepHeading.current?.focus()
  }, [step])

  const payload = (): MatchCreate | null =>
    company
      ? {
          id: matchId,
          company_id: company.id,
          cliente: toCliente(cliente),
          lettera: letteraToSend(lettera),
          giorni_previsti: giorniPrevistiToSend(giorni),
        }
      : null
  // What the page shows now. A prefill, a tax save or a check can land after the admin
  // has moved on: picked another company, left the step, changed what the check was
  // asked about. Such a response is dropped rather than let it fill the forms with
  // another company's details or jump to an outdated check (Greptile 4092036036).
  const shown = useRef({
    company: null as string | null,
    prefillFor: null as string | null,
    step: 0,
    request: '',
    fiscal: FISCAL_EMPTY,
  })
  const request = JSON.stringify(payload())
  useLayoutEffect(() => {
    shown.current = { company: company?.id ?? null, prefillFor, step, request, fiscal }
  })

  function landOnContracts(notice: string) {
    void navigate({ to: '/admin/freelance/$id/contracts', params: { id }, state: { notice } })
  }

  const loadPrefill = useMutation({
    mutationFn: (companyId: string) => admin.matchPrefill(id, companyId),
    onSuccess: (data, companyId) => {
      if (shown.current.step !== 0 || shown.current.company !== companyId) return
      const client = clienteForm(data.cliente)
      const form = letteraForm(data.lettera)
      setPrefillFor(companyId)
      if (olderFiscal(data.fiscale, heldFiscal.current)) {
        // Read before a tax save the page already has back: the saved record and the
        // draft stay, and the section opens only on text typed and not saved yet.
        setEditFiscal(fiscalTyped.current.size > 0)
      } else {
        holdFiscal(data.fiscale)
        setFiscal(draftFromFiscal(data.fiscale))
        fiscalTyped.current = new Set()
        setEditFiscal(false)
      }
      setCliente(client)
      setEditCliente(!clienteComplete(client))
      setLettera(withPayMode(form, payModeOf(form)))
      // An estimate typed for another request is not this one's.
      setGiorni('')
      setDayRate(form.compenso)
      setAltreOpen(false)
    },
  })
  const saveFiscal = useMutation({
    mutationFn: ({ data }: { data: FiscalData; companyId: string }) => admin.saveFiscal(id, data),
    onSuccess: (saved, { data, companyId }) => {
      // The tax data are the freelancer's, whichever request is picked now: what the
      // server saved is what the page shows from here, except in a field the admin has
      // typed in since, for the request now picked.
      holdFiscal(saved)
      const typed = typedAfterSave(shown.current.fiscal, fiscalTyped.current, data)
      fiscalTyped.current = typed
      setFiscal((current) => refillFiscal(current, saved, typed))
      // Closing the section and moving on belong to the request they were saved for;
      // after a switch the admin may have opened the section again for the new one. A
      // field typed in again while the save was on its way is not saved yet: the section
      // stays open on it.
      const now = shown.current
      if (typed.size > 0 || now.step !== 0 || now.company !== companyId || now.prefillFor !== companyId) return
      setEditFiscal(false)
      setStep(1)
    },
  })
  const check = useMutation({
    // The check and the letter's preview together; the framework agreement's preview only
    // when the check says one leaves first. The check's refusal wins over the preview's,
    // since it names the field as saving would. Blob URLs are made only once every PDF
    // is back, so a failure leaves none behind.
    mutationFn: async (body: MatchCreate): Promise<Review> => {
      const [words, letter] = await Promise.allSettled([
        admin.matchCheck(id, body),
        admin.matchPreview(id, body, 'lettera'),
      ])
      if (words.status === 'rejected') throw words.reason
      if (letter.status === 'rejected') throw letter.reason
      const quadro = words.value.quadro_necessario ? await admin.matchPreview(id, body, 'quadro') : null
      return {
        check: words.value,
        lettera: URL.createObjectURL(letter.value),
        quadro: quadro ? URL.createObjectURL(quadro) : null,
      }
    },
    onSuccess: (made, sent) => {
      if (shown.current.step !== 1 || shown.current.request !== JSON.stringify(sent)) {
        revoke(made)
        return
      }
      setReview(made)
      setStep(2)
    },
    // A refused field must be marked where the admin sees it: inside «Altre condizioni»,
    // opened; on «Chi e per chi», back there with its section's fields open.
    onError: (error, sent) => {
      if (shown.current.step !== 1 || shown.current.request !== JSON.stringify(sent)) return
      if (!(error instanceof ApiError)) return
      if (error.fields.some((field) => ALTRE_CONDIZIONI_FIELDS.has(field))) setAltreOpen(true)
      if (error.fields.some((field) => CLIENTE_FIELDS.has(field))) setEditCliente(true)
      if (error.fields.some((field) => FISCAL_FIELDS.has(field))) setEditFiscal(true)
      if (error.fields.some(onStepOne)) setStep(0)
    },
  })
  const save = useMutation({
    mutationFn: (body: MatchCreate) => admin.createMatch(id, body),
    onSuccess: () => landOnContracts(DRAFT_SAVED),
  })
  const sendNow = useMutation({
    mutationFn: async (body: MatchCreate) => {
      const match = created ?? (await admin.createMatch(id, body))
      setCreated(match)
      return admin.sendMatch(match.id)
    },
    onSuccess: (report) => {
      const sentence = sendReportMessage(report)
      if (report.mail_inviata === false) setSentMessage(sentence)
      else landOnContracts(sentence)
    },
  })

  const loaded = company !== null && prefillFor === company.id
  const prefillFailure =
    !loaded && loadPrefill.variables === company?.id
      ? failureOf(loadPrefill.error, 'Non riesco a leggere questa richiesta: sceglila di nuovo per riprovare.')
      : null
  const fiscalFailure = failureOf(saveFiscal.error, 'Non riesco a salvare i dati fiscali.')
  const checkFailure = failureOf(check.error, 'Non riesco a preparare il riepilogo.')
  const chiFailure =
    fiscalFailure ?? prefillFailure ?? (checkFailure?.fields.some(onStepOne) ? checkFailure : null)
  const sendFailure = failureOf(sendNow.error, 'Non riesco a inviare per la firma.')
  const reviewFailure =
    failureOf(save.error, 'Non riesco a salvare la bozza.') ??
    (sendFailure && created
      ? { ...sendFailure, message: `${sendFailure.message} La bozza è salvata: la trovi in «Match e contratti».` }
      : sendFailure)
  const name = person.data ? `${person.data.nome} ${person.data.cognome}` : ''
  const nome = person.data?.nome ?? ''

  function select(item: Company) {
    // A check that refused the request picked before is not about this pick.
    check.reset()
    setCompany(item)
    if (item.id === prefillFor) return
    if (loadPrefill.isPending && loadPrefill.variables === item.id) return
    // The forms are about to hold another request's prefill: a refused tax save is no
    // longer about what they show.
    saveFiscal.reset()
    loadPrefill.mutate(item.id)
  }

  function submitChi() {
    if (!company || !loaded) return
    // A refusal of the previous check stays on «Chi e per chi» while the admin fixes
    // what it names, whether they came back through «Indietro» or were brought back;
    // «Condizioni» starts clean.
    check.reset()
    const data = fiscalToSave(savedFiscal, fiscal, editFiscal)
    if (data) saveFiscal.mutate({ data, companyId: company.id })
    else setStep(1)
  }

  function submitCondizioni() {
    const body = payload()
    if (body) check.mutate(body)
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
        <h2 ref={stepHeading} tabIndex={-1} className="sr-only">
          Passo {step + 1} di {STEPS.length}: {STEPS[step]}
        </h2>
        {step === 0 && (
          <ChiStep
            nome={nome}
            selected={company}
            onSelect={select}
            loaded={loaded}
            pickAgain={prefillFailure !== null || (checkFailure?.fields.includes(REQUEST_FIELD) ?? false)}
            cliente={cliente}
            onCliente={setCliente}
            editCliente={editCliente}
            onEditCliente={() => setEditCliente(true)}
            savedFiscal={savedFiscal}
            fiscal={fiscal}
            onFiscal={(next) => {
              for (const key of typedFiscalFields(fiscal, next)) fiscalTyped.current.add(key)
              setFiscal(next)
            }}
            editFiscal={editFiscal}
            onEditFiscal={() => setEditFiscal(true)}
            onNext={submitChi}
            pending={saveFiscal.isPending}
            failure={chiFailure}
          />
        )}
        {step === 1 && (
          <CondizioniStep
            form={lettera}
            onChange={setLettera}
            giorniPrevisti={giorni}
            onGiorniPrevisti={setGiorni}
            dayRate={dayRate}
            altreOpen={altreOpen}
            onAltreOpen={setAltreOpen}
            onBack={() => setStep(0)}
            onNext={submitCondizioni}
            pending={check.isPending}
            failure={checkFailure}
          />
        )}
        {step === 2 && review && (
          <ControllaStep
            review={review}
            giorniPrevisti={giorniPrevistiToSend(giorni)}
            nome={nome}
            onBack={() => {
              setReview(null)
              setStep(1)
            }}
            onSave={() => {
              if (created) return landOnContracts(DRAFT_SAVED)
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
            failure={reviewFailure}
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
