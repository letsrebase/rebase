/**
 * «Get started» (ORB-180): the assistant first, because it is what the landing sells and
 * what a space is for, then the four first steps with their state, each with a prompt to
 * copy into the assistant (ORB-182). Both read from the data
 * (`useFirstSteps`) and nothing is stored: the card stays until this user has a token, the
 * steps stay with their ticks. The Home sends a person here once, after the first login;
 * the sidebar brings them back whenever they want.
 */
import { Link } from '@tanstack/react-router'
import { Bot, Check, ChevronRight, Circle, Rocket } from 'lucide-react'
import { useEffect } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { useAuth, useCanWrite } from '@/lib/auth'
import { CopyPrompt } from './CopyPrompt'
import { CONNECT_ASSISTANT_TO, markGetStartedSeen, useFirstSteps, type FirstStep } from './firstSteps'
import { INTRO_PROMPT, STEP_PROMPTS } from './prompts'

export function GetStartedPage() {
  const { user } = useAuth()
  const userId = user?.id ?? ''
  const canWrite = useCanWrite()
  const state = useFirstSteps()

  // Being here is what the Home's one-time redirect remembers, however one arrived:
  // through the redirect or through the sidebar. Either way the page has been seen.
  useEffect(() => {
    if (userId) markGetStartedSeen(userId)
  }, [userId])

  return (
    <>
      <PageHeader
        icon={Rocket}
        title="Get started"
        description="Il tuo spazio, dal primo passo all’assistente che lavora per te."
      />
      <div className="space-y-4 px-8 py-6" data-testid="get-started">
        {state.loading ? null : (
          <>
            {!state.assistantConnected && <AssistantCard />}
            <FirstStepsList steps={state.steps} doneCount={state.doneCount} canWrite={canWrite} />
          </>
        )}
      </div>
    </>
  )
}

function AssistantCard() {
  return (
    <Card className="border-foreground border-2">
      <CardHeader className="flex flex-row items-start gap-4">
        <Bot className="mt-1 size-8 shrink-0 text-[var(--color-watermelon)]" aria-hidden />
        <div className="space-y-1.5">
          <CardTitle className="text-xl">Il CRM che lavora al posto tuo</CardTitle>
          <CardDescription className="text-base">
            Collega Claude al tuo spazio e chiedigli di registrare le ore, preparare
            un’offerta, riassumere la settimana. Tutto quello che fai qui lo può fare lui.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <Button asChild size="lg">
          <Link to={CONNECT_ASSISTANT_TO}>
            Collega l’assistente
            <ChevronRight className="ml-1 size-4" aria-hidden />
          </Link>
        </Button>
        {/* What to say first, once connected (ORB-182). */}
        <CopyPrompt text={INTRO_PROMPT} summary="Il primo prompt, appena collegato" />
      </CardContent>
    </Card>
  )
}

function FirstStepsList({
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
    <Card>
      <CardHeader>
        <CardTitle>Primi passi</CardTitle>
        <CardDescription>
          {allDone
            ? 'Fatti tutti. Da qui in avanti il CRM è tuo.'
            : `${doneCount} di ${steps.length}. Nell’ordine in cui il CRM li chiede.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="divide-y">
          {steps.map((step) => {
            const linkable = !step.done && canWrite && step.canDo
            return (
              <li key={step.id} className="flex items-start gap-3 py-3">
                {step.done ? (
                  <Check className="text-foreground mt-0.5 size-5 shrink-0" aria-hidden />
                ) : (
                  <Circle className="text-muted-foreground mt-0.5 size-5 shrink-0" aria-hidden />
                )}
                <div className="min-w-0 flex-1">
                  <span className="sr-only">{step.done ? 'Fatto: ' : 'Da fare: '}</span>
                  {step.done ? (
                    <p className="text-muted-foreground line-through">{step.title}</p>
                  ) : linkable ? (
                    <Link to={step.to} className="font-medium underline-offset-4 hover:underline">
                      {step.title}
                    </Link>
                  ) : (
                    <p className="font-medium">{step.title}</p>
                  )}
                  {!step.done && (
                    <p className="text-muted-foreground text-sm">
                      {canWrite ? step.hint : 'Lo fa chi può scrivere nello spazio.'}
                    </p>
                  )}
                  {/* The same step, said to the assistant (ORB-182): only for a step still
                      to do, and only for someone who may do it. Named after the step, so
                      three disclosures on one page read apart. */}
                  {linkable && (
                    <CopyPrompt
                      text={STEP_PROMPTS[step.id]}
                      summary={`Prompt per l’assistente: ${step.title}`}
                    />
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      </CardContent>
    </Card>
  )
}
