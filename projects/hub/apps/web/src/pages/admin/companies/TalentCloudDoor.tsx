import { Button } from '@rebase/ui/button'
import type { TalentCloudGrant } from '@/lib/api'
import { failureMessage } from '@/lib/adminList'
import { formatDate } from '@/lib/format'

/** «Talent cloud» on a company request's page (REB-518, spec § 4.1): whether the
 *  request's referente can open the private cloud, and the one button that opens it,
 *  with a mail to them, or closes it again. Only this request's grant: the same person
 *  may hold another company's, which that request's page shows. */
export function TalentCloudDoor({
  grant,
  opening,
  revoking,
  onOpen,
  onRevoke,
  error,
}: {
  grant: TalentCloudGrant | null
  opening: boolean
  revoking: boolean
  onOpen: () => void
  onRevoke: () => void
  error: Error | null
}) {
  const failure = failureMessage(error, 'Non riesco a cambiare l’accesso al talent cloud.')
  return (
    <section aria-label="Talent cloud" className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">Talent cloud</h2>
      {grant ? (
        <p className="text-sm">
          Aperto il {formatDate(grant.granted_at)}
          {grant.granted_by_name ? ` da ${grant.granted_by_name}` : ''} per {grant.contact_person} ({grant.email}).
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">
          Il referente non vede il talent cloud. Aprirlo gli manda una mail con il link.
        </p>
      )}
      {grant ? (
        <Button type="button" variant="outline" size="sm" onClick={onRevoke} disabled={revoking}>
          {revoking ? 'Revoco…' : 'Revoca il talent cloud'}
        </Button>
      ) : (
        <Button type="button" size="sm" onClick={onOpen} disabled={opening}>
          {opening ? 'Apro…' : 'Apri il talent cloud'}
        </Button>
      )}
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </section>
  )
}
