"""The anonymous CV's identity guard (REB-862): what it refuses, and what a CV may say."""

from rebase_core.anonymous_cv.guard import Who, identifying_fields, who_of
from rebase_core.anonymous_cv.schema import (
    AnonymousCv,
    Esperienza,
    Gruppo,
    Lingua,
    Punto,
    Sezione,
    Titolo,
    strings,
)

CV = AnonymousCv(
    lingua="it",
    titolo="Backend developer",
    sommario="Sviluppatore con dieci anni di esperienza su sistemi distribuiti.",
    esperienze=[
        Esperienza(
            ruolo="GRC Analyst",
            azienda="TIM S.p.A.",
            luogo="Messina, IT",
            periodo="2019 – oggi",
            punti=[
                Punto(titolo="Audit (TIM S.p.A.)", testo="Ha condotto audit ISO 27001."),
                Punto(titolo=None, testo="Ha scritto le policy di sicurezza."),
            ],
        ),
        Esperienza(
            ruolo="Sviluppatore",
            azienda="Acme S.r.l.",
            luogo="Roma, IT",
            periodo="2014 – 2019",
            punti=[
                Punto(titolo=None, testo="Ha portato in produzione il servizio di fatturazione.")
            ],
        ),
    ],
    competenze=[Gruppo(area="Linguaggi", voci=["Python", "Go"])],
    formazione=[
        Titolo(
            titolo="Diploma di perito informatico",
            istituto="I.I.S. Ettore Majorana",
            luogo="Messina, IT",
            periodo="2009 – 2014",
        )
    ],
    certificazioni=["CISA"],
    lingue=[
        Lingua(lingua="Italiano", livello="Madrelingua"),
        Lingua(lingua="Inglese", livello=None),
    ],
    altre_sezioni=[Sezione(titolo="Progetti", voci=["Un parser di fatture"])],
)

WHO = who_of(
    nome="Mario",
    cognome="Messina",
    emails=["mario.messina@proton.me"],
    phones=["+39 333 123 4567"],
    links=[
        "https://github.com/mmessina",
        "https://mariomessina.dev",
        "https://www.linkedin.com/in/mario-messina",
    ],
)


def test_a_clean_cv_passes() -> None:
    assert identifying_fields(CV, WHO) == []


def test_surname_in_a_bullet() -> None:
    cv = CV.model_copy(deep=True)
    cv.esperienze[1].punti[0].testo = "Ha lavorato con Messina su fatturazione."
    assert identifying_fields(cv, WHO) == ["esperienze[1].punti[0].testo"]


def test_surname_in_capitals_in_the_title() -> None:
    cv = CV.model_copy(update={"titolo": "MARIO MESSINA – Backend developer"})
    assert "titolo" in identifying_fields(cv, WHO)


def test_surname_in_a_company() -> None:
    cv = CV.model_copy(deep=True)
    cv.esperienze[0].azienda = "Messina Consulting S.r.l."
    assert identifying_fields(cv, WHO) == ["esperienze[0].azienda"]


def test_lowercase_word_is_not_the_surname() -> None:
    who = who_of(nome="Mario", cognome="Porta", emails=[], phones=[], links=[])
    cv = CV.model_copy(deep=True)
    cv.esperienze[1].punti[0].testo = "Ha portato in produzione, porta in produzione."
    assert identifying_fields(cv, who) == []


def test_first_name_that_is_a_skill() -> None:
    who = who_of(nome="Ada", cognome=None, emails=[], phones=[], links=[])
    cv = CV.model_copy(update={"sommario": "Sviluppo in Ada"})
    cv.competenze = [Gruppo(area="Linguaggi", voci=["Ada"])]
    assert identifying_fields(cv, who) == []
    other = CV.model_copy(update={"sommario": "Ada ha guidato il team"})
    assert identifying_fields(other, who) == ["sommario"]


