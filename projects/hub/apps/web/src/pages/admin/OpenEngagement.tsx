import { useMutation, useQuery } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useRef, useState } from 'react'
import { Button } from '@rebase/ui/button'
import { admin, ApiError, type Company } from '@/lib/api'
import { keys } from '@/lib/query-keys'
import { CompanyList } from './create-match/ChiStep'
import { Header } from './list-kit'

/** «Crea match» (REB-1105): the engagement is the CRM's, so this page only pairs the card with a
 *  company request and hands over. «Apri nel CRM» has the hub ask the CRM for the freelancer's
 *  organization, keep a `handoff` row under a new engagement id and answer the CRM's form, filled in
 *  with the request's deal, the organization and that id; the admin is sent there. A request that is
 *  not in the CRM yet has no deal to open the form on: the page says so and offers «Riprova», which
 *  files it and asks again, never a blank form. */
export function AdminOpenEngagement() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id/match/new' })
  const person = useQuery({ queryKey: keys.freelancer(id), queryFn: () => admin.freelancer(id) })
  const searchRef = useRef<HTMLInputElement>(null)
  const [company, setCompany] = useState<Company | null>(null)
  const [noDeal, setNoDeal] = useState<string | null>(null)

  const handOff = useMutation({
    mutationFn: (companyId: string) => admin.handOffEngagement(id, companyId),
    onSuccess: (answer) => {
      if (answer.state === 'ready' && answer.url) {
        setNoDeal(null)
        window.location.assign(answer.url)
        return
      }
      setNoDeal(answer.message ?? 'Questa richiesta non è ancora nel CRM.')
    },
  })
  const retry = useMutation({
    mutationFn: (companyId: string) => admin.linkCompanyToCrm(companyId),
    onSuccess: (_linked, companyId) => handOff.mutate(companyId),
  })

  const name = person.data ? `${person.data.first_name} ${person.data.last_name}` : null
  const failure = [handOff.error, retry.error].find((error) => error !== null)
  const failureText =
    failure instanceof ApiError ? failure.message : failure ? 'Non riesco ad aprire il CRM: riprova tra poco.' : null
  const busy = handOff.isPending || retry.isPending

  return (
    <>
      <Header title={name ? `Crea match · ${name}` : 'Crea match'} />
      <div className="max-w-3xl space-y-4 p-6">
        <Link
          to="/admin/freelance/$id/contracts"
          params={{ id }}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
          Match e contratti
        </Link>
        <p className="text-sm text-muted-foreground">
          L’incarico si scrive nel CRM: scegli la richiesta dell’azienda e ti porto al modulo, già compilato con il suo
          deal e questo freelance.
        </p>
        <CompanyList
          selected={company}
          onSelect={(next) => {
            setCompany(next)
            setNoDeal(null)
            handOff.reset()
            retry.reset()
          }}
          searchRef={searchRef}
        />
        {noDeal && (
          <div role="status" className="space-y-2 border bg-card p-4 text-sm">
            <p>{noDeal}</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy || company === null}
              onClick={() => company && retry.mutate(company.id)}
            >
              {retry.isPending ? 'Riprovo…' : 'Riprova'}
            </Button>
          </div>
        )}
        {failureText && (
          <p role="alert" className="text-sm text-destructive">
            {failureText}
          </p>
        )}
        <Button type="button" disabled={company === null || busy} onClick={() => company && handOff.mutate(company.id)}>
          {handOff.isPending ? 'Apro il CRM…' : 'Apri nel CRM'}
        </Button>
      </div>
    </>
  )
}
