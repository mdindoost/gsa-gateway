#!/usr/bin/env bash
# stats.sh — GSA Gateway usage overview (Discord + Telegram)
# Usage:
#   bash scripts/stats.sh              # full overview
#   bash scripts/stats.sh --today      # today only
#   bash scripts/stats.sh --week       # last 7 days
#   bash scripts/stats.sh --platform telegram   # one platform only
#   bash scripts/stats.sh --questions  # show recent questions
#   bash scripts/stats.sh --feedback   # feedback ratings only
#   bash scripts/stats.sh --gaps       # full gap analysis report

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

cd "$(dirname "$0")/.."

DB="gsa_gateway.db"
if [ ! -f "$DB" ]; then
    echo "ERROR: $DB not found. Run from repo root or check DATABASE_PATH."
    exit 1
fi

q() { sqlite3 "$DB" "$1"; }

# Check whether the response_feedback table exists (created on first bot restart
# after this update).  All feedback queries are guarded by this flag.
HAS_FEEDBACK=$(q "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='response_feedback';")

# ── Parse flags ───────────────────────────────────────────────────────────────
FILTER_DATE=""
FILTER_PLATFORM=""
SHOW_QUESTIONS=false
SHOW_FEEDBACK_ONLY=false
SHOW_GAPS_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --today)     FILTER_DATE="AND DATE(timestamp) = DATE('now')" ;;
        --week)      FILTER_DATE="AND DATE(timestamp) >= DATE('now', '-7 days')" ;;
        --platform)  ;;
        telegram|discord) FILTER_PLATFORM="AND platform = '$arg'" ;;
        --questions) SHOW_QUESTIONS=true ;;
        --feedback)  SHOW_FEEDBACK_ONLY=true ;;
        --gaps)      SHOW_GAPS_ONLY=true ;;
    esac
done
# Handle --platform telegram/discord as a pair
if [[ "${1:-}" == "--platform" && -n "${2:-}" ]]; then
    FILTER_PLATFORM="AND platform = '${2}'"
fi

WHERE="WHERE 1=1 $FILTER_DATE $FILTER_PLATFORM"

# ── Gap-only mode ─────────────────────────────────────────────────────────────
if $SHOW_GAPS_ONLY; then
    echo ""
    echo -e "${BOLD}══════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  GSA Gateway — Gap Analysis (last 30 days)${NC}"
    echo -e "${BOLD}══════════════════════════════════════════════${NC}"

    echo ""
    echo -e "${CYAN}[ Coverage ]${NC}"
    TOTAL_Q=$(q "SELECT COUNT(*) FROM questions WHERE timestamp >= datetime('now','-30 days');")
    ANSWERED=$(q "SELECT COUNT(*) FROM questions WHERE timestamp >= datetime('now','-30 days') AND matched_topic IS NOT NULL AND confidence >= 60;")
    if [ "$TOTAL_Q" -gt 0 ]; then
        RATE=$(awk "BEGIN { printf \"%.1f\", ($ANSWERED/$TOTAL_Q)*100 }")
    else
        RATE="0.0"
    fi
    echo -e "  Answered: ${GREEN}${ANSWERED}/${TOTAL_Q}${NC} (${BOLD}${RATE}%${NC})"

    echo ""
    echo -e "${CYAN}[ Satisfaction ]${NC}"
    if [ "$HAS_FEEDBACK" = "1" ]; then
        UP=$(q "SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_up';")
        DOWN=$(q "SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_down';")
        RETRY=$(q "SELECT COUNT(*) FROM response_feedback WHERE rating='regenerate';")
        TOTAL_RATED=$((UP + DOWN))
        if [ "$TOTAL_RATED" -gt 0 ]; then
            SAT=$(awk "BEGIN { printf \"%.1f\", ($UP/$TOTAL_RATED)*100 }")
        else
            SAT="n/a"
        fi
        echo -e "  👍 ${GREEN}${UP}${NC}  👎 ${RED}${DOWN}${NC}  🔄 ${YELLOW}${RETRY}${NC}  Satisfaction: ${BOLD}${SAT}%${NC}"
    else
        echo -e "  ${YELLOW}(feedback table not yet created — restart the bot once)${NC}"
    fi

    echo ""
    echo -e "${CYAN}[ Top 20 Gaps — Priority = (asked×2) + (👎×3) + (1−conf/100)×5 ]${NC}"
    printf "  %-6s  %-5s  %-4s  %-6s  %-55s\n" "Score" "Asked" "👎" "Conf" "Question"
    printf "  %-6s  %-5s  %-4s  %-6s  %-55s\n" "------" "-----" "----" "------" "-------------------------------------------------------"

    if [ "$HAS_FEEDBACK" = "1" ]; then
        FB_JOIN="LEFT JOIN (SELECT question_id, SUM(CASE WHEN rating='thumbs_down' THEN 1 ELSE 0 END) AS td_count FROM response_feedback GROUP BY question_id) fb ON fb.question_id = q.id"
        FB_WHERE="OR fb.td_count > 0"
        FB_SUM="COALESCE(SUM(fb.td_count),0)"
    else
        FB_JOIN=""
        FB_WHERE=""
        FB_SUM="0"
    fi

    q "SELECT
         ROUND((COUNT(DISTINCT q.id)*2) + (${FB_SUM}*3) + ((1.0 - AVG(COALESCE(q.confidence,0.0))/100.0)*5), 1) AS score,
         COUNT(DISTINCT q.id) AS times_asked,
         ${FB_SUM} AS td_count,
         ROUND(AVG(COALESCE(q.confidence,0.0)),0) AS avg_conf,
         q.question_text
       FROM questions q ${FB_JOIN}
       WHERE q.timestamp >= datetime('now','-30 days')
         AND (q.matched_topic IS NULL OR q.confidence < 60 ${FB_WHERE})
       GROUP BY q.question_text
       ORDER BY score DESC
       LIMIT 20;" | while IFS='|' read -r score asked td conf question; do
        printf "  %-6s  %-5s  %-4s  %-5s%%  %-55s\n" "$score" "$asked" "$td" "$conf" "${question:0:55}"
    done

    echo ""
    echo -e "${CYAN}[ Never Matched (no KB hit, last 30 days) ]${NC}"
    q "SELECT DISTINCT question_text FROM questions
       WHERE matched_topic IS NULL
         AND timestamp >= datetime('now','-30 days')
       ORDER BY timestamp DESC LIMIT 10;" | while read -r question; do
        echo -e "  • ${question:0:70}"
    done

    echo ""
    echo -e "${BOLD}══════════════════════════════════════════════${NC}"
    echo ""
    exit 0
