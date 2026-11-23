import { CircleAlert, Lock } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { PracticeTable } from '@/components/Practices'
import { TalentCloudCard } from '@/components/TalentCloudCard'
import { UsageBadges } from '@/components/TechUsage'
import { WebLink } from '@/components/WebLink'
import { useFreelancerCard } from '@/hooks/use-freelancer-card'
import { type Freelancer, type WorkProfileAdmin } from '@/lib/api'
import { cloudTalent } from '@/lib/cloudProfile'
import { LoadingState } from '../list-kit'
import { AnonymityResult } from './ProfileDecision'

type Anchor = WorkProfileAdmin['unsupported_anchors'][number]
type Profile = WorkProfileAdmin['profile']
type Anchors = Map<string, Anchor[]>

/** The sentence a person sends a profile with, so the empty state says how to get one. */
const COMMAND = 'npx @letsrebase/verify'

/** What the recheck calls each kind of claim, in the admin's words. */
const ANCHOR_KINDS: Record<string, string> = {
  number: 'numero',
  percent: 'percentuale',
  period: 'periodo',
  tech: 'tecnologia',
  tag: 'tipo di lavoro',
}

/** A phone-width `(min-width)` is no help to a block that sits in a column of its own: the grids
 *  below ask their container how wide it is. */
const TWO_UP = '@3xl:grid-cols-2'

const number = new Intl.NumberFormat('it-IT')

const monthShort = new Intl.DateTimeFormat('it-IT', { month: 'short', timeZone: 'UTC' })

/** `2026-07` (or a longer date) as a month count since year zero, or `null` for anything else. */
function monthIndex(value: string | null): number | null {
  const match = value ? /^(\d{4})-(\d{2})/.exec(value) : null
  return match ? Number(match[1]) * 12 + Number(match[2]) - 1 : null
}

/** «5 mesi» with «lug – nov 2026» under it, from the profile's window; `null` when it has none. */
function period(from: string | null, to: string | null): { months: string; span: string } | null {
  const start = monthIndex(from)
  const end = monthIndex(to)
  if (start === null || end === null || end < start) return null
  const label = (index: number) => ({
    month: monthShort.format(new Date(Date.UTC(Math.floor(index / 12), index % 12, 1))).replace('.', ''),
    year: Math.floor(index / 12),
  })
  const [a, b] = [label(start), label(end)]
  const count = end - start + 1
  return {
    months: count === 1 ? '1 mese' : `${count} mesi`,
    span: a.year === b.year ? `${a.month} – ${b.month} ${b.year}` : `${a.month} ${a.year} – ${b.month} ${b.year}`,
  }
}

/** A count of a lens, by its label, for the figures band. */
function lensCount(profile: Profile, lens: string, label: string): string | null {
  return profile.lenses.find((candidate) => candidate.key === lens)?.counts.find((count) => count.label === label)?.value ?? null
}

/** The profile's size in one band: sessions, products, the days it was active out of those
 *  observed, the period it covers and how much of the prose the data backs. A figure the profile
 *  does not carry is left out, not shown as zero. */
