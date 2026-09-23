"""Every setting an installation is meant to choose reaches the container.

This file exists because of a hole that was real for a few hours on 2026-09-09. Four
settings were added for the Orbiters signup conversion (since moved to `projects/hub`),
documented in `.env.example`, tested in core, wired into the API -- and not listed in
`docker-compose.yml`. Compose forwards only the variables a service names, so in
production the API would have seen a bare `Settings()` and no conversion would ever have
been sent. Silently: nothing in this codebase logs, so the only visible symptom would
have been a campaign with no conversions, weeks later, with the `.env` on the server
looking correct.

So the check is mechanical, and the exemptions are a list with reasons. A new field on
`Settings` fails this file until somebody either forwards it or writes down why it is
not an installation's choice. That is the same discipline
`apps/mcp/tests/test_mcp_surface_coverage.py` applies to the agent surface, for the same
reason: the decision is forced while it is still cheap.
"""

import re
from pathlib import Path

import pytest

from pigrocrm.core.config import Settings

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"
PREFIX = "PIGROCRM_"

# Fields that are deliberately **not** in the shared `x-api-environment` anchor block
# (docker-compose.yml, consumed by both the `api` and `mcp` services), each with the
# reason. Two kinds only: a value the compose file computes itself, and a value that must
# not be configurable in a container.
NOT_FORWARDED: dict[str, str] = {
    # The one variable whose absence is the security control, with the whole argument
    # written in docker-compose.yml: `.env` carries `false` for the local flow, and
    # forwarding it would serve cookies without `Secure` on any deploy whose `.env`
    # descends from `.env.example`.
    "cookie_secure": "assente di proposito: il default compilato (`True`) e' la garanzia",
    # Timings and ceilings. Real settings, and nobody has ever needed to move them on a
    # deploy: they are here so that a bare `Settings()` in a container is a considered
    # answer rather than an accident. Forwarding them costs nothing but a line each, and
    # the day one of them needs moving, this list is where the decision is recorded.
    "access_token_minutes": "durata del token: il default vale per ogni installazione",
    "magic_link_minutes": "durata del link via mail: come sopra",
    "gmail_backfill_days": "quanto indietro guarda il primo sync: default",
    "gmail_watermark_overlap_hours": "sovrapposizione del watermark: default",
    "gmail_body_max_bytes": "quanto corpo si conserva: default",
    "gmail_attachment_max_bytes": "peso massimo di un allegato: default",
    "gmail_send_grace_minutes": "finestra di annullo di un invio: default",
    "gmail_reconcile_by_message_id": "strategia di riconciliazione: default, vedi la nota",
    "drive_text_max_bytes": "quanto testo torna da un file Drive: default",
    "document_text_max_bytes": "quanto testo torna da un documento archiviato: default",
    "storage_local_root": "percorso dentro l'immagine, montato dal volume",
    "tenants_database_url": "vuoto significa lo stesso server del CRM, che e' cio' che serve",
    "tenants_alembic_ini": "vuoto risolve all'alembic.ini che l'immagine contiene",
    "gmail_sync_address_batch_size": "quante query per ciclo: default",
    # Paths inside the image. The Dockerfile installs both under these names, so a
    # deployment able to change them could only break rendering.
    "pandoc_binary": "il nome con cui l'immagine installa Pandoc",
    "typst_binary": "il nome con cui l'immagine installa Typst",
    # The three numbers behind a payment reminder. Deliberately not a deploy-time knob:
    # they decide whether chasing somebody is legitimate and finite, they already carry
    # `ge`/`le` in `config.py` for that reason, and an operator who wants a different
    # grace period is asking for a product decision rather than an environment variable.
    "solleciti_grace_days": "quando un sollecito e' legittimo: non una scelta di deploy",
    "solleciti_min_interval_days": "come sopra",
    "solleciti_max_reminders": "come sopra, e il tetto e' il numero di registri del template",
    # The preferred-share concentration threshold (REB-371): a practitioner's own
    # preference about their client mix, not a deployment choice, exactly the same
    # reasoning as the three `solleciti_*` thresholds just above -- and, like them,
    # reachable per space through `space_settings` instead.
    "concentrazione_soglia_preferita": "quota preferita di un cliente: scelta del praticante",
}


