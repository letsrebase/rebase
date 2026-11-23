import { BadgeCheck, FileText, ShieldCheck } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { cn } from '@rebase/ui/cn'
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@rebase/ui/sheet'
import { toast } from '@rebase/ui/sonner'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { ApiError, cloud, type CloudTalent } from '@/lib/api'
import { bandLabel } from '@/lib/bands'
import { CLOUD_VETTED_LABEL, REMOTE_LABELS, SENIORITY_LABELS } from '@/lib/format'

/** The builder's own thanks (`TeamBuilder.tsx`): a request from a card is the same
 *  request, and the same people write back. */
const THANKS = 'Grazie: ti scriviamo entro due giorni lavorativi.'

/** How many skills and fields of work the card itself shows; the panel shows them all. */
const CARD_SKILLS = 4
const CARD_DOMAINS = 3

/** The panel is a side sheet from this width, a bottom sheet below it. */
const WIDE = '(min-width: 640px)'

type WorkProfile = NonNullable<CloudTalent['work_profile']>
type Domain = WorkProfile['domains'][number]

/** The API's own sentence whenever it gave one; ours only when nothing answered. */
function sentence(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/** A link the freelancer gave, as its host («github.com/ada» reads as «github.com»);
 *  anything that is not http(s) is not a link this page opens. */
function linkLabel(url: string): string | null {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.host.replace(/^www\./, '') : null
  } catch {
    return null
  }
}

/** «Senior · 9 anni»: the level a card gives, without the role, and nothing for the years
 *  the CV did not date. */
function level(card: CloudTalent['card']): string {
  const seniority = `${SENIORITY_LABELS[card.seniority] ?? card.seniority}${card.seniority_uncertain ? ' (da verificare)' : ''}`
  const years = card.experience_years
  if (years === null) return seniority
  return `${seniority} · ${years === 0 ? 'meno di un anno' : years === 1 ? '1 anno' : `${years} anni`}`
}

function sessionsLabel(sessions: number): string {
  return sessions === 1 ? '1 sessione' : `${sessions} sessioni`
}

/** «Fintech · 9 sessioni»: a field of work with the one count the card has room for. */
function domainTag(domain: Domain): string {
  return domain.sessions === null ? domain.label : `${domain.label} · ${sessionsLabel(domain.sessions)}`
}

/** A tag that may be long (a field of work, a language): it wraps instead of running off a phone. */
const WRAPPING = 'h-auto min-w-0 max-w-full py-0.5 break-words whitespace-normal'

function Tags({
  label,
  items,
  variant,
  limit,
}: {
  label: string
  items: string[]
  variant: 'outline' | 'secondary'
  limit?: number
}) {
  if (items.length === 0) return null
  const shown = limit === undefined ? items : items.slice(0, limit)
  const rest = items.length - shown.length
  return (
    <ul aria-label={label} className="flex min-w-0 flex-wrap gap-1.5">
      {shown.map((item, index) => (
        <li key={`${index}-${item}`} className="flex min-w-0 max-w-full items-center gap-1.5">
          <Badge variant={variant} className={WRAPPING}>
            {item}
          </Badge>
          {/* «+N» belongs to the last tag's item, so it wraps with it and never stands alone. */}
          {rest > 0 && index === shown.length - 1 && (
            <span className="shrink-0 text-xs text-muted-foreground">
              <span aria-hidden="true">+{rest}</span>
              <span className="sr-only">{`e altre ${rest}`}</span>
            </span>
          )}
        </li>
      ))}
    </ul>
  )
}

/** The three things a card says of a talent's rate: how they work and the band. */
function ModeAndBand({ talent }: { talent: CloudTalent }) {
  return (
    <p className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5 tabular-nums">
      {talent.mode && <span className="text-muted-foreground">{REMOTE_LABELS[talent.mode]}</span>}
      <span className="font-medium">{talent.band ? bandLabel(talent.band) : 'Tariffa da definire'}</span>
    </p>
  )
}

function VerifiedPill() {
  return (
    <Badge variant="pill" className="gap-1">
      <BadgeCheck aria-hidden="true" />
      {CLOUD_VETTED_LABEL}
    </Badge>
  )
}

/** «Richiedi»: files a request for the talent through the cloud, thanks in a toast. A
 *  `preview` card (the admin's «Come lo legge un'azienda») files nothing. */
