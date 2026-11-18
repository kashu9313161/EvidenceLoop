import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml"


def test_config_defines_light_and_dark_themes():
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    theme = config["theme"]

    # Both sections are what enable Streamlit's Light / Dark / System toggle.
    for mode in ("light", "dark"):
        assert theme[mode]["backgroundColor"]
        assert theme[mode]["textColor"]
        assert len(theme[mode]["chartCategoricalColors"]) >= 8
    assert theme["light"]["backgroundColor"] != theme["dark"]["backgroundColor"]


def test_toolbar_keeps_settings_menu():
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["client"]["toolbarMode"] != "minimal"
    assert config["server"]["maxUploadSize"] == 100
