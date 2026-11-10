/**
 * The wizard's draft: what a person had answered and where they were, kept in the
 * browser so that leaving is not starting over.
 *
 * It exists because of what PostHog showed on 2026-09-14: a person on an iPhone
 * answered three questions in three minutes, reached the CV step, opened the file
 * picker and lost the page; they came back twice, hours apart, found the first
 * question again and left in five seconds. `localStorage` rather than
 * `sessionStorage` for that reason -- the return is in a new tab, the next morning --
 * with a life of seven days, after which a half-filled form is a form nobody wants
 * back. The key is per wizard, so a company's answers never surface in the freelance
 * form.
 *
 * A `File` cannot be stored: the CV stays out, and since the step is optional the
 * review shows «—» for it and the person picks it again if they want to. A storage
 * that refuses (private mode, quota, a browser with it off) refuses quietly: the form
 * still works, it just forgets.
 *
 * A wizard whose key was renamed (`rebase.wizard.azienda` became `rebase.wizard.company`
 * in the 2026-11 rename) passes the old key as `previousKey`: a draft is read from it for
 * 30 days (see `lib/legacy.ts`), moved to the new key, and the old one dropped.
 */

import { readWithFallback } from '@/lib/legacy'

export const DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000

const VERSION = 1

interface Stored {
  v: number
  savedAt: number
  index: number
  value: Record<string, unknown>
}

export interface Draft<T> {
  value: Partial<T>
  index: number
}

function storage(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function storable(value: unknown): boolean {
  return !(typeof Blob !== 'undefined' && value instanceof Blob)
}

function blank(value: Record<string, unknown>): boolean {
  return Object.values(value).every(
    (item) => item === '' || item === null || item === undefined || (Array.isArray(item) && item.length === 0),
  )
}

/** The answers of a draft saved before the 2026-11 rename carry the Italian field names
 *  (`nome`, `remoto`: `ibrido`, ...): each is given its English name, and an answer whose
 *  name nobody knows is dropped rather than spread into the form and sent. */
export interface DraftRenames {
  /** The current field names: the draft's keys not in it and not in `fields` are dropped. */
  known: readonly string[]
  /** Old field name to the current one. */
  fields: Record<string, string>
}

/** The value of an option that was Italian in a draft's `remote` answer. */
const REMOTE_OPTIONS: Record<string, string> = { 'remoto': 'remote', 'ibrido': 'hybrid', 'in_sede': 'on_site' }

function translateDraft(value: Record<string, unknown>, renames: DraftRenames): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [name, item] of Object.entries(value)) {
    const current = renames.known.includes(name) ? name : renames.fields[name]
    if (!current) continue
    // A current answer wins over an old one of the same field.
    if (current !== name && current in value) continue
    out[current] = current === 'remote' && typeof item === 'string' ? (REMOTE_OPTIONS[item] ?? item) : item
  }
  return out
}

/** The draft under `key` (or, for 30 days, under the `previousKey` it had before the
 *  rename), or `null` when there is none, it expired, or it cannot be read. */
export function loadDraft<T>(key: string, previousKey?: string, renames?: DraftRenames): Draft<T> | null {
  const raw = previousKey ? readWithFallback(storage(), key, previousKey) : storage()?.getItem(key)
  if (!raw) return null
  try {
    const stored = JSON.parse(raw) as Partial<Stored>
    if (
      stored.v !== VERSION ||
      typeof stored.savedAt !== 'number' ||
      typeof stored.index !== 'number' ||
      typeof stored.value !== 'object' ||
      stored.value === null
    ) {
      return null
    }
    if (Date.now() - stored.savedAt > DRAFT_TTL_MS) {
      clearDraft(key, previousKey)
      return null
    }
    return { value: (renames ? translateDraft(stored.value, renames) : stored.value) as Partial<T>, index: stored.index }
  } catch {
    return null
  }
}

/** Keeps `value` (minus anything that is a file) and `index`; a blank form is cleared
 *  instead, so an untouched visit leaves nothing behind. */
export function saveDraft<T extends object>(key: string, value: T, index: number): void {
  const kept: Record<string, unknown> = {}
  for (const [name, item] of Object.entries(value)) if (storable(item)) kept[name] = item
  if (blank(kept)) {
    clearDraft(key)
    return
  }
  const stored: Stored = { v: VERSION, savedAt: Date.now(), index, value: kept }
  try {
    storage()?.setItem(key, JSON.stringify(stored))
  } catch {
    /* refused: the form still works, it just will not remember */
  }
}

export function clearDraft(key: string, previousKey?: string): void {
  try {
    storage()?.removeItem(key)
    if (previousKey) storage()?.removeItem(previousKey)
  } catch {
    /* nothing to forget, or nowhere to forget it from */
  }
}
