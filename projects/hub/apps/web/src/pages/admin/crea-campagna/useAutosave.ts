import { useCallback, useEffect, useRef, useState } from 'react'
import { admin, type Campaign, type CampaignDraft } from '@/lib/api'
import { useDebounce } from '@/lib/adminList'

/** How long the page waits after the last keystroke before it saves the draft. */
export const AUTOSAVE_MS = 600

export interface Autosave {
  /** The campaign as the server last answered it; `null` until the first save. */
  campaign: Campaign | null
  /** The key of the last draft the server stored (`keyOf` in `form.ts`). */
  savedKey: string | null
  saving: boolean
  error: unknown
  /** When the server last stored the draft, for «Bozza salvata alle …». */
  savedAt: string | null
  /** Stores the draft with this key, unless it is the one already stored, then runs
   *  `action` on the stored campaign and keeps the campaign it answers. The test and
   *  «Invia» go through it, so neither acts on a campaign older than the page, and no
   *  save lands while they run. */
  run: (key: string, action: (campaign: Campaign) => Promise<Campaign>) => Promise<Campaign>
}

/** The draft saves itself (REB-526): `key` settles for `AUTOSAVE_MS`, then the first
 *  save creates the campaign and every later one patches it. Every call to the server
 *  goes through one queue, saves and the test and «Invia» alike, so none overlaps
 *  another: a fast typist can neither create the campaign twice nor have an older body
 *  land after a newer one, and the test's answer can never be overtaken by a save that
 *  started after it. A save that fails is not retried until the draft changes again, or
 *  until the test or «Invia» asks for it. `initial` is the stored campaign on the edit
 *  route, whose form is taken as already saved: opening a tested campaign must not undo
 *  its test. */
export function useAutosave(key: string | null, initial: Campaign | null): Autosave {
  const [campaign, setCampaign] = useState<Campaign | null>(initial)
  const [savedKey, setSavedKey] = useState<string | null>(initial ? key : null)
  const [failedKey, setFailedKey] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  // The same facts for the queued tasks, which must read what is true when they run
  // rather than what was true when they were queued.
  const stored = useRef<{ campaign: Campaign | null; key: string | null }>({ campaign: initial, key: initial ? key : null })
  const queue = useRef<Promise<unknown>>(Promise.resolve())

  const enqueue = useCallback(<T>(task: () => Promise<T>): Promise<T> => {
    const next = queue.current.then(task, task)
    queue.current = next.catch(() => undefined)
    return next
  }, [])

  const keep = useCallback((next: Campaign) => {
    stored.current.campaign = next
    setCampaign(next)
  }, [])

  /** One save, run from inside the queue. */
  const save = useCallback(
    async (target: string): Promise<Campaign> => {
      const current = stored.current
      if (current.campaign && target === current.key) {
        // Back to what the server holds (a failed edit undone): nothing is unsaved, so
        // no failure is left to show.
        setError(null)
        setFailedKey(null)
        return current.campaign
      }
      const body = JSON.parse(target) as CampaignDraft
      setSaving(true)
      try {
        const saved = current.campaign ? await admin.updateCampaign(current.campaign.id, body) : await admin.createCampaign(body)
        keep(saved)
        current.key = target
        setSavedKey(target)
        setSavedAt(new Date().toISOString())
        setError(null)
        setFailedKey(null)
        return saved
      } catch (failure) {
        setError(failure)
        setFailedKey(target)
        throw failure
      } finally {
        setSaving(false)
      }
    },
    [keep],
  )

  const run = useCallback(
    (target: string, action: (campaign: Campaign) => Promise<Campaign>) =>
      enqueue(async () => {
        const answered = await action(await save(target))
        keep(answered)
        return answered
      }),
    [enqueue, save, keep],
  )

  const settled = useDebounce(key, AUTOSAVE_MS)
  useEffect(() => {
    if (settled === null || settled === failedKey) return
    // `save` returns early when this draft is the stored one; a failure is already in
    // `error`, where the page shows it.
    enqueue(() => save(settled)).catch(() => undefined)
  }, [settled, failedKey, enqueue, save])

  return { campaign, savedKey, saving, error, savedAt, run }
}
