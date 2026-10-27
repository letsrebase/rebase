/**
 * What the Notifiche section says about web push, as functions of plain values (REB-738).
 * The browser and API wiring is `usePush.ts`; the part that shows a notification is the
 * service worker's (`sw/push.ts`).
 */

export type PushSupport = 'unsupported' | 'ios-needs-install' | 'supported'

export interface PushSupportInputs {
  hasServiceWorker: boolean
  hasPushManager: boolean
  hasNotification: boolean
  isIos: boolean
  isStandalone: boolean
}

/**
 * Whether this browser can subscribe now, and why not when it cannot. iOS Safari exposes
 * `PushManager` before the page is installed to the Home Screen, but `subscribe()` only
 * succeeds once it is: that is told ahead of the call, from what the page already knows,
 * and not discovered from a failed `subscribe()` after the person has pressed the button.
 */
export function pushSupport(inputs: PushSupportInputs): PushSupport {
  if (!inputs.hasServiceWorker || !inputs.hasPushManager || !inputs.hasNotification) {
    return 'unsupported'
  }
  if (inputs.isIos && !inputs.isStandalone) return 'ios-needs-install'
  return 'supported'
}

/** What the section shows, in the order the reasons apply. */
export type PushState =
  | 'loading'
  /** This installation has no VAPID key: the server cannot send push at all. */
  | 'unavailable'
  | 'unsupported'
  | 'ios-needs-install'
  /** The person blocked notifications for this site in the browser. */
  | 'denied'
  | 'off'
  | 'on'

export interface PushStateInputs {
  loading: boolean
  /** `GET /api/push/config`'s `enabled`. */
  serverEnabled: boolean
  support: PushSupport
  permission: NotificationPermission
  /** This browser has a subscription and the server has it on file for this person. */
  subscribed: boolean
}

export function pushState({
  loading,
  serverEnabled,
  support,
  permission,
  subscribed,
}: PushStateInputs): PushState {
  if (loading) return 'loading'
  if (!serverEnabled) return 'unavailable'
  if (support !== 'supported') return support
  if (subscribed) return 'on'
  return permission === 'denied' ? 'denied' : 'off'
}

/** The key as `PushManager.subscribe` wants it: the bytes of the unpadded base64url the
 *  server hands out. Backed by a plain `ArrayBuffer`, which is what `BufferSource` asks. */
export function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = base64url + '='.repeat((4 - (base64url.length % 4)) % 4)
  const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'))
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i)
  return bytes
}

/** Whether a subscription was made with this key: one made with another, after the
 *  server's key was changed, is dropped and made again, or `subscribe()` throws. */
export function sameKey(existing: ArrayBuffer | null, wanted: Uint8Array): boolean {
  if (existing === null) return false
  const have = new Uint8Array(existing)
  return have.length === wanted.length && have.every((byte, i) => byte === wanted[i])
}