def test_first_name_is_not_read_in_a_place_or_a_school() -> None:
    # «Roma» is a luogo and «Ettore» the school's name: neither is prose.
    for nome in ("Roma", "Ettore"):
        who = who_of(nome=nome, cognome=None, emails=[], phones=[], links=[])
        assert identifying_fields(CV, who) == []
    prose = CV.model_copy(update={"sommario": "Ettore ha guidato il team"})
    assert identifying_fields(
        prose, who_of(nome="Ettore", cognome=None, emails=[], phones=[], links=[])
    ) == ["sommario"]


def test_phone_in_another_format() -> None:
    cv = CV.model_copy(deep=True)
    cv.esperienze[0].punti[1].testo = "chiamare 333 1234567"
    assert identifying_fields(cv, WHO) == ["esperienze[0].punti[1].testo"]


def test_short_number_is_not_a_phone() -> None:
    who = who_of(nome=None, cognome=None, emails=[], phones=["12345"], links=[])
    cv = CV.model_copy(update={"sommario": "Ha seguito 12345 utenti."})
    assert identifying_fields(cv, who) == []


def test_email_handle_and_login() -> None:
    for word in ("mmessina", "mario.messina"):
        cv = CV.model_copy(deep=True)
        cv.altre_sezioni[0].voci[0] = word
        assert identifying_fields(cv, WHO) == ["altre_sezioni[0].voci[0]"]


def test_any_url_or_at() -> None:
    for word in ("www.github.com/acme", "https://github.com/acme", "@acme"):
        cv = CV.model_copy(deep=True)
        cv.altre_sezioni[0].voci[0] = word
        assert identifying_fields(cv, WHO) == ["altre_sezioni[0].voci[0]"]


def test_personal_domain_whole_host() -> None:
    cv = CV.model_copy(deep=True)
    cv.altre_sezioni[0].voci[0] = "mariomessina.dev"
    assert identifying_fields(cv, WHO) == ["altre_sezioni[0].voci[0]"]


def test_an_employer_called_like_the_site_passes() -> None:
    who = who_of(nome=None, cognome=None, emails=[], phones=[], links=["https://acme.io"])
    cv = CV.model_copy(deep=True)
    cv.esperienze[1].azienda = "Acme S.r.l."
    assert identifying_fields(cv, who) == []


def test_merged_unites_without_repeating() -> None:
    other = who_of(
        nome="Mario", cognome="Messina", emails=["MARIO.MESSINA@proton.me"], phones=[], links=[]
    )
    merged = WHO.merged(other)
    assert merged.nomi == ["Mario"] and merged.emails == ["mario.messina@proton.me"]
    assert isinstance(merged, Who) and merged.github_logins == ["mmessina"]


def test_strings_paths_in_order() -> None:
    assert [path for path, _ in strings(CV)][:3] == ["titolo", "sommario", "esperienze[0].ruolo"]


def _voce(value: str) -> AnonymousCv:
    cv = CV.model_copy(deep=True)
    cv.altre_sezioni[0].voci[0] = value
    return cv


def test_bare_profile_link() -> None:
    who = who_of(nome=None, cognome=None, emails=[], phones=[], links=[])
    for word in ("linkedin.com/in/someone-else", "github.com/acme", "Github.com/Acme"):
        assert identifying_fields(_voce(word), who) == ["altre_sezioni[0].voci[0]"]
    assert identifying_fields(_voce("GitHub Actions e LinkedIn"), who) == []


def test_name_inside_a_slug() -> None:
    who = who_of(nome="Alex", cognome="Fegatilli", emails=[], phones=[], links=[])
    for word in ("alex-fegatilli-fau50", "fegatilli.dev"):
        assert identifying_fields(_voce(word), who) == ["altre_sezioni[0].voci[0]"]


def test_skills_with_dots_and_slashes_pass() -> None:
    cv = CV.model_copy(deep=True)
    cv.competenze = []
    cv.esperienze[1].punti[0].testo = "migrato da Node.js/Express ad ASP.NET Core"
    assert identifying_fields(cv, WHO) == []


def _who(nome: str | None = None, cognome: str | None = None, **kw: list[str]) -> Who:
    return who_of(
        nome=nome,
        cognome=cognome,
        emails=kw.get("emails", []),
        phones=[],
        links=kw.get("links", []),
    )


