import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, fetchWithRefresh, toProblem, unwrap, type ProblemDetail } from '@/lib/api'
import type { components } from '@/lib/api-types'
import type { EntityType } from '@/lib/schema'
import { queryKeys } from '@/lib/query'

// -- Field definitions --------------------------------------------------------

export type FieldDefinitionRecord = components['schemas']['FieldDefinitionRead']
type FieldDefinitionCreateBody = components['schemas']['FieldDefinitionCreate']
type FieldDefinitionUpdateBody = components['schemas']['FieldDefinitionUpdate']

/**
 * `include_archived: true`, always -- unlike `useEntitySchema` (lib/schema.ts),
 * which only needs what Customers/Persons/Deals currently render and excludes
 * archived fields by design, this admin screen's job is to also show what has
 * been archived, with a way back (`FieldsPanel`'s "Ripristina").
 */
export function useFieldDefinitions(entityType: EntityType) {
  return useQuery({
    queryKey: queryKeys.fields(entityType),
    queryFn: () =>
      unwrap(
        api.GET('/api/field-definitions', {
          params: { query: { entity_type: entityType, include_archived: true } },
        }),
      ),
  })
}

/** Invalidates both this admin list and `queryKeys.schema` -- the second is
 *  what makes a field created/archived/restored/edited here show up (or
 *  disappear) on Customers/Persons/Deals immediately. Keyed off the response's
 *  own `entity_type`, never a caller-supplied one. */
function invalidateFieldQueries(queryClient: QueryClient, entityType: string) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.fields(entityType) })
  void queryClient.invalidateQueries({ queryKey: queryKeys.schema(entityType) })
}

export function useCreateFieldDefinition() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.POST('/api/field-definitions', { body: body as unknown as FieldDefinitionCreateBody }),
      ),
    onSuccess: (field) => invalidateFieldQueries(queryClient, field.entity_type),
  })
}

/** `FieldDefinitionUpdate` accepts `label`/`options`/`required`/`position` --
 *  `key`/`field_type`/`entity_type` are absent from the schema itself (identity,
 *  not a label; see that schema's own docstring), so there is nothing to guard
 *  client-side: the backend already has no spelling that would change them. */
export function useUpdateFieldDefinition() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ fieldId, body }: { fieldId: string; body: Record<string, unknown> }) =>
      unwrap(
        api.PATCH('/api/field-definitions/{field_id}', {
          params: { path: { field_id: fieldId } },
          body: body as unknown as FieldDefinitionUpdateBody,
        }),
      ),
    onSuccess: (field) => invalidateFieldQueries(queryClient, field.entity_type),
  })
}

export function useArchiveFieldDefinition() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (fieldId: string) =>
      unwrap(
        api.POST('/api/field-definitions/{field_id}/archive', {
          params: { path: { field_id: fieldId } },
        }),
      ),
    onSuccess: (field) => invalidateFieldQueries(queryClient, field.entity_type),
  })
}

/** Symmetric to `useArchiveFieldDefinition` -- without this, archiving would be
 *  a one-way door in the UI even though the data model treats it as
 *  reversible. See `FieldsPanel.tsx`'s "Ripristina". */
export function useUnarchiveFieldDefinition() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (fieldId: string) =>
      unwrap(
        api.POST('/api/field-definitions/{field_id}/unarchive', {
          params: { path: { field_id: fieldId } },
        }),
      ),
    onSuccess: (field) => invalidateFieldQueries(queryClient, field.entity_type),
  })
}

// -- Pipeline stages ------------------------------------------------------------
//
// `Stage`/`useStages` already live in features/deals/queries.ts (the Kanban
// board's own read side, open to every role -- `PipelineService.list` applies
// no role check). `PipelinePanel` imports that hook directly; only the
// admin-only writes the board has no use for live here.

type StageCreateBody = components['schemas']['PipelineStageCreate']

export function useCreateStage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/pipeline-stages', { body: body as unknown as StageCreateBody })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.stages }),
  })
}

/** `DELETE /api/pipeline-stages/{id}` is a hard delete (`PipelineRepository.
 *  delete` calls `session.delete`, not a soft-delete flag) that refuses with a
 *  409 naming how many deals -- archived included -- still point at the stage.
 *  This hook does not reshape that; `PipelinePanel.remove` reads `toProblem`
 *  and its `deals` count for what the user sees. */
