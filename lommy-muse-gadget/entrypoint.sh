#!/bin/bash
# Entrypoint for the Muse Gadget Umbrel app.
#
# Runs as root only long enough to wire up permissions, then supervises:
#   - the status/setup page (always)
#   - the musegadget daemon (once an SDK token exists — entered in the
#     app's web UI, or pre-seeded via token.env)
# The daemon itself always runs as the unprivileged 'gadget' user: the
# container equivalent of `bash install.sh --run-as gadget`.
set -euo pipefail

# The host's docker group GID varies between installs (997 is only the common
# default). Detect it from the socket itself — a wrong hardcoded GID silently
# leaves the gadget user without Docker access, which breaks sibling-container
# management entirely.
DOCKER_GID="$(stat -c %g /var/run/docker.sock 2>/dev/null || echo "${DOCKER_GID:-997}")"
TOKEN_FILE="/data/state/sdk_token"
DOCKER_ENABLED_FILE="/data/state/docker_enabled"
DOCKER_ACCESS_REQUEST="/data/state/docker_access.request"
DOCKER_ACCESS_RESULT="/data/state/docker_access.result"

# Give the gadget user access to the host's Docker socket so it can manage
# sibling Umbrel app containers — unless the user turned that off in the
# web UI. (Same privilege tradeoff as the host install, declared here
# instead of via --run-as on a sudo-capable account.)
if getent group "$DOCKER_GID" >/dev/null 2>&1; then
    DOCKER_GROUP="$(getent group "$DOCKER_GID" | cut -d: -f1)"
else
    groupadd -o -g "$DOCKER_GID" dockerhost
    DOCKER_GROUP="dockerhost"
fi

# User toggle for Docker access (web UI writes "1"/"0", default 1).
docker_enabled() { [ "$(tr -d ' \t\r\n' < "$DOCKER_ENABLED_FILE" 2>/dev/null || echo 1)" = "1" ]; }

apply_docker_group() {
    # Group membership is per-process: callers restart the gadget's
    # processes afterwards so the change takes effect immediately.
    if docker_enabled; then
        usermod -aG "$DOCKER_GROUP" gadget || true
    else
        gpasswd -d gadget "$DOCKER_GROUP" >/dev/null 2>&1 || true
    fi
}
apply_docker_group

# Persisted device identity + pairing state.
mkdir -p /data/state
chown gadget:gadget /data/state
chmod 700 /data/state

# Status + first-run setup page (background, as gadget; supervised below).
start_status_page() {
    su -s /bin/bash gadget -c 'exec python3 /opt/musegadget/status.py >>/tmp/musegadget-status.log 2>&1' &
    echo "status/setup page on :8756"
}
start_status_page

stop_gadget_processes() {
    pkill -f "musegadget/status.py" 2>/dev/null || true
    stop_daemon
}

# Docker-access toggle requested from the web UI. Runs as root: updates
# the flag, fixes group membership, and restarts the gadget's processes so
# the new membership takes effect right away (the daemon reconnects).
apply_docker_toggle_request() {
    [ -f "$DOCKER_ACCESS_REQUEST" ] || return 0
    want="$(tr -d ' \t\r\n' < "$DOCKER_ACCESS_REQUEST" 2>/dev/null || echo 1)"
    case "$want" in 1|on|true|yes) want=1;; *) want=0;; esac
    echo "$want" > "$DOCKER_ENABLED_FILE"
    chown gadget:gadget "$DOCKER_ENABLED_FILE" 2>/dev/null || true
    apply_docker_group
    pkill -f "musegadget/status.py" 2>/dev/null || true
    stop_daemon
    sleep 1
    start_status_page
    if [ "$want" = "1" ]; then
        msg="Docker access enabled — Muse can list, inspect and restart your other Umbrel apps."
    else
        msg="Docker access disabled — Muse can no longer reach the Docker socket."
    fi
    printf '{"ok":true,"enabled":"%s","message":"%s"}\n' "$want" "$msg" > "$DOCKER_ACCESS_RESULT"
    chown gadget:gadget "$DOCKER_ACCESS_RESULT" 2>/dev/null || true
    rm -f "$DOCKER_ACCESS_REQUEST"
}

# One-tap host Bluetooth fixes, requested from the setup page. The page
# runs as the unprivileged gadget user and cannot write the host's
# /etc/bluetooth or /etc/systemd itself; it drops a request file and this
# root loop applies (or reverts) it via /opt/musegadget/bt_fix.py — the MTU
# fix backs up the original config first, the iPhone fix uses an additive
# systemd drop-in that is simply removed on restore.
apply_bt_fix_request() {
    if [ -f /data/state/bt_fix.request ]; then
        python3 /opt/musegadget/bt_fix.py || true
    fi
    if [ -f /data/state/bt_iphone.request ]; then
        python3 /opt/musegadget/bt_fix.py || true
    fi
}

daemon_pid=""
token_used=""
warned_no_token=""

# Token precedence: environment (token.env pre-seed) beats the setup wizard.
get_token() {
    if [ -n "${MUSEGADGET_SDK_TOKEN:-}" ]; then
        printf '%s' "${MUSEGADGET_SDK_TOKEN}"
        return 0
    fi
    if [ -s "$TOKEN_FILE" ]; then
        tr -d ' \t\r\n' < "$TOKEN_FILE"
        return 0
    fi
    return 1
}

stop_daemon() {
    if [ -n "${daemon_pid:-}" ] && kill -0 "$daemon_pid" 2>/dev/null; then
        kill "$daemon_pid" 2>/dev/null || true
    fi
    daemon_pid=""
}

trap 'stop_gadget_processes; exit 0' TERM INT

while true; do
    apply_bt_fix_request
    apply_docker_toggle_request
    # Supervise the status page too (it is restarted after Docker toggles).
    if ! pgrep -f "musegadget/status.py" >/dev/null 2>&1; then
        start_status_page
    fi
    if token="$(get_token)"; then
        warned_no_token=""
        if [ "$token" != "${token_used:-}" ]; then
            # Token is new or changed: (re)start the daemon with it.
            # (The token itself is never logged.)
            stop_daemon
            token_used="$token"
        fi
        if [ -z "${daemon_pid:-}" ] || ! kill -0 "$daemon_pid" 2>/dev/null; then
            MUSEGADGET_SDK_TOKEN="$token" \
                su -s /bin/bash gadget -c 'exec /opt/musegadget/venv/bin/musegadget run' &
            daemon_pid=$!
            echo "musegadget daemon started (pid $daemon_pid)"
        fi
    else
        # No token: stop any stale daemon; the setup page (port 8756)
        # collects the token.
        stop_daemon
        token_used=""
        if [ -z "$warned_no_token" ]; then
            echo "No SDK token yet — open the app's web UI (port 8756) to enter it." >&2
            warned_no_token="1"
        fi
    fi
    # sleep in background so SIGTERM/SIGINT interrupts promptly via the trap
    sleep 15 &
    wait $!
done
