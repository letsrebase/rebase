import { createFileRoute } from '@tanstack/react-router'
import { NotificationsPanel } from '@/features/settings/NotificationsPanel'

/**
 * `?da=digest` survives the hop from the digest mail's opt-out link (the Profilo route
 * redirects here with it), so the address bar stays what the mail said and PostHog reads the
 * return from it. Nothing on this page acts on it.
 */
export const Route = createFileRoute('/app/settings/notifications')({
  validateSearch: (search: Record<string, unknown>): { da?: string } => ({
    da: typeof search.da === 'string' ? search.da : undefined,
  }),
  component: NotificationsPanel,
})
