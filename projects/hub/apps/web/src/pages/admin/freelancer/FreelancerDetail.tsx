import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft, Download } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { ContactPointsList } from '@/components/ContactPointsList'
import { WebLink } from '@/components/WebLink'
import { useFreelancerCard } from '@/hooks/use-freelancer-card'
import { admin, type Freelancer, type FreelancerOverride, type Note } from '@/lib/api'
import { failureMessage } from '@/lib/adminList'
import { extraPoints } from '@/lib/contactPoints'
import {
  FREELANCER_STATES,
  REMOTE_LABELS,
  VETTED_LABEL,
  formatBytes,
  formatDate,
  formatDateTime,
} from '@/lib/format'
import { formatMoney } from '@rebase/ui/format'
import { keys } from '@/lib/query-keys'
import { AuditTrail } from '../AuditTrail'
import { DuplicateOf } from '../DuplicateMarks'
import { Empty, Header, IncompletePill, LoadingState, Row, StatePill, StatusEditor, VettedPill } from '../list-kit'
import { Notes } from '../Notes'
import { FreelancerOverrideDialog, RecordLifecycle } from '../Override'
import { AnonymousCard } from '../AnonymousCard'
import {
  CvCityRow,
  CvCompaniesRow,
  CvDifferences,
  CvEmailRow,
  CvLinksRow,
  CvMark,
  CvPhoneRows,
} from './CvRows'
import { FreelancerSignup, RecentEvents } from './Subscription'

/** What REB-284 adds to the freelancer detail beyond `Freelancer`: everywhere else in
 *  the hub that already knows this address, fetched server-side in the same call so
 *  the page does not fan out four requests of its own. */

/** Who the answers on a card come from, as the detail's «Scheda» row says it. */
function ownership(f: Freelancer): string {
  if (f.filled_by === 'person') return 'compilata dalla persona'
  return f.complete ? 'scritta dall’admin' : 'scritta dall’admin, da completare'
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
  if (row.isError) return <Empty>Scheda non trovata.</Empty>
  if (row.isPending) return <LoadingState />
  const f = row.data
  const person = cvCard?.person ?? null
  const overrideFailure = failureMessage(override.error, 'Non riesco a salvare la scheda.')
  const clearCvFailure = failureMessage(clearCv.error, 'Non riesco a rimuovere il CV.')
  const lifecycleFailure = failureMessage(del.error ?? restore.error, 'Non riesco a completare l’operazione.')
  return (
    <>
      <Header title={`${f.first_name} ${f.last_name}`}>
        <div className="flex flex-wrap items-center gap-2">
          <StatePill status={f.status} />
          {f.vetted_at && <VettedPill />}
          {f.deleted_at === null && <DuplicateOf freelancerId={f.id} />}
          {!f.complete && <IncompletePill />}
          {f.cv_filename !== null && f.cv_size !== null && f.deleted_at === null && (
            <>
              <Button asChild variant="outline" size="sm">
                <a href={admin.cvUrl(f.id)}>
                  <Download className="mr-2 size-4" />
                  CV · {formatBytes(f.cv_size)}
                </a>
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => clearCv.mutate()}
                disabled={clearCv.isPending}
              >
                {clearCv.isPending ? 'Rimuovo…' : 'Rimuovi CV'}
              </Button>
            </>
          )}
          {f.deleted_at === null && (
            <Button type="button" variant="outline" size="sm" onClick={() => setOverrideOpen(true)}>
              Modifica scheda
            </Button>
          )}
          <RecordLifecycle
            deletedAt={f.deleted_at}
            deleting={del.isPending}
            restoring={restore.isPending}
            onDelete={() => del.mutate()}
            onRestore={() => restore.mutate()}
            error={lifecycleFailure}
          />
        </div>
      </Header>
      {clearCvFailure && (
        <p role="alert" className="px-6 text-sm text-destructive">
          {clearCvFailure}
        </p>
      )}
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-2">
          {person && <CvDifferences differences={cvCard?.differences ?? []} />}
          <dl className="space-y-3 text-sm">
            <Row label="Email"><a className="underline underline-offset-2" href={`mailto:${f.email}`}>{f.email}</a></Row>
            <CvPhoneRows phone={f.phone ?? null} person={person} />
            {extraPoints(f.contact_points).length > 0 && (
              <Row label="Altri recapiti">
                <ContactPointsList points={extraPoints(f.contact_points)} />
              </Row>
            )}
            <CvEmailRow email={f.email} person={person} />
            <CvCityRow person={person} />
            <Row label="Posizione">{f.job_title ?? '—'}</Row>
            <Row label="Tariffa a giornata">
              {f.daily_rate === null ? '—' : formatMoney(f.daily_rate)}
            </Row>
            <Row label="Modalità">{f.remote ? REMOTE_LABELS[f.remote] : '—'}</Row>
            <Row label="LinkedIn">
              {f.linkedin_url ? (
                <WebLink href={f.linkedin_url} />
              ) : person?.linkedin_url ? (
                <>
                  <WebLink href={person.linkedin_url} />
                  <CvMark />
                </>
              ) : (
                '—'
              )}
            </Row>
            <CvLinksRow links={f.links} person={person} />
            <CvCompaniesRow person={person} />
            <Row label="Arrivato">{formatDate(f.created_at)}{f.utm_source ? ` · da ${f.utm_source}` : ''}{f.origin ? ` · pagina ${f.origin}` : ''}</Row>
            <Row label="Provenienza">{f.provenance}</Row>
            <Row label="Accessi">
              {f.logins === 0 || f.last_access === null
                ? 'Mai entrato'
                : `${f.logins} · ultimo ${formatDateTime(f.last_access)}`}
            </Row>
            <Row label="Scheda">{ownership(f)}</Row>
            <Row label="Verifica">
              {f.vetted_at ? `${VETTED_LABEL} il ${formatDate(f.vetted_at)}` : 'Non verificato'}
            </Row>
          </dl>
          {!person && f.cv_filename !== null && f.deleted_at === null && cvCard && (
            <p className="text-sm text-muted-foreground">
              Dati dal CV non ancora letti: «Rigenera scheda» li legge.
            </p>
          )}
        </div>
        <div className="min-w-0 space-y-6">
          {f.deleted_at === null && (
            <StatusEditor
              states={FREELANCER_STATES}
              status={f.status}
              saving={move.isPending}
              onSave={(status) => move.mutate(status)}
              error={failureMessage(move.error, 'Non riesco a salvare lo stato.')}
            />
          )}
          <Notes kind="freelancers" id={f.id} notes={f.note} onChange={onNotesChange} />
        </div>
      </div>
      {f.deleted_at === null && (
        <AnonymousCard freelancerId={f.id} hasCv={f.cv_filename !== null} />
      )}
      <FreelancerSignup utm={f.signup_utm} />
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
      {f.pigro_slug && (
        <section className="space-y-2 px-6 pb-6">
          <h2 className="text-sm font-medium">Spazio PigroCRM</h2>
          <p className="text-sm"><code className="border bg-muted px-1.5 py-0.5">{f.pigro_slug}</code></p>
        </section>
      )}
      <AuditTrail
        kind="freelancers"
        id={f.id}
        canRevert={f.deleted_at === null}
        onReverted={() => {
          void client.invalidateQueries({ queryKey: keys.freelancer(id) })
          // A reverted override can put back the work mode or the rate, which the
          // «Scheda anonima» section reads beside the card, as `mergeAndRefresh` says.
          void client.invalidateQueries({ queryKey: keys.freelancerCard(id) })
          void client.invalidateQueries({ queryKey: keys.talents() })
        }}
      />
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
