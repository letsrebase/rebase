"""The anonymous cards' commands: the backlog, an admin's regeneration, the Message Batch,
the sources and the evaluation. Each command imports the model stack inside its handler."""

import argparse
import sys
from collections.abc import Callable, Sequence
from contextlib import closing
from uuid import UUID

from rebase_core.cli.common import Command, no_arguments, open_session
from rebase_core.config import get_settings
from rebase_core.errors import DomainError


def cards_refresh(limit: int) -> int:
    """`rebase cards-refresh [--limit N]`: the anonymous card of every freelancer whose CV
    has none yet, or has changed since, `limit` at a time, the oldest first (REB-510).
    Run once after the deploy that brings the key, and again until it prints «0 schede
    scritte, 0 non riuscite». A CV that failed on its own account is not tried again
    until it changes, so the runs end; a batch that met an outage stops there, counts it
    among «non riuscite», says «fermato: modello non disponibile» and leaves it and the
    rest to the next run. A card still giving the gender away after its rewrite is among
    «non riuscite» (REB-802), and not tried again until its CV changes. Without a model
    configured for the `card` task it says so, rather than printing «0 schede scritte» for
    ever."""
    from rebase_core.card_writer import CardWriter
    from rebase_core.llm import call_for_task

    if limit < 1:
        print("--limit deve essere almeno 1.", file=sys.stderr)
        return 2
    settings = get_settings()
    opener = open_session(settings)
    llm = call_for_task(lambda: closing(opener()), settings, "card")
    if llm is None:
        print(
            "Nessun modello configurato per le schede: apri «Modelli AI».",
            file=sys.stderr,
        )
        return 1
    session = opener()
    try:
        result = CardWriter(session, llm, workers=settings.cards_workers).refresh_stale(limit)
    finally:
        session.close()
    summary = f"{result.written} schede scritte, {result.failed} non riuscite"
    if result.stopped:
        summary += ", fermato: modello non disponibile"
    print(summary)
    return 0


