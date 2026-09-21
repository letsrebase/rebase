import { createFileRoute } from '@tanstack/react-router'
import { TimePage } from '@/features/time/TimePage'

// Only `Route` is exported: a route file exporting anything else opts that route out of
// the router plugin's automatic code-splitting, which `routeTree.gen.ts` warns about --
// the same reason `TokensPanel` and `SettingsLayout` live in `features/` rather than in
// their route files.
//
// No admin guard, and deliberately a top-level sibling of deal/fatture rather than
// something nested: a week of one's own hours is what every authenticated role logs, and
// `list_time_entries` already scopes what comes back.
export const Route = createFileRoute('/app/hours')({ component: TimePage })
