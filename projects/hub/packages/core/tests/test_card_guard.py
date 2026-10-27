"""The anonymity guard (REB-838): what in a card names the person, read against the
identifiers the sources add to the person's own name. No database, no model."""

from typing import Any

import pytest
from fakes_cards import CARD

from rebase_core.card_guard import KnownIdentifiers, identifying
from rebase_core.team_schemas import Card

KNOWN = KnownIdentifiers(
    cognome="Lovelace",
    nome="Ada",
    github_login="ada-codes",
    repo_names=["my-lib", "billing_engine", "app", "api"],
    hostnames=["mario.dev", "www.ada-writes.example"],
    employers=["Initech S.r.l.", "Globex"],
)


def _card(**fields: Any) -> Card:
    return Card.model_validate({**CARD, **fields})


def test_an_ordinary_card_is_clean() -> None:
    assert identifying(_card(), KNOWN) == []


def test_github_login_in_summary_fails() -> None:
    assert identifying(_card(sintesi="Mantiene ada-codes e altri progetti."), KNOWN) == ["sintesi"]
    # Without the punctuation, in any case.
    assert identifying(_card(sintesi="Su GitHub come ADA CODES."), KNOWN) == ["sintesi"]


def test_repo_name_in_summary_fails() -> None:
    assert identifying(_card(sintesi="Autore di my-lib, una libreria."), KNOWN) == ["sintesi"]
    assert identifying(_card(settori=["fintech", "billing engine"]), KNOWN) == ["settori"]


@pytest.mark.parametrize("written", ["my-lib", "my_lib", "my lib", "My Lib", "MY-LIB", "MyLib"])
def test_repo_name_variants_match(written: str) -> None:
    assert identifying(_card(sintesi=f"Ha scritto {written} da zero."), KNOWN) == ["sintesi"]


def test_repo_name_inside_a_longer_word_is_not_a_match() -> None:
    assert identifying(_card(sintesi="Una my-library di utilità, e mylibraries."), KNOWN) == []


@pytest.mark.parametrize("written", ["mario.dev", "https://mario.dev/blog"])
def test_hostname_in_any_field_fails(written: str) -> None:
    # `https://` also trips the address rule, as it always did: the field is named once.
    assert identifying(_card(luogo=written), KNOWN) == ["luogo"]
    assert identifying(_card(settori=["fintech", written]), KNOWN) == ["settori"]


def test_hostname_written_as_words_fails_and_www_is_dropped() -> None:
    assert identifying(_card(sintesi="Scrive su mario dev da anni."), KNOWN) == ["sintesi"]
    assert identifying(_card(sintesi="Il blog ada writes example."), KNOWN) == ["sintesi"]


def test_employer_in_ruolo_fails() -> None:
    assert identifying(_card(ruolo="Backend developer in Globex"), KNOWN) == ["ruolo"]


def test_employer_is_read_without_its_legal_form() -> None:
    assert identifying(_card(sintesi="Nove anni in Initech, API Python."), KNOWN) == ["sintesi"]


@pytest.mark.parametrize("name", ["app", "api", "ab", "web", "App Api"])
def test_generic_repo_name_app_does_not_fail(name: str) -> None:
    known = KnownIdentifiers(cognome="Lovelace", repo_names=[name])
    card = _card(sintesi="Sviluppa un'app e una api per il web.", competenze=["Python", "api"])
    assert identifying(card, known) == []


def test_a_repo_named_like_one_of_the_cards_skills_is_the_technology() -> None:
    known = KnownIdentifiers(cognome="Lovelace", repo_names=["fastapi"])
    assert identifying(_card(sintesi="API in FastAPI e AWS."), known) == []
    # The same name with no such skill is a repository the card must not name.
    assert identifying(_card(competenze=["Python"], sintesi="API in FastAPI."), known) == [
        "sintesi"
    ]


def test_returns_field_names_not_values() -> None:
    card = _card(
        ruolo="Autore di my-lib",
        sintesi="Su GitHub come ada-codes.",
        luogo="mario.dev",
        settori=["Globex"],
    )

    hit = identifying(card, KNOWN)

    assert hit == ["ruolo", "sintesi", "settori", "luogo"]
    joined = " ".join(hit).lower()
    for identifier in ("my-lib", "ada", "mario", "globex"):
        assert identifier not in joined


