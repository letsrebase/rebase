import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
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
import { useLegalEntity, type LegalEntityRecord } from '@/lib/legalEntity'
import { scopeBody } from './scope'
import { ROLES } from './userRoles'
import { useInviteUser, useUpdateUser, type UserRecord } from './queries'

/**
 * The three dialogs of the Utenti panel -- invite, rename, change the aziende of a member --
 * each with its own form state. The panel mounts one only while it is open, so every
 * opening starts from the member's current values, and passes the panel's `update` mutation
 * to the two that edit a member, so a pending change on any row disables both Save buttons.
 */

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

function unattributed(problem: ProblemDetail | null): string | null {
  if (!problem) return null
  const fieldError = fieldErrorFrom(problem)
  if (fieldError && KNOWN_FIELDS.includes(fieldError.field)) return null
  return problem.detail
}

/** «Invita»: the address, an optional name, the role and, from the second azienda on,
 *  the aziende the invited person will see. */
export function InviteUserDialog({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState('')
  const [nome, setNome] = useState('')
  const [ruolo, setRuolo] = useState<UserRecord['ruolo']>('collaboratore')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  // The scope (REB-635): the checked aziende of the invite. Drawn from the second azienda
  // on, like the selector.
  const azienda = useLegalEntity()
  const several = azienda.aziende.length > 1
  const [inviteAziende, setInviteAziende] = useState<string[]>(() =>
    azienda.aziende.map((a) => a.id),
  )
  const invite = useInviteUser()

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = unattributed(problem)

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
        onSuccess: (sent) => {
          // The invitation exists either way; what the toast owes the admin is whether
          // the mail left (REB-678: it used to say «inviato» for one that never did).
          if (sent.mail_status === 'failed') {
            toast.error(
              'Invito creato, ma la mail non è partita. Usa «Reinvia il link» nella lista.',
            )
          } else {
            toast.success('Invito inviato')
          }
          onClose()
        },
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
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
          <Button variant="ghost" onClick={onClose}>
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
  )
}

/** «Nome di …»: corrects a member's name (REB-796): a space's founder is stored under
 *  whatever the signup form asked for, often the space's own name, and that is the name
 *  every assignee and owner picker then shows. */
export function RenameUserDialog({
  user,
  update,
  onClose,
}: {
  user: UserRecord
  update: ReturnType<typeof useUpdateUser>
  onClose: () => void
}) {
  const [renameNome, setRenameNome] = useState(user.nome)
  const [renameProblem, setRenameProblem] = useState<ProblemDetail | null>(null)

  function saveRename() {
    setRenameProblem(null)
    update.mutate(
      { userId: user.id, body: { nome: renameNome.trim() } },
      {
        onSuccess: () => {
          toast.success('Nome aggiornato')
          onClose()
        },
        onError: (error) => setRenameProblem(toProblem(error)),
      },
    )
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nome di {user.nome}</DialogTitle>
          <DialogDescription>
            È il nome che compare nei selettori di assegnatario e responsabile e nello storico.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="rename-nome">Nome</Label>
          <Input
            id="rename-nome"
            aria-invalid={renameProblem !== null}
            aria-describedby={renameProblem ? 'rename-nome-error' : undefined}
            value={renameNome}
            onChange={(event) => setRenameNome(event.target.value)}
          />
          {renameProblem && (
            <p id="rename-nome-error" role="alert" className="text-sm text-destructive">
              {renameProblem.detail}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Annulla
          </Button>
          <Button onClick={saveRename} disabled={update.isPending || renameNome.trim() === ''}>
            {update.isPending ? 'Salvataggio…' : 'Salva'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** «Aziende di …»: widens or narrows the aziende a member sees. */
export function ScopeUserDialog({
  user,
  update,
  onClose,
}: {
  user: UserRecord
  update: ReturnType<typeof useUpdateUser>
  onClose: () => void
}) {
  const azienda = useLegalEntity()
  // Only the ids the checklist draws: an azienda deactivated since the scope was set
  // is not there to uncheck, and sending it back would be refused as inactive.
  const [scopeAziende, setScopeAziende] = useState<string[]>(() =>
    Array.isArray(user.aziende)
      ? user.aziende.filter((id) => azienda.byId(id) !== undefined)
      : azienda.aziende.map((a) => a.id),
  )
  const [scopeProblem, setScopeProblem] = useState<ProblemDetail | null>(null)

  function saveScope() {
    setScopeProblem(null)
    update.mutate(
      { userId: user.id, body: { aziende: scopeBody(scopeAziende, azienda.aziende) } },
      {
        onSuccess: () => {
          toast.success('Aziende aggiornate')
          onClose()
        },
        onError: (error) => setScopeProblem(toProblem(error)),
      },
    )
  }

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Aziende di {user.nome}</DialogTitle>
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
          <Button variant="ghost" onClick={onClose}>
            Annulla
          </Button>
          <Button onClick={saveScope} disabled={update.isPending || scopeAziende.length === 0}>
            {update.isPending ? 'Salvataggio…' : 'Salva'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
