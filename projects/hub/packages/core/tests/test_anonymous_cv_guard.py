"""The anonymous CV's identity guard (REB-862): what it refuses, and what a CV may say."""

from rebase_core.anonymous_cv.guard import Who, identifying_fields, who_of
from rebase_core.anonymous_cv.schema import (
    AnonymousCv,
    Experience,
    Group,
    Language,
    Point,
    Section,
    Title,
    strings,
)

CV = AnonymousCv(
    language="it",
    title="Backend developer",
    summary="Sviluppatore con dieci anni di esperienza su sistemi distribuiti.",
    experience=[
        Experience(
            role="GRC Analyst",
            company="TIM S.p.A.",
            location="Messina, IT",
            period="2019 – oggi",
            points=[
                Point(title="Audit (TIM S.p.A.)", text="Ha condotto audit ISO 27001."),
                Point(title=None, text="Ha scritto le policy di sicurezza."),
            ],
        ),
        Experience(
            role="Sviluppatore",
            company="Acme S.r.l.",
            location="Roma, IT",
            period="2014 – 2019",
            points=[
                Point(title=None, text="Ha portato in produzione il servizio di fatturazione.")
            ],
        ),
    ],
    skills=[Group(area="Linguaggi", entries=["Python", "Go"])],
    education=[
        Title(
            title="Diploma di perito informatico",
            institution="I.I.S. Ettore Majorana",
            location="Messina, IT",
            period="2009 – 2014",
        )
    ],
    certifications=["CISA"],
    languages=[
        Language(language="Italiano", level="Madrelingua"),
        Language(language="Inglese", level=None),
    ],
    other_sections=[Section(title="Progetti", entries=["Un parser di fatture"])],
)

