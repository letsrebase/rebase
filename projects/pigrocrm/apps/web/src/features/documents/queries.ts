import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchWithRefresh, isMissingFile, toProblem, unwrap } from '@/lib/api'
import { downloadFromApi } from '@/lib/download'
import type { StatusTone } from '@/components/StatusPill'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

export type Document = components['schemas']['DocumentRead']
export type DocumentVersion = components['schemas']['DocumentVersionRead']
export type DocumentPage = components['schemas']['DocumentPage']
export type Template = components['schemas']['TemplateRead']
export type TemplatePage = components['schemas']['TemplatePage']
export type TemplateDescription = components['schemas']['TemplateDescription']
export type TemplateVariable = components['schemas']['TemplateVariable']
export type LegalEntity = components['schemas']['LegalEntityRead']

export type OfferState = 'draft' | 'sent' | 'accepted' | 'rejected'

/**
 * A document belongs to a customer **or** to a deal, never both -- so the hooks take
 * a discriminated object rather than two optional strings. There is no "empty id"
 * spelling to get wrong (B1), and a call site physically cannot ask for both.
 */
export type DocumentOwner = { customerId: string } | { dealId: string }

/**
 * The backend's own state machine (`OFFER_TRANSITIONS` in
 * packages/core/src/pigrocrm/core/documents/service.py), read here only to decide
 * which buttons to draw. It is not a second copy of the rule: a transition the UI
 * offers is still checked server-side, and a refused one comes back as the server's
 * own 409 message, which is what the user sees.
 */
export const OFFER_TRANSITIONS: Record<OfferState, OfferState[]> = {
  draft: ['sent'],
  sent: ['accepted', 'rejected', 'draft'],
  accepted: [],
  rejected: [],
}

export const OFFER_STATE_LABELS: Record<OfferState, string> = {
  draft: 'Bozza',
  sent: 'Inviata',
  accepted: 'Accettata',
  rejected: 'Rifiutata',
}

/**
 * The tone each offer state reads as in a `StatusPill` (design spec §4), beside the
 * labels and total over `OfferState` for the same reason `OFFER_TRANSITIONS` is -- which
 * means the compile breaks when that hand-written union is widened, not when the server
 * grows a state.
 *
 * `accepted` is `ink`, not gold: an accepted offer is the settled, ordinary outcome --
 * the thing every offer is trying to become -- and gold in this product means "waiting
 * on somebody", which an accepted offer no longer is. `sent` is also `ink` because
 * it is a real, correct state and a table of sent offers must not read as a table of
 * warnings; `draft` claims nothing yet, and `rejected` is the one that has gone the
 * wrong way.
 */
export const OFFER_STATE_TONE: Record<OfferState, StatusTone> = {
  draft: 'muted',
  sent: 'ink',
  accepted: 'ink',
  rejected: 'danger',
}

/** What a `documents.type` is (`DocumentRead.type`), the types the server's `DOCUMENT_TYPES` accepts. */
export type DocumentType = Document['type']

/**
 * One word for every `documents.type` the API stores, the invoice side included: the
 * customer's Documenti tab lists the generated invoice PDF, its FatturaPA XML and the
 * proforma beside the offers, and a code without a word printed the English enum raw. A
 * `Record` over the API's own union, so a type added there is a compile error here.
 */
const DOCUMENT_TYPE_WORDS: Record<DocumentType, string> = {
  offer: 'Offerta',
  contract: 'Contratto',
  minutes: 'Verbale',
  document: 'Documento',
  invoice: 'Fattura',
  invoice_xml: 'Fattura XML',
  proforma: 'Proforma',
  hours_report: 'Rapporto ore',
}

/** The two template types no document can be (`TemplateType` in `templates/schemas.py`). */
const TEMPLATE_ONLY_WORDS = { email: 'Email', payment_reminder: 'Sollecito' }

/** A word for every type a document or a template can have. */
export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  ...DOCUMENT_TYPE_WORDS,
  ...TEMPLATE_ONLY_WORDS,
}

/**
 * Whether a template of this type can make a document: the server refuses any other
 * (`DocumentTemplating.require_template`), so the picker does not offer it.
 */
export function canMakeDocument(templateType: string): templateType is DocumentType {
  return Object.hasOwn(DOCUMENT_TYPE_WORDS, templateType)
}

