"""`rebase`: the operator's commands (`rebase_core.cli:main`, the `rebase` script).

One module per group of commands: `members` (tokens, roles, the welcome mail, duplicate
talents), `cards`, `campaigns`, `crm` (the sweep that files company requests), `ai` (the model
providers).
`main` knows only the names and the help of the commands, and imports a group's module when
one of its commands is chosen, which then imports what its own work needs inside its
handler (REB-892): `rebase campaigns-tick` does not load the model providers' libraries.
A command is a `Command` (`common.py`) in its module's `COMMANDS`.
"""

import argparse
import importlib
import sys
from collections.abc import Sequence

from rebase_core.cli.common import Command

# name -> (module, help). Every name here is in that module's `COMMANDS`.
REGISTRY: dict[str, tuple[str, str]] = {
    "conversions-check": (
        "campaigns",
        "Check the Conversions API key without recording a conversion",
    ),
    "crm-sweep": (
        "crm",
        "File the company requests the CRM does not have yet",
    ),
    "campaigns-tick": (
        "campaigns",
        "Send the campaigns that reached their time, one mail at a time",
    ),
    "cards-refresh": (
        "cards",
        "Write the anonymous card of whoever has a new or changed CV, oldest first",
    ),
    "cards-regenerate": (
        "cards",
        "Rewrite the cards to update, in parallel or, in bulk, with the Batch API",
    ),
    "cards-batch-poll": (
        "cards",
        "Advance the card batch sent to the Batch API and apply its results",
    ),
    "sources-refetch": (
        "cards",
        "Re-read GitHub and the links a card is written from, without calling a model",
    ),
    "cards-eval": (
        "cards",
        "Write the cards of a sample and compare them with their sources, "
        "without touching the live cards",
    ),
    "ai-seed": (
        "ai",
        "Move the old Anthropic key and model into «Modelli AI», with the prices",
    ),
    "ai-prices-seed": (
        "ai",
        "Write the OpenAI and Google prices from their pages, leaving existing rows alone",
    ),
    "ai-verify": (
        "ai",
        "Try a call with the provider's native structured output, with its own key",
    ),
    "ai-rotate-secrets": (
        "ai",
        "Re-seal the model keys: REBASE_SECRETS_KEY_OLD and REBASE_SECRETS_KEY_NEW",
    ),
    "talent-duplicates": (
        "members",
        "List who is on the list twice (dry run) and, with --apply, merge the cards",
    ),
    "createtoken": ("members", "Create an administrator's personal token, for an agent"),
    "setrole": (
        "members",
        "Set the role (member/admin) of an address, creating it if needed",
    ),
    "welcome": (
        "members",
        "Send the «la tua area è aperta» mail to one address or to every known one",
    ),
}


def _command(name: str) -> Command:
    module = importlib.import_module(f"rebase_core.cli.{REGISTRY[name][0]}")
    command: Command = module.COMMANDS[name]
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in REGISTRY:
        # No command, `--help` or a name nobody knows: the listing answers, or refuses.
        parser = argparse.ArgumentParser(prog="rebase")
        sub = parser.add_subparsers(dest="command", required=True)
        for name, (_, help_) in REGISTRY.items():
            sub.add_parser(name, help=help_)
        parser.parse_args(args)
        return 2
    name = args[0]
    own = argparse.ArgumentParser(prog=f"rebase {name}", description=REGISTRY[name][1])
    command = _command(name)
    command.add_arguments(own)
    return command.run(own.parse_args(args[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
