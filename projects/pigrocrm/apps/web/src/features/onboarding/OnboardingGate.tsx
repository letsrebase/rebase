import { useLocation } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { Loader } from '@rebase/ui/loader'
import { useAuth, useIsAdmin } from '@/lib/auth'
import { useModules } from '@/lib/modules'
import { Onboarding } from './Onboarding'

/**
 * A new space's administrator is asked how the space works before anything else (spec
 * 2026-11-09, B3, REB-957): a full screen, outside the shell. It covers the screen while the
 * answer is `pending`, and when «Primi passi» sends the person back to it after a
 * «Decido dopo». Nobody else sees it, and a read that failed is «not known», not «pending».
 * Its own component so the modules are read only for a session that exists.
 */
export function OnboardingGate({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  // The server asks for an unscoped administrator (`require_unscoped_admin`): a scoped one
  // would be shown a screen whose every button is refused.
  const isAdmin = useIsAdmin() && !Array.isArray(user?.legal_entities)
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
