import { createFileRoute, useParams } from '@tanstack/react-router'
import { FileText } from 'lucide-react'
import { EntityDetailLayout } from '@/components/EntityDetailLayout'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { OfferStatePicker } from '@/features/documents/OfferStatePicker'
import { VersionHistory } from '@/features/documents/VersionHistory'
import { DOCUMENT_TYPE_LABELS, downloadDocument, useDocument } from '@/features/documents/queries'
import { toProblem } from '@/lib/api'

function DocumentDetail() {
  const { documentId } = useParams({ from: '/app/documents/$documentId' })
  const document = useDocument(documentId)

  if (document.isError) {
    // A real 404 keeps its own honest wording, the same way CustomerDetail's own
    // isError branch splits a genuine 404 from every other failure -- collapsing
    // both into the generic banner would tell the user a deleted/unknown document
    // "could not be reached" when the truth is simpler.
    if (toProblem(document.error).status === 404) {
      return <p className="p-8">Documento non trovato.</p>
    }
    return (
      <div className="p-8">
        <QueryErrorBanner error={document.error} />
      </div>
    )
  }
  if (!document.data) return <div className="p-8 text-muted-foreground">Caricamento…</div>

  const record = document.data

  return (
    <EntityDetailLayout
      icon={FileText}
      title={record.titolo}
      subtitle={DOCUMENT_TYPE_LABELS[record.tipo] ?? record.tipo}
      entityType="document"
      entityId={record.id}
      actions={
        <Button
          disabled={record.versione_corrente === 0}
          onClick={() => void downloadDocument(record.id)}
        >
          Scarica
        </Button>
      }
      overview={
        <div className="space-y-6">
          <OfferStatePicker document={record} />
          <VersionHistory documentId={record.id} />
        </div>
      }
    />
  )
}

export const Route = createFileRoute('/app/documents/$documentId')({
  component: DocumentDetail,
})
