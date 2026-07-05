"""Task 0 — display-mode flags scaffold + output-path SSOT."""
import pytest
from facultyfolio import config, paths


def test_flag_defaults():
    # SOCIAL_ICONS and ABOUT_ROWS are the Fixed pilots; the rest keep the Adaptive default
    assert config.flag("SOCIAL_ICONS") == "Fixed"
    assert config.flag("ABOUT_ROWS") == "Fixed"
    assert config.flag("SCHOLAR_METRICS") == "Adaptive"
    assert config.flag("PUBLICATIONS") == "Adaptive"
    assert config.flag("NAV") == "Adaptive"
    assert config.flag("LEADERBOARD_ROSTER") == "Adaptive"


def test_flag_env_override(monkeypatch):
    monkeypatch.setenv("FACULTYFOLIO_SOCIAL_ICONS", "Adaptive")
    assert config.flag("SOCIAL_ICONS") == "Adaptive"


def test_flag_rejects_unknown_name():
    with pytest.raises(KeyError):
        config.flag("NOPE")


def test_flag_rejects_bad_value(monkeypatch):
    monkeypatch.setenv("FACULTYFOLIO_SOCIAL_ICONS", "sometimes")
    with pytest.raises(ValueError):
        config.flag("SOCIAL_ICONS")


def test_paths_ssot_matches_current_layout():
    # Task-0 paths.py must reproduce the CURRENT flat layout byte-for-byte (no visual change)
    assert paths.profile_path("/out", "koutis") == "/out/p/koutis.html"
    assert paths.leaderboard_path("/out") == "/out/cs/index.html"
    assert paths.assets_dir("/out") == "/out/assets"
