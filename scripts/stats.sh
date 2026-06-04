#!/usr/bin/env bash
# stats.sh — GSA Gateway usage overview (Discord + Telegram)
# Usage:
#   bash scripts/stats.sh              # full overview
#   bash scripts/stats.sh --today      # today only
#   bash scripts/stats.sh --week       # last 7 days
#   bash scripts/stats.sh --platform telegram   # one platform only
#   bash scripts/stats.sh --questions  # show recent questions

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

cd "$(dirname "$0")/.."

DB="gsa_gateway.db"
if [ ! -f "$DB" ]; then
    echo "ERROR: $DB not found. Run from repo root or check DATABASE_PATH."
    exit 1
fi

q() { sqlite3 "$DB" "$1"; }

# ── Parse flags ───────────────────────────────────────────────────────────────
FILTER_DATE=""
FILTER_PLATFORM=""
SHOW_QUESTIONS=false

for arg in "$@"; do
    case "$arg" in
        --today)    FILTER_DATE="AND DATE(timestamp) = DATE('now')" ;;
        --week)     FILTER_DATE="AND DATE(timestamp) >= DATE('now', '-7 days')" ;;
        --platform) ;;
        telegram|discord) FILTER_PLATFORM="AND platform = '$arg'" ;;
        --questions) SHOW_QUESTIONS=true ;;
    esac
done
# Handle --platform telegram/discord as a pair
if [[ "${1:-}" == "--platform" && -n "${2:-}" ]]; then
    FILTER_PLATFORM="AND platform = '${2}'"
fi

WHERE="WHERE 1=1 $FILTER_DATE $FILTER_PLATFORM"

echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${BOLD}  GSA Gateway Stats — $(date '+%Y-%m-%d %H:%M')${NC}"
[[ -n "$FILTER_DATE" ]] && echo -e "  Filter: $FILTER_DATE"
[[ -n "$FILTER_PLATFORM" ]] && echo -e "  Platform: $FILTER_PLATFORM"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"

# ── 1. Questions by platform ──────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[ Questions by Platform ]${NC}"

DISCORD_Q=$(q "SELECT COUNT(*) FROM questions WHERE platform='discord' $FILTER_DATE;")
TELEGRAM_Q=$(q "SELECT COUNT(*) FROM questions WHERE platform='telegram' $FILTER_DATE;")
TOTAL_Q=$(q "SELECT COUNT(*) FROM questions $WHERE;")

echo -e "  Discord:   ${GREEN}${DISCORD_Q}${NC} questions"
echo -e "  Telegram:  ${GREEN}${TELEGRAM_Q}${NC} questions"
echo -e "  Total:     ${BOLD}${TOTAL_Q}${NC}"

# ── 2. Unique users by platform ───────────────────────────────────────────────
echo ""
echo -e "${CYAN}[ Unique Users ]${NC}"

DISCORD_U=$(q "SELECT COUNT(DISTINCT user_id_hash) FROM questions WHERE platform='discord' $FILTER_DATE;")
TELEGRAM_U=$(q "SELECT COUNT(DISTINCT user_id_hash) FROM questions WHERE platform='telegram' $FILTER_DATE;")

echo -e "  Discord:   ${GREEN}${DISCORD_U}${NC} unique users"
echo -e "  Telegram:  ${GREEN}${TELEGRAM_U}${NC} unique users"

# ── 3. Daily breakdown (last 14 days) ─────────────────────────────────────────
echo ""
echo -e "${CYAN}[ Daily Activity — Last 14 Days ]${NC}"
printf "  %-12s  %10s  %10s  %6s\n" "Date" "Discord" "Telegram" "Total"
printf "  %-12s  %10s  %10s  %6s\n" "------------" "----------" "----------" "------"

q "SELECT
    DATE(timestamp) as day,
    SUM(CASE WHEN platform='discord'  THEN 1 ELSE 0 END) as disc,
    SUM(CASE WHEN platform='telegram' THEN 1 ELSE 0 END) as tg,
    COUNT(*) as total
   FROM questions
   WHERE DATE(timestamp) >= DATE('now', '-14 days')
   GROUP BY day
   ORDER BY day DESC;" | while IFS='|' read -r day disc tg total; do
    printf "  %-12s  %10s  %10s  %6s\n" "$day" "$disc" "$tg" "$total"
done

# ── 4. Top topics ─────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[ Top Topics $([ -n "$FILTER_PLATFORM" ] && echo "(${2:-})" || echo "(all platforms)") ]${NC}"
printf "  %-40s  %6s\n" "Topic" "Count"
printf "  %-40s  %6s\n" "----------------------------------------" "------"

q "SELECT matched_topic, COUNT(*) as cnt
   FROM questions $WHERE
   AND matched_topic IS NOT NULL
   GROUP BY matched_topic
   ORDER BY cnt DESC
   LIMIT 10;" | while IFS='|' read -r topic cnt; do
    printf "  %-40s  %6s\n" "${topic:0:40}" "$cnt"
done

# ── 5. Process status ─────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[ Bot Status ]${NC}"

DISCORD_PID=$(pgrep -f "python.*bot.main" 2>/dev/null | head -1 || true)
TELEGRAM_PID=$(pgrep -f "python.*run_telegram" 2>/dev/null | head -1 || true)

if [[ -n "$DISCORD_PID" ]]; then
    echo -e "  Discord:   ${GREEN}running${NC} (PID $DISCORD_PID)"
else
    echo -e "  Discord:   ${YELLOW}NOT running${NC}"
fi

if [[ -n "$TELEGRAM_PID" ]]; then
    echo -e "  Telegram:  ${GREEN}running${NC} (PID $TELEGRAM_PID)"
else
    echo -e "  Telegram:  ${YELLOW}NOT running${NC}"
fi

# ── 6. Recent questions (optional) ────────────────────────────────────────────
if $SHOW_QUESTIONS; then
    echo ""
    echo -e "${CYAN}[ Recent Questions (last 20) ]${NC}"
    printf "  %-10s  %-10s  %-55s\n" "Date" "Platform" "Question"
    printf "  %-10s  %-10s  %-55s\n" "----------" "----------" "-------------------------------------------------------"

    q "SELECT DATE(timestamp), platform, question_text
       FROM questions $WHERE
       ORDER BY timestamp DESC
       LIMIT 20;" | while IFS='|' read -r day plat question; do
        printf "  %-10s  %-10s  %-55s\n" "$day" "$plat" "${question:0:55}"
    done
fi

# ── 7. Log tail ───────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[ Last 5 Log Lines ]${NC}"
echo -e "  ${BLUE}Discord (gsa_gateway.log):${NC}"
tail -5 gsa_gateway.log 2>/dev/null | sed 's/^/    /' || echo "    (no log file)"
echo -e "  ${BLUE}Telegram (telegram_bot.log):${NC}"
tail -5 telegram_bot.log 2>/dev/null | sed 's/^/    /' || echo "    (no log file)"

echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "  Flags: --today  --week  --platform discord|telegram  --questions"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo ""
