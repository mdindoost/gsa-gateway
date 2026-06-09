# ⚠️ Post-World-Cup cleanup — DO THIS AFTER THE TOURNAMENT (not before)

The World Cup 2026 starts **June 11** and runs to **~July 19, 2026**. The live
World Cup tracker is **v1** code. Until the tournament is over (and ideally until
a v2-native tracker has been built and validated), the items below are
**deferred**. Tier 1 cleanup (dev `.db` artifacts, `node_modules/`, `.gitignore`,
`MANUAL.md → docs/`) was already done on 2026-06-08.

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

## The v1 → v2 tracker migration (the real "move everything to v2")
"Retiring v1" means first **building a v2 bot runtime** that reimplements what
only exists in v1 today: the `on_message` chat/intent pipeline, all 11 slash
commands, **MathCafe**, and the **World Cup tracker**. That is a multi-week
project, not a folder reshuffle.

For the World Cup tracker specifically (see `LOCAL_SERVER.md`/connector pattern):
- v2 already owns the **output** (`ConnectorRegistry` → Discord+Telegram, logged,
  dashboard-visible). The only event-specific part is **detection** (poll the
  API, decide "a goal happened").
- Build `v2/integration/live_tracker.py` — a poll loop that calls
  `registry.publish(Post(...))` per detected event. Use v1's
  `football_client.py` / `worldcup_tracker.py` as the blueprint.
- **Validate it in shadow mode** against real matches (read the feed, post to a
  private test channel / just log) BEFORE promoting it. Then retire v1's tracker.
- 👉 **During the 2026 tournament, capture `gsa_gateway.log` match-day output** —
  that real data is what you'll test the v2 tracker against later.

## Rollback (always available)
`V2_RETRIEVER_ENABLED=false` + `V2_SCHEDULER_ENABLED=false` in `.env`, then
`bash scripts/restart.sh` → full v1 behavior in ~30s.