def _skill(value: str) -> AnonymousCv:
    cv = CV.model_copy(deep=True)
    cv.competenze = [Gruppo(area="Linguaggi", voci=["Python", value])]
    return cv


VOCE = ["altre_sezioni[0].voci[0]"]


def test_address_alone() -> None:
    for word in ("https://acme.io", "www.acme.io"):
        assert identifying_fields(_voce(word), _who()) == VOCE


def test_whole_host_alone() -> None:
    # A two-letter label is not searched alone, so only the whole host can catch it.
    who = _who(links=["https://ab.dev"])
    assert identifying_fields(_voce("ab.dev"), who) == VOCE
    assert identifying_fields(_voce("ab"), who) == []
    personal = _who(links=["https://fau50.dev"])
    assert identifying_fields(_voce("fau50"), personal) == VOCE  # the label of the host


def test_slugs_are_caught() -> None:
    who = _who("Alex", "Fegatilli")
    assert identifying_fields(_voce("alex-fegatilli-fau50"), who) == VOCE
    assert identifying_fields(_voce("fegatilli.dev"), who) == VOCE
    assert identifying_fields(_voce("linkedin.com/in/mario-rossi"), who) == VOCE
    assert identifying_fields(_voce("mariomessina.dev"), _who("Mario", "Messina")) == VOCE
    assert identifying_fields(_voce("mmessina.dev"), _who("Mario", "Messina")) == VOCE
    assert identifying_fields(_voce("deluca.dev"), _who("Mario", "De Luca")) == VOCE
    assert identifying_fields(_voce("damico.dev"), _who("Mario", "D'Amico")) == VOCE
    assert identifying_fields(_voce("cantu.dev"), _who("Mario", "Cantù")) == VOCE


def test_ordinary_words_with_a_name_inside_pass() -> None:
    for nome, cognome, text in (
        ("Maria", "Rossi", "MySQL/MariaDB"),
        ("Tom", "Rossi", "Apache/Tomcat"),
        ("Ian", "Rossi", "Debian/Ubuntu"),
        ("Jan", "Rossi", "Python/Django"),
        ("Eva", "Rossi", "model-evaluation"),
        ("Lea", "Rossi", "release-management"),
        ("Mario", "Conti", "continuous-integration"),
        ("Mario", "Riva", "private-cloud"),
    ):
        assert identifying_fields(_voce(text), _who(nome, cognome)) == []


def test_name_in_a_link_respects_place_and_skills() -> None:
    cv = CV.model_copy(deep=True)
    cv.esperienze[0].luogo = "Messina/Remoto"
    assert identifying_fields(cv, _who("Mario", "Messina")) == []
    assert identifying_fields(_skill("Julia/Python"), _who("Julia", "Rossi")) == []


def test_skill_called_like_the_person_is_a_leak() -> None:
    who = _who("Mario", "Messina")
    assert identifying_fields(_skill("Mario Messina"), who) == ["competenze[0].voci[1]"]
    cv = _skill("Messina").model_copy(update={"titolo": "Messina - Backend developer"})
    assert identifying_fields(cv, _who(None, "Messina")) == ["titolo", "competenze[0].voci[1]"]


def test_compound_surname_words() -> None:
    cv = CV.model_copy(update={"sommario": "Luca ha guidato il team"})
    assert identifying_fields(cv, _who("Mario", "De Luca")) == ["sommario"]
    cv = CV.model_copy(deep=True)
    cv.esperienze[1].punti[0].testo = "Ha lavorato con Bianchi."
    assert identifying_fields(cv, _who("Mario", "Rossi Bianchi")) == [
        "esperienze[1].punti[0].testo"
    ]


def test_logins_survive_a_merge() -> None:
    profile = _who(links=["https://github.com/mmessina"])
    from_cv = _who(links=["github.com/fau50"])
    merged = profile.merged(from_cv)
    assert merged.github_logins == ["mmessina", "fau50"]
    for word in ("mmessina", "fau50"):
        assert identifying_fields(_voce(word), merged) == VOCE


