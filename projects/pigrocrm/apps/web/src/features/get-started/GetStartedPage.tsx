/**
 * «Primi passi», the start page (spec 2026-09-16 §4, REB-222), in three blocks:
 *
 * 1. **Porta dentro il tuo lavoro**: what brings a person's real work in without typing
 *    it, the Gmail door (`GmailDoor`) and the invoice door of §6 (`InvoiceDoor`,
 *    REB-224).
 * 2. **Fai lavorare l’assistente**: the connection itself, inline (`ConnectAgentPanel`,
 *    the body of the sidebar's dialog), and the first prompt to say to it. Once this
 *    person has a token the block is the prompt alone.
 * 3. **Oppure a mano**: the four first steps, each with its screen and its prompt side
 *    by side. The §6.7 rules of 2026-09-12 stand: a step is ticked because the thing
 *    exists, a person who cannot do it reads who can, nothing can be hidden.
 *
 * The same page is the Home while the space is empty (`HomePage`) and the sidebar's
 * «Primi passi» always, so whoever arrives from either sees the same thing. Nothing is
 * stored: every state is read from the data (`useFirstSteps`, `useGmailHealth`).
 */
import { Link } from '@tanstack/react-router'
import { Check, Circle, Rocket } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { useCanWrite } from '@/lib/auth'
import { ConnectAgentPanel } from '@/features/tokens/ConnectAgentPanel'
import type { CreatedToken } from '@/features/tokens/queries'
import { useUnsavedTokenGuard } from '@/features/tokens/useUnsavedTokenGuard'
import { CopyPrompt, PromptBody } from './CopyPrompt'
import { useFirstSteps, type FirstStep } from './firstSteps'
import { GmailDoor } from './GmailDoor'
import { InvoiceDoor } from './InvoiceDoor'
import { INTRO_PROMPT, STEP_PROMPTS } from './prompts'

export function GetStartedPage({ esito }: { esito?: string }) {
  const canWrite = useCanWrite()
  const state = useFirstSteps()

  return (
    <>
      <PageHeader
        icon={Rocket}
        title="Primi passi"
        description="Il tuo spazio, dal primo passo all’assistente che lavora per te."
      />
      <div className="space-y-4 px-8 py-6" data-testid="get-started">
        {state.loading ? null : (
          <>
            <Block
              title="Porta dentro il tuo lavoro"
              description="Il lavoro che hai già, senza riscriverlo a mano."
            >
              {/* Two doors side by side (§4.2): the mailbox, and the last invoice issued
                  (REB-224). */}
              <div className="grid gap-4 lg:grid-cols-2 lg:items-start">
                <GmailDoor esito={esito} />
                <InvoiceDoor assistantConnected={state.assistantConnected} />
              </div>
            </Block>
            {/* Side by side on a wide screen: the assistant and the manual steps are the
                two ways to use what the first block brought in, and the connection's
                snippets are tall enough to push the steps under the fold on their own. */}
            <div className="grid gap-4 xl:grid-cols-2 xl:items-start">
              <AssistantBlock connected={state.assistantConnected} />
              <ManualSteps steps={state.steps} doneCount={state.doneCount} canWrite={canWrite} />
            </div>
          </>
        )}
      </div>
    </>
  )
}

function Block({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: ReactNode
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <h2 className="text-lg">{title}</h2>
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

/**
 * The connection inline, until this person has a token; then the prompt alone.
 *
 * The token minted here is held here, and it keeps the panel on screen: minting one
 * refreshes the token read behind `connected`, and without `issued` the panel would
 * vanish the instant it showed the one copy of the token that will ever exist. The same
 * guard as the dialog and the Token page asks before leaving with it on screen, and
 * «Ho copiato il token» puts it away, as «Chiudi» does in the dialog.
 */
function AssistantBlock({ connected }: { connected: boolean }) {
  const [issued, setIssued] = useState<CreatedToken | null>(null)
  useUnsavedTokenGuard(Boolean(issued))
  const full = !connected || issued !== null
  return (
    <Block
      title="Fai lavorare l’assistente"
      description={
        full
          ? 'Collega Claude al tuo spazio e chiedigli di registrare le ore, preparare un’offerta, riassumere la settimana. Tutto quello che fai qui lo può fare lui.'
          : 'Il tuo assistente è collegato: ecco da dove cominciare.'
      }
    >
      {full && <ConnectAgentPanel issued={issued} onIssued={setIssued} />}
      {issued && (
        <Button type="button" variant="outline" className="mt-4" onClick={() => setIssued(null)}>
          Ho copiato il token
        </Button>
      )}
      {/* What to say first, once connected (ORB-182). */}
      <CopyPrompt text={INTRO_PROMPT} summary="Il primo prompt, appena collegato" />
    </Block>
  )
}

function ManualSteps({
  steps,
  doneCount,
  canWrite,
}: {
  steps: FirstStep[]
  doneCount: number
  canWrite: boolean
}) {
  const allDone = doneCount === steps.length
  return (
    <Block
      title="Oppure a mano"
      description={
        allDone
          ? 'Fatti tutti. Da qui in avanti il CRM è tuo.'
          : `${doneCount} di ${steps.length}. Nell’ordine in cui il CRM li chiede.`
      }
    >
      <ol className="divide-y">
        {steps.map((step) => (
          <StepRow key={step.id} step={step} canWrite={canWrite} />
        ))}
      </ol>
    </Block>
  )
}

/**
 * One step: its title and hint, then two actions side by side while it is still to do
 * and the person may do it, the screen and the prompt (spec 2026-09-16 §4.2). The
 * prompt opens under the row rather than inside it, so the two actions stay on one line.
 */
function StepRow({ step, canWrite }: { step: FirstStep; canWrite: boolean }) {
  const [promptOpen, setPromptOpen] = useState(false)
  const actionable = !step.done && canWrite && step.canDo
  const promptId = `prompt-${step.id}`
  // Four «Chiedilo all’assistente» and two «A mano: Deal» on one page: each action is
  // described by its step's title, so a screen reader's list of controls reads apart.
  const titleId = `step-${step.id}`
  return (
    <li className="flex items-start gap-3 py-3">
      {step.done ? (
        <Check className="text-foreground mt-0.5 size-5 shrink-0" aria-hidden />
      ) : (
        <Circle className="text-muted-foreground mt-0.5 size-5 shrink-0" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <span className="sr-only">{step.done ? 'Fatto: ' : 'Da fare: '}</span>
        <p id={titleId} className={step.done ? 'text-muted-foreground line-through' : 'font-medium'}>
          {step.title}
        </p>
        {!step.done && (
          <p className="text-muted-foreground text-sm">
            {canWrite ? step.hint : 'Lo fa chi può scrivere nello spazio.'}
          </p>
        )}
        {actionable && (
          <>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
              <Link to={step.to} aria-describedby={titleId} className="font-medium underline underline-offset-4">
                A mano: {step.screen}
              </Link>
              <button
                type="button"
                aria-describedby={titleId}
                aria-expanded={promptOpen}
                aria-controls={promptOpen ? promptId : undefined}
                onClick={() => setPromptOpen((open) => !open)}
                className="font-medium underline underline-offset-4"
              >
                Chiedilo all’assistente
              </button>
            </div>
            {promptOpen && <PromptBody id={promptId} text={STEP_PROMPTS[step.id]} className="mt-2" />}
          </>
        )}
      </div>
    </li>
  )
}
