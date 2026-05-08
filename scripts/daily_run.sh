#!/usr/bin/env bash
# Daily pipeline run: ingest → extract → backtest → score.
#
# Designed to be invoked by launchd (macOS) or cron (linux). All output
# is appended to data/logs/cron.log; exit code is non-zero on any failure.
#
# Usage:
#   scripts/daily_run.sh                 # full daily run with sane limits
#   scripts/daily_run.sh --catch-up      # higher limits for backfills
#
# Environment:
#   Requires .venv at the repo root with the project installed.
#   Reads .env for ANTHROPIC_API_KEY + YOUTUBE_API_KEY.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/.venv"
TSR="$VENV/bin/tsr"
LOG_DIR="$REPO_ROOT/data/logs"
LOG_FILE="$LOG_DIR/cron.log"

mkdir -p "$LOG_DIR"

# Pre-flight checks
if [[ ! -x "$TSR" ]]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: $TSR not found or not executable" >> "$LOG_FILE"
    exit 1
fi
if [[ ! -f "$REPO_ROOT/.env" ]]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] FATAL: .env missing" >> "$LOG_FILE"
    exit 1
fi

INGEST_LIMIT=10
EXTRACT_LIMIT=30
# Lowered from 50 (2026-05-05): short videos (Adam Mancini's daily SPX levels,
# IBD's per-stock segments) are deliberately compact and still carry useful
# tickers + technicals. The extractor's prefilter rejects no-signal segments
# anyway, so the cost of letting short videos through is a few extra LLM calls.
EXTRACT_MIN_SEGMENTS=10

if [[ "${1:-}" == "--catch-up" ]]; then
    INGEST_LIMIT=50
    EXTRACT_LIMIT=0   # 0 = no limit
fi

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

echo "" >> "$LOG_FILE"
echo "===== $(ts) daily_run.sh start (ingest=$INGEST_LIMIT extract=$EXTRACT_LIMIT) =====" >> "$LOG_FILE"

# Each stage is independent — if one fails, the rest are still worth trying
# next time. Capture stdout+stderr per stage so failures are easy to find.

stage() {
    local name="$1"; shift
    echo "[$(ts)] -- $name --" >> "$LOG_FILE"
    if "$@" >> "$LOG_FILE" 2>&1; then
        echo "[$(ts)] $name OK" >> "$LOG_FILE"
        return 0
    else
        local rc=$?
        echo "[$(ts)] $name FAILED rc=$rc" >> "$LOG_FILE"
        return $rc
    fi
}

failed=0

stage "initdb"   "$TSR" initdb || failed=1
stage "ingest"   "$TSR" ingest --limit "$INGEST_LIMIT" || failed=1

extract_args=("--min-segments" "$EXTRACT_MIN_SEGMENTS")
if [[ "$EXTRACT_LIMIT" -gt 0 ]]; then
    extract_args+=("--limit" "$EXTRACT_LIMIT")
fi
stage "extract"  "$TSR" extract "${extract_args[@]}" || failed=1

stage "backtest" "$TSR" backtest || failed=1

# Compute realized lens outcomes for snapshots whose horizons elapsed.
# Best-effort — failure should not block the rest of the pipeline.
stage "score-lens-outcomes" "$TSR" score-lens-outcomes --max-age-days 60 || \
    echo "[$(ts)] warn: lens outcome scoring failed; continuing" >> "$LOG_FILE"

stage "score"    "$TSR" score || failed=1

echo "===== $(ts) daily_run.sh done (failed=$failed) =====" >> "$LOG_FILE"

# Trim log if it ever exceeds 5MB.
if [[ -f "$LOG_FILE" ]] && [[ "$(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE")" -gt 5242880 ]]; then
    tail -c 2097152 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

exit $failed
