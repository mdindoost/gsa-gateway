from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path("/home/md724/gsa-gateway")

# Env vars the harness REQUIRES (read at import time by bot.config / message_handler).
# Values are the harness-correct settings; the launcher exports them before python starts.
REQUIRED_ENV = {
    "ROUTER_V21": "1",            # else unified_router is None -> runner AttributeError
    "ROUTER_V21_SHADOW": "0",     # else handle() ignores decide() + writes shared shadow log
    "LIVE_ENABLED": "0",          # module constant; must be set pre-import (zero external footprint)
    "ROUTER_V21_SLOT_RECOVERY": "0",  # deterministic captured route
}

def assert_env() -> None:
    wrong = {k: (os.environ.get(k), v) for k, v in REQUIRED_ENV.items()
             if os.environ.get(k) != v}
    if wrong:
        lines = [f"  {k}={got!r} (must be {want!r})" for k, (got, want) in wrong.items()]
        raise RuntimeError(
            "autoeval required env not set correctly (export via scripts/autoeval.sh):\n"
            + "\n".join(lines))

@dataclass
class AutoEvalConfig:
    repo_root: Path = REPO_ROOT
    prod_db: str = str(REPO_ROOT / "gsa_gateway.db")
    snapshot_dir: str = str(REPO_ROOT / "autoeval" / "snapshots")
    autoeval_db: str = str(REPO_ROOT / "autoeval" / "autoeval.db")
    status_file: str = str(REPO_ROOT / "autoeval" / "status.json")
    sampler_mix: dict = field(default_factory=lambda: {
        "person": 0.50, "org": 0.20, "area": 0.15, "chunk": 0.15})
    arm_counts: dict = field(default_factory=lambda: {"answer": 3, "out_of_scope": 2})
    concurrency: int = 1
    staleness_days: int = 7
    live_enabled: bool = False
    codex_model: str | None = None  # None -> codex default

def load_config() -> AutoEvalConfig:
    cfg = AutoEvalConfig()
    Path(cfg.snapshot_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.autoeval_db).parent.mkdir(parents=True, exist_ok=True)
    return cfg
