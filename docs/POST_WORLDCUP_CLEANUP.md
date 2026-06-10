# Post-World-Cup cleanup notes

> **Update 2026-06-10:** The World Cup tracker is v2 (`v2/integration/worldcup_runner.py`)
> and now publishes through the standard generator contract (`enqueue_post`). The
> tournament-specific deferral below is obsolete; the rollback/cleanup notes that
> follow are retained because they are not World-Cup-specific.

## 🚫 Do NOT delete these — they are load-bearing, not junk
- **`chroma_db/`** — the v1 retriever's vector store. It is your **rollback**:
  set `V2_RETRIEVER_ENABLED=false` + `restart.sh` and v1 loads it. Keep until v1
  is formally retired.
- **`gsa_gateway.db.backup_*`** — the migration safety nets ("restore in 5s").
  Keep until v2 has run clean for a good while.
- **`run_telegram.py`** — a live process (the Telegram bot).
- **`gsa_gateway.db`** (+ `-wal`/`-shm`) — the live database the bots have open.
- The whole **`bot/`** tree — v2 is wired *into* the v1 bot; it is not a
  standalone replacement. The running process is `python -m bot.main` (v1).

## Tier 2 — after the next planned restart (low risk)
- [ ] Move `run_telegram.py` → `bot/run_telegram.py`.
- [ ] Update `scripts/restart.sh` (it `pkill`/`nohup`s `python run_telegram.py`)
      and the systemd unit (`scripts/gsa-telegram.service`) to the new path.
- [ ] `restart.sh` and confirm both bots come back up.

## Tier 3 — after v1 is retired / v2 is permanent (higher blast radius)
Only once you've decided v2 is the permanent system and you can take the bots
down for a tested change:
- [ ] Move the live DB + `chroma_db/` into a `data/` dir. This touches **every**
      hard-coded path: bot config, `v2/local_server.py` (`DB_PATH`), the migrate
      scripts, `restart.sh`, `.env`, the systemd units. Do it as its own tested
      change with the bots stopped — never casually.
- [ ] Archive (then remove) the `*.backup_*` files.
- [ ] Retire `chroma_db/` **only after** the v1 retriever is gone.
- [ ] Review/relocate `TEMP CONTENT/` (old website HTML — `2025/2026` event pages).

## Rollback (always available)
`V2_RETRIEVER_ENABLED=false` + `V2_SCHEDULER_ENABLED=false` in `.env`, then
`bash scripts/restart.sh` → full v1 behavior in ~30s.
