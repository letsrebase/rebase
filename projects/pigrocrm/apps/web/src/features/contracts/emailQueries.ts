import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'

export type EmailTemplate = components['schemas']['ContractEmailTemplateRead']
export type EmailTemplateCreateBody = components['schemas']['ContractEmailTemplateCreate']
export type EmailTemplateUpdateBody = components['schemas']['ContractEmailTemplateUpdate']
export type EmailPreview = components['schemas']['ContractEmailPreview']
export type AttachmentKind = EmailTemplate['allegati'][number]

const templatesKey = (contractId: string) => ['contract', contractId, 'email-templates'] as const

export function useEmailTemplates(contractId: string) {
  return useQuery({
    queryKey: templatesKey(contractId),
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/email-templates', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
}

export function useEmailVariables(contractId: string) {
  return useQuery({
    queryKey: ['contract', contractId, 'email-template-variables'] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/contracts/{contract_id}/email-template-variables', {
          params: { path: { contract_id: contractId } },
        }),
      ),
  })
}

export function useCreateEmailTemplate(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: EmailTemplateCreateBody) =>
      unwrap(
        api.POST('/api/contracts/{contract_id}/email-templates', {
          params: { path: { contract_id: contractId } },
          body,
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: templatesKey(contractId) }),
  })
}

export function useUpdateEmailTemplate(contractId: string, templateId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: EmailTemplateUpdateBody) =>
      unwrap(
        api.PATCH('/api/contracts/{contract_id}/email-templates/{template_id}', {
          params: { path: { contract_id: contractId, template_id: templateId } },
          body,
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: templatesKey(contractId) }),
  })
}

/** What would leave, with nothing written. Asked once both choices are made. */
export function useEmailPreview(
  contractId: string,
  templateId: string | undefined,
  invoiceId: string | undefined,
) {
  return useQuery({
    enabled: templateId !== undefined && invoiceId !== undefined,
    queryKey: ['contract', contractId, 'email-preview', templateId, invoiceId] as const,
    queryFn: () =>
      unwrap(
        api.POST('/api/contracts/{contract_id}/email/preview', {
          params: { path: { contract_id: contractId } },
          body: { template_id: templateId ?? '', invoice_id: invoiceId ?? '' },
        }),
      ),
  })
}

/**
 * «Invia»: the draft with the files attached, then its send, which is the one request in
 * the product that emails anybody and is the person's alone. Two calls on purpose: the
 * draft is durable once written, so a send that fails (Gmail not connected, a revoked
 * grant) leaves a draft in the customer's Email tab to send from there, not a lost text.
 */
export function useSendContractEmail(contractId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (variables: {
      templateId: string
      invoiceId: string
      to: string[]
    }) => {
      const draft = await unwrap(
        api.POST('/api/contracts/{contract_id}/email/draft', {
          params: { path: { contract_id: contractId } },
          body: {
            template_id: variables.templateId,
            invoice_id: variables.invoiceId,
            to_addresses: variables.to,
          },
        }),
      )
      return unwrap(
        api.POST('/api/email-drafts/{draft_id}/send', {
          params: { path: { draft_id: draft.id } },
          body: { updated_at: draft.updated_at },
        }),
      )
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['email-drafts'] })
      void queryClient.invalidateQueries({ queryKey: ['documents'] })
    },
  })
}