export function useDeleteStage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (stageId: string) =>
      unwrap(
        api.DELETE('/api/pipeline-stages/{stage_id}', { params: { path: { stage_id: stageId } } }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.stages }),
  })
}

export function useSeedStages() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => unwrap(api.POST('/api/pipeline-stages/seed')),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.stages }),
  })
}

// -- Users ------------------------------------------------------------------

export type UserRecord = components['schemas']['UserRead']
type InvitationCreateBody = components['schemas']['InvitationCreate']
type UserUpdateBody = components['schemas']['UserUpdate']

export function useUsers() {
  return useQuery({
    queryKey: queryKeys.users,
    queryFn: () => unwrap(api.GET('/api/users')),
  })
}

// -- Invitations (spec 2026-09-17, REB-290's routes; the panel's REB-291 callers) ----

export type InvitationRecord = components['schemas']['InvitationRead']

/** `POST /api/users` with a typed password is dead here: the mail's link, not a
 *  hallway handoff, is how a space gains a person now (spec §0). The API route itself
 *  stays until the spec's removal is scheduled (REB-290 left it deliberately); this
 *  panel simply no longer calls it. */
export function useInviteUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      email: string
      nome: string | null
      ruolo: string
      aziende?: string[] | null
    }) =>
      unwrap(
        api.POST('/api/users/invites', {
          body: body as unknown as InvitationCreateBody,
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.invites }),
  })
}

/** «Inviti in attesa»: open and not yet expired only (the server's predicate, spec §3),
 *  newest first. A dead invitation is not shown as waiting; a resend or a fresh invite
 *  revives an expired one. */
export function usePendingInvites() {
  return useQuery({
    queryKey: queryKeys.invites,
    queryFn: () => unwrap(api.GET('/api/users/invites')),
  })
}

/** A fresh token on the same row, a week measured from now, the old link dead at once
 *  (spec §1). Invalidates the list so the row's new `expires_at` is what shows. */
export function useResendInvite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (invitationId: string) =>
      unwrap(
        api.POST('/api/users/invites/{invitation_id}/resend', {
          params: { path: { invitation_id: invitationId } },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.invites }),
  })
}

/** 204 on success, and the row is gone from the pending list (the server keeps it with
 *  `revoked_at` set; the list's predicate does not show it). 404 for a row that already
 *  ended, which the toast's own sentence names. */
export function useRevokeInvite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (invitationId: string) =>
      unwrap(
        api.DELETE('/api/users/invites/{invitation_id}', {
          params: { path: { invitation_id: invitationId } },
        }),
      ),
    // The 204 carries no body, so the row to drop is the mutation's own argument.
    onSuccess: (_void, invitationId) => {
      queryClient.setQueryData<InvitationRecord[]>(queryKeys.invites, (previous) =>
        previous?.filter((invite) => invite.id !== invitationId),
      )
    },
  })
}

/**
 * Used for both the active/inactive toggle and the role select in
 * `UsersPanel`. Writes the server's own response straight into the cached
 * list (`setQueryData`) instead of invalidating and trusting a second round
 * trip to succeed.
 *
 * That second round trip is exactly what used to lie: deactivating your own
 * account kills your session as a side effect of the PATCH succeeding, so the
 * background refetch this hook used to trigger came back 401 -- and
 * `DataTable` deliberately keeps showing the last good page on a background
 * error (see its own docstring), which left "Attivo" on screen next to a
 * toast that had already said "Utente disattivato". The PATCH response is the
 * authoritative answer to "did this work", already in hand the moment
 * `onSuccess` runs; a second request that can independently fail for
 * unrelated reasons was never necessary to trust it.
 */
export function useUpdateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: Record<string, unknown> }) =>
      unwrap(
        api.PATCH('/api/users/{user_id}', {
          params: { path: { user_id: userId } },
          body: body as unknown as UserUpdateBody,
        }),
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData<UserRecord[]>(queryKeys.users, (previous) =>
        previous?.map((user) => (user.id === updated.id ? updated : user)),
      )
    },
  })
}

// -- Me (own profile) ---------------------------------------------------------

