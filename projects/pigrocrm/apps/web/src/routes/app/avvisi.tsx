import { createFileRoute } from '@tanstack/react-router'
import { AvvisiPage } from '@/features/reminders/AvvisiPage'

// Only `Route` is exported, so the router plugin keeps code-splitting this route; the page
// is `features/reminders/AvvisiPage.tsx`. No role guard: the list is the caller's own, and
// every role has reminders (the API shows each person only what their scope allows).
export const Route = createFileRoute('/app/avvisi')({ component: AvvisiPage })
