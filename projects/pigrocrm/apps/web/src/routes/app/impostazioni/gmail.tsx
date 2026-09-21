import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): `settings/gmail.tsx` is the real page now. The Gmail
// OAuth callback (`_SETTINGS_PAGE` in apps/api/src/pigrocrm_api/routers/gmail.py)
// already lands on the new path directly, so this covers only a stale bookmark.
export const Route = createFileRoute('/app/impostazioni/gmail')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/gmail' })
  },
})
