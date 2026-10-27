import { Outlet, createRootRoute } from '@tanstack/react-router'
import { Toaster } from '@rebase/ui/sonner'
import { InstallPrompt } from '@/pwa/InstallPrompt'

export const Route = createRootRoute({
  component: () => (
    // REB-791: the install offer is a row under the page, not a card floating over it:
    // fixed at the bottom it covered the user menu of the sidebar and the last row of
    // the content on a phone. The page keeps the height the offer leaves it.
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1">
        <Outlet />
      </div>
      <Toaster richColors position="top-right" />
      <InstallPrompt />
    </div>
  ),
})
