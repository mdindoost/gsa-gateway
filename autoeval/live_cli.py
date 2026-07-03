# autoeval/live_cli.py
import sys
from autoeval.config import load_config
from autoeval.store import Store
from autoeval.resilience import read_status
from autoeval.live import format_status, format_tail

def _latest_run(store):
    row = store.conn.execute("SELECT MAX(run_id) AS r FROM runs").fetchone()
    return row["r"] if row and row["r"] else None

def main():
    cfg = load_config(); store = Store(cfg.autoeval_db); store.init_schema()
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    run_id = _latest_run(store)
    rows = store.results_for_run(run_id) if run_id else []
    if action == "tail":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        print(format_tail([r for r in rows if r.get("result")][-n:]))
    else:
        counts = {"total": len([r for r in rows if r.get("result")]),
                  "pass": sum(1 for r in rows if r.get("result") == "pass"),
                  "fabrication": sum(1 for r in rows if r.get("failure_class") == "fabrication")}
        print(format_status(read_status(cfg.status_file), counts))

if __name__ == "__main__":
    main()
