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
 * «Collega un agente» (ORB-170): `ConnectAgentPanel` in a dialog, the primary action of
 * the Agenti page (REB-873; it was a sidebar button until then, with a link back to the
 * token list, which is now the page it opens on). The token is shown once, like when one is
 * made by hand on that page, and the same guard asks before it is lost. The panel's own
 * state (the name being typed, a refusal) goes with the dialog's content when it closes;
 * the token is held here, because closing is where it has to be asked about and thrown
 * away.
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

  /** Closes unless a token is on screen and the person declines to lose it. */
  function close(next: boolean) {
    if (!next && issued && !confirmDiscardingToken()) return
    if (!next) setIssued(null)
    onOpenChange(next)
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      {/* The footer's own «Chiudi» is the visible way out; the corner X gets a name of its
          own so a screen reader does not hear two identical controls (REB-593). */}
      <DialogContent className="sm:max-w-xl" closeLabel="Chiudi la finestra">
        <DialogHeader>
          <DialogTitle>Collega un agente</DialogTitle>
          <DialogDescription>
            Un agente come Claude Code parla con PigroCRM tramite il server MCP, con un token
            di accesso di questo account. Copia l&apos;endpoint e uno dei due snippet.
          </DialogDescription>
        </DialogHeader>

        <ConnectAgentPanel issued={issued} onIssued={setIssued} />

        <DialogFooter>
          <Button onClick={() => close(false)}>Chiudi</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
