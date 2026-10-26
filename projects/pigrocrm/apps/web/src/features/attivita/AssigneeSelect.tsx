import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { EVERYONE, ME } from './filters'
import { useAssignees } from './queries'

/**
 * The «whose attività» select the Attività page and the Oggi page share: everybody's,
 * the signed-in person's own, or one colleague's. The two words that are not user ids
 * (`EVERYONE`, `ME`) are `filters.ts`'s, so a page translates the value with one rule.
 */
export function AssigneeSelect({
  value,
  onChange,
}: {
  value: string
  onChange: (next: string) => void
}) {
  const assignees = useAssignees()
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-52" aria-label="Filtra per assegnatario">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={EVERYONE}>Di chiunque</SelectItem>
        <SelectItem value={ME}>Assegnate a me</SelectItem>
        {assignees.options
          .filter((person) => person.id !== assignees.me)
          .map((person) => (
            <SelectItem key={person.id} value={person.id}>
              {person.nome}
            </SelectItem>
          ))}
      </SelectContent>
    </Select>
  )
}
