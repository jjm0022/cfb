#!/usr/bin/env bash
# Reconcile final scores for the database's active NFL and CFB pick'em weeks.
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
database="$project_root/data/pickem.duckdb"

usage() {
    echo "Usage: $0 [--db PATH]" >&2
}

while (($#)); do
    case "$1" in
        --db)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            database=$2
            shift 2
            ;;
        --help | -h)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

python_bin="$project_root/.venv/bin/python"
[[ -x "$python_bin" ]] || {
    echo "Expected project Python at $python_bin; run 'uv sync' first." >&2
    exit 1
}
[[ -f "$database" ]] || {
    echo "Pick'em database not found: $database" >&2
    exit 1
}

mapfile -t scopes < <("$python_bin" - "$database" <<'PY'
from datetime import UTC, datetime
from pathlib import Path
import sys

from pickem.store.db import Store

with Store(Path(sys.argv[1])) as store:
    for sport, season, week in store.active_pickem_scopes(datetime.now(UTC)):
        print(sport.value, season, week)
PY
)

if ((${#scopes[@]} == 0)); then
    echo "No active pick'em scopes found in $database" >&2
    exit 1
fi

for scope in "${scopes[@]}"; do
    read -r sport season week <<<"$scope"
    command=(uv run pickem sync-results --sport "$sport" --season "$season")
    if [[ "$sport" == "cfb" ]]; then
        command+=(--week "$week")
    fi
    command+=(--db "$database")
    printf 'Reconciling %s %s week %s\n' "$sport" "$season" "$week"
    "${command[@]}"
done
