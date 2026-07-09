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


def test_retired_leaderboard_roster_flag_gone():
    # the multi-view leaderboard always shows the full roster; the old toggle is retired
    with pytest.raises(KeyError):
        config.flag("LEADERBOARD_ROSTER")


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


def test_paths_ssot_matches_multicollege_layout():
    # Multi-college nested layout (Spec A): profiles flat, leaderboards nested under college,
    # NJIT hub at root, college hub at /<college>/.
    assert paths.profile_path("/out", "koutis") == "/out/p/koutis.html"
    assert paths.leaderboard_path("/out", "ywcc", "computer-science") == "/out/ywcc/computer-science/index.html"
    assert paths.college_hub_path("/out", "ywcc") == "/out/ywcc/index.html"
    assert paths.njit_hub_path("/out") == "/out/index.html"
    assert paths.redirect_path("/out", "cs") == "/out/cs/index.html"
    assert paths.assets_dir("/out") == "/out/assets"
