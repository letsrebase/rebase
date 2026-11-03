import type { ColumnDef } from '@tanstack/react-table'
import { MailPlus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { RowActions } from '@/components/RowActions'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { DataTable, type DataTableFeatures } from '@/components/DataTable'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { toProblem } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { scopeLabel } from './scope'
import { MAIL_HINT, MAIL_LABEL, MAIL_TONE } from './inviteMail'
import { roleLabel } from '@/lib/roles'
import {
  usePendingInvites,
  useResendInvite,
  useRevokeInvite,
  useUpdateUser,
  useUsers,
  type InvitationRecord,
  type UserRecord,
} from './queries'
import { InviteUserDialog, RenameUserDialog, ScopeUserDialog } from './UserDialogs'
import { ROLES } from './userRoles'

/** «gg/mm/aaaa, hh:mm»: date and short time, shared by the invitation's «Scade il…»
 *  (spec §2's seven-day window made visible) and the member's «Ultimo accesso»
 *  (REB-297) -- one instant read the same way everywhere this panel shows one. */
const shortDateTime = new Intl.DateTimeFormat('it-IT', { dateStyle: 'short', timeStyle: 'short' })

export function UsersPanel() {
  const { user: currentUser } = useAuth()
  // Each dialog is mounted while it is open, with its own form state: `inviting`, and the
  // member whose name is being corrected (REB-796) or whose legal entities are being edited
  // (REB-635), which is drawn from the second legal entity on, like the selector.
  const [inviting, setInviting] = useState(false)
  const [renameOf, setRenameOf] = useState<UserRecord | null>(null)
  const [scopeOf, setScopeOf] = useState<UserRecord | null>(null)
  const legal_entity = useLegalEntity()
  const several = legal_entity.legal_entities.length > 1
  const byId = (id: string) => legal_entity.byId(id)

  const users = useUsers()
  const invites = usePendingInvites()
  const resend = useResendInvite()
  const revoke = useRevokeInvite()
  const update = useUpdateUser()
  // The scope column and the row action are drawn from the second legal entity on, and
  // also in a one-legal-entity space while somebody still carries a scope: a member whose
  // legal entities were all deactivated sees nothing, and this is where an admin reads
  // «nessuna azienda attiva» and widens them back.
  const showScope =
    several ||
    (users.data ?? []).some((u) => Array.isArray(u.legal_entities)) ||
    (invites.data ?? []).some((i) => Array.isArray(i.legal_entities))

  const columns: ColumnDef<DataTableFeatures, UserRecord>[] = [
    { header: 'Nome', accessorKey: 'name' },
    { header: 'Email', accessorKey: 'email' },
    {
      header: 'Ruolo',
      id: 'role',
      cell: (info) => {
        const user = info.row.original
        // Disabled for your own row: nothing server-side stops the last
        // admin from demoting themselves (`UserService.update` has no such
        // check), and there is no undo except the `createadmin` CLI -- the
        // same reasoning as the Disattiva guard below, applied to the other
        // way an admin can lock themselves out of this exact screen.
        const isSelf = user.id === currentUser?.id
        return (
          <Select
            value={user.role}
            disabled={isSelf || update.isPending}
            onValueChange={(value) =>
              update.mutate(
                { userId: user.id, body: { role: value } },
                {
                  onSuccess: () => toast.success('Ruolo aggiornato'),
                  onError: (error) => toast.error(toProblem(error).detail),
                },
              )
            }
          >
            <SelectTrigger
              className="w-40"
              aria-label={`Ruolo di ${user.name}`}
              title={isSelf ? 'Non puoi cambiare il ruolo del tuo stesso account.' : undefined}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ROLES.map((role) => (
                <SelectItem key={role.value} value={role.value}>
                  {role.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )
      },
    },
    ...(showScope
      ? [
          {
            header: 'Aziende',
            id: 'legal_entities',
            cell: (info) => scopeLabel(info.row.original.legal_entities, byId),
          } satisfies ColumnDef<DataTableFeatures, UserRecord>,
        ]
      : []),
    {
      header: 'Stato',
      id: 'active',
      // The same pill every state in the product goes through (design spec §4). `ink`
      // for the settled, ordinary state and `muted` for one that claims nothing: a
      // deactivated account is not an error, so it is not `danger`.
      cell: (info) => (
        <StatusPill tone={info.row.original.active ? 'ink' : 'muted'}>
          {info.row.original.active ? 'Attivo' : 'Disattivato'}
        </StatusPill>
      ),
    },
    {
      header: 'Ultimo accesso',
      id: 'last_login',
      // `None` until the account's first session -- a password login or an accepted
      // invitation (REB-297) -- so an em dash reads as "never", not as a blank cell
      // that looks like a loading state.
      cell: (info) => {
        const { last_login_at } = info.row.original
        return last_login_at ? shortDateTime.format(new Date(last_login_at)) : '—'
      },
    },
    {
      header: '',
      id: 'actions',
      meta: { align: 'right' },
      cell: (info) => {
        const user = info.row.original
        const isSelf = user.id === currentUser?.id
        // Behind the «⋯» like every other row action in the product (§4). Disabled
        // rather than omitted for your own active row: nothing server-side stops the
        // last admin from locking themselves out (recovery needs the `createadmin`
        // CLI), and an item that disappears from one row is a menu nobody learns.
        return (
          <RowActions
            label={`Azioni per ${user.name}`}
            items={[
              {
                label: 'Rinomina…',
                disabled: update.isPending,
                onSelect: () => setRenameOf(user),
              },
              ...(showScope
                ? [
                    {
                      label: 'Aziende…',
                      disabled: update.isPending,
                      onSelect: () => setScopeOf(user),
                    },
                  ]
                : []),
              {
                label: user.active ? 'Disattiva' : 'Riattiva',
                disabled: update.isPending || (user.active && isSelf),
                onSelect: () =>
                  update.mutate(
                    { userId: user.id, body: { active: !user.active } },
                    {
                      onSuccess: () =>
                        toast.success(user.active ? 'Utente disattivato' : 'Utente riattivato'),
                      onError: (error) => toast.error(toProblem(error).detail),
                    },
                  ),
              },
            ]}
          />
        )
      },
    },
  ]

  const inviteColumns: ColumnDef<DataTableFeatures, InvitationRecord>[] = [
    {
      header: 'Persona',
      id: 'persona',
      // The name the admin carried, when they knew one; the acceptance page asks for
      // the rest (spec §1), so an address alone is a normal row here.
      cell: (info) => info.row.original.name ?? info.row.original.email,
    },
    { header: 'Email', accessorKey: 'email' },
    {
      header: 'Ruolo',
      accessorKey: 'role',
      cell: (info) => roleLabel(info.row.original.role),
    },
    ...(showScope
      ? [
          {
            header: 'Aziende',
            id: 'legal_entities',
            cell: (info) => scopeLabel(info.row.original.legal_entities, byId),
          } satisfies ColumnDef<DataTableFeatures, InvitationRecord>,
        ]
      : []),
    {
      header: 'Mail',
      id: 'mail',
      // `null` is an invitation written before the outcome was recorded: nothing to say.
      cell: (info) => {
        const state = info.row.original.mail_status
        if (!state) return null
        const hint = MAIL_HINT[state]
        return (
          <div className="space-y-1">
            <StatusPill tone={MAIL_TONE[state]}>{MAIL_LABEL[state]}</StatusPill>
            {hint && (
              <p className="max-w-72 whitespace-normal text-xs text-muted-foreground">{hint}</p>
            )}
          </div>
        )
      },
    },
    {
      header: 'Scadenza',
      id: 'due_date',
      cell: (info) => shortDateTime.format(new Date(info.row.original.expires_at)),
    },
    {
      header: '',
      id: 'actions',
      meta: { align: 'right' },
      cell: (info) => {
        const row = info.row.original
        const label = row.name ?? row.email
        return (
          <RowActions
            label={`Azioni per ${label}`}
            items={[
              {
                label: 'Reinvia il link',
                disabled: resend.isPending,
                onSelect: () =>
                  resend.mutate(row.id, {
                    onSuccess: (sent) =>
                      sent.mail_status === 'failed'
                        ? toast.error('La mail non è partita. Riprova tra poco.')
                        : toast.success('Invito reinviato'),
                    onError: (error) => toast.error(toProblem(error).detail),
                  }),
              },
              {
                label: 'Revoca',
                destructive: true,
                disabled: revoke.isPending,
                onSelect: () =>
                  revoke.mutate(row.id, {
                    onSuccess: () => toast.success('Invito revocato'),
                    onError: (error) => toast.error(toProblem(error).detail),
                  }),
              },
            ]}
          />
        )
      },
    },
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold">Utenti</h2>
          <p className="text-sm text-muted-foreground">
            Non esiste registrazione pubblica: le persone entrano con un invito che mandi tu.
            L&apos;invito vale 7 giorni e funziona una volta sola. Il cambio password non è
            disponibile in questa versione — disattiva e invita di nuovo se serve.
          </p>
        </div>
        <Button onClick={() => setInviting(true)}>
          <MailPlus className="mr-2 size-4" />
          Invita
        </Button>
      </div>

      <DataTable
        label="Utenti"
        columns={columns}
        data={users.data ?? []}
        isLoading={users.isLoading}
        isError={users.isError}
        error={users.error}
        emptyMessage="Nessun utente."
      />

      <div>
        <h3 className="mb-2 font-semibold">Inviti in attesa</h3>
        <DataTable
          label="Inviti in attesa"
          columns={inviteColumns}
          data={invites.data ?? []}
          isLoading={invites.isLoading}
          isError={invites.isError}
          error={invites.error}
          emptyMessage="Nessun invito in attesa."
        />
      </div>

      {inviting && <InviteUserDialog onClose={() => setInviting(false)} />}
      {renameOf && (
        <RenameUserDialog user={renameOf} update={update} onClose={() => setRenameOf(null)} />
      )}
      {scopeOf && (
        <ScopeUserDialog user={scopeOf} update={update} onClose={() => setScopeOf(null)} />
      )}
    </div>
  )
}
