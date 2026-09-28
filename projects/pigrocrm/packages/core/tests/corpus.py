"""The §16 reference corpus: ten years of a five-person practice.

One scale, one generator. Until REB-580 there was a second, inflated scale of 50 000
rows per searched table for the plan assertions of criterion 3, which went with them: a
space's tables hold hundreds of rows, where Postgres picks a sequential scan because it
*is* the cheapest plan, so a plan asserted at fifty thousand measured nothing a user of
this product meets.

Rows are inserted with `session.execute(insert(Model), [dicts])` rather than through the
ORM: at the 50 000 rows per table the inflated scale once had, the unit-of-work overhead
was the difference between a test that runs and a test nobody runs, and the shape stays.
`flush()` is called, never `commit()` -- the caller's
transaction owns the lifetime, which is what lets `db_session` roll the whole corpus
back.

Determinism is by seed, not by luck. `random.Random(seed)` is instantiated locally and
never the module-level `random` functions, so a concurrent test that seeds the global
generator cannot change what this one produces.

A module rather than a fixture: three sub-plans and six test files need it, and a
function they call inside their own session is the shape that lets each choose its
scope.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.people.models import Person
from pigrocrm.core.pipeline.models import PipelineStage

# The one customer every search test looks for. Both values are deliberately ordinary:
# a VAT number whose middle five digits ("34567") are a realistic fragment to type, and
# a company name whose first four characters ("Ross") are a realistic prefix.
KNOWN_PARTITA_IVA = "01234567890"
KNOWN_RAGIONE_SOCIALE = "Rossi Ingegneria Srl"

_SURNAMES = (
    "Rossi",
    "Bianchi",
    "Ferrari",
    "Russo",
    "Esposito",
    "Colombo",
    "Ricci",
    "Marino",
    "Greco",
    "Bruno",
    "Gallo",
    "Conti",
    "De Luca",
    "Costa",
    "Giordano",
    "Mancini",
    "Rizzo",
    "Lombardi",
    "Moretti",
    "Barbieri",
)
_FIRST_NAMES = (
    "Marco",
    "Giulia",
    "Luca",
    "Chiara",
    "Andrea",
    "Sara",
    "Matteo",
    "Elena",
    "Francesco",
    "Alessia",
    "Davide",
    "Martina",
    "Simone",
    "Federica",
    "Alessandro",
    "Valentina",
)
_SECTORS = (
    "Ingegneria",
    "Consulenza",
    "Logistica",
    "Impianti",
    "Servizi",
    "Costruzioni",
    "Informatica",
    "Trasporti",
    "Manutenzioni",
    "Progettazione",
)
_LEGAL_FORMS = ("Srl", "Spa", "Snc", "Sas", "Srls")
_DEAL_WORDS = (
    "Rifacimento",
    "Ampliamento",
    "Adeguamento",
    "Collaudo",
    "Fornitura",
    "Revisione",
    "Migrazione",
    "Assistenza",
    "Ristrutturazione",
    "Certificazione",
)
_DOC_WORDS = ("Offerta", "Contratto", "Verbale", "Relazione", "Preventivo", "Capitolato")

# `customers.partita_iva` is String(11) and the known customer already owns
# "01234567890", so the generated ones start above it and can never collide with it
# however large `scale.customers` grows -- 10_000_000_000 + 50_000 is still 11 digits.
_GENERATED_PIVA_BASE = 10_000_000_000


@dataclass(frozen=True)
class CorpusScale:
    customers: int
    people: int
    deals: int
    documents: int
    invoices: int


REFERENCE = CorpusScale(customers=500, people=800, deals=2000, documents=1000, invoices=5000)

# Ten years of a register, whatever the scale. `(anno, numero)` is unique
# (`uq_invoices_anno_numero`), so the two cannot be derived from the same modulus: the
# brief's `anno = 2017 + index % 10` with `numero = index % 500 + 1` produces exactly 500
# distinct pairs for any scale -- 10 divides 500, so the year is a function of the number --
# and every row past the five-hundredth violates the constraint. Dividing for the year and
# taking the remainder for the number is what makes the pair unique by construction.
_REGISTER_YEARS = 10
_FIRST_REGISTER_YEAR = 2017


@dataclass(frozen=True)
class CorpusIds:
    stage_open_id: UUID
    stage_won_id: UUID
    stage_lost_id: UUID
    customer_ids: list[UUID] = field(default_factory=list)
    deal_ids: list[UUID] = field(default_factory=list)


def _stages(session: Session) -> tuple[UUID, UUID, UUID]:
    """Three stages, resolved by `code` and created only if absent.

    By `code`, never by `nome`: `PipelineStage`'s own docstring gives the reason, and a
    corpus that deduplicated on the renamable label would create a second "Vinto" the
    moment a test renamed the first one. The three codes are a subset of
    `pipeline/service.py::DEFAULT_STAGES`, so a corpus built after `seed_defaults` has
    run reuses those rows instead of tripping `uq_pipeline_stage_code`.
    """
    wanted = (
        ("offerta", "Offerta", 2, 50, "open"),
        ("vinto", "Vinto", 4, 100, "won"),
        ("perso", "Perso", 5, 0, "lost"),
    )
    ids: list[UUID] = []
    for code, nome, posizione, probabilita, tipo in wanted:
        existing = session.scalar(select(PipelineStage).where(PipelineStage.code == code))
        if existing is None:
            existing = PipelineStage(
                code=code,
                nome=nome,
                posizione=posizione,
                probabilita_default=probabilita,
                tipo=tipo,
            )
            session.add(existing)
            session.flush()
        ids.append(existing.id)
    return ids[0], ids[1], ids[2]


def build_corpus(session: Session, scale: CorpusScale, *, seed: int = 20260821) -> CorpusIds:
    rng = random.Random(seed)
    stage_open, stage_won, stage_lost = _stages(session)

    customer_ids: list[UUID] = []
    customer_rows: list[dict[str, object]] = []
    for index in range(scale.customers):
        cid = uuid7()
        customer_ids.append(cid)
        if index == 0:
            ragione, piva = KNOWN_RAGIONE_SOCIALE, KNOWN_PARTITA_IVA
        else:
            ragione = (
                f"{rng.choice(_SURNAMES)} {rng.choice(_SECTORS)} {rng.choice(_LEGAL_FORMS)} {index}"
            )
            piva = str(_GENERATED_PIVA_BASE + index)
        customer_rows.append(
            {
                "id": cid,
                "ragione_sociale": ragione[:255],
                "partita_iva": piva,
                # Exactly 16 characters, which is what `codice_fiscale` is sized for.
                "codice_fiscale": f"CF{index:014d}",
                "email": f"info{index}@{rng.choice(_SECTORS).lower()}.example",
                "nazione": "IT",
                "stato": rng.choice(("attivo", "prospect", None)),
                "custom_fields": {},
            }
        )
    session.execute(insert(Customer), customer_rows)

    person_rows: list[dict[str, object]] = []
    for index in range(scale.people):
        # Every fifth person has no surname: `people.cognome` is nullable, and Task A3's
        # NULLS LAST cursor has no exerciser without rows in the null tail.
        cognome = None if index % 5 == 0 else rng.choice(_SURNAMES)
        person_rows.append(
            {
                "id": uuid7(),
                "customer_id": customer_ids[index % len(customer_ids)],
                "nome": rng.choice(_FIRST_NAMES),
                "cognome": cognome,
                "email": f"persona{index}@example.it",
                "custom_fields": {},
            }
        )
    session.execute(insert(Person), person_rows)

    deal_ids: list[UUID] = []
    deal_rows: list[dict[str, object]] = []
    for index in range(scale.deals):
        did = uuid7()
        deal_ids.append(did)
        stage = (stage_open, stage_won, stage_lost)[index % 3]
        # Every seventh deal has no expected value. Task B9 counts these under
        # "senza valore" and never sums them as zero.
        #
        # `Decimal`, not the string the brief proposed: `deals.valore_previsto` is
        # Numeric(12, 2), and money is never a float anywhere in this codebase.
        valore = None if index % 7 == 0 else Decimal(f"{1000 + index % 90000}.00")
        deal_rows.append(
            {
                "id": did,
                "nome": f"{rng.choice(_DEAL_WORDS)} {rng.choice(_SECTORS)} {index}",
                "customer_id": customer_ids[index % len(customer_ids)],
                "pipeline_stage_id": stage,
                "valore_previsto": valore,
                "probabilita": (index % 11) * 10,
                "custom_fields": {},
            }
        )
    session.execute(insert(Deal), deal_rows)

    document_rows: list[dict[str, object]] = []
    for index in range(scale.documents):
        # `ck_documents_customer_xor_deal`: exactly one of the two, never both.
        owner_is_deal = index % 2 == 0
        document_rows.append(
            {
                "id": uuid7(),
                "customer_id": None if owner_is_deal else customer_ids[index % len(customer_ids)],
                "deal_id": deal_ids[index % len(deal_ids)] if owner_is_deal else None,
                "tipo": "offerta" if index % 3 == 0 else "documento",
                "titolo": f"{rng.choice(_DOC_WORDS)} {rng.choice(_SECTORS)} {index}",
                # `documents.stato` is meaningful only for `tipo = 'offerta'` and NULL
                # for every other type -- the model's own docstring says so.
                "stato": "inviata" if index % 3 == 0 else None,
                "versione_corrente": 1,
                "custom_fields": {},
            }
        )
    session.execute(insert(Document), document_rows)

    # Invoices, the fifth searched table (§8.1's fifth branch, Task C12). Every row is
    # `fattura`/`emessa` and therefore numbered: the search branch's equality path only
    # reaches numbered rows, and a corpus of drafts would leave it with nothing to find.
    #
    # The `CHECK` constraints on this table are unusually dense and each of these values is
    # chosen against one of them: `anno` and `numero` are set together
    # (`ck_invoices_anno_numero_together`), `numero` requires an issued `fattura`
    # (`ck_invoices_numero_requires_issued_fattura`), `riferimento` is proforma-only, and
    # `snapshot`/`annullata_il`/`data_incasso` are all left NULL because each pairs with
    # another column this corpus does not set.
    invoice_rows: list[dict[str, object]] = []
    per_year = -(-scale.invoices // _REGISTER_YEARS) or 1
    for index in range(scale.invoices):
        anno = _FIRST_REGISTER_YEAR + index // per_year
        invoice_rows.append(
            {
                "id": uuid7(),
                "customer_id": customer_ids[index % len(customer_ids)],
                # Every other invoice hangs off a deal. `deals.id` is a real FK, so the
                # value has to come from `deal_ids` and not from a fresh UUID.
                "deal_id": deal_ids[index % len(deal_ids)] if index % 2 == 0 else None,
                "tipo": "fattura",
                "stato": "emessa",
                "stato_pagamento": "da_incassare" if index % 3 else "incassato",
                "anno": anno,
                "numero": index % per_year + 1,
                "causale": f"{rng.choice(_DEAL_WORDS)} {rng.choice(_SECTORS)} {index}"[:200],
                # `Decimal`, not a string: `Numeric(12, 2)`, and money is never a float
                # anywhere in this codebase.
                "imponibile": Decimal("1000.00"),
                "imposta": Decimal("0.00"),
                "bollo": Decimal("0.00"),
                "totale": Decimal("1000.00"),
                "data_emissione": date(anno, index % 12 + 1, 15),
                "tipo_documento": "TD01",
                "divisa": "EUR",
                "custom_fields": {},
            }
        )
    session.execute(insert(Invoice), invoice_rows)

    session.flush()
    return CorpusIds(
        stage_open_id=stage_open,
        stage_won_id=stage_won,
        stage_lost_id=stage_lost,
        customer_ids=customer_ids,
        deal_ids=deal_ids,
    )
