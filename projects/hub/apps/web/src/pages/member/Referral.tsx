import { useQuery } from '@tanstack/react-query'
import { Check, Copy } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { member } from '@/lib/api'
import { formatDate } from '@/lib/format'

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

/** «Il tuo link di segnalazione» in the member area (P-REB-44): a member's own code,
 *  issued the first time this section reads it, and who has signed up under it so
 *  far. Never a euro figure -- rebase's own margin on a referred engagement is an
 *  admin-only number, computed and shown only on the admin's ledger. */
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
          <p className="text-sm text-muted-foreground">
            Segnala rebase a un freelance o a un&apos;azienda con questo link: se firmano un incarico, lo
            vedi qui sotto.
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
