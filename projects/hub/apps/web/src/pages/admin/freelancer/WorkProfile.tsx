import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type ReactNode } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import { WebLink } from '@/components/WebLink'
import { admin, ApiError, type WorkProfileAdmin } from '@/lib/api'
import { failureMessage } from '@/lib/adminList'
import { VETTED_LABEL, formatDate } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { LoadingState } from '../list-kit'

type Anchor = WorkProfileAdmin['unsupported_anchors'][number]
type Profile = WorkProfileAdmin['profile']

/** What the recheck calls each kind of claim, in the admin's words. */
const ANCHOR_KINDS: Record<string, string> = {
  number: 'numero',
  percent: 'percentuale',
  period: 'periodo',
  tech: 'tecnologia',
  tag: 'tipo di lavoro',
}

const STATE_WORDS: Record<WorkProfileAdmin['state'], string> = {
  draft: 'Da rivedere',
  approved: 'Approvato',
  rejected: 'Rifiutato',
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  )
}

/** The claims of one piece of prose the hub could not find in the profile's own data, set
 *  right under that prose: the signature of this section (spec 2026-11-17 § 10). */
function Anchors({ where, anchors }: { where: string; anchors: Map<string, Anchor[]> }) {
  const found = anchors.get(where)
  if (!found?.length) return null
  return (
    <ul aria-label="Da verificare in questo testo" className="mt-1 space-y-0.5 text-xs text-destructive">
      {found.map((anchor, index) => (
        <li key={`${index}-${anchor.anchor}`}>
          Non trovato nei dati: «{anchor.anchor}» ({ANCHOR_KINDS[anchor.kind] ?? anchor.kind})
        </li>
      ))}
    </ul>
  )
}

