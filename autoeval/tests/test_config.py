import os
import pytest
from autoeval.config import assert_env, load_config, REQUIRED_ENV

def test_assert_env_passes_when_all_correct(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    assert_env()  # no raise

def test_assert_env_fails_on_wrong_flag(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ROUTER_V21_SHADOW", "1")  # wrong
    with pytest.raises(RuntimeError, match="ROUTER_V21_SHADOW"):
        assert_env()

def test_load_config_defaults():
    cfg = load_config()
    assert cfg.arm_counts["answer"] == 3
    assert cfg.arm_counts["out_of_scope"] == 2
    assert abs(sum(cfg.sampler_mix.values()) - 1.0) < 1e-6
    assert cfg.concurrency == 1
    assert cfg.autoeval_db.endswith("autoeval.db")