def cards_regenerate(limit: int, batch: bool, admin_email: str | None) -> int:
    """`rebase cards-regenerate --admin EMAIL [--limit N] [--batch]` (REB-853, REB-861): an
    admin's start of the regeneration from the command line: the oldest `limit` stale cards
    («Schede da aggiornare»), written again with `REBASE_CARDS_WORKERS` at once, or, from
    `REBASE_CARDS_BATCH_THRESHOLD` cards up (always with `--batch`), as one Message Batch
    at half the price, which `rebase cards-batch-poll` and the `cards` service carry on. A
    provider with no batch API falls back to the workers, and says so. `--admin` is the
    address of the administrator who asks, and is required: the start, each card and the end
    are signed in the trail with it, `source` `cli`; with none, or one that is not an
    administrator's, nothing is started and the exit code is 2. A start while another is on
    writes nothing and says so."""
    from rebase_core.card_batch import CardBatches
    from rebase_core.card_writer import CardWriter
    from rebase_core.llm import call_for_task
    from rebase_core.llm_batch import batch_for_task
    from rebase_core.users import UserService

    if limit < 1:
        print("--limit deve essere almeno 1.", file=sys.stderr)
        return 2
    if not admin_email or not admin_email.strip():
        print(
            "--admin vuole l'indirizzo di un amministratore: la rigenerazione è firmata.",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()
    opener = open_session(settings)
    email = admin_email.strip().lower()
    with closing(opener()) as lookup:
        admin = UserService(lookup, settings).by_email(email)
        admin_id = admin.id if admin is not None and admin.role == "admin" else None
    if admin_id is None:
        print(f"Nessun amministratore con email {email}", file=sys.stderr)
        return 2
    llm = call_for_task(lambda: closing(opener()), settings, "card")
    if llm is None:
        print(
            "Nessun modello configurato per le schede: apri «Modelli AI».",
            file=sys.stderr,
        )
        return 1
    call = batch_for_task(lambda: closing(opener()), settings, "card")
    if call is None and batch:
        print("Il fornitore delle schede non ha un Batch API: scrivo una scheda alla volta.")
    batches = (
        CardBatches(call, 1 if batch else settings.cards_batch_threshold)
        if call is not None
        else None
    )
    session = opener()
    try:
        writer = CardWriter(session, llm, workers=settings.cards_workers, batches=batches)
        if writer.regeneration_running():
            print("Una rigenerazione è già in corso: nessuna scheda avviata.")
            return 0
        result = writer.regenerate_stale(admin_id, limit, source="cli")
    finally:
        session.close()
    if result.batched:
        print(f"{result.batched} schede inviate al Batch API: `rebase cards-batch-poll` le applica")
        return 0
    summary = f"{result.written} schede scritte, {result.failed} non riuscite"
    if result.stopped:
        summary += ", fermato: modello non disponibile"
    print(summary)
    return 0


def cards_batch_poll() -> int:
    """`rebase cards-batch-poll` (REB-853): one pass over the regeneration sent through the
    Batch API: asks whether it ended and, once it has, applies each result as a synchronous
    write would (`CardBatches.poll`). Run every few minutes by the `cards` service, whatever
    `REBASE_CARDS_LOOP` says (an admin started the batch), and by hand to resume one after a
    restart: its state is in the database and its results at the provider. With no batch
    on, it says so and exits 0 so the loop keeps running."""
    from rebase_core.card_batch import CardBatches
    from rebase_core.card_writer import CardWriter
    from rebase_core.llm import call_for_task
    from rebase_core.llm_batch import AiBatch

    settings = get_settings()
    opener = open_session(settings)
    llm = call_for_task(lambda: closing(opener()), settings, "card")
    batch = AiBatch(lambda: closing(opener()), settings, "card")
    session = opener()
    try:
        polled = CardBatches(batch, settings.cards_batch_threshold).poll(
            CardWriter(session, llm, workers=settings.cards_workers)
        )
    finally:
        session.close()
    lines = {
        "none": "nessun batch di schede in corso",
        "busy": "un altro passaggio sta applicando il batch",
        "running": f"batch in corso al fornitore: {polled.left} schede da applicare",
        "failed": "batch mai partito: le schede restano da aggiornare",
        "unavailable": "il fornitore non risponde: riprovo al prossimo passaggio",
        "applied": f"batch concluso: {polled.written} schede scritte, {polled.failed} non riuscite",
        "applying": (
            f"{polled.written} schede scritte, {polled.failed} non riuscite, "
            f"{polled.left} ancora da applicare"
        ),
    }
    print(lines[polled.state])
    return 0


def sources_refetch(limit: int) -> int:
    """`rebase sources-refetch [--limit N]` (REB-839): the weekly pass over the sources a
    card is written from. Reads, for up to `limit` people at a time, the GitHub account
    and the links whose text is a week old, missing, or no longer matches what the person
    gave, and deletes the text of a link the person removed. It never calls a model and
    never writes a card: a source that changed only marks the card, for an admin's start
    («Schede da aggiornare»). The `cards` service runs it every hour before `cards-refresh`,
    with or without a model. With every switch off it says so and reads nothing."""
    from rebase_core.sources import default_source_service

    if limit < 1:
        print("--limit deve essere almeno 1.", file=sys.stderr)
        return 2
    settings = get_settings()
    opener = open_session(settings)
    session = opener()
    try:
        service = default_source_service(session)
        if not service.enabled_kinds():
            print("Nessuna fonte accesa: apri «Modelli AI».")
            return 0
        refetched = service.refetch_due(limit)
    finally:
        session.close()
    print(f"{refetched} persone riviste")
    return 0


def cards_eval(
    sample_size: int | None,
    freelancers: Sequence[UUID],
    provider: str | None,
    model: str | None,
    effort: str | None,
    prompt_version: int,
    judge_provider: str | None,
    judge_model: str | None,
    baseline: UUID | None = None,
    allow_disabled: bool = False,
) -> int:
    """`rebase cards-eval [--sample N | --freelancer ID ...] [--provider P --model M
    [--effort E]] [--prompt-version V] [--judge-provider P --judge-model M] [--baseline
    RUN_ID] [--allow-disabled]` (REB-842): writes the card of each person of the fixed
    sample (or of the `--sample N` first of it, a failure at a time, or of the people
    named) with the provider and model (the `card` task's own when left out), effort and
    prompt version asked, then cross-checks every card against the sources it came from:
    the rules (years recomputed from the CV's dates where the text allows, the seniority's
    band, the guards, the identifiers the person's data holds) and a separate judging call,
    answered by `--judge-provider` and `--judge-model` or, left out, by the first of
    `DEFAULT_JUDGES` that is not the writer (the same model as the writer is refused,
    REB-847), that marks each field supported, unsupported or contradicted and flags
    what was invented. Prints per person and in total the supported fields, the
    contradictions, the guard failures, the identifiers found, the latency and the cost of
    the writing and of the judging apart, and how the run stands against a baseline:
    `--baseline RUN_ID`, or the newest finished run of the live configuration, or
    «nessun riferimento». `--prompt-version 1` writes with the CV-alone prompt of before
    the sources, for a baseline of it. A provider that is switched off is refused, «Il
    fornitore è spento», unless `--allow-disabled` says this evaluation may send CVs to
    it (the report says so). No live card and no task setting is touched; the calls are
    recorded under the `card` task with `purpose="eval"`, so they count against its daily
    cap, which can end the run early: it then says so, exits 1 and is never taken as a
    baseline. Nothing printed carries a card's text, a reason or a name."""
    from rebase_core.ai_config import PROVIDER_OFF
    from rebase_core.card_eval import format_report, named, results_of, run_eval, sample
    from rebase_core.card_judge import DEFAULT_JUDGES, JUDGE_MAX_OUTPUT_TOKENS
    from rebase_core.llm import AiCall, TaskOverride
    from rebase_core.models import AiProvider, AiTask

    if sample_size is not None and freelancers:
        print("--sample e --freelancer si escludono.", file=sys.stderr)
        return 2
    if (provider is None) != (model is None):
        print("--provider e --model vanno insieme.", file=sys.stderr)
        return 2
    if (judge_provider is None) != (judge_model is None):
        print("--judge-provider e --judge-model vanno insieme.", file=sys.stderr)
        return 2
    settings = get_settings()
    opener = open_session(settings)
    session = opener()
    try:
        live = session.get(AiTask, "card")
        if live is None:
            print("Nessun modello configurato per le schede: apri «Modelli AI».", file=sys.stderr)
            return 1
        if provider is None or model is None:
            provider, model = live.provider, live.model
            effort = effort if effort is not None else live.effort
        writer = TaskOverride(provider, model, effort, allow_disabled)
        if judge_provider is None or judge_model is None:
            judge_provider, judge_model = next(
                judge for judge in DEFAULT_JUDGES if judge != (provider, model)
            )
        if (judge_provider, judge_model) == (provider, model):
            print(
                f"Il giudice è lo stesso modello che scrive ({provider}/{model}): "
                "scegli un altro --judge-provider e --judge-model.",
                file=sys.stderr,
            )
            return 2
        judge_effort = None
        judging = TaskOverride(
            judge_provider, judge_model, judge_effort, allow_disabled, JUDGE_MAX_OUTPUT_TOKENS
        )
        for name in dict.fromkeys((provider, judge_provider)):
            account = session.get(AiProvider, name)
            if not allow_disabled and (account is None or not account.enabled):
                print(
                    f"{PROVIDER_OFF} ({name}): una valutazione manda i CV al fornitore, quindi "
                    "serve --allow-disabled.",
                    file=sys.stderr,
                )
                return 1

        def call_as(override: TaskOverride) -> Callable[[UUID], AiCall]:
            return lambda freelancer_id: AiCall(
                lambda: closing(opener()),
                settings,
                "card",
                override=override,
                purpose="eval",
                freelancer_id=freelancer_id,
            )

        targets = named(session, freelancers) if freelancers else sample(session, sample_size)
        run = run_eval(
            session,
            call_as(writer),
            call_as(judging),
            task="card",
            provider=provider,
            model=model,
            effort=effort,
            targets=targets,
            prompt_version=prompt_version,
            judge_by=(judge_provider, judge_model),
            baseline_run_id=baseline,
            allow_disabled=allow_disabled,
        )
        report = format_report(run, results_of(session, run.id))
        complete = run.summary["complete"]
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
    print(report)
    return 0 if complete else 1


def _limit_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=50)


