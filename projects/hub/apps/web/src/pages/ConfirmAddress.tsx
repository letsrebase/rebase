import { useMutation } from '@tanstack/react-query'
import { Link, useSearch } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import { ApiError, campaigns } from '@/lib/api'

/** The page the confirmation mail's button opens (REB-911): one sentence, one button, and
 *  nothing happens until the click, since mail scanners open links on their own and a
 *  scanner must not confirm an address for its owner. Following it again says the same. */
export function ConfirmAddress() {
  const { t } = useSearch({ strict: false }) as { t?: string }
  const done = useMutation({ mutationFn: (token: string) => campaigns.confirmAddress(token) })
  if (!t || (done.error instanceof ApiError && done.error.status === 410)) {
    return (
      <div className="mx-auto max-w-xl space-y-4 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Questo link non funziona</h1>
        <p role="alert" className="text-muted-foreground">
          Il link non è valido o è scaduto. Se vuoi, entra con il tuo indirizzo: anche così risulta confermato.
        </p>
        <Link to="/login" className="text-sm underline underline-offset-2">
          Chiedi il link per entrare
        </Link>
      </div>
    )
  }
  return (
    <div className="mx-auto max-w-xl space-y-6 text-center">
      <h1 className="text-3xl font-semibold tracking-tight">Conferma il tuo indirizzo</h1>
      {done.isSuccess ? (
        <p role="status">Fatto: l'indirizzo è confermato. Grazie.</p>
      ) : (
        <>
          <p className="text-muted-foreground">
            Questo indirizzo è stato scritto nel modulo di rebase. Se è il tuo, confermalo: da quel momento potremo scriverti.
          </p>
          <Button type="button" disabled={done.isPending} onClick={() => done.mutate(t)}>
            Confermo
          </Button>
          {done.isError && <p role="alert" className="text-sm text-destructive">Non ci siamo riusciti. Riprova tra un minuto.</p>}
        </>
      )}
    </div>
  )
}
