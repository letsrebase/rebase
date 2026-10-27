import type { ColumnDef } from '@tanstack/react-table'
import { Copy, KeyRound, Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { DateCell } from '@/components/cells'
import { PageHeader } from '@/components/PageHeader'
import { RowActions } from '@/components/RowActions'
import { StatusPill, type StatusTone } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
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
import {
  useCreateToken,
  useRevokeToken,
  useSetTokenFullAccess,
  useTeamTokens,
  useTokenOptions,
  useTokens,
  type CreatedToken,
  type TeamTokenRecord,
  type TokenRecord,
} from './queries'
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

/**
 * What the token may do beyond the ordinary tools (REB-810): the invoice, rate and
 * period-lock tools. «Completo» wears the gold dot, the one tone in the product that says
 * «look here»: it is the setting somebody scans the list for. An ordinary token is quiet.
 */
const ACCESS_LABELS = { completo: 'Completo', standard: 'Standard' } as const
const ACCESS_TONE: Record<keyof typeof ACCESS_LABELS, StatusTone> = {
  completo: 'gold',
  standard: 'muted',
}

function accessOf(token: TokenRecord): keyof typeof ACCESS_LABELS {
  return token.full_access ? 'completo' : 'standard'
}

const FULL_ACCESS_HELP =
  'Con l’accesso completo l’agente può emettere e annullare fatture, cambiare tariffe e chiudere i mesi: atti che non si annullano.'

/** The row action that opens or closes a token's full access, for a token of anybody; none
 *  for a revoked one. Closing is offered whatever the space says: it is always allowed. */
function fullAccessAction(
  token: TokenRecord,
  available: boolean,
  change: (token: TokenRecord, fullAccess: boolean) => void,
) {
  if (token.revoked_at) return []
  if (token.full_access) {
    return [{ label: 'Chiudi l’accesso completo', onSelect: () => change(token, false) }]
  }
  return [
    // Disabled rather than missing when the space has not opened it: the item is where
    // somebody looks for it, and the page says why it does not work.
    { label: 'Dai l’accesso completo', disabled: !available, onSelect: () => change(token, true) },
  ]
}

export function TokensPanel() {
  const { user } = useAuth()
  const [creating, setCreating] = useState(false)
  const [nome, setNome] = useState('')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [fullAccess, setFullAccess] = useState(false)
  const [issued, setIssued] = useState<CreatedToken | null>(null)
  const [copied, setCopied] = useState(false)

  const tokens = useTokens()
  const create = useCreateToken()
  const revoke = useRevokeToken()
  const setAccess = useSetTokenFullAccess()
  const options = useTokenOptions()
  const isAdmin = user?.ruolo === 'admin'
  const team = useTeamTokens(isAdmin)
  // Unknown (still loading, or the read failed) counts as closed: the server refuses an
  // opening the space does not offer, and an offered box that fails on submit is worse than
  // a disabled one that explains itself.
  const fullAccessAvailable = options.data?.full_access_available === true

  useUnsavedTokenGuard(Boolean(issued))

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = problem && fieldError?.field !== 'nome' ? problem.detail : null

  function openDialog() {
    setProblem(null)
    setNome('')
    setFullAccess(false)
    setCreating(true)
  }

  function submit() {
    setProblem(null)
    create.mutate({ nome, full_access: fullAccess && fullAccessAvailable }, {
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

  /** Opening asks first, since what it hands over cannot be taken back once used; closing
   *  never does. `whose` is empty for the caller's own token. */
  function changeAccess(token: TokenRecord, next: boolean, whose = '') {
    if (next) {
      const confirmed = window.confirm(
        `Dare l’accesso completo al token "${token.nome}"${whose}? ${FULL_ACCESS_HELP}`,
      )
      if (!confirmed) return
    }
    setAccess.mutate(
      { tokenId: token.id, full_access: next },
      {
        onSuccess: () =>
          toast.success(next ? 'Accesso completo attivato' : 'Accesso completo chiuso'),
        onError: (error) => toast.error(toProblem(error).detail),
      },
    )
  }

  function accessColumn<T extends TokenRecord>(): ColumnDef<DataTableFeatures, T> {
    return {
      header: 'Accesso',
      id: 'full_access',
      accessorFn: (row) => ACCESS_LABELS[accessOf(row)],
      cell: (info) => {
        const access = accessOf(info.row.original)
        return <StatusPill tone={ACCESS_TONE[access]}>{ACCESS_LABELS[access]}</StatusPill>
      },
    }
  }

  const columns: ColumnDef<DataTableFeatures, TokenRecord>[] = [
    { header: 'Nome', accessorKey: 'nome' },
    { header: 'Prefisso', id: 'prefix', cell: (info) => <code>{info.row.original.prefix}</code> },
    accessColumn<TokenRecord>(),
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
                : [
                    ...fullAccessAction(token, fullAccessAvailable, (t, next) =>
                      changeAccess(t, next),
                    ),
                    { label: 'Revoca', destructive: true, onSelect: () => revokeToken(token) },
                  ]
            }
          />
        )
      },
    },
  ]

  const teamColumns: ColumnDef<DataTableFeatures, TeamTokenRecord>[] = [
    { header: 'Titolare', accessorKey: 'owner_nome' },
    { header: 'Nome', accessorKey: 'nome' },
    { header: 'Prefisso', id: 'prefix', cell: (info) => <code>{info.row.original.prefix}</code> },
    accessColumn<TeamTokenRecord>(),
    {
      header: 'Ultimo uso',
      id: 'last_used_at',
      accessorFn: (row) => formatUsed(row.last_used_at),
      cell: (info) => <DateCell value={info.row.original.last_used_at} absent="mai" />,
    },
    {
      header: '',
      id: 'actions',
      cell: (info) => {
        const token = info.row.original
        return (
          <RowActions
            label={`Azioni per ${token.nome} di ${token.owner_nome}`}
            items={fullAccessAction(token, fullAccessAvailable, (t, next) =>
              changeAccess(t, next, ` di ${token.owner_nome}`),
            )}
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
          Un token eredita <strong>l&apos;intero ruolo di chi lo crea</strong> e va{' '}
          <strong>senza scadenza</strong>: chiunque lo possieda può fare, tramite l&apos;API,
          tutto ciò che puoi fare tu — un token creato da un amministratore può anche creare altri
          amministratori. Trattalo come una password: non condividerlo e revocalo subito se
          sospetti che sia stato esposto.
        </p>

        <p className="text-sm text-muted-foreground">
          Fatture, tariffe e chiusura dei mesi sono fuori dalla portata di un token, a meno che tu
          non gli dia l&apos;<strong>accesso completo</strong>: lo scegli token per token, ed è
          chiuso finché non lo chiedi.
        </p>

        <DataTable
          label="Token"
          columns={columns}
          data={tokens.data ?? []}
          isLoading={tokens.isLoading}
          isError={tokens.isError}
          error={tokens.error}
          emptyMessage="Nessun token creato."
        />

        {isAdmin && (
          <section className="space-y-2 pt-4" aria-labelledby="team-tokens-title">
            <h2 id="team-tokens-title" className="text-base font-medium">
              Token del team
            </h2>
            <p className="text-sm text-muted-foreground">
              I token attivi degli altri membri. Puoi chiudere l’accesso completo di uno che non
              deve arrivare alle fatture; la modifica compare nella cronologia del titolare.
            </p>
            <DataTable
              label="Token del team"
              columns={teamColumns}
              data={team.data ?? []}
              isLoading={team.isLoading}
              isError={team.isError}
              error={team.error}
              emptyMessage="Nessun altro membro ha un token attivo."
            />
          </section>
        )}
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
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Checkbox
                id="token-full-access"
                checked={fullAccess && fullAccessAvailable}
                disabled={!fullAccessAvailable}
                onCheckedChange={(checked) => setFullAccess(checked === true)}
              />
              <Label htmlFor="token-full-access">Accesso completo</Label>
            </div>
            <p className="text-sm text-muted-foreground">
              {fullAccessAvailable
                ? FULL_ACCESS_HELP
                : 'Chiuso per questo spazio: lo apre un amministratore in Impostazioni → Spazio. Senza, l’agente non ha gli strumenti per le fatture, le tariffe e la chiusura dei mesi.'}
            </p>
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