def test_handle_in_the_path_of_a_link() -> None:
    who = _who(links=["https://www.linkedin.com/in/afegatilli-99"])
    assert identifying_fields(_voce("afegatilli"), who) == VOCE


def test_employer_named_in_the_cv_is_not_the_persons_site() -> None:
    who = _who(links=["https://acme-labs.io"])
    cv = CV.model_copy(deep=True)
    cv.esperienze[1].azienda = "Acme Labs S.r.l."
    assert identifying_fields(cv, who) == []


def test_name_like_handles_are_not_read_in_a_place() -> None:
    who = _who(emails=["messina@gmail.com"])
    assert identifying_fields(CV, who) == []
    assert identifying_fields(_voce("messina"), who) == VOCE
    assert identifying_fields(_voce("scrivi a messina@gmail.com"), who) == VOCE


def test_more_profile_hosts() -> None:
    for word in ("t.me/fau50", "wa.me/39333", "orcid.org/0000", "leetcode.com/u/x"):
        assert identifying_fields(_voce(word), _who()) == VOCE


def test_identifier_in_a_bullet_title() -> None:
    cv = CV.model_copy(deep=True)
    cv.esperienze[0].punti[0].titolo = "Audit per Messina"
    assert identifying_fields(cv, WHO) == ["esperienze[0].punti[0].titolo"]


def test_surname_is_not_read_in_a_language() -> None:
    cv = CV.model_copy(deep=True)
    cv.lingue[1] = Lingua(lingua="Tedesco", livello=None)
    assert identifying_fields(cv, _who("Mario", "Tedesco")) == []


def test_platform_links_name_no_one() -> None:
    cases = (
        ("https://youtube.com/@alexf", "YouTube"),
        ("https://instagram.com/alexf", "Instagram"),
        ("https://facebook.com/alexf", "Facebook Ads"),
        ("https://twitter.com/alexf", "Twitter API"),
        ("https://hackerrank.com/profile/alexf", "HackerRank Python (Gold)"),
        ("https://leetcode.com/u/alexf", "LeetCode"),
        ("https://scholar.google.com/citations", "Google Cloud Professional Architect"),
        ("https://about.me/alexf", "about"),
        ("https://dev.to/alexf", "from dev to prod"),
        ("https://about.me/alexf", "about me"),
    )
    for link, text in cases:
        assert identifying_fields(_voce(text), _who(links=[link])) == [], (link, text)
    assert identifying_fields(_voce("alexf"), _who(links=["https://instagram.com/alexf"])) == VOCE


def test_generic_path_segments_name_no_one() -> None:
    cases = (
        ("https://stackoverflow.com/users/1234567/mario-rossi", "Served 2M users"),
        ("https://youtube.com/channel/UC1234", "Multi-channel"),
        ("https://youtube.com/user/x", "User research"),
        ("https://researchgate.net/profile/Mario-Rossi", "customer profile service"),
        ("https://mariorossi.dev/about", "about"),
        ("https://mariorossi.dev/work", "Remote work"),
        ("https://credly.com/badges/0f1e", "Open Badges"),
        ("https://sites.google.com/view/x", "Point of view"),
    )
    for link, text in cases:
        assert identifying_fields(_voce(text), _who(links=[link])) == [], (link, text)
    for link, text in (
        ("https://linkedin.com/in/afegatilli-99", "afegatilli-99"),
        ("https://stackoverflow.com/users/1234567/mario-rossi", "mario-rossi"),
        ("https://mariorossi.dev/folio-zeta", "folio-zeta"),
        ("https://mario.github.io", "mario"),
    ):
        assert identifying_fields(_voce(text), _who(links=[link])) == VOCE, (link, text)


def test_italian_particles() -> None:
    for cognome, text in (
        ("Dal Lago", "Dal 2019 ha guidato il team"),
        ("Dalla Costa", "Dalla fase di analisi al rilascio"),
        ("Dalle Mura", "Dalle specifiche al codice"),
        ("Dall'Ara", "Dall'analisi al rilascio"),
        ("Dell'Acqua", "server Dell PowerEdge"),
        ("Dell'Orto", "Dell EMC"),
    ):
        cv = CV.model_copy(update={"sommario": text})
        assert identifying_fields(cv, _who("Mario", cognome)) == [], (cognome, text)
    cv = CV.model_copy(update={"sommario": "Acqua e Lago"})
    assert identifying_fields(cv, _who("Mario", "Dell'Acqua")) == ["sommario"]


