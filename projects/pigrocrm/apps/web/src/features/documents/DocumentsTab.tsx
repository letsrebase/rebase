import { useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { RowActions } from '@/components/RowActions'
import { StatusPill } from '@/components/StatusPill'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { NewFromTemplateDialog } from './NewFromTemplateDialog'
import {
  DOCUMENT_TYPE_LABELS,
  OFFER_STATE_LABELS,
  OFFER_STATE_TONE,
  downloadDocument,
  useCreateDocument,
  useDeleteDocument,
  useDocuments,
  useUploadVersion,
  type Document,
  type DocumentOwner,
  type OfferState,
} from './queries'
import { UploadDropzone } from './UploadDropzone'

/** Mirrors ALLOWED_CONTENT_TYPES in
 *  packages/core/src/pigrocrm/core/documents/schemas.py. The backend is the authority
 *  and rejects anything else with its own message; this only stops the file picker
 *  from offering a type that would be refused a moment later. */
export const ACCEPTED_UPLOAD_TYPES = [
  'application/pdf',
  'text/markdown',
  'text/plain',
  'image/png',
  'image/jpeg',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
].join(',')

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('it-IT', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
}

/**
 * One tab, two owners. Everything it shows comes from `GET /api/documents` filtered by
 * the owner it was given; nothing is recomputed client-side, and every failure is
 * rendered as the server's own message rather than as an empty list.
 */
export function DocumentsTab({ owner }: { owner: DocumentOwner }) {
  const documents = useDocuments(owner)
  const createDocument = useCreateDocument(owner)
  const deleteDocument = useDeleteDocument(owner)
  const upload = useUploadVersion()
  const [uploading, setUploading] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [templateOpen, setTemplateOpen] = useState(false)

  /**
   * An uploaded file becomes a *new* document holding its first version -- a document
   * and its bytes arrive together, so there is no moment where a row exists with
   * nothing behind it. The title is the file name; the user renames it afterwards if
   * they want to.
   *
   * `created.id` is passed straight into `upload.mutateAsync`, never through a piece
   * of component state read back a moment later: `setState` does not take effect
   * until the next render, so a `useUploadVersion(idFromState)` hook constructed
   * once at the top of this component would still be reading whatever id was
   * current at the *previous* render -- `null`/`''` the first time through, always,
   * since this handler never yields control back to React before it needs the id.
   * Reproduced live: the upload landed on `/api/documents//versions` and came back
   * a 404 before `useUploadVersion` was changed to take the id per call instead.
   */
  async function handleFiles(files: File[]) {
    setProblem(null)
    setUploading(true)
    try {
      for (const file of files) {
        const created = (await createDocument.mutateAsync({
          tipo: 'documento',
          titolo: file.name,
        })) as Document
        await upload.mutateAsync({ documentId: created.id, file })
      }
    } catch (error) {
      setProblem(toProblem(error))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-medium">Documenti</h2>
        <Button onClick={() => setTemplateOpen(true)}>Nuovo da template</Button>
      </div>

      {problem && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {problem.detail}
        </p>
      )}

      <UploadDropzone
        onFiles={(files) => void handleFiles(files)}
        busy={uploading}
        accept={ACCEPTED_UPLOAD_TYPES}
      />

      {documents.isError && <QueryErrorBanner error={documents.error} />}

      {!documents.isError && documents.data && documents.data.items.length === 0 && (
        <p className="text-muted-foreground">Nessun documento.</p>
      )}

      {!documents.isError && documents.data && documents.data.items.length > 0 && (
        <ul className="divide-y rounded-lg border">
          {documents.data.items.map((document) => (
            <li key={document.id} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <a
                  href={`/app/documents/${document.id}`}
                  className="block truncate font-medium underline-offset-2 hover:underline"
                >
                  {document.titolo}
                </a>
                <p className="text-sm text-muted-foreground">{formatDate(document.created_at)}</p>
              </div>
              <Badge variant="secondary">
                {DOCUMENT_TYPE_LABELS[document.tipo] ?? document.tipo}
              </Badge>
              {document.stato && (
                /* The document *type* stays a plain badge -- it is a category, not a
                   state -- while the offer's state reads as the same dotted pill every
                   other state in the product does (design spec §4). */
                <StatusPill tone={OFFER_STATE_TONE[document.stato as OfferState]}>
                  {OFFER_STATE_LABELS[document.stato as OfferState] ?? document.stato}
                </StatusPill>
              )}
              <span className="text-sm text-muted-foreground">v{document.versione_corrente}</span>
              {/* Behind the «⋯» like every other row action (§4). These were the two lone
                  icons at the end of the row -- exactly the pattern the revision moved
                  into the menu -- and the words they carried only as `aria-label` are
                  now the items themselves. «Scarica» is disabled rather than dropped on
                  a document with no version yet: that is a state of this row, not an
                  action it can never take. */}
              <RowActions
                label={`Azioni per ${document.titolo}`}
                items={[
                  {
                    label: 'Scarica',
                    disabled: document.versione_corrente === 0,
                    onSelect: () => {
                      setProblem(null)
                      void downloadDocument(document.id).catch((error: unknown) =>
                        setProblem(toProblem(error)),
                      )
                    },
                  },
                  {
                    // A soft delete the server can undo, but it takes the row out of the
                    // list being read, so it reads in the destructive tone.
                    label: 'Archivia',
                    destructive: true,
                    onSelect: () => {
                      setProblem(null)
                      deleteDocument.mutate(document.id, {
                        onError: (error: unknown) => setProblem(toProblem(error)),
                      })
                    },
                  },
                ]}
              />
            </li>
          ))}
        </ul>
      )}

      <NewFromTemplateDialog open={templateOpen} onOpenChange={setTemplateOpen} owner={owner} />
    </div>
  )
}
