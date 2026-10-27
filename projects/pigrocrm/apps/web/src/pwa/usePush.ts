import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useState } from 'react'
import { api, toProblem, unwrap } from '@/lib/api'
import { queryKeys } from '@/lib/query'
import { pushState, pushSupport, keyBytes, sameKey, type PushState } from './push'
import { isIosDevice, isRunningStandalone } from './useInstallPrompt'

/** An `Error` of ours carries its own sentence; anything else is the API's problem. */
function said(caught: unknown, fallback: string): string {
  if (caught instanceof Error) return caught.message
  return toProblem(caught).detail || fallback
}

function heldBy(subscription: PushSubscription | null) {
  return subscription
    ? { endpoint: subscription.endpoint, key: subscription.options.applicationServerKey }
    : null
}

function currentSupport() {
  return pushSupport({
    hasServiceWorker: 'serviceWorker' in navigator,
    hasPushManager: 'PushManager' in window,
    hasNotification: 'Notification' in window,
    isIos: isIosDevice(),
    isStandalone: isRunningStandalone(),
  })
}

function currentPermission(): NotificationPermission {
  return 'Notification' in window ? Notification.permission : 'default'
}

/** This browser's own subscription, or `null`: from the worker of the space this page is
 *  in (`navigator.serviceWorker.ready` is the registration whose scope holds the page). */
async function browserSubscription(): Promise<PushSubscription | null> {
  const registration = await navigator.serviceWorker.ready
  return registration.pushManager.getSubscription()
}

export interface PushControls {
  state: PushState
  /** A subscribe or an unsubscribe is under way. */
  busy: boolean
  /** The last attempt failed, in words for the person. */
  error: string | null
  subscribe(): Promise<void>
  unsubscribe(): Promise<void>
}

/**
 * Turns web push on and off for this browser (REB-738): the permission, the browser's
 * subscription, and the server's row for it, which is what a reminder is sent to.
 *
 * «On» is both halves: the browser holds a subscription **and** the server has it on file
 * for the person signed in. A subscription the server does not know (another person
 * signed in on this browser, a row the push service reported gone) reads as off, and
 * turning it on again registers the same subscription for this person.
 */
export function usePush(): PushControls {
  const queryClient = useQueryClient()
  const config = useQuery({
    queryKey: queryKeys.pushConfig,
    queryFn: () => unwrap(api.GET('/api/push/config')),
    // The key is the installation's, and changes only when an operator rotates it.
    staleTime: 5 * 60_000,
  })
  const mine = useQuery({
    queryKey: queryKeys.pushSubscriptions,
    queryFn: () => unwrap(api.GET('/api/push/subscriptions')),
  })
  const support = currentSupport()
  // What this browser holds: `undefined` until it has been asked, `null` for nothing.
  const [held, setHeld] = useState<{ endpoint: string; key: ArrayBuffer | null } | null | undefined>(undefined)
  const [permission, setPermission] = useState<NotificationPermission>(currentPermission)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (support !== 'supported') return
    let live = true
    browserSubscription()
      .then((subscription) => live && setHeld(heldBy(subscription)))
      .catch(() => live && setHeld(null))
    return () => {
      live = false
    }
  }, [support])

  const refresh = useCallback(
    () => queryClient.invalidateQueries({ queryKey: queryKeys.pushSubscriptions }),
    [queryClient],
  )

  const register = useMutation({
    mutationFn: async () => {
      const key = config.data?.public_key
      if (!key) throw new Error('Le notifiche push non sono attive su questa installazione.')
      const result = await Notification.requestPermission()
      setPermission(result)
      if (result !== 'granted') {
        throw new Error('Il permesso per le notifiche non è stato dato.')
      }
      const wanted = keyBytes(key)
      const registration = await navigator.serviceWorker.ready
      let subscription = await registration.pushManager.getSubscription()
      // One made with a key the server no longer has would make `subscribe()` throw.
      if (subscription && !sameKey(subscription.options.applicationServerKey, wanted)) {
        await subscription.unsubscribe()
        subscription = null
      }
      subscription ??= await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: wanted,
      })
      const json = subscription.toJSON()
      const p256dh = json.keys?.p256dh
      const auth = json.keys?.auth
      if (!json.endpoint || !p256dh || !auth) throw new Error('Il browser non ha dato la sottoscrizione.')
      await unwrap(api.POST('/api/push/subscribe', { body: { endpoint: json.endpoint, p256dh, auth } }))
      setHeld(heldBy(subscription))
    },
    onSuccess: refresh,
  })

  const remove = useMutation({
    mutationFn: async () => {
      const subscription = await browserSubscription()
      if (!subscription) return
      // The server's row first: the browser's own subscription is what the endpoint
      // belongs to, and the server only needs the endpoint to forget it.
      await unwrap(
        api.POST('/api/push/unsubscribe', { body: { endpoint: subscription.endpoint } }),
      )
      await subscription.unsubscribe()
      setHeld(null)
    },
    onSuccess: refresh,
  })

  const subscribe = useCallback(async () => {
    setError(null)
    try {
      await register.mutateAsync()
    } catch (caught) {
      setError(said(caught, 'Non sono riuscito ad attivare le notifiche.'))
    }
  }, [register])

  const unsubscribe = useCallback(async () => {
    setError(null)
    try {
      await remove.mutateAsync()
    } catch (caught) {
      setError(said(caught, 'Non sono riuscito a disattivare le notifiche.'))
    }
  }, [remove])

  // On means the browser holds a subscription made with the key the server holds now (one
  // made with a key an operator has since rotated is dead: the push service refuses it, so
  // it reads as off and pressing «Attiva» makes a new one) and the server has it on file.
  const serverKey = config.data?.public_key
  const current = !!held && !!serverKey && sameKey(held.key, keyBytes(serverKey))
  const known = !!held && (mine.data ?? []).some((row) => row.endpoint === held.endpoint)
  const loading =
    config.isPending || mine.isPending || (support === 'supported' && held === undefined)

  return {
    state: pushState({
      loading,
      serverEnabled: config.data?.enabled === true,
      support,
      permission,
      subscribed: current && known,
    }),
    busy: register.isPending || remove.isPending,
    error,
    subscribe,
    unsubscribe,
  }
}
