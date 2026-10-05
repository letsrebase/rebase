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
import { Checkbox } from '@rebase/ui/checkbox'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useLegalEntity, type LegalEntityRecord } from '@/lib/legalEntity'
import { scopeBody, scopeLabel } from './scope'
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

const KNOWN_FIELDS = ['email', 'nome', 'ruolo', 'aziende']

function LegalEntitiesChecklist({
  aziende,
  checked,
  onChange,
  idPrefix,
  error,
}: {
  aziende: LegalEntityRecord[]
  checked: string[]
  onChange: (next: string[]) => void
  idPrefix: string
  error?: string
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Aziende</legend>
      <p className="text-sm text-muted-foreground">
        Vedrà solo le aziende selezionate: clienti, deal, documenti, fatture e cruscotti. Tutte
        selezionate significa tutto lo spazio, anche le aziende che aggiungerai.
      </p>
      {aziende.map((azienda) => {
        const id = `${idPrefix}-${azienda.id}`
        return (
          <div key={azienda.id} className="flex items-center gap-2">
            <Checkbox
              id={id}
              checked={checked.includes(azienda.id)}
              onCheckedChange={(value) =>
                onChange(
                  value === true
                    ? [...checked, azienda.id]
                    : checked.filter((item) => item !== azienda.id),
                )
              }
            />
            <Label htmlFor={id}>{azienda.nome}</Label>
          </div>
        )
      })}
      {error && <p className="text-sm text-destructive">{error}</p>}
    </fieldset>
  )
}

/** «gg/mm/aaaa, hh:mm»: date and short time, shared by the invitation's «Scade il…»
 *  (spec §2's seven-day window made visible) and the member's «Ultimo accesso»
 *  (REB-297) -- one instant read the same way everywhere this panel shows one. */
const shortDateTime = new Intl.DateTimeFormat('it-IT', { dateStyle: 'short', timeStyle: 'short' })

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
  // The scope (REB-635): the checked aziende of the invite, and the member whose scope
  // is being edited with theirs. Drawn from the second azienda on, like the selector.
  const azienda = useLegalEntity()
  const several = azienda.aziende.length > 1
  const [inviteAziende, setInviteAziende] = useState<string[]>([])
  const [scopeOf, setScopeOf] = useState<UserRecord | null>(null)
  const [scopeAziende, setScopeAziende] = useState<string[]>([])
  const [scopeProblem, setScopeProblem] = useState<ProblemDetail | null>(null)
  const byId = (id: string) => azienda.byId(id)

  const users = useUsers()
  const invites = usePendingInvites()
  const invite = useInviteUser()
  const resend = useResendInvite()
  const revoke = useRevokeInvite()
  const update = useUpdateUser()
  // The scope column and the row action are drawn from the second azienda on, and
  // also in a one-azienda space while somebody still carries a scope: a member whose
  // aziende were all deactivated sees nothing, and this is where an admin reads
  // «nessuna azienda attiva» and widens them back.
  const showScope =
    several ||
    (users.data ?? []).some((u) => Array.isArray(u.aziende)) ||
    (invites.data ?? []).some((i) => Array.isArray(i.aziende))

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = unattributed(problem)

  function openDialog() {
    setProblem(null)
    setEmail('')
    setNome('')
    setRuolo('collaboratore')
    setInviteAziende(azienda.aziende.map((a) => a.id))
    setOpen(true)
  }

  function openScope(user: UserRecord) {
    setScopeProblem(null)
    setScopeOf(user)
    // Only the ids the checklist draws: an azienda deactivated since the scope was set
    // is not there to uncheck, and sending it back would be refused as inactive.
    setScopeAziende(
      Array.isArray(user.aziende)
        ? user.aziende.filter((id) => azienda.byId(id) !== undefined)
        : azienda.aziende.map((a) => a.id),
    )
  }

  function saveScope() {
    if (!scopeOf) return
    setScopeProblem(null)
    update.mutate(
      { userId: scopeOf.id, body: { aziende: scopeBody(scopeAziende, azienda.aziende) } },
      {
        onSuccess: () => {
          toast.success('Aziende aggiornate')
          setScopeOf(null)
        },
        onError: (error) => setScopeProblem(toProblem(error)),
      },
    )
  }

  function submit() {
    setProblem(null)
    invite.mutate(
      // The empty optional name is sent as null, the shape the API's own schema
      // answers `InvitationCreate.nome` with: the acceptance page asks for one when
      // the invitation carried none (spec §1), so "blank" here means "not known yet".
      // The scope only from the second azienda on: a one-azienda space sends none.
      {
        email,
        nome: nome.trim() === '' ? null : nome.trim(),
        ruolo,
        ...(several ? { aziende: scopeBody(inviteAziende, azienda.aziende) } : {}),
      },
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
    ...(showScope
      ? [
          {
            header: 'Aziende',
            id: 'aziende',
            cell: (info) => scopeLabel(info.row.original.aziende, byId),
          } satisfies ColumnDef<DataTableFeatures, UserRecord>,
        ]
      : []),
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
      header: 'Ultimo accesso',
      id: 'ultimo_accesso',
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
            label={`Azioni per ${user.nome}`}
            items={[
              ...(showScope
                ? [
                    {
                      label: 'Aziende…',
                      disabled: update.isPending,
                      onSelect: () => openScope(user),
                    },
                  ]
                : []),
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
    ...(showScope
      ? [
          {
            header: 'Aziende',
            id: 'aziende',
            cell: (info) => scopeLabel(info.row.original.aziende, byId),
          } satisfies ColumnDef<DataTableFeatures, InvitationRecord>,
        ]
      : []),
    {
      header: 'Scadenza',
      id: 'scadenza',
      cell: (info) => shortDateTime.format(new Date(info.row.original.expires_at)),
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
            {several && (
              <LegalEntitiesChecklist
                aziende={azienda.aziende}
                checked={inviteAziende}
                onChange={setInviteAziende}
                idPrefix="invite-azienda"
                error={fieldError?.field === 'aziende' ? fieldError.message : undefined}
              />
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Annulla
            </Button>
            <Button
              onClick={submit}
              disabled={invite.isPending || (several && inviteAziende.length === 0)}
            >
              {invite.isPending ? 'Invio…' : 'Invia invito'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={scopeOf !== null} onOpenChange={(next) => !next && setScopeOf(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Aziende di {scopeOf?.nome}</DialogTitle>
            <DialogDescription>
              La modifica vale dalla prossima richiesta. Lo spazio tiene sempre almeno un
              amministratore che vede tutte le aziende.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {scopeProblem && fieldErrorFrom(scopeProblem)?.field !== 'aziende' && (
              <p
                role="alert"
                className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {scopeProblem.detail}
              </p>
            )}
            <LegalEntitiesChecklist
              aziende={azienda.aziende}
              checked={scopeAziende}
              onChange={setScopeAziende}
              idPrefix="scope-azienda"
              error={
                scopeProblem && fieldErrorFrom(scopeProblem)?.field === 'aziende'
                  ? fieldErrorFrom(scopeProblem)?.message
                  : undefined
              }
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setScopeOf(null)}>
              Annulla
            </Button>
            <Button onClick={saveScope} disabled={update.isPending || scopeAziende.length === 0}>
              {update.isPending ? 'Salvataggio…' : 'Salva'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
