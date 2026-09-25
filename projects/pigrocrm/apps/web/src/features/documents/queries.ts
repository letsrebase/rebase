import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchWithRefresh, toProblem, unwrap } from '@/lib/api'
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
export type EmitterProfile = components['schemas']['EmitterProfileRead']

export type OfferState = 'bozza' | 'inviata' | 'accettata' | 'rifiutata'

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
  bozza: ['inviata'],
  inviata: ['accettata', 'rifiutata', 'bozza'],
  accettata: [],
  rifiutata: [],
}

export const OFFER_STATE_LABELS: Record<OfferState, string> = {
  bozza: 'Bozza',
  inviata: 'Inviata',
  accettata: 'Accettata',
  rifiutata: 'Rifiutata',
}

/**
 * The tone each offer state reads as in a `StatusPill` (design spec §4), beside the
 * labels and total over `OfferState` for the same reason `OFFER_TRANSITIONS` is -- which
 * means the compile breaks when that hand-written union is widened, not when the server
 * grows a state.
 *
 * `accettata` is `ink`, not gold: an accepted offer is the settled, ordinary outcome --
 * the thing every offer is trying to become -- and gold in this product means "waiting
 * on somebody", which an accepted offer no longer is. `inviata` is also `ink` because
 * it is a real, correct state and a table of sent offers must not read as a table of
 * warnings; `bozza` claims nothing yet, and `rifiutata` is the one that has gone the
 * wrong way.
 */
export const OFFER_STATE_TONE: Record<OfferState, StatusTone> = {
  bozza: 'muted',
  inviata: 'ink',
  accettata: 'ink',
  rifiutata: 'danger',
}

export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  offerta: 'Offerta',
  contratto: 'Contratto',
  verbale: 'Verbale',
  documento: 'Documento',
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
      titolo: string
      variabili: Record<string, unknown>
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
async function putVersion(documentId: string, file: File): Promise<DocumentVersion> {
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
    onSuccess: (_data, variables) => {
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
    mutationFn: (stato: OfferState) =>
      unwrap(
        api.POST('/api/documents/{document_id}/status', {
          params: { path: { document_id: documentId } },
          body: { stato },
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
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
}

export function useRegenerateVersion(documentId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (numero: number) =>
      unwrap(
        api.POST('/api/documents/{document_id}/versions/{numero}/regenerate', {
          params: { path: { document_id: documentId, numero } },
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
    mutationFn: (args: { templateId: string; variabili: Record<string, unknown> }) =>
      unwrap(
        api.POST('/api/templates/{template_id}/preview', {
          params: { path: { template_id: args.templateId } },
          body: { variabili: args.variabili },
        }),
      ),
  })
}

/**
 * Downloads through the API, which is the only place authorisation exists on either
 * storage backend. The `Content-Disposition` the server sets is what names the file;
 * this only has to hand the blob to the browser and release the object URL, or the
 * page leaks one per download for as long as it stays open.
 */
export async function downloadDocument(documentId: string, numero?: number): Promise<void> {
  const search = numero === undefined ? '' : `?numero=${numero}`
  // `fetchWithRefresh`, not a bare `fetch`: this call is outside the typed client
  // because of the *response type*, and that used to silently also put it outside the
  // session handling every other request gets -- so a download attempted more than
  // fifteen minutes after the last request failed with «Autenticazione richiesta»
  // while a valid refresh cookie sat in the jar. See `lib/api.ts`.
  const response = await fetchWithRefresh(`/api/documents/${documentId}/download${search}`)
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    throw toProblem(payload, response.status)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  // Empty `download` keeps the server's own Content-Disposition filename, which is
  // already slugified server-side; naming it here would re-derive a name the server
  // already decided.
  anchor.download = ''
  // Snyk Code javascript/DOMXSS here is a false positive: the href is a blob: URL made
  // just above, never a script URL, and `download` makes the click save the file
  // whatever its type, so nothing it holds is ever rendered.
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
