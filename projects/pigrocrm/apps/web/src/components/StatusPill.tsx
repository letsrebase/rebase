import type { ReactNode } from 'react'
import { Badge, type BadgeDot } from '@rebase/ui/badge'

/**
 * The five tints a state can read as, and the only five (design spec §3: no new
 * colour). They are the `Badge` dot tints, re-exported under a name that says what a
 * *caller* is choosing -- a tone for a state -- rather than what the primitive draws:
 *
 * - `ink`     the settled, ordinary state (an issued invoice, an accepted offer)
 * - `muted`   a state that claims nothing yet (a draft)
 * - `gold`    a state that is waiting on somebody (an invoice still to be collected)
 * - `danger`  a state that has gone wrong or been undone (overdue, annulled, refused)
 * - `accent`  the --accent slot, for a state that must follow the theme's own accent
 *
 * A feature never inlines a tone at the call site: each one keeps a `Record` from its
 * own state enum to a tone, next to the labels for those states, so adding a state
 * makes the map fail to compile rather than silently render as the default. Five of the
 * six enums it is keyed on (`InvoiceStato`, `StatoPagamento`, `OfferState`,
 * `TimeEntryStato`, `TokenStato`) are unions written by hand next to the labels, so the
 * compile only breaks once somebody has widened the union -- only `Stage['tipo']` comes
 * from the generated API types.
 */
export type StatusTone = BadgeDot

/**
 * A state, as the reference screenshots show it: a fully round chip on Paper with a
 * small coloured dot before an Italian label («● Emessa», «● Bozza»).
 *
 * Deliberately thin -- it is `Badge variant="pill"` with the dot wired up and nothing
 * else -- because its value is that every state in the product goes through one
 * component: a table of dozens of these has to be quiet and identical, and the tone
 * maps that feed it are where the product's actual decisions live.
 *
 * `data-tone` is on the element for the same reason `Badge` already sets
 * `data-variant`: a test, or somebody in devtools, can read which tone a row got
 * without matching Tailwind classes that the next design pass will rename.
 */
export function StatusPill({ tone, children }: { tone: StatusTone; children: ReactNode }) {
  return (
    <Badge variant="pill" dot={tone} data-tone={tone} className="gap-1.5">
      {children}
    </Badge>
  )
}
