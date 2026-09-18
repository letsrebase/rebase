import { Outlet, createRootRoute } from '@tanstack/react-router'
import { Toaster } from '@rebase/ui/sonner'

export const Route = createRootRoute({
  component: () => (
    <>
      <Outlet />
      <Toaster richColors position="top-right" />
    </>
  ),
})
