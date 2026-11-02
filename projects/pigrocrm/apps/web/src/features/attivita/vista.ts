/**
 * The two views of the Attività page: «Oggi», what is late and what falls due, and «Tutte»,
 * the whole list as a table. The value is what `?vista=` carries in the URL, so a link to
 * either view is a plain address; the label is what the tab says.
 */
export const VISTE = [
  { id: 'oggi', label: 'Oggi' },
  { id: 'tutte', label: 'Tutte' },
] as const

export type Vista = (typeof VISTE)[number]['id']

export function isVista(value: unknown): value is Vista {
  return VISTE.some((vista) => vista.id === value)
}

/**
 * The view an address asks for. `?vista=` says it; without it the page opens on «Oggi»,
 * except for the two addresses that point at a row or a person in the whole list: an
 * attività to open (`?apri=`, the dialog sits over the table) and a chosen assignee
 * (`?assegnata=`, the Oggi view's «le altre sono nella pagina Attività»).
 */
export function vistaOf(search: { vista?: Vista; apri?: string; assegnata?: string }): Vista {
  if (search.vista !== undefined) return search.vista
  return search.apri !== undefined || search.assegnata !== undefined ? 'tutte' : 'oggi'
}
