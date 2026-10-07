import { defaultParseSearch } from '@tanstack/react-router'

/** The list filters that hold an amount in euro, as the lists and the address name them. */
const AMOUNT_PARAMS = ['tariffa_min', 'tariffa_max', 'budget_min', 'budget_max'] as const

/** Parameters that are a visitor's own text, kept exactly as the address carries them
 *  (REB-675): the landing's form without JavaScript sends the whole project description
 *  as `?descrizione=`, and a description that happens to read as JSON (`{"progetto":
 *  …}`, `[…]`, `123`, `true`) must reach the box as the words it is, never as the value
 *  the default parser makes of it. Quoted or not: the text is the text. */
const TEXT_PARAMS = ['descrizione'] as const

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
    // Only a single, unquoted value: a repeated parameter stays the list the default
    // makes, which `strParam` refuses, rather than quietly becoming its first value.
    const [value, ...more] = raw.getAll(param)
    if (value !== undefined && more.length === 0 && !value.trimStart().startsWith('"')) parsed[param] = value
  }
  for (const param of TEXT_PARAMS) {
    const [value, ...more] = raw.getAll(param)
    if (value !== undefined && more.length === 0) parsed[param] = value
  }
  return parsed
}
