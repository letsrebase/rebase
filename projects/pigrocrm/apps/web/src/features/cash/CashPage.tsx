import { useState } from 'react'
import { Wallet } from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { useIsAdmin } from '@/lib/auth'
import { UploadStatementDialog } from './account/UploadStatementDialog'
import { CashSchedule } from './schedule/CashSchedule'

/**
 * «Cassa»: when money comes in and goes out, week by week, with today's balance per account
 * (the brief `2026-11-10-cash-and-schedule-brief.md`). The body is the schedule the Home tab
 * «Scadenziario» shows too. The one strong button, «Carica estratto conto», is an
 * administrator's; on the phone a statement is added from «Carica un documento» or from an
 * account page, so the header draws no button there.
 */
export function CashPage() {
  const admin = useIsAdmin()
  const desktop = useMediaQuery('(min-width: 768px)')
  const [uploading, setUploading] = useState(false)

  return (
    <>
      <PageHeader
        icon={Wallet}
        title="Cassa"
        description="Quando i soldi entrano ed escono, settimana per settimana, e cosa è già in ritardo."
        actions={
          admin && desktop ? (
            <Button onClick={() => setUploading(true)}>Carica estratto conto</Button>
          ) : undefined
        }
      />
      <div className="px-8 py-6">
        <CashSchedule />
      </div>
      {uploading && <UploadStatementDialog open onOpenChange={setUploading} />}
    </>
  )
}
