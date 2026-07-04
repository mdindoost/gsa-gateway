from facultyfolio import config


def test_config_constants():
    assert config.CS_ORG_ID == 16
    assert config.KOUTIS_NODE == 33
    assert config.FIXED_HEADING == "Impact & trajectory"
    assert config.ACTIVE_SINCE_LABEL == "Active since"
    assert isinstance(config.SUPPRESSED, (set, frozenset))
    assert config.OUT_ROOT.endswith("Faculty-Folio")
    assert config.DB_PATH.endswith("gsa_gateway.db")


def test_sync_label():
    assert config.sync_label("2026-06-30") == "Synced 30 Jun 2026"
    assert config.sync_label("") == ""
    assert config.sync_label(None) == ""
