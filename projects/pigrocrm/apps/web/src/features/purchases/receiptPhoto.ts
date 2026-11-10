import { fetchWithRefresh, toProblem } from '@/lib/api'
import type { components } from '@/lib/api-types'

/**
 * Sends the photo to the inbox and reads none of it: `read=false` stores the file as an item and
 * leaves it unread, so no paid model call is made for a justification the person already
 * explained with the form. The item is then the purchase's `inbox_item_id`. A refused request is
 * a 422 with the sentence that says why and nothing written.
 */
export async function uploadReceiptPhoto(file: File): Promise<string> {
  const body = new FormData()
  body.append('file', file, file.name)
  const response = await fetchWithRefresh('/api/inbox/upload?read=false', { method: 'POST', body })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) throw toProblem(payload, response.status)
  const [item] = (payload as components['schemas']['InboxUploadResult']).items
  if (item === undefined) throw toProblem(null, response.status)
  return item.id
}

/**
 * An amount as typed to the decimal string the API takes: `12,50` and `12.50` both become
 * `12.50`. Nothing is parsed to a number; the shape is checked and the comma swapped for the
 * dot, and what the amount means (zero, too large) is the server's to refuse. `null` when it is
 * not an amount at all.
 */
export function normaliseAmount(value: string): string | null {
  const trimmed = value.trim().replace(',', '.')
  return /^\d+(\.\d{1,2})?$/.test(trimmed) ? trimmed : null
}
