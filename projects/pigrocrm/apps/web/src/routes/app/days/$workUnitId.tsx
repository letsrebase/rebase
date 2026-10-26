import { createFileRoute, useParams } from '@tanstack/react-router'
import { DayDetail } from '@/features/days/DayDetail'

function DayRoute() {
  const { workUnitId } = useParams({ from: '/app/days/$workUnitId' })
  return <DayDetail workUnitId={workUnitId} />
}

export const Route = createFileRoute('/app/days/$workUnitId')({
  component: DayRoute,
})
