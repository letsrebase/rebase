/**
 * The start page's second door, «Carica l’ultima fattura che hai emesso» (spec 2026-09-16
 * §6, REB-224, with Ivan's decision of 2026-09-23 on the card: «prima il cliente»).
 *
 * Three steps, all through the public API and none of it new:
 *
 * 1. **The customer.** A document belongs to exactly one customer, deal or contract
 *    (REB-358, a database check), and an empty space has none, so the door first asks
 *    whom the invoice was issued to: one already on file, or a new name, which is
 *    `POST /api/customers` exactly as «Nuovo cliente» on Clienti sends it.
 * 2. **The PDF.** One file, a PDF, filed as a `fattura` document of that customer
 *    (`POST /api/documents`, then its first version), because that is the one shape
 *    `import_issued_invoice` accepts as the original (`pdf_sorgente.document_id`).
 * 3. **The handoff.** The prompt with the document's id, the connection beside it when
 *    this person has no token yet, and «Registrata» with the link as soon as an invoice
 *    of that customer has this document as its `pdf_document_id`. Nothing is parsed on
 *    the server (§2) and nothing new is stored on the document (§6); which document the
 *    door is waiting on is remembered in this browser (`invoiceHandoff.ts`).
 *
 * Registering an issued invoice is `require_admin` in the service, so the door is the
 * admin's, like the fiscal step: anybody else reads who does it. The assistant also needs
 * the space's «Accesso completo per i token dell'agente», because `import_issued_invoice`
 * is registered only under `mcp_full_access` (it cannot be undone); the handoff says so,
 * with the link, while that switch is off.
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { FileText } from 'lucide-react'
import { useId, useRef, useState, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { api, toProblem, unwrap } from '@/lib/api'
import { useAuth, useCan } from '@/lib/auth'
import { queryKeys } from '@/lib/query'
import { useCreateCustomer, useCustomers } from '@/features/customers/queries'
import { useDocument, useUploadVersion } from '@/features/documents/queries'
import { UploadDropzone } from '@/features/documents/UploadDropzone'
import { SPACE_SETTINGS_KEY } from '@/features/settings/SpacePanel'
import { ConnectAgentPanel } from '@/features/tokens/ConnectAgentPanel'
import type { CreatedToken } from '@/features/tokens/queries'
import { useUnsavedTokenGuard } from '@/features/tokens/useUnsavedTokenGuard'
import { PromptBody } from './CopyPrompt'
import { forgetHandoff, readHandoff, rememberHandoff, type InvoiceHandoff } from './invoiceHandoff'
import { invoicePrompt } from './prompts'

const PDF = 'application/pdf'
// `documents.titolo` is `String(200)`, and `DocumentCreate` refuses anything longer.
const TITOLO_MAX = 200

export function InvoiceDoor({ assistantConnected }: { assistantConnected: boolean }) {
  const { user } = useAuth()
  const userId = user?.id ?? ''
  const canImport = useCan('import_issued_invoice')
  const [handoff, setHandoff] = useState<InvoiceHandoff | null>(() => readHandoff(userId))

  function started(next: InvoiceHandoff) {
    rememberHandoff(userId, next)
    setHandoff(next)
  }

  function reset() {
    forgetHandoff(userId)
    setHandoff(null)
  }

  return (
    <section aria-labelledby="porta-fattura" className="space-y-3 border p-4">
      <header className="flex items-start gap-3">
        <FileText className="mt-0.5 size-5 shrink-0" aria-hidden />
        <div className="space-y-1">
          <h3 id="porta-fattura" className="font-medium">
            Carica l’ultima fattura che hai emesso
          </h3>
          <p className="text-muted-foreground text-sm">
            Il PDF che hai mandato al cliente: l’assistente lo legge e la registra con il suo
            numero, le righe e i totali.
          </p>
        </div>
      </header>
      {!canImport ? (
        <p className="text-muted-foreground text-sm">
          Registrare una fattura emessa è un passo dell’amministratore dello spazio, con il
          suo assistente.
        </p>
      ) : handoff ? (
        <Handoff handoff={handoff} assistantConnected={assistantConnected} onReset={reset} />
      ) : (
        <Upload onUploaded={started} />
      )}
    </section>
  )
}

/** Steps 1 and 2: the customer, then the one PDF. */
function Upload({ onUploaded }: { onUploaded: (handoff: InvoiceHandoff) => void }) {
  const [customer, setCustomer] = useState<{ id: string; name: string } | null>(null)
  if (customer === null) return <ChooseCustomer onChosen={setCustomer} />
  return (
    <UploadPdf
      customer={customer}
      onChangeCustomer={() => setCustomer(null)}
      onUploaded={onUploaded}
    />
  )
}

