import { useState, type FormEvent } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { WebLink } from '@/components/WebLink'
import { ApiError, type Company } from '@/lib/api'
import { dealIdOf } from '@/lib/crm'
import { formatDate } from '@/lib/format'
import { isHttpUrl } from '@/lib/url'

const STATE_WORDS: Record<string, string> = {
  da_collegare: 'In attesa di essere registrata nel CRM',
  collegato: 'Registrata nel CRM',
  errore: 'Non ancora registrata nel CRM',
  rifiutato: 'Rifiutata dal CRM',
}

/** Where a request stands in rebase's CRM (REB-869): its state, the CRM's sentence when it
 *  is not linked, the deal's address when it is, «Riprova» to file it now and «Collega a un
 *  altro deal» to point it at a deal the CRM already has (spec 2026-10-30 § 4). The link is
 *  made by the hub's API with its own token; this page only asks. */
export function CrmLink({
  company,
  pending,
  onLink,
  error,
}: {
  company: Pick<Company, 'crm_stato' | 'crm_situazione' | 'crm_deal_url' | 'crm_attempted_at'>
  pending: boolean
  onLink: (dealId?: string) => void
  error: Error | null
}) {
  const [text, setText] = useState('')
  const [invalid, setInvalid] = useState(false)
  const failure =
    error instanceof ApiError ? error.message : error ? 'Non riesco a collegare la richiesta al CRM.' : null
  const linked = company.crm_stato === 'collegato'

  function submit(event: FormEvent) {
    event.preventDefault()
    const deal = dealIdOf(text)
    setInvalid(deal === null)
    if (deal !== null) {
      onLink(deal)
      setText('')
    }
  }

  return (
    <section aria-label="CRM" className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">CRM</h2>
      {company.crm_stato === null ? (
        <p className="text-sm text-muted-foreground">
          Richiesta arrivata prima del collegamento: non è nel CRM finché non la registri qui.
        </p>
      ) : (
        <p className="text-sm">
          <strong>{STATE_WORDS[company.crm_stato]}.</strong>
          {company.crm_attempted_at ? ` Ultimo tentativo il ${formatDate(company.crm_attempted_at)}.` : ''}
        </p>
      )}
      {company.crm_situazione && (
        <p role="status" className="text-sm text-muted-foreground">
          {company.crm_situazione}
        </p>
      )}
      {linked && company.crm_deal_url && isHttpUrl(company.crm_deal_url) && (
        <p className="text-sm">
          <WebLink href={company.crm_deal_url}>Apri il deal nel CRM</WebLink>
        </p>
      )}
      {!linked && (
        <Button type="button" size="sm" onClick={() => onLink()} disabled={pending}>
          {pending ? 'Collego…' : company.crm_stato === null ? 'Registra nel CRM' : 'Riprova'}
        </Button>
      )}
      <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="crm-deal">Collega a un altro deal</Label>
          <Input
            id="crm-deal"
            value={text}
            onChange={(event) => {
              setText(event.target.value)
              setInvalid(false)
            }}
            placeholder="Indirizzo o id del deal nel CRM"
            className="w-96 max-w-full"
          />
        </div>
        <Button type="submit" variant="outline" size="sm" disabled={pending || text.trim() === ''}>
          Collega al deal
        </Button>
      </form>
      {invalid && (
        <p role="alert" className="text-sm text-destructive">
          Non trovo l’id di un deal in quello che hai scritto.
        </p>
      )}
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </section>
  )
}
