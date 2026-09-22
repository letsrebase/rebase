import type { ColumnDef } from '@tanstack/react-table'
import { MailPlus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { RowActions } from '@/components/RowActions'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { DataTable, type DataTableFeatures } from '@/components/DataTable'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { roleLabel } from '@/lib/roles'
import {
  useInviteUser,
  usePendingInvites,
  useResendInvite,
  useRevokeInvite,
  useUpdateUser,
  useUsers,
  type InvitationRecord,
  type UserRecord,
} from './queries'

const ROLES: { value: UserRecord['ruolo']; label: string }[] = [
  { value: 'admin', label: 'Amministratore' },
  { value: 'collaboratore', label: 'Collaboratore' },
  { value: 'readonly', label: 'Sola lettura' },
]

const KNOWN_FIELDS = ['email', 'nome', 'ruolo']

/** «Scade il…»: the row's own `expires_at` as date and short time, the reader being
 *  when the link stops working (spec §2's seven-day window made visible). */
const expiry = new Intl.DateTimeFormat('it-IT', { dateStyle: 'short', timeStyle: 'short' })

function unattributed(problem: ProblemDetail | null): string | null {
  if (!problem) return null
  const fieldError = fieldErrorFrom(problem)
  if (fieldError && KNOWN_FIELDS.includes(fieldError.field)) return null
  return problem.detail
}

export function UsersPanel() {
  const { user: currentUser } = useAuth()
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState('')
  const [nome, setNome] = useState('')
  const [ruolo, setRuolo] = useState<UserRecord['ruolo']>('collaboratore')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const users = useUsers()
  const invites = usePendingInvites()
  const invite = useInviteUser()
  const resend = useResendInvite()
  const revoke = useRevokeInvite()
  const update = useUpdateUser()

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = unattributed(problem)

  function openDialog() {
    setProblem(null)
    setEmail('')
    setNome('')
    setRuolo('collaboratore')
    setOpen(true)
  }

  function submit() {
    setProblem(null)
    invite.mutate(
      // The empty optional name is sent as null, the shape the API's own schema
      // answers `InvitationCreate.nome` with: the acceptance page asks for one when
      // the invitation carried none (spec §1), so "blank" here means "not known yet".
      { email, nome: nome.trim() === '' ? null : nome.trim(), ruolo },
      {
        onSuccess: () => {
          toast.success('Invito inviato')
          setOpen(false)
        },
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  const columns: ColumnDef<DataTableFeatures, UserRecord>[] = [
    { header: 'Nome', accessorKey: 'nome' },
    { header: 'Email', accessorKey: 'email' },
    {
      header: 'Ruolo',
      id: 'ruolo',
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
            value={user.ruolo}
            disabled={isSelf || update.isPending}
            onValueChange={(value) =>
              update.mutate(
                { userId: user.id, body: { ruolo: value } },
                {
                  onSuccess: () => toast.success('Ruolo aggiornato'),
                  onError: (error) => toast.error(toProblem(error).detail),
                },
              )
            }
          >
            <SelectTrigger
              className="w-40"
              aria-label={`Ruolo di ${user.nome}`}
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
    {
      header: 'Stato',
      id: 'attivo',
      // The same pill every state in the product goes through (design spec §4). `ink`
      // for the settled, ordinary state and `muted` for one that claims nothing: a
      // deactivated account is not an error, so it is not `danger`.
      cell: (info) => (
        <StatusPill tone={info.row.original.attivo ? 'ink' : 'muted'}>
          {info.row.original.attivo ? 'Attivo' : 'Disattivato'}
        </StatusPill>
      ),
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
            label={`Azioni per ${user.nome}`}
            items={[
              {
                label: user.attivo ? 'Disattiva' : 'Riattiva',
                disabled: update.isPending || (user.attivo && isSelf),
                onSelect: () =>
                  update.mutate(
                    { userId: user.id, body: { attivo: !user.attivo } },
                    {
                      onSuccess: () =>
                        toast.success(user.attivo ? 'Utente disattivato' : 'Utente riattivato'),
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
      cell: (info) => info.row.original.nome ?? info.row.original.email,
    },
    { header: 'Email', accessorKey: 'email' },
    {
      header: 'Ruolo',
      accessorKey: 'ruolo',
      cell: (info) => roleLabel(info.row.original.ruolo),
    },
    {
      header: 'Scadenza',
      id: 'scadenza',
      cell: (info) => expiry.format(new Date(info.row.original.expires_at)),
    },
    {
      header: '',
      id: 'actions',
      meta: { align: 'right' },
      cell: (info) => {
        const row = info.row.original
        const label = row.nome ?? row.email
        return (
          <RowActions
            label={`Azioni per ${label}`}
            items={[
              {
                label: 'Reinvia il link',
                disabled: resend.isPending,
                onSelect: () =>
                  resend.mutate(row.id, {
                    onSuccess: () => toast.success('Invito reinviato'),
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
        <Button onClick={openDialog}>
          <MailPlus className="mr-2 size-4" />
          Invita
        </Button>
      </div>

      <DataTable
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
          columns={inviteColumns}
          data={invites.data ?? []}
          isLoading={invites.isLoading}
          isError={invites.isError}
          error={invites.error}
          emptyMessage="Nessun invito in attesa."
        />
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invita</DialogTitle>
            <DialogDescription>
              Riceverà una mail con un link che vale 7 giorni e funziona una volta sola: niente
              password da comunicare tu. Il nome è opzionale, la pagina dell&apos;invito glielo
              chiede se non lo inserisci.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {banner && (
              <p
                role="alert"
                className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {banner}
              </p>
            )}
            <div className="space-y-2">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                aria-invalid={fieldError?.field === 'email'}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              {fieldError?.field === 'email' && (
                <p className="text-sm text-destructive">{fieldError.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="invite-nome">Nome (opzionale)</Label>
              <Input
                id="invite-nome"
                aria-invalid={fieldError?.field === 'nome'}
                value={nome}
                onChange={(event) => setNome(event.target.value)}
              />
              {fieldError?.field === 'nome' && (
                <p className="text-sm text-destructive">{fieldError.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="invite-ruolo">Ruolo</Label>
              <Select value={ruolo} onValueChange={(value) => setRuolo(value as typeof ruolo)}>
                <SelectTrigger id="invite-ruolo" className="w-full">
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
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Annulla
            </Button>
            <Button onClick={submit} disabled={invite.isPending}>
              {invite.isPending ? 'Invio…' : 'Invia invito'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
