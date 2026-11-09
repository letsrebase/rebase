import { Outlet, createFileRoute, useLocation, useNavigate } from '@tanstack/react-router'
import { useEffect, type ReactNode } from 'react'
import { AppShell } from '@/components/AppShell'
import { GmailBanner } from '@/components/GmailBanner'
import { ModuleGate } from '@/components/ModuleGate'
import { Button } from '@rebase/ui/button'
import { Loader } from '@rebase/ui/loader'
import { Onboarding } from '@/features/onboarding/Onboarding'
import { useAuth, useIsAdmin } from '@/lib/auth'
import { useModules } from '@/lib/modules'
import { PUBLIC_APP_ROUTES } from '@/lib/tenant'

/**
 * A new space's administrator is asked how the space works before anything else (spec
 * 2026-11-09, B3, REB-957): a full screen, outside the shell. It covers the screen while the
 * answer is `pending`, and when «Primi passi» sends the person back to it after a
 * «Decido dopo». Nobody else sees it, and a read that failed is «not known», not «pending».
 * Its own component so the modules are read only for a session that exists.
 */
function OnboardingGate({ children }: { children: ReactNode }) {
  const isAdmin = useIsAdmin()
  const modules = useModules()
  const { pathname } = useLocation()
  if (!isAdmin) return children
  if (modules.loading) {
    return (
      <div className="flex min-h-full items-center justify-center p-8">
        <Loader className="size-8" />
      </div>
    )
  }
  const revisiting = modules.onboarding === 'skipped' && pathname.replace(/\/+$/, '') === '/app/onboarding'
  if (modules.onboarding === 'pending' || revisiting) return <Onboarding />
  return children
}

function AppLayout() {
  const { user, isLoading, isError, retrySession } = useAuth()
  const navigate = useNavigate()
  const { pathname, href } = useLocation()
  // Task A7 moved the login page to routes/app/login.tsx so its URL is
  // /app/login, which makes it a CHILD of this very layout route in TanStack
  // Router's file-based nesting (confirmed in routeTree.gen.ts:
  // AppLoginRoute's getParentRoute is AppRoute) -- rendered inside this
  // component's own <Outlet />. Gating that Outlet on `user` the same way as
  // every other /app/* page would mean an unauthenticated visitor can never
  // see the login form at all: the redirect target and the thing being
  // redirected FROM would be the same gate, chosen forever. The login route is
  // the one child of this layout that must render unconditionally, with no
  // redirect and no AppShell chrome around it.
  // The three pages a visitor reaches without a session: the login, the signup that
  // makes a space (spec 2026-09-08) and the page that spends a link by mail (spec
  // 2026-09-12 §6.2). Everything else under /app bounces to the login.
  // Without a trailing slash: `/app/register/` is the same page, and a visitor who
  // arrived through a redirect that kept one must not be bounced to the login for it.
  const isLoginRoute = PUBLIC_APP_ROUTES.has(pathname.replace(/\/+$/, ''))

  useEffect(() => {
    // The href is basepath-relative (main.tsx's `createRouter({ basepath })` strips the
    // tenant prefix before the router ever sees a path), so it is already exactly the
    // shape `safeAppRedirect` (lib/tenant.ts) checks against: never carries the slug,
    // never another origin. The login page reads it back once a session exists.
    if (!isLoginRoute && !isLoading && !isError && !user) void navigate({ to: '/app/login', search: { redirect: href } })
  }, [isLoginRoute, isLoading, isError, user, navigate, href])

  if (isLoginRoute) return <Outlet />

  if (isLoading) {
    return (
      <div className="flex min-h-full items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader className="size-8" />
        Caricamento…
      </div>
    )
  }
  // The session could not be read at all (the API is restarting, the network dropped):
  // that is not "signed out", so the login page is no answer. Say so and offer a retry.
  if (isError) {
    return (
      <div className="flex min-h-full flex-col items-center justify-center gap-4 p-8 text-center">
        <p className="text-sm text-muted-foreground">
          Non riesco a raggiungere il server. Controlla la connessione e riprova.
        </p>
        <Button onClick={retrySession}>Riprova</Button>
      </div>
    )
  }
  if (!user) return null

  return (
    <OnboardingGate>
    <AppShell>
      {/* Outside the <Outlet />, so it survives every navigation within the shell
          rather than being remounted (and re-announced) per page -- and inside this
          component's `!user` guard, so the health query is never issued by an
          unauthenticated visitor, who has no google_accounts row to have an opinion
          about and would collect a 401 on every load of the login screen. */}
      <GmailBanner />
      {/* The pages of a module the space has switched off are not drawn (REB-953). */}
      <ModuleGate>
        <Outlet />
      </ModuleGate>
    </AppShell>
    </OnboardingGate>
  )
}

// Deliberately NOT "_app.tsx" (TanStack Router's pathless-layout convention):
// a leading underscore contributes no URL segment at all, so "_app/index.tsx"
// would resolve to the exact same full path as the root "/" -- reproduced live
// while wiring this up ("Conflicting configuration paths ... '/', '/' ...
// routes/index.tsx and routes/_app/index.tsx"). This file's name is what makes
// "/app" a real, matchable path for AppShell's nav links to point at, and for
// nginx's `location = /app { return 302 /app/; }` (deploy/nginx/spa.conf) to
// redirect into. "/" itself belongs to the landing page (apps/web/landing/),
// not to this router at all, since slice 5 (Task A7): there is no more
// routes/index.tsx to redirect from.
export const Route = createFileRoute('/app')({ component: AppLayout })
