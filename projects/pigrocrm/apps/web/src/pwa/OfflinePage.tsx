import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { BrandMark } from '@/components/BrandMark'

/**
 * What the service worker answers a page load with when the network is gone
 * (`sw/index.ts`). Built as its own entry (`offline.html`), so it renders with no
 * router, no session and no API: nothing in it is a person's data, which is what makes
 * it safe to keep in the precache and hand to whoever opens the app.
 */
export function OfflinePage({ onRetry }: { onRetry: () => void }) {
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="inline-flex items-center text-2xl">
            <BrandMark className="mr-2.5 size-3.5" />
            Pigro<span className="text-[var(--color-watermelon)]">CRM</span>
          </CardTitle>
          <CardDescription>Il CRM che lavora al posto tuo.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <h1 className="text-lg font-medium">Sei offline</h1>
          <p className="text-muted-foreground text-sm">
            Senza rete PigroCRM non ti mostra niente: preferisce non farti vedere cifre vecchie come se
            fossero di oggi. Riconnettiti e riprova, la pagina si ricarica da sola appena la rete torna.
          </p>
          <Button type="button" className="w-full" onClick={onRetry}>
            Riprova
          </Button>
        </CardContent>
      </Card>
    </main>
  )
}
