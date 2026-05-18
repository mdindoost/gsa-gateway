# Deployment Guide

## Local Deployment (Current)

### Prerequisites
- Python 3.11+
- A Discord bot token (see README for setup)
- Ports: none needed (bot connects outbound only)

### Steps
```bash
cd gsa-gateway
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # then edit .env
python scripts/init_db.py
bash scripts/run_bot.sh
```

The bot runs until the terminal is closed or Ctrl+C. For persistence, wrap in `screen`, `tmux`, or systemd.

### systemd (recommended for always-on)
Create `/etc/systemd/system/gsa-gateway.service`:
```ini
[Unit]
Description=GSA Gateway Discord Bot
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/path/to/gsa-gateway
ExecStart=/path/to/gsa-gateway/.venv/bin/python -m bot.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Then: `sudo systemctl enable --now gsa-gateway`

---

## GitHub Pages (Website)

1. Push the `website/` folder contents to a `gh-pages` branch (or configure Pages from `main/website/`).
2. In GitHub repo → Settings → Pages → Source: `main` branch, `/website` folder.
3. The site will be available at `https://yourusername.github.io/gsa-gateway/`.
4. Update `events.json` by running `python scripts/export_events_json.py` and pushing the result.

---

## Migration to NJIT Department Server

When moving from the local machine to an NJIT server:

### Pre-migration checklist
- [ ] Get SSH access to the target server (request through NJIT IT or your department)
- [ ] Confirm Python 3.11+ is available or can be installed
- [ ] Confirm outbound HTTPS is allowed (Discord API uses port 443)
- [ ] Back up `gsa_gateway.db`

### Migration steps
1. **Copy files**: `scp -r gsa-gateway/ user@njit-server:~/`
2. **Copy database**: `scp gsa_gateway.db user@njit-server:~/gsa-gateway/`
3. **Copy .env**: `scp .env user@njit-server:~/gsa-gateway/` (never commit .env to git)
4. **Install dependencies**: SSH in, activate venv, `pip install -r requirements.txt`
5. **Test**: Run `python scripts/init_db.py` (safe to re-run), then `pytest bot/tests/ -v`
6. **Start**: Use systemd on the server, or `screen`/`tmux` for a quick test
7. **Verify**: Confirm slash commands still work in Discord

### If Ollama is used
Install Ollama on the NJIT server separately: `curl -fsSL https://ollama.com/install.sh | sh`
Then: `ollama pull llama3`
Ollama listens on `localhost:11434` by default — no external exposure needed.

### Keeping the database synced (if testing on both machines)
Do not run two bot instances simultaneously against the same Discord guild — duplicate responses will occur. Shut down the old instance before starting the new one.
