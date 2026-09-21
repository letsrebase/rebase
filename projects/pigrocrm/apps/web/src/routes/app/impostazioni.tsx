import { Outlet, createFileRoute } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): `settings.tsx` is the real layout now. This one has no
// content of its own any more -- it only gives every `impostazioni/*` child below a
// parent to nest under, so each of them can throw its own redirect to the equivalent
// `/app/settings/*` page.
export const Route = createFileRoute('/app/impostazioni')({ component: () => <Outlet /> })
