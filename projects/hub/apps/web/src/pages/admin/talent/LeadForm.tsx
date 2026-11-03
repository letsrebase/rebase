import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import { WebLink } from '@/components/WebLink'
import { admin, ApiError, type FreelancerDraft, type Remoto } from '@/lib/api'
import { AMOUNT_PROBLEM, acceptedAmount, sentAmount } from '@/lib/amount'
import { REMOTO_LABELS, formatDate } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { Empty, Header, Row, StatePill } from '../list-kit'

interface LeadDraft {
  nome: string
  cognome: string
  linkedin_url: string
  posizione: string
  tariffa_giornaliera: string
  remoto: Remoto | ''
  links: string
  fonti: string
}

const LEAD_DRAFT_EMPTY: LeadDraft = {
  nome: '',
  cognome: '',
  linkedin_url: '',
  posizione: '',
  tariffa_giornaliera: '',
  remoto: '',
  links: '',
  fonti: '',
}

/** A bare sign-up (ORB-163, REB-283): what the landing knows, and the form that turns
 *  it into a card in place, through the same `draft_from_signup` the MCP tool
 *  `create_freelancer_from_signup` calls (ORB-155). One line per URL for «Link» and
 *  «Fonti»; at least one source is required, since a card written from research with
 *  no source is a card nobody can check. The row is read on its own, by the sign-up's id. */
export function AdminTalentoLead() {
  const { id } = useParams({ from: '/signedIn/admin/talent/$id' })
  const navigate = useNavigate()
  const client = useQueryClient()
  const read = useQuery({ queryKey: keys.signup(id), queryFn: () => admin.signup(id) })
  const lead = read.data
  const [draft, setDraft] = useState(LEAD_DRAFT_EMPTY)
  const seeded = useRef(false)
  useEffect(() => {
    if (lead && !seeded.current) {
      seeded.current = true
      setDraft((current) => ({
        ...current,
        nome: lead.nome ?? current.nome,
        cognome: lead.cognome ?? current.cognome,
        linkedin_url: lead.linkedin_url ?? current.linkedin_url,
      }))
    }
  }, [lead])

  const draftCard = useMutation({
    mutationFn: (data: FreelancerDraft) => admin.draftFromSignup(id, data),
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: keys.talents() })
      void client.invalidateQueries({ queryKey: keys.signup(id) })
      void navigate({ to: '/admin/freelance/$id', params: { id: created.id } })
    },
  })
  const failure = draftCard.error instanceof ApiError ? draftCard.error : null
  const message = failure ? failure.message : draftCard.error ? 'Non riesco a creare la scheda.' : null
  const wrong = (field: string) => failure?.fields.includes(field) || undefined

  function field(name: keyof LeadDraft) {
    return (value: string) => setDraft((current) => ({ ...current, [name]: value }))
  }

  // Blank is allowed: a card drafted from research may not know the rate.
  const rateProblem = draft.tariffa_giornaliera.trim() !== '' && !acceptedAmount(draft.tariffa_giornaliera)

  function submit(event: FormEvent) {
    event.preventDefault()
    if (rateProblem) return
    draftCard.mutate({
      nome: draft.nome.trim(),
      cognome: draft.cognome.trim(),
      linkedin_url: draft.linkedin_url.trim() || undefined,
      posizione: draft.posizione.trim() || undefined,
      tariffa_giornaliera: sentAmount(draft.tariffa_giornaliera) || undefined,
      remoto: draft.remoto || undefined,
      links: draft.links.split('\n').map((line) => line.trim()).filter(Boolean),
      fonti: draft.fonti.split('\n').map((line) => line.trim()).filter(Boolean),
    })
  }

  if (read.error instanceof ApiError && read.error.status === 404) return <Empty>Lead non trovato.</Empty>
  if (read.isError) return <Empty>Non riesco a leggere questo lead.</Empty>
  if (!lead) return <Empty>Caricamento…</Empty>
  return (
    <>
      <Header title={[lead.nome, lead.cognome].filter(Boolean).join(' ') || lead.email}>
        <StatePill stato="lead" />
      </Header>
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <dl className="space-y-3 text-sm lg:col-span-2">
          <Row label="Email">
            <a className="underline underline-offset-2" href={`mailto:${lead.email}`}>
              {lead.email}
            </a>
          </Row>
          <Row label="LinkedIn">
            {lead.linkedin_url ? <WebLink href={lead.linkedin_url} /> : '—'}
          </Row>
          <Row label="Arrivato">
            {formatDate(lead.created_at)}
            {lead.utm_source ? ` · da ${lead.utm_source}` : ''}
          </Row>
        </dl>
        <form onSubmit={submit} className="space-y-3 border p-4">
          <p className="text-sm font-medium">Scrivi la scheda</p>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="lead-nome">Nome</Label>
              <Input
                id="lead-nome"
                required
                maxLength={120}
                value={draft.nome}
                onChange={(event) => field('nome')(event.target.value)}
                aria-invalid={wrong('nome')}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="lead-cognome">Cognome</Label>
              <Input
                id="lead-cognome"
                required
                maxLength={120}
                value={draft.cognome}
                onChange={(event) => field('cognome')(event.target.value)}
                aria-invalid={wrong('cognome')}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lead-linkedin">LinkedIn</Label>
            <Input
              id="lead-linkedin"
              value={draft.linkedin_url}
              onChange={(event) => field('linkedin_url')(event.target.value)}
              aria-invalid={wrong('linkedin_url')}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lead-posizione">Posizione</Label>
            <Input
              id="lead-posizione"
              value={draft.posizione}
              onChange={(event) => field('posizione')(event.target.value)}
              aria-invalid={wrong('posizione')}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="lead-tariffa">Tariffa a giornata</Label>
              <Input
                id="lead-tariffa"
                inputMode="decimal"
                value={draft.tariffa_giornaliera}
                onChange={(event) => field('tariffa_giornaliera')(event.target.value)}
                aria-invalid={rateProblem || wrong('tariffa_giornaliera')}
                aria-describedby={rateProblem ? 'lead-tariffa-error' : undefined}
              />
              {rateProblem && (
                <p role="alert" id="lead-tariffa-error" className="text-sm text-destructive">
                  {AMOUNT_PROBLEM}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="lead-remoto">Modalità</Label>
              <select
                id="lead-remoto"
                value={draft.remoto}
                onChange={(event) => field('remoto')(event.target.value)}
                className="h-9 w-full border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <option value="">—</option>
                {(Object.keys(REMOTO_LABELS) as Remoto[]).map((value) => (
                  <option key={value} value={value}>
                    {REMOTO_LABELS[value]}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lead-links">Link</Label>
            <Textarea
              id="lead-links"
              rows={2}
              placeholder="Un URL per riga"
              value={draft.links}
              onChange={(event) => field('links')(event.target.value)}
              aria-invalid={wrong('links')}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="lead-fonti">Fonti</Label>
            <Textarea
              id="lead-fonti"
              required
              rows={2}
              placeholder="Da dove viene questa scheda: un URL per riga"
              value={draft.fonti}
              onChange={(event) => field('fonti')(event.target.value)}
              aria-invalid={wrong('fonti')}
            />
          </div>
          <Button type="submit" size="sm" disabled={draftCard.isPending}>
            {draftCard.isPending ? 'Creo…' : 'Crea scheda'}
          </Button>
          {message && (
            <p role="alert" className="text-sm text-destructive">
              {message}
            </p>
          )}
        </form>
      </div>
      <p className="px-6 pb-6">
        <Link to="/admin/talent" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutti i talenti
        </Link>
      </p>
    </>
  )
}
