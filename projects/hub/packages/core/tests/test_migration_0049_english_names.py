"""0049 (REB-902): every Italian column, closed value, JSON key, constraint and index name
becomes English, and the downgrade gives all of them back, with the data."""

import json
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from rebase_core.migrate import INI_PATH

REVISION = "0049"
PARENT = "0048"

CARD = {
    "ruolo": "Dev",
    "anni": 5,
    "seniority": "senior",
    "competenze": ["py"],
    "settori": ["it"],
    "lingue": ["it"],
    "luogo": None,
    "sintesi": "x",
}
CARD_AFTER = {
    "role": "Dev",
    "experience_years": 5,
    "seniority": "senior",
    "skills": ["py"],
    "industries": ["it"],
    "languages": ["it"],
    "location": None,
    "profile_summary": "x",
}
CV = {
    "lingua": "it",
    "titolo": "T",
    "sommario": "S",
    "esperienze": [
        {
            "ruolo": "R",
            "azienda": "A",
            "luogo": "L",
            "periodo": "P",
            "punti": [{"titolo": "t", "testo": "x"}],
        }
    ],
    "competenze": [{"area": "a", "voci": ["v"]}],
    "formazione": [{"titolo": "t", "istituto": "i", "luogo": "l", "periodo": "p"}],
    "certificazioni": ["c"],
    "lingue": [{"lingua": "it", "livello": "C1"}],
    "altre_sezioni": [{"titolo": "t", "voci": ["v"]}],
}
CV_AFTER = {
    "language": "it",
    "title": "T",
    "summary": "S",
    "experience": [
        {
            "role": "R",
            "company": "A",
            "location": "L",
            "period": "P",
            "points": [{"title": "t", "text": "x"}],
        }
    ],
    "skills": [{"area": "a", "entries": ["v"]}],
    "education": [{"title": "t", "institution": "i", "location": "l", "period": "p"}],
    "certifications": ["c"],
    "languages": [{"language": "it", "level": "C1"}],
    "other_sections": [{"title": "t", "entries": ["v"]}],
}


def _config(url: str) -> Config:
    config = Config(str(INI_PATH))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _insert(connection: Any, table: str, **values: Any) -> None:
    columns = ", ".join(values)
    marks = ", ".join(
        f"CAST(:{name} AS jsonb)" if isinstance(value, (dict, list)) else f":{name}"
        for name, value in values.items()
    )
    connection.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({marks})"),
        {
            name: json.dumps(value) if isinstance(value, (dict, list)) else value
            for name, value in values.items()
        },
    )


def _one(connection: Any, sql: str) -> Any:
    return connection.execute(text(sql)).scalar_one()


