"""Words for the team builder and the talent cloud (REB-509): the labels an admin's
«Richieste team» page and a talent's own answer page show for a request's state, where
it came from, and what a talent answered. The same reasoning `match_words.py` gives for
its own pair of maps: the word lives here once, the web copies it, and
`test_web_labels.py` holds the copy equal rather than letting the two drift apart a
character at a time.
"""

TEAM_REQUEST_STATE_LABELS = {"nuova": "Nuova", "contattata": "Contattata", "chiusa": "Chiusa"}
TEAM_ORIGIN_LABELS = {"pubblico": "Pubblico", "cloud": "Cloud", "admin": "Admin"}
TALENT_ANSWER_LABELS = {"si": "Sì", "no": "No"}
# A talent who has the availability mail and has not answered yet (REB-517); one who was
# never mailed has no word at all, and the page shows «—».
TALENT_WAITING_LABEL = "In attesa"
