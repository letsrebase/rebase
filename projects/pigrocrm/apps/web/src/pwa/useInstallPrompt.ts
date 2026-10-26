import { useCallback, useEffect, useState } from 'react'
import { tenantPrefix } from '@/lib/tenant'
import { shouldShowAndroidPrompt, shouldShowIosHint } from './install'

// Per space: every space of an origin is its own installed app, and turning the offer
// down in one must not hide it in the next, whose copy says «sul tuo spazio».
const ANDROID_DISMISSED_KEY = `pigrocrm:install-prompt-dismissed:${tenantPrefix}`
const IOS_HINT_SEEN_KEY = `pigrocrm:ios-install-hint-seen:${tenantPrefix}`
// Not on first paint: wait for the person to do something, or this long, whichever
// comes first, before offering anything.
const ENGAGEMENT_DELAY_MS = 20_000
const ENGAGEMENT_EVENTS = ['pointerdown', 'keydown', 'scroll'] as const

/** Chrome and Edge fire this in place of a plain Event; lib.dom.d.ts does not have it. */
interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

function readFlag(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === 'true'
  } catch {
    return false
  }
}

function writeFlag(key: string): void {
  try {
    window.localStorage.setItem(key, 'true')
  } catch {
    // Private mode: the offer comes back next visit, which is the right failure.
  }
}

/** iOS Safari alone has `navigator.standalone`; unlike a user-agent test a desktop-site
 *  toggle does not spoof it. It is also how a home-screen launch is told apart there. */
function isIosDevice(): boolean {
  return 'standalone' in navigator
}

function isRunningStandalone(): boolean {
  return (
    window.matchMedia?.('(display-mode: standalone)').matches === true ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  )
}

export interface InstallPrompt {
  showAndroidPrompt: boolean
  showIosHint: boolean
  install(): Promise<void>
  dismissAndroid(): void
  dismissIosHint(): void
}

/** The install offer for this visit (REB-731). `enabled` is false where the page is not
 *  a space's (`isInstallablePage`), so nothing is listened for there. */
export function useInstallPrompt(enabled: boolean): InstallPrompt {
  const [engaged, setEngaged] = useState(false)
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null)
  const [androidDismissed, setAndroidDismissed] = useState(() => readFlag(ANDROID_DISMISSED_KEY))
  const [iosSeenBefore] = useState(() => readFlag(IOS_HINT_SEEN_KEY))
  const [iosHiddenNow, setIosHiddenNow] = useState(false)

  useEffect(() => {
    if (!enabled || isRunningStandalone()) return

    const onBeforeInstallPrompt = (event: Event) => {
      event.preventDefault()
      setDeferred(event as BeforeInstallPromptEvent)
    }
    const onInstalled = () => {
      setDeferred(null)
      setAndroidDismissed(true)
    }
    window.addEventListener('beforeinstallprompt', onBeforeInstallPrompt)
    window.addEventListener('appinstalled', onInstalled)

    const markEngaged = () => {
      setEngaged(true)
      // The iOS hint has no dismiss event to wait for: being shown once is what
      // "once" means for it, so it is recorded the moment it can appear.
      if (isIosDevice() && !isRunningStandalone()) writeFlag(IOS_HINT_SEEN_KEY)
      stopWaiting()
    }
    const timer = window.setTimeout(markEngaged, ENGAGEMENT_DELAY_MS)
    for (const type of ENGAGEMENT_EVENTS) {
      window.addEventListener(type, markEngaged, { once: true, passive: true })
    }
    function stopWaiting() {
      window.clearTimeout(timer)
      for (const type of ENGAGEMENT_EVENTS) window.removeEventListener(type, markEngaged)
    }

    return () => {
      window.removeEventListener('beforeinstallprompt', onBeforeInstallPrompt)
      window.removeEventListener('appinstalled', onInstalled)
      stopWaiting()
    }
  }, [enabled])

  const dismissAndroid = useCallback(() => {
    setAndroidDismissed(true)
    writeFlag(ANDROID_DISMISSED_KEY)
  }, [])

  const install = useCallback(async () => {
    if (!deferred) return
    setDeferred(null)
    await deferred.prompt()
    const { outcome } = await deferred.userChoice
    if (outcome === 'dismissed') dismissAndroid()
  }, [deferred, dismissAndroid])

  const dismissIosHint = useCallback(() => {
    setIosHiddenNow(true)
    writeFlag(IOS_HINT_SEEN_KEY)
  }, [])

  return {
    showAndroidPrompt:
      enabled &&
      shouldShowAndroidPrompt({
        engaged,
        hasDeferredPrompt: deferred !== null,
        dismissed: androidDismissed,
      }),
    showIosHint:
      enabled &&
      shouldShowIosHint({
        engaged,
        isIos: isIosDevice(),
        isStandalone: isRunningStandalone(),
        seenBefore: iosSeenBefore,
        hiddenThisVisit: iosHiddenNow,
      }),
    install,
    dismissAndroid,
    dismissIosHint,
  }
}
