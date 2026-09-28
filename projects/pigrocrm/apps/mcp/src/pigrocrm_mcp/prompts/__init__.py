"""Registration for §10's four prompts.

`MCPServer.prompt()` infers the arguments from the signature exactly as `tool()` does --
verified against the installed `mcp==2.2.0` (`prompts/base.py::Prompt.from_function` runs
the same `func_metadata` the tool manager runs), not assumed from the documentation -- and
the function may receive the `Context`, so a prompt can read the database like a tool. None
of these four needs one.

Two consequences of that shared machinery are load-bearing here:

  * `functools.wraps` inside the guard's one body (`_guarded` in `server.py`) is what makes
    the inferred schema the *guarded* function's, not the wrapper's bare
    `(*args, **kwargs)`. It is already there for the tools and `_prompt_guard` shares that
    body, so the prompts inherit it -- and `test_mcp_prompts.py` pins the inferred
    arguments so a future guard that lost `wraps` breaks here as loudly as it would there;
  * `Prompt.render` re-raises `MCPError` untouched and replaces everything else with
    `ValueError(f"Error rendering prompt {name}")`, with no text after the name since mcp
    2.2 (REB-451). That is why the guard passed here is `_prompt_guard` and not the tools'
    `_guard`: it raises a domain error as an `MCPError` whose message is
    `to_agent_message`'s diagnosis, so the assistant reads the domain's own sentence, and a
    crash keeps its text on the server. It is also why these prompts raise domain errors and
    do not render an apology in Italian: the guard already turns one into guidance, and a
    second rendering would be a second vocabulary for the same failure.
"""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.prompts import customer as customer_prompts
from pigrocrm_mcp.prompts import dashboards as dashboard_prompts


def register_prompts(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    """`guard` is typed `Callable[..., Any]`, exactly as `register_entity_tools` types it.

    A single `[T: Callable[..., Any]]` on this function would bind one `T` for all four
    registrations, so the second signature it met would be an error -- and the honest
    alternative, a `Protocol` with a generic `__call__`, would describe `_guard` no better
    than this does while adding a type nobody else in this package needs.
    """

    @mcp.prompt(name="revisione-pipeline")
    @guard
    def revisione_pipeline(da: str | None = None, a: str | None = None) -> list[dict[str, Any]]:
        """La revisione settimanale della pipeline commerciale, con i deal fermi e le
        offerte in attesa. `da` e `a` sono date ISO opzionali: senza, il mese in corso."""
        return dashboard_prompts.revisione_pipeline(context, da, a)

    @mcp.prompt(name="chiusura-mese")
    @guard
    def chiusura_mese(anno: int, mese: int) -> list[dict[str, Any]]:
        """La lista di cose da fare prima di chiudere un mese: conto economico del periodo,
        ore non fatturate, fatture scadute, e se il periodo è già chiuso."""
        return dashboard_prompts.chiusura_mese(context, anno, mese)

    @mcp.prompt(name="ore-da-registrare")
    @guard
    def ore_da_registrare(settimana: str | None = None) -> list[dict[str, Any]]:
        """I giorni della settimana corrente senza nessuna ora registrata, con i deal su
        cui si è lavorato di recente."""
        return dashboard_prompts.ore_da_registrare(context, settimana)

    @mcp.prompt(name="stato-cliente")
    @guard
    def stato_cliente(customer_id: str) -> list[dict[str, Any]]:
        """Il briefing prima di una telefonata: fatture non incassate, e la scheda completa
        del cliente allegata come risorsa."""
        return customer_prompts.stato_cliente(context, customer_id)