WHO = who_of(
    first_name="Mario",
    last_name="Messina",
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
    cv.experience[1].points[0].text = "Ha lavorato con Messina su fatturazione."
    assert identifying_fields(cv, WHO) == ["experience[1].points[0].text"]


def test_surname_in_capitals_in_the_title() -> None:
    cv = CV.model_copy(update={"title": "MARIO MESSINA – Backend developer"})
    assert "title" in identifying_fields(cv, WHO)


def test_surname_in_a_company() -> None:
    cv = CV.model_copy(deep=True)
    cv.experience[0].company = "Messina Consulting S.r.l."
    assert identifying_fields(cv, WHO) == ["experience[0].company"]


def test_lowercase_word_is_not_the_surname() -> None:
    who = who_of(first_name="Mario", last_name="Porta", emails=[], phones=[], links=[])
    cv = CV.model_copy(deep=True)
    cv.experience[1].points[0].text = "Ha portato in produzione, porta in produzione."
    assert identifying_fields(cv, who) == []


def test_first_name_that_is_a_skill() -> None:
    who = who_of(first_name="Ada", last_name=None, emails=[], phones=[], links=[])
    cv = CV.model_copy(update={"summary": "Sviluppo in Ada"})
    cv.skills = [Group(area="Linguaggi", entries=["Ada"])]
    assert identifying_fields(cv, who) == []
    other = CV.model_copy(update={"summary": "Ada ha guidato il team"})
    assert identifying_fields(other, who) == ["summary"]


def test_first_name_is_not_read_in_a_place_or_a_school() -> None:
    # «Roma» is a location and «Ettore» the school's name: neither is prose.
    for first_name in ("Roma", "Ettore"):
        who = who_of(first_name=first_name, last_name=None, emails=[], phones=[], links=[])
        assert identifying_fields(CV, who) == []
    prose = CV.model_copy(update={"summary": "Ettore ha guidato il team"})
    assert identifying_fields(
        prose, who_of(first_name="Ettore", last_name=None, emails=[], phones=[], links=[])
    ) == ["summary"]


def test_phone_in_another_format() -> None:
    cv = CV.model_copy(deep=True)
    cv.experience[0].points[1].text = "chiamare 333 1234567"
    assert identifying_fields(cv, WHO) == ["experience[0].points[1].text"]


def test_short_number_is_not_a_phone() -> None:
    who = who_of(first_name=None, last_name=None, emails=[], phones=["12345"], links=[])
    cv = CV.model_copy(update={"summary": "Ha seguito 12345 utenti."})
    assert identifying_fields(cv, who) == []


def test_email_handle_and_login() -> None:
    for word in ("mmessina", "mario.messina"):
        cv = CV.model_copy(deep=True)
        cv.other_sections[0].entries[0] = word
        assert identifying_fields(cv, WHO) == ["other_sections[0].entries[0]"]


def test_any_url_or_at() -> None:
    for word in ("www.github.com/acme", "https://github.com/acme", "@acme"):
        cv = CV.model_copy(deep=True)
        cv.other_sections[0].entries[0] = word
        assert identifying_fields(cv, WHO) == ["other_sections[0].entries[0]"]


def test_personal_domain_whole_host() -> None:
    cv = CV.model_copy(deep=True)
    cv.other_sections[0].entries[0] = "mariomessina.dev"
    assert identifying_fields(cv, WHO) == ["other_sections[0].entries[0]"]


def test_an_employer_called_like_the_site_passes() -> None:
    who = who_of(first_name=None, last_name=None, emails=[], phones=[], links=["https://acme.io"])
    cv = CV.model_copy(deep=True)
    cv.experience[1].company = "Acme S.r.l."
    assert identifying_fields(cv, who) == []


def test_merged_unites_without_repeating() -> None:
    other = who_of(
        first_name="Mario",
        last_name="Messina",
        emails=["MARIO.MESSINA@proton.me"],
        phones=[],
        links=[],
    )
    merged = WHO.merged(other)
    assert merged.first_names == ["Mario"] and merged.emails == ["mario.messina@proton.me"]
    assert isinstance(merged, Who) and merged.github_logins == ["mmessina"]


def test_strings_paths_in_order() -> None:
    assert [path for path, _ in strings(CV)][:3] == ["title", "summary", "experience[0].role"]


def _entry(value: str) -> AnonymousCv:
    cv = CV.model_copy(deep=True)
    cv.other_sections[0].entries[0] = value
    return cv


def test_bare_profile_link() -> None:
    who = who_of(first_name=None, last_name=None, emails=[], phones=[], links=[])
    for word in ("linkedin.com/in/someone-else", "github.com/acme", "Github.com/Acme"):
        assert identifying_fields(_entry(word), who) == ["other_sections[0].entries[0]"]
    assert identifying_fields(_entry("GitHub Actions e LinkedIn"), who) == []


def test_name_inside_a_slug() -> None:
    who = who_of(first_name="Alex", last_name="Fegatilli", emails=[], phones=[], links=[])
    for word in ("alex-fegatilli-fau50", "fegatilli.dev"):
        assert identifying_fields(_entry(word), who) == ["other_sections[0].entries[0]"]


def test_skills_with_dots_and_slashes_pass() -> None:
    cv = CV.model_copy(deep=True)
    cv.skills = []
    cv.experience[1].points[0].text = "migrato da Node.js/Express ad ASP.NET Core"
    assert identifying_fields(cv, WHO) == []


def _who(first_name: str | None = None, last_name: str | None = None, **kw: list[str]) -> Who:
    return who_of(
        first_name=first_name,
        last_name=last_name,
        emails=kw.get("emails", []),
        phones=[],
        links=kw.get("links", []),
    )


def _skill(value: str) -> AnonymousCv:
    cv = CV.model_copy(deep=True)
    cv.skills = [Group(area="Linguaggi", entries=["Python", value])]
    return cv


ENTRY = ["other_sections[0].entries[0]"]


def test_address_alone() -> None:
    for word in ("https://acme.io", "www.acme.io"):
        assert identifying_fields(_entry(word), _who()) == ENTRY


def test_whole_host_alone() -> None:
    # A two-letter label is not searched alone, so only the whole host can catch it.
    who = _who(links=["https://ab.dev"])
    assert identifying_fields(_entry("ab.dev"), who) == ENTRY
    assert identifying_fields(_entry("ab"), who) == []
    personal = _who(links=["https://fau50.dev"])
    assert identifying_fields(_entry("fau50"), personal) == ENTRY  # the label of the host


def test_slugs_are_caught() -> None:
    who = _who("Alex", "Fegatilli")
    assert identifying_fields(_entry("alex-fegatilli-fau50"), who) == ENTRY
    assert identifying_fields(_entry("fegatilli.dev"), who) == ENTRY
    assert identifying_fields(_entry("linkedin.com/in/mario-rossi"), who) == ENTRY
    assert identifying_fields(_entry("mariomessina.dev"), _who("Mario", "Messina")) == ENTRY
    assert identifying_fields(_entry("mmessina.dev"), _who("Mario", "Messina")) == ENTRY
    assert identifying_fields(_entry("deluca.dev"), _who("Mario", "De Luca")) == ENTRY
    assert identifying_fields(_entry("damico.dev"), _who("Mario", "D'Amico")) == ENTRY
    assert identifying_fields(_entry("cantu.dev"), _who("Mario", "Cantù")) == ENTRY


def test_ordinary_words_with_a_name_inside_pass() -> None:
    for first_name, last_name, text in (
        ("Maria", "Rossi", "MySQL/MariaDB"),
        ("Tom", "Rossi", "Apache/Tomcat"),
        ("Ian", "Rossi", "Debian/Ubuntu"),
        ("Jan", "Rossi", "Python/Django"),
        ("Eva", "Rossi", "model-evaluation"),
        ("Lea", "Rossi", "release-management"),
        ("Mario", "Conti", "continuous-integration"),
        ("Mario", "Riva", "private-cloud"),
    ):
        assert identifying_fields(_entry(text), _who(first_name, last_name)) == []


def test_name_in_a_link_respects_place_and_skills() -> None:
    cv = CV.model_copy(deep=True)
    cv.experience[0].location = "Messina/Remoto"
    assert identifying_fields(cv, _who("Mario", "Messina")) == []
    assert identifying_fields(_skill("Julia/Python"), _who("Julia", "Rossi")) == []


def test_skill_called_like_the_person_is_a_leak() -> None:
    who = _who("Mario", "Messina")
    assert identifying_fields(_skill("Mario Messina"), who) == ["skills[0].entries[1]"]
    cv = _skill("Messina").model_copy(update={"title": "Messina - Backend developer"})
    assert identifying_fields(cv, _who(None, "Messina")) == ["title", "skills[0].entries[1]"]


def test_compound_surname_words() -> None:
    cv = CV.model_copy(update={"summary": "Luca ha guidato il team"})
    assert identifying_fields(cv, _who("Mario", "De Luca")) == ["summary"]
    cv = CV.model_copy(deep=True)
    cv.experience[1].points[0].text = "Ha lavorato con Bianchi."
    assert identifying_fields(cv, _who("Mario", "Rossi Bianchi")) == [
        "experience[1].points[0].text"
    ]


def test_logins_survive_a_merge() -> None:
    profile = _who(links=["https://github.com/mmessina"])
    from_cv = _who(links=["github.com/fau50"])
    merged = profile.merged(from_cv)
    assert merged.github_logins == ["mmessina", "fau50"]
    for word in ("mmessina", "fau50"):
        assert identifying_fields(_entry(word), merged) == ENTRY


def test_handle_in_the_path_of_a_link() -> None:
    who = _who(links=["https://www.linkedin.com/in/afegatilli-99"])
    assert identifying_fields(_entry("afegatilli"), who) == ENTRY


def test_employer_named_in_the_cv_is_not_the_persons_site() -> None:
    who = _who(links=["https://acme-labs.io"])
    cv = CV.model_copy(deep=True)
    cv.experience[1].company = "Acme Labs S.r.l."
    assert identifying_fields(cv, who) == []


def test_name_like_handles_are_not_read_in_a_place() -> None:
    who = _who(emails=["messina@gmail.com"])
    assert identifying_fields(CV, who) == []
    assert identifying_fields(_entry("messina"), who) == ENTRY
    assert identifying_fields(_entry("scrivi a messina@gmail.com"), who) == ENTRY


def test_more_profile_hosts() -> None:
    for word in ("t.me/fau50", "wa.me/39333", "orcid.org/0000", "leetcode.com/u/x"):
        assert identifying_fields(_entry(word), _who()) == ENTRY


def test_identifier_in_a_bullet_title() -> None:
    cv = CV.model_copy(deep=True)
    cv.experience[0].points[0].title = "Audit per Messina"
    assert identifying_fields(cv, WHO) == ["experience[0].points[0].title"]


def test_surname_is_not_read_in_a_language() -> None:
    cv = CV.model_copy(deep=True)
    cv.languages[1] = Language(language="Tedesco", level=None)
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
        assert identifying_fields(_entry(text), _who(links=[link])) == [], (link, text)
    assert identifying_fields(_entry("alexf"), _who(links=["https://instagram.com/alexf"])) == ENTRY


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
        assert identifying_fields(_entry(text), _who(links=[link])) == [], (link, text)
    for link, text in (
        ("https://linkedin.com/in/afegatilli-99", "afegatilli-99"),
        ("https://stackoverflow.com/users/1234567/mario-rossi", "mario-rossi"),
        ("https://mariorossi.dev/folio-zeta", "folio-zeta"),
        ("https://mario.github.io", "mario"),
    ):
        assert identifying_fields(_entry(text), _who(links=[link])) == ENTRY, (link, text)


def test_italian_particles() -> None:
    for last_name, text in (
        ("Dal Lago", "Dal 2019 ha guidato il team"),
        ("Dalla Costa", "Dalla fase di analisi al rilascio"),
        ("Dalle Mura", "Dalle specifiche al codice"),
        ("Dall'Ara", "Dall'analisi al rilascio"),
        ("Dell'Acqua", "server Dell PowerEdge"),
        ("Dell'Orto", "Dell EMC"),
    ):
        cv = CV.model_copy(update={"summary": text})
        assert identifying_fields(cv, _who("Mario", last_name)) == [], (last_name, text)
    cv = CV.model_copy(update={"summary": "Acqua e Lago"})
    assert identifying_fields(cv, _who("Mario", "Dell'Acqua")) == ["summary"]


def test_first_name_in_a_slug_outside_prose() -> None:
    cv = CV.model_copy(deep=True)
    cv.certifications = ["Credly: alex-cloud.dev"]
    assert identifying_fields(cv, _who("Alex", "Rossi")) == ["certifications[0]"]


def test_skill_is_blanked_unless_it_holds_the_surname_as_a_word() -> None:
    cv = _skill("Julia").model_copy(update={"summary": "Sviluppo in Julia e Python."})
    assert identifying_fields(cv, _who("Julia", "Li")) == []


def test_short_handles_keep_their_digits() -> None:
    cv = CV.model_copy(deep=True)
    cv.education[0].institution = "FAU Erlangen-Nürnberg"
    assert identifying_fields(cv, _who(links=["https://github.com/fau50"])) == []
    cv = _skill("Java")
    assert identifying_fields(cv, _who(links=["https://gitlab.com/java8"])) == []


def _cert(value: str) -> AnonymousCv:
    cv = CV.model_copy(deep=True)
    cv.certifications = [value]
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
        assert identifying_fields(_cert(text), who) == ["certifications[0]"], (link, text)


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
        assert identifying_fields(_entry(text), _who(links=[link])) == [], (link, text)


def test_hyphenated_names_outside_prose_pass() -> None:
    for first_name, text in (
        ("Max", "Max-Planck-Institut"),
        ("Mercedes", "Mercedes-Benz Italia S.p.A."),
        ("Marie", "Marie-Curie fellowship"),
        ("Ada", "ADA-compliant"),
    ):
        assert identifying_fields(_cert(text), _who(first_name, "Rossi")) == [], text


def test_name_run_without_a_link() -> None:
    who = _who("Mario", "Rossi")
    for text in ("Blog: mariorossi", "Canale MarioRossi", "rossimario", "mariorossi99"):
        assert identifying_fields(_entry(text), who) == ENTRY, text
    assert identifying_fields(_entry("Mario Rossi"), who) == ENTRY  # the capitalised-word reading


def test_a_page_of_a_profile_site_is_not_a_handle() -> None:
    """Without its marker, a profile site's path names a user only when it is one segment
    long (`leetcode.com/mariorossi`): a certificate, a story, a paper or a problem list is
    the site's own page."""
    for link in (
        "https://www.hackerrank.com/certificates/a1b2c3d4e5",
        "https://stackoverflow.com/story/mariorossi",
        "https://www.researchgate.net/publication/123456_Fatture_elettroniche",
        "https://leetcode.com/problemset/all",
        "https://www.kaggle.com/competitions/titanic",
    ):
        assert _who(links=[link]).handles == [], link
    assert (
        identifying_fields(
            _cert("HackerRank certificates"),
            _who(links=["https://www.hackerrank.com/certificates/a1b2c3d4e5"]),
        )
        == []
    )
    assert (
        identifying_fields(
            _entry("Kaggle competitions"),
            _who(links=["https://www.kaggle.com/competitions/titanic"]),
        )
        == []
    )
    assert _who(links=["https://leetcode.com/mariorossi"]).handles == ["mariorossi"]


def test_a_gist_names_its_user_not_gist() -> None:
    who = _who(links=["https://gist.github.com/mariorossi"])
    assert identifying_fields(_entry("Snippet condivisi come gist"), who) == []
    assert identifying_fields(_entry("Gist di mariorossi"), who) == ENTRY


def test_google_is_skipped_only_as_the_domain() -> None:
    """`scholar.google.co.uk` is Google's; `google.mariorossi.dev` is the person's own."""
    assert (
        identifying_fields(_entry("Blog mariorossi"), _who(links=["https://google.mariorossi.dev"]))
        == ENTRY
    )
    for link in ("https://scholar.google.co.uk/citations", "https://sites.google.com/view/x"):
        assert identifying_fields(_entry("Google Cloud"), _who(links=[link])) == [], link


def test_an_underscore_slug_outside_prose() -> None:
    assert identifying_fields(_cert("Credly: alex_cloud"), _who("Alex", "Rossi")) == [
        "certifications[0]"
    ]


# ---- tax identifiers (C2): a tax code, a VAT number or an IBAN names one person ----

NOBODY = who_of(first_name=None, last_name=None, emails=[], phones=[], links=[])


def _bullet(text: str) -> AnonymousCv:
    cv = CV.model_copy(deep=True)
    cv.experience[1].points[0].text = text
    return cv


def test_to_labelled_vat_number_is_refused() -> None:
    for text in (
        "Freelance con P.IVA 01234567890",
        "Freelance, P. IVA: 0123 456 7890.",
        "Partita IVA n. 01234567890",
        "VAT IT01234567890",
        "Iscritto all'albo, IVA 01234567890",
        "CF 01234567890 (ditta individuale)",
        "codice fiscale: 01234567890",
    ):
        assert identifying_fields(_bullet(text), NOBODY) == ["experience[1].points[0].text"], text


def test_to_tax_code_is_refused_anywhere() -> None:
    assert identifying_fields(_bullet("C.F. MSSMRA85M01F158K"), NOBODY) == [
        "experience[1].points[0].text"
    ]
    # Its shape alone, in any case, in a place too.
    cv = CV.model_copy(deep=True)
    cv.experience[0].location = "Messina mssmra85m01f158k"
    assert identifying_fields(cv, NOBODY) == ["experience[0].location"]


def test_to_known_vat_number_is_refused_without_to_label() -> None:
    who = who_of(
        first_name=None, last_name=None, emails=[], phones=[], links=[], tax_ids=["01234567890"]
    )
    assert identifying_fields(_bullet("Rif. 012 3456 7890"), who) == [
        "experience[1].points[0].text"
    ]
    cv = CV.model_copy(deep=True)
    cv.experience[0].location = "Messina 01234567890"
    assert identifying_fields(cv, who) == ["experience[0].location"]


def test_to_known_tax_code_written_spaced_is_refused() -> None:
    who = who_of(
        first_name=None,
        last_name=None,
        emails=[],
        phones=[],
        links=[],
        tax_ids=["mss mra 85m01 f158k"],
    )
    assert who.tax_ids == ["MSSMRA85M01F158K"]
    assert identifying_fields(_bullet("Codice MSS MRA 85M01 F158K"), who) == [
        "experience[1].points[0].text"
    ]


def test_words_about_to_vat_number_pass() -> None:
    assert identifying_fields(_bullet("Partita IVA attiva dal 2019"), NOBODY) == []
    assert identifying_fields(_bullet("Libero professionista con P.IVA dal 2019"), NOBODY) == []


def test_an_unlabelled_unknown_number_passes() -> None:
    who = who_of(
        first_name=None, last_name=None, emails=[], phones=[], links=[], tax_ids=["01234567890"]
    )
    assert identifying_fields(_bullet("Ordine di lavoro 98765432109 consegnato"), who) == []


def test_an_iban_is_refused() -> None:
    for text in (
        "Pagamenti su IT60X0542811101000000123456",
        "Pagamenti su IT60 X054 2811 1010 0000 0123 456",
        "IBAN: DE89 3704 0044 0532 0130 00",
    ):
        assert identifying_fields(_bullet(text), NOBODY) == ["experience[1].points[0].text"], text
    # A code shaped like a foreign IBAN with no «IBAN» before it is a code.
    assert identifying_fields(_bullet("Progetto DE89 3704 0044 0532"), NOBODY) == []


def test_tax_ids_are_merged_once() -> None:
    one = who_of(
        first_name=None, last_name=None, emails=[], phones=[], links=[], tax_ids=["01234567890"]
    )
    two = who_of(
        first_name=None,
        last_name=None,
        emails=[],
        phones=[],
        links=[],
        tax_ids=[" 01234567890", None],
    )
    assert one.merged(two).tax_ids == ["01234567890"]


# ---- the date and place of birth (I3) ------------------------------------------------------


def test_a_birth_statement_is_refused() -> None:
    for text in (
        "Nato il 3 marzo 1985",
        "nata il 03/03/1985 a Messina",
        "Nato a Messina",
        "nata ad Ancona nel 1990",
        "Data di nascita: 3.3.1985",
        "Date of birth: March 3, 1985",
        "Born on 3 March 1985",
        "born in Rome",
        "Born in the UK in 1985",
        "DOB 1985-03-03",
        "Luogo di nascita: Palermo",
    ):
        assert identifying_fields(_bullet(text), NOBODY) == ["experience[1].points[0].text"], text
    # In a place too.
    cv = CV.model_copy(deep=True)
    cv.experience[0].location = "Messina (nato il 3/3/1985)"
    assert identifying_fields(cv, NOBODY) == ["experience[0].location"]


def test_words_about_being_born_pass() -> None:
    for text in (
        "Una piattaforma nata a supporto dei team di vendita",
        "Il servizio è nato in un hackathon",
        "Born in the cloud: un'architettura serverless",
        "Il progetto è nato il giorno in cui il cliente ha chiesto un'app",
        "Il progetto è nato nel 2019",
        "Un team nato nel 2020 a Torino",
    ):
        assert identifying_fields(_bullet(text), NOBODY) == [], text


def test_nato_the_alliance_is_not_a_birth_label() -> None:
    for text in (
        "Consulente per la NATO a Bruxelles",
        "Progetti NATO in Belgio",
        "Worked for NATO in Brussels",
        "Joined NATO in 2019 as a contractor",
    ):
        assert identifying_fields(_bullet(text), NOBODY) == [], text
    cv = CV.model_copy(deep=True)
    cv.experience[0].location = "NATO in Brussels"
    assert identifying_fields(cv, NOBODY) == []
    for text in ("Nato a Messina il 3 marzo 1985", "nata il 01/08/1985"):
        assert identifying_fields(_bullet(text), NOBODY) == ["experience[1].points[0].text"], text


def test_a_birth_statement_in_capitals_or_after_a_comma_is_refused() -> None:
    for text in (
        "NATA A MESSINA IL 01/08/1985",
        "NATO IL 3 MARZO 1985",
        "NATA NEL 1990",
        "NATO NEL 1985",
        "Ingegnere, nato nel 1985",
        "(nata nel 1990)",
    ):
        assert identifying_fields(_bullet(text), NOBODY) == ["experience[1].points[0].text"], text


def test_birth_and_tax_leak_gaps_are_closed() -> None:
    for text in (
        "P.I. 01234567890",
        "P. I. 01234567890",
        "Born: 3 March 1985",
        "Born 3 March 1985",
        "Nato nel 1985 a Messina",
        "nata nel 1990",
        "Nato nel 1985 a Messina",
        "Profilo. Nata nel 1990",
        "Italiana | Nato nel 1985",
        "MSSMRA85M01F15LK",
    ):
        assert identifying_fields(_bullet(text), NOBODY) == ["experience[1].points[0].text"], text
    assert identifying_fields(_bullet("Born and raised in a DevOps culture"), NOBODY) == []
