"""A campaign's list: templates, candidates, exclusions, the action already done."""

from rebase_core.campaigns.states import JOURNEY_STATES, PHASE_ONE_STATES
from rebase_core.campaigns.templates import STATE_TEMPLATES
from rebase_core.models import CAMPAIGN_ACTIONS, CAMPAIGN_DESTINATIONS


def test_every_phase_one_state_has_a_template_that_fits_the_columns() -> None:
    assert set(STATE_TEMPLATES) == set(PHASE_ONE_STATES)
    for key, template in STATE_TEMPLATES.items():
        assert template.etichetta == JOURNEY_STATES[key]
        assert template.azione in CAMPAIGN_ACTIONS and template.azione != "pigro_cliente"
        assert template.bottone_meta in CAMPAIGN_DESTINATIONS and template.bottone_meta != "pigro"
        assert template.testo.startswith("Ciao {nome},")
        assert len(template.oggetto) <= 200 and len(template.bottone_testo) <= 60
