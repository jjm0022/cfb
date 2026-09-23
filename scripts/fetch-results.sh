#!/usr/bin/env bash
# Import the pool week that just finished: fetch its CBS Weekly Standings page,
# then run import-results, which stores it, writes the report and DMs it.
#
# Run by pickem-results.timer on Tuesday and again on Wednesday morning. The
# week comes from the database (the earliest stored week that is over but not
# imported), not from CBS's current week, which may not have advanced yet. A
# week with a game still unfinished is not saved, so the Wednesday run fetches
# it again.
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
database="$project_root/data/pickem.duckdb"
results_dir="/mnt/nas/Betting/pickem/results"
season=""
retry_note="Retry runs Wednesday 09:00."

usage() {
    cat >&2 <<EOF
Usage: $0 [--season YEAR] [--results-dir DIR] [--db PATH]
EOF
}

notify() {
    uv run pickem notify-owner --title "Pick'em results" "$1" ||
        echo "Discord DM failed; the message was: $1" >&2
}

while (($#)); do
    case "$1" in
        --season | --results-dir | --db)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            case "$1" in
                --season) season=$2 ;;
                --results-dir) results_dir=$2 ;;
                --db) database=$2 ;;
            esac
            shift 2
            ;;
        --help | -h) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done

if [[ -z "$season" ]]; then
    season=$(date +%Y)
    (( 10#$(date +%m) <= 2 )) && season=$((season - 1))
fi
[[ "$season" =~ ^[0-9]{4}$ ]] || { usage; exit 2; }

if ! pool_week=$(PICKEM_LOG_CONSOLE=off uv run pickem pending-results-week \
    --season "$season" --db "$database"); then
    notify "Could not tell which pool week's results are due; see the log. $retry_note"
    exit 1
fi
if [[ -z "$pool_week" ]]; then
    echo "No finished pool week is waiting for results"
    exit 0
fi
if [[ ! "$pool_week" =~ ^[1-9][0-9]*$ ]]; then
    notify "Could not tell which pool week's results are due; see the log. $retry_note"
    exit 1
fi

page="$results_dir/week$pool_week.html"
if [[ ! -f "$page" ]]; then
    if ! output=$(PICKEM_LOG_CONSOLE=off uv run pickem fetch-cbs \
        --page standings --pool-week "$pool_week" --out "$page" 2>&1); then
        echo "$output" >&2
        notify "Pool week $pool_week standings not fetched: $(printf '%s\n' \
            "$output" | tail -n 1) $retry_note"
        exit 1
    fi
    echo "$output"
fi

rc=0
uv run pickem import-results --season "$season" --pool-week "$pool_week" \
    --file "$page" --out-dir "$results_dir" --db "$database" || rc=$?
if [[ $rc -eq 3 ]]; then
    # Imported, reported and DMed; only the dashboard page write failed.
    notify "Pool week $pool_week imported and reported, but the dashboard page was not \
written; see the log, then run: uv run pickem results-report --season $season --pool-week $pool_week"
    exit 3
elif [[ $rc -eq 2 ]]; then
    # Imported and reported; only the Discord DM failed.
    notify "Pool week $pool_week imported and the report written, but the results DM \
failed; see the log. The report is at $results_dir/week${pool_week}-report.md"
    exit 2
elif [[ $rc -ne 0 ]]; then
    notify "Pool week $pool_week standings are saved but import-results failed; see the log. $retry_note"
    exit 1
fi
