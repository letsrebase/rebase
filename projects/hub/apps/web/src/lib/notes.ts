import type { NoteSource } from '@/lib/api'

/** What a note's (or a trail entry's) source reads as: the door it came through. `null`
 *  is a note from before the source was tracked, which shows no badge. */
export const NOTE_SOURCE_LABELS: Record<NoteSource, string> = {
  web: 'dal sito',
  mcp: 'MCP',
  persona: 'dalla persona',
  sistema: 'dal sistema',
}

export function noteSourceLabel(fonte: NoteSource | null | undefined): string | null {
  return fonte ? NOTE_SOURCE_LABELS[fonte] : null
}