function ownerQuery(owner: DocumentOwner): { customer_id?: string; deal_id?: string } {
  return 'customerId' in owner ? { customer_id: owner.customerId } : { deal_id: owner.dealId }
}

export function useDocuments(owner: DocumentOwner) {
  return useQuery({
    queryKey: queryKeys.documents(owner),
    queryFn: () => unwrap(api.GET('/api/documents', { params: { query: ownerQuery(owner) } })),
  })
}

export function useDocument(documentId: string) {
  return useQuery({
    // Disabled rather than conditionally called: a hook cannot be called
    // conditionally, and an empty id used to produce a 307 to the *list* endpoint
    // with an absolute URL that bypassed Vite's proxy (residuo B1).
    enabled: documentId !== '',
    queryKey: queryKeys.document(documentId),
    queryFn: () =>
      unwrap(
        api.GET('/api/documents/{document_id}', {
          params: { path: { document_id: documentId } },
        }),
      ),
  })
}

export function useDocumentVersions(documentId: string) {
  return useQuery({
    enabled: documentId !== '',
    queryKey: queryKeys.documentVersions(documentId),
    queryFn: () =>
      unwrap(
        api.GET('/api/documents/{document_id}/versions', {
          params: { path: { document_id: documentId } },
        }),
      ),
  })
}

export function useCreateDocument(owner: DocumentOwner) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.POST('/api/documents', {
          body: { ...ownerQuery(owner), ...body } as never,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents() })
    },
  })
}

export function useCreateFromTemplate(owner: DocumentOwner) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      template_id: string
      title: string
      variables: Record<string, unknown>
    }) =>
      unwrap(
        api.POST('/api/documents/from-template', {
          body: { ...ownerQuery(owner), ...body } as never,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents() })
    },
  })
}

/**
 * `openapi-fetch` cannot send a `FormData` body for a multipart route, so this one
 * mutation goes outside the generated client -- the single documented exception, and
 * it is still inside a hook, never in a component.
 *
 * Through `fetchWithRefresh` rather than a bare `fetch`, for the two reasons every
 * other caller outside the client uses it (lib/api.ts): the session cookie travels
 * (`credentials: 'include'`, set there), and an upload attempted after the
 * fifteen-minute access cookie expired renews the session and retries instead of
 * failing with «Autenticazione richiesta» -- the drop that quietly did nothing after
 * a quiet spell. It also applies `tenantPrefix`, which this absolute-from-root
 * `/api/...` was missing: inside a space, the upload was addressing the *root*
 * installation. No `Content-Type` is set by hand, because the browser must append its
 * own multipart boundary.
 */
export async function putVersion(documentId: string, file: File): Promise<DocumentVersion> {
  const body = new FormData()
  body.append('file', file)
  const response = await fetchWithRefresh(`/api/documents/${documentId}/versions`, {
    method: 'POST',
    body,
  })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) throw toProblem(payload, response.status)
  return payload as DocumentVersion
}

/**
 * The document id travels in the mutation's own call, not as a constructor
 * argument: `DocumentsTab.handleFiles` only learns the id once `useCreateDocument`
 * resolves, in the same async handler that then has to upload the first version --
 * a hook whose id is instead fixed at *render* time (the brief's own original
 * shape, `useUploadVersion(documentId)`) would still be reading the id from the
 * render where it was `null`, since setting state and re-rendering never happens
 * synchronously inside that same handler. Reproduced live: an upload landed on
 * `/api/documents//versions` and came back 404 before this fix.
 */
export function useUploadVersion() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (args: { documentId: string; file: File }) =>
      putVersion(args.documentId, args.file),
    onSuccess: (_date, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.document(variables.documentId) })
      void queryClient.invalidateQueries({
        queryKey: queryKeys.documentVersions(variables.documentId),
      })
    },
  })
}

export function useSetOfferState(documentId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (status: OfferState) =>
      unwrap(
        api.POST('/api/documents/{document_id}/status', {
          params: { path: { document_id: documentId } },
          body: { status },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.document(documentId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents() })
      void queryClient.invalidateQueries({ queryKey: queryKeys.timeline('document', documentId) })
      // Accepting an offer may move a deal (automation A1), which changes the pipeline, the
      // closures and the "offerta accettata, deal non vinto" signal. By the ['dashboard']
      // prefix rather than an exact key: this mutation cannot know which period the user is
      // looking at, and the prefix covers every cached period and every tab.
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboardAll })
    },
  })
}

