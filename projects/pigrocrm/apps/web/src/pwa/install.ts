/**
 * When to offer the install, as functions of plain values (REB-731). The browser and
 * storage wiring is `useInstallPrompt.ts`.
 */

export interface AndroidPromptInputs {
  /** The person has done something in this visit: the offer is never on first paint. */
  engaged: boolean
  /** A `beforeinstallprompt` event was captured and not yet used. */
  hasDeferredPrompt: boolean
  /** They turned the offer down, here or in the browser's own dialog, on any past visit. */
  dismissed: boolean
}

export function shouldShowAndroidPrompt({
  engaged,
  hasDeferredPrompt,
  dismissed,
}: AndroidPromptInputs): boolean {
  return engaged && hasDeferredPrompt && !dismissed
}

export interface IosHintInputs {
  engaged: boolean
  isIos: boolean
  isStandalone: boolean
  /** The hint was shown on a past visit: iOS gives no dismiss event to wait for. */
  seenBefore: boolean
  hiddenThisVisit: boolean
}

export function shouldShowIosHint({
  engaged,
  isIos,
  isStandalone,
  seenBefore,
  hiddenThisVisit,
}: IosHintInputs): boolean {
  // An installed app is already standalone: the hint would tell them to do what they did.
  return engaged && isIos && !isStandalone && !seenBefore && !hiddenThisVisit
}
