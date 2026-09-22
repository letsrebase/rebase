import { Link } from '@tanstack/react-router'
import { ArrowRight, Briefcase, UserRound } from 'lucide-react'

/** The front door of the hub: two doors, and a word for each. */
export function Chooser() {
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
    </div>
  )
}
