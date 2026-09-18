import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Plus } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { ApiError, admin, type Admin, type AdminCreate } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { Empty, Header } from './lists'

const ADMINS_KEY = ['admins'] as const
/** Mirrors `PASSWORD_MIN_LENGTH` in `rebase_core.admin`; the API is the one that refuses. */
const PASSWORD_MIN_LENGTH = 10

const EMPTY: AdminCreate = { nome: '', email: '', password: '' }

/** What the one dialog is doing: nothing, creating, or editing one row. */
type Mode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; admin: Admin }

/**
 * Who reads this area, a button that adds one more, and a pencil on every row (ORB-123,
 * ORB-125, ORB-129). The page is the list; the form lives in one dialog that either
 * creates or edits, so the two flows cannot drift. The creating admin chooses the
 * password and hands it over out of band, as `rebase createadmin` does on the server;
 * when editing, an empty password means «keep it». The rules (one address, ten
 * characters) are the service's, and a 422 comes back naming the field, so the dialog
 * stays open and points at it. On success it closes, the list refreshes and the page says
 * who was created or changed. No deactivation and no deletion here, on purpose.
 */
export function AdminAdmins() {
  const client = useQueryClient()
  const list = useQuery({ queryKey: ADMINS_KEY, queryFn: () => admin.admins() })
  const [mode, setMode] = useState<Mode>({ kind: 'closed' })
  const [done, setDone] = useState<{ verb: 'creato' | 'aggiornato'; email: string } | null>(null)
  const [draft, setDraft] = useState<AdminCreate>(EMPTY)
  /** Whatever opened the dialog, the header button or one pencil, gets focus back on close. */
  const opener = useRef<HTMLElement | null>(null)
  const save = useMutation({
    mutationFn: (data: AdminCreate & { id?: string }) =>
      data.id ? admin.updateAdmin({ ...data, id: data.id }) : admin.createAdmin(data),
    onSuccess: (saved, data) => {
      setMode({ kind: 'closed' })
      setDone({ verb: data.id ? 'aggiornato' : 'creato', email: saved.email })
      void client.invalidateQueries({ queryKey: ADMINS_KEY })
    },
  })

  const editing = mode.kind === 'edit' ? mode.admin : null

  /** Every opening starts clean: the row's values or an empty draft, and no error from
   *  the last attempt. While a request is out the dialog stays: closing it would swallow a
   *  late refusal, and a late success would close whatever dialog had been reopened in the
   *  meantime, since the mutation's `onSuccess` outlives `reset()`. */
  function open(next: Mode, from: HTMLElement | null = null) {
    if (next.kind === 'closed' && save.isPending) return
    if (next.kind !== 'closed') {
      // From the event, not `document.activeElement`: Safari does not focus a clicked button.
      opener.current = from
      setDraft(next.kind === 'edit' ? { nome: next.admin.nome, email: next.admin.email, password: '' } : EMPTY)
      save.reset()
    }
    setMode(next)
  }

  const failure = save.error instanceof ApiError ? save.error : null
  const message = failure
    ? failure.message
    : save.error
      ? editing
        ? 'Non riesco a salvare le modifiche.'
        : 'Non riesco a creare l’amministratore.'
      : null
  const wrong = (field: keyof AdminCreate) => failure?.fields.includes(field) || undefined

  function submit(event: FormEvent) {
    event.preventDefault()
    save.mutate({ ...draft, nome: draft.nome.trim(), email: draft.email.trim(), id: editing?.id })
  }

  function field(name: keyof AdminCreate) {
    return (value: string) => setDraft((current) => ({ ...current, [name]: value }))
  }

  return (
    <Dialog open={mode.kind !== 'closed'} onOpenChange={(isOpen) => !isOpen && open({ kind: 'closed' })}>
      <Header title="Amministratori" count={list.data?.length}>
        <Button type="button" size="sm" onClick={(event) => open({ kind: 'create' }, event.currentTarget)}>
          <Plus data-icon="inline-start" />
          Nuovo amministratore
        </Button>
      </Header>

      {/* Always mounted: a live region that appears already filled is often not read out. */}
      <p role="status" aria-live="polite" className={done ? 'border-b px-6 py-3 text-sm' : undefined}>
        {done?.verb === 'creato' && (
          <>
            Amministratore creato: <span className="font-medium">{done.email}</span>. Ora può accedere con la
            password che gli hai dato.
          </>
        )}
        {done?.verb === 'aggiornato' && (
          <>
            Amministratore aggiornato: <span className="font-medium">{done.email}</span>.
          </>
        )}
      </p>

      {list.isError ? (
        <Empty>Non riesco a leggere la lista.</Empty>
      ) : list.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="px-6 py-2 font-medium">Chi</th>
              <th className="px-3 py-2 font-medium">Stato</th>
              <th className="px-3 py-2 text-right font-medium">Da quando</th>
              <th className="px-6 py-2">
                <span className="sr-only">Azioni</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((row) => (
              <tr key={row.id} className="border-b last:border-0 hover:bg-muted">
                <td className="px-6 py-2.5">
                  <p className="font-medium">{row.nome}</p>
                  <p className="text-xs text-muted-foreground">{row.email}</p>
                </td>
                <td className="px-3 py-2.5">
                  <Badge variant="pill">{row.attivo ? 'Attivo' : 'Disattivato'}</Badge>
                </td>
                <td className="px-3 py-2.5 text-right text-muted-foreground">{formatDate(row.created_at)}</td>
                <td className="px-6 py-1.5 text-right">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    onClick={(event) => open({ kind: 'edit', admin: row }, event.currentTarget)}
                  >
                    <Pencil />
                    <span className="sr-only">
                      Modifica {row.nome} ({row.email})
                    </span>
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <DialogContent
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          opener.current?.focus()
        }}
      >
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{editing ? 'Modifica amministratore' : 'Nuovo amministratore'}</DialogTitle>
            <DialogDescription>
              {editing
                ? `Lascia la password vuota per non cambiarla. Una nuova password vale da subito, almeno ${PASSWORD_MIN_LENGTH} caratteri, chiude le sue sessioni aperte, e la comunichi tu a voce o su un canale sicuro.`
                : `Scegli tu la password, almeno ${PASSWORD_MIN_LENGTH} caratteri, e comunicala a voce o su un canale sicuro: qui non viene inviata nessuna email.`}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="admin-nome">Nome</Label>
            <Input
              id="admin-nome"
              required
              maxLength={120}
              autoComplete="off"
              value={draft.nome}
              onChange={(event) => field('nome')(event.target.value)}
              aria-invalid={wrong('nome')}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="admin-email">Email</Label>
            <Input
              id="admin-email"
              type="email"
              required
              autoComplete="off"
              value={draft.email}
              onChange={(event) => field('email')(event.target.value)}
              aria-invalid={wrong('email')}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="admin-password">{editing ? 'Nuova password' : 'Password'}</Label>
            <Input
              id="admin-password"
              type="password"
              required={!editing}
              minLength={editing && !draft.password ? undefined : PASSWORD_MIN_LENGTH}
              autoComplete="new-password"
              placeholder={editing ? 'Vuota: resta quella di adesso' : undefined}
              value={draft.password}
              onChange={(event) => field('password')(event.target.value)}
              aria-invalid={wrong('password')}
            />
          </div>
          {message && (
            <p role="alert" className="text-sm text-destructive">
              {message}
            </p>
          )}
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline" disabled={save.isPending}>
                Annulla
              </Button>
            </DialogClose>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? 'Salvo…' : editing ? 'Salva modifiche' : 'Crea amministratore'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
