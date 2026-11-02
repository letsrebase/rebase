"""`rebase`: the operator's commands (`rebase_core.cli:main`, the `rebase` script).

One module per group of commands: `members` (tokens, roles, the welcome mail, duplicate
talents), `cards`, `campaigns`, `signing` (contracts and Documenso), `ai` (the model
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
        "Verifica la chiave della Conversions API senza registrare una conversione",
    ),
    "contracts-check": (
        "signing",
        "Compone i contratti con pandoc e Typst: questa macchina li sa generare?",
    ),
    "documenso-check": ("signing", "Documenso risponde, e il token di questo ambiente lo apre?"),
    "contracts-sweep": (
        "signing",
        "Rifà quanto un riavvio o una mail rifiutata hanno lasciato indietro",
    ),
    "campaigns-tick": (
        "campaigns",
        "Invia le campagne arrivate alla loro ora, una mail alla volta",
    ),
    "cards-refresh": (
        "cards",
        "Scrive la scheda anonima di chi ha un CV nuovo o cambiato, i più vecchi prima",
    ),
    "cards-regenerate": (
        "cards",
        "Riscrive le schede da aggiornare, in parallelo o, in blocco, col Batch API",
    ),
    "cards-batch-poll": (
        "cards",
        "Porta avanti il batch di schede inviato al Batch API e ne applica i risultati",
    ),
    "sources-refetch": (
        "cards",
        "Rilegge GitHub e i link da cui si scrive la scheda, senza chiamare un modello",
    ),
    "cards-eval": (
        "cards",
        "Scrive le schede di un campione e le confronta con le loro fonti, "
        "senza toccare le schede vere",
    ),
    "ai-seed": (
        "ai",
        "Sposta la chiave Anthropic e il modello vecchi in «Modelli AI», con i prezzi",
    ),
    "ai-prices-seed": (
        "ai",
        "Scrive i prezzi OpenAI e Google dalle loro pagine, senza toccare le righe esistenti",
    ),
    "ai-verify": (
        "ai",
        "Prova una chiamata con output strutturato nativo al fornitore, con la sua chiave",
    ),
    "ai-rotate-secrets": (
        "ai",
        "Ricifra le chiavi dei modelli: REBASE_SECRETS_KEY_OLD e REBASE_SECRETS_KEY_NEW",
    ),
    "talent-duplicates": (
        "members",
        "Elenca chi è in lista due volte (a secco) e con --apply unisce le schede",
    ),
    "createtoken": ("members", "Crea un token personale di un amministratore, per un agente"),
    "setrole": (
        "members",
        "Imposta il ruolo (member/admin) di un indirizzo, creandolo se serve",
    ),
    "welcome": (
        "members",
        "Manda la mail «la tua area è aperta» a un indirizzo o a tutti quelli noti",
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
