import { Link } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import { TeamBuilder } from '@/components/TeamBuilder'

/** The public team builder, `/hub/team` (P-REB-43, spec § 3.1, § 4.3): no login, the
 *  wizards' chrome, the builder, and the beta box that points a company wanting the
 *  whole cloud at the company wizard, with `da=team-builder` as its origin. */
export function Team() {
  return (
    <div className="mx-auto w-full max-w-3xl space-y-10">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">
          Descrivi il progetto, ti proponiamo il team
        </h1>
        <p className="mt-2 text-muted-foreground">
          Scrivi cosa va fatto: leggiamo i profili dei talenti di rebase e di solito in pochi secondi ti
          proponiamo un team, senza nomi, con una fascia di prezzo. Se ti convince, lo assumi da qui.
        </p>
      </div>
      <TeamBuilder mode="public" />
      <aside
        aria-label="Talent cloud"
        className="space-y-3 border-l-4 border-(--landing-ink) bg-card py-3 pl-4 pr-2"
      >
        <p>
          Il team builder è in beta e senza limiti. Le aziende che entrano nel talent cloud vedono i
          profili per nome, sfogliano tutto il cloud e chiedono i talenti direttamente.
        </p>
        <Button asChild variant="outline">
          <Link to="/companies" search={{ da: 'team-builder' }}>
            Chiedi l’accesso al talent cloud
          </Link>
        </Button>
      </aside>
    </div>
  )
}
