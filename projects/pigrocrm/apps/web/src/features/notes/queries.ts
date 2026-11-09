import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

/** Wire shapes straight from the generated schema, like every other `queries.ts` here:
 *  `packages/core/src/pigrocrm/core/notes/schemas.py` is the one source. */
export type Note = components['schemas']['NoteRead']
export type NoteEntityType = Note['entity_type']
export type NoteSource = NonNullable<Note['source']>

/** The collection the URL spells for each entity type a note can hang on. */
const COLLECTIONS = { deal: 'deals', organization: 'organizations', person: 'people' } as const

export function useNotes(entityType: NoteEntityType, entityId: string) {
  return useQuery({
    queryKey: queryKeys.notes(entityType, entityId),
    queryFn: () =>
      unwrap(
        api.GET('/api/{collection}/{entity_id}/notes', {
          params: { path: { collection: COLLECTIONS[entityType], entity_id: entityId } },
        }),
      ),
  })
}

/** Every add, edit and delete is an entry of the parent's timeline, so the thread and the
 *  timeline go stale together. */
function invalidateNotes(client: QueryClient, entityType: NoteEntityType, entityId: string) {
  void client.invalidateQueries({ queryKey: queryKeys.notes(entityType, entityId) })
  void client.invalidateQueries({ queryKey: queryKeys.timeline(entityType, entityId) })
}

export function useAddNote(entityType: NoteEntityType, entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (text: string) =>
      unwrap(
        api.POST('/api/{collection}/{entity_id}/notes', {
          params: { path: { collection: COLLECTIONS[entityType], entity_id: entityId } },
          body: { text },
        }),
      ),
    onSuccess: () => invalidateNotes(queryClient, entityType, entityId),
  })
}

export function useUpdateNote(entityType: NoteEntityType, entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ noteId, text }: { noteId: string; text: string }) =>
      unwrap(
        api.PATCH('/api/notes/{note_id}', {
          params: { path: { note_id: noteId } },
          body: { text },
        }),
      ),
    onSuccess: () => invalidateNotes(queryClient, entityType, entityId),
  })
}

export function useDeleteNote(entityType: NoteEntityType, entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (noteId: string) =>
      unwrap(api.DELETE('/api/notes/{note_id}', { params: { path: { note_id: noteId } } })),
    onSuccess: () => invalidateNotes(queryClient, entityType, entityId),
  })
}
