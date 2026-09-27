import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import type { CampaignAzione, CampaignMeta } from '@/lib/api'
import { AZIONE_LABELS, CAMPAIGN_MAX_LENGTH, META_LABELS } from '@/lib/campaigns'
import type { CampaignForm } from './form'

/** Messaggio (REB-526): what the mail says and where its button leads. The preview
 *  beside it follows every keystroke. */
export function Messaggio({ form, onChange }: { form: CampaignForm; onChange: (patch: Partial<CampaignForm>) => void }) {
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
          <Select value={form.bottoneMeta} onValueChange={(value) => onChange({ bottoneMeta: value as CampaignMeta })}>
            <SelectTrigger id="campagna-bottone-meta" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(META_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {form.fonte === 'stato' ? (
        // A state brings its own action (spec § 1): a sentence, not a menu that cannot
        // be opened, and nothing before a state is picked.
        form.statoPercorso !== null && (
          <p className="text-sm">
            <span className="text-muted-foreground">Cosa misuriamo:</span> {AZIONE_LABELS[form.azione]}
          </p>
        )
      ) : (
        <div className="max-w-sm space-y-1.5">
          <Label htmlFor="campagna-azione">Cosa misuriamo</Label>
          <Select value={form.azione} onValueChange={(value) => onChange({ azione: value as CampaignAzione })}>
            <SelectTrigger id="campagna-azione" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(AZIONE_LABELS)
                .filter(([value]) => value !== 'pigro_cliente')
                .map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">Chi l’ha già fatto quando la mail parte viene saltato.</p>
        </div>
      )}
    </section>
  )
}
