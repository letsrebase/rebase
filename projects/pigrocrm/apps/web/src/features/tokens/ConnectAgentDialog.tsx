import { Link } from '@tanstack/react-router'
import { Copy } from 'lucide-react'
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
import { confirmDiscardingToken, useUnsavedTokenGuard } from './useUnsavedTokenGuard'

/**
 * Everything a client needs to talk to this installation's MCP server over HTTP
 * (ORB-170): the endpoint, a token minted right here, and the two shapes a
 * configuration takes. The token is shown once, like on the Token page, and the same
 * guard asks before it is lost. Opened from the sidebar's «Collega un agente».
 */
export function ConnectAgentDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [nome, setNome] = useState('Claude Code')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [issued, setIssued] = useState<CreatedToken | null>(null)
  const create = useCreateToken()
  useUnsavedTokenGuard(Boolean(issued))

  const url = mcpEndpoint(window.location.origin, tenantPrefix)
  const name = serverName(tenantPrefix)
  const token = issued?.token ?? TOKEN_PLACEHOLDER
  const command = claudeCodeCommand(name, url, token)
  const json = mcpServersJson(name, url, token)
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = problem && fieldError?.field !== 'nome' ? problem.detail : null

  /** True when the dialog actually closed; false when the guard was declined. The
   *  Token-page link reads the answer to cancel its own navigation. */
  function close(next: boolean): boolean {
    if (!next && issued && !confirmDiscardingToken()) return false
    if (!next) {
      setIssued(null)
      setProblem(null)
      setNome('Claude Code')
    }
    onOpenChange(next)
    return true
  }

  function mint() {
    setProblem(null)
    create.mutate(nome, {
      onSuccess: (created) => setIssued(created),
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
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Collega un agente</DialogTitle>
          <DialogDescription>
            Un agente come Claude Code parla con PigroCRM tramite il server MCP, con un token
            di accesso di questo account. Copia l&apos;endpoint e uno dei due snippet.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <Field
            label="Endpoint"
            copyLabel="Copia l'endpoint"
            id="mcp-endpoint"
            value={url}
            onCopy={() => copy(url, "L'endpoint")}
          />

          {issued ? (
            <div className="space-y-2">
              <Label htmlFor="mcp-token">Token</Label>
              <div className="flex gap-2">
                <Input id="mcp-token" readOnly value={issued.token} className="font-mono text-xs" />
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
                  className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                >
                  {banner}
                </p>
              )}
              <Label htmlFor="mcp-token-nome">Nome del token</Label>
              <div className="flex gap-2">
                <Input
                  id="mcp-token-nome"
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
            id="mcp-claude-code"
            value={command}
            onCopy={() => copy(command, 'Il comando')}
            multiline
          />
          <Field
            label="Configurazione JSON"
            copyLabel="Copia la configurazione JSON"
            id="mcp-json"
            value={json}
            onCopy={() => copy(json, 'La configurazione')}
            multiline
          />
        </div>

        <DialogFooter className="sm:justify-between">
          <Button variant="ghost" asChild>
            {/* `close(false)` already asks the same question this navigation's own
                blocker would (`useUnsavedTokenGuard`, shared with the Token page): both
                read `issued` and call `confirmDiscardingToken()`. Without `ignoreBlocker`
                the router asks a second time for this exact navigation, and the second
                answer -- "no" -- lands after `close(false)` has already discarded the
                token, so declining it looks like it keeps the token but does not. This
                Link is the only navigation this dialog ever performs by hand; every other
                way to leave with a token on screen (Back, reload, closing the tab) still
                goes through the blocker alone. */}
            <Link
              to="/app/token"
              ignoreBlocker
              onClick={(event) => {
                if (!close(false)) event.preventDefault()
              }}
            >
              Gestisci i token
            </Link>
          </Button>
          <Button onClick={() => close(false)}>Chiudi</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
