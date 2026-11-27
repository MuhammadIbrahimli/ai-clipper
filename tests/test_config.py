from pathlib import Path

import pytest

from clipper.core.config import ConfigError, load_campaign

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_campaign_loads():
    c = load_campaign(FIXTURES / "campaign_ok.yaml")
    assert c.id == "demo"
    assert c.limits.per_day == 2
    assert c.clip.min_s == 30 and c.clip.max_s == 55


def test_invalid_campaign_names_field():
    with pytest.raises(ConfigError, match="поле 'mode'"):
        load_campaign(FIXTURES / "campaign_bad.yaml")
