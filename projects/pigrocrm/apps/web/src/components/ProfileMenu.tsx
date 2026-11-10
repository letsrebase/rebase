import { Link } from '@tanstack/react-router'
import { ChevronsUpDown, HardDrive, LogOut, Rocket, Settings, UserRound } from 'lucide-react'
import { useSpaceHasData } from '@/features/get-started/firstSteps'
import { SpaceMenu } from '@/features/spaces/SpaceMenu'
import { useSpaceSwitcher } from '@/features/spaces/useSpaceSwitcher'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { cn } from '@rebase/ui/cn'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@rebase/ui/dropdown-menu'
import { useAuth, useIsAdmin } from '@/lib/auth'
import { canSeeSettingsTab } from '@/lib/permissions'
import { writeRegisterHandoffEmail } from '@/lib/registerHandoff'
import { roleLabel } from '@/lib/roles'
import { SETTINGS_PATHS } from './nav'
import { FOCUS } from './sidebarStyles'

/**
 * The profile, anchored at the bottom of the sidebar, opens a menu: the account's own
 * things -- who is signed in, the space's settings for an admin, the other spaces, the way
 * out. `go` is how a move to another basepath is made (a full navigation).
 */
export function ProfileMenu({ rail, go }: { rail: boolean; go: (url: string) => void }) {
  const { user, logout } = useAuth()
  const isAdmin = useIsAdmin()
  const switcher = useSpaceSwitcher(go)
  // Whether the space has data yet: «Primi passi» is in this menu from then on. The same
  // predicate the Home asks to choose between the start page and the dashboard, so the
  // two cannot disagree about when a space stops being new.
  const hasData = useSpaceHasData()

  const initials = (user?.name ?? '?')
    .split(' ')
    .map((part) => part[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()

  return (
    <div className="mt-auto border-t border-sidebar-border p-3">
      <DropdownMenu
        onOpenChange={(open) => {
          if (open) switcher.refresh()
        }}
      >
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className={cn(
              'flex w-full items-center gap-3 px-2 py-2 text-left transition-colors hover:bg-sidebar-accent',
              FOCUS,
              rail && 'justify-center px-0',
            )}
            // WCAG 2.5.3 (axe `label-content-name-mismatch`): the name has to contain what
            // the button shows, initials first, name and role after, so a voice user
            // saying what they read finds it. «menu del profilo» stays at the end: it is
            // what the control is, and how the tests and `e2e/helpers.ts` find it.
            aria-label={`${initials} ${user?.name ?? ''} ${user ? roleLabel(user.role) : ''}, menu del profilo`}
          >
            <Avatar className="size-8 shrink-0">
              <AvatarFallback className="bg-sidebar-accent text-xs text-sidebar-foreground">
                {initials}
              </AvatarFallback>
            </Avatar>
            {/* The spaces between the three texts are not drawn (flex items), but they are
                in the text a checker reads as what the button shows. */}{' '}
            <div className={cn('min-w-0 flex-1', rail && 'sr-only')}>
              <p className="truncate text-sm font-medium">{user?.name}</p>{' '}
              {/* The role in words: «admin» is what the API stores, not what the
                  product writes (spec §5.5). */}
              <p className="truncate text-xs text-sidebar-foreground/70">
                {user ? roleLabel(user.role) : null}
              </p>
            </div>
            <ChevronsUpDown
              className={cn('size-4 shrink-0 text-sidebar-foreground/70', rail && 'hidden')}
              aria-hidden="true"
            />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" className="w-56">
          <DropdownMenuLabel className="font-normal">
            <p className="truncate text-sm font-medium">{user?.name}</p>
            <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          {/* «Profilo», for every role and with no gate on it: a shortcut to the same
              page as the sidebar's «Personale». One menu item, above the admin's own
              entry, because it is the one thing here that belongs to the person
              rather than to the space. */}
          <DropdownMenuItem asChild>
            <Link to="/app/settings/profile">
              <UserRound className="size-4" />
              Profilo
            </Link>
          </DropdownMenuItem>
          {/* «Primi passi» (ORB-180, REB-222) used to be a sidebar entry, right under
              Home. It is the start page of a new space, and the Home already is that
              page while the space is empty, so the entry is only wanted from the first
              piece of work on: this is where it goes then, for every role, one click
              from the profile. `/app/get-started` itself answers as before. */}
          {hasData && (
            <DropdownMenuItem asChild>
              <Link to="/app/get-started">
                <Rocket className="size-4" />
                Primi passi
              </Link>
            </DropdownMenuItem>
          )}
          {/* «Google Drive», for a collaboratore (REB-457): their Drive is their own
              credential, as the profile is, and the Drive tab is where every consent
              outcome lands. Read from the tab table, so a readonly person, whose
              consent the service refuses, is not offered it. An admin reaches it through
              the sidebar's «Collegamenti». */}
          {!isAdmin && user !== null && canSeeSettingsTab(user.role, 'drive') && (
            <DropdownMenuItem asChild>
              <Link to={SETTINGS_PATHS.drive}>
                <HardDrive className="size-4" />
                Google Drive
              </Link>
            </DropdownMenuItem>
          )}
          {isAdmin && (
            <DropdownMenuItem asChild>
              <Link to="/app/settings/space">
                <Settings className="size-4" />
                Impostazioni dello spazio
              </Link>
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <SpaceMenu
            otherSpaces={switcher.otherSpaces}
            enteringSlug={switcher.enteringSlug}
            onEnter={(space) => void switcher.enter(space)}
            onCreate={() => {
              if (user) writeRegisterHandoffEmail(user.email)
              go('/app/register')
            }}
          />
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void logout()}>
            <LogOut className="size-4" />
            Esci
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
