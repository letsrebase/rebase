"""The API does not exist on the published placeholder signing key (PCORE-B-05)."""

import pytest

from pigrocrm.core.config import DEFAULT_JWT_SECRET, get_settings
from pigrocrm_api.main import create_app


def test_building_the_app_on_a_placeholder_secret_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIGROCRM_JWT_SECRET", DEFAULT_JWT_SECRET)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="PIGROCRM_JWT_SECRET"):
            create_app()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
