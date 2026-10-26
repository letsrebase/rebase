import { Outlet, createRootRoute } from '@tanstack/react-router'
import { Toaster } from '@rebase/ui/sonner'
import { InstallPrompt } from '@/pwa/InstallPrompt'

export const Route = createRootRoute({
  component: () => (
    <>
      <Outlet />
      <Toaster richColors position="top-right" />
      <InstallPrompt />
    </>
  ),
})
