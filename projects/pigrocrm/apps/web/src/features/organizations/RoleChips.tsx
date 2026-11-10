import { Badge } from '@rebase/ui/badge'
import { orderedRoles, roleLabel } from './roles'

/**
 * What an organization is to us, as outlined chips («Cliente», «Fornitore», «Freelance»).
 * A role is not a state: no tone, no dot, so it never reads like a `StatusPill`
 * (the approved brief of 2026-11-10, «What a component can violate»).
 */
export function RoleChips({ roles }: { roles: readonly string[] }) {
  return (
    <>
      {orderedRoles(roles).map((role) => (
        <Badge key={role} variant="outline" data-role={role}>
          {roleLabel(role)}
        </Badge>
      ))}
    </>
  )
}