/** A narrative with its anchors; nothing when the person's model wrote none. */
function Prose({ text, where, anchors }: { text: string | null; where: string; anchors: Map<string, Anchor[]> }) {
  if (!text) return null
  return (
    <>
      <p className="whitespace-pre-line">{text}</p>
      <Anchors where={where} anchors={anchors} />
    </>
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

function Lens({ lens, anchors }: { lens: Profile['lenses'][number]; anchors: Map<string, Anchor[]> }) {
  return (
    <div className="space-y-1 border-t py-3">
      <h4 className="font-medium">{lens.label}</h4>
      {lens.tags.length > 0 && (
        <p className="flex flex-wrap gap-1.5">
          {lens.tags.map((tag) => (
            <Badge key={tag} variant="outline">
              {tag}
            </Badge>
          ))}
        </p>
      )}
      <Prose text={lens.narrative} where={lens.where} anchors={anchors} />
      {lens.counts.length > 0 && (
        <dl className="space-y-0.5 text-muted-foreground">
          {lens.counts.map((count) => (
            <div key={count.label} className="flex justify-between gap-4">
              <dt>{count.label}</dt>
              <dd className="shrink-0 tabular-nums">{count.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

function Trajectory({ trajectory, anchors }: { trajectory: NonNullable<Profile['trajectory']>; anchors: Map<string, Anchor[]> }) {
  return (
    <div className="space-y-2 border-t py-3">
      <h4 className="font-medium">Come è cambiato</h4>
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
          <Anchors where="trajectory.principles" anchors={anchors} />
        </>
      )}
      {trajectory.new_vocabulary.length > 0 && (
        <p className="text-muted-foreground">Parole nuove: {trajectory.new_vocabulary.join(', ')}</p>
      )}
    </div>
  )
}

function Project({ project, anchors }: { project: Profile['projects'][number]; anchors: Map<string, Anchor[]> }) {
  const span = [project.span_from, project.span_to].filter(Boolean)
  return (
    <li className="space-y-1 border-t py-3">
      <p className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium">{project.id}</span>
        {project.types.map((type) => (
          <Badge key={type} variant="outline">
            {type}
          </Badge>
        ))}
        <span className="text-muted-foreground tabular-nums">
          {[
            span.length ? [...new Set(span)].join(' – ') : null,
            project.sessions !== null ? `${project.sessions} sessioni` : null,
            project.commits !== null ? `${project.commits} commit` : null,
            project.reverts !== null ? `${project.reverts} revert` : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </p>
      <Prose text={project.domain} where={`${project.id}.domain`} anchors={anchors} />
      <Prose text={project.did} where={`${project.id}.did`} anchors={anchors} />
      <Prose text={project.why_representative} where={`${project.id}.whyRepresentative`} anchors={anchors} />
      {project.tech.length > 0 && <p className="text-muted-foreground">{project.tech.join(', ')}</p>}
      {project.artifact && (
        <p>
          <WebLink href={project.artifact.url}>{project.artifact.label ?? project.artifact.url}</WebLink>
        </p>
      )}
    </li>
  )
}

/** The profile as upstream's `profile.md` lays it out, from the payload the API rendered. */
function ProfileBody({ profile, anchors }: { profile: Profile; anchors: Map<string, Anchor[]> }) {
  const volume = [
    profile.products !== null ? `${profile.products} prodotti` : null,
    profile.sessions !== null ? `${profile.sessions} sessioni` : null,
    profile.instructions !== null ? `${profile.instructions} istruzioni` : null,
  ].filter(Boolean)
  const window = [profile.window_from, profile.window_to].filter(Boolean).join(' – ')
  return (
    <div className="space-y-1 text-sm">
      <p className="text-muted-foreground tabular-nums">{[window, ...volume].filter(Boolean).join(' · ')}</p>
      <Prose text={profile.summary} where="summary" anchors={anchors} />
      {profile.domains.length > 0 && (
        <div className="space-y-1 border-t py-3">
          <h4 className="font-medium">Ambiti di lavoro</h4>
          <Anchors where="domains" anchors={anchors} />
          <ul className="space-y-1">
            {profile.domains.map((domain) => (
              <li key={domain.where}>
                <span className="font-medium">{domain.label}</span>
                <span className="text-muted-foreground tabular-nums">
                  {[
                    domain.products !== null ? `${domain.products} prodotti` : null,
                    domain.sessions !== null ? `${domain.sessions} sessioni` : null,
                  ]
                    .filter(Boolean)
                    .map((part) => ` · ${part}`)
                    .join('')}
                </span>
                <Prose text={domain.note} where={domain.where} anchors={anchors} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {profile.lenses.map((lens) => (
        <Lens key={lens.key} lens={lens} anchors={anchors} />
      ))}
      {profile.trajectory && <Trajectory trajectory={profile.trajectory} anchors={anchors} />}
      {profile.stack.length > 0 && (
        <div className="border-t py-3">
          <h4 className="font-medium">Strumenti adottati</h4>
          <p className="text-muted-foreground">{profile.stack.join(', ')}</p>
        </div>
      )}
      {profile.projects.length > 0 && (
        <div>
          <h4 className="border-t pt-3 font-medium">Progetti rappresentativi</h4>
          <ul>
            {profile.projects.map((project) => (
              <Project key={project.id} project={project} anchors={anchors} />
            ))}
          </ul>
        </div>
      )}
      {profile.other_projects > 0 && (
        <p className="border-t pt-3 text-muted-foreground">
          Altri progetti: {profile.other_projects}, senza testo.
        </p>
      )}
    </div>
  )
}

/** «Approva» and «Rifiuta»: one note, optional for the first (it stays with the admins),
 *  required for the second (the person reads it). Admins only, as the whole area is. */
function Decision({ freelancerId, vettedAt }: { freelancerId: string; vettedAt: string | null }) {
  const client = useQueryClient()
  const noteId = useId()
  const [note, setNote] = useState('')
  const onDone = (read: WorkProfileAdmin) => {
    client.setQueryData(keys.workProfile(freelancerId), read)
    void client.invalidateQueries({ queryKey: keys.freelancer(freelancerId) })
    void client.invalidateQueries({ queryKey: keys.audit('freelancers', freelancerId) })
    void client.invalidateQueries({ queryKey: keys.talents() })
  }
  const approve = useMutation({
    mutationFn: () => admin.approveWorkProfile(freelancerId, note.trim() || null),
    onSuccess: onDone,
  })
  const reject = useMutation({
    mutationFn: () => admin.rejectWorkProfile(freelancerId, note.trim()),
    onSuccess: onDone,
  })
  const failure = failureMessage(approve.error ?? reject.error, 'Non riesco a salvare la decisione.')
  const busy = approve.isPending || reject.isPending
  return (
    <div className="space-y-2 border-t pt-4">
      <Label htmlFor={noteId}>Nota</Label>
      <Textarea
        id={noteId}
        value={note}
        maxLength={2000}
        onChange={(event) => setNote(event.target.value)}
        aria-describedby={`${noteId}-help`}
      />
      <p id={`${noteId}-help`} className="text-xs text-muted-foreground">
        Per rifiutare serve una nota, e la persona la legge nella sua area. Per approvare è facoltativa e la leggono solo
        gli admin.
        {vettedAt === null && ` Approvare segna anche la persona come «${VETTED_LABEL}».`}
      </p>
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" disabled={busy || note.trim() === ''} onClick={() => reject.mutate()}>
          {reject.isPending ? 'Rifiuto…' : 'Rifiuta'}
        </Button>
        <Button type="button" disabled={busy} onClick={() => approve.mutate()}>
          {approve.isPending ? 'Approvo…' : 'Approva'}
        </Button>
      </div>
    </div>
  )
}

/**
 * «Profilo di lavoro» on the admin's talent page (REB-1126, spec 2026-11-17 § 4): what the
 * freelancer sent with the `work-profile` command, for an admin to read and decide on. The
 * state, the dates and the tool version; the hub's recheck, with each claim it could not find
 * in the data set right under the prose it came from; the profile rendered from the payload;
 * and «Approva» / «Rifiuta». Nothing is shown for a person who sent none. A profile past its
 * 12 months is shown as expired and cannot be decided.
 */
export function WorkProfile({ freelancerId, vettedAt }: { freelancerId: string; vettedAt: string | null }) {
  const headingId = useId()
  const read = useQuery({
    queryKey: keys.workProfile(freelancerId),
    queryFn: () => admin.workProfile(freelancerId),
    retry: false,
  })
  if (read.isPending) return <LoadingState label="Caricamento…" className="justify-start px-6 pb-6" />
  if (read.isError) {
    if (read.error instanceof ApiError && read.error.status === 404) return null
    return (
      <section aria-labelledby={headingId} className="space-y-2 px-6 pb-6">
        <h2 id={headingId} className="text-sm font-medium">
          Profilo di lavoro
        </h2>
        <p className="text-sm text-muted-foreground">Non riesco a leggere il profilo di lavoro.</p>
      </section>
    )
  }
  const profile = read.data
  const anchors = new Map<string, Anchor[]>()
  for (const anchor of profile.unsupported_anchors) {
    anchors.set(anchor.where, [...(anchors.get(anchor.where) ?? []), anchor])
  }
  const places = renderedPlaces(profile.profile)
  const orphans = profile.unsupported_anchors.filter((anchor) => !places.has(anchor.where))
  return (
    <section aria-labelledby={headingId} className="space-y-3 px-6 pb-6">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id={headingId} className="text-sm font-medium">
          Profilo di lavoro
        </h2>
        <Badge variant="pill" dot={profile.state === 'draft' ? 'accent' : undefined}>
          {profile.expired ? 'Scaduto' : STATE_WORDS[profile.state]}
        </Badge>
      </div>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
        <Field label="Inviato il">{formatDate(profile.submitted_at)}</Field>
        <Field label="Scade il">{formatDate(profile.expires_at)}</Field>
        <Field label="Versione dello strumento">{profile.tool_version ?? '—'}</Field>
        <Field label="Testo verificabile">
          <span className="tabular-nums">{profile.groundedness}%</span>{' '}
          <span className="text-muted-foreground">
            ({profile.unsupported_anchors.length === 0
              ? 'nessuna affermazione senza dati'
              : `${profile.unsupported_anchors.length} da verificare`}
            )
          </span>
        </Field>
        {profile.decided_at && (
          <Field label="Deciso">
            {formatDate(profile.decided_at)}
            {profile.decided_by_name ? ` da ${profile.decided_by_name}` : ''}
          </Field>
        )}
        {profile.decision_note && <Field label="Nota">{profile.decision_note}</Field>}
      </dl>
      {orphans.length > 0 && (
        <ul aria-label="Da verificare altrove" className="space-y-0.5 text-xs text-destructive">
          {orphans.map((anchor, index) => (
            <li key={`${index}-${anchor.where}`}>
              {anchor.where}: non trovato nei dati «{anchor.anchor}» ({ANCHOR_KINDS[anchor.kind] ?? anchor.kind})
            </li>
          ))}
        </ul>
      )}
      <ProfileBody profile={profile.profile} anchors={anchors} />
      {profile.state === 'draft' && !profile.expired && <Decision freelancerId={freelancerId} vettedAt={vettedAt} />}
    </section>
  )
}
