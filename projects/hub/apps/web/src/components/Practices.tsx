import type { WorkProfilePractice } from '@/lib/api'

export const MARK_CODE = 'contato dal codice'
export const MARK_MODEL = 'dal modello della persona, non ricontrollato'

const AGENT_LABELS: Record<string, string> = {
  omp: 'oh-my-pi',
  'claude-code': 'Claude Code',
  codex: 'Codex',
  opencode: 'OpenCode',
  gemini: 'Gemini CLI',
  cursor: 'Cursor',
  copilot: 'Copilot CLI',
  kimi: 'Kimi',
  qwen: 'Qwen Code',
  'anthropic-api': 'API Anthropic',
  'slash-command': 'comando /rebase-verify',
}

const mark = (practice: WorkProfilePractice) => {
  if (practice.method === 'code') return MARK_CODE
  const by = practice.classified_by
  if (!by) return MARK_MODEL
  return `${MARK_MODEL} · ${AGENT_LABELS[by.agent] ?? by.agent}${by.model ? `, ${by.model}` : ''}`
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/**
 * The working practices of a profile, one row each in the order given: the label, the counts
 * (in how many sessions out of those that could have shown it, over how many products and
 * months) and how the practice was found. Counts only: no bar, no meter, no total.
 * `labelledBy` is the id of the heading the list sits under. Nothing at all for an empty list.
 */
export function PracticeRows({ practices, labelledBy }: { practices: WorkProfilePractice[]; labelledBy: string }) {
  if (practices.length === 0) return null
  return (
    <ul aria-labelledby={labelledBy} className="space-y-2 text-sm">
      {practices.map((practice) => (
        <li key={practice.id}>
          <span className="font-medium">{practice.label}</span>
          {/* The final full stop separates the counts from the mark for a screen reader. */}
          <span className="block text-muted-foreground">
            {`in ${plural(practice.seen, 'sessione', 'sessioni')} su ${practice.of}, ${plural(practice.products, 'prodotto', 'prodotti')}, ${plural(practice.months, 'mese', 'mesi')}.`}
          </span>
          <span className="block text-xs text-muted-foreground">{mark(practice)}</span>
        </li>
      ))}
    </ul>
  )
}
