#!/usr/bin/env bash
# Start the GSA Gateway bots (Discord + Telegram, plus GroupMe when enabled) with logging.
# Usage: bash scripts/run_bot.sh
# Ctrl+C stops all of them.

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

# Start GroupMe bot in background (only when enabled in .env)
GM_PID=""
if grep -qi '^GROUPME_ENABLED=true' .env 2>/dev/null; then
    echo "Starting GroupMe bot...    (logs: groupme_bot.log)"
    python run_groupme.py &
    GM_PID=$!
fi

# Kill background bots on exit (Ctrl+C, script exit, or error)
trap "echo ''; echo 'Stopping bots...'; kill $TG_PID ${GM_PID} 2>/dev/null; exit" INT TERM EXIT

# Start Discord bot in foreground
echo "Starting Discord bot...    (logs: gsa_gateway.log)"
echo "(Ctrl+C to stop both)"
python -m bot.main