function Figures({ profile }: { profile: WorkProfileAdmin }) {
  const view = profile.profile
  const active = lensCount(view, 'intensity', 'Giorni attivi')
  const observed = lensCount(view, 'intensity', 'Giorni osservati')
  const span = period(view.window_from, view.window_to)
  const figures: { label: string; value: ReactNode; note?: string }[] = [
    view.sessions !== null ? { label: 'Sessioni', value: number.format(view.sessions) } : null,
    view.products !== null ? { label: 'Prodotti', value: number.format(view.products) } : null,
    active !== null
      ? {
          label: 'Giorni attivi',
          value: (
            <>
              {active}
              {observed !== null && <span className="text-base font-normal text-muted-foreground"> su {observed}</span>}
            </>
          ),
        }
      : null,
    span ? { label: span.span, value: span.months } : null,
    { label: 'Verificabile', value: `${profile.groundedness}%` },
  ].filter((figure) => figure !== null)
  return (
    <dl
      aria-label="Numeri del profilo"
      className="grid grid-cols-2 gap-px border bg-border [&>:last-child:nth-child(odd)]:col-span-2 @2xl:auto-cols-fr @2xl:grid-flow-col @2xl:grid-cols-none @2xl:[&>:last-child:nth-child(odd)]:col-span-1"
    >
      {figures.map((figure) => (
        <div key={figure.label} className="bg-card p-3">
          <dt className="text-xs text-muted-foreground">{figure.label}</dt>
          <dd className="text-2xl font-semibold tracking-tight tabular-nums">{figure.value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** The claims of one piece of prose the hub could not find in the profile's own data, set
 *  right under that prose as amber notes: the signature of this section (spec 2026-11-17 § 10). */
function AnchorNotes({ where, anchors }: { where: string; anchors: Anchors }) {
  const found = anchors.get(where)
  if (!found?.length) return null
  return (
    <ul aria-label="Da verificare in questo testo" className="mt-1.5 space-y-1">
      {found.map((anchor, index) => (
        <li
          key={`${index}-${anchor.anchor}`}
          className="flex items-start gap-1.5 border-l-(length:--line-strong) border-accent bg-accent/15 px-2 py-1 text-xs"
        >
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 break-words">
            Non trovato nei dati: «{anchor.anchor}» ({ANCHOR_KINDS[anchor.kind] ?? anchor.kind})
          </span>
        </li>
      ))}
    </ul>
  )
}

/** A narrative with its anchors; nothing when the person's model wrote none. */
function Prose({ text, where, anchors, className }: { text: string | null; where: string; anchors: Anchors; className?: string }) {
  if (!text) return null
  return (
    <>
      <p className={cn('break-words whitespace-pre-line', className)}>{text}</p>
      <AnchorNotes where={where} anchors={anchors} />
    </>
  )
}

/** A block of the profile: a heading over a bordered surface. */
function Block({ title, id, children, className }: { title: string; id?: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn('min-w-0 space-y-3 border bg-card p-4', className)}>
      <h2 id={id} className="text-sm font-medium">
        {title}
      </h2>
      {children}
    </section>
  )
}

/** The places the page renders prose for, so an anchor filed under a place it does not show
 *  (a project the profile no longer lists) is still listed, at the top, not lost. */
function renderedPlaces(profile: Profile): Set<string> {
  const places = new Set<string>(['summary', 'domains', 'trajectory.principles'])
  for (const domain of profile.domains) places.add(domain.where)
  for (const lens of profile.lenses) places.add(lens.where)
  if (profile.trajectory) places.add(profile.trajectory.where)
  for (const project of profile.projects) {
    for (const part of ['domain', 'did', 'whyRepresentative']) places.add(`${project.id}.${part}`)
  }
  return places
}

/** «Dove lavora»: the fields of work, each a bar by its sessions with its label, its counts and
 *  the note the person's model wrote under it. */
function Domains({ domains, anchors }: { domains: Profile['domains']; anchors: Anchors }) {
  const most = Math.max(1, ...domains.map((domain) => domain.sessions ?? 0))
  return (
    <Block title="Dove lavora">
      <AnchorNotes where="domains" anchors={anchors} />
      <ul className="space-y-4">
        {domains.map((domain) => (
          <li key={domain.where} className="space-y-1">
            <p className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 font-medium break-words">{domain.label}</span>
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {[
                  domain.products !== null ? (domain.products === 1 ? '1 prodotto' : `${domain.products} prodotti`) : null,
                  domain.sessions !== null ? `${domain.sessions} sessioni` : null,
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
            <Prose text={domain.note} where={domain.where} anchors={anchors} className="text-muted-foreground" />
          </li>
        ))}
      </ul>
    </Block>
  )
}

/** A lens's narrative, four lines with «Leggi tutto» to open the rest. */
function Clamped({ text, where, anchors }: { text: string | null; where: string; anchors: Anchors }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  return (
    <div className="space-y-1">
      <p className={cn('break-words whitespace-pre-line', !open && 'line-clamp-4')}>{text}</p>
      {text.length > 280 && (
        <Button type="button" variant="link" size="sm" className="h-auto p-0" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Mostra meno' : 'Leggi tutto'}
        </Button>
      )}
      <AnchorNotes where={where} anchors={anchors} />
    </div>
  )
}

/** The two shares of how a person works with the model, as one bar split in two. */
function DirectingSplit({ counts }: { counts: Profile['lenses'][number]['counts'] }) {
  const share = (label: string) => {
    const value = Number.parseFloat(counts.find((count) => count.label === label)?.value ?? '')
    return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : null
  }
  const directing = share('Dirige')
  const together = share('Ragiona insieme')
  if (directing === null || together === null) return null
  return (
    <div aria-hidden="true" className="flex h-2 overflow-hidden bg-secondary">
      <div className="h-full bg-foreground" style={{ width: `${directing}%` }} />
      <div className="h-full bg-accent" style={{ width: `${together}%` }} />
    </div>
  )
}

function Lens({ lens, anchors }: { lens: Profile['lenses'][number]; anchors: Anchors }) {
  return (
    <Block title={lens.label} className="text-sm">
      {lens.tags.length > 0 && (
        <p className="flex flex-wrap gap-1.5">
          {lens.tags.map((tag) => (
            <Badge key={tag} variant="outline" className="h-auto max-w-full py-0.5 whitespace-normal">
              {tag}
            </Badge>
          ))}
        </p>
      )}
      <Clamped text={lens.narrative} where={lens.where} anchors={anchors} />
      {lens.key === 'aiRelationship' && <DirectingSplit counts={lens.counts} />}
      {lens.counts.length > 0 && (
        <dl className="space-y-0.5 text-muted-foreground">
          {lens.counts.map((count) => (
            <div key={count.label} className="flex justify-between gap-4">
              <dt className="min-w-0 break-words">{count.label}</dt>
              <dd className="shrink-0 tabular-nums">{count.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </Block>
  )
}

function Trajectory({ trajectory, anchors }: { trajectory: NonNullable<Profile['trajectory']>; anchors: Anchors }) {
  return (
    <Block title="Come è cambiato" className="space-y-2 text-sm">
      <Prose text={trajectory.narrative} where={trajectory.where} anchors={anchors} />
      {trajectory.shifts.length > 0 && (
        <dl className="space-y-0.5 text-muted-foreground">
          {trajectory.shifts.map((shift) => (
            <div key={shift.metric} className="flex justify-between gap-4">
              <dt>{shift.metric}</dt>
              <dd className="shrink-0 tabular-nums">
                {shift.early} → {shift.late}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {trajectory.topics.map((topic) => (
        <p key={topic.quarter} className="text-muted-foreground">
          {topic.quarter}: {topic.themes.map((theme) => `${theme.label} ${theme.value}`).join(' · ')}
        </p>
      ))}
      {trajectory.principles.length > 0 && (
        <>
          <ul className="space-y-0.5">
            {trajectory.principles.map((principle, index) => (
              <li key={`${index}-${principle.text}`}>
                {principle.when && <span className="text-muted-foreground tabular-nums">{principle.when} · </span>}
                {principle.text}
              </li>
            ))}
          </ul>
          <AnchorNotes where="trajectory.principles" anchors={anchors} />
        </>
      )}
      {trajectory.new_vocabulary.length > 0 && (
        <p className="text-muted-foreground">Parole nuove: {trajectory.new_vocabulary.join(', ')}</p>
      )}
    </Block>
  )
}

/** One representative project: its domain as the title, when and how much, the kinds of work
 *  as tags, what was done, why it stands for the person (set on a gold rule) and the stack. */
function Project({ project, anchors }: { project: Profile['projects'][number]; anchors: Anchors }) {
  const span = [project.span_from, project.span_to].filter(Boolean)
  const meta = [
    span.length ? [...new Set(span)].join(' – ') : null,
    project.sessions !== null ? `${project.sessions} ${project.sessions === 1 ? 'sessione' : 'sessioni'}` : null,
    project.commits !== null ? `${project.commits} commit` : null,
    project.reverts !== null ? `${project.reverts} revert` : null,
  ].filter(Boolean)
  return (
    <li className="min-w-0 space-y-2 border bg-card p-4 text-sm">
      <div>
        <Prose text={project.domain ?? project.id} where={`${project.id}.domain`} anchors={anchors} className="font-medium" />
        {meta.length > 0 && <p className="text-xs text-muted-foreground tabular-nums">{meta.join(' · ')}</p>}
      </div>
      {project.types.length > 0 && (
        <p className="flex flex-wrap gap-1.5">
          {project.types.map((type) => (
            <Badge key={type} variant="outline">
              {type}
            </Badge>
          ))}
        </p>
      )}
      <Prose text={project.did} where={`${project.id}.did`} anchors={anchors} />
      {project.why_representative && (
        <div className="border-l-(length:--line-strong) border-accent pl-3 text-muted-foreground">
          <Prose text={project.why_representative} where={`${project.id}.whyRepresentative`} anchors={anchors} />
        </div>
      )}
      {project.tech.length > 0 && <p className="text-xs text-muted-foreground">{project.tech.join(', ')}</p>}
      {project.artifact && (
        <p>
          <WebLink href={project.artifact.url}>{project.artifact.label ?? project.artifact.url}</WebLink>
        </p>
      )}
    </li>
  )
}

/** «Come lo legge un'azienda»: the cloud's own card, drawn from the three fields the cloud read
 *  would return for this profile, with the anonymity guards' result under it. */
function CloudPreview({ freelancer, profile }: { freelancer: Freelancer; profile: WorkProfileAdmin }) {
  const card = useFreelancerCard(freelancer.id)
  const talent = card.data ? cloudTalent(freelancer, card.data, profile) : null
  return (
    <Block title="Come lo legge un'azienda" className="text-sm">
      {card.isPending ? (
        <LoadingState label="Caricamento…" className="justify-start p-0" />
      ) : talent ? (
        <div className="max-w-sm">
          <TalentCloudCard talent={talent} preview />
        </div>
      ) : (
        <p className="text-muted-foreground">
          Serve la scheda anonima per vedere la carta: senza, le aziende non vedrebbero nessuno.
        </p>
      )}
      <AnonymityResult profile={profile} />
      <p className="text-xs text-muted-foreground">
        La carta è quella del talent cloud, con gli stessi tre campi del profilo. La vede una azienda solo a profilo
        approvato.
      </p>
    </Block>
  )
}

/** The notes about what the page could not show or the hub could not read, each a muted line. */
function Notices({ profile, orphans }: { profile: WorkProfileAdmin; orphans: Anchor[] }) {
  const unknown = (count: number, one: string, many: string) => (count === 1 ? one : `${count} ${many}`)
  return (
    <>
      {orphans.length > 0 && (
        <ul aria-label="Da verificare altrove" className="space-y-1">
          {orphans.map((anchor, index) => (
            <li
              key={`${index}-${anchor.where}`}
              className="flex items-start gap-1.5 border-l-(length:--line-strong) border-accent bg-accent/15 px-2 py-1 text-xs"
            >
              <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              <span className="min-w-0 break-words">
                {anchor.where}: non trovato nei dati «{anchor.anchor}» ({ANCHOR_KINDS[anchor.kind] ?? anchor.kind})
              </span>
            </li>
          ))}
        </ul>
      )}
      {profile.unknown_technologies > 0 && (
        <p className="text-xs text-muted-foreground">
          {unknown(
            profile.unknown_technologies,
            '1 tecnologia non riconosciuta da questa versione del hub è stata tolta dal profilo.',
            'tecnologie non riconosciute da questa versione del hub sono state tolte dal profilo.',
          )}
        </p>
      )}
      {profile.unknown_practices > 0 && (
        <p className="text-xs text-muted-foreground">
          {unknown(
            profile.unknown_practices,
            '1 pratica non riconosciuta da questa versione del hub è stata tolta dal profilo.',
            'pratiche non riconosciute da questa versione del hub sono state tolte dal profilo.',
          )}
        </p>
      )}
    </>
  )
}

/** The tab of a talent who sent no work profile: one sentence and how they send one. */
function NoProfile() {
  return (
    <div className="space-y-2 border border-dashed p-6 text-sm">
      <p className="font-medium">Nessun profilo di lavoro</p>
      <p className="text-muted-foreground">
        La persona non ne ha inviato uno. Lo invia dal suo computer con{' '}
        <code className="border bg-muted px-1.5 py-0.5 font-mono whitespace-nowrap">{COMMAND}</code>: arriva qui da
        rivedere e conta solo dopo la tua approvazione.
      </p>
    </div>
  )
}

/**
 * The «Profilo di lavoro» tab (REB-1126, REB-1158): what the freelancer sent with the
 * `rebase-verify` command, for an admin to read. The numbers first, then the summary with the
 * claims the hub could not find in the data under it, how a company reads it, where the person
 * works and with what, the lenses, the practices, the trajectory and the projects. The decision
 * is in the page's rail, not here. A profile past its 12 months reads as expired.
 */
export function WorkProfileTab({
  freelancer,
  profile,
  failed,
}: {
  freelancer: Freelancer
  profile: WorkProfileAdmin | null
  failed: boolean
}) {
  const practicesId = useId()
  if (failed) {
    return <p className="text-sm text-muted-foreground">Non riesco a leggere il profilo di lavoro.</p>
  }
  if (profile === null) return <NoProfile />
  const view = profile.profile
  const anchors: Anchors = new Map()
  for (const anchor of profile.unsupported_anchors) {
    anchors.set(anchor.where, [...(anchors.get(anchor.where) ?? []), anchor])
  }
  const places = renderedPlaces(view)
  const orphans = profile.unsupported_anchors.filter((anchor) => !places.has(anchor.where))
  return (
    <div className="@container space-y-4 text-sm">
      <Notices profile={profile} orphans={orphans} />
      <Figures profile={profile} />
      <div className={cn('grid items-start gap-4', TWO_UP)}>
        <Block title="Riassunto">
          {view.summary ? (
            <Prose text={view.summary} where="summary" anchors={anchors} />
          ) : (
            <p className="text-muted-foreground">Il profilo non ha un riassunto.</p>
          )}
        </Block>
        <CloudPreview freelancer={freelancer} profile={profile} />
      </div>
      {view.domains.length > 0 && <Domains domains={view.domains} anchors={anchors} />}
      {view.technologies.length > 0 && (
        <Block title="Tecnologie">
          <p className="flex items-start gap-1.5 text-muted-foreground">
            <Lock className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            Le vedono gli admin e la persona. Le aziende non le vedono, nemmeno a profilo approvato.
          </p>
          <UsageBadges technologies={view.technologies} />
        </Block>
      )}
      {view.lenses.length > 0 && (
        <div className={cn('grid items-start gap-4', TWO_UP)}>
          {view.lenses.map((lens) => (
            <Lens key={lens.key} lens={lens} anchors={anchors} />
          ))}
        </div>
      )}
      {view.practices.length > 0 && (
        <Block title="Come lavora" id={practicesId}>
          <p className="text-muted-foreground">
            Le vedono gli admin e la persona. Le aziende non le vedono, nemmeno a profilo approvato.
          </p>
          <PracticeTable labelledBy={practicesId} practices={view.practices} />
        </Block>
      )}
      {view.trajectory && <Trajectory trajectory={view.trajectory} anchors={anchors} />}
      {view.stack.length > 0 && (
        <Block title="Strumenti adottati">
          <p className="text-muted-foreground">{view.stack.join(', ')}</p>
        </Block>
      )}
      {view.projects.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">Progetti rappresentativi</h2>
          <ul className={cn('grid gap-4', TWO_UP)}>
            {view.projects.map((project) => (
              <Project key={project.id} project={project} anchors={anchors} />
            ))}
          </ul>
        </section>
      )}
      {view.other_projects > 0 && (
        <p className="text-muted-foreground">
          {view.other_projects === 1 ? 'Altro progetto: 1, senza testo.' : `Altri ${view.other_projects} progetti, senza testo.`}
        </p>
      )}
    </div>
  )
}