export function useRegenerateVersion(documentId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (number: number) =>
      unwrap(
        api.POST('/api/documents/{document_id}/versions/{number}/regenerate', {
          params: { path: { document_id: documentId, number } },
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.document(documentId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.documentVersions(documentId) })
    },
  })
}

export function useDeleteDocument(owner: DocumentOwner) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (documentId: string) =>
      unwrap(
        api.DELETE('/api/documents/{document_id}', {
          params: { path: { document_id: documentId } },
        }),
      ),
    onSuccess: () => {
      // The owner-scoped key first (the exact list this tab is looking at), then the
      // wildcard (`queryKeys.documents()`, an empty-object filter that partially
      // matches every cached owner) as defense in depth -- the same belt-and-braces
      // pattern `useUpdateCustomer` already uses for its own single-record key plus
      // the list wildcard.
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents(owner) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents() })
    },
  })
}

/**
 * `GET /api/templates` answers a `TemplatePage` (`{items, next_cursor}`), the same
 * cursor-paginated shape every other list endpoint in this codebase uses -- never a
 * bare array. A caller wants `.data.items`, not `.data` itself.
 */
export function useTemplates() {
  return useQuery({
    queryKey: queryKeys.templates(),
    queryFn: () => unwrap(api.GET('/api/templates', { params: { query: {} } })),
  })
}

export function useTemplateDescription(templateId: string | null) {
  return useQuery({
    enabled: templateId !== null && templateId !== '',
    queryKey: queryKeys.templateDescription(templateId ?? ''),
    queryFn: () =>
      unwrap(
        api.GET('/api/templates/{template_id}/describe', {
          params: { path: { template_id: templateId as string } },
        }),
      ),
  })
}

export function useTemplatePreview() {
  return useMutation({
    mutationFn: (args: { templateId: string; variables: Record<string, unknown> }) =>
      unwrap(
        api.POST('/api/templates/{template_id}/preview', {
          params: { path: { template_id: args.templateId } },
          body: { variables: args.variables },
        }),
      ),
  })
}

/**
 * Downloads through the API, which is the only place authorisation exists on either
 * storage backend. The `Content-Disposition` the server sets is what names the file.
 */
export function downloadDocument(documentId: string, number?: number): Promise<void> {
  const search = number === undefined ? '' : `?number=${number}`
  return downloadFromApi(`/api/documents/${documentId}/download${search}`)
}

/**
 * What `DocumentsTab` and `VersionHistory` show for a failed `downloadDocument`. A 404
 * means one of three different things gone, each `NotFound` on the server
 * (`DocumentService`): the document itself (`_require`, entity `document` -- someone
 * else archived or deleted it after the list loaded), the version asked for
 * (`_resolve_version`, entity `document_version`), or the bytes stored under its key
 * (`storage.get`, entity `document_blob`). The server's own `detail` for any of them is
 * a log line (`document_blob <key> not found`) that must never reach the screen, the
 * same fact `InvoiceActions` and `InvoicePdfPreview` act on for the invoice pages
 * (REB-168), recognised here through the same `isMissingFile` rather than a second 404
 * check.
 *
 * The two halves of the sentence differ because the two facts differ: a lost
 * `document_blob` is a file this exact row still names correctly, gone from storage --
 * "not available" -- while a missing `document`/`document_version` means the row itself
 * is stale, describing something that no longer exists at all, so refreshing is the
 * honest next step rather than a retry. `problem.entity` is what `toProblem` carries
 * through unchanged from the server's problem document (`domain_error_handler` spreads
 * `NotFound`'s `entity`/`identifier` at the top level), so this reads it rather than
 * guessing from `fileName`. `fileName` is the name shown in the row the person clicked
 * -- the document's `title`, or that title with its version number -- so either
 * sentence names what they were after instead of the identifier the server logged. Any
 * other failure keeps the server's own `detail`, as every other action on these two
 * tabs does.
 */
export function documentDownloadErrorMessage(error: unknown, fileName: string): string {
  const problem = toProblem(error)
  if (!isMissingFile(problem)) return problem.detail
  return problem.entity === 'document_blob'
    ? `Il file «${fileName}» non è disponibile: il file archiviato non esiste più.`
    : `Il file «${fileName}» non esiste più: aggiorna la pagina.`
}
