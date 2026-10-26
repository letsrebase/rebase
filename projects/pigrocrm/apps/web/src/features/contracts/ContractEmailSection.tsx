import { Pencil, Plus, Send } from 'lucide-react'
import { useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Skeleton } from '@rebase/ui/skeleton'
import { useCan } from '@/lib/auth'
import { EmailTemplateDialog } from './EmailTemplateDialog'
import { SendInvoiceDialog } from './SendInvoiceDialog'
import { useEmailTemplates, useUpdateEmailTemplate, type EmailTemplate } from './emailQueries'

const ATTACHMENT_LABEL = { fattura_pdf: 'fattura', registro_pdf: 'registro' } as const

function Archive({ contractId, template }: { contractId: string; template: EmailTemplate }) {
  const update = useUpdateEmailTemplate(contractId, template.id)
  return (
    <Button
      size="sm"
      variant="ghost"
      disabled={update.isPending}
      onClick={() => update.mutate({ attivo: !template.attivo })}
    >
      {template.attivo ? 'Archivia' : 'Ripristina'}
    </Button>
  )
}

/**
 * The «Email al cliente» section of a contract's page (REB-753): the saved emails of this
 * contract, and the step that sends an invoice and its register with one. The send is the
 * person's: it writes the draft and sends it, the one request in the product that emails
 * anybody, and nothing here offers it to an agent.
 */
export function ContractEmailSection({
  contractId,
  customerId,
}: {
  contractId: string
  customerId: string
}) {
  const canWrite = useCan('create_contract_email_template')
  const canSend = useCan('prepare_contract_email')
  const templates = useEmailTemplates(contractId)
  const [editing, setEditing] = useState<{ open: boolean; template?: EmailTemplate }>({ open: false })
  const [sending, setSending] = useState(false)
  const active = (templates.data ?? []).filter((template) => template.attivo)

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="font-semibold">Email al cliente</h2>
          <div className="flex gap-2">
            {canWrite && (
              <Button size="sm" variant="outline" onClick={() => setEditing({ open: true })}>
                <Plus className="mr-2 size-4" />
                Nuovo modello
              </Button>
            )}
            {canSend && active.length > 0 && (
              <Button size="sm" onClick={() => setSending(true)}>
                <Send className="mr-2 size-4" />
                Invia fattura e registro
              </Button>
            )}
          </div>
        </div>

        {templates.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : templates.isError ? (
          <QueryErrorBanner error={templates.error} />
        ) : (templates.data ?? []).length === 0 ? (
          <p className="text-muted-foreground">
            Nessun modello su questo contratto: salvane uno e invii la fattura con il registro in due
            clic.
          </p>
        ) : (
          <ul className="divide-y">
            {(templates.data ?? []).map((template) => (
              <li key={template.id} className="flex items-center gap-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="font-medium">{template.nome}</span>
                    {!template.attivo && <StatusPill tone="muted">Archiviato</StatusPill>}
                  </div>
                  <p className="truncate text-sm text-muted-foreground">
                    {template.oggetto}
                    {template.allegati.length > 0 &&
                      ` · allegati: ${template.allegati.map((kind) => ATTACHMENT_LABEL[kind]).join(', ')}`}
                  </p>
                </div>
                {canWrite && (
                  <>
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label={`Modifica il modello ${template.nome}`}
                      onClick={() => setEditing({ open: true, template })}
                    >
                      <Pencil className="size-4" />
                    </Button>
                    <Archive contractId={contractId} template={template} />
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      {editing.open && (
        <EmailTemplateDialog
          key={editing.template?.id ?? 'new'}
          contractId={contractId}
          template={editing.template}
          open
          onOpenChange={(open) => setEditing((previous) => ({ ...previous, open }))}
        />
      )}
      {sending && (
        <SendInvoiceDialog
          contractId={contractId}
          customerId={customerId}
          templates={active}
          open
          onOpenChange={setSending}
        />
      )}
    </Card>
  )
}
