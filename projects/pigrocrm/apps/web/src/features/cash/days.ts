/** A day count the server computed, in words: «1 giorno», «41 giorni». Callers write «da …». */
export function daysCount(days: number): string {
  return days === 1 ? '1 giorno' : `${days} giorni`
}
