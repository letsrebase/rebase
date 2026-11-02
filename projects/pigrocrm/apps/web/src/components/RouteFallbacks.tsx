import { Link, type ErrorComponentProps } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import { Loader } from '@rebase/ui/loader'
import { isChunkLoadError } from '@/lib/chunkReload'

function Fallback({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center p-8">
      <div className="max-w-md space-y-4 text-center">
        <h1 className="text-xl font-semibold">{title}</h1>
        {children}
      </div>
    </div>
  )
}

/** The router's `defaultErrorComponent`: a render exception, or a lazy page whose file
 *  a deploy replaced (then reloading is the whole remedy). */
export function RouteError({ error, reset }: ErrorComponentProps) {
  const stale = isChunkLoadError(error)
  return (
    <Fallback title={stale ? 'C’è una nuova versione' : 'Qualcosa è andato storto'}>
      <p className="text-sm text-muted-foreground">
        {stale
          ? 'L’applicazione è stata aggiornata: ricarica la pagina per continuare.'
          : 'Non riesco a mostrare questa pagina. Riprova, o ricarica la pagina.'}
      </p>
      <div className="flex justify-center gap-2">
        {!stale && (
          <Button variant="outline" onClick={reset}>
            Riprova
          </Button>
        )}
        <Button onClick={() => window.location.reload()}>Ricarica</Button>
      </div>
    </Fallback>
  )
}

/** The router's `defaultNotFoundComponent`: an unknown path under `/app/`. */
export function RouteNotFound() {
  return (
    <Fallback title="Pagina non trovata">
      <p className="text-sm text-muted-foreground">Questo indirizzo non esiste.</p>
      <Button asChild>
        <Link to="/app">Vai alla home</Link>
      </Button>
    </Fallback>
  )
}

export function RoutePending() {
  return (
    <div className="flex min-h-full items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
      <Loader className="size-8" />
      Caricamento…
    </div>
  )
}
