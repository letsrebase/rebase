import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import { Loader } from '@rebase/ui/loader'
import { ApiError, member, type MissedAccount } from '@/lib/api'

const PROVIDER_NAMES: Record<string, string> = { github: 'GitHub', google: 'Google' }

/** Where a sign-in with GitHub or Google ends when the address the provider vouched for is
 *  not a login address of anybody here (REB-1137, spec 2026-11-18 § 5): no account is
 *  created, and the page says so, names the address and offers the two ways forward, the
 *  magic link (an address typed by hand) and the chooser (a person who is not in yet). The
 *  address comes from a ten-minute cookie of this browser; with none, the page says the same
 *  thing without it. A long address wraps at a space first and inside the word only when the
 *  word alone is wider than the column. */
export function NoAccount() {
  const miss = useQuery({
    queryKey: ['oauth-miss'] as const,
    queryFn: async (): Promise<MissedAccount | null> => {
      try {
        return await member.noAccount()
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null
        throw error
      }
    },
    retry: false,
    staleTime: Infinity,
  })

  if (miss.isPending) {
    return (
      <div className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader className="size-8" />
        Un attimo…
      </div>
    )
  }

  const who = miss.data ? (PROVIDER_NAMES[miss.data.provider] ?? 'Il tuo account') : null
  return (
    <div className="mx-auto max-w-md space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Non troviamo il tuo account</h1>
        {miss.data ? (
          <p className="text-muted-foreground break-words">
            {who} ci ha detto che il tuo indirizzo verificato è{' '}
            <span className="font-medium text-foreground">{miss.data.email}</span>, e nessun
            account rebase lo usa.
          </p>
        ) : (
          <p className="text-muted-foreground">
            L’indirizzo verificato del tuo account GitHub o Google non è quello di nessun account rebase.
          </p>
        )}
      </div>
      <div className="space-y-3">
        <p>Ti sei iscritto con un altro indirizzo? Scrivilo nel modulo di accesso: ti mandiamo un link per entrare.</p>
        <Button asChild className="w-full">
          <Link to="/login">Entra con il link via mail</Link>
        </Button>
      </div>
      <div className="space-y-3">
        <p>Non sei ancora dentro? Scegli come vuoi iniziare.</p>
        <Button asChild variant="outline" className="w-full">
          <Link to="/">Raccontaci chi sei</Link>
        </Button>
      </div>
    </div>
  )
}
