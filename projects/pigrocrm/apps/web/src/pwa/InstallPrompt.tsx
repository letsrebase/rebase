import { Button } from '@rebase/ui/button'
import { useRouterState } from '@tanstack/react-router'
import { tenantPrefix } from '@/lib/tenant'
import { useInstallPrompt } from './useInstallPrompt'
import { isInstallablePage } from './urls'

/**
 * The offer to install PigroCRM as an app (REB-731): Chrome's own prompt, held back
 * until the person has done something, on Android and desktop; and, on iOS Safari,
 * which never fires that event, a line saying where the button is. Each is shown once.
 * Only in the production build, where the worker and the manifest exist, and only on a
 * page that belongs to a space (`isInstallablePage`), judged on every route change as
 * `startPwa` is: signing in from the bare `/app/login` moves the same document onto one.
 */
export function InstallPrompt() {
  // The router's own address, not `window.location`: a render that follows a navigation
  // runs before the browser's history has caught up with it.
  const href = useRouterState({ select: (state) => state.location.href })
  const enabled =
    import.meta.env.PROD &&
    isInstallablePage(new URL(href, 'http://internal.invalid').pathname, tenantPrefix)
  const prompt = useInstallPrompt(enabled)
  if (!prompt.showAndroidPrompt && !prompt.showIosHint) return null

  return (
    <div
      role="status"
      className="bg-popover text-popover-foreground flex flex-none items-center justify-between gap-4 border-t p-3 text-sm"
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
