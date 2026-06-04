#!/usr/bin/env bash
# Start both GSA Gateway bots (Discord + Telegram) with logging.
# Usage: bash scripts/run_bot.sh
# Ctrl+C stops both.

set -euo pipefail

if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

if [ ! -f ".env" ]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in your tokens."
    exit 1
fi

# Start Telegram bot in background
echo "Starting Telegram bot...   (logs: telegram_bot.log)"
python run_telegram.py &
TG_PID=$!

# Kill Telegram on exit (Ctrl+C, script exit, or error)
trap "echo ''; echo 'Stopping both bots...'; kill $TG_PID 2>/dev/null; exit" INT TERM EXIT

# Start Discord bot in foreground
echo "Starting Discord bot...    (logs: gsa_gateway.log)"
echo "(Ctrl+C to stop both)"
python -m bot.main