def _seed(engine: Engine) -> dict[str, Any]:
    """One row per table the revision touches, in the shape the old code wrote."""
    ids = {name: uuid4() for name in ("admin", "person", "freelancer", "company", "match")}
    with engine.begin() as c:
        _insert(
            c,
            "users",
            id=ids["admin"],
            email="admin@x.it",
            nome="Ada",
            cognome="L",
            role="admin",
            attivo=True,
            telefono="1",
        )
        _insert(
            c,
            "users",
            id=ids["person"],
            email="p@x.it",
            nome="Pia",
            cognome="R",
            role="member",
            attivo=True,
        )
        _insert(
            c,
            "freelancers",
            id=ids["freelancer"],
            user_id=ids["person"],
            stato="contattato",
            compilata_da="persona",
            remoto="ibrido",
            posizione="Dev",
            anni_admin=3,
            tariffa_giornaliera=500,
            links=[],
        )
        _insert(
            c,
            "freelancer_cards",
            freelancer_id=ids["freelancer"],
            card=CARD,
            fonti={"ruolo": ["cv"], "anni": ["cv", "github"]},
            conflicts=[{"kind": "anni_diversi", "anni_scheda": 3, "anni_hub": 5}],
            persona={
                "nome": "Pia",
                "cognome": "R",
                "telefono": "1",
                "citta": "Roma",
                "altri_link": ["https://a"],
                "aziende": ["Acme"],
            },
            source_hashes={"cv": "h"},
        )
        _insert(
            c,
            "anonymous_cvs",
            freelancer_id=ids["freelancer"],
            body=CV,
            cv_sha256="a" * 64,
            prompt_version=1,
            model="m",
            generated_at="2026-01-01T00:00:00Z",
            provider="anthropic",
        )
        _insert(
            c,
            "companies",
            id=ids["company"],
            user_id=ids["person"],
            nome_azienda="Acme",
            progetto="p",
            periodo_da="2026-01-01",
            durata="3m",
            budget_giornaliero=500,
            numero_risorse=2,
            figura_richiesta="Dev",
            stato="in_corso",
            remoto="ibrido",
            giorni_presenza=2,
            crm_stato="collegato",
            origine="home",
            crm_bozza={
                "ruolo": "Dev",
                "giorni_settimana": 3,
                "durata_settimane": 12,
                "competenze": ["py"],
                "sintesi": "s",
            },
        )
        _insert(
            c,
            "matches",
            id=ids["match"],
            freelancer_id=ids["freelancer"],
            company_id=ids["company"],
            cliente_ragione_sociale="C",
            cliente_piva="1",
            cliente_sede="S",
            stato="in_firma",
            pigro_stato="da_collegare",
            created_by=ids["admin"],
            giorni_previsti=10,
        )
        _insert(
            c,
            "contract_documents",
            id=uuid4(),
            kind="quadro",
            stato="disdetto",
            regime="partita_iva",
            freelancer_id=ids["freelancer"],
            created_by=ids["admin"],
            text_version="1",
            testo_bozza=False,
            pdf=b"x",
            data={"professionista-nome": "A"},
        )
        _insert(
            c,
            "contract_documents",
            id=uuid4(),
            kind="lettera",
            stato="annullato",
            regime="occasionale",
            freelancer_id=ids["freelancer"],
            match_id=ids["match"],
            numero="2026-001",
            created_by=ids["admin"],
            text_version="1",
            testo_bozza=False,
            pdf=b"x",
            data={},
        )
        _insert(
            c,
            "notes",
            id=uuid4(),
            entity_type="freelancer",
            entity_id=ids["freelancer"],
            testo="t",
            autore="A",
            fonte="persona",
        )
        _insert(
            c,
            "referrals",
            id=uuid4(),
            referrer_user_id=ids["admin"],
            kind="freelancer",
            entity_id=ids["freelancer"],
            code="C1",
            stato="verificato",
            verified_at="2026-01-01T00:00:00Z",
            verified_via="accesso",
        )
        campaign = uuid4()
        _insert(
            c,
            "campaigns",
            id=campaign,
            created_by=ids["admin"],
            nome="C",
            slug="c-1",
            fonte="filtri",
            oggetto="o",
            testo="t {nome}",
            bottone_testo="b",
            bottone_meta="richiesta",
            azione="scheda_completa",
            stato="programmata",
            contenuto_at="2026-01-01T00:00:00Z",
            filtri={
                "lista": "talenti",
                "stato": "nuovo",
                "remoto": "ibrido",
                "posizione": "dev",
                "tariffa_min": "100",
                "origine": "home",
                "con_accessi": True,
                "creato_da": "2026-01-01T00:00:00Z",
            },
        )
        _insert(
            c,
            "campaigns",
            id=uuid4(),
            created_by=ids["admin"],
            nome="S",
            slug="c-2",
            fonte="stato",
            stato_percorso="card_empty_new",
            oggetto="o",
            testo="t",
            bottone_testo="b",
            bottone_meta="area",
            azione="entrato",
            stato="bozza",
            contenuto_at="2026-01-01T00:00:00Z",
        )
        _insert(
            c,
            "campaign_recipients",
            id=uuid4(),
            campaign_id=campaign,
            email="r@x.it",
            nome="N",
            tipo="azienda",
            stato="in_coda",
            codice="cd",
            tentativi=0,
            disiscrizione_token="tk",
            prima={
                "t": "2026-01-01T00:00:00+00:00",
                "ha_scheda": True,
                "ha_cv": False,
                "completa": False,
                "richieste": {"abc": "2026-01-01"},
            },
            pigro_slugs=[],
        )
        _insert(c, "campaign_optouts", email="o@x.it", fonte="reclamo")
        _insert(
            c,
            "team_proposals",
            id=uuid4(),
            descrizione="d",
            riassunto="r",
            luogo={"locale": True, "dove": "Roma"},
            team=[
                {
                    "posizione": 1,
                    "freelancer_id": str(ids["freelancer"]),
                    "ruolo": "Dev",
                    "motivazione": "m",
                    "giorni_settimana": 3,
                }
            ],
            economia={
                "giorno": {"min": 1, "max": 2},
                "mese": {"min": 1, "max": None},
                "giorni_mese": 20,
            },
            model="m",
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            origine="pubblico",
            persone=3,
        )
        request = uuid4()
        _insert(
            c,
            "team_requests",
            id=request,
            origine="pubblico",
            azienda="A",
            email="a@x.it",
            stato="nuova",
            telefono="1",
            rif="RR",
        )
        _insert(
            c,
            "team_request_talents",
            id=uuid4(),
            request_id=request,
            freelancer_id=ids["freelancer"],
            ruolo="Dev",
            risposta="si",
        )
        _insert(
            c,
            "ai_usage",
            id=uuid4(),
            task="card",
            purpose="prova",
            priced=False,
            provider="anthropic",
            model="m",
        )
        run = uuid4()
        _insert(
            c,
            "card_eval_runs",
            id=run,
            task="card",
            provider="anthropic",
            model="m",
            prompt_version=1,
            summary={"anni_ok": 1, "anni_checked": 2, "anni_ok_rate": 0.5},
        )
        _insert(
            c,
            "card_eval_results",
            run_id=run,
            freelancer_id=ids["freelancer"],
            label="t",
            card=CARD,
            checks={"anni": {"ok": True}},
            judgement={"ruolo": {"verdict": "ok"}},
            anni_ok=True,
        )
        _insert(c, "signups", id=uuid4(), email="s@x.it", nome="Ada", cognome="L", rif="ABC")
        for kind, payload in (
            (
                "overridden",
                {
                    "changed": ["posizione", "stato"],
                    "before": {"posizione": "a", "stato": "nuovo"},
                    "after": {"posizione": "b", "stato": "attivo"},
                },
            ),
            ("note_added", {"nota_id": "x", "fonte": "web", "testo": "t"}),
            ("card_regenerated", {"esito": "scritta", "ricostruita": True, "source": "cli"}),
            ("documents_sent", {"documento": "a", "kind": "quadro", "mail": True}),
            ("merged", {"lasciati": {"x": 1}, "scheda_da_rigenerare": True, "cv": "kept"}),
        ):
            _insert(
                c,
                "admin_actions",
                id=uuid4(),
                entity_type="freelancer",
                entity_id=ids["freelancer"],
                kind=kind,
                admin_id=ids["admin"],
                payload=payload,
            )
    return ids


