import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { admin, type Company, type CompanyOverride, type Note, type TalentCloudGrant } from '@/lib/api'
import { failureMessage } from '@/lib/adminList'
import { COMPANY_STATES, REMOTE_LABELS, TEAM_BUILDER_ORIGIN, formatDate } from '@/lib/format'
import { formatMoney } from '@rebase/ui/format'
import { keys } from '@/lib/query-keys'
import { AuditTrail } from '../AuditTrail'
import { CrmLink } from '../CrmLink'
import { Empty, Header, Row, StatePill, StatusEditor, TeamBuilderPill } from '../list-kit'
import { Notes } from '../Notes'
import { CompanyOverrideDialog, RecordLifecycle } from '../Override'
import { TalentCloudDoor } from './TalentCloudDoor'

export function AdminCompanyDetail() {
  const { id } = useParams({ from: '/signedIn/admin/companies/$id' })
  const client = useQueryClient()
  const auditKey = keys.audit('companies', id)
  const row = useQuery({ queryKey: keys.company(id), queryFn: () => admin.company(id) })
  const move = useMutation({
    mutationFn: (status: string) => admin.moveCompany(id, status),
    // The PATCH's own `note` is always `[]` (REB-356, same cause as REB-355's own
    // mutations below): merged onto the cached detail with `note` kept from the
    // current cache, or a save would wipe the thread from view. The same for
    // `talent_cloud_grant` (REB-518), which only `get` fills: a save must not show the
    // talent cloud closed while its grant is live.
    onSuccess: (updated: Company) => {
      client.setQueryData<Company>(keys.company(id), (current) =>
        current && {
          ...current,
          ...updated,
          note: current.note,
          talent_cloud_grant: current.talent_cloud_grant,
        },
      )
      void client.invalidateQueries({ queryKey: keys.companies() })
      void client.invalidateQueries({ queryKey: auditKey })
    },
  })
  // Every mutation REB-355 adds shares `move`'s own merge-not-replace contract, plus a
  // fresh read of the trail: an override, a delete or a restore all write a fresh
  // `AdminAction` the admin expects to see without reloading the page. `note` is
  // kept from the current cache rather than `updated`'s own (always `[]`, since none of
  // these routes' response models fill it -- only `get` does): replacing it here would
  // wipe the thread from view until the next full fetch. `talent_cloud_grant` is kept
  // the same way (REB-518): these answers carry it as `null` whatever is live.
  function mergeAndRefresh(updated: Company) {
    client.setQueryData<Company>(keys.company(id), (current) =>
      current && {
        ...current,
        ...updated,
        note: current.note,
        talent_cloud_grant: current.talent_cloud_grant,
      },
    )
    void client.invalidateQueries({ queryKey: keys.companies() })
    void client.invalidateQueries({ queryKey: auditKey })
  }
  const [overrideOpen, setOverrideOpen] = useState(false)
  // The grant's read names the referente (REB-518), whom an override may rename: the
  // request is read again, grant included, rather than showing the old name beside it.
  const override = useMutation({
    mutationFn: (data: CompanyOverride) => admin.overrideCompany(id, data),
    onSuccess: (updated) => {
      mergeAndRefresh(updated)
      void client.invalidateQueries({ queryKey: keys.company(id) })
      setOverrideOpen(false)
    },
  })
  const del = useMutation({ mutationFn: () => admin.deleteCompany(id), onSuccess: mergeAndRefresh })
  // A restored request opens its talent cloud again (`cloud.for_user` skips a deleted
  // one), so its page reads the request, grant included, once more.
  const restore = useMutation({
    mutationFn: () => admin.restoreCompany(id),
    onSuccess: (updated) => {
      mergeAndRefresh(updated)
      void client.invalidateQueries({ queryKey: keys.company(id) })
    },
  })
  // «Riprova» and «Collega a un altro deal» (REB-869): the answer is the request as it
  // stands, merged like every other write here so the notes and the talent cloud stay.
  const linkCrm = useMutation({
    mutationFn: (dealId?: string) => admin.linkCompanyToCrm(id, dealId),
    onSuccess: mergeAndRefresh,
  })
  const onNotesChange = (update: (previous: Note[]) => Note[]) => {
    client.setQueryData<Company>(keys.company(id), (current) => current && { ...current, note: update(current.note) })
    void client.invalidateQueries({ queryKey: auditKey })
  }
  // The door of the talent cloud (REB-518): the answer is the grant, live after «Apri»
  // and closed after «Revoca», kept on the cached request as its own read carries it.
  function setGrant(grant: TalentCloudGrant | null) {
    client.setQueryData<Company>(keys.company(id), (current) => current && { ...current, talent_cloud_grant: grant })
  }
  const openCloud = useMutation({ mutationFn: () => admin.openTalentCloud(id), onSuccess: setGrant })
  const revokeCloud = useMutation({ mutationFn: () => admin.revokeTalentCloud(id), onSuccess: () => setGrant(null) })
  if (row.isError) return <Empty>Richiesta non trovata.</Empty>
  if (row.isPending) return <Empty>Caricamento…</Empty>
  const c = row.data
  const overrideFailure = failureMessage(override.error, 'Non riesco a salvare la richiesta.')
  const lifecycleFailure = failureMessage(del.error ?? restore.error, 'Non riesco a completare l’operazione.')
  return (
    <>
      <Header title={c.company_name}>
        <div className="flex flex-wrap items-center gap-2">
          <StatePill status={c.status} />
          {c.origin === TEAM_BUILDER_ORIGIN && <TeamBuilderPill />}
          {c.deleted_at === null && (
            <Button type="button" variant="outline" size="sm" onClick={() => setOverrideOpen(true)}>
              Modifica richiesta
            </Button>
          )}
          <RecordLifecycle
            deletedAt={c.deleted_at}
            deleting={del.isPending}
            restoring={restore.isPending}
            onDelete={() => del.mutate()}
            onRestore={() => restore.mutate()}
            error={lifecycleFailure}
          />
        </div>
      </Header>
      <div className="grid gap-6 p-6 lg:grid-cols-3">
        <dl className="space-y-3 text-sm lg:col-span-2">
          <Row label="Referente">
            {c.contact_person} · <a className="underline underline-offset-2" href={`mailto:${c.email}`}>{c.email}</a>
            {c.phone ? ` · ${c.phone}` : ''}
          </Row>
          <Row label="Figura richiesta">{c.requested_role}</Row>
          <Row label="Progetto"><p className="whitespace-pre-wrap">{c.project}</p></Row>
          <Row label="Periodo">dal {formatDate(c.period_from)}, {c.duration}</Row>
          <Row label="Budget a giornata">{formatMoney(c.daily_budget)}</Row>
          <Row label="Modalità">
            {REMOTE_LABELS[c.remote]}
            {c.onsite_days ? ` · ${c.onsite_days} giorni a settimana in sede` : ''}
          </Row>
          <Row label="Persone richieste">{c.headcount}</Row>
          <Row label="Arrivata">{formatDate(c.created_at)}{c.utm_source ? ` · da ${c.utm_source}` : ''}{c.origin ? ` · pagina ${c.origin}` : ''}</Row>
        </dl>
        <div className="min-w-0 space-y-6">
          {c.deleted_at === null && (
            <StatusEditor
              states={COMPANY_STATES}
              status={c.status}
              saving={move.isPending}
              onSave={(status) => move.mutate(status)}
              error={failureMessage(move.error, 'Non riesco a salvare lo stato.')}
            />
          )}
          <Notes kind="companies" id={c.id} notes={c.note} onChange={onNotesChange} />
        </div>
      </div>
      {c.deleted_at === null && (
        <CrmLink
          company={c}
          pending={linkCrm.isPending}
          onLink={(dealId) => linkCrm.mutate(dealId)}
          error={linkCrm.error}
        />
      )}
      {c.deleted_at === null && (
        <TalentCloudDoor
          grant={c.talent_cloud_grant ?? null}
          opening={openCloud.isPending}
          revoking={revokeCloud.isPending}
          onOpen={() => openCloud.mutate()}
          onRevoke={() => revokeCloud.mutate()}
          // The failure of the button on screen: «Revoca» while a grant is live,
          // «Apri» otherwise, so an old refusal of the other one never lingers.
          error={c.talent_cloud_grant ? revokeCloud.error : openCloud.error}
        />
      )}
      <AuditTrail
        kind="companies"
        id={c.id}
        canRevert={c.deleted_at === null}
        onReverted={() => void client.invalidateQueries({ queryKey: keys.company(id) })}
      />
      <p className="px-6 pb-6">
        <Link to="/admin/companies" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutte le aziende
        </Link>
      </p>
      <CompanyOverrideDialog
        company={c}
        open={overrideOpen}
        onOpenChange={setOverrideOpen}
        onSave={(data) => override.mutate(data)}
        saving={override.isPending}
        error={overrideFailure}
      />
    </>
  )
}