# `PIGROCRM_X: ${PIGROCRM_X:-default}`, indented under the shared anchor block (2 spaces,
# `x-api-environment:` itself sitting at column 0) rather than under a service directly.
_LINE = re.compile(r"^\s+(PIGROCRM_[A-Z0-9_]+):\s*(\S.*?)\s*$", re.MULTILINE)
# Two variables that have been forwarded since the first deploy and are not going
# anywhere. They are the canary for the parser below: if a compose change makes this
# file read nothing, these two go missing and every test here fails loudly instead of
# passing over an empty set.
_CANARIES = frozenset({f"{PREFIX}DATABASE_URL", f"{PREFIX}JWT_SECRET"})


def _shared_environment() -> dict[str, str]:
    """The `PIGROCRM_*` lines of the shared `x-api-environment` anchor block.

    Read with a regex rather than a YAML parser, and the reason is worth the paragraph:
    PyYAML is not a declared dependency of this repository. It happens to be installed
    today, transitively, so `import yaml` would work -- until the day it does not, and
    then a suite that checks the deployment would fail for a reason that has nothing to
    do with the deployment. What is being parsed is a flat block of `KEY: value` lines,
    which a regex reads exactly; `_CANARIES` is what keeps that claim honest.

    Both `api` and `mcp` resolve their `environment:` to this one block (`environment: *
    api-environment`), so reading the anchor once is reading what either service actually
    gets; `test_both_python_services_share_the_one_environment_block` is what keeps that
    claim honest too.
    """
    return {match.group(1): match.group(2) for match in _LINE.finditer(_shared_environment_block())}


def _shared_environment_block() -> str:
    """The anchor's text, header line included, up to the next top-level key."""
    text = COMPOSE.read_text()
    anchor = text[text.index("\nx-api-environment:") + 1 :]
    # Up to the next top-level key (`services:`), so `services:` and everything under it
    # cannot be mistaken for the anchor's own content. Searched *past* the anchor's own
    # header line, which the same pattern would otherwise match at offset zero.
    after_header = anchor.index("\n") + 1
    end = re.search(r"^[a-z][a-z0-9_-]*:", anchor[after_header:], re.MULTILINE)
    return anchor[: after_header + end.start()] if end else anchor


def _service_block(name: str) -> str:
    """The named service's own YAML block, sliced the same way the anchor block above
    used to be sliced when it lived inside `api`: from `\\n  <name>:` up to the next
    service at the same indentation."""
    text = COMPOSE.read_text()
    service = text[text.index(f"\n  {name}:") + 1 :]
    after_header = service.index("\n") + 1
    end = re.search(r"^  [a-z][a-z0-9_-]*:", service[after_header:], re.MULTILINE)
    return service[: after_header + end.start()] if end else service


def _forwarded() -> set[str]:
    return set(_shared_environment())


def test_the_environment_block_is_really_being_read() -> None:
    """If this fails, every other test in this file is asserting nothing."""
    assert COMPOSE.is_file(), COMPOSE
    assert _forwarded() >= _CANARIES, sorted(_forwarded())