def _regenerate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--batch", action="store_true", help="sempre col Batch API, qualunque sia la soglia"
    )
    # Not `required=True`: argparse would refuse in English, and the refusal is ours.
    parser.add_argument(
        "--admin",
        help="l'indirizzo dell'amministratore che avvia la rigenerazione (obbligatorio: la firma)",
    )


def _eval_arguments(parser: argparse.ArgumentParser) -> None:
    from rebase_core.card_prompt import PROMPT_VERSION, PROMPT_VERSIONS
    from rebase_core.models import AI_EFFORTS, AI_PROVIDERS

    parser.add_argument("--sample", type=int, help="le prime N persone del campione fisso")
    parser.add_argument("--freelancer", type=UUID, action="append", default=[])
    parser.add_argument("--provider", choices=AI_PROVIDERS)
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=AI_EFFORTS)
    parser.add_argument(
        "--prompt-version", type=int, choices=PROMPT_VERSIONS, default=PROMPT_VERSION
    )
    parser.add_argument("--judge-provider", choices=AI_PROVIDERS)
    parser.add_argument("--judge-model")
    parser.add_argument("--baseline", type=UUID)
    parser.add_argument("--allow-disabled", action="store_true")


def _eval_run(args: argparse.Namespace) -> int:
    return cards_eval(
        args.sample,
        args.freelancer,
        args.provider,
        args.model,
        args.effort,
        args.prompt_version,
        args.judge_provider,
        args.judge_model,
        args.baseline,
        args.allow_disabled,
    )


COMMANDS = {
    "cards-refresh": Command(_limit_argument, lambda args: cards_refresh(args.limit)),
    "cards-regenerate": Command(
        _regenerate_arguments, lambda args: cards_regenerate(args.limit, args.batch, args.admin)
    ),
    "cards-batch-poll": Command(no_arguments, lambda args: cards_batch_poll()),
    "sources-refetch": Command(_limit_argument, lambda args: sources_refetch(args.limit)),
    "cards-eval": Command(_eval_arguments, _eval_run),
}
