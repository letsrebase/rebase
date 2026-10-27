import { Link } from '@tanstack/react-router'
import { badgeVariants } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { pairCount, useDuplicates } from '@/lib/duplicates'

/** «Possibile duplicato», on a talent whose card may be someone already on the list
 *  (REB-800). One other card: the pill opens it. Several: it opens «Duplicati», where
 *  each pair is read side by side. The link wears the badge's own classes: `Badge`'s
 *  `asChild` slots onto one element and hands its Slot two children, so it cannot wrap
 *  a link. */
export function DuplicatePill({ others }: { others: readonly string[] }) {
  if (others.length === 0) return null
  const className = `${badgeVariants({ variant: 'pill' })} hover:underline`
  return others.length === 1 ? (
    <Link to="/admin/freelance/$id" params={{ id: others[0]! }} title="Apri l’altra scheda" className={className}>
      Possibile duplicato
    </Link>
  ) : (
    <Link to="/admin/talent/duplicates" title="Vedi le schede simili" className={className}>
      Possibile duplicato
    </Link>
  )
}

/** The pill on a talent's own page. The detail does not carry the flag (it lives on the
 *  Talenti row), so it reads it from the pairs: the other card of every pair this one is
 *  in. */
export function DuplicateOf({ freelancerId }: { freelancerId: string }) {
  const duplicates = useDuplicates()
  if (!duplicates.data) return null
  const others = [...duplicates.data.unibili, ...duplicates.data.da_rivedere].flatMap((pair) =>
    pair.survivor.freelancer_id === freelancerId
      ? [pair.assorbito.freelancer_id]
      : pair.assorbito.freelancer_id === freelancerId
        ? [pair.survivor.freelancer_id]
        : [],
  )
  return <DuplicatePill others={[...new Set(others)]} />
}

/** The way into «Duplicati» from Talenti: there only while there is a pair to look at. */
export function DuplicatesLink() {
  const duplicates = useDuplicates()
  const count = duplicates.data ? pairCount(duplicates.data) : 0
  if (count === 0) return null
  return (
    <Button asChild variant="outline" size="sm">
      <Link to="/admin/talent/duplicates">Duplicati · {count}</Link>
    </Button>
  )
}
