import { Link } from '@tanstack/react-router'
import { CalendarPlus } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { Button } from '@rebase/ui/button'
import { useCanWrite } from '@/lib/auth'

/** Whether a key press is meant for a field, a menu or a dialog and not for the page. */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return (
    target.isContentEditable ||
    target.closest('input, textarea, select, [role="combobox"], [role="listbox"], [role="dialog"]') !== null
  )
}

/**
 * The Home's way into «Registra una giornata»: a button, and the `N` key (REB-745).
 *
 * In mastro recording today's day was one key from the home, and the key stays: `N`, with
 * no modifier, anywhere on the page that is not a field or a dialog. It is drawn on the
 * button so it can be found, and nothing happens for a readonly role, which has no day to
 * record. Rendered only by the dashboard, so the key is a shortcut of the Home and not of
 * every screen.
 */
export function RecordDayShortcut() {
  const canWrite = useCanWrite()
  // The key presses the button's own link, so the navigation is the router's own and this
  // component needs no router hook of its own.
  const link = useRef<HTMLAnchorElement>(null)

  useEffect(() => {
    if (!canWrite) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key.toLowerCase() !== 'n') return
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey || event.repeat) return
      if (isTypingTarget(event.target)) return
      event.preventDefault()
      link.current?.click()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [canWrite])

  if (!canWrite) return null
  return (
    <div className="mb-6">
      <Button asChild variant="outline" size="lg" className="w-full sm:w-auto">
        <Link ref={link} to="/app/days/new">
          <CalendarPlus className="mr-2 size-4" aria-hidden />
          Registra una giornata
          <kbd
            aria-hidden="true"
            className="ml-3 hidden border px-1.5 font-mono text-xs text-muted-foreground sm:inline"
          >
            N
          </kbd>
        </Link>
      </Button>
    </div>
  )
}