def test_first_name_in_a_slug_outside_prose() -> None:
    cv = CV.model_copy(deep=True)
    cv.certificazioni = ["Credly: alex-cloud.dev"]
    assert identifying_fields(cv, _who("Alex", "Rossi")) == ["certificazioni[0]"]


def test_skill_is_blanked_unless_it_holds_the_surname_as_a_word() -> None:
    cv = _skill("Julia").model_copy(update={"sommario": "Sviluppo in Julia e Python."})
    assert identifying_fields(cv, _who("Julia", "Li")) == []


def test_short_handles_keep_their_digits() -> None:
    cv = CV.model_copy(deep=True)
    cv.formazione[0].istituto = "FAU Erlangen-Nürnberg"
    assert identifying_fields(cv, _who(links=["https://github.com/fau50"])) == []
    cv = _skill("Java")
    assert identifying_fields(cv, _who(links=["https://gitlab.com/java8"])) == []


def _cert(value: str) -> AnonymousCv:
    cv = CV.model_copy(deep=True)
    cv.certificazioni = [value]
    return cv


def test_every_profile_handle_is_read() -> None:
    for link, text in (
        ("https://leetcode.com/mariorossi/", "LeetCode: mariorossi"),
        ("https://www.hackerrank.com/mariorossi", "HackerRank mariorossi"),
        ("https://www.youtube.com/c/MarioRossi", "Canale YouTube MarioRossi"),
        ("https://www.youtube.com/user/mariorossi", "Canale YouTube mariorossi"),
        ("https://mariorossi.medium.com", "Blog Medium mariorossi"),
        ("https://bitbucket.org/mariorossi/fatture", "Bitbucket mariorossi"),
        ("https://sourceforge.net/u/mariorossi/profile", "SourceForge mariorossi"),
        ("https://kaggle.com/mariorossi", "Kaggle mariorossi"),
        ("https://www.facebook.com/people/Mario-Rossi/100012345", "Facebook mario-rossi"),
    ):
        who = _who(links=[link])
        assert identifying_fields(_cert(text), who) == ["certificazioni[0]"], (link, text)


def test_more_platforms_name_no_one() -> None:
    for link, text in (
        ("https://scholar.google.it/citations?user=AbC123", "Google Cloud"),
        ("https://scholar.google.co.uk/citations?user=AbC123", "Google Cloud"),
        ("https://hub.docker.com/u/xdev99", "Docker"),
        ("https://xdev.webflow.io", "Webflow"),
        ("https://xdev.framer.website", "Framer"),
        ("https://www.credly.com/users/xdev99", "Credly"),
        ("https://www.upwork.com/freelancers/~01abc", "Upwork"),
        ("https://www.credly.com/badges/1b2c3d4e/public_url", "public URL firmati"),
        ("https://www.facebook.com/people/Mario-Rossi/100012345", "People management"),
    ):
        assert identifying_fields(_voce(text), _who(links=[link])) == [], (link, text)


def test_hyphenated_names_outside_prose_pass() -> None:
    for nome, text in (
        ("Max", "Max-Planck-Institut"),
        ("Mercedes", "Mercedes-Benz Italia S.p.A."),
        ("Marie", "Marie-Curie fellowship"),
        ("Ada", "ADA-compliant"),
    ):
        assert identifying_fields(_cert(text), _who(nome, "Rossi")) == [], text


def test_name_run_without_a_link() -> None:
    who = _who("Mario", "Rossi")
    for text in ("Blog: mariorossi", "Canale MarioRossi", "rossimario", "mariorossi99"):
        assert identifying_fields(_voce(text), who) == VOCE, text
    assert identifying_fields(_voce("Mario Rossi"), who) == VOCE  # the capitalised-word reading
    assert identifying_fields(_voce("mario rossi"), who) == []
