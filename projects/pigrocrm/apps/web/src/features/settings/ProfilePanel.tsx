import { toast } from 'sonner'
import { toProblem } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { roleLabel } from '@/lib/roles'
import { cn } from '@rebase/ui/cn'
import { useUpdateMe } from './queries'

/**
 * A switch, hand-rolled rather than imported: same reasoning as `AutomationsPanel`'s
 * `RuleSwitch` -- `components/ui/` has no `switch.tsx`, and adding one means running
 * the shadcn generator for a single control. `role="switch"` with `aria-checked` is
 * what a screen reader and `getByRole('switch')`/`toBeChecked()` both read.
 *
 * Not shared with any other panel: this preference has exactly one home now (REB-221's
 * second round moved it here from `UsersPanel`, the admin-only users list), so there is
 * nothing yet to factor out into `components/ui/`.
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
        'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50',
        checked ? 'bg-primary' : 'bg-muted',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-block size-4 rounded-full bg-card transition-transform',
          checked ? 'translate-x-4' : 'translate-x-0.5',
        )}
      />
    </button>
  )
}

/**
 * A person's own account: name, email and role as plain text -- there is no form here,
 * because changing any of the three needs either a new password flow this product does
 * not have yet (email) or an administrator (role, nome) -- and the one thing this screen
 * does let a person change about themselves, the weekly digest.
 *
 * Reachable by every signed-in user, not only an admin: `SettingsLayout` exempts this
 * one tab from its blanket admin gate, and the digest mail's own opt-out link (spec
 * 2026-09-16 §3.6) points here. Nothing on this panel calls `/api/users` -- that
 * endpoint is `require_admin` end to end -- only `PATCH /api/auth/me`, which touches
 * the caller's own row and nobody else's.
 */
export function ProfilePanel() {
  const { user } = useAuth()
  const update = useUpdateMe()

  // `routes/app.tsx`'s own guard already keeps an unauthenticated visitor from
  // reaching this component at all, so `user` is null only for the one render before
  // that guard has resolved.
  if (!user) return null

  return (
    <div className="max-w-xl space-y-6">
      <div>
        <h2 className="font-semibold">Profilo</h2>
        <p className="text-sm text-muted-foreground">Il tuo account su questo spazio.</p>
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <dt className="text-muted-foreground">Nome</dt>
        <dd>{user.nome}</dd>
        <dt className="text-muted-foreground">Email</dt>
        <dd>{user.email}</dd>
        <dt className="text-muted-foreground">Ruolo</dt>
        <dd>{roleLabel(user.ruolo)}</dd>
      </dl>

      <div className="flex items-start justify-between gap-4 rounded-lg border p-4">
        <div className="space-y-1">
          <p className="text-sm font-medium">Ricevi il resoconto settimanale</p>
          <p className="text-sm text-muted-foreground">
            Ogni lunedì alle 8 ricevi la settimana appena chiusa: fatture, incassi, ore,
            pipeline.
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
    </div>
  )
}
