import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'

export type Mailbox = components['schemas']['MailboxRead']
export type MailboxCreate = components['schemas']['ImapMailboxCreate']

/** `GET /api/mailboxes`: the caller's own and, for an administrator, the space's. */
export function useMailboxes() {
  return useQuery({
    queryKey: queryKeys.mailboxes,
    queryFn: () => unwrap(api.GET('/api/mailboxes')),
  })
}

/** Every write answers with the mailbox as it now is, and a refusal can have changed a row's
 *  health (a refused password is committed before the error leaves), so both refetch the list. */
function useMailboxMutation<Variables>(run: (variables: Variables) => Promise<Mailbox>) {
  const queryClient = useQueryClient()
  const refresh = () => void queryClient.invalidateQueries({ queryKey: queryKeys.mailboxes })
  return useMutation({ mutationFn: run, onSuccess: refresh, onError: refresh })
}

export function useConnectMailbox() {
  return useMailboxMutation<MailboxCreate>((body) => unwrap(api.POST('/api/mailboxes/imap', { body })))
}

export function useChangeMailboxPassword() {
  return useMailboxMutation<{ id: string; password: string }>(({ id, password }) =>
    unwrap(
      api.PUT('/api/mailboxes/{mailbox_id}/password', {
        params: { path: { mailbox_id: id } },
        body: { password },
      }),
    ),
  )
}

export function useDisconnectMailbox() {
  return useMailboxMutation<string>((id) =>
    unwrap(api.DELETE('/api/mailboxes/{mailbox_id}', { params: { path: { mailbox_id: id } } })),
  )
}
