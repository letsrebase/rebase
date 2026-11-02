const RELOAD_KEY = 'pigrocrm.chunk-reload-at'
const RELOAD_WINDOW_MS = 60_000

/** Whether an error is a lazy route chunk that no longer exists: after a deploy the old
 *  hashed files are gone, and nginx answers the missing `.js` with the SPA's HTML. */
export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error)
  return /dynamically imported module|importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i.test(
    message,
  )
}

/** Reloads the page to pick up the new build, at most once a minute: a chunk that is
 *  missing for another reason must not become a reload loop. Returns whether it reloaded. */
export function reloadOncePerMinute(
  storage: Pick<Storage, 'getItem' | 'setItem'> | null = sessionStorageOrNull(),
  now: number = Date.now(),
  reload: () => void = () => window.location.reload(),
): boolean {
  // No storage (blocked, or a privacy mode that throws) means no way to rate-limit, so no
  // reload: showing the error screen is better than a possible loop.
  if (storage === null) return false
  try {
    const last = Number(storage.getItem(RELOAD_KEY) ?? 0)
    if (now - last < RELOAD_WINDOW_MS) return false
    storage.setItem(RELOAD_KEY, String(now))
  } catch {
    return false
  }
  reload()
  return true
}

function sessionStorageOrNull(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}
