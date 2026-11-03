"""The model providers' commands: the seeds, the verification and the key rotation."""

import argparse
import base64
import binascii
import os
import sys
from decimal import Decimal, InvalidOperation

from rebase_core.cli.common import Command, no_arguments, open_session
from rebase_core.config import get_settings
from rebase_core.errors import DomainError
from rebase_core.secrets import KEY_BYTES


def ai_seed(eur_per_usd: str) -> int:
    """`rebase ai-seed --eur-per-usd 0.92`: once, moves `REBASE_ANTHROPIC_API_KEY` and
    `REBASE_TEAM_BUILDER_MODEL` (read from the environment: the Settings fields are gone,
    REB-831) into «Modelli AI»: the key sealed, `anthropic` on, both tasks on that model,
    and the prices of the Claude models in euros at `--eur-per-usd` (EUR per 1 USD, the
    inverse of the ECB's quote). Prints the hint of the key, never the key (REB-832)."""
    from rebase_core.ai_config import AiConfigService

    rate = _parse_rate(eur_per_usd)
    if rate is None:
        return 2
    api_key = os.environ.get("REBASE_ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("REBASE_ANTHROPIC_API_KEY manca: non c'è niente da spostare.", file=sys.stderr)
        return 1
    model = os.environ.get("REBASE_TEAM_BUILDER_MODEL", "").strip() or "claude-opus-5"
    settings = get_settings()
    session = open_session(settings)()
    try:
        result = AiConfigService(session, settings).seed_from_env(None, api_key, model, rate)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
    print(f"chiave salvata, finisce con …{result.key_hint[-4:]}")
    print(f"modello {result.model} per le schede e per il team, {result.prices} prezzi scritti")
    return 0


def _parse_rate(eur_per_usd: str) -> Decimal | None:
    try:
        return Decimal(eur_per_usd)
    except InvalidOperation:
        print("--eur-per-usd deve essere un numero, per esempio 0.92.", file=sys.stderr)
        return None


def ai_prices_seed(eur_per_usd: str) -> int:
    """`rebase ai-prices-seed --eur-per-usd 0.92`: the reviewed OpenAI and Google price table
    (USD per million tokens from their pricing pages, `SEED_USD_PRICES`) in euros at that
    rate. A price row that exists, an admin's edit included, is left as it is. Turns no
    provider on: that is «Modelli AI» (REB-840)."""
    from rebase_core.ai_config import AiConfigService

    rate = _parse_rate(eur_per_usd)
    if rate is None:
        return 2
    settings = get_settings()
    session = open_session(settings)()
    try:
        result = AiConfigService(session, settings).seed_prices(rate)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
    print(f"{result.written} prezzi scritti, {result.kept} già presenti e lasciati com'erano")
    return 0


def ai_verify(provider: str, model: str) -> int:
    """`rebase ai-verify --provider openai|google --model M`: one call with a fixed prompt
    that carries no personal data, to the key saved in «Modelli AI», and what the provider
    did with the seam's native structured output. Exit 0 only when the key was injected, the
    output came back in the schema and the effort setting was taken; it works with the
    provider still switched off (REB-840). Never prints the key."""
    from rebase_core.ai_config import AiConfigService
    from rebase_core.ai_verify import verify

    settings = get_settings()
    session = open_session(settings)()
    try:
        key = AiConfigService(session, settings).resolve_key(provider)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
    if key is None:
        print(f"Manca la chiave di {provider}: salvala in «Modelli AI» e riprova.", file=sys.stderr)
        return 1
    report = verify(provider, model, key)
    print("\n".join(report.lines()))
    return 0 if report.ok else 1


def _key_from_argument(value: str) -> bytes | None:
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    return key if len(key) == KEY_BYTES else None


def ai_rotate_secrets() -> int:
    """`rebase ai-rotate-secrets`: re-seals every stored provider key from the old
    `REBASE_SECRETS_KEY` to the new one, in one transaction, and prints how many. Both
    values come from the environment (`REBASE_SECRETS_KEY_OLD`, `REBASE_SECRETS_KEY_NEW`),
    never from the command line, where `ps`, the shell history and `docker exec` would show
    what opens every stored key. Put the new value in `.env` afterwards (REB-832)."""
    from rebase_core.ai_config import AiConfigService

    old = _key_from_argument(os.environ.get("REBASE_SECRETS_KEY_OLD", ""))
    new = _key_from_argument(os.environ.get("REBASE_SECRETS_KEY_NEW", ""))
    if old is None or new is None:
        print(
            "REBASE_SECRETS_KEY_OLD e REBASE_SECRETS_KEY_NEW devono essere 32 byte in base64.",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()
    session = open_session(settings)()
    try:
        count = AiConfigService(session, settings).rotate(old, new)
    except DomainError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    finally:
        session.close()
    print(f"{count} chiavi ricifrate")
    return 0


def _rate_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--eur-per-usd", required=True)


def _verify_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", required=True, choices=["openai", "google"])
    parser.add_argument("--model", required=True)


COMMANDS = {
    "ai-seed": Command(_rate_argument, lambda args: ai_seed(args.eur_per_usd)),
    "ai-prices-seed": Command(_rate_argument, lambda args: ai_prices_seed(args.eur_per_usd)),
    "ai-verify": Command(_verify_arguments, lambda args: ai_verify(args.provider, args.model)),
    "ai-rotate-secrets": Command(no_arguments, lambda args: ai_rotate_secrets()),
}
