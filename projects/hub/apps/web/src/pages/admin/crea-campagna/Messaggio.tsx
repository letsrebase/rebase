import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import type { CampaignAzione, CampaignMeta } from '@/lib/api'
import { AZIONE_LABELS, CAMPAIGN_MAX_LENGTH } from '@/lib/campaigns'
import { linkProblem, metaOptions, type CampaignForm } from './form'

/** Messaggio (REB-526): what the mail says and where its button leads. The preview
 *  beside it follows every keystroke. «Dove porta» goes through `onMeta` and the link's
 *  address through `onLink`, not `onChange`: «Un link» brings its own action, and
 *  choosing where the button leads is not writing the mail (REB-530). */
export function Messaggio({
  form,
  onChange,
  onMeta,
  onLink,
}: {
  form: CampaignForm
  onChange: (patch: Partial<CampaignForm>) => void
  onMeta: (meta: CampaignMeta) => void
  onLink: (url: string) => void
}) {
  const link = form.bottoneMeta === 'link'
  // Said while typing, but not before anything is typed: an empty field shows what to
  // write in it, and the header says the draft waits for it.
  const problem = link && form.bottoneUrl.trim() !== '' ? linkProblem(form) : null
  return (
    <section aria-labelledby="campagna-messaggio" className="space-y-4">
      <h2 id="campagna-messaggio" className="text-lg font-semibold">
        Messaggio
      </h2>
      <div className="space-y-1.5">
        <Label htmlFor="campagna-oggetto">Oggetto</Label>
        <Input
          id="campagna-oggetto"
          maxLength={CAMPAIGN_MAX_LENGTH.oggetto}
          value={form.oggetto}
          onChange={(event) => onChange({ oggetto: event.target.value })}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="campagna-testo">Testo</Label>
        <Textarea
          id="campagna-testo"
          rows={10}
          maxLength={CAMPAIGN_MAX_LENGTH.testo}
          aria-describedby="campagna-testo-aiuto"
          value={form.testo}
          onChange={(event) => onChange({ testo: event.target.value })}
        />
        <p id="campagna-testo-aiuto" className="text-xs text-muted-foreground">
          {'{nome}'} diventa il nome della persona. Una riga vuota separa i paragrafi.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="campagna-bottone-testo">Testo del bottone</Label>
          <Input
            id="campagna-bottone-testo"
            maxLength={CAMPAIGN_MAX_LENGTH.bottone_testo}
            value={form.bottoneTesto}
            onChange={(event) => onChange({ bottoneTesto: event.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="campagna-bottone-meta">Dove porta</Label>
          <Select value={form.bottoneMeta} onValueChange={(value) => onMeta(value as CampaignMeta)}>
            <SelectTrigger id="campagna-bottone-meta" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {metaOptions(form).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {link && (
        <div className="space-y-1.5">
          <Label htmlFor="campagna-bottone-url">Indirizzo del link</Label>
          <Input
            id="campagna-bottone-url"
            type="url"
            placeholder="https://lu.ma/…"
            maxLength={CAMPAIGN_MAX_LENGTH.bottone_url}
            aria-invalid={problem !== null}
            aria-describedby="campagna-bottone-url-aiuto"
            value={form.bottoneUrl}
            onChange={(event) => onLink(event.target.value)}
          />
          <p id="campagna-bottone-url-aiuto" className={problem ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
            {problem ?? 'Una pagina fuori dal hub: un evento su Luma, un gruppo WhatsApp, un modulo. Deve iniziare con https://.'}
          </p>
        </div>
      )}
      {link ? (
        // Of a page the hub does not own, the click is all it sees (REB-530).
        <div className="space-y-1">
          <p className="text-sm">
            <span className="text-muted-foreground">Cosa misuriamo:</span> {AZIONE_LABELS.clic}
          </p>
          <p className="text-xs text-muted-foreground">Di una pagina fuori dal hub vediamo solo il clic sul bottone.</p>
        </div>
      ) : form.fonte === 'filtri' ? (
        <div className="max-w-sm space-y-1.5">
          <Label htmlFor="campagna-azione">Cosa misuriamo</Label>
          <Select value={form.azione} onValueChange={(value) => onChange({ azione: value as CampaignAzione })}>
            <SelectTrigger id="campagna-azione" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(AZIONE_LABELS)
                .filter(([value]) => value !== 'pigro_cliente' && value !== 'clic')
                .map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">Chi l’ha già fatto quando la mail parte viene saltato.</p>
        </div>
      ) : (
        // A state brings its own action (spec § 1), and a `lista` keeps its parent's
        // (Task 6): a sentence, not a menu that cannot be opened, and nothing before a
        // state is picked.
        (form.fonte === 'lista' || form.statoPercorso !== null) && (
          <p className="text-sm">
            <span className="text-muted-foreground">Cosa misuriamo:</span> {AZIONE_LABELS[form.azione]}
          </p>
        )
      )}
    </section>
  )
}
