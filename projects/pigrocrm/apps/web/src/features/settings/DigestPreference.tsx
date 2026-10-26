import { toast } from '@rebase/ui/sonner'
import { toProblem } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@rebase/ui/cn'
import { useUpdateMe } from './queries'

/**
 * A switch, hand-rolled rather than imported: same reasoning as `AutomationsPanel`'s
 * `RuleSwitch` -- `components/ui/` has no `switch.tsx`, and adding one means running
 * the shadcn generator for a single control. `role="switch"` with `aria-checked` is
 * what a screen reader and `getByRole('switch')`/`toBeChecked()` both read.
 *
 * Not shared with any other panel: this preference has exactly one home (REB-221's
 * second round moved it to Profilo from `UsersPanel`, the admin-only users list, and
 * REB-735 moved it on to Notifiche), so there is nothing yet to factor out into
 * `components/ui/`.
 */
function ReportSwitch({
  checked,
  disabled,
  onToggle,
}: {
  checked: boolean
  disabled: boolean
  onToggle: (next: boolean) => void
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label="Ricevi il resoconto settimanale"
      disabled={disabled}
      onClick={() => onToggle(!checked)}
      className={cn(
        'relative inline-flex h-5 w-9 shrink-0 items-center border border-input transition-colors disabled:opacity-50',
        checked ? 'bg-primary' : 'bg-muted',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          // Off, the knob is the ink square the canvas draws on the left of the
          // bordered track; on, it is the white one over the primary fill. A white
          // knob on the Paper track is invisible, which is what `bg-card` was.
          'inline-block size-4 transition-transform',
          checked ? 'translate-x-4 bg-card' : 'translate-x-0.5 bg-foreground',
        )}
      />
    </button>
  )
}

/**
 * The weekly digest switch: the one account preference a person changes about themselves
 * besides the reminders. It lives in Notifiche (REB-735), where the digest mail's own
 * opt-out link lands (spec 2026-09-16 §3.6), and calls nothing but `PATCH /api/auth/me`,
 * which touches the caller's own row and nobody else's, so every role may use it.
 */
export function DigestPreference() {
  const { user } = useAuth()
  const update = useUpdateMe()
  if (!user) return null

  return (
    <div className="flex items-start justify-between gap-4 border p-4">
      <div className="space-y-1">
        <p className="text-sm font-medium">Ricevi il resoconto settimanale</p>
        <p className="text-sm text-muted-foreground">
          Ogni lunedì alle 8 ricevi la settimana appena chiusa: fatture, incassi, ore, pipeline.
        </p>
      </div>
      <ReportSwitch
        checked={user.digest_settimanale}
        disabled={update.isPending}
        onToggle={(next) =>
          update.mutate(
            { digest_settimanale: next },
            {
              onSuccess: () => toast.success('Resoconto settimanale aggiornato'),
              onError: (error) => toast.error(toProblem(error).detail),
            },
          )
        }
      />
    </div>
  )
}
