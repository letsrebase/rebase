/**
 * Which uploaded invoice the door is waiting on (REB-224), remembered in this browser per
 * space and per user, the way the start page's redirect once remembered its visit.
 *
 * Why remembered at all: the door's first step creates or picks a customer, so on the
 * next visit the Home is the dashboard, and the handoff is still open on «Primi passi».
 * Without this the door would forget the document it gave the assistant and could never
 * say «Registrata». Why only here and not on the document: the spec rules out a new flag
 * (§6), and the relation that matters, an invoice whose `pdf_document_id` is this
 * document, is the server's and is read from there.
 *
 * Every access is guarded: a browser that refuses storage simply has a door that starts
 * over on the next visit.
 */
import { tenantPrefix } from '@/lib/tenant'

export interface InvoiceHandoff {
  documentId: string
  customerId: string
  customerName: string
}

export function handoffKey(userId: string): string {
  return `pigrocrm.fattura-da-registrare:${tenantPrefix || '/'}:${userId}`
}

export function readHandoff(userId: string): InvoiceHandoff | null {
  if (!userId) return null
  try {
    const raw = window.localStorage.getItem(handoffKey(userId))
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    if (
      typeof value === 'object' &&
      value !== null &&
      typeof (value as InvoiceHandoff).documentId === 'string' &&
      typeof (value as InvoiceHandoff).customerId === 'string' &&
      typeof (value as InvoiceHandoff).customerName === 'string'
    ) {
      const { documentId, customerId, customerName } = value as InvoiceHandoff
      return { documentId, customerId, customerName }
    }
    return null
  } catch {
    return null
  }
}

export function rememberHandoff(userId: string, handoff: InvoiceHandoff): void {
  try {
    window.localStorage.setItem(handoffKey(userId), JSON.stringify(handoff))
  } catch {
    // Storage refused: the door still shows the handoff until the page is left.
  }
}

export function forgetHandoff(userId: string): void {
  try {
    window.localStorage.removeItem(handoffKey(userId))
  } catch {
    // Nothing was stored, or nothing can be: either way there is nothing to forget.
  }
}