def test_each_field_is_named_once() -> None:
    card = _card(sintesi="my-lib, billing_engine e ada-codes.")
    assert identifying(card, KNOWN) == ["sintesi"]


def test_the_person_with_no_source_is_the_old_guard() -> None:
    known = KnownIdentifiers.empty("Conti", "Ada")
    assert identifying(_card(sintesi="La dashboard dei conti di una banca."), known) == []
    assert identifying(_card(sintesi="Il profilo di Conti."), known) == ["sintesi"]
    assert identifying(_card(sintesi="Il profilo di CONTI."), known) == ["sintesi"]
    assert identifying(_card(competenze=["Python", "Conti"]), known) == ["competenze"]
    assert identifying(_card(luogo="Conti"), known) == []  # a place is the CV's own
    assert identifying(_card(lingue=["Conti"]), known) == []
    assert identifying(_card(sintesi="Ada, backend developer."), known) == ["sintesi"]
    assert identifying(_card(competenze=["Ada", "SPARK"]), known) == []
    assert identifying(_card(sintesi="Un Adapter per ogni sensore."), known) == []
    assert identifying(_card(sintesi="Scrivete a ada@studio.it."), known) == ["sintesi"]
    assert identifying(_card(competenze=["Python", "www.ada.dev"]), known) == ["competenze"]
    assert identifying(_card(luogo="https://maps.example/torino"), known) == ["luogo"]


def test_the_first_name_inside_the_persons_own_skill_is_the_skill() -> None:
    known = KnownIdentifiers.empty("Neri", "Ruby")
    skills = ["Ruby on Rails", "PostgreSQL"]
    card = _card(ruolo="Ruby on Rails developer", competenze=skills)
    assert identifying(card, known) == []
    assert identifying(_card(ruolo="Ruby, backend developer", competenze=skills), known) == [
        "ruolo"
    ]


def test_a_source_that_names_nothing_adds_nothing() -> None:
    known = KnownIdentifiers(cognome="Lovelace", github_login=None, repo_names=[], hostnames=[])
    assert identifying(_card(), known) == []
    assert identifying(_card(), KnownIdentifiers.empty()) == []


# ---- review of #671: the label without the TLD, ordinary words, the skill exemption -----


@pytest.mark.parametrize(
    ("host", "written"),
    [
        ("acme-labs.io", "Ha lavorato per Acme Labs."),
        ("acmelabs.com", "Scrive per Acmelabs."),
        ("mario-rossi.dev", "Il blog di Mario Rossi."),
        ("blog.acme-labs.co.uk", "Sviluppa per ACME-LABS."),
        ("mario.github.io", "Pagina personale di mario."),
    ],
)
def test_a_links_label_is_caught_without_its_tld(host: str, written: str) -> None:
    known = KnownIdentifiers(cognome="Zz", hostnames=[host])

    assert identifying(_card(sintesi=written), known) == ["sintesi"]


def test_a_platform_or_an_ordinary_label_is_not_a_leak() -> None:
    known = KnownIdentifiers(
        cognome="Zz", hostnames=["mario.github.io", "blog.medium.com", "website.dev"]
    )

    card = _card(sintesi="Pubblica su GitHub e Medium, con un website e un blog.")

    assert identifying(card, known) == []


@pytest.mark.parametrize("name", ["website", "todo", "shop", "notes", "chat"])
def test_ordinary_repository_names_do_not_fail_every_card(name: str) -> None:
    known = KnownIdentifiers(cognome="Zz", repo_names=[name])

    assert identifying(_card(sintesi=f"Costruisce un {name} e una chat."), known) == []


def test_a_login_or_a_label_equal_to_the_cards_own_skill_is_the_technology() -> None:
    skills = {"competenze": ["Python", "Flask", "Rust"]}
    known = KnownIdentifiers(cognome="Zz", github_login="flask", hostnames=["rust.example"])

    assert identifying(_card(sintesi="Sviluppa API in Flask e Rust.", **skills), known) == []
    # The same words with no such skill on the card are still the person.
    assert identifying(_card(sintesi="Sviluppa API in Flask."), known) == ["sintesi"]


def test_a_clients_name_is_read_like_an_employer() -> None:
    known = KnownIdentifiers(cognome="Zz", employers=["Azienda Finta S.r.l."])

    assert identifying(_card(sintesi="Un progetto per Azienda Finta."), known) == ["sintesi"]