function useAsk(talent: CloudTalent, preview: boolean) {
  const [state, setState] = useState<'idle' | 'sending' | 'sent'>('idle')
  const name = `${talent.first_name} ${talent.last_name}`
  async function ask() {
    setState('sending')
    try {
      await cloud.ask(talent.freelancer_id)
      setState('sent')
      toast.success(THANKS)
    } catch (error) {
      setState('idle')
      toast.error(sentence(error, 'Non siamo riusciti a inviare la richiesta. Riprova.'))
    }
  }
  const button = (className?: string): ReactNode => (
    <Button
      type="button"
      className={className}
      aria-label={state === 'sent' ? `Richiesta inviata per ${name}` : `Richiedi ${name}`}
      disabled={preview || state !== 'idle'}
      onClick={() => void ask()}
    >
      {state === 'sent' ? 'Richiesta inviata' : state === 'sending' ? 'Invio…' : 'Richiedi'}
    </Button>
  )
  return button
}

/**
 * One talent in the cloud, kept short (REB-1159): who, the level, «Verificato da rebase»,
 * three lines of the card's description, a few skills, and for a verified work profile a
 * tinted block with the fields of work and «Leggi il profilo». Everything else is in the
 * panel the name and that button open, which reads only what the cloud read returns. The
 * footer (mode, band, «Richiedi») sits at the bottom, so cards of one row line up.
 */
export function TalentCloudCard({ talent, preview = false }: { talent: CloudTalent; preview?: boolean }) {
  const [open, setOpen] = useState(false)
  const askButton = useAsk(talent, preview)
  const { card, work_profile: profile } = talent
  const name = `${talent.first_name} ${talent.last_name}`
  return (
    <Card className="h-full min-w-0 gap-3">
      <CardHeader className="gap-1">
        <CardTitle className="min-w-0 break-words">
          <h3>
            <button
              type="button"
              className="text-left underline-offset-4 hover:underline focus-visible:underline"
              onClick={() => setOpen(true)}
            >
              {name}
            </button>
          </h3>
        </CardTitle>
        <CardDescription className="break-words">{`${card.role} · ${level(card)}`}</CardDescription>
        {talent.vetted && (
          <div className="pt-1">
            <VerifiedPill />
          </div>
        )}
      </CardHeader>
      <CardContent className="flex min-w-0 flex-1 flex-col gap-3">
        <p className="line-clamp-3 text-muted-foreground">{card.profile_summary}</p>
        <Tags label="Competenze" items={card.skills} variant="outline" limit={CARD_SKILLS} />
        {profile && (
          <section
            aria-label={`Profilo di lavoro di ${name}`}
            className="min-w-0 space-y-2 border-l-(length:--line-strong) border-accent bg-accent/10 p-3"
          >
            <h4 className="flex items-center gap-1.5 text-xs font-medium">
              <ShieldCheck className="size-3.5" aria-hidden="true" />
              Profilo di lavoro verificato
            </h4>
            <Tags
              label="Ambiti di lavoro"
              items={profile.domains.map(domainTag)}
              variant="secondary"
              limit={CARD_DOMAINS}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label={`Leggi il profilo di ${name}`}
              onClick={() => setOpen(true)}
            >
              Leggi il profilo
            </Button>
          </section>
        )}
        <div className="mt-auto flex items-end justify-between gap-3 border-t pt-3">
          <ModeAndBand talent={talent} />
          {askButton()}
        </div>
      </CardContent>
      <TalentPanel talent={talent} open={open} onOpenChange={setOpen} ask={askButton} />
    </Card>
  )
}

