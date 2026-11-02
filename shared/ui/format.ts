/**
 * The few display helpers both SPAs wrote for themselves: money as Italian currency, a
 * size in bytes, a blob handed to the browser as a download, a string put on the
 * clipboard. Date formatting stays in each application: the CRM reads a calendar day
 * field by field in the browser's zone and the hub reads it as UTC (`lib/format.ts`),
 * which is a rule of each product, not a copy of one.
 */

// `useGrouping: 'always'` is mandatory: it-IT's default withholds the thousands
// separator below five integer digits, so 1500.00 would print as "1500,00 €".
const euro = new Intl.NumberFormat('it-IT', {
  style: 'currency',
  currency: 'EUR',
  useGrouping: 'always',
})

/**
 * A decimal string from the API (`"450.00"`) as `450,00 €`.
 *
 * The string goes into the formatter verbatim: `Intl.NumberFormat.format` parses a
 * string with full decimal precision (ES2023), whereas `Number("0.29") * 100` is
 * 28.999999999999996 and a formatter fed a float is how cents disappear. Neither SPA
 * may do arithmetic on a money field in the browser, and this does none.
 */
export function formatMoney(value: string): string {
  return euro.format(value as `${number}`)
}

const BYTES_PER_KILOBYTE = 1024
const BYTES_PER_MEGABYTE = BYTES_PER_KILOBYTE * BYTES_PER_KILOBYTE

/**
 * A size as `Intl` writes it in Italian (`12,1 kB`, `1,5 MB`). Divides by 1024 while
 * labelling the decimal unit: `12345` bytes reads `12,1 kB`, the figure Finder and
 * Explorer show, where dividing by 1000 would give `12,3 kB` for the same bytes.
 */
export function formatBytes(bytes: number): string {
  const [unit, divisor] =
    bytes >= BYTES_PER_MEGABYTE
      ? (['megabyte', BYTES_PER_MEGABYTE] as const)
      : bytes >= BYTES_PER_KILOBYTE
        ? (['kilobyte', BYTES_PER_KILOBYTE] as const)
        : (['byte', 1] as const)
  return new Intl.NumberFormat('it-IT', {
    style: 'unit',
    unit,
    maximumFractionDigits: unit === 'byte' ? 0 : 1,
  }).format(bytes / divisor)
}

/** Hands a blob to the browser as a file named `filename`, and releases the object URL. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/** Puts `text` on the clipboard; `false` when the browser refuses (no permission, an
 *  insecure context), so the caller can tell the person to select the text by hand. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
