import type { WorkProfilePractice } from '@/lib/api'

export const MARK_CODE = 'contato dal codice'
export const MARK_MODEL = 'dal modello della persona, non ricontrollato'

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/**
 * The working practices of a profile, one row each in the order given: the label, the counts
 * (in how many sessions out of those that could have shown it, over how many products and
 * months) and how the practice was found. Counts only: no bar, no meter, no total.
 * Nothing at all for an empty list.
 */
export function PracticeRows({ practices }: { practices: WorkProfilePractice[] }) {
  if (practices.length === 0) return null
  return (
    <ul aria-label="Come lavora" className="space-y-2">
      {practices.map((practice) => (
        <li key={practice.id}>
          <span className="font-medium">{practice.label}</span>
          <span className="block text-muted-foreground">
            {`in ${plural(practice.seen, 'sessione', 'sessioni')} su ${practice.of}, ${plural(practice.products, 'prodotto', 'prodotti')}, ${plural(practice.months, 'mese', 'mesi')}`}
          </span>
          <span className="block text-xs text-muted-foreground">{practice.method === 'code' ? MARK_CODE : MARK_MODEL}</span>
        </li>
      ))}
    </ul>
  )
}
