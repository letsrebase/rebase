import { Link, useLocation } from '@tanstack/react-router'
import { ArrowRight, Briefcase, UserRound } from 'lucide-react'
import { useEffect } from 'react'
import { resolveReferral } from '@/lib/utm'

/** The front door of the hub: two doors, a word for each, and a line to the team
 *  builder (P-REB-43). Reads `?rif=` once, on mount, and remembers it for the tab
 *  (`resolveReferral`) before either door's own `<Link>` drops the query string on
 *  navigation -- a visitor who opens `/hub/?rif=CODE` and picks a door would
 *  otherwise lose the code between here and the wizard that reads it (CodeRabbit,
 *  P-REB-44). */
export function Chooser() {
  const searchStr = useLocation({ select: (location) => location.searchStr })
  useEffect(() => {
    resolveReferral(searchStr)
  }, [searchStr])

  return (
    <div className="mx-auto max-w-2xl space-y-10">
      <div>
        <p className="text-xs font-medium tracking-wide text-muted-foreground">rebase</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Chi sei?</h1>
        <p className="mt-2 text-muted-foreground">
          Due minuti, poche domande, una alla volta. Ti scriviamo noi.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Link
          to="/freelance"
          className="group flex flex-col gap-3 border-[length:var(--landing-border-width)] bg-card p-6 shadow-sm transition-colors hover:bg-muted focus-visible:outline-3 focus-visible:outline-offset-3 focus-visible:outline-(color:--landing-focus)"
        >
          <UserRound className="size-6" aria-hidden="true" />
          <span className="text-lg font-semibold">Sono un talento</span>
          <span className="text-sm text-muted-foreground">
            Lavoro in proprio: voglio progetti da aziende vere, e gente con cui parlarne.
          </span>
          <span className="mt-auto inline-flex items-center gap-1 text-sm font-medium">
            Entra <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />
          </span>
        </Link>
        <Link
          to="/companies"
          className="group flex flex-col gap-3 border-[length:var(--landing-border-width)] bg-card p-6 shadow-sm transition-colors hover:bg-muted focus-visible:outline-3 focus-visible:outline-offset-3 focus-visible:outline-(color:--landing-focus)"
        >
          <Briefcase className="size-6" aria-hidden="true" />
          <span className="text-lg font-semibold">Cerco persone per un progetto</span>
          <span className="text-sm text-muted-foreground">
            Raccontaci cosa serve e per quanto: ti proponiamo chi ha già fatto cose simili.
          </span>
          <span className="mt-auto inline-flex items-center gap-1 text-sm font-medium">
            Raccontaci il progetto{' '}
            <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />
          </span>
        </Link>
      </div>
      <p className="text-sm text-muted-foreground">
        Hai già un progetto in mente?{' '}
        <Link to="/team" className="font-medium text-foreground underline underline-offset-2">
          Cerca un team
        </Link>
        : te lo proponiamo subito, senza nomi.
      </p>
    </div>
  )
}