function ChooseCustomer({ onChosen }: { onChosen: (customer: { id: string; name: string }) => void }) {
  const customers = useCustomers({ limit: 200 })
  const create = useCreateCustomer()
  const [existing, setExisting] = useState('')
  const [nuovo, setNuovo] = useState('')
  const ids = useId()
  const items = customers.data?.items ?? []
  const nome = nuovo.trim()

  function next(event: FormEvent) {
    event.preventDefault()
    if (create.isPending) return
    if (nome) {
      create.mutate(
        { ragione_sociale: nome },
        { onSuccess: (created) => onChosen({ id: created.id, name: created.ragione_sociale }) },
      )
      return
    }
    const chosen = items.find((item) => item.id === existing)
    if (chosen) onChosen({ id: chosen.id, name: chosen.ragione_sociale })
  }

  // While the list loads, offering only «un cliente nuovo» would invite a duplicate of
  // one that is on file: the step waits for the answer, or says it could not get one.
  if (customers.isPending) return <p className="text-muted-foreground text-sm">Caricamento…</p>

  return (
    <form onSubmit={next} className="space-y-3 text-sm">
      <fieldset className="space-y-3">
        <legend className="font-medium">A chi l’hai emessa?</legend>
        {customers.isError ? (
          <p className="text-muted-foreground">
            Non riesco a leggere i clienti che hai già: puoi scriverne il nome qui sotto.
          </p>
        ) : null}
        {items.length > 0 && (
          <>
            <div className="space-y-2">
              <Label htmlFor={`${ids}-esistente`}>Un cliente che hai già</Label>
              <Select
                value={existing}
                onValueChange={(value) => {
                  setExisting(value)
                  setNuovo('')
                }}
              >
                <SelectTrigger id={`${ids}-esistente`} className="w-full">
                  <SelectValue placeholder="Scegli il cliente…" />
                </SelectTrigger>
                <SelectContent>
                  {items.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.ragione_sociale}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <p className="text-muted-foreground">oppure</p>
          </>
        )}
        <div className="space-y-2">
          <Label htmlFor={`${ids}-nuovo`}>
            {items.length > 0 ? 'Un cliente nuovo: ragione sociale' : 'Ragione sociale del cliente'}
          </Label>
          <Input
            id={`${ids}-nuovo`}
            value={nuovo}
            maxLength={255}
            onChange={(event) => {
              setNuovo(event.target.value)
              if (event.target.value.trim()) setExisting('')
            }}
          />
        </div>
      </fieldset>
      {create.error ? (
        <p role="alert" className="text-destructive">
          {toProblem(create.error).detail}
        </p>
      ) : null}
      <Button type="submit" disabled={create.isPending || (!nome && !existing)}>
        Avanti
      </Button>
    </form>
  )
}

