import { Button } from '@rebase/ui/button'
import { useState } from 'react'
import { tenantPrefix } from '@/lib/tenant'
import { useInstallPrompt } from './useInstallPrompt'
import { isInstallablePage } from './urls'

/**
 * The offer to install PigroCRM as an app (REB-731): Chrome's own prompt, held back
 * until the person has done something, on Android and desktop; and, on iOS Safari,
 * which never fires that event, a line saying where the button is. Each is shown once.
 * Only in the production build, where the worker and the manifest exist, and only on a
 * page that belongs to a space (`isInstallablePage`), judged once from the address the
 * page was opened at, as `startPwa` judges it.
 */
export function InstallPrompt() {
  const [enabled] = useState(
    () => import.meta.env.PROD && isInstallablePage(window.location.pathname, tenantPrefix),
  )
  const prompt = useInstallPrompt(enabled)
  if (!prompt.showAndroidPrompt && !prompt.showIosHint) return null

  return (
    <div
      role="status"
      className="bg-popover text-popover-foreground ring-foreground/10 fixed inset-x-4 bottom-4 z-50 flex items-center justify-between gap-4 rounded-xl p-4 text-sm shadow-lg ring-1 sm:left-auto sm:max-w-md"
    >
      <p>
        {prompt.showAndroidPrompt
          ? "Installa PigroCRM: lo apri dall'icona, sul tuo spazio, come un'app."
          : 'Per installare PigroCRM tocca Condividi, poi «Aggiungi alla schermata Home».'}
      </p>
      <div className="flex flex-none gap-2">
        {prompt.showAndroidPrompt ? (
          <>
            <Button size="sm" onClick={() => void prompt.install()}>
              Installa
            </Button>
            <Button size="sm" variant="ghost" onClick={prompt.dismissAndroid}>
              Non ora
            </Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" onClick={prompt.dismissIosHint}>
            Ho capito
          </Button>
        )}
      </div>
    </div>
  )
}
