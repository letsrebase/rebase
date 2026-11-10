import { api } from '@/lib/api'

/** The longest a logout waits for this to finish: leaving matters more than forgetting. */
const WAIT_MS = 3000

/**
 * Turns push off for this browser when its person signs out (REB-738). A subscription is a
 * channel to **this person's** reminders (customer names, invoice titles, amounts), and a
 * signed-out screen is one nobody should keep receiving them on; the next person who signs
 * in and turns push on takes the browser over, but until then it would go on. The server's
 * row goes first, while the session still authorises it, then the browser's own
 * subscription. Best effort and bounded: it never throws and never holds a logout longer
 * than `WAIT_MS`, so a network that is down at that moment leaves the subscription in place
 * and the person turns it off from Notifiche the next time they sign in.
 */
export async function leavePush(): Promise<void> {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator) || !('PushManager' in window)) return
  const work = (async () => {
    // `getRegistration`, not `ready`: with no worker it answers `undefined` at once.
    const registration = await navigator.serviceWorker.getRegistration()
    const subscription = await registration?.pushManager.getSubscription()
    if (!subscription) return
    await api.POST('/api/push/unsubscribe', { body: { endpoint: subscription.endpoint } })
    await subscription.unsubscribe()
  })().catch(() => undefined)
  await Promise.race([work, new Promise<void>((resolve) => setTimeout(resolve, WAIT_MS))])
}
