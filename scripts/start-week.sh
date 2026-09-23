#!/usr/bin/env bash
# Start a pool week: fetch its CBS board page if it is not saved yet, import
# both boards into the pick'em database, then take each league's first market
# snapshot.
#
# Pool week N is CFB week N plus NFL week N-1; both boards live on one page,
# <weeks-dir>/weekN.html on the NAS. Each league is ingested and polled
# separately so a board CBS has not posted yet fails alone without blocking the
# other. A league whose ingest fails is not polled: poll-odds derives its slate
# from the stored league lines. Preflight and the report stay manual steps.
#
# --auto is the scheduled form (pickem-board.timer): it asks CBS for the
# current pool week, does nothing if that week is already started, and DMs the
# owner the pick reminder on success or the reason on failure.
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
database="$project_root/data/pickem.duckdb"
weeks_dir="/mnt/nas/Betting/pickem/weeks"
sports=(cfb nfl)
season=""
page=""
days=11
auto=0
refetch=0
retry_note="Retry runs Wednesday 09:00."
reminder="Reminder: submit this week's picks."

usage() {
    cat >&2 <<EOF
Usage: $0 POOL_WEEK|--auto [--sport cfb|nfl] [--season YEAR] [--days N] [--file PATH]
          [--weeks-dir DIR] [--refetch] [--db PATH]

  POOL_WEEK    Pool week number; starts CFB week POOL_WEEK and NFL week POOL_WEEK-1
  --auto       Start the week CBS marks current; DM the owner the outcome
  --sport      Start only one league (default: both)
  --season     Season year (default: current season)
  --days       Kickoff window for the first odds poll, in days (default: 11)
  --file       Saved CBS page to use as-is; never fetched
  --weeks-dir  Where board pages are saved (default: /mnt/nas/Betting/pickem/weeks)
  --refetch    Fetch the board page again even if it is saved
  --db         Database path (default: data/pickem.duckdb)
EOF
}

notify() {
    ((auto)) || return 0
    uv run pickem notify-owner --title "Pick'em week start" "$1" ||
        echo "Discord DM failed; the message was: $1" >&2
}

pool_week=""
while (($#)); do
    case "$1" in
        --sport | --season | --days | --file | --db | --weeks-dir)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            case "$1" in
                --sport)
                    [[ "$2" == cfb || "$2" == nfl ]] || { usage; exit 2; }
                    sports=("$2")
                    ;;
                --season) season=$2 ;;
                --days)
                    [[ "$2" =~ ^[1-9][0-9]*$ ]] || { usage; exit 2; }
                    days=$2
                    ;;
                --file) page=$2 ;;
                --db) database=$2 ;;
                --weeks-dir) weeks_dir=$2 ;;
            esac
            shift 2
            ;;
        --auto) auto=1; shift ;;
        --refetch) refetch=1; shift ;;
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

explicit_file=$page
if ((auto)); then
    [[ -z "$pool_week" && -z "$page" ]] || { usage; exit 2; }
else
    [[ -n "$pool_week" ]] || { usage; exit 2; }
fi

if [[ -z "$season" ]]; then
    # The season is named for the year it starts; January and February
    # games still belong to the previous year's season.
    season=$(date +%Y)
    (( 10#$(date +%m) <= 2 )) && season=$((season - 1))
fi
[[ "$season" =~ ^[0-9]{4}$ ]] || { usage; exit 2; }

if ((auto)); then
    if ! pool_week=$(PICKEM_LOG_CONSOLE=off uv run pickem cbs-current-week) ||
        [[ ! "$pool_week" =~ ^[1-9][0-9]*$ ]]; then
        echo "Could not read the current pool week from CBS" >&2
        notify "Could not read the current pool week from CBS; see the log. $retry_note"
        exit 1
    fi
    if ! status=$(uv run pickem pool-week-status --season "$season" --pool-week "$pool_week" \
        --db "$database"); then
        notify "Could not read pool week $pool_week's status from the database; see the log. $retry_note"
        exit 1
    fi
    case "$status" in
        started)
            if ((!refetch)); then
                echo "Pool week $pool_week is already started"
                exit 0
            fi
            ;;
        finished)
            message="CBS still shows pool week $pool_week, which is already finished; the new board is not up yet. $retry_note"
            echo "$message" >&2
            notify "$message"
            exit 1
            ;;
    esac
fi

# An explicit --file is used as-is and never fetched; the default page is
# fetched when missing, and fetch-cbs's exit status is trusted for it.
if [[ -n "$explicit_file" && ! -f "$page" ]]; then
    echo "Saved CBS page not found: $page" >&2
    exit 1
fi
if [[ -z "$page" ]]; then
    page="$weeks_dir/week$pool_week.html"
    if ((refetch)) || [[ ! -f "$page" ]]; then
        fetch=(env PICKEM_LOG_CONSOLE=off uv run pickem fetch-cbs --page board \
            --pool-week "$pool_week" --out "$page")
        ((refetch)) && fetch+=(--force)
        if ! output=$("${fetch[@]}" 2>&1); then
            echo "$output" >&2
            notify "Pool week $pool_week board not fetched: $(printf '%s\n' "$output" | tail -n 1) $retry_note"
            exit 1
        fi
        echo "$output"
    fi
fi
failed=()
loaded=()
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
        failed+=("$sport week $week (ingest)")
        continue
    fi
    loaded+=("$sport week $week")
    printf 'Polling %s %s week %s odds over the next %s days\n' "$sport" "$season" "$week" "$days"
    if ! uv run pickem poll-odds --sport "$sport" --season "$season" --week "$week" \
        --days "$days" --db "$database"; then
        failed+=("$sport week $week (poll)")
    fi
done

if ((${#failed[@]})); then
    summary=$(printf '%s, ' "${failed[@]}")
    echo "Week start failed for: ${summary%, }" >&2
    message="Pool week $pool_week: failed for ${summary%, }."
    if ((${#loaded[@]})); then
        done_list=$(printf '%s, ' "${loaded[@]}")
        message+=" Loaded: ${done_list%, }. $reminder"
    fi
    notify "$message $retry_note"
    exit 1
fi

loaded_list=$(printf '%s, ' "${loaded[@]}")
notify "Pool week $pool_week board loaded (${loaded_list%, }). $reminder"
