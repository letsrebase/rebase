/**
 * The start page's second door, «Carica l’ultima fattura che hai emesso» (spec 2026-09-16
 * §6, REB-224, with Ivan's decision of 2026-09-23 on the card: «prima il cliente»).
 *
 * Three steps, all through the public API and none of it new:
 *
 * 1. **The customer.** A document belongs to exactly one customer, deal or contract
 *    (REB-358, a database check), and an empty space has none, so the door first asks
 *    whom the invoice was issued to: one already on file, found by name as it is typed,
 *    or a new name, which is `POST /api/customers` exactly as «Nuovo cliente» on
 *    Clienti sends it.
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
import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { api, toProblem, unwrap } from '@/lib/api'
import { useAuth, useCan } from '@/lib/auth'
import { queryKeys } from '@/lib/query'
import { useCreateCustomer } from '@/features/customers/queries'
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

/** The typed name, once it has stopped changing for a quarter of a second. */
function useSettled(value: string, delay = 250): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return settled
}

/**
 * One field: the customer's name. As it is typed, the customers already on file whose
 * name or VAT number matches are offered (`GET /api/customers?search=`, the Clienti
 * page's own search), so a space of any size finds its customer, and a name that is
 * already on file is used rather than created twice. Only a name with no exact match
 * creates a customer, and not while the search for it is still on its way.
 */
function ChooseCustomer({ onChosen }: { onChosen: (customer: { id: string; name: string }) => void }) {
  const [nome, setNome] = useState('')
  const typed = nome.trim()
  const query = useSettled(typed)
  const searchable = query.length >= 2
  const matches = useQuery({
    queryKey: queryKeys.customers({ search: query, limit: 8, scope: 'invoice-door' }),
    queryFn: () => unwrap(api.GET('/api/customers', { params: { query: { search: query, limit: 8 } } })),
    enabled: searchable,
  })
  const create = useCreateCustomer()
  const ids = useId()
  const items = searchable ? (matches.data?.items ?? []) : []
  const exact = items.find((item) => item.ragione_sociale.trim().toLowerCase() === typed.toLowerCase())
  // Until the search has answered for exactly what is in the field, «create» could make
  // a second copy of a customer the search was about to show.
  const searching = typed.length >= 2 && (query !== typed || matches.isFetching)

  function next(event: FormEvent) {
    event.preventDefault()
    if (!typed || create.isPending || searching) return
    if (exact) {
      onChosen({ id: exact.id, name: exact.ragione_sociale })
      return
    }
    create.mutate(
      { ragione_sociale: typed },
      { onSuccess: (created) => onChosen({ id: created.id, name: created.ragione_sociale }) },
    )
  }

  return (
    <form onSubmit={next} className="space-y-3 text-sm">
      <fieldset className="space-y-2">
        <legend className="font-medium">A chi l’hai emessa?</legend>
        <Label htmlFor={`${ids}-nome`}>Ragione sociale del cliente</Label>
        <Input
          id={`${ids}-nome`}
          value={nome}
          maxLength={255}
          autoComplete="off"
          onChange={(event) => setNome(event.target.value)}
        />
        <p className="text-muted-foreground">Se è già tra i tuoi clienti, lo trovi qui sotto.</p>
        {items.length > 0 && (
          <ul aria-label="Già tra i tuoi clienti" className="flex flex-wrap gap-2">
            {items.map((item) => (
              <li key={item.id}>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => onChosen({ id: item.id, name: item.ragione_sociale })}
                >
                  {item.partita_iva ? `${item.ragione_sociale} · P.IVA ${item.partita_iva}` : item.ragione_sociale}
                </Button>
              </li>
            ))}
          </ul>
        )}
        {searchable && matches.isError ? (
          <p className="text-muted-foreground">
            Non riesco a cercare tra i clienti che hai già: se è nuovo, crealo qui sotto.
          </p>
        ) : null}
      </fieldset>
      {create.error ? (
        <p role="alert" className="text-destructive">
          {toProblem(create.error).detail}
        </p>
      ) : null}
      <Button type="submit" disabled={!typed || create.isPending || searching}>
        {exact ? `Usa ${exact.ragione_sociale}` : 'Crea il cliente e vai avanti'}
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
  // The document is created once per attempt: a failed upload of its file removes it
  // (below), and only a removal that fails too leaves it here to be retried onto.
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
      // A document whose file never arrived is removed rather than left behind as an
      // empty `fattura` on the customer, whether or not the person tries again: the next
      // drop files a fresh one. Kept for a retry only if even the removal fails.
      const orphan = filed.current
      if (orphan !== null) {
        let removed = false
        try {
          const result = await api.DELETE('/api/documents/{document_id}', {
            params: { path: { document_id: orphan } },
          })
          removed = result.error === undefined
        } catch {
          removed = false
        }
        if (removed) {
          filed.current = null
          setDocumentId(null)
        }
      }
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

  // Shown unless the switch is known to be on: it is off by default, and a read that
  // failed must not hide the one prerequisite the prompt cannot work without.
  const fullAccessOff = settings.isError || settings.data?.mcp_full_access === false
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