def test_revision_follows_the_magic_link_door() -> None:
    parent = ScriptDirectory.from_config(Config(str(INI_PATH))).get_revision(REVISION).down_revision
    assert parent == PARENT


def test_the_upgrade_renames_the_columns_the_values_and_the_json_documents(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        command.upgrade(_config(url), PARENT)
        engine = create_engine(url, future=True)
        ids = _seed(engine)
        command.upgrade(_config(url), REVISION)
        with engine.connect() as c:
            assert (
                _one(c, "SELECT first_name || ' ' || last_name FROM users WHERE phone = '1'")
                == "Ada L"
            )
            assert _one(
                c, "SELECT status || filled_by || remote || job_title FROM freelancers"
            ) == ("contactedpersonhybridDev")
            assert _one(c, "SELECT card FROM freelancer_cards") == CARD_AFTER
            assert _one(c, "SELECT sources FROM freelancer_cards") == {
                "role": ["cv"],
                "experience_years": ["cv", "github"],
            }
            assert _one(c, "SELECT conflicts FROM freelancer_cards") == [
                {
                    "kind": "different_experience_years",
                    "experience_years_card": 3,
                    "experience_years_hub": 5,
                }
            ]
            assert _one(c, "SELECT person FROM freelancer_cards") == {
                "first_name": "Pia",
                "last_name": "R",
                "phone": "1",
                "city": "Roma",
                "other_links": ["https://a"],
                "companies": ["Acme"],
            }
            assert _one(c, "SELECT body FROM anonymous_cvs") == CV_AFTER
            assert _one(c, "SELECT crm_draft FROM companies") == {
                "role": "Dev",
                "week_days": 3,
                "duration_weeks": 12,
                "skills": ["py"],
                "profile_summary": "s",
            }
            assert _one(
                c, "SELECT status || '/' || remote || '/' || crm_status FROM companies"
            ) == ("in_progress/hybrid/linked")
            assert _one(c, "SELECT status || '/' || pigro_status FROM matches") == (
                "awaiting_signature/to_link"
            )
            documents = c.execute(
                text("SELECT kind, status, regime FROM contract_documents ORDER BY kind")
            ).all()
            assert [tuple(row) for row in documents] == [
                ("framework", "notice_served", "vat_registered"),
                ("letter", "cancelled", "occasional"),
            ]
            assert _one(c, "SELECT source FROM notes") == "person"
            assert (
                _one(c, "SELECT status || '/' || verified_via FROM referrals") == "verified/login"
            )
            assert _one(c, "SELECT filters FROM campaigns WHERE source = 'filters'") == {
                "list": "talents",
                "status": "new",
                "remote": "hybrid",
                "job_title": "dev",
                "min_rate": "100",
                "origin_page": "home",
                "with_logins": True,
                "created_from": "2026-01-01T00:00:00Z",
            }
            assert _one(c, "SELECT journey_status FROM campaigns WHERE source = 'status'") == (
                "card_empty_new"
            )
            assert _one(
                c, "SELECT button_meta || '/' || action FROM campaigns WHERE source = 'filters'"
            ) == ("request/card_complete")
            assert _one(c, "SELECT kind || '/' || status FROM campaign_recipients") == (
                "company/queued"
            )
            assert _one(c, "SELECT before FROM campaign_recipients") == {
                "t": "2026-01-01T00:00:00+00:00",
                "has_card": True,
                "has_cv": False,
                "complete": False,
                "requests": {"abc": "2026-01-01"},
            }
            assert _one(c, "SELECT source FROM campaign_optouts") == "complaint"
            assert _one(c, "SELECT location FROM team_proposals") == {
                "on_site": True,
                "place": "Roma",
            }
            assert _one(c, "SELECT team FROM team_proposals") == [
                {
                    "position": 1,
                    "freelancer_id": str(ids["freelancer"]),
                    "role": "Dev",
                    "reasoning": "m",
                    "week_days": 3,
                }
            ]
            assert _one(c, "SELECT economics FROM team_proposals") == {
                "day": {"min": 1, "max": 2},
                "month": {"min": 1, "max": None},
                "days_month": 20,
            }
            assert _one(c, "SELECT origin || '/' || people FROM team_proposals") == "public/3"
            assert _one(c, "SELECT status || '/' || origin || '/' || ref FROM team_requests") == (
                "new/public/RR"
            )
            assert _one(c, "SELECT answer FROM team_request_talents") == "yes"
            assert _one(c, "SELECT count(*) FROM ai_tasks WHERE task = 'anonymous_cv'") == 1
            assert _one(c, "SELECT purpose FROM ai_usage") == "test"
            assert _one(c, "SELECT summary FROM card_eval_runs") == {
                "experience_years_ok": 1,
                "experience_years_checked": 2,
                "experience_years_ok_rate": 0.5,
            }
            assert _one(c, "SELECT checks FROM card_eval_results") == {
                "experience_years": {"ok": True}
            }
            assert _one(c, "SELECT judgement FROM card_eval_results") == {"role": {"verdict": "ok"}}
            assert _one(c, "SELECT first_name || last_name || ref FROM signups") == "AdaLABC"
            payloads = {
                row.kind: row.payload
                for row in c.execute(text("SELECT kind, payload FROM admin_actions"))
            }
            assert payloads["overridden"] == {
                "changed": ["job_title", "status"],
                "before": {"job_title": "a", "status": "new"},
                "after": {"job_title": "b", "status": "active"},
            }
            assert payloads["note_added"] == {"note_id": "x", "source": "web", "text": "t"}
            assert payloads["card_regenerated"] == {
                "outcome": "written",
                "rebuilt": True,
                "source": "cli",
            }
            assert payloads["documents_sent"] == {
                "document": "a",
                "kind": "framework",
                "mail": True,
            }
            assert payloads["merged"] == {
                "left_behind": {"x": 1},
                "card_to_regenerate": True,
                "cv": "kept",
            }
            names = {
                i["name"]
                for t in inspect(engine).get_table_names()
                for i in inspect(engine).get_indexes(t)
            }
            assert {"uq_signups_email_lower", "uq_contract_documents_number"} <= names
            assert not {n for n in names if any(w in n for w in ("orbiters", "_nome_", "_stato_"))}
        engine.dispose()


def test_the_new_checks_refuse_the_old_values(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        command.upgrade(_config(url), PARENT)
        engine = create_engine(url, future=True)
        ids = _seed(engine)
        command.upgrade(_config(url), REVISION)
        with pytest.raises(IntegrityError, match="ck_freelancers_status"), engine.begin() as c:
            c.execute(
                text("UPDATE freelancers SET status = 'nuovo' WHERE id = :id"),
                {"id": ids["freelancer"]},
            )
        with (
            pytest.raises(IntegrityError, match="ck_contract_documents_regime"),
            engine.begin() as c,
        ):
            c.execute(text("UPDATE contract_documents SET regime = 'partita_iva'"))
        engine.dispose()


def test_the_downgrade_gives_every_old_name_and_value_back(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        command.upgrade(_config(url), PARENT)
        engine = create_engine(url, future=True)
        _seed(engine)
        command.upgrade(_config(url), REVISION)
        command.downgrade(_config(url), PARENT)
        with engine.connect() as c:
            assert _one(
                c, "SELECT stato || compilata_da || remoto || posizione FROM freelancers"
            ) == ("contattatopersonaibridoDev")
            assert _one(c, "SELECT card FROM freelancer_cards") == CARD
            assert _one(c, "SELECT fonti FROM freelancer_cards") == {
                "ruolo": ["cv"],
                "anni": ["cv", "github"],
            }
            assert _one(c, "SELECT body FROM anonymous_cvs") == CV
            assert _one(c, "SELECT crm_bozza FROM companies")["durata_settimane"] == 12
            assert (
                _one(c, "SELECT stato || '/' || pigro_stato FROM matches")
                == "in_firma/da_collegare"
            )
            assert (
                _one(c, "SELECT regime FROM contract_documents WHERE kind = 'quadro'")
                == "partita_iva"
            )
            assert _one(c, "SELECT filtri FROM campaigns WHERE fonte = 'filtri'") == {
                "lista": "talenti",
                "stato": "nuovo",
                "remoto": "ibrido",
                "posizione": "dev",
                "tariffa_min": "100",
                "origine": "home",
                "con_accessi": True,
                "creato_da": "2026-01-01T00:00:00Z",
            }
            assert _one(c, "SELECT prima FROM campaign_recipients")["ha_scheda"] is True
            assert _one(c, "SELECT luogo FROM team_proposals") == {"locale": True, "dove": "Roma"}
            assert _one(c, "SELECT risposta FROM team_request_talents") == "si"
            assert _one(c, "SELECT count(*) FROM ai_tasks WHERE task = 'cv_anonimo'") == 1
            assert _one(c, "SELECT purpose FROM ai_usage") == "prova"
            payloads = {
                row.kind: row.payload
                for row in c.execute(text("SELECT kind, payload FROM admin_actions"))
            }
            assert payloads["overridden"]["after"] == {"posizione": "b", "stato": "attivo"}
            assert payloads["note_added"] == {"nota_id": "x", "fonte": "web", "testo": "t"}
            assert payloads["card_regenerated"] == {
                "esito": "scritta",
                "ricostruita": True,
                "source": "cli",
            }
            assert payloads["documents_sent"]["kind"] == "quadro"
            assert payloads["merged"] == {
                "lasciati": {"x": 1},
                "scheda_da_rigenerare": True,
                "cv": "kept",
            }
            indexes = {i["name"] for i in inspect(engine).get_indexes("signups")}
            assert "uq_orbiters_signups_email_lower" in indexes
        engine.dispose()
