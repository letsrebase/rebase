import type { ColumnDef } from '@tanstack/react-table'
import { Copy, KeyRound, Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { DateCell } from '@/components/cells'
import { PageHeader } from '@/components/PageHeader'
import { RowActions } from '@/components/RowActions'
import { StatusPill, type StatusTone } from '@/components/StatusPill'
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
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { roleLabel } from '@/lib/roles'
import { useCreateToken, useRevokeToken, useTokens, type CreatedToken, type TokenRecord } from './queries'
import { useUnsavedTokenGuard } from './useUnsavedTokenGuard'

const dateFormatter = new Intl.DateTimeFormat('it-IT', { dateStyle: 'medium', timeStyle: 'short' })

/** «mai», not the em dash every other absent value shows: a token that has never been
 *  used is a fact about the token, not a missing field, and it is the fact somebody is
 *  looking for before revoking one. `DateCell` renders the same word through `absent`. */
function formatUsed(value: string | null): string {
  return value === null ? 'mai' : dateFormatter.format(new Date(value))
}

/**
 * A token's two states, derived from whether `revoked_at` is set. Named -- rather than
 * left as a pair of inline ternaries -- so the label and the tone have one key to agree
 * on, the same shape the four stored enums use.
 *
 * `attivo` is the ordinary, settled state. `revocato` stays quiet rather than reading as
 * a warning: revoking is something the owner *did*, deliberately, and a list of old
 * tokens all in the destructive tint would say something went wrong when nothing did.
 */
type TokenStato = 'attivo' | 'revocato'

const TOKEN_STATE_LABELS: Record<TokenStato, string> = { attivo: 'Attivo', revocato: 'Revocato' }

const TOKEN_STATE_TONE: Record<TokenStato, StatusTone> = { attivo: 'ink', revocato: 'muted' }

function statoOf(token: TokenRecord): TokenStato {
  return token.revoked_at ? 'revocato' : 'attivo'
}

export function TokensPanel() {
  const { user } = useAuth()
  const [creating, setCreating] = useState(false)
  const [nome, setNome] = useState('')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [issued, setIssued] = useState<CreatedToken | null>(null)
  const [copied, setCopied] = useState(false)

  const tokens = useTokens()
  const create = useCreateToken()
  const revoke = useRevokeToken()

  useUnsavedTokenGuard(Boolean(issued))

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = problem && fieldError?.field !== 'nome' ? problem.detail : null

  function openDialog() {
    setProblem(null)
    setNome('')
    setCreating(true)
  }

  function submit() {
    setProblem(null)
    create.mutate(nome, {
      onSuccess: (created) => {
        setIssued(created)
        setCopied(false)
        setCreating(false)
        setNome('')
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function copyIssued() {
    if (!issued) return
    void navigator.clipboard
      .writeText(issued.token)
      .then(() => {
        setCopied(true)
        toast.success('Token copiato negli appunti')
      })
      // A denied permission, an insecure origin or a page without focus all reject
      // here; silence would leave the person thinking the token is on the clipboard.
      .catch(() => toast.error('Copia negli appunti non riuscita'))
  }

  function revokeToken(token: TokenRecord) {
    const confirmed = window.confirm(
      `Revocare il token "${token.nome}"? Chi lo usa smetterà immediatamente di avere accesso. L'operazione non è reversibile.`,
    )
    if (!confirmed) return

    revoke.mutate(token.id, {
      onSuccess: () => toast.success('Token revocato'),
      onError: (error) => toast.error(toProblem(error).detail),
    })
  }

  const columns: ColumnDef<DataTableFeatures, TokenRecord>[] = [
    { header: 'Nome', accessorKey: 'nome' },
    { header: 'Prefisso', id: 'prefix', cell: (info) => <code>{info.row.original.prefix}</code> },
    {
      header: 'Ultimo uso',
      id: 'last_used_at',
      accessorFn: (row) => formatUsed(row.last_used_at),
      // `DateCell` keeps the time of day for a timestamp, which is the whole point on a
      // token: "used at 03:12" is the answer somebody is looking for, and it renders the
      // same string `formatUsed` does above.
      cell: (info) => <DateCell value={info.row.original.last_used_at} absent="mai" />,
    },
    {
      header: 'Stato',
      id: 'revoked_at',
      // The accessor carries the word, so the column has one plain text value; the pill
      // is the same dotted one every other state in the product wears (design spec §4),
      // not the filled badge this column used to show.
      accessorFn: (row) => TOKEN_STATE_LABELS[statoOf(row)],
      cell: (info) => {
        const stato = statoOf(info.row.original)
        return <StatusPill tone={TOKEN_STATE_TONE[stato]}>{TOKEN_STATE_LABELS[stato]}</StatusPill>
      },
    },
    {
      header: '',
      id: 'actions',
      cell: (info) => {
        const token = info.row.original
        // An already-revoked token has nothing left to do, so `RowActions` draws no «⋯»
        // for it at all rather than a menu holding one disabled item.
        return (
          <RowActions
            label={`Azioni per ${token.nome}`}
            items={
              token.revoked_at
                ? []
                : [{ label: 'Revoca', destructive: true, onSelect: () => revokeToken(token) }]
            }
          />
        )
      },
    },
  ]

  const ruolo = user ? roleLabel(user.ruolo) : null

  return (
    <>
      <PageHeader
        icon={KeyRound}
        title="Token di accesso"
        description="Servono a far usare PigroCRM a un agente (per esempio Claude, tramite il server MCP) con le credenziali di questo account."
        actions={
          <Button onClick={openDialog}>
            <Plus className="mr-2 size-4" />
            Nuovo token
          </Button>
        }
      />

      <div className="space-y-4 px-8 pb-8">
        {/* The variable name is something you type into a shell character for character,
            so it is set in monospace like every other literal in the product. It cannot
            live in the header: `PageHeader`'s description is a plain string, which is how
            the `<code>` was lost when the header moved there. */}
        <p className="text-sm text-muted-foreground">
          Imposta il token nella variabile d&apos;ambiente <code>PIGROCRM_TOKEN</code> di chi lo
          userà.
        </p>

        {/* Stays on the page rather than folding into the header's own description: it
            is the warning, not the explanation, and the two read differently. */}
        <p className="text-sm text-muted-foreground">
          Un token eredita <strong>l&apos;intero ruolo di chi lo crea</strong>, senza possibilità
          di limitarne l&apos;ambito e senza scadenza: chiunque lo possieda può fare, tramite
          l&apos;API, tutto ciò che puoi fare tu — un token creato da un amministratore può
          anche creare altri amministratori. Trattalo come una password: non condividerlo e
          revocalo subito se sospetti che sia stato esposto.
        </p>

        <DataTable
          columns={columns}
          data={tokens.data ?? []}
          isLoading={tokens.isLoading}
          isError={tokens.isError}
          error={tokens.error}
          emptyMessage="Nessun token creato."
        />
      </div>

      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Nuovo token</DialogTitle>
            <DialogDescription>
              Dai un nome riconoscibile, es. &quot;Claude sul portatile&quot;. Avrà lo stesso ruolo
              di questo account ({ruolo}) e non scadrà finché non lo revochi.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            {banner && (
              <p
                role="alert"
                className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {banner}
              </p>
            )}
            <Label htmlFor="token-nome">Nome</Label>
            <Input
              id="token-nome"
              aria-invalid={fieldError?.field === 'nome'}
              value={nome}
              onChange={(event) => setNome(event.target.value)}
            />
            {fieldError?.field === 'nome' && (
              <p className="text-sm text-destructive">{fieldError.message}</p>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              Annulla
            </Button>
            <Button onClick={submit} disabled={create.isPending}>
              {create.isPending ? 'Creazione…' : 'Crea'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/*
       * The one and only place this value is shown. No Esc, no click-outside,
       * no X icon (`showCloseButton={false}`, both handlers below
       * `preventDefault`, `onOpenChange` a deliberate no-op); `useBlocker`
       * above covers the two paths those three can't (Back/a Link, and a real
       * reload/close). The only way out is the explicit button at the
       * bottom -- never gated on `copied`, since selecting and copying the
       * text by hand is just as valid as clicking Copy.
       */}
      <Dialog open={Boolean(issued)} onOpenChange={() => {}}>
        <DialogContent
          showCloseButton={false}
          onEscapeKeyDown={(event) => event.preventDefault()}
          onPointerDownOutside={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>Token creato</DialogTitle>
            <DialogDescription>
              Copialo adesso: questa è l&apos;unica volta in cui sarà visibile. Il server ne
              conserva solo un hash e non potrà più mostrartelo — se lo perdi dovrai revocare
              questo token e crearne uno nuovo.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="token-issued">Token</Label>
            <div className="flex gap-2">
              <Input id="token-issued" readOnly value={issued?.token ?? ''} className="font-mono text-xs" />
              <Button variant="outline" size="icon" aria-label="Copia il token" onClick={copyIssued}>
                <Copy className="size-4" />
              </Button>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => setIssued(null)}>
              {copied ? 'Ho copiato il token, chiudi' : 'Ho salvato il token altrove, chiudi'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
