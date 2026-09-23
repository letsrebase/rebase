import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { ConnectAgentPanel } from './ConnectAgentPanel'
import type { CreatedToken } from './queries'
import { confirmDiscardingToken, useUnsavedTokenGuard } from './useUnsavedTokenGuard'

/**
 * The sidebar's «Collega un agente» (ORB-170): `ConnectAgentPanel` in a dialog, with a
 * way out to the Token page. The token is shown once, like on the Token page, and the
 * same guard asks before it is lost. The panel's own state (the name being typed, a
 * refusal) goes with the dialog's content when it closes; the token is held here,
 * because closing is where it has to be asked about and thrown away.
 */
export function ConnectAgentDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [issued, setIssued] = useState<CreatedToken | null>(null)
  useUnsavedTokenGuard(Boolean(issued))

  /** True when the dialog actually closed; false when the guard was declined. The
   *  Token-page link reads the answer to cancel its own navigation. */
  function close(next: boolean): boolean {
    if (!next && issued && !confirmDiscardingToken()) return false
    if (!next) setIssued(null)
    onOpenChange(next)
    return true
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

        <ConnectAgentPanel issued={issued} onIssued={setIssued} />

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
