import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Textarea } from '@rebase/ui/textarea'
import { ApiError, admin, type Comment, type CommentKind } from '@/lib/api'
import { formatDateTime } from '@/lib/format'

/**
 * The thread under a freelancer or a company: what was said about them, newest first,
 * and a box to add one line more. Append-only by construction, like the API behind it:
 * no edit and no delete anywhere. The list belongs to the detail page's cache, so the
 * page hands it in and hears back what was added (`onAdded`) rather than the component
 * keeping a second copy that could drift from the row.
 */
export function Comments({
  kind,
  id,
  comments,
  onAdded,
}: {
  kind: CommentKind
  id: string
  comments: Comment[]
  onAdded: (created: Comment) => void
}) {
  const [draft, setDraft] = useState('')
  const add = useMutation({
    mutationFn: (testo: string) => admin.addComment(kind, id, testo),
    onSuccess: (created) => {
      setDraft('')
      onAdded(created)
    },
  })
  const text = draft.trim()
  const failure =
    add.error instanceof ApiError ? add.error.message : add.error ? 'Non riesco a salvare il commento.' : null

  return (
    <section aria-label="Commenti" className="space-y-4 px-6 pb-6">
      <h2 className="text-sm font-medium">
        Commenti
        {comments.length > 0 && (
          <span className="ml-2 font-normal text-muted-foreground">{comments.length}</span>
        )}
      </h2>
      {comments.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessun commento, per ora.</p>
      ) : (
        <ol className="divide-y rounded-2xl border text-sm">
          {comments.map((comment) => (
            <li key={comment.id} className="space-y-1 px-4 py-3">
              <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{comment.autore}</span>
                <time dateTime={comment.created_at}>{formatDateTime(comment.created_at)}</time>
              </p>
              <p className="whitespace-pre-wrap break-words">{comment.testo}</p>
            </li>
          ))}
        </ol>
      )}
      <form
        className="space-y-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (text) add.mutate(text)
        }}
      >
        <Textarea
          aria-label="Nuovo commento"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="Una telefonata fatta, un'impressione, una cosa da ricordare"
          disabled={add.isPending}
        />
        {failure && (
          <p role="alert" className="text-sm text-destructive">
            {failure}
          </p>
        )}
        <Button type="submit" size="sm" disabled={!text || add.isPending}>
          {add.isPending ? 'Aggiungo…' : 'Aggiungi commento'}
        </Button>
      </form>
    </section>
  )
}
