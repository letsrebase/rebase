import { defaultParseSearch } from '@tanstack/react-router'

/** The list filters that hold an amount in euro, as the lists and the address name them. */
const AMOUNT_PARAMS = ['tariffa_min', 'tariffa_max', 'budget_min', 'budget_max'] as const

/**
 * The router's `parseSearch` (REB-531): the default one, except that an amount filter
 * the address carries unquoted keeps its text. The default runs `JSON.parse` on every
 * raw value, so a link typed or pasted by hand, `?tariffa_min=1.500`, would reach the
 * page as the number 1.5 and the amount rule (`lib/amount.ts`) would never see the
 * Italian «1.500». The links the app writes quote such a value (`%221.500%22`), which
 * the default already reads back as text, so those go through it unchanged, and so does
 * every other parameter.
 */
export function parseSearch(searchStr: string): Record<string, unknown> {
  const parsed: Record<string, unknown> = { ...defaultParseSearch(searchStr) }
  const raw = new URLSearchParams(searchStr)
  for (const param of AMOUNT_PARAMS) {
    const value = raw.get(param)
    if (value !== null && !value.startsWith('"')) parsed[param] = value
  }
  return parsed
}
