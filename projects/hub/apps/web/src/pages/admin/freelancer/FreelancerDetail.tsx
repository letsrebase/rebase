import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft, BadgeCheck, CircleAlert, CircleX, Clock, Download, Ellipsis } from 'lucide-react'
import { useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@rebase/ui/dropdown-menu'
import { formatMoney } from '@rebase/ui/format'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { useFreelancerCard } from '@/hooks/use-freelancer-card'
import { admin, type Freelancer, type FreelancerOverride, type Note } from '@/lib/api'
import { failureMessage } from '@/lib/adminList'
import { FREELANCER_STATES, REMOTE_LABELS, SENIORITY_LABELS, formatBytes, formatDate, formatExperience } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { AnonymousCard } from '../AnonymousCard'
import { AuditTrail } from '../AuditTrail'
import { DuplicateOf } from '../DuplicateMarks'
import { Empty, IncompletePill, LoadingState, StatePill, StatusEditor, VettedPill } from '../list-kit'
import { Notes } from '../Notes'
import { FreelancerOverrideDialog } from '../Override'
import { ContactsTab } from './ContactsTab'
import { ProfileDecisionBar, ProfileRailCard } from './ProfileDecision'
import { profileStandingWord, useWorkProfile } from './profileState'
import { RecentEvents } from './Subscription'
import { WorkProfileTab } from './WorkProfile'

/** The page keeps its right rail from this width up; below it the rail is gone, the decision is
 *  a bar at the foot of the page and «Stato» and «Note» sit in «Storico». */
const RAIL = '(min-width: 1024px)'

/** Below this the header keeps the CV and one overflow menu; «Crea match» and «Modifica scheda» move into it. */
const WIDE = '(min-width: 640px)'

type TabName = 'profile' | 'card' | 'contacts' | 'history'

/** «Profilo da rivedere», «Profilo approvato»…: the profile's standing in the header, so the
 *  decision that waits is seen before any tab is opened. */
function ProfilePill({ profile }: { profile: Parameters<typeof profileStandingWord>[0] }) {
  const Icon = profile.expired ? Clock : { draft: CircleAlert, approved: BadgeCheck, rejected: CircleX }[profile.state]
  return (
    <Badge variant="pill" className="gap-1">
      <Icon aria-hidden="true" />
      Profilo {profileStandingWord(profile).toLowerCase()}
    </Badge>
  )
}

/** The person's one line of facts: role, level, rate, how and where they work, how to reach them. */
function Facts({ f, role, level, city }: { f: Freelancer; role: string | null; level: string | null; city: string | null }) {
  const facts = [
    role ? <span key="role">{role}</span> : null,
    level ? <span key="level">{level}</span> : null,
    f.daily_rate !== null ? <span key="rate" className="tabular-nums">{`${formatMoney(f.daily_rate)} al giorno`}</span> : null,
    f.remote ? <span key="mode">{REMOTE_LABELS[f.remote]}</span> : null,
    city ? <span key="city">{city}</span> : null,
    <a key="email" className="underline underline-offset-2" href={`mailto:${f.email}`}>
      {f.email}
    </a>,
    f.phone ? <span key="phone" className="tabular-nums">{f.phone}</span> : null,
  ].filter((fact) => fact !== null)
  return (
    <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
      {facts}
    </p>
  )
}

export function AdminFreelancerDetail() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id' })
  const client = useQueryClient()
  const auditKey = keys.audit('freelancers', id)
  const row = useQuery({
    queryKey: keys.freelancer(id),
    queryFn: () => admin.freelancer(id),
  })
  const move = useMutation({
    mutationFn: (status: string) => admin.moveFreelancer(id, status),
    // The PATCH answers the plain card, not the REB-284 sections, and its own `note` is
    // always `[]` (REB-356, same cause as REB-355's own mutations below): merged onto the
    // cached detail with `note` kept from the current cache, or a save would drop both
    // the sections and the thread from view. The change is a fresh trail entry.
    onSuccess: (updated: Freelancer) => {
      client.setQueryData<Freelancer>(keys.freelancer(id), (current) =>
        current && { ...current, ...updated, note: current.note },
      )
      void client.invalidateQueries({ queryKey: auditKey })
      // The list shows the state: it must not be served from cache for 30 s.
      void client.invalidateQueries({ queryKey: keys.talents() })
    },
  })
  // Every mutation REB-355 adds shares `move`'s own merge-not-replace contract, plus a
  // fresh read of the trail: an override, a clear, a delete or a restore all write a
  // fresh `AdminAction` the admin expects to see without reloading the page. `note`
  // is kept from the current cache rather than `updated`'s own (always `[]`, since none
  // of these routes' response models fill it -- only `get` does): replacing it here
  // would wipe the thread from view until the next full fetch.
  function mergeAndRefresh(updated: Freelancer) {
    client.setQueryData<Freelancer>(keys.freelancer(id), (current) =>
      current && { ...current, ...updated, note: current.note },
    )
    void client.invalidateQueries({ queryKey: auditKey })
    // «Scheda anonima» (REB-514): clearing the CV deletes the card, and an override
    // may change the work mode or the rate, whose band the section reads beside it.
    void client.invalidateQueries({ queryKey: keys.freelancerCard(id) })
    void client.invalidateQueries({ queryKey: keys.talents() })
  }
  const [overrideOpen, setOverrideOpen] = useState(false)
  const override = useMutation({
    mutationFn: (data: FreelancerOverride) => admin.overrideFreelancer(id, data),
    onSuccess: (updated) => {
      mergeAndRefresh(updated)
      setOverrideOpen(false)
    },
  })
  const clearCv = useMutation({ mutationFn: () => admin.clearFreelancerCv(id), onSuccess: mergeAndRefresh })
  const del = useMutation({ mutationFn: () => admin.deleteFreelancer(id), onSuccess: mergeAndRefresh })
  const restore = useMutation({ mutationFn: () => admin.restoreFreelancer(id), onSuccess: mergeAndRefresh })
  // The thread lives on the detail row, so a change to a note goes into the same cache
  // entry and nothing is fetched twice; it also wrote a trail entry, read again.
  const onNotesChange = (update: (previous: Note[]) => Note[]) => {
    client.setQueryData<Freelancer>(keys.freelancer(id), (current) => current && { ...current, note: update(current.note) })
    void client.invalidateQueries({ queryKey: auditKey })
  }
  // What the CV says (REB-855), read by the same call «Scheda anonima» reads and in
  // parallel with the profile, not after it. A deleted card's route answers a 404, which
  // leaves the CV rows out.
  const cvCard = useFreelancerCard(id).data
  const workProfile = useWorkProfile(id)
  const withRail = useMediaQuery(RAIL)
  const [chosen, setChosen] = useState<TabName | null>(null)
  const wide = useMediaQuery(WIDE)
  if (row.isError) return <Empty>Scheda non trovata.</Empty>
  if (row.isPending) return <LoadingState />
  const f = row.data
  const live = f.deleted_at === null
  const profile = live ? workProfile.profile : null
  // The profile first when there is one, the anonymous card when there is not: the tab the
  // admin most likely came for. A deleted card has neither.
  const tab: TabName = chosen ?? (!live ? 'contacts' : profile ? 'profile' : 'card')
  const person = cvCard?.person ?? null
  const card = cvCard?.card ?? null
  const level = card
    ? `${SENIORITY_LABELS[card.seniority] ?? card.seniority} · ${formatExperience(card.experience_years)}`
    : null
  const flags = cvCard?.flags.length ?? 0
  const overrideFailure = failureMessage(override.error, 'Non riesco a salvare la scheda.')
  const clearCvFailure = failureMessage(clearCv.error, 'Non riesco a rimuovere il CV.')
  const lifecycleFailure = failureMessage(del.error ?? restore.error, 'Non riesco a completare l’operazione.')
  const hasCv = f.cv_filename !== null && f.cv_size !== null && live
  const status = live && (
    <StatusEditor
      states={FREELANCER_STATES}
      status={f.status}
      saving={move.isPending}
      onSave={(next) => move.mutate(next)}
      error={failureMessage(move.error, 'Non riesco a salvare lo stato.')}
    />
  )
  const notes = <Notes kind="freelancers" id={f.id} notes={f.note} onChange={onNotesChange} />
  return (
    <>
      <header className="space-y-3 border-b px-6 py-5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">{`${f.first_name} ${f.last_name}`}</h1>
          <div className="flex flex-wrap items-center gap-2">
            <StatePill status={f.status} />
            {f.vetted_at && <VettedPill />}
            {live && <DuplicateOf freelancerId={f.id} />}
            {!f.complete && <IncompletePill />}
            {profile && <ProfilePill profile={profile} />}
            {!live && <Badge variant="pill">Eliminata il {formatDate(f.deleted_at!)}</Badge>}
          </div>
        </div>
        <Facts f={f} role={card?.role ?? f.job_title} level={level} city={person?.city ?? null} />
        <div className="flex flex-wrap items-center gap-2">
          {hasCv && (
            <Button asChild variant="outline" size="sm">
              <a href={admin.cvUrl(f.id)}>
                <Download className="mr-2 size-4" />
                CV · {formatBytes(f.cv_size!)}
              </a>
            </Button>
          )}
          {live && wide && (
            <Button asChild variant="outline" size="sm">
              <Link to="/admin/freelance/$id/match/new" params={{ id: f.id }}>
                Crea match
              </Link>
            </Button>
          )}
          {live && wide && (
            <Button type="button" variant="outline" size="sm" onClick={() => setOverrideOpen(true)}>
              Modifica scheda
            </Button>
          )}
          {!live && (
            <Button type="button" variant="outline" size="sm" onClick={() => restore.mutate()} disabled={restore.isPending}>
              {restore.isPending ? 'Ripristino…' : 'Ripristina'}
            </Button>
          )}
          {live && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="outline" size="icon-sm" aria-label="Altre azioni">
                  <Ellipsis />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                {!wide && (
                  <>
                    <DropdownMenuItem asChild>
                      <Link to="/admin/freelance/$id/match/new" params={{ id: f.id }}>
                        Crea match
                      </Link>
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => setOverrideOpen(true)}>Modifica scheda</DropdownMenuItem>
                  </>
                )}
                {hasCv && (
                  <DropdownMenuItem disabled={clearCv.isPending} onSelect={() => clearCv.mutate()}>
                    {clearCv.isPending ? 'Rimuovo…' : 'Rimuovi CV'}
                  </DropdownMenuItem>
                )}
                <DropdownMenuItem variant="destructive" disabled={del.isPending} onSelect={() => del.mutate()}>
                  {del.isPending ? 'Elimino…' : 'Elimina'}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
        {(clearCvFailure || lifecycleFailure) && (
          <p role="alert" className="text-sm text-destructive">
            {clearCvFailure ?? lifecycleFailure}
          </p>
        )}
      </header>
      {workProfile.pending && live ? (
        <LoadingState />
      ) : (
        <div className={withRail ? 'grid grid-cols-[minmax(0,1fr)_17rem] items-start gap-6 p-6' : 'p-6'}>
          <Tabs value={tab} onValueChange={(next) => setChosen(next as TabName)} className="min-w-0">
            <TabsList className="max-w-full justify-start overflow-x-auto">
              {live && (
                <TabsTrigger value="profile" className="whitespace-nowrap">
                  Profilo di lavoro
                  {profile?.state === 'draft' && !profile.expired && (
                    <Badge variant="pill" className="ml-2 gap-1">
                      <CircleAlert aria-hidden="true" />
                      Da rivedere
                    </Badge>
                  )}
                </TabsTrigger>
              )}
              {live && (
                <TabsTrigger value="card" className="whitespace-nowrap">
                  Scheda anonima
                  {flags > 0 && (
                    <Badge variant="pill" className="ml-2">
                      <span className="sr-only">Da verificare: </span>
                      {flags}
                    </Badge>
                  )}
                </TabsTrigger>
              )}
              <TabsTrigger value="contacts" className="whitespace-nowrap">
                Contatti e dati
              </TabsTrigger>
              <TabsTrigger value="history" className="whitespace-nowrap">
                Storico
              </TabsTrigger>
            </TabsList>
            {live && (
              <TabsContent value="profile" className="pt-4">
                <section aria-label="Profilo di lavoro">
                  <WorkProfileTab freelancer={f} profile={profile} failed={workProfile.failed} />
                </section>
              </TabsContent>
            )}
            {live && (
              <TabsContent value="card" className="pt-4">
                <AnonymousCard freelancerId={f.id} hasCv={f.cv_filename !== null} />
              </TabsContent>
            )}
            <TabsContent value="contacts" className="pt-4">
              <ContactsTab f={f} cvCard={cvCard} />
            </TabsContent>
            <TabsContent value="history" className="space-y-6 pt-4">
              {!withRail && (
                <div className="space-y-4">
                  {status}
                  {notes}
                </div>
              )}
              <RecentEvents
                title="Ultimi accessi"
                empty="Non è mai entrata."
                items={f.last_logins.map((login) => ({ id: login.id, when: login.logged_at }))}
              />
              <RecentEvents
                title="Download della guida"
                empty="Non ha scaricato la guida."
                items={f.last_download_guide.map((download) => ({
                  id: download.id,
                  when: download.downloaded_at,
                }))}
              />
              <AuditTrail
                kind="freelancers"
                id={f.id}
                canRevert={live}
                className=""
                onReverted={() => {
                  void client.invalidateQueries({ queryKey: keys.freelancer(id) })
                  // A reverted override can put back the work mode or the rate, which the
                  // «Scheda anonima» section reads beside the card, as `mergeAndRefresh` says.
                  void client.invalidateQueries({ queryKey: keys.freelancerCard(id) })
                  void client.invalidateQueries({ queryKey: keys.talents() })
                }}
              />
            </TabsContent>
          </Tabs>
          {withRail && (
            <aside aria-label="Decisione, stato e note" className="sticky top-4 min-w-0 space-y-4">
              {profile && <ProfileRailCard profile={profile} freelancerId={f.id} vettedAt={f.vetted_at} />}
              {status}
              {notes}
            </aside>
          )}
        </div>
      )}
      {!withRail && profile && <ProfileDecisionBar profile={profile} freelancerId={f.id} vettedAt={f.vetted_at} />}
      <p className="px-6 pb-6">
        <Link to="/admin/talent" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutti i talenti
        </Link>
      </p>
      <FreelancerOverrideDialog
        freelancer={f}
        open={overrideOpen}
        onOpenChange={setOverrideOpen}
        onSave={(data) => override.mutate(data)}
        saving={override.isPending}
        error={overrideFailure}
      />
    </>
  )
}