type MeUpdateBody = components['schemas']['MeUpdate']

/**
 * `PATCH /api/auth/me`, not `/api/users/{id}`: the one write on this file that needs
 * no admin role, since `UserService.update_own_digest` reads the caller's own id off
 * the session rather than a path parameter -- see `ProfilePanel`. Invalidates
 * `queryKeys.me`, the same key `AuthProvider`'s own `useQuery` populates, so
 * `useAuth().user` -- and this switch -- reflect the server on the next render.
 */
export function useUpdateMe() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: MeUpdateBody) => unwrap(api.PATCH('/api/auth/me', { body })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.me }),
  })
}

// -- Templates (admin writes) -------------------------------------------------

// `Template`/`useTemplates` already live in features/documents/queries.ts: the
// new-from-template dialog reads them and is open to every role, since
// `TemplateService.list` applies no role check. Only the admin-only writes,
// which that dialog has no use for, live here.

type TemplateCreateBody = components['schemas']['TemplateCreate']
type TemplateUpdateBody = components['schemas']['TemplateUpdate']

function invalidateTemplates(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.templates() })
}

export function useCreateTemplate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/templates', { body: body as unknown as TemplateCreateBody })),
    onSuccess: () => invalidateTemplates(queryClient),
  })
}

export function useUpdateTemplate(templateId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PATCH('/api/templates/{template_id}', {
          params: { path: { template_id: templateId } },
          body: body as unknown as TemplateUpdateBody,
        }),
      ),
    onSuccess: () => invalidateTemplates(queryClient),
  })
}

/** `DELETE /api/templates/{id}` deactivates rather than deleting: it flips
 *  `attivo` and returns the row. Paired with `useActivateTemplate` deliberately
 *  -- an administrator who deactivates a template by mistake must have a way
 *  back, the same lesson `useUnarchiveFieldDefinition` exists for. A one-way
 *  door on a boolean column is a defect, not a simplification. */
export function useDeactivateTemplate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (templateId: string) =>
      unwrap(
        api.DELETE('/api/templates/{template_id}', {
          params: { path: { template_id: templateId } },
        }),
      ),
    onSuccess: () => invalidateTemplates(queryClient),
  })
}

export function useActivateTemplate() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (templateId: string) =>
      unwrap(
        api.POST('/api/templates/{template_id}/activate', {
          params: { path: { template_id: templateId } },
        }),
      ),
    onSuccess: () => invalidateTemplates(queryClient),
  })
}

// -- Aziende ------------------------------------------------------------------

export type LegalEntityRecord = components['schemas']['LegalEntityRead']
type LegalEntityUpsertBody = components['schemas']['LegalEntityUpsert']
type LegalEntityCreateBody = components['schemas']['LegalEntityCreate']

/**
 * The aziende of the space, the default first (REB-617, spec 2026-10-03 §5). Every
 * provisioned space has one, so an empty list is a space between signup and its first
 * boot and not a state the page designs for.
 */
export function useLegalEntities() {
  return useQuery({
    queryKey: queryKeys.aziende,
    queryFn: () => unwrap(api.GET('/api/aziende')),
  })
}

/** `PUT`, not `PATCH`: `LegalEntityUpsert` is one shape for every write, because a
 *  write is always a whole row. Slice 3 builds FatturaPA on this row, so a partial
 *  save leaving `partita_iva` empty would surface much later as an invalid invoice.
 *  The answer is the row as saved: written into the list at once, so the panel's row
 *  moves with the save and not one round trip later (REB-622: the form compares its
 *  version with the row's, and a prop that lagged the save read as another admin's
 *  older row), then the list is invalidated, which is where every reader gets it from. */
export function useSaveLegalEntity(aziendaId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(
        api.PUT('/api/aziende/{azienda_id}', {
          params: { path: { azienda_id: aziendaId } },
          body: body as unknown as LegalEntityUpsertBody,
        }),
      ),
    onSuccess: (saved) => {
      queryClient.setQueryData<LegalEntityRecord[]>(queryKeys.aziende, (previous) =>
        previous?.map((azienda) => (azienda.id === saved.id ? saved : azienda)),
      )
      void queryClient.invalidateQueries({ queryKey: queryKeys.aziende })
    },
  })
}