function UploadPdf({
  customer,
  onChangeCustomer,
  onUploaded,
}: {
  customer: { id: string; name: string }
  onChangeCustomer: () => void
  onUploaded: (handoff: InvoiceHandoff) => void
}) {
  // The document is created once: a failed upload of its file is retried onto the same
  // row rather than leaving a second, empty `fattura` behind for every attempt.
  const [documentId, setDocumentId] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Read by `handle` itself, not from its render: two drops in one tick both see the
  // same closure, and state set by the first is not there yet for the second.
  const inFlight = useRef(false)
  const filed = useRef<string | null>(null)
  const createDocument = useMutation({
    mutationFn: (titolo: string) =>
      unwrap(
        api.POST('/api/documents', {
          body: { customer_id: customer.id, tipo: 'fattura', titolo, custom_fields: {} },
        }),
      ),
  })
  const upload = useUploadVersion()

  async function handle(files: File[]) {
    if (inFlight.current) return
    setProblem(null)
    const [dropped] = files
    if (files.length !== 1 || !dropped) {
      setProblem('Una fattura alla volta: scegli un solo PDF.')
      return
    }
    const namedPdf = dropped.type === '' && /\.pdf$/i.test(dropped.name)
    if (dropped.type !== PDF && !namedPdf) {
      setProblem('Serve il PDF della fattura, quello che hai mandato al cliente.')
      return
    }
    // A PDF the system did not type (no MIME, a `.pdf` name) would travel as
    // `application/octet-stream`, which the server refuses after the document exists.
    const file = namedPdf ? new File([dropped], dropped.name, { type: PDF }) : dropped
    inFlight.current = true
    setBusy(true)
    try {
      let id = filed.current
      if (id === null) {
        const titolo = (file.name.trim() || 'Fattura emessa').slice(0, TITOLO_MAX)
        id = (await createDocument.mutateAsync(titolo)).id
        filed.current = id
        setDocumentId(id)
      }
      await upload.mutateAsync({ documentId: id, file })
      onUploaded({ documentId: id, customerId: customer.id, customerName: customer.name })
    } catch (error) {
      setProblem(toProblem(error).detail)
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 text-sm">
      <p>
        Emessa a <span className="font-medium">{customer.name}</span>.{' '}
        {documentId === null && (
          <button type="button" onClick={onChangeCustomer} className="underline underline-offset-4">
            Cambia cliente
          </button>
        )}
      </p>
      <UploadDropzone
        accept={PDF}
        busy={busy}
        inputLabel="Carica il PDF della fattura"
        onFiles={(files) => void handle(files)}
      />
      {problem ? (
        <p role="alert" className="text-destructive">
          {problem}
        </p>
      ) : null}
    </div>
  )
}

/**
 * Step 3. The invoice read is scoped to the customer and capped at 200, the dropdowns'
 * own cap: an imported invoice of a customer with more than two hundred on file would
 * not be found, which this door, for the first invoice of a new space, does not meet.
 * It is read on every mount, every twenty seconds until found, and whenever the tab
 * regains focus, which is when the person comes back from their assistant.
 */
function Handoff({
  handoff,
  assistantConnected,
  onReset,
}: {
  handoff: InvoiceHandoff
  assistantConnected: boolean
  onReset: () => void
}) {
  const uploaded = useDocument(handoff.documentId)
  const registered = useQuery({
    queryKey: queryKeys.invoices({
      customer_id: handoff.customerId,
      document_id: handoff.documentId,
      limit: 200,
      scope: 'invoice-door',
    }),
    queryFn: async () => {
      const page = await unwrap(
        api.GET('/api/invoices', { params: { query: { customer_id: handoff.customerId, limit: 200 } } }),
      )
      return page.items.find((invoice) => invoice.pdf_document_id === handoff.documentId) ?? null
    },
    refetchInterval: (query) => (query.state.data ? false : 20_000),
    // Never fresh while waiting: the assistant registers the invoice from outside this
    // tab, so nothing here invalidates the read, and «Primi passi» opened again within
    // the app's thirty seconds must look again rather than show the cached «not yet».
    staleTime: 0,
  })
  const settings = useQuery({
    queryKey: SPACE_SETTINGS_KEY,
    queryFn: () => unwrap(api.GET('/api/settings/space')),
    enabled: !registered.data,
  })
  const [issued, setIssued] = useState<CreatedToken | null>(null)
  useUnsavedTokenGuard(Boolean(issued))

  // The document was deleted (or the id remembered here belongs to nothing): nothing is
  // waiting any more, and saying so is better than a prompt about a missing file. Only
  // on a 404: a request that merely failed says nothing about the file.
  if (uploaded.isError && toProblem(uploaded.error).status === 404) {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-muted-foreground">La fattura caricata non è più tra i documenti.</p>
        <Button type="button" variant="outline" onClick={onReset}>
          Carica un’altra fattura
        </Button>
      </div>
    )
  }

  const invoice = registered.data
  if (invoice) {
    const label =
      invoice.numero !== null && invoice.anno !== null ? `${invoice.numero}/${invoice.anno}` : ''
    return (
      <div role="status" className="space-y-2 text-sm">
        <p className="font-medium">Registrata</p>
        <p>
          <Link
            to="/app/invoices/$invoiceId"
            params={{ invoiceId: invoice.id }}
            className="underline underline-offset-4"
          >
            {`Apri la fattura${label ? ` ${label}` : ''} di ${handoff.customerName}`}
          </Link>
        </p>
        <Button type="button" variant="outline" onClick={onReset}>
          Carica un’altra fattura
        </Button>
      </div>
    )
  }

  const fullAccessOff = settings.data?.mcp_full_access === false
  return (
    <div className="space-y-3 text-sm">
      <p>
        Caricata tra i documenti di <span className="font-medium">{handoff.customerName}</span>.
        Ora passala all’assistente con questo prompt:
      </p>
      <PromptBody text={invoicePrompt(handoff)} />
      {fullAccessOff && (
        <p role="note" className="bg-muted/50 border px-3 py-2">
          L’assistente la registra solo con l’accesso completo per i token dell’agente:
          attivalo in{' '}
          <Link to="/app/settings/space" className="font-medium underline underline-offset-4">
            Impostazioni → Spazio
          </Link>
          , poi ricollega l’assistente perché veda lo strumento.
        </p>
      )}
      {(!assistantConnected || issued !== null) && (
        <div className="space-y-3 border-t pt-3">
          <p className="font-medium">Prima collega l’assistente</p>
          <ConnectAgentPanel issued={issued} onIssued={setIssued} />
          {issued && (
            <Button type="button" variant="outline" onClick={() => setIssued(null)}>
              Ho copiato il token
            </Button>
          )}
        </div>
      )}
      <p className="text-muted-foreground">
        Quando l’avrà registrata, qui compare il link alla fattura.
      </p>
      <Button type="button" variant="ghost" onClick={onReset}>
        Carica un’altra fattura
      </Button>
    </div>
  )
}
