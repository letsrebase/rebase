/** «1.234,56» and «1234.56» both read as 1234.56; anything else is not a figure. */
export function parseAmount(text: string, signed: boolean): { value: string | null } | { error: string } {
  const trimmed = text.trim()
  if (trimmed === '') return { value: null }
  const plain = trimmed.includes(',') ? trimmed.replace(/\./g, '').replace(',', '.') : trimmed
  if (!/^-?\d+(\.\d{1,2})?$/.test(plain)) return { error: 'Scrivi un importo in euro, con al massimo due decimali.' }
  if (!signed && plain.startsWith('-')) return { error: 'Questa cifra non può essere negativa.' }
  return { value: plain }
}
