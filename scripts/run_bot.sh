#!/usr/bin/env bash
# Start the GSA Gateway bot with logging.
# Usage: bash scripts/run_bot.sh

set -euo pipefail

# Activate virtual environment if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Verify .env exists
if [ ! -f ".env" ]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in DISCORD_TOKEN."
    exit 1
fi

echo "Starting GSA Gateway bot... (Ctrl+C to stop)"
echo "Logs: gsa_gateway.log"
python -m bot.main
