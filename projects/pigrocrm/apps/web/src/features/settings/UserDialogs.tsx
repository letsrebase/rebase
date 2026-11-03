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
 * The three dialogs of the Utenti panel -- invite, rename, change the legal entities of a member --
 * each with its own form state. The panel mounts one only while it is open, so every
 * opening starts from the member's current values, and passes the panel's `update` mutation
 * to the two that edit a member, so a pending change on any row disables both Save buttons.
 */

const KNOWN_FIELDS = ['email', 'nome', 'role', 'legal_entities']

function LegalEntitiesChecklist({
  legal_entities,
  checked,
  onChange,
  idPrefix,
  error,
}: {
  legal_entities: LegalEntityRecord[]
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
      {legal_entities.map((legal_entity) => {
        const id = `${idPrefix}-${legal_entity.id}`
        return (
          <div key={legal_entity.id} className="flex items-center gap-2">
            <Checkbox
              id={id}
              checked={checked.includes(legal_entity.id)}
              onCheckedChange={(value) =>
                onChange(
                  value === true
                    ? [...checked, legal_entity.id]
                    : checked.filter((item) => item !== legal_entity.id),
                )
              }
            />
            <Label htmlFor={id}>{legal_entity.nome}</Label>
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

/** «Invita»: the address, an optional name, the role and, from the second legal entity on,
 *  the legal entities the invited person will see. */
export function InviteUserDialog({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState('')
  const [nome, setName] = useState('')
  const [roleValue, setRole] = useState<UserRecord['role']>('collaborator')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  // The scope (REB-635): the checked legal entities of the invite. Drawn from the second legal entity
  // on, like the selector.
  const legal_entity = useLegalEntity()
  const several = legal_entity.legal_entities.length > 1
  const [inviteLegalEntities, setInviteLegalEntities] = useState<string[]>(() =>
    legal_entity.legal_entities.map((a) => a.id),
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
      // The scope only from the second legal entity on: a one-legal-entity space sends none.
      {
        email,
        nome: nome.trim() === '' ? null : nome.trim(),
        role: roleValue,
        ...(several ? { legal_entities: scopeBody(inviteLegalEntities, legal_entity.legal_entities) } : {}),
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
              onChange={(event) => setName(event.target.value)}
            />
            {fieldError?.field === 'nome' && (
              <p className="text-sm text-destructive">{fieldError.message}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="invite-role">Ruolo</Label>
            <Select value={roleValue} onValueChange={(value) => setRole(value as typeof roleValue)}>
              <SelectTrigger id="invite-role" className="w-full">
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
              legal_entities={legal_entity.legal_entities}
              checked={inviteLegalEntities}
              onChange={setInviteLegalEntities}
              idPrefix="invite-legal-entity"
              error={fieldError?.field === 'legal_entities' ? fieldError.message : undefined}
            />
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Annulla
          </Button>
          <Button
            onClick={submit}
            disabled={invite.isPending || (several && inviteLegalEntities.length === 0)}
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
  const [renameName, setRenameName] = useState(user.nome)
  const [renameProblem, setRenameProblem] = useState<ProblemDetail | null>(null)

  function saveRename() {
    setRenameProblem(null)
    update.mutate(
      { userId: user.id, body: { nome: renameName.trim() } },
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
            value={renameName}
            onChange={(event) => setRenameName(event.target.value)}
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
          <Button onClick={saveRename} disabled={update.isPending || renameName.trim() === ''}>
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
  const legal_entity = useLegalEntity()
  // Only the ids the checklist draws: an azienda deactivated since the scope was set
  // is not there to uncheck, and sending it back would be refused as inactive.
  const [scopeLegalEntities, setScopeLegalEntities] = useState<string[]>(() =>
    Array.isArray(user.legal_entities)
      ? user.legal_entities.filter((id) => legal_entity.byId(id) !== undefined)
      : legal_entity.legal_entities.map((a) => a.id),
  )
  const [scopeProblem, setScopeProblem] = useState<ProblemDetail | null>(null)

  function saveScope() {
    setScopeProblem(null)
    update.mutate(
      { userId: user.id, body: { legal_entities: scopeBody(scopeLegalEntities, legal_entity.legal_entities) } },
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
          {scopeProblem && fieldErrorFrom(scopeProblem)?.field !== 'legal_entities' && (
            <p
              role="alert"
              className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {scopeProblem.detail}
            </p>
          )}
          <LegalEntitiesChecklist
            legal_entities={legal_entity.legal_entities}
            checked={scopeLegalEntities}
            onChange={setScopeLegalEntities}
            idPrefix="scope-legal-entity"
            error={
              scopeProblem && fieldErrorFrom(scopeProblem)?.field === 'legal_entities'
                ? fieldErrorFrom(scopeProblem)?.message
                : undefined
            }
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Annulla
          </Button>
          <Button onClick={saveScope} disabled={update.isPending || scopeLegalEntities.length === 0}>
            {update.isPending ? 'Salvataggio…' : 'Salva'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
