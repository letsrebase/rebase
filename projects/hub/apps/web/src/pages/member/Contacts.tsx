import { Pencil } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { ContactPointsEditor } from '@/components/ContactPointsEditor'
import { ContactPointsList } from '@/components/ContactPointsList'
import { ApiError } from '@/lib/api'
import { allValid, draftsFrom, loginPoint, toWrite, type PointDraft } from '@/lib/contactPoints'
import { useMe, useUpdateContactPoints } from '@/lib/me'

/**
 * «I tuoi recapiti» (REB-944): the person's own list of ways to be reached, for a freelancer,
 * a company's referente or both (it is one list on the person, whatever they are here). The
 * address they sign in with is first, read only; the rest they add, label and annotate here,
 * and the list is saved whole. An address another person already holds is refused by the API
 * with a sentence that names it, shown as it comes.
 */
export function MemberContacts() {
  const me = useMe()
  const update = useUpdateContactPoints()
  const [drafts, setDrafts] = useState<PointDraft[] | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  if (!me.data) return null
  const points = me.data.contact_points
  const login = loginPoint(points)?.value ?? me.data.email

  async function save() {
    if (drafts === null) return
    setFailure(null)
    try {
      await update.mutateAsync(toWrite(drafts))
      setDrafts(null)
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : 'Non siamo riusciti a salvare. Riprova.')
    }
  }

  return (
    <section aria-labelledby="member-contacts" className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h2 id="member-contacts" className="text-lg font-semibold tracking-tight">
          I tuoi recapiti
        </h2>
        {drafts === null && (
          <Button type="button" variant="outline" size="sm" onClick={() => setDrafts(draftsFrom(points))}>
            <Pencil className="mr-2 size-4" />
            Modifica recapiti
          </Button>
        )}
      </div>
      {drafts === null ? (
        <div className="border bg-card px-4 py-3 text-sm">
          <ContactPointsList points={points} />
        </div>
      ) : (
        <div className="space-y-3 border bg-card p-4">
          <ContactPointsEditor
            idPrefix="member-contact"
            login={login}
            drafts={drafts}
            onChange={setDrafts}
            disabled={update.isPending}
          />
          {failure && (
            <p role="alert" className="text-sm text-destructive">
              {failure}
            </p>
          )}
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              disabled={update.isPending}
              onClick={() => {
                setDrafts(null)
                setFailure(null)
              }}
            >
              Annulla
            </Button>
            <Button type="button" disabled={update.isPending || !allValid(drafts)} onClick={() => void save()}>
              {update.isPending ? 'Salvo…' : 'Salva recapiti'}
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}