def test_the_anchor_block_is_flat_yaml() -> None:
    """Every line of the anchor sits at exactly two spaces, comments included.

    `_LINE` accepts any indentation, so a variable pasted at six spaces (a conflict
    resolved by hand from a service's own `environment:` block, 2026-09-14, ORB-186)
    passed every test here while `docker compose build` refused the file with «yaml:
    line 12: did not find expected key» on the trunk. No PyYAML in this environment,
    on purpose (see `_shared_environment`), so the check is the one YAML rule a mapping
    of scalars has: one indentation, and each line a `key: value` or a comment.
    """
    text = COMPOSE.read_text()
    first_line = text[: text.index("\nx-api-environment:")].count("\n") + 3
    for number, line in enumerate(_shared_environment_block().splitlines()[1:], start=first_line):
        if not line.strip():
            continue
        # `number` is the file's own line, the one compose's error names.
        assert line.startswith("  ") and not line.startswith("   "), (number, line)
        body = line[2:]
        assert body.startswith("#") or re.match(r"^[A-Z0-9_]+: \S", body), (number, line)


@pytest.mark.parametrize("field", sorted(Settings.model_fields))
def test_every_setting_is_forwarded_or_declared_not_to_be(field: str) -> None:
    variable = f"{PREFIX}{field.upper()}"
    assert variable in _forwarded() or field in NOT_FORWARDED, (
        f"`{field}` e' una nuova impostazione e il container non la vede: aggiungi "
        f"`{variable}: ${{{variable}:-<default>}}` al blocco condiviso `x-api-environment` "
        "in docker-compose.yml, oppure una riga in NOT_FORWARDED che dice perche' no. "
        "Documentarla solo in .env.example non basta: compose inoltra soltanto quello "
        "che un servizio nomina, e il sintomo di una variabile dimenticata e' una "
        "funzionalita' che non parte senza dirlo a nessuno."
    )


@pytest.mark.parametrize("field", sorted(NOT_FORWARDED))
def test_every_exemption_still_names_a_real_setting(field: str) -> None:
    """The other direction: a renamed or deleted field must not leave a reason behind
    explaining why something that no longer exists is not forwarded."""
    assert field in Settings.model_fields, f"`{field}` non e' piu' un campo di Settings"


@pytest.mark.parametrize("field", sorted(NOT_FORWARDED))
def test_no_exemption_is_actually_forwarded(field: str) -> None:
    """An exemption that is forwarded anyway is a false statement, and the dangerous one
    is `cookie_secure`: it reads as a considered absence while the hole is open."""
    assert f"{PREFIX}{field.upper()}" not in _forwarded(), (
        f"`{field}` e' dichiarato non inoltrato ma compose lo inoltra davvero"
    )


def test_every_forwarded_variable_is_a_setting() -> None:
    """And nothing is forwarded that no longer exists: a `PIGROCRM_*` line for a field
    that was renamed is a line that quietly stopped doing anything."""
    known = {f"{PREFIX}{field.upper()}" for field in Settings.model_fields}
    assert _forwarded() <= known, sorted(_forwarded() - known)


def test_a_secret_is_required_and_never_defaulted() -> None:
    """`JWT_SECRET` uses compose's `:?` form, so a deploy without one fails to start
    instead of running on a value somebody can guess."""
    assert "?" in str(_shared_environment()[f"{PREFIX}JWT_SECRET"])


def test_refresh_token_days_is_forwarded_with_its_documented_default() -> None:
    """REB-257: `.env.example` already documented this as a per-installation choice
    (the sliding six-month session), while NOT_FORWARDED exempted it as if it were a
    ceiling nobody moves, so no `.env` could ever change it. Pinned here, with the
    same default `Settings` compiles in, so it cannot quietly fall back into
    NOT_FORWARDED without this failing."""
    variable = f"{PREFIX}REFRESH_TOKEN_DAYS"
    default = Settings.model_fields["refresh_token_days"].default
    assert _shared_environment()[variable] == f"${{{variable}:-{default}}}"


def test_both_python_services_share_the_one_environment_block() -> None:
    """One block, two consumers: `api` and `mcp` must both resolve `environment:` to
    the same anchor, so the forwarded set checked above is checked once and holds
    for both containers."""
    for name in ("api", "mcp"):
        assert "environment: *api-environment" in _service_block(name), name
