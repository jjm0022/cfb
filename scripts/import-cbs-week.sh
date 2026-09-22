#!/usr/bin/env bash
# Import a pool week's CBS boards from the saved page into the pick'em database.
#
# Pool week N is CFB week N plus NFL week N-1; both boards live on one saved
# page, <weeks-dir>/weekN.html on the NAS. Each league is ingested separately
# so a board CBS has not posted yet fails alone without blocking the other.
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
database="$project_root/data/pickem.duckdb"
weeks_dir="/mnt/nas/Betting/pickem/weeks"
sports=(cfb nfl)
season=""
page=""

usage() {
    cat >&2 <<EOF
Usage: $0 POOL_WEEK [--sport cfb|nfl] [--season YEAR] [--file PATH] [--db PATH]

  POOL_WEEK  Pool week number; imports CFB week POOL_WEEK and NFL week POOL_WEEK-1
  --sport    Import only one league's board (default: both)
  --season   Season year (default: current season)
  --file     Saved CBS page (default: /mnt/nas/Betting/pickem/weeks/weekPOOL_WEEK.html)
  --db       Database path (default: data/pickem.duckdb)
EOF
}

pool_week=""
while (($#)); do
    case "$1" in
        --sport | --season | --file | --db)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            case "$1" in
                --sport)
                    [[ "$2" == cfb || "$2" == nfl ]] || { usage; exit 2; }
                    sports=("$2")
                    ;;
                --season) season=$2 ;;
                --file) page=$2 ;;
                --db) database=$2 ;;
            esac
            shift 2
            ;;
        --help | -h)
            usage
            exit 0
            ;;
        *)
            [[ -z "$pool_week" && "$1" =~ ^[1-9][0-9]*$ ]] || { usage; exit 2; }
            pool_week=$1
            shift
            ;;
    esac
done

[[ -n "$pool_week" ]] || { usage; exit 2; }

if [[ -z "$season" ]]; then
    # The season is named for the year it starts; January and February
    # games still belong to the previous year's season.
    season=$(date +%Y)
    (( 10#$(date +%m) <= 2 )) && season=$((season - 1))
fi
[[ "$season" =~ ^[0-9]{4}$ ]] || { usage; exit 2; }

page=${page:-"$weeks_dir/week$pool_week.html"}
[[ -f "$page" ]] || {
    echo "Saved CBS page not found: $page" >&2
    exit 1
}

failed=()
for sport in "${sports[@]}"; do
    week=$pool_week
    if [[ "$sport" == nfl ]]; then
        week=$((pool_week - 1))
        if ((week < 1)); then
            echo "Pool week $pool_week has no NFL board; skipping nfl"
            continue
        fi
    fi
    printf 'Importing %s %s week %s from %s\n' "$sport" "$season" "$week" "$page"
    if ! uv run pickem ingest-cbs --html --file "$page" \
        --sport "$sport" --season "$season" --week "$week" --db "$database"; then
        failed+=("$sport week $week")
    fi
done

if ((${#failed[@]})); then
    printf 'Import failed for: %s\n' "${failed[@]}" >&2
    exit 1
fi
