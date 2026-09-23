#!/usr/bin/env bash
# Open the dedicated CBS Chrome profile in a window on the desktop so the
# owner can sign in to CBS again. Close the window afterwards: the scheduled
# fetch cannot use the profile while this Chrome holds it.
#
# Detached with setsid so the window outlives the shell that opened it (an SSH
# or agent session). Same --password-store=basic as the headless fetch, so the
# cookies it saves are readable without the GNOME keyring.
set -euo pipefail

profile=${PICKEM_CBS_CHROME_PROFILE:-$HOME/.local/share/pickem/cbs-chrome}
chrome=${PICKEM_CHROME_BINARY:-/opt/google/chrome/chrome}
pool_url=${PICKEM_CBS_POOL_URL:-https://picks.cbssports.com/football/pickem/pools/kbxw63b2ge3dkobtge2tq===}
runtime_dir=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

mkdir -p "$profile"
WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0} XDG_RUNTIME_DIR=$runtime_dir DISPLAY=${DISPLAY:-:0} \
    setsid -f "$chrome" --user-data-dir="$profile" --password-store=basic \
    --ozone-platform=wayland --no-first-run --no-default-browser-check --new-window \
    "$pool_url" >/dev/null 2>&1 </dev/null
echo "Opened CBS in the dedicated Chrome profile. Sign in, decline saving the password, then close the window."
