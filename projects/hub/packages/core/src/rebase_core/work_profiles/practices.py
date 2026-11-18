"""The practices block of a received work profile (REB-1144): its structure check.

`practices` is what the fork's `src/practices.mjs` emits: per practice the sessions that showed
it (`seen`), the sessions that could have (`of`), and the products and distinct months it showed
up in. The block is counts only: it says what a session shows, never how well the person works.

`issues` ports the practices part of `assessStructure` (`src/consistency.mjs`) that needs no logs,
against the list vendored in `practices.json` (see `VENDORED.txt`). An id that is not in that list
is no issue here: a practice the fork learns before the hub's copy is refreshed must not cost the
person the whole profile. `drop_unknown` removes such items before the payload is stored and only
counts them.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

from rebase_core.work_profiles import tech_usage
from rebase_core.work_profiles.tech_usage import is_count, is_month, is_timezone

PRACTICES_PATH = Path(__file__).with_name("practices.json")
RUBRIC = "rebase-practices/v1"
MAX_MODEL_SAMPLE = 60

PRACTICE_LABELS: dict[str, str] = {
    "checks_before_commit": "Verifica prima del commit",
    "tests_with_changes": "Test insieme al codice",
    "failure_reproduced_before_fix": "Riproduce il problema prima di correggerlo",
    "plan_before_edit": "Piano prima delle modifiche",
    "reviews_changes": "Rilettura delle modifiche prima del commit",
    "design_documents": "Scrive documenti di progetto",
    "security_checks": "Controlli di sicurezza",
    "asks_for_alternatives": "Chiede alternative",
    "pushes_back_with_reason": "Corregge l'agente motivando",
    "states_done_criteria": "Dice quando il lavoro è finito",
    "raises_risks": "Considera i rischi",
    "limits_scope": "Delimita il lavoro",
}

_KEYS = ("seen", "of", "products", "months")


@cache
def practice_methods() -> dict[str, str]:
    """Every practice of the rubric with the method that counts it (`code` or `model`), from the
    vendored list."""
    rows = json.loads(PRACTICES_PATH.read_text(encoding="utf-8"))["practices"]
    return {str(row["id"]): str(row["method"]) for row in rows}


def _window_months(payload: dict[str, Any]) -> int | None:
    window = payload.get("window")
    if not isinstance(window, dict):
        return None
    first, last = window.get("from"), window.get("to")
    if not is_month(first) or not is_month(last) or first > last:
        return None
    return tech_usage.month_span(first, last)


def _item_issues(
    item: dict[str, Any],
    method: str,
    volume: dict[str, Any],
    window_months: int | None,
) -> list[str]:
    where = f"practices.{item.get('id')}"
    if item.get("method") != method:
        return [f"{where}: il metodo è «{item.get('method')}» ma la lista lo conta con «{method}»"]
    if not all(is_count(item.get(key)) for key in _KEYS):
        return [f"{where}: seen, of, products e months sono numeri interi da zero in su"]
    seen, of, products, months = (item[key] for key in _KEYS)
    found: list[str] = []
    if seen > of:
        found.append(f"{where}: seen è {seen} ma solo {of} sessioni potevano mostrarlo (of)")
    total = volume.get("sessions")
    if is_count(total) and of > total:
        found.append(f"{where}: of è {of} sessioni ma il profilo ne ha {total}")
    if method == "model" and of > MAX_MODEL_SAMPLE:
        found.append(
            f"{where}: of è {of} ma una pratica letta dal modello guarda al massimo "
            f"{MAX_MODEL_SAMPLE} sessioni"
        )
    total = volume.get("products")
    if is_count(total) and products > total:
        found.append(f"{where}: {products} prodotti ma il profilo ne ha {total}")
    if products > seen:
        found.append(f"{where}: {products} prodotti ma visto in sole {seen} sessioni")
    if months > seen:
        found.append(f"{where}: {months} mesi ma visto in sole {seen} sessioni")
    if seen >= 1 and months < 1:
        found.append(f"{where}: visto in {seen} sessioni ma i mesi sono 0")
    # months are counted in the profile's zone, the window in UTC: an offset of at most 14 hours
    # shifts a month key by at most one.
    if window_months is not None and months > window_months + 1:
        found.append(f"{where}: {months} mesi ma la finestra del profilo ne copre {window_months}")
    return found


def issues(payload: dict[str, Any]) -> list[str]:
    """What is wrong with the `practices` block of `payload`, as sentences; nothing when the
    block is absent (it is optional) or sound. An item that is no object or has no id is refused;
    a well-formed item with an unknown id is skipped (see `drop_unknown`)."""
    block = payload.get("practices")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["practices: serve un oggetto"]
    found: list[str] = []
    if block.get("rubric") != RUBRIC:
        found.append(
            f"practices.rubric: è «{block.get('rubric')}» ma questo hub conta secondo «{RUBRIC}»"
        )
    zone = block.get("timezone")
    if not is_timezone(zone):
        found.append(f"practices.timezone: «{zone}» non è un fuso orario IANA")
    else:
        intensity = payload.get("intensity")
        other = intensity.get("timezone") if isinstance(intensity, dict) else None
        if other and other != zone:
            found.append(
                f"practices.timezone è {zone} ma intensity.timezone è {other}: "
                "una generazione usa un solo fuso"
            )
    items = block.get("items")
    if not isinstance(items, list):
        found.append("practices.items: serve una lista")
        return found
    volume = payload.get("volume")
    volume = volume if isinstance(volume, dict) else {}
    window_months = _window_months(payload)
    methods = practice_methods()
    listed: set[str] = set()
    for position, item in enumerate(items, start=1):
        ident = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item, dict) or not isinstance(ident, str) or not ident:
            found.append(
                f"practices.items[{position}]: non è una pratica (serve un oggetto con un id)"
            )
            continue
        if ident not in methods:
            continue
        if ident in listed:
            found.append(f"practices.{ident}: compare più di una volta")
        listed.add(ident)
        found.extend(_item_issues(item, methods[ident], volume, window_months))
    return found


def drop_unknown(payload: dict[str, Any]) -> int:
    """Remove from `payload["practices"]["items"]`, in place, every item whose id is not in the
    vendored list; how many were removed, 0 when the block is absent or malformed."""
    block = payload.get("practices")
    if not isinstance(block, dict):
        return 0
    items = block.get("items")
    if not isinstance(items, list):
        return 0
    methods = practice_methods()
    kept = [
        i
        for i in items
        if isinstance(i, dict) and isinstance(i.get("id"), str) and i["id"] in methods
    ]
    block["items"] = kept
    return len(items) - len(kept)


def reads_true(item: Any) -> bool:
    """Whether a stored item is one the intake would have accepted on its own points: a known id,
    the method the list gives it and counts that hold together. It rechecks the item alone, not
    the profile's totals or window, which the intake enforced when the profile was stored. The
    view applies it so a profile stored before a rule changed never shows a row the rules
    refuse."""
    if not isinstance(item, dict):
        return False
    ident = item.get("id")
    methods = practice_methods()
    if not isinstance(ident, str) or ident not in methods:
        return False
    return not _item_issues(item, methods[ident], {}, None)
