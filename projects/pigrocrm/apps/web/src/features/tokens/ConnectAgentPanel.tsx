import { Copy } from 'lucide-react'
import { useId, useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { tenantPrefix } from '@/lib/tenant'
import {
  TOKEN_PLACEHOLDER,
  claudeCodeCommand,
  mcpEndpoint,
  mcpServersJson,
  serverName,
} from './connectSnippets'
import { useCreateToken, type CreatedToken } from './queries'

/**
 * Everything a client needs to talk to this installation's MCP server over HTTP
 * (ORB-170): the endpoint, a token minted right here, and the two shapes a
 * configuration takes. Shared by the sidebar's «Collega un agente» dialog and the
 * start page's «Fai lavorare l'assistente» block (spec 2026-09-16 §4.2, REB-222), so
 * the two cannot drift.
 *
 * The minted token is held by the caller, not here: the caller is the one that has to
 * ask before it is lost (`useUnsavedTokenGuard`), and the dialog is the one that throws
 * it away when it closes. Everything else (the name being typed, the refusal) lives and
 * dies with this component.
 */
export function ConnectAgentPanel({
  issued,
  onIssued,
}: {
  issued: CreatedToken | null
  onIssued: (created: CreatedToken) => void
}) {
  const [nome, setNome] = useState('Claude Code')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const create = useCreateToken()
  // The dialog and the start page can both be on screen (the sidebar's «Collega un
  // agente» over an empty space's Home): fixed ids would appear twice and a label
  // would name the input behind the dialog.
  const ids = useId()
  const id = (name: string) => `${ids}-${name}`

  const url = mcpEndpoint(window.location.origin, tenantPrefix)
  const name = serverName(tenantPrefix)
  const token = issued?.token ?? TOKEN_PLACEHOLDER
  const command = claudeCodeCommand(name, url, token)
  const json = mcpServersJson(name, url, token)
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = problem && fieldError?.field !== 'nome' ? problem.detail : null

  function mint() {
    setProblem(null)
    create.mutate(nome, {
      onSuccess: (created) => onIssued(created),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function copy(text: string, what: string) {
    void navigator.clipboard
      .writeText(text)
      .then(() => toast.success(`${what} copiato negli appunti`))
      // A denied permission, an insecure origin or a page without focus all reject
      // here; silence would leave the person thinking the value is on the clipboard.
      .catch(() => toast.error('Copia negli appunti non riuscita'))
  }

  return (
    <div className="space-y-4">
      <Field
        label="Endpoint"
        copyLabel="Copia l'endpoint"
        id={id('endpoint')}
        value={url}
        onCopy={() => copy(url, "L'endpoint")}
      />

      {issued ? (
        <div className="space-y-2">
          <Label htmlFor={id('token')}>Token</Label>
          <div className="flex gap-2">
            <Input id={id('token')} readOnly value={issued.token} className="font-mono text-xs" />
            <Button
              variant="outline"
              size="icon"
              aria-label="Copia il token"
              onClick={() => copy(issued.token, 'Il token')}
            >
              <Copy className="size-4" />
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            Viene mostrato una volta sola. Eredita <strong>l&apos;intero ruolo di questo
            account</strong>, senza scadenza: trattalo come una password e revocalo dalla
            pagina Token se sospetti che sia stato esposto.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Serve un token di accesso: viene mostrato una volta sola.
          </p>
          {banner && (
            <p
              role="alert"
              className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {banner}
            </p>
          )}
          <Label htmlFor={id('token-nome')}>Nome del token</Label>
          <div className="flex gap-2">
            <Input
              id={id('token-nome')}
              aria-invalid={fieldError?.field === 'nome'}
              value={nome}
              onChange={(event) => setNome(event.target.value)}
            />
            <Button onClick={mint} disabled={create.isPending}>
              Crea il token
            </Button>
          </div>
          {fieldError?.field === 'nome' && (
            <p className="text-sm text-destructive">{fieldError.message}</p>
          )}
        </div>
      )}

      <Field
        label="Comando per Claude Code"
        copyLabel="Copia il comando per Claude Code"
        id={id('claude-code')}
        value={command}
        onCopy={() => copy(command, 'Il comando')}
        multiline
      />
      <Field
        label="Configurazione JSON"
        copyLabel="Copia la configurazione JSON"
        id={id('json')}
        value={json}
        onCopy={() => copy(json, 'La configurazione')}
        multiline
      />
    </div>
  )
}

/** A read-only value with its copy button; `copyLabel` is the button's accessible name. */
function Field({
  label,
  copyLabel,
  id,
  value,
  onCopy,
  multiline = false,
}: {
  label: string
  copyLabel: string
  id: string
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
        <Button variant="outline" size="icon" aria-label={copyLabel} onClick={onCopy}>
          <Copy className="size-4" />
        </Button>
      </div>
    </div>
  )
}
