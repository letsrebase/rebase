import { Link } from '@tanstack/react-router'
import { CalendarCheck, CalendarPlus } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { useCanWrite } from '@/lib/auth'
import { DayRows } from './DayRows'
import { useDayContractOptions, useWorkUnits } from './queries'
import { defaultMonth, monthWindow, type DaysSearch } from './search'

const EVERY_CONTRACT = '__all__'

/**
 * `/app/days`: the register of contract days. A contract, a month, or both, in the URL.
 *
 * With nothing chosen it opens on the current month of every contract; with a contract and
 * no month it is that contract's whole ledger (the API's own bound, the newest two hundred,
 * said on the page when it applies). The API refuses a list with neither a contract nor a
 * date, so «every contract, every month» is not a view this page offers.
 */
export function DaysPage({
  search,
  onSearchChange,
}: {
  search: DaysSearch
  onSearchChange: (next: DaysSearch) => void
}) {
  const canWrite = useCanWrite()
  const contracts = useDayContractOptions(search.contract)
  const month = search.month ?? (search.contract === undefined ? defaultMonth() : undefined)
  const window = month === undefined ? undefined : monthWindow(month)
  const days = useWorkUnits({ contractId: search.contract, from: window?.from, to: window?.to })

  return (
    <>
      <PageHeader
        icon={CalendarCheck}
        title="Giornate"
        description="Le giornate lavorate sui contratti, con il loro stato."
        actions={
          canWrite ? (
            <Button asChild>
              <Link to="/app/days/new" search={{ contract: search.contract }}>
                <CalendarPlus className="mr-2 size-4" aria-hidden />
                Registra giornata
              </Link>
            </Button>
          ) : undefined
        }
      >
        <div className="flex flex-wrap items-end gap-4 px-8 pb-4">
          <div className="space-y-1">
            <Label htmlFor="days-contract">Contratto</Label>
            <Select
              value={search.contract ?? EVERY_CONTRACT}
              onValueChange={(value) =>
                onSearchChange({ contract: value === EVERY_CONTRACT ? undefined : value, month: search.month })
              }
            >
              <SelectTrigger id="days-contract" className="w-64">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={EVERY_CONTRACT}>Tutti i contratti</SelectItem>
                {(contracts.data ?? []).map((contract) => (
                  <SelectItem key={contract.id} value={contract.id}>
                    {contract.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="days-month">Mese</Label>
            <Input
              id="days-month"
              type="month"
              value={month ?? ''}
              onChange={(event) =>
                onSearchChange({ contract: search.contract, month: event.target.value || undefined })
              }
              className="w-44"
            />
          </div>
        </div>
      </PageHeader>
      <div className="px-8 py-6">
        <DayRows
          days={days}
          empty={{
            title: 'Nessuna giornata',
            body: 'In questo periodo non c’è nessuna giornata registrata. Registrane una dal pulsante in alto.',
          }}
        />
      </div>
    </>
  )
}
