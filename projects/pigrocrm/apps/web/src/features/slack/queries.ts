import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { tenantPrefix } from '@/lib/tenant'

export type SlackHealth = components['schemas']['SlackHealth']
export type SlackMember = components['schemas']['SlackMemberRead']
export type SlackWorkspace = components['schemas']['SlackWorkspaceRead']
export type SlackChannel = components['schemas']['SlackChannelRead']
export type SlackMine = components['schemas']['SlackMine']

/**
 * Where the Slack install starts: a plain navigation, never a fetch, because it leaves for
 * Slack. Named here because two places offer it, the settings panel and the «Riprova» under
 * a consent that came back with no session. Under a space the API answers at
 * `/<slug>/api/...` and the session cookie is scoped to that prefix (`DRIVE_OAUTH_START`).
 */
export const SLACK_OAUTH_START = `${tenantPrefix}/api/slack/oauth/start`

export const slackKeys = {
  health: ['slack', 'health'] as const,
  mine: ['slack', 'mine'] as const,
  channels: ['slack', 'channels'] as const,
}

/**
 * `GET /api/slack/account` answers 200 on an installation with no Slack app
 * (`routers/slack.py`), so this query is in `isError` only when the request failed:
 * «Slack non è configurato» arrives as data, `configured: false`, and is a screen.
 */
export function useSlackHealth() {
  return useQuery({
    queryKey: slackKeys.health,
    queryFn: () => unwrap(api.GET('/api/slack/account')),
  })
}

/** What one person is told about their own Slack, for the Notifiche page. Anyone's. */
export function useSlackMine() {
  return useQuery({
    queryKey: slackKeys.mine,
    queryFn: () => unwrap(api.GET('/api/slack/me')),
  })
}

/** The channels the picker offers, read from Slack only when the picker is open. */
export function useSlackChannels(enabled: boolean) {
  return useQuery({
    queryKey: slackKeys.channels,
    queryFn: () => unwrap(api.GET('/api/slack/channels')),
    enabled,
    // Slack's own list, which changes outside the CRM: never served from an earlier look.
    staleTime: 0,
    gcTime: 0,
  })
}

/** Every write but the disconnect answers with the whole page's state, which is written
 *  into the cache as it comes, so the list never flickers back to what it was. */
function useHealthMutation<Variables>(mutate: (variables: Variables) => Promise<SlackHealth>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: mutate,
    onSuccess: (health) => {
      queryClient.setQueryData(slackKeys.health, health)
      void queryClient.invalidateQueries({ queryKey: slackKeys.mine })
    },
    // A refusal can have changed the row: Slack saying the token is dead is committed as
    // `revoked` before the error leaves, and the page must show the reconnect and not an
    // active workspace.
    onError: () => void queryClient.invalidateQueries({ queryKey: slackKeys.health }),
  })
}

export function useRematchSlack() {
  return useHealthMutation<void>(() => unwrap(api.POST('/api/slack/account/rematch')))
}

export function useLinkSlackUser() {
  return useHealthMutation<{ userId: string; email: string }>(({ userId, email }) =>
    unwrap(
      api.PUT('/api/slack/users/{user_id}/link', {
        params: { path: { user_id: userId } },
        body: { email },
      }),
    ),
  )
}

export function useUnlinkSlackUser() {
  return useHealthMutation<string>((userId) =>
    unwrap(
      api.DELETE('/api/slack/users/{user_id}/link', { params: { path: { user_id: userId } } }),
    ),
  )
}

export function useSetSlackChannel() {
  return useHealthMutation<string | null>((channelId) =>
    unwrap(api.PUT('/api/slack/channel', { body: { channel_id: channelId } })),
  )
}

export function useDisconnectSlack() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      await unwrap(api.DELETE('/api/slack/account'))
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: slackKeys.health })
      void queryClient.invalidateQueries({ queryKey: slackKeys.mine })
    },
  })
}
