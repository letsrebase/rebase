import { useQuery } from '@tanstack/react-query'
import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@rebase/ui/popover'
import { member, type MemberReferral as MemberReferralData } from '@/lib/api'
import { formatDate, formatEuro } from '@/lib/format'
import { EXAMPLE, formatRate, rateWithArticle, referralExample } from '@/lib/referral'

const KIND_LABELS: Record<string, string> = { freelancer: 'Freelance', company: 'Azienda' }

/** The member's own link (P-REB-44): the chooser's own URL with `?rif=` appended, so a
 *  detour through it into either wizard still carries the code -- the same persistence
 *  `da=`'s own UTM mechanism already gives a campaign (`resolveReferral`, `lib/utm.ts`). */
function referralLink(code: string): string {
  return `${window.location.origin}/hub/?rif=${code}`
}

function CopyLinkButton({ link }: { link: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* a clipboard a browser refused: the input beside it still holds the link */
    }
  }
  return (
    <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>
      {copied ? <Check className="mr-2 size-4" /> : <Copy className="mr-2 size-4" />}
      {copied ? 'Copiato' : 'Copia link'}
    </Button>
  )
}

/** What each referral earns, in the two live rates, and «Come si calcola?» beside it
 *  (REB-610). Both come from the API on every load, never from the copy: an admin edits
 *  them on the ledger. A rate the API answered in a shape this page cannot read is left
 *  out, so the sentence never says a number that is wrong; with neither, nothing at all
 *  is said and the link below still works. */
function Earnings({ rates }: { rates: Pick<MemberReferralData, 'rate_freelancer' | 'rate_company'> }) {
  const freelancer = formatRate(rates.rate_freelancer)
  const company = formatRate(rates.rate_company)
  if (freelancer === null && company === null) return null
  const example = referralExample(rates)
  const said =
    freelancer && company
      ? `Se segnali un freelance ricevi ${rateWithArticle(rates.rate_freelancer)} del margine di rebase, se segnali un'azienda ${rateWithArticle(rates.rate_company)}.`
      : freelancer
        ? `Se segnali un freelance ricevi ${rateWithArticle(rates.rate_freelancer)} del margine di rebase.`
        : `Se segnali un'azienda ricevi ${rateWithArticle(rates.rate_company)} del margine di rebase.`
  return (
    <p className="text-sm">
      {said}{' '}
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="whitespace-nowrap font-medium underline underline-offset-2 hover:text-muted-foreground"
          >
            Come si calcola?
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          aria-label="Come si calcola il compenso di una segnalazione"
          className="w-80 max-w-(--radix-popover-content-available-width) max-h-(--radix-popover-content-available-height) gap-3 overflow-y-auto p-4 sm:w-96"
        >
          <p>
            Il compenso matura una volta sola, quando la persona o l&apos;azienda che hai portato firma
            la sua prima lettera d&apos;incarico.
          </p>
          <p>
            È una percentuale del margine di rebase su quell&apos;incarico, cioè di quello che paga il
            cliente meno quello che guadagna il freelance, non del prezzo del cliente.
          </p>
          <p>
            Vale la percentuale in vigore alla firma. rebase conferma il compenso e poi te lo paga. Per
            un incarico a corpo senza una stima dei giorni, l&apos;importo lo stabilisce rebase caso per
            caso.
          </p>
          <div className="space-y-1 border-t pt-3">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Esempio con numeri inventati
            </p>
            <p className="text-muted-foreground">
              Un incarico a giornata: il cliente paga {EXAMPLE.budget} € al giorno, il freelance ne
              guadagna {EXAMPLE.compenso}, per {EXAMPLE.giorni} giorni.
            </p>
            <p>
              Margine: ({EXAMPLE.budget} - {EXAMPLE.compenso}) × {EXAMPLE.giorni} ={' '}
              <span className="font-medium tabular-nums">{formatEuro(example.margin)}</span>
            </p>
            {freelancer && example.freelancer && (
              <p>
                Segnali un freelance: {freelancer} di {formatEuro(example.margin)} ={' '}
                <span className="font-medium tabular-nums">{formatEuro(example.freelancer)}</span>
              </p>
            )}
            {company && example.company && (
              <p>
                Segnali un&apos;azienda: {company} di {formatEuro(example.margin)} ={' '}
                <span className="font-medium tabular-nums">{formatEuro(example.company)}</span>
              </p>
            )}
          </div>
        </PopoverContent>
      </Popover>
    </p>
  )
}

/** «Il tuo link di segnalazione» in the member area (P-REB-44): a member's own code,
 *  issued the first time this section reads it, what a referral earns (the two live
 *  rates and a popover with an invented worked example, REB-610), and who has signed up
 *  under it so far. Percentages are shown; a euro figure of a real engagement never is
 *  -- rebase's own margin on it is an admin-only number, computed and shown only on the
 *  admin's ledger. */
export function MemberReferral() {
  const referral = useQuery({ queryKey: ['me', 'referral'], queryFn: () => member.referral() })
  const data = referral.data

  return (
    <section aria-labelledby="me-referral" className="space-y-3">
      <h2 id="me-referral" className="text-lg font-semibold tracking-tight">
        Il tuo link di segnalazione
      </h2>
      {referral.isError ? (
        <p className="text-sm text-muted-foreground">Non riesco a leggere il tuo link. Riprova tra poco.</p>
      ) : !data ? (
        <p className="text-sm text-muted-foreground">Caricamento…</p>
      ) : (
        <div className="space-y-3 border bg-card p-4">
          <Earnings rates={data} />
          <p className="text-sm text-muted-foreground">
            Segnala rebase a un freelance o a un&apos;azienda con questo link: lo vedi qui sotto appena
            si iscrive.
          </p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input readOnly value={referralLink(data.code)} aria-label="Link di segnalazione da copiare" />
            <CopyLinkButton link={referralLink(data.code)} />
          </div>
          {data.referred.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nessuna segnalazione ancora.</p>
          ) : (
            <ul className="divide-y border-t pt-3">
              {data.referred.map((item, index) => (
                <li
                  key={`${item.kind}-${item.nome}-${index}`}
                  className="flex items-center justify-between gap-4 py-2 text-sm"
                >
                  <div>
                    <p className="font-medium">{item.nome}</p>
                    <p className="text-xs text-muted-foreground">{KIND_LABELS[item.kind] ?? item.kind}</p>
                  </div>
                  <p className="text-xs text-muted-foreground">{formatDate(item.created_at)}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
