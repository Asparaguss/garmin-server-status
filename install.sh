#!/bin/sh
set -eu

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

case "${1:-install}" in
    -h|--help)
        printf '%s\n' 'Usage: sudo sh install.sh [install|show-token|rotate-token]'
        exit 0
        ;;
    install|show-token|rotate-token) ;;
    *) fail 'Use install, show-token, or rotate-token. Run sh install.sh --help for usage.' ;;
esac
[ "$#" -le 1 ] || fail 'Pass at most one action.'
[ "$(uname -s)" = Linux ] || fail 'Run this installer on the Linux server.'
[ "$(id -u)" -eq 0 ] || fail 'Run sudo sh install.sh on the Linux server.'

case "$0" in
    */*) directory=${0%/*} ;;
    *) directory=. ;;
esac
directory=$(CDPATH= cd -P "$directory" && pwd)
for file in install.py caddy_config.py status_service.py; do
    [ -r "$directory/server/$file" ] || fail 'Extract the complete Server Status archive before running install.sh.'
done

action=${1:-install}
if [ "$action" != show-token ]; then
    command -v systemctl >/dev/null 2>&1 || fail 'The Linux host needs systemd and systemctl.'
    systemd_version=$(systemctl show --property=Version --value 2>/dev/null) ||
        fail 'Could not reach the system systemd manager. Run this on the Linux host.'
    systemd_version=${systemd_version%%[!0-9]*}
    [ -n "$systemd_version" ] && [ "$systemd_version" -ge 247 ] ||
        fail 'Server Status requires systemd 247 or later for service credentials.'
fi
if [ "$action" = install ]; then
    for file in stat meminfo loadavg uptime; do
        [ -r "/proc/$file" ] || fail "Could not read /proc/$file. Run the installer on the Linux host."
    done
fi

find_python() {
    python=
    for name in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
        candidate=$(command -v "$name") || continue
        if "$candidate" -I -c 'import fcntl, http.server, secrets, socket, ssl, urllib.request, sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
            python=$candidate
            return 0
        fi
    done
    return 1
}

has_ca_store() {
    "$python" -I -c '
import os, ssl
context = ssl.create_default_context()
directory = ssl.get_default_verify_paths().capath
if not context.get_ca_certs() and not (directory and os.path.isdir(directory) and os.listdir(directory)):
    raise SystemExit(1)
' >/dev/null 2>&1
}

if ! find_python || { [ "$action" = install ] && ! has_ca_store; }; then
    if command -v apt-get >/dev/null 2>&1; then
        manager=apt-get
        python_package=python3
    elif command -v dnf >/dev/null 2>&1; then
        manager=dnf
        python_package=python3.11
    else
        fail 'Install Python 3.10 or later and your distribution CA certificates package, then rerun install.sh.'
    fi
    if [ -n "$python" ]; then
        set -- ca-certificates
    else
        set -- "$python_package" ca-certificates
    fi
    printf 'Install missing dependencies with %s (%s)? [y/N] ' "$manager" "$*"
    IFS= read -r answer || answer=
    case "$answer" in
        y|Y|yes|YES) ;;
        *) fail 'Dependency installation declined. Install the packages and rerun install.sh.' ;;
    esac
    if [ "$manager" = apt-get ]; then
        apt-get update || fail 'Could not refresh APT package lists.'
    fi
    "$manager" install -y "$@" || fail 'Could not install the required packages.'
    find_python || fail 'Python 3.10 or later is still unavailable. Install a supported Python version and rerun install.sh.'
    if [ "$action" = install ]; then
        has_ca_store || fail 'Could not load the system HTTPS trust store. Check the CA certificates package.'
    fi
fi

exec "$python" -E -s -B "$directory/server/install.py" "$action"
