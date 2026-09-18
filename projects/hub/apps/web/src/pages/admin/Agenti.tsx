import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import { ApiError, admin, type AdminToken, type CreatedToken } from '@/lib/api'
import { TOKEN_PLACEHOLDER, claudeCodeCommand, mcpEndpoint, mcpServersJson } from '@/lib/connect'
import { formatDate } from '@/lib/format'
import { Empty, Header } from './lists'

const TOKENS_KEY = ['tokens'] as const
const DEFAULT_NAME = 'Claude Code'

/**
 * Everything an agent needs to talk to the hub's MCP server as this admin (REB-213): the
 * endpoint, a token minted here and shown once, the admin's own tokens with a revoke on
 * each, and the two snippets a client takes. The token is a password: it opens every
 * tool the admin area opens, so the page says so beside it and the list keeps the revoked
 * rows, since a list that hides what was revoked cannot show that somebody revoked it.
 */
export function AdminAgenti() {
  const client = useQueryClient()
  const list = useQuery({ queryKey: TOKENS_KEY, queryFn: () => admin.tokens() })
  const [nome, setNome] = useState(DEFAULT_NAME)
  const [issued, setIssued] = useState<CreatedToken | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: (name: string) => admin.createToken(name),
    onSuccess: (created) => {
      setIssued(created)
      setNome(DEFAULT_NAME)
      void client.invalidateQueries({ queryKey: TOKENS_KEY })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) => admin.revokeToken(id),
    onSuccess: () => void client.invalidateQueries({ queryKey: TOKENS_KEY }),
  })

  const url = mcpEndpoint(window.location.origin)
  const token = issued?.token ?? TOKEN_PLACEHOLDER
  const command = claudeCodeCommand(url, token)
  const json = mcpServersJson(url, token)
  const failure = create.error instanceof ApiError ? create.error.message : create.error ? 'Non riesco a creare il token.' : null

  function mint(event: FormEvent) {
    event.preventDefault()
    create.mutate(nome.trim() || DEFAULT_NAME)
  }

  function copy(text: string, what: string) {
    void navigator.clipboard
      .writeText(text)
      .then(() => setCopied(`${what} copiato negli appunti.`))
      .catch(() => setCopied('Copia negli appunti non riuscita.'))
  }

  return (
    <>
      <Header title="Agenti" count={list.data?.length} />
      <div className="grid gap-8 px-6 py-6">
        <section className="grid max-w-2xl gap-4">
          <p className="text-sm text-muted-foreground">
            Un agente come Claude Code legge l’hub tramite il server MCP, con un token personale di
            questo account. Copia l’endpoint e uno dei due snippet.
          </p>
          <Field
            id="mcp-endpoint"
            label="Endpoint"
            copyLabel="Copia l'endpoint"
            value={url}
            onCopy={() => copy(url, 'L’endpoint')}
          />
          {issued ? (
            <div className="space-y-2">
              <Label htmlFor="mcp-token">Token «{issued.nome}»</Label>
              <div className="flex gap-2">
                <Input id="mcp-token" readOnly value={issued.token} className="font-mono text-xs" />
                <Button type="button" variant="outline" size="icon" aria-label="Copia il token" onClick={() => copy(issued.token, 'Il token')}>
                  <Copy className="size-4" />
                </Button>
              </div>
              <p className="text-sm text-muted-foreground">
                Viene mostrato una volta sola. Apre <strong>tutto quello che apre quest’area</strong>, senza
                scadenza: trattalo come una password e revocalo qui sotto se sospetti che sia stato esposto.
              </p>
            </div>
          ) : (
            <form onSubmit={mint} className="space-y-2">
              <p className="text-sm text-muted-foreground">Serve un token di accesso: viene mostrato una volta sola.</p>
              <Label htmlFor="mcp-token-nome">Nome del token</Label>
              <div className="flex gap-2">
                <Input
                  id="mcp-token-nome"
                  maxLength={120}
                  autoComplete="off"
                  value={nome}
                  onChange={(event) => setNome(event.target.value)}
                />
                <Button type="submit" disabled={create.isPending}>
                  {create.isPending ? 'Creo…' : 'Crea il token'}
                </Button>
              </div>
              {failure && (
                <p role="alert" className="text-sm text-destructive">
                  {failure}
                </p>
              )}
            </form>
          )}
          <Field
            id="mcp-claude-code"
            label="Comando per Claude Code"
            copyLabel="Copia il comando per Claude Code"
            value={command}
            onCopy={() => copy(command, 'Il comando')}
            multiline
          />
          <Field
            id="mcp-json"
            label="Configurazione JSON"
            copyLabel="Copia la configurazione JSON"
            value={json}
            onCopy={() => copy(json, 'La configurazione')}
            multiline
          />
          {/* Always mounted: a live region that appears already filled is often not read out. */}
          <p role="status" aria-live="polite" className="text-sm text-muted-foreground">
            {copied}
          </p>
        </section>

        <section>
          <h2 className="mb-3 text-sm font-semibold">I tuoi token</h2>
          {list.isError ? (
            <Empty>Non riesco a leggere la lista.</Empty>
          ) : list.isPending ? (
            <Empty>Caricamento…</Empty>
          ) : list.data.length === 0 ? (
            <Empty>Nessun token ancora.</Empty>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr className="border-b">
                  <th className="py-2 font-medium">Token</th>
                  <th className="px-3 py-2 font-medium">Stato</th>
                  <th className="px-3 py-2 text-right font-medium">Creato</th>
                  <th className="px-3 py-2 text-right font-medium">Ultimo uso</th>
                  <th className="py-2">
                    <span className="sr-only">Azioni</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.data.map((row: AdminToken) => (
                  <tr key={row.id} className="border-b last:border-0">
                    <td className="py-2.5">
                      <p className="font-medium">{row.nome}</p>
                      <p className="font-mono text-xs text-muted-foreground">{row.prefix}…</p>
                    </td>
                    <td className="px-3 py-2.5">
                      <Badge variant="pill">{row.revoked_at ? 'Revocato' : 'Attivo'}</Badge>
                    </td>
                    <td className="px-3 py-2.5 text-right text-muted-foreground">{formatDate(row.created_at)}</td>
                    <td className="px-3 py-2.5 text-right text-muted-foreground">
                      {row.last_used_at ? formatDate(row.last_used_at) : 'Mai'}
                    </td>
                    <td className="py-1.5 text-right">
                      {!row.revoked_at && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={revoke.isPending}
                          onClick={() => revoke.mutate(row.id)}
                        >
                          Revoca
                          <span className="sr-only"> {row.nome}</span>
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </>
  )
}

/** A read-only value with its copy button; `copyLabel` is the button's accessible name. */
function Field({
  id,
  label,
  copyLabel,
  value,
  onCopy,
  multiline = false,
}: {
  id: string
  label: string
  copyLabel: string
  value: string
  onCopy: () => void
  multiline?: boolean
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex gap-2">
        {multiline ? (
          <Textarea id={id} readOnly value={value} rows={3} className="font-mono text-xs" />
        ) : (
          <Input id={id} readOnly value={value} className="font-mono text-xs" />
        )}
        <Button type="button" variant="outline" size="icon" aria-label={copyLabel} onClick={onCopy}>
          <Copy className="size-4" />
        </Button>
      </div>
    </div>
  )
}