/** The fields of work as horizontal bars by sessions, the longest being the full width. */
function DomainBars({ domains }: { domains: Domain[] }) {
  const most = Math.max(1, ...domains.map((domain) => domain.sessions ?? 0))
  return (
    <ul aria-label="Ambiti di lavoro" className="space-y-2.5">
      {domains.map((domain) => (
        <li key={domain.label} className="space-y-1">
          <p className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 break-words font-medium">{domain.label}</span>
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
              {[
                domain.products === null ? null : domain.products === 1 ? '1 prodotto' : `${domain.products} prodotti`,
                domain.sessions === null ? null : sessionsLabel(domain.sessions),
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </p>
          {domain.sessions !== null && (
            <div aria-hidden="true" className="h-1.5 bg-secondary">
              <div className="h-full bg-foreground" style={{ width: `${(domain.sessions / most) * 100}%` }} />
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

function Section({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn('min-w-0 space-y-2', className)}>
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  )
}

/**
 * The panel of one talent: the card whole and, for an approved work profile, its summary in
 * full with the fields of work as bars and the projects' domains. It draws only what
 * `GET /api/hub/me/cloud/talents` returns for the talent, never another field.
 */
function TalentPanel({
  talent,
  open,
  onOpenChange,
  ask,
}: {
  talent: CloudTalent
  open: boolean
  onOpenChange: (open: boolean) => void
  ask: (className?: string) => ReactNode
}) {
  const wide = useMediaQuery(WIDE)
  const { card, work_profile: profile } = talent
  const name = `${talent.first_name} ${talent.last_name}`
  const links = talent.links
    .map((url) => ({ url, label: linkLabel(url) }))
    .filter((link): link is { url: string; label: string } => link.label !== null)
  const linkedin = talent.linkedin_url && linkLabel(talent.linkedin_url) ? talent.linkedin_url : null
  const anchor = 'font-medium underline underline-offset-4'
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side={wide ? 'right' : 'bottom'}
        closeLabel={`Chiudi il profilo di ${name}`}
        className="gap-0 p-0 data-[side=bottom]:max-h-[90dvh] data-[side=right]:sm:max-w-xl"
      >
        <SheetHeader className="gap-1 border-b p-5 pr-14">
          <SheetTitle className="text-xl break-words">{name}</SheetTitle>
          <SheetDescription className="break-words">{`${card.role} · ${level(card)}`}</SheetDescription>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pt-1">
            {talent.vetted && <VerifiedPill />}
            <ModeAndBand talent={talent} />
          </div>
        </SheetHeader>
        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-5">
          <Section title="Scheda">
            <p className="break-words">{card.profile_summary}</p>
            <Tags label="Competenze" items={card.skills} variant="outline" />
            <Tags label="Settori" items={card.industries} variant="secondary" />
            <Tags label="Lingue" items={card.languages} variant="secondary" />
          </Section>
          {profile && (
            <section aria-label={`Profilo di lavoro verificato di ${name}`} className="min-w-0 space-y-4 border-l-(length:--line-strong) border-accent bg-accent/10 p-4">
              <h3 className="flex items-center gap-1.5 text-xs font-medium tracking-wide uppercase">
                <ShieldCheck className="size-3.5" aria-hidden="true" />
                Profilo di lavoro verificato
              </h3>
              {profile.summary && <p className="break-words">{profile.summary}</p>}
              {profile.domains.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-medium text-muted-foreground">Dove lavora</h4>
                  <DomainBars domains={profile.domains} />
                </div>
              )}
              {profile.project_domains.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-xs font-medium text-muted-foreground">Progetti rappresentativi</h4>
                  <ul aria-label="Progetti rappresentativi" className="list-disc space-y-1 pl-4">
                    {profile.project_domains.map((domain) => (
                      <li key={domain} className="break-words">
                        {domain}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          )}
          {(linkedin || links.length > 0 || talent.has_cv) && (
            <Section title="Link e CV">
              <ul aria-label={`Link di ${name}`} className="flex flex-wrap gap-x-4 gap-y-1">
                {talent.has_cv && (
                  <li>
                    <a
                      href={cloud.cvUrl(talent.freelancer_id)}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={`Apri il CV di ${name}`}
                      className={cn(anchor, 'inline-flex items-center gap-1')}
                    >
                      <FileText className="size-4" aria-hidden="true" />
                      Apri il CV
                    </a>
                  </li>
                )}
                {linkedin && (
                  <li>
                    <a href={linkedin} target="_blank" rel="noopener noreferrer" className={anchor}>
                      LinkedIn
                    </a>
                  </li>
                )}
                {links.map((link) => (
                  <li key={link.url} className="min-w-0 break-all">
                    <a href={link.url} target="_blank" rel="noopener noreferrer" className={anchor}>
                      {link.label}
                    </a>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
        <SheetFooter className="border-t p-4">{ask('w-full')}</SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
