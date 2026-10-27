/// <reference lib="webworker" />

/**
 * Reminders as notifications (REB-738): the `push` and `notificationclick` listeners, in
 * their own file as `sw/index.ts` asks of every feature. Nothing here touches a cache:
 * what the worker shows arrives in the push message and is shown, never stored.
 *
 * **The message is untrusted input.** It comes off the network, decrypted for this browser
 * alone, but an older server or a bug can send anything, so each field is checked and one
 * that is not a string is dropped; a message with no title shows nothing (a push the
 * browser is told to show and does not is a notice that the site is running in the
 * background). The decisions are plain functions of values so `pwa/sw-push.test.ts` holds
 * them without a worker.
 *
 * **Where a tap goes is the message's `url`, kept on this origin.** The server sends a path
 * under the space (`/acme/app/invoices/<id>`); anything that resolves to another origin is
 * ignored for the space's own list of reminders, so a message cannot send the person
 * elsewhere.
 */

/** What the server sends (`reminders/push.py`'s `PushMessage.payload`). */
export interface ReminderNotification {
  title: string
  body: string | undefined
  url: string | undefined
  tag: string | undefined
}

function text(value: unknown, max: number): string | undefined {
  return typeof value === 'string' && value.trim() !== '' ? value.slice(0, max) : undefined
}

/** The notification a message stands for, or `null` for one that shows nothing. */
export function readMessage(raw: unknown): ReminderNotification | null {
  if (raw === null || typeof raw !== 'object') return null
  const message = raw as Record<string, unknown>
  const title = text(message.title, 200)
  if (title === undefined) return null
  return {
    title,
    body: text(message.body, 400),
    url: text(message.url, 2048),
    tag: text(message.tag, 100),
  }
}

/** The options `showNotification` is handed. A tag replaces the notification already on
 *  screen with that tag, which is what a reminder that climbs wants (the severe one
 *  replaces the mild one, and `renotify` makes it sound again); a message without one
 *  stacks. */
export function notificationOptions(message: ReminderNotification, icon: string) {
  return {
    body: message.body,
    icon,
    ...(message.tag !== undefined ? { tag: message.tag, renotify: true } : {}),
    data: { url: message.url },
  }
}

/** Where a tap opens: the message's path or URL when it is on this origin, the space's
 *  list of reminders otherwise. `scope` is the worker's (`https://host/acme/app`). */
export function clickTarget(data: unknown, scope: string): string {
  const fallback = `${scope}/avvisi`
  const url = (data as { url?: unknown } | null)?.url
  if (typeof url !== 'string') return fallback
  try {
    const target = new URL(url, scope)
    return target.origin === new URL(scope).origin ? target.href : fallback
  } catch {
    return fallback
  }
}

export function registerPush(sw: ServiceWorkerGlobalScope, icon: string): void {
  sw.addEventListener('push', (event) => {
    let message: ReminderNotification | null = null
    try {
      message = readMessage(event.data?.json())
    } catch {
      // Not JSON: nothing to show.
    }
    if (message === null) return
    event.waitUntil(sw.registration.showNotification(message.title, notificationOptions(message, icon)))
  })

  sw.addEventListener('notificationclick', (event) => {
    event.notification.close()
    const target = clickTarget(event.notification.data, sw.registration.scope)
    event.waitUntil(openTarget(sw, target))
  })
}

/** A window of this space already open is taken to the record and focused, so a tap does
 *  not stack a tab per reminder; none open, or one the browser will not navigate (an
 *  uncontrolled one), opens a new one. */
async function openTarget(sw: ServiceWorkerGlobalScope, target: string): Promise<void> {
  const scope = sw.registration.scope
  const windows = await sw.clients.matchAll({ type: 'window', includeUncontrolled: true })
  const open = windows.find((client) => client.url.startsWith(scope))
  if (open) {
    try {
      const moved = await open.navigate(target)
      await (moved ?? open).focus()
      return
    } catch {
      // Fall through to a window of its own.
    }
  }
  await sw.clients.openWindow(target)
}
