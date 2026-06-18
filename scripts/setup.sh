#!/usr/bin/env bash
# Full setup automation for GSA Gateway.
# Run once after cloning: bash scripts/setup.sh

set -euo pipefail

echo "=== GSA Gateway Setup ==="

# 1. Check Python version
python_version=$(python3 --version 2>&1)
echo "Python: $python_version"
if ! python3 -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
    echo "ERROR: Python 3.11+ required."
    exit 1
fi

# 2. Create virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate

# 3. Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "Dependencies installed."

# 4. Copy .env if missing
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "*** ACTION REQUIRED ***"
    echo "Edit .env and set DISCORD_TOKEN and DISCORD_GUILD_ID before starting the bot."
    echo ""
fi

# 5. Initialise database
echo "Initialising database..."
python scripts/init_db.py

# 6. Export initial events JSON
echo "Exporting events to website/data/events.json..."
python scripts/export_events_json.py

# 7. Run tests
echo ""
echo "Running tests..."
pytest bot/tests/ -v

echo ""
echo "=== Setup complete! ==="
echo "Next steps:"
echo "  1. Edit .env — add DISCORD_TOKEN and DISCORD_GUILD_ID"
echo "  2. Run: bash scripts/restart.sh"
