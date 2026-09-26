import { useMutation } from '@tanstack/react-query'
import { useSearch } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import { campaigns } from '@/lib/api'

/** The page a campaign mail's «Disiscriviti» opens (spec § 7): one sentence, one button,
 *  and nothing happens until the click, since mail scanners open links on their own. */
export function Disiscrizione() {
  const { t } = useSearch({ strict: false }) as { t?: string }
  const done = useMutation({ mutationFn: (token: string) => campaigns.unsubscribe(token) })
  if (!t) {
    return <p className="mx-auto max-w-xl text-center text-muted-foreground">Il link non è completo: aprilo di nuovo dalla mail.</p>
  }
  return (
    <div className="mx-auto max-w-xl space-y-6 text-center">
      <h1 className="text-3xl font-semibold tracking-tight">Non vuoi più ricevere queste mail?</h1>
      {done.isSuccess ? (
        <p role="status">Fatto: non riceverai più queste mail da rebase.</p>
      ) : (
        <>
          <p className="text-muted-foreground">
            Smetti di ricevere le mail di rebase su profilo e novità. I link per entrare nella tua area continuano ad arrivare quando li chiedi.
          </p>
          <Button type="button" disabled={done.isPending} onClick={() => done.mutate(t)}>
            Non scrivermi più
          </Button>
          {done.isError && <p role="alert" className="text-sm text-destructive">Non ci siamo riusciti. Riprova tra un minuto.</p>}
        </>
      )}
    </div>
  )
}
