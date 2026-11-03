/**
 * The two views of the Tasks page: «Oggi», what is late and what falls due, and «Tutte»,
 * the whole list as a table. The value is what `?view=` carries in the URL, so a link to
 * either view is a plain address; the label is what the tab says.
 */
export const VIEWS = [
  { id: 'today', label: 'Oggi' },
  { id: 'all', label: 'Tutte' },
] as const

export type Vista = (typeof VIEWS)[number]['id']

export function isVista(value: unknown): value is Vista {
  return VIEWS.some((view) => view.id === value)
}

/**
 * The view an address asks for. `?view=` says it; without it the page opens on «Oggi»,
 * except for the two addresses that point at a row or a person in the whole list: an
 * tasks to open (`?open=`, the dialog sits over the table) and a chosen assignee
 * (`?assigned=`, the Oggi view's «le altre sono nella pagina Attività»).
 */
export function vistaOf(search: { view?: Vista; open?: string; assigned?: string }): Vista {
  if (search.view !== undefined) return search.view
  return search.open !== undefined || search.assigned !== undefined ? 'all' : 'today'
}
