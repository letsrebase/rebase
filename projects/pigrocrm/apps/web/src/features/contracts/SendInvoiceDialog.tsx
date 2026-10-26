import { Paperclip, Send } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Skeleton } from '@rebase/ui/skeleton'
import { formatInvoiceNumber } from '@/features/invoices/format'
import { useInvoices } from '@/features/invoices/queries'
import { formatAmount } from './labels'
import { useEmailPreview, useSendContractEmail, type EmailTemplate } from './emailQueries'

interface Props {
  contractId: string
  customerId: string
  templates: EmailTemplate[]
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * «Invia fattura e registro»: an issued invoice of the customer and one of the contract's
 * templates, the text that would leave and the files that go with it, and «Invia». The
 * register is the one for the days the invoice bills, which the server derives, so the
 * file and the invoice cannot disagree. Two choices and the press: the invoice is the
 * newest issued one and the template the first, so the usual send is just «Invia».
 */
export function SendInvoiceDialog({ contractId, customerId, templates, open, onOpenChange }: Props) {
  const invoices = useInvoices({ customer_id: customerId, tipo: 'fattura', stato: 'emessa', limit: 50 })
  const [invoiceChoice, setInvoiceChoice] = useState<string>()
  const [templateChoice, setTemplateChoice] = useState<string>()
  const [recipients, setRecipients] = useState<string>()

  const issued = invoices.data?.items ?? []
  const invoiceId = invoiceChoice ?? issued[0]?.id
  const templateId = templateChoice ?? templates[0]?.id
  const preview = useEmailPreview(contractId, templateId, invoiceId)
  const send = useSendContractEmail(contractId)
  // The first suggested address, until the person types their own.
  const to = recipients ?? preview.data?.to_suggeriti[0] ?? ''

  function submit() {
    if (invoiceId === undefined || templateId === undefined) return
    send.mutate(
      {
        templateId,
        invoiceId,
        to: to
          .split(/[,;\s]+/)
          .map((address) => address.trim())
          .filter((address) => address !== ''),
      },
      {
        onSuccess: () => {
          toast.success('Email inviata: la trovi nella corrispondenza.')
          onOpenChange(false)
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Invia fattura e registro</DialogTitle>
        </DialogHeader>

        {invoices.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : invoices.isError ? (
          <QueryErrorBanner error={invoices.error} />
        ) : issued.length === 0 ? (
          <p className="text-muted-foreground">
            Il cliente non ha ancora una fattura emessa: emetti prima quella con le giornate.
          </p>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="send-invoice">Fattura</Label>
                <Select value={invoiceId} onValueChange={setInvoiceChoice}>
                  <SelectTrigger id="send-invoice" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {issued.map((invoice) => (
                      <SelectItem key={invoice.id} value={invoice.id}>
                        {formatInvoiceNumber(invoice)} · {formatAmount(invoice.totale, invoice.divisa)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="send-template">Modello</Label>
                <Select value={templateId} onValueChange={setTemplateChoice}>
                  <SelectTrigger id="send-template" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {templates.map((template) => (
                      <SelectItem key={template.id} value={template.id}>
                        {template.nome}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {preview.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : preview.isError ? (
              <QueryErrorBanner error={preview.error} />
            ) : preview.data === undefined ? null : (
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="send-to">A</Label>
                  <Input
                    id="send-to"
                    value={to}
                    placeholder="indirizzo@cliente.it"
                    onChange={(event) => setRecipients(event.target.value)}
                  />
                </div>
                <div className="rounded-md border p-3">
                  <p className="font-medium">{preview.data.subject}</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm">{preview.data.body}</p>
                </div>
                {preview.data.allegati.length > 0 && (
                  <ul className="space-y-1 text-sm text-muted-foreground">
                    {preview.data.allegati.map((attachment) => (
                      <li key={attachment.tipo} className="flex items-center gap-2">
                        <Paperclip className="size-4" aria-hidden="true" />
                        {attachment.nome}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}

        {send.isError && <QueryErrorBanner error={send.error} />}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={send.isPending || preview.data === undefined || to === ''}>
            <Send className="mr-2 size-4" aria-hidden="true" />
            {send.isPending ? 'Invio…' : 'Invia'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