fi

# ── Feedback-only mode ────────────────────────────────────────────────────────
if $SHOW_FEEDBACK_ONLY; then
    echo ""
    echo -e "${BOLD}══════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  GSA Gateway — Feedback Ratings${NC}"
    echo -e "${BOLD}══════════════════════════════════════════════${NC}"
    echo ""

    if [ "$HAS_FEEDBACK" = "1" ]; then
        UP=$(q "SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_up';")
        DOWN=$(q "SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_down';")
        RETRY=$(q "SELECT COUNT(*) FROM response_feedback WHERE rating='regenerate';")
        TOTAL_RATED=$((UP + DOWN))
        if [ "$TOTAL_RATED" -gt 0 ]; then
            SAT=$(awk "BEGIN { printf \"%.1f\", ($UP/$TOTAL_RATED)*100 }")
            SAT_DISPLAY="${SAT}%"
        else
            SAT_DISPLAY="no ratings yet"
        fi

        echo -e "  👍 Thumbs up:    ${GREEN}${UP}${NC}"
        echo -e "  👎 Thumbs down:  ${RED}${DOWN}${NC}"
        echo -e "  🔄 Retry:        ${YELLOW}${RETRY}${NC}"
        echo -e "  Satisfaction:    ${BOLD}${SAT_DISPLAY}${NC}  (of rated responses)"

        echo ""
        echo -e "${CYAN}[ Detail Breakdown (thumbs down reasons) ]${NC}"
        printf "  %-20s  %6s\n" "Reason" "Count"
        printf "  %-20s  %6s\n" "--------------------" "------"
        q "SELECT detail, COUNT(*) as cnt
           FROM response_feedback
           WHERE rating='thumbs_down' AND detail IS NOT NULL
           GROUP BY detail ORDER BY cnt DESC;" | while IFS='|' read -r detail cnt; do
            printf "  %-20s  %6s\n" "$detail" "$cnt"
        done

        echo ""
        echo -e "${CYAN}[ Retry Outcomes ]${NC}"
        echo -e "  (Did retries help? Rows where original got 👎, retry got 👍)"
        RETRY_IMPROVED=$(q "
          SELECT COUNT(*) FROM (
            SELECT rf_new.question_id
            FROM response_feedback rf_new
            JOIN response_feedback rf_orig ON rf_orig.question_id = rf_new.original_question_id
            WHERE rf_new.rating = 'regenerate'
              AND rf_orig.rating = 'thumbs_down'
              AND EXISTS (
                SELECT 1 FROM response_feedback rf_pos
                WHERE rf_pos.question_id = rf_new.question_id
                  AND rf_pos.rating = 'thumbs_up'
              )
          );")
        echo -e "  Retry improved answer: ${GREEN}${RETRY_IMPROVED}${NC} case(s)"
    else
        echo -e "  ${YELLOW}(feedback table not yet created — restart the bot once)${NC}"
    fi

    echo ""
    echo -e "${BOLD}══════════════════════════════════════════════${NC}"
    echo ""
    exit 0
fi

# ── Full overview ─────────────────────────────────────────────────────────────
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

# ── 5. Feedback ratings ───────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[ Feedback Ratings ]${NC}"

if [ "$HAS_FEEDBACK" = "1" ]; then
    UP=$(q "SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_up';")
    DOWN=$(q "SELECT COUNT(*) FROM response_feedback WHERE rating='thumbs_down';")
    RETRY_COUNT=$(q "SELECT COUNT(*) FROM response_feedback WHERE rating='regenerate';")
    TOTAL_RATED=$((UP + DOWN))
    if [ "$TOTAL_RATED" -gt 0 ]; then
        SAT=$(awk "BEGIN { printf \"%.1f\", ($UP/$TOTAL_RATED)*100 }")
        SAT_STR="${SAT}%"
    else
        SAT_STR="no ratings yet"
    fi
    echo -e "  👍 Thumbs up:    ${GREEN}${UP}${NC}"
    echo -e "  👎 Thumbs down:  ${RED}${DOWN}${NC}"
    echo -e "  🔄 Retry:        ${YELLOW}${RETRY_COUNT}${NC}"
    echo -e "  Satisfaction:    ${BOLD}${SAT_STR}${NC}"
else
    echo -e "  ${YELLOW}(feedback table not yet created — restart the bot once)${NC}"
fi

# ── 6. Process status ─────────────────────────────────────────────────────────
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

# ── 7. Recent questions (optional) ────────────────────────────────────────────
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

# ── 8. Log tail ───────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[ Last 5 Log Lines ]${NC}"
echo -e "  ${BLUE}Discord (gsa_gateway.log):${NC}"
tail -5 gsa_gateway.log 2>/dev/null | sed 's/^/    /' || echo "    (no log file)"
echo -e "  ${BLUE}Telegram (telegram_bot.log):${NC}"
tail -5 telegram_bot.log 2>/dev/null | sed 's/^/    /' || echo "    (no log file)"

# ── 9. Gap preview (last 30 days, top 5) ─────────────────────────────────────
echo ""
echo -e "${CYAN}[ Top 5 Gaps — Last 30 Days ]${NC}"
printf "  %-6s  %-5s  %-4s  %-6s  %-55s\n" "Score" "Asked" "👎" "Conf" "Question"
printf "  %-6s  %-5s  %-4s  %-6s  %-55s\n" "------" "-----" "----" "------" "-------------------------------------------------------"

if [ "$HAS_FEEDBACK" = "1" ]; then
    GAP_JOIN="LEFT JOIN (SELECT question_id, SUM(CASE WHEN rating='thumbs_down' THEN 1 ELSE 0 END) AS td_count FROM response_feedback GROUP BY question_id) fb ON fb.question_id = q.id"
    GAP_WHERE="OR fb.td_count > 0"
    GAP_SUM="COALESCE(SUM(fb.td_count),0)"
else
    GAP_JOIN=""
    GAP_WHERE=""
    GAP_SUM="0"
fi

q "SELECT
     ROUND((COUNT(DISTINCT q.id)*2) + (${GAP_SUM}*3) + ((1.0 - AVG(COALESCE(q.confidence,0.0))/100.0)*5), 1) AS score,
     COUNT(DISTINCT q.id) AS times_asked,
     ${GAP_SUM} AS td_count,
     ROUND(AVG(COALESCE(q.confidence,0.0)),0) AS avg_conf,
     q.question_text
   FROM questions q ${GAP_JOIN}
   WHERE q.timestamp >= datetime('now','-30 days')
     AND (q.matched_topic IS NULL OR q.confidence < 60 ${GAP_WHERE})
   GROUP BY q.question_text
   ORDER BY score DESC
   LIMIT 5;" | while IFS='|' read -r score asked td conf question; do
    printf "  %-6s  %-5s  %-4s  %-5s%%  %-55s\n" "$score" "$asked" "$td" "$conf" "${question:0:55}"
done

echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "  Flags: --today  --week  --platform discord|telegram  --questions  --feedback  --gaps"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo ""
