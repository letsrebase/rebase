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
  /** Stores the draft with this key now, unless it is the one already stored, and
   *  answers the stored campaign: the test and «Invia» go through it, so neither acts
   *  on a campaign older than the page. */
  persist: (key: string) => Promise<Campaign>
  /** A campaign another call answered (the test), taken as the stored one. */
  adopt: (campaign: Campaign) => void
}

/** The draft saves itself (REB-526): `key` settles for `AUTOSAVE_MS`, then the first
 *  save creates the campaign and every later one patches it. Saves never overlap: one
 *  that is asked for while another is in flight waits for it, so a fast typist can
 *  neither create the campaign twice nor have an older body land after a newer one. A
 *  save that fails is not retried until the draft changes again, or until the test or
 *  «Invia» asks for it. `initial` is the stored campaign on the edit route, whose form
 *  is taken as already saved: opening a tested campaign must not undo its test. */
export function useAutosave(key: string | null, initial: Campaign | null): Autosave {
  const [campaign, setCampaign] = useState<Campaign | null>(initial)
  const [savedKey, setSavedKey] = useState<string | null>(initial ? key : null)
  const [failedKey, setFailedKey] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  // The same facts for the async code, which must read what is true now rather than
  // what was true when its render ran.
  const stored = useRef<{ campaign: Campaign | null; key: string | null; inFlight: Promise<Campaign> | null }>({
    campaign: initial,
    key: initial ? key : null,
    inFlight: null,
  })

  const persist = useCallback(async (target: string): Promise<Campaign> => {
    const current = stored.current
    while (current.inFlight) await current.inFlight.catch(() => undefined)
    if (current.campaign && target === current.key) return current.campaign
    const body = JSON.parse(target) as CampaignDraft
    const call = current.campaign ? admin.updateCampaign(current.campaign.id, body) : admin.createCampaign(body)
    current.inFlight = call
    setSaving(true)
    try {
      const saved = await call
      current.campaign = saved
      current.key = target
      setCampaign(saved)
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
      current.inFlight = null
      setSaving(false)
    }
  }, [])

  const adopt = useCallback((next: Campaign) => {
    stored.current.campaign = next
    setCampaign(next)
  }, [])

  const settled = useDebounce(key, AUTOSAVE_MS)
  useEffect(() => {
    if (settled === null || settled === failedKey) return
    // `persist` itself returns early when this draft is the stored one; the failure is
    // already in `error`, where the page shows it.
    persist(settled).catch(() => undefined)
  }, [settled, failedKey, persist])

  return { campaign, savedKey, saving, error, savedAt, persist, adopt }
}
