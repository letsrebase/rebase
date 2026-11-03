import { ArrowLeftRight, MailPlus, Plus } from 'lucide-react'
import { DropdownMenuItem } from '@rebase/ui/dropdown-menu'
import { roleLabel } from '@/lib/roles'
import type { IdentitySpace } from './useSpaceSwitcher'

/**
 * The space entries of the profile menu: every other space this identity may enter (design
 * §3, REB-345) -- the same live chooser the login page uses, reachable now without first
 * logging out -- and the way to make a new one. Every row is disabled while one is being
 * entered (`enteringSlug`).
 */
export function SpaceMenu({
  otherSpaces,
  enteringSlug,
  onEnter,
  onCreate,
}: {
  otherSpaces: IdentitySpace[]
  enteringSlug: string | null
  onEnter: (space: IdentitySpace) => void
  onCreate: () => void
}) {
  return (
    <>
      {otherSpaces.map((space) => (
        <DropdownMenuItem
          key={space.slug}
          disabled={enteringSlug !== null}
          onSelect={() => onEnter(space)}
        >
          {space.invito ? <MailPlus className="size-4" /> : <ArrowLeftRight className="size-4" />}
          <span className="flex-1 truncate">{space.slug}</span>
          <span className="text-xs text-muted-foreground">
            {space.invito ? 'Invito' : roleLabel(space.ruolo)}
          </span>
        </DropdownMenuItem>
      ))}
      <DropdownMenuItem onSelect={onCreate}>
        <Plus className="size-4" />
        Crea un nuovo spazio
      </DropdownMenuItem>
    </>
  )
}
