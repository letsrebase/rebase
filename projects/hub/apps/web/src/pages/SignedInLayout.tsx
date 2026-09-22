import { Link, Outlet, useNavigate } from '@tanstack/react-router'
import { Menu } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { SidebarNav } from '@/components/SidebarNav'
import { useMediaQuery } from '@/hooks/use-media-query'
import { Button } from '@rebase/ui/button'
import { Sheet, SheetContent, SheetTrigger } from '@rebase/ui/sheet'
import { useIdentify } from '@/lib/analytics'
import { useMe } from '@/lib/me'

/** The same `lg` line the CRM's `AppShell` draws its sidebar/overlay boundary at: below
 *  it the sidebar is not a column beside the content, it is a menu over it (REB-316). */
const DESKTOP = '(min-width: 1024px)'

/**
 * The one guard and the one frame every signed-in route passes through (REB-279):
 * `AdminLayout.tsx`'s dark sidebar and `pages/member/Guard.tsx`'s `MemberGuard`
 * collapse into this one component, reading `useMe()`. Nothing renders while the
 * session is unknown, a signed-out visitor goes to `/accedi`, and the admin group of
 * the sidebar shows only for `role === 'admin'`. `/admin/*`'s own access rule lives
 * one level further in, `AdminGuard`: a signed-in non-admin still belongs in this same
 * frame, just not in that section of it, which is what closes the old empty-frame bug
 * (REB-106) as a side effect of there being one guard here instead of two.
 *
 * REB-316 adds the small-screen shape, on the treatment the CRM's shell (#228) already
 * chose for this exact problem -- "272px of the 390px a phone has is not a layout, it is
 * a menu" -- rendered here through the system's own `Sheet` rather than a hand-rolled
 * overlay: below `lg` the sidebar leaves the layout entirely (so the content column
 * gets the full width), a bar at the top of the page carries the brand and a 44px menu
 * trigger, and the drawer is the same `SidebarNav` on the `bg-sidebar` slots with every
 * row lifted to its 44px touch target. The desktop shape renders exactly the DOM it
 * rendered before.
 */
export function SignedInLayout() {
  const me = useMe()
  const navigate = useNavigate()
  useIdentify(me.data, me.data?.role)

  const isDesktop = useMediaQuery(DESKTOP)
  // Local, unpersisted UI state, same as the CRM's shell: which way the sidebar is
  // showing is a per-visit convenience, not a setting. Tapping a destination closes
  // the drawer through `SidebarNav`'s `onNavigate`; Escape and a scrim tap come from
  // Radix's own dialog dismissal, so there is no second mechanism to keep in sync.
  const [drawerOpen, setDrawerOpen] = useState(false)

  useEffect(() => {
    if (!me.isPending && me.data === null) void navigate({ to: '/login', replace: true })
  }, [me.isPending, me.data, navigate])

  if (me.isPending) return <p className="p-8 text-sm text-muted-foreground">Caricamento…</p>
  if (!me.data) return null
  const isAdmin = me.data.role === 'admin'

  return (
    <div className="flex h-full overflow-hidden">
      {/* The sidebar reads the sidebar slots, not the raw palette: `--sidebar` is the
         same Prussian Blue and `--sidebar-foreground` the same Paper, and the record's
         Sidebar paragraph is what points them. The CRM's shell (#228) draws the same
         pair from the same tokens. Desktop only: below `lg` the aside is not rendered
         at all, so the content column gets the phone's whole width. */}
      {isDesktop && (
        <aside className="flex h-full w-56 shrink-0 flex-col gap-6 overflow-y-auto bg-sidebar p-4 text-sidebar-foreground">
          <SidebarNav me={me.data} isAdmin={isAdmin} />
        </aside>
      )}
      <Sheet open={isDesktop ? false : drawerOpen} onOpenChange={setDrawerOpen}>
        {/* The page: white from the sidebar's edge to the window's, no inset frame and
           no border of its own (REB-348, mirroring the CRM's own `AppShell`, REB-328).
           The 12px grid-ground margin and the `lg:border` were copied from the CRM's
           old panel and read as a card scrolling on its own inside the page. `<main>`
           is the one scroller, which the root's `overflow-hidden` guarantees; pages
           carry their own padding. */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {/* The mobile bar lives above the page, so it reads as the top of the page
             and not a band of grid ground across the phone. Trigger and brand link
             each carry their 44px target (REB-316). */}
          {!isDesktop && (
            <header className="flex shrink-0 items-center gap-1 border-b border-border bg-card p-2">
              <SheetTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label="Apri il menu"
                  className="size-11"
                >
                  <Menu className="size-5" aria-hidden="true" />
                </Button>
              </SheetTrigger>
              <Link
                to="/me"
                className="inline-flex min-h-11 items-center gap-2.5 px-1 font-semibold"
              >
                <BrandMark className="size-3.5" />
                rebase
              </Link>
            </header>
          )}
          {/* No padding on `main`: each page insets itself, the admin pages with their
             own `px-6` rows and the member pages with a `p-6` on their root. */}
          <main className="min-h-0 flex-1 overflow-y-auto bg-card">
            <Outlet />
          </main>
        </div>
        {/* The drawer: the system's left `Sheet` (the shape the gallery renders for it),
            on the sidebar's own slots. `w-3/4` and the `sm` cap are the primitive's, so
            the drawer's width is the system's; the close button it would carry at 32px
            is switched off because the card's bar is 44px on every control -- the scrim,
            Escape and tapping a destination are the three ways out, and the trigger that
            opened it is the fourth. `aria-label` stands in for a title: the drawer *is*
            the navigation, and the nav's own landmark says so. */}
        {!isDesktop && (
          <SheetContent
            side="left"
            showCloseButton={false}
            aria-label="Menu di navigazione"
            className="gap-6 bg-sidebar p-4 text-sidebar-foreground"
          >
            <SidebarNav
              me={me.data}
              isAdmin={isAdmin}
              drawer
              onNavigate={() => setDrawerOpen(false)}
            />
          </SheetContent>
        )}
      </Sheet>
    </div>
  )
}
