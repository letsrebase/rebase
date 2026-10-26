import { useAuth } from '@/lib/auth'
import { roleLabel } from '@/lib/roles'

/**
 * A person's own account: name, email and role as plain text -- there is no form here,
 * because changing any of the three needs either a new password flow this product does
 * not have yet (email) or an administrator (role, nome). What a person may change about
 * themselves, the weekly digest and the reminders, is Notifiche's (REB-735).
 *
 * Reachable by every signed-in user, not only an admin: `SettingsLayout` exempts this
 * tab from its blanket admin gate. Nothing on this panel calls `/api/users` -- that
 * endpoint is `require_admin` end to end.
 */
export function ProfilePanel() {
  const { user } = useAuth()

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
    </div>
  )
}