/**
 * A second azienda, born with its fiscal profile in the one request `POST /api/aziende`
 * takes (REB-632, spec 2026-10-03 §3, §9 milestone 5). Invalidates the list, which is
 * also what the sidebar's provider reads, so the selector appears the moment the list
 * has two.
 */
export function useCreateLegalEntity() {
  const queryClient = useQueryClient()
  return useMutation({
    // The same cast `useSaveLegalEntity` makes: the form sends the keys it asks for and the
    // server defaults the rest, while the generated type lists them all as present.
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.POST('/api/aziende', { body: body as unknown as LegalEntityCreateBody })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.aziende })
      // The Home's «tutte» is a different answer from the second azienda on (no
      // space-wide estimate, the concentration per azienda), and its key does not
      // change: dropped here so a sixty-second `staleTime` cannot keep the old shape.
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      void queryClient.invalidateQueries({ queryKey: ['fiscal-estimate'] })
    },
  })
}

// -- The logo and the signature of an azienda (REB-629) --------------------------------

export type LegalEntityImageSlot = 'logo' | 'firma'

/**
 * The image's bytes, as a Blob the block turns into an object URL, or `null` when the
 * azienda has none. Asked only when the row says there is one (`present`): the route
 * answers 404 otherwise, and a 404 is a fact the row already carries.
 *
 * Outside the typed client for the response type, through `fetchWithRefresh` for the
 * session and the tenant prefix, like a document's download (`features/documents`).
 */
export function useLegalEntityImage(aziendaId: string, slot: LegalEntityImageSlot, present: boolean) {
  return useQuery({
    queryKey: queryKeys.aziendaImage(aziendaId, slot),
    enabled: present,
    queryFn: async (): Promise<Blob | null> => {
      const response = await fetchWithRefresh(`/api/aziende/${aziendaId}/${slot}`)
      if (response.status === 404) return null
      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => null)
        throw toProblem(payload, response.status)
      }
      return response.blob()
    },
  })
}

/** Multipart, so outside the typed client, the same exception `putVersion` documents. */
async function putLegalEntityImage(
  aziendaId: string,
  slot: LegalEntityImageSlot,
  file: File,
): Promise<LegalEntityRecord> {
  const body = new FormData()
  body.append('file', file)
  const response = await fetchWithRefresh(`/api/aziende/${aziendaId}/${slot}`, {
    method: 'PUT',
    body,
  })
  // nginx answers a body over its own limit with an HTML 413 before the service can
  // say anything: the same sentence the service would have said.
  if (response.status === 413) throw toProblem({ detail: IMAGE_TOO_LARGE }, 413)
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) throw toProblem(payload, response.status)
  return payload as LegalEntityRecord
}

/** The service's own limit and its own sentence (`LegalEntityAssets._check`), repeated here
 *  so a file the browser already knows is too large is refused before it travels. */
export const IMAGE_MAX_BYTES = 1024 * 1024
export const IMAGE_TOO_LARGE = 'il file supera 1024 KiB'

export function imageTooLarge(file: File): ProblemDetail | null {
  return file.size > IMAGE_MAX_BYTES ? toProblem({ detail: IMAGE_TOO_LARGE }, 413) : null
}

export function useUploadLegalEntityImage(aziendaId: string, slot: LegalEntityImageSlot) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => putLegalEntityImage(aziendaId, slot, file),
    // The row (its key changed) and the bytes under the same prefix.
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.aziende }),
  })
}

export function useRemoveLegalEntityImage(aziendaId: string, slot: LegalEntityImageSlot) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      unwrap(
        slot === 'logo'
          ? api.DELETE('/api/aziende/{azienda_id}/logo', {
              params: { path: { azienda_id: aziendaId } },
            })
          : api.DELETE('/api/aziende/{azienda_id}/firma', {
              params: { path: { azienda_id: aziendaId } },
            }),
      ),
    onSuccess: () => {
      // The bytes query is disabled once the row has no key, so its cached Blob would
      // otherwise outlive the removal and flash on the next upload: dropped outright.
      queryClient.removeQueries({ queryKey: queryKeys.aziendaImage(aziendaId, slot) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.aziende })
    },
  })
}
