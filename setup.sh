#!/bin/sh
set -eu

# Update both values when publishing a new installer archive.
release=v0.1.4
archive_sha256=43df7adc03a2c714c42af9c232a2998bd35e0436c6bfad42eb275d2ea717c2ca
asset=garmin-server-status-linux.tar.gz
release_page="https://github.com/Asparaguss/garmin-server-status/releases/tag/$release"
archive_url="https://github.com/Asparaguss/garmin-server-status/releases/download/$release/$asset"

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

[ "$#" -le 1 ] || fail 'Pass at most one option. Run sh setup.sh --help for usage.'
case "${1:-}" in
    -h|--help)
        printf '%s\n' 'Usage: sh setup.sh [--download-only]' \
            'Download and verify the pinned Server Status release, then unpack it in the current directory.'
        exit 0
        ;;
    ''|--download-only) ;;
    *) fail 'Run sh setup.sh --help for usage.' ;;
esac

[ "$(uname -s)" = Linux ] || fail 'Run this setup script on the Linux server.'
for tool in curl sha256sum tar mktemp mkdir rm rmdir; do
    command -v "$tool" >/dev/null 2>&1 ||
        fail "Required command missing: $tool. Install it with your system package manager and rerun setup.sh."
done

target="$(pwd -P)/garmin-server-status-$release"
if [ -e "$target" ] || [ -L "$target" ]; then
    fail "Directory already exists: $target. To reuse a previous download, run install.sh inside it."
fi

scratch=$(mktemp -d "${TMPDIR:-/tmp}/garmin-server-status.XXXXXXXX")
archive="$scratch/$asset"
cleanup() {
    rm -f -- "$archive"
    rmdir -- "$scratch"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

printf 'Downloading Server Status %s\n%s\n' "$release" "$release_page"
curl -q --proto '=https' --proto-redir '=https' --fail --silent --show-error \
    --location --connect-timeout 15 --max-time 120 --output "$archive" "$archive_url" ||
    fail "Could not download the installer from $release_page."
if ! (cd "$scratch" && printf '%s  %s\n' "$archive_sha256" "$asset" | sha256sum -c -); then
    fail 'The archive does not match the SHA-256 pinned in this setup script.'
fi

mkdir -m 700 -- "$target" || fail "Could not create $target."
tar -xzf "$archive" --no-same-owner --no-same-permissions -C "$target" ||
    fail "Could not unpack the verified archive into $target."

printf '\nVerified installer unpacked at %s\n' "$target"
printf 'Inspect README.md and the source there. To run it later, enter that directory and run sudo sh install.sh.\n'
[ "${1:-}" != --download-only ] || exit 0

printf 'Run the verified installer with administrator privileges now? [y/N] '
IFS= read -r answer || answer=
case "$answer" in
    y|Y|yes|YES)
        if [ "$(id -u)" -eq 0 ]; then
            sh "$target/install.sh"
        else
            command -v sudo >/dev/null 2>&1 || fail 'Run sh install.sh as root from the extracted directory.'
            sudo sh "$target/install.sh"
        fi
        ;;
esac
