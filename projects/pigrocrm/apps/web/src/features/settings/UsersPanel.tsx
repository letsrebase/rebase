import type { ColumnDef } from '@tanstack/react-table'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
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
import { useCreateUser, useUpdateUser, useUsers, type UserRecord } from './queries'

const ROLES: { value: UserRecord['ruolo']; label: string }[] = [
  { value: 'admin', label: 'Amministratore' },
  { value: 'collaboratore', label: 'Collaboratore' },
  { value: 'readonly', label: 'Sola lettura' },
]

const KNOWN_FIELDS = ['email', 'password', 'nome', 'ruolo']

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
  const [password, setPassword] = useState('')
  const [ruolo, setRuolo] = useState<UserRecord['ruolo']>('collaboratore')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const users = useUsers()
  const create = useCreateUser()
  const update = useUpdateUser()

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = unattributed(problem)

  function openDialog() {
    setProblem(null)
    setEmail('')
    setNome('')
    setPassword('')
    setRuolo('collaboratore')
    setOpen(true)
  }

  function submit() {
    setProblem(null)
    create.mutate(
      { email, nome, password, ruolo },
      {
        onSuccess: () => {
          toast.success('Utente creato')
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

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold">Utenti</h2>
          <p className="text-sm text-muted-foreground">
            Non esiste registrazione pubblica: gli utenti li crei tu. Il cambio password non è
            disponibile in questa versione — disattiva e ricrea l&apos;utente se serve.
          </p>
        </div>
        <Button onClick={openDialog}>
          <Plus className="mr-2 size-4" />
          Nuovo utente
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

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Nuovo utente</DialogTitle>
            <DialogDescription>
              La password deve avere almeno 10 caratteri. Comunicala tu all&apos;utente: il sistema
              non invia email in questa versione.
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
              <Label htmlFor="user-nome">Nome</Label>
              <Input
                id="user-nome"
                aria-invalid={fieldError?.field === 'nome'}
                value={nome}
                onChange={(event) => setNome(event.target.value)}
              />
              {fieldError?.field === 'nome' && (
                <p className="text-sm text-destructive">{fieldError.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="user-email">Email</Label>
              <Input
                id="user-email"
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
              <Label htmlFor="user-password">Password</Label>
              <Input
                id="user-password"
                type="password"
                aria-invalid={fieldError?.field === 'password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              {fieldError?.field === 'password' && (
                <p className="text-sm text-destructive">{fieldError.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="user-ruolo">Ruolo</Label>
              <Select value={ruolo} onValueChange={(value) => setRuolo(value as typeof ruolo)}>
                <SelectTrigger id="user-ruolo" className="w-full">
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
            <Button onClick={submit} disabled={create.isPending}>
              {create.isPending ? 'Creazione…' : 'Crea'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
