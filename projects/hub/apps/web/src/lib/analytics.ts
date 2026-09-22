import { capture, identifyUser } from '@rebase/analytics/browser'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { Me } from './api'

/**
 * What the hub tells PostHog beyond pageviews and autocapture (ORB-185, design
 * `docs/design/2026-09-12-posthog-analytics-design.md` § The hub): the wizard funnel,
 * the guide download, and who the person is once a session is known. Every call goes
 * through `@rebase/analytics/browser`, which is silent on localhost and in tests, so
 * nothing here checks whether analytics is on.
 */

export type WizardKind = 'freelance' | 'azienda'

/** `perk=` off a search string, as it arrived (trimmed and capped, the way `readUtm`
 *  keeps a UTM): the site's guide section sends `guida`, and a funnel needs the value
 *  that was there rather than the one the banner recognises. */
export function readPerkParam(search: string): string | null {
  const value = new URLSearchParams(search).get('perk')?.trim().slice(0, 200)
  return value || null
}

/**
 * The three funnel events of one wizard. `onStep` is for the engine to call on every
 * step it shows, including the first and the review (`passo === passi`, `schermata`
 * `'riepilogo'`), so a breakdown by `passo` or by `schermata` needs no special case;
 * its first call also sends `wizard_iniziato`, once per mount, whatever the page
 * re-renders for. `schermata` is the screen's own id -- a field's id today, one field
 * per screen until REB-120/121 group some of them -- captured beside the numeric
 * `passo` so a funnel read after the regroup still knows which screen a pre-regroup
 * `passo` meant (REB-122). `completed` is for the page to call once the API said yes.
 */
export function useWizardAnalytics(tipo: WizardKind, search: string) {
  const perk = readPerkParam(search)
  const base = useMemo(() => ({ tipo, ...(perk ? { perk } : {}) }), [tipo, perk])
  const started = useRef(false)

  // `wizard_iniziato` rides on the first step rather than on an effect of its own: a
  // child's effects commit before its parent's, so a separate effect would land
  // `wizard_passo { passo: 0 }` first and a funnel could tie on the timestamps.
  const onStep = useCallback(
    (passo: number, passi: number, schermata: string) => {
      if (!started.current) {
        started.current = true
        capture('wizard_iniziato', base)
      }
      capture('wizard_passo', { ...base, passo, passi, schermata })
    },
    [base],
  )
  const completed = useCallback(() => capture('wizard_completato', base), [base])
  return { onStep, completed }
}

/** Whoever `useMe()` resolves to, identified once by id, with the role that decides
 *  what the sidebar shows (REB-279: replaces `useIdentifyAdmin`/`useIdentifyMember`,
 *  called once from the signed-in shell instead of twice from two guards). `ruolo` is
 *  `'admin' | 'member'` and sent whenever the person is known, rather than typed
 *  `'admin'`-only and dropped when falsy: REB-280's own scope item ("widen `ruolo` to
 *  `'admin' | 'member'` and always send it"), done here because it was already on this
 *  exact code path in this diff. */
export function useIdentify(
  person: Pick<Me, 'id' | 'email' | 'nome'> | null | undefined,
  ruolo: 'admin' | 'member' | undefined,
): void {
  // Primitives rather than the object: the query hands a new object on every refetch
  // and the person has not changed.
  const id = person?.id
  const email = person?.email
  const nome = person?.nome
  useEffect(() => {
    if (id) identifyUser(id, { email, nome, ruolo })
  }, [id, email, nome, ruolo])
}
