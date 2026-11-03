import { Button } from '@rebase/ui/button'
import type { Failure } from '@/lib/contracts'

/** «Indietro» and the step's own submit, with the refusal that kept the admin here. */
export function StepFooter({
  onBack,
  next,
  pending,
  ready = true,
  failure,
}: {
  onBack?: () => void
  next: string
  pending: boolean
  ready?: boolean
  failure: Failure | null
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {onBack && (
          <Button type="button" variant="outline" onClick={onBack}>
            Indietro
          </Button>
        )}
        <Button type="submit" disabled={pending || !ready}>
          {pending ? 'Un momento…' : next}
        </Button>
      </div>
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure.message}
        </p>
      )}
    </div>
  )
}
